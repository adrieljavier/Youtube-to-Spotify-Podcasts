"""YouTube discovery. No API key, no quota, no account.

Two sources, used for different jobs:

* The channel's public Atom feed, https://www.youtube.com/feeds/videos.xml —
  cheap and instant, but it only ever returns the **latest 15 videos**. This is
  what the hourly watcher polls.
* yt-dlp's flat channel listing, which walks the whole channel history. Slower,
  so it is only used by the one-time backfill script.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

import requests
import yt_dlp

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=%s"
WATCH_URL = "https://www.youtube.com/watch?v=%s"
CHANNEL_VIDEOS_URL = "https://www.youtube.com/channel/%s/videos"
# Every channel has an auto-generated "uploads" playlist whose ID is the
# channel ID with UC swapped for UU. Unlike the Videos tab — which honours
# whatever sort order the channel owner picked, often "Popular" — this playlist
# is always strictly newest-first, which the backfill cutoff depends on.
UPLOADS_PLAYLIST_URL = "https://www.youtube.com/playlist?list=UU%s"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

USER_AGENT = "sermon-podcast-bot/1.0 (+https://github.com)"


class YouTubeError(RuntimeError):
    pass


def _text(node, path: str) -> str:
    found = node.find(path, NS)
    return (found.text or "").strip() if found is not None else ""


def fetch_channel_feed(channel_id: str, timeout: int = 30) -> List[dict]:
    """Latest videos from the public Atom feed, newest first."""
    url = FEED_URL % channel_id
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    if response.status_code == 404:
        raise YouTubeError(
            "YouTube returned 404 for channel %s. Check that youtube.channel_id "
            "is the 24-character ID starting with 'UC' (not the @handle)."
            % channel_id
        )
    response.raise_for_status()

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise YouTubeError("Could not parse the YouTube feed: %s" % exc)

    videos = []
    for entry in root.findall("atom:entry", NS):
        video_id = _text(entry, "yt:videoId")
        if not video_id:
            continue
        videos.append(
            {
                "video_id": video_id,
                "title": _text(entry, "atom:title"),
                "published": _text(entry, "atom:published"),
                "description": _text(entry, "media:group/media:description"),
                "link": WATCH_URL % video_id,
            }
        )
    return videos


def list_channel_videos(channel_id: str, limit: Optional[int] = None) -> List[dict]:
    """Every public video on the channel, newest first.

    Uses a flat (metadata-free) extraction, so this is one request per ~100
    videos rather than one per video.
    """
    if not channel_id.startswith("UC"):
        raise YouTubeError(
            "Channel ID %r does not start with 'UC'. The uploads playlist can "
            "only be derived from a real channel ID, not a handle." % channel_id
        )

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    if limit:
        options["playlistend"] = int(limit)

    info = None
    for url in (
        UPLOADS_PLAYLIST_URL % channel_id[2:],
        CHANNEL_VIDEOS_URL % channel_id,  # fallback; ordering is not guaranteed
    ):
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("entries"):
            break

    if not info or not info.get("entries"):
        raise YouTubeError(
            "yt-dlp found no videos on channel %s. Check the channel ID and "
            "that the channel has public videos." % channel_id
        )

    videos = []
    for entry in info["entries"]:
        if not entry or not entry.get("id"):
            continue
        videos.append(
            {
                "video_id": entry["id"],
                "title": entry.get("title") or "",
                "link": WATCH_URL % entry["id"],
            }
        )
    return videos


def list_all_videos_unordered(channel_id: str) -> List[dict]:
    """Every video on the channel, in no meaningful order.

    The uploads playlist is correctly ordered but YouTube caps it at 100
    entries. The Videos tab returns the full catalogue, but in whatever order
    the channel owner chose — often "Popular". Use this only to mark old videos
    as history, never to decide a cutoff.
    """
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(CHANNEL_VIDEOS_URL % channel_id, download=False)
    except Exception:
        return []

    return [
        {"video_id": e["id"], "title": e.get("title") or ""}
        for e in (info or {}).get("entries") or []
        if e and e.get("id")
    ]


def fetch_video_info(video_id: str, attempts: int = 4) -> dict:
    """Full metadata for one video, without downloading it.

    YouTube intermittently answers with "The page needs to be reloaded" when it
    sees a burst of requests from one address. It is transient, so back off and
    try again rather than failing the episode.
    """
    options = {"quiet": True, "no_warnings": True, "skip_download": True}
    delay = 3
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(WATCH_URL % video_id, download=False)
            if info:
                return info
            last_error = "yt-dlp returned no metadata"
        except Exception as exc:
            last_error = str(exc).strip()
        if attempt < attempts:
            time.sleep(delay)
            delay = min(delay * 2, 30)

    raise YouTubeError(
        "Could not read metadata for %s after %d attempts: %s"
        % (video_id, attempts, last_error)
    )


def published_iso(info: dict) -> Optional[str]:
    """Best available publication timestamp from a yt-dlp info dict."""
    import datetime as dt

    for key in ("release_timestamp", "timestamp"):
        value = info.get(key)
        if value:
            return (
                dt.datetime.fromtimestamp(int(value), dt.timezone.utc)
                .replace(microsecond=0)
                .isoformat()
            )
    date = info.get("upload_date")  # YYYYMMDD
    if date and len(date) == 8:
        return "%s-%s-%sT12:00:00+00:00" % (date[0:4], date[4:6], date[6:8])
    return None


def check_eligibility(cfg, title: str, duration, live_status=None) -> Tuple[bool, str]:
    """Decide whether a video belongs in the podcast.

    Returns (eligible, reason). A False result with reason "live" is temporary —
    the caller should leave the video queued and try again later. Every other
    False reason is permanent.
    """
    if live_status in ("is_live", "is_upcoming"):
        return False, "live"

    # An allow-list pattern is far more reliable than blacklisting every kind
    # of non-sermon upload: Shorts, worship sets and midweek clips simply do
    # not follow the sermon title convention.
    required = cfg.get("youtube.require_title_pattern")
    if required:
        try:
            if not re.search(required, title or "", re.IGNORECASE):
                return False, "title does not match the sermon pattern %r" % required
        except re.error:
            pass

    for pattern in cfg.get("youtube.skip_title_patterns", []) or []:
        try:
            if re.search(pattern, title or "", re.IGNORECASE):
                return False, "title matched skip pattern %r" % pattern
        except re.error:
            continue  # A broken pattern must not take down the run.

    minimum = int(cfg.get("youtube.min_duration_seconds", 0) or 0)
    maximum = int(cfg.get("youtube.max_duration_seconds", 0) or 0)
    if duration is not None:
        if minimum and duration < minimum:
            return False, "shorter than %ss (%ss)" % (minimum, int(duration))
        if maximum and duration > maximum:
            return False, "longer than %ss (%ss)" % (maximum, int(duration))

    return True, "eligible"
