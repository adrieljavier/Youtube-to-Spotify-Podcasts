"""Audio extraction with yt-dlp + ffmpeg."""

from __future__ import annotations

import pathlib
import shutil
from typing import Optional

import yt_dlp

from . import youtube
from .config import REPO_ROOT, log, warn
from .youtube import WATCH_URL


def ffmpeg_location() -> Optional[str]:
    """A directory containing an ffmpeg binary yt-dlp will accept.

    Prefers a system ffmpeg on PATH. Otherwise falls back to the one bundled
    inside the imageio-ffmpeg wheel, so `pip install -r requirements.txt` is
    genuinely all that is needed — no Homebrew, no admin password, nothing to
    install by hand on the machine that runs this.

    The binary ships under a versioned name (ffmpeg-macos-aarch64-v7.1) and
    yt-dlp identifies programs by filename, so it is exposed through a symlink
    plainly named "ffmpeg".
    """
    if shutil.which("ffmpeg"):
        return None  # system ffmpeg is on PATH; let yt-dlp find it itself

    try:
        import imageio_ffmpeg
    except ImportError:
        return None

    try:
        binary = pathlib.Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return None

    bin_dir = REPO_ROOT / ".ffmpeg-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "ffmpeg"

    # Re-point the symlink if the virtualenv was rebuilt underneath it.
    if link.is_symlink() and link.resolve() != binary.resolve():
        link.unlink()
    if not link.exists():
        link.symlink_to(binary)

    return str(bin_dir)


class AudioError(RuntimeError):
    pass


def _options(cfg, work_dir: pathlib.Path, video_id: str, embed_thumbnail: bool) -> dict:
    bitrate = int(cfg.get("audio.bitrate_kbps", 64))
    channels = int(cfg.get("audio.channels", 1))
    sample_rate = int(cfg.get("audio.sample_rate", 44100))

    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            # yt-dlp treats values >= 10 as a constant bitrate in kbps.
            "preferredquality": str(bitrate),
        },
        # Writes title/artist/date into the mp3's ID3 tags, which is what some
        # players show while an episode is downloading.
        {"key": "FFmpegMetadata", "add_metadata": True},
    ]
    if embed_thumbnail:
        postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(work_dir / ("%s.%%(ext)s" % video_id)),
        "postprocessors": postprocessors,
        # -ac/-ar are appended to the ExtractAudio ffmpeg invocation, giving
        # mono spoken-word audio at the configured bitrate.
        "postprocessor_args": {
            "extractaudio": ["-ac", str(channels), "-ar", str(sample_rate)],
        },
        "writethumbnail": embed_thumbnail,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "consoletitle": False,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 60,
        "overwrites": True,
    }
    location = ffmpeg_location()
    if location:
        options["ffmpeg_location"] = location
    return options


def _find_mp3(work_dir: pathlib.Path, video_id: str) -> Optional[pathlib.Path]:
    candidate = work_dir / ("%s.mp3" % video_id)
    return candidate if candidate.exists() else None


def download_audio(cfg, video_id: str, work_dir: pathlib.Path) -> dict:
    """Download a video's audio as an mp3.

    Returns {"path", "bytes", "duration", "info"}. Raises AudioError on failure.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    embed = bool(cfg.get("audio.embed_thumbnail", True))

    info = _run_download(cfg, video_id, work_dir, embed)
    path = _find_mp3(work_dir, video_id)

    if path is None and embed:
        # Thumbnail embedding is the most fragile step in the chain (webp
        # conversion, mutagen availability). It is cosmetic, so drop it and
        # retry rather than losing the episode.
        warn("Thumbnail embedding failed for %s; retrying without it" % video_id)
        info = _run_download(cfg, video_id, work_dir, embed_thumbnail=False)
        path = _find_mp3(work_dir, video_id)

    if path is None:
        raise AudioError("yt-dlp finished but produced no mp3 for %s" % video_id)

    size = path.stat().st_size
    if size < 100_000:
        raise AudioError(
            "Extracted audio for %s is only %s bytes — treating as a failed "
            "download." % (video_id, size)
        )

    log("    audio: %s (%.1f MB)" % (path.name, size / 1_048_576))
    return {
        "path": path,
        "bytes": size,
        "duration": int(info.get("duration") or 0),
        "info": info,
    }


def _run_download(cfg, video_id: str, work_dir: pathlib.Path, embed_thumbnail: bool):
    """Download, trying each configured player client until one is allowed.

    On a datacenter address — which is what a GitHub Actions runner is —
    YouTube refuses the default web client with "Sign in to confirm you're not
    a bot". Other player clients are gated differently and usually still serve.
    """
    clients = youtube.player_clients(cfg)
    last_error = None

    for client in clients:
        options = _options(cfg, work_dir, video_id, embed_thumbnail)
        options.update(youtube.client_options(client))
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.extract_info(WATCH_URL % video_id, download=True) or {}
            if client != clients[0]:
                log("    (downloaded via the %s player client)" % client)
            return result
        except yt_dlp.utils.DownloadError as exc:
            last_error = str(exc).strip()
            # Clear partial files before the next client tries.
            cleanup(work_dir, video_id)
            if not youtube.client_rejected(last_error):
                # Not a client problem — a genuinely broken video, a network
                # failure, a full disk. Trying four more clients would only
                # obscure it.
                raise AudioError("yt-dlp failed for %s: %s" % (video_id, last_error))

    raise AudioError(
        "No player client could download %s (tried: %s). Last error: %s"
        % (video_id, ", ".join(clients), last_error)
    )


def cleanup(work_dir: pathlib.Path, video_id: str) -> None:
    """Remove every intermediate file for a video, successful run or not."""
    if not work_dir.exists():
        return
    for leftover in work_dir.glob("%s.*" % video_id):
        try:
            leftover.unlink()
        except OSError:
            pass
