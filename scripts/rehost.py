"""Convert any non-audio episode in the feed into audio, and re-host it.

Spotify refuses an entire feed if a single episode carries a video enclosure:

    We're unable to accept podcasts with videos.

Imported back catalogues sometimes contain one — a service that was posted as
an mp4 years ago. This finds those, extracts the audio, uploads it to
archive.org alongside everything else and rewrites the enclosure in place.

The episode's <guid> is left untouched, so podcast apps treat it as the same
episode they already have rather than a new one.

    python -m scripts.rehost            # report what would change
    python -m scripts.rehost --apply    # do it (needs archive.org credentials)

Runs as a step in the workflow, where it is a no-op unless something needs
fixing.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

import requests

from . import archive_upload, audio, feed
from .config import ConfigError, WORK_DIR, log, warn
from .config import load as load_config

MEDIA_CONTENT = "{http://www.rssboard.org/media-rss}content"


def offending_items(channel: ET.Element):
    """Episodes whose enclosure is not audio."""
    out = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        if not (enclosure.get("type") or "").lower().startswith("audio"):
            out.append((item, enclosure))
    return out


def legacy_key(guid: str) -> str:
    """A stable per-episode key, standing in for a YouTube video ID.

    archive_upload builds the real identifier as <prefix>-<key>, so this is all
    that is needed to give an imported episode a permanent, repeatable home.
    """
    return "legacy-%s" % hashlib.sha1(guid.encode("utf-8")).hexdigest()[:10]


def download(url: str, dest: pathlib.Path) -> None:
    log("    downloading %s" % url)
    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)
    log("    got %.1f MB" % (dest.stat().st_size / 1_048_576))


def to_mp3(cfg, source: pathlib.Path, dest: pathlib.Path) -> None:
    """Strip the video track and re-encode the audio to the podcast's format."""
    location = audio.ffmpeg_location()
    binary = str(pathlib.Path(location) / "ffmpeg") if location else "ffmpeg"
    command = [
        binary, "-nostdin", "-y", "-i", str(source),
        "-vn",                                    # drop the video stream
        "-ac", str(int(cfg.get("audio.channels", 1))),
        "-ar", str(int(cfg.get("audio.sample_rate", 44100))),
        "-b:a", "%dk" % int(cfg.get("audio.bitrate_kbps", 64)),
        "-map_metadata", "0",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(
            "ffmpeg failed converting %s:\n%s" % (source.name, result.stderr[-800:])
        )
    log("    converted to %.1f MB mp3" % (dest.stat().st_size / 1_048_576))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="actually convert and upload")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    tree = feed.load()
    channel = feed.channel_of(tree)
    build_date = channel.findtext("lastBuildDate")
    targets = offending_items(channel)

    if not targets:
        log("Every episode already has an audio enclosure — nothing to do.")
        return 0

    log("%d episode(s) with a non-audio enclosure:\n" % len(targets))
    for item, enclosure in targets:
        log("  %-56s %s" % ((item.findtext("title") or "?")[:56], enclosure.get("type")))

    if not args.apply:
        log("\nPreview only — nothing changed. Re-run with --apply to convert.")
        return 0

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0

    for item, enclosure in targets:
        title = item.findtext("title") or "?"
        guid = (item.findtext("guid") or enclosure.get("url") or "").strip()
        log("\n-> %s" % title[:70])

        key = legacy_key(guid)
        source = WORK_DIR / ("%s.src" % key)
        mp3 = WORK_DIR / ("%s.mp3" % key)

        try:
            download(enclosure.get("url"), source)
            to_mp3(cfg, source, mp3)

            uploaded = archive_upload.upload(
                cfg,
                key,
                mp3,
                title=title,
                description=item.findtext("description") or "",
                published=(item.findtext("pubDate") or "")[:16],
            )

            enclosure.set("url", uploaded["url"])
            enclosure.set("type", "audio/mpeg")
            enclosure.set("length", str(uploaded["bytes"]))

            # Keep media:content consistent so nothing else reports it as video.
            for media in item.findall(MEDIA_CONTENT):
                media.set("url", uploaded["url"])
                media.set("type", "audio/mpeg")
                media.set("medium", "audio")
                if media.get("fileSize"):
                    media.set("fileSize", str(uploaded["bytes"]))

            log("    enclosure now %s" % uploaded["url"])
        except Exception as exc:
            failures += 1
            warn("could not rehost %s: %s" % (title[:50], exc))
        finally:
            for leftover in (source, mp3):
                try:
                    leftover.unlink()
                except OSError:
                    pass

    feed.refresh_channel(cfg, channel, build_date=build_date)
    written = feed.commit(tree)
    log("\nFeed %s. %d failure(s)." % ("updated" if written else "unchanged", failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
