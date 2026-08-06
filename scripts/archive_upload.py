"""Permanent, free audio hosting on archive.org.

One archive.org item per sermon, identified as <prefix>-<youtube_video_id>.
Because the identifier is derived from the video ID it is stable and
idempotent: re-running after a crash finds the existing item instead of
creating a duplicate.
"""

from __future__ import annotations

import pathlib
import re
import time
from typing import Optional

import requests
from internetarchive import get_session

from .config import log, warn

DOWNLOAD_URL = "https://archive.org/download/%s/%s"
DETAILS_URL = "https://archive.org/details/%s"

# archive.org identifiers: ASCII alphanumerics, dots, dashes and underscores,
# 3-100 chars, must start with an alphanumeric.
_IDENTIFIER_SAFE = re.compile(r"[^A-Za-z0-9._-]")


class ArchiveError(RuntimeError):
    pass


def build_identifier(cfg, video_id: str) -> str:
    prefix = str(cfg.get("archive.identifier_prefix", "sermon")).strip("-")
    identifier = _IDENTIFIER_SAFE.sub("-", "%s-%s" % (prefix, video_id))
    if len(identifier) < 3:
        raise ArchiveError("Derived archive.org identifier %r is too short" % identifier)
    return identifier[:100]


def audio_filename(video_id: str) -> str:
    return "%s.mp3" % video_id


def download_url(identifier: str, filename: str) -> str:
    return DOWNLOAD_URL % (identifier, filename)


def _metadata(cfg, video_id: str, title: str, description: str, published: str) -> dict:
    return {
        "mediatype": "audio",
        "collection": cfg.get("archive.collection", "opensource_audio"),
        "title": title or video_id,
        "creator": cfg.get("podcast.author", "") or "",
        "date": (published or "")[:10],
        "description": _item_description(description, video_id),
        "subject": [
            tag
            for tag in ["sermon", "podcast", "church", cfg.get("podcast.author") or ""]
            if tag
        ],
        "language": cfg.get("podcast.language", "en-us"),
        "licenseurl": cfg.get("archive.license_url", ""),
        "originalurl": "https://www.youtube.com/watch?v=%s" % video_id,
        "scanner": "sermon-podcast-pipeline",
    }


def _item_description(description: str, video_id: str) -> str:
    body = (description or "").strip()
    if len(body) > 4000:
        body = body[:4000].rstrip() + "…"
    source = (
        '<p>Source video: <a href="https://www.youtube.com/watch?v=%s">'
        "https://www.youtube.com/watch?v=%s</a></p>" % (video_id, video_id)
    )
    return ("%s\n\n%s" % (body, source)).strip() if body else source


def _session(cfg):
    access, secret = cfg.archive_credentials
    return get_session(config={"s3": {"access": access, "secret": secret}})


def upload(
    cfg,
    video_id: str,
    audio_path: pathlib.Path,
    title: str,
    description: str,
    published: str,
) -> dict:
    """Upload one mp3 and return {"identifier", "filename", "url", "bytes"}."""
    identifier = build_identifier(cfg, video_id)
    filename = audio_filename(video_id)
    size = audio_path.stat().st_size

    session = _session(cfg)
    item = session.get_item(identifier)

    if _already_uploaded(item, filename, size):
        log("    archive.org: %s already has %s, skipping upload" % (identifier, filename))
    else:
        log("    archive.org: uploading to %s" % (DETAILS_URL % identifier))
        try:
            responses = item.upload(
                files={filename: str(audio_path)},
                metadata=_metadata(cfg, video_id, title, description, published),
                # We only ever serve the file we uploaded, so skip archive.org's
                # derivation queue: faster, and no surprise re-encodes.
                queue_derive=False,
                verify=True,
                retries=3,
                retries_sleep=15,
                verbose=False,
            )
        except Exception as exc:  # network, auth, quota — all retryable next run
            raise ArchiveError("Upload to %s failed: %s" % (identifier, exc))

        bad = [r for r in responses if getattr(r, "status_code", None) not in (200, None)]
        if bad:
            codes = ", ".join(str(getattr(r, "status_code", "?")) for r in bad)
            raise ArchiveError(
                "archive.org rejected the upload to %s (HTTP %s). A 403 here "
                "usually means the IA_ACCESS_KEY/IA_SECRET_KEY secrets are "
                "wrong or the account is not yet verified." % (identifier, codes)
            )

    url = download_url(identifier, filename)
    wait_until_available(cfg, url, expected_bytes=size)
    return {"identifier": identifier, "filename": filename, "url": url, "bytes": size}


def _already_uploaded(item, filename: str, size: int) -> bool:
    if not getattr(item, "exists", False):
        return False
    for entry in item.files or []:
        if entry.get("name") == filename:
            existing = int(entry.get("size") or 0)
            return existing == size
    return False


def wait_until_available(cfg, url: str, expected_bytes: Optional[int] = None) -> None:
    """Block until the file actually serves over HTTP.

    A fresh upload takes a little while to propagate to archive.org's download
    nodes. Publishing a feed entry that 404s would push a broken episode to
    Spotify, so an episode is not marked published until its URL is live.
    """
    timeout = int(cfg.get("archive.availability_timeout_seconds", 600))
    deadline = time.time() + timeout
    delay = 10
    last = "no response"

    while time.time() < deadline:
        try:
            response = requests.head(url, allow_redirects=True, timeout=30)
            if response.status_code == 200:
                length = int(response.headers.get("Content-Length") or 0)
                if not expected_bytes or not length or length == expected_bytes:
                    log("    archive.org: %s is live" % url)
                    return
                last = "size mismatch (%s vs %s)" % (length, expected_bytes)
            else:
                last = "HTTP %s" % response.status_code
        except requests.RequestException as exc:
            last = str(exc)

        time.sleep(delay)
        delay = min(delay * 2, 60)

    raise ArchiveError(
        "%s did not become downloadable within %ss (last: %s). The upload most "
        "likely succeeded — the next run will pick it up from where it left "
        "off without re-uploading." % (url, timeout, last)
    )
