"""Incremental podcast RSS 2.0 generation.

The feed is never rebuilt from scratch. Existing <item> elements are parsed and
carried across untouched, and a new episode is spliced in at the right position
by publication date. Channel-level metadata *is* refreshed on every write, so
editing config.yml propagates to the feed without disturbing episodes.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import uuid
import xml.etree.ElementTree as ET
from typing import List, Optional

from .config import FEED_PATH, log

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ATOM = "http://www.w3.org/2005/Atom"
CONTENT = "http://purl.org/rss/1.0/modules/content/"
PODCAST = "https://podcastindex.org/namespace/1.0"

# Namespace UUID defined by the Podcast Index spec for deriving a stable feed
# GUID from a feed URL.
PODCAST_GUID_NS = uuid.UUID("ead4c236-bf58-58c6-a2c6-a6b28d128cb6")

GENERATOR = "sermon-podcast-pipeline"

for prefix, uri in (
    ("itunes", ITUNES),
    ("atom", ATOM),
    ("content", CONTENT),
    ("podcast", PODCAST),
):
    ET.register_namespace(prefix, uri)


def _q(namespace: str, tag: str) -> str:
    return "{%s}%s" % (namespace, tag)


def _sub(parent, tag: str, text: Optional[str] = None, **attrs):
    element = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        element.text = text
    return element


def rfc2822(when) -> str:
    """Format a datetime or ISO-8601 string as an RSS pubDate."""
    if isinstance(when, str):
        value = when.strip().replace("Z", "+00:00")
        try:
            when = dt.datetime.fromisoformat(value)
        except ValueError:
            when = dt.datetime.now(dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return email.utils.format_datetime(when)


def _parse_pubdate(value: str) -> dt.datetime:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _feed_guid(feed_url: str) -> str:
    """Stable feed identity, per the Podcast Index podcast:guid spec."""
    trimmed = feed_url.split("://", 1)[-1].rstrip("/")
    return str(uuid.uuid5(PODCAST_GUID_NS, trimmed))


def format_duration(seconds) -> str:
    """Seconds to HH:MM:SS for itunes:duration."""
    seconds = max(0, int(seconds or 0))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs)


def speaker_from_title(cfg, title: str) -> str:
    """Pull the speaker's name out of a sermon title.

    The channel's convention puts the speaker last, after a pipe:

        "Refresh | Steve Abraham"                              -> Steve Abraham
        "No Name | Mark 5:1-20 | Steve Abraham"                -> Steve Abraham
        "FAMILY MATTERS | House Rules | Gen 25 | Steve Abraham" -> Steve Abraham

    Conference uploads append the event after a double slash, so the segment is
    trimmed at that too:

        "JOHN 8:31-32 | BEN PRESCOTT // FIRST THINGS FIRST 2026" -> Ben Prescott

    Anything that does not look like a person's name falls back to the
    show-level author, so a stray title can never put nonsense in the feed.
    """
    fallback = str(cfg.get("podcast.author", "") or "")
    if not cfg.get("podcast.speaker_from_title", True):
        return fallback

    separator = str(cfg.get("podcast.speaker_separator", "|"))
    segments = [s.strip() for s in (title or "").split(separator) if s.strip()]
    if len(segments) < 2:
        return fallback

    candidate = segments[-1].split("//")[0].strip(" -–—")

    words = candidate.split()
    looks_like_a_name = (
        candidate
        and len(candidate) <= 40
        and 1 <= len(words) <= 4
        and not any(char.isdigit() for char in candidate)
    )
    if not looks_like_a_name:
        return fallback

    # Titles from the all-caps era read better normalised.
    if candidate.isupper():
        candidate = candidate.title()
    return candidate


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split("\n"))
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Loading and saving
# ---------------------------------------------------------------------------


def load(path=FEED_PATH) -> ET.ElementTree:
    """Open the existing feed, or create an empty <rss><channel> shell."""
    if path.exists() and path.stat().st_size > 0:
        return ET.parse(path)
    # No manual xmlns:* attributes here — ElementTree emits the declarations
    # itself from the registered prefixes, and hand-written ones would be
    # duplicated on write, producing malformed XML.
    rss = ET.Element("rss", {"version": "2.0"})
    ET.SubElement(rss, "channel")
    return ET.ElementTree(rss)


def channel_of(tree: ET.ElementTree) -> ET.Element:
    channel = tree.getroot().find("channel")
    if channel is None:
        raise ValueError("feed.xml has no <channel> element")
    return channel


def _serialize(tree: ET.ElementTree) -> bytes:
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True) + b"\n"


def commit(tree: ET.ElementTree, path=FEED_PATH) -> bool:
    """Write the feed only if it actually changed. Returns True if written.

    lastBuildDate is deliberately compared using its *previous* value first.
    Otherwise every hourly run would produce a one-line diff and the repo would
    collect ~700 empty commits a month.
    """
    candidate = _serialize(tree)
    if path.exists() and path.read_bytes() == candidate:
        return False

    node = channel_of(tree).find("lastBuildDate")
    if node is not None:
        node.text = rfc2822(dt.datetime.now(dt.timezone.utc))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_serialize(tree))
    return True


def existing_guids(channel: ET.Element) -> set:
    guids = set()
    for item in channel.findall("item"):
        guid = item.find("guid")
        if guid is not None and guid.text:
            guids.add(guid.text.strip())
    return guids


def episode_count(channel: ET.Element) -> int:
    return len(channel.findall("item"))


# ---------------------------------------------------------------------------
# Channel metadata
# ---------------------------------------------------------------------------


def refresh_channel(cfg, channel: ET.Element, build_date: Optional[str] = None) -> None:
    """Rewrite channel-level tags from config, preserving all <item>s.

    build_date carries the previous lastBuildDate through so an unchanged feed
    serialises byte-identically. commit() bumps it only when something moved.
    """
    items = channel.findall("item")
    for child in list(channel):
        channel.remove(child)

    explicit = "true" if cfg.get("podcast.explicit", False) else "false"
    title = cfg.require("podcast.title")
    description = _truncate(str(cfg.get("podcast.description", "")), 4000)
    author = str(cfg.get("podcast.author", "") or title)
    link = str(cfg.get("podcast.link", "") or cfg.base_url)
    cover = cfg.cover_url
    owner_email = str(cfg.get("podcast.owner_email", "") or "")

    _sub(channel, "title", title)
    _sub(channel, "link", link)
    _sub(channel, "description", description)
    _sub(channel, "language", str(cfg.get("podcast.language", "en-us")))
    if cfg.get("podcast.copyright"):
        _sub(channel, "copyright", str(cfg.get("podcast.copyright")))
    _sub(
        channel,
        "lastBuildDate",
        build_date or rfc2822(dt.datetime.now(dt.timezone.utc)),
    )
    _sub(channel, "generator", GENERATOR)
    _sub(channel, "docs", "https://www.rssboard.org/rss-specification")

    # Self-reference: required by Apple, and how Spotify re-finds the feed.
    _sub(
        channel,
        _q(ATOM, "link"),
        href=cfg.feed_url,
        rel="self",
        type="application/rss+xml",
    )

    _sub(channel, _q(ITUNES, "author"), author)
    _sub(channel, _q(ITUNES, "summary"), description)
    _sub(channel, _q(ITUNES, "type"), str(cfg.get("podcast.itunes_type", "episodic")))
    _sub(channel, _q(ITUNES, "explicit"), explicit)
    _sub(channel, _q(ITUNES, "image"), href=cover)

    # itunes:category carries its value in a "text" attribute, which collides
    # with _sub()'s text argument — build these with SubElement directly.
    category = ET.SubElement(
        channel,
        _q(ITUNES, "category"),
        {"text": str(cfg.get("podcast.category", ""))},
    )
    if cfg.get("podcast.subcategory"):
        ET.SubElement(
            category,
            _q(ITUNES, "category"),
            {"text": str(cfg.get("podcast.subcategory"))},
        )

    owner = ET.SubElement(channel, _q(ITUNES, "owner"))
    _sub(owner, _q(ITUNES, "name"), str(cfg.get("podcast.owner_name", "") or author))
    _sub(owner, _q(ITUNES, "email"), owner_email)

    # Plain RSS 2.0 image, for readers that ignore the iTunes namespace.
    image = ET.SubElement(channel, "image")
    _sub(image, "url", cover)
    _sub(image, "title", title)
    _sub(image, "link", link)

    if cfg.get("podcast.locked", True) and owner_email:
        _sub(channel, _q(PODCAST, "locked"), "yes", owner=owner_email)
    _sub(channel, _q(PODCAST, "guid"), _feed_guid(cfg.feed_url))

    for item in items:
        channel.append(item)


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------


def build_item(cfg, episode: dict) -> ET.Element:
    """Build one <item> from an episode dict.

    Expected keys: video_id, title, description, published, audio_url,
    audio_bytes, duration_seconds.
    """
    item = ET.Element("item")
    explicit = "true" if cfg.get("podcast.explicit", False) else "false"
    video_url = "https://www.youtube.com/watch?v=%s" % episode["video_id"]

    summary = _truncate(episode.get("description", ""), 3800)
    body = summary
    if body:
        body = "%s\n\nWatch this message on YouTube: %s" % (body, video_url)
    else:
        body = "Watch this message on YouTube: %s" % video_url

    _sub(item, "title", episode["title"])
    _sub(item, "link", video_url)
    _sub(item, "description", body)
    # Rich show notes for clients that render HTML; Spotify reads <description>.
    _sub(
        item,
        _q(CONTENT, "encoded"),
        '%s<p>Watch this message on YouTube: <a href="%s">%s</a></p>'
        % ("<p>%s</p>" % summary if summary else "", video_url, video_url),
    )
    _sub(item, "pubDate", rfc2822(episode.get("published")))

    guid = _sub(item, "guid", "yt:video:%s" % episode["video_id"])
    guid.set("isPermaLink", "false")

    _sub(
        item,
        "enclosure",
        url=episode["audio_url"],
        length=int(episode["audio_bytes"]),
        type="audio/mpeg",
    )

    _sub(item, _q(ITUNES, "title"), episode["title"])
    # Per-episode author is the preacher, not the church — this is what podcast
    # apps show under the episode title.
    _sub(item, _q(ITUNES, "author"), speaker_from_title(cfg, episode["title"]))
    _sub(item, _q(ITUNES, "summary"), body)
    _sub(item, _q(ITUNES, "duration"), format_duration(episode.get("duration_seconds", 0)))
    _sub(item, _q(ITUNES, "explicit"), explicit)
    _sub(item, _q(ITUNES, "episodeType"), "full")
    _sub(item, _q(ITUNES, "image"), href=cfg.cover_url)

    return item


def insert_item(channel: ET.Element, new_item: ET.Element) -> None:
    """Splice an episode into the feed, newest first, without a full rebuild."""
    new_date = _parse_pubdate(new_item.findtext("pubDate", ""))

    children = list(channel)
    insert_at = len(children)  # default: oldest episode so far, goes last
    for index, child in enumerate(children):
        if child.tag != "item":
            continue
        if _parse_pubdate(child.findtext("pubDate", "")) < new_date:
            insert_at = index
            break

    channel.insert(insert_at, new_item)


def find_item(channel: ET.Element, guid: str) -> Optional[ET.Element]:
    for item in channel.findall("item"):
        if (item.findtext("guid") or "").strip() == guid:
            return item
    return None


def update_episode_audio(
    channel: ET.Element,
    video_id: str,
    audio_url: str,
    audio_bytes: int,
    duration_seconds=None,
) -> bool:
    """Point an existing episode at different audio, in place.

    Used when audio is re-encoded — a bitrate change, a video episode converted
    to audio. The <guid> is deliberately untouched, so podcast apps treat it as
    the same episode being corrected rather than a new one to announce.

    Returns True if anything actually changed.
    """
    item = find_item(channel, "yt:video:%s" % video_id)
    if item is None:
        return False

    changed = False
    enclosure = item.find("enclosure")
    if enclosure is not None:
        for key, value in (
            ("url", audio_url),
            ("length", str(int(audio_bytes))),
            ("type", "audio/mpeg"),
        ):
            if enclosure.get(key) != value:
                enclosure.set(key, value)
                changed = True

    if duration_seconds:
        node = item.find(_q(ITUNES, "duration"))
        formatted = format_duration(duration_seconds)
        if node is not None and node.text != formatted:
            node.text = formatted
            changed = True

    return changed


def add_episode(cfg, tree: ET.ElementTree, episode: dict) -> bool:
    """Add one episode, or refresh its audio if it is already present.

    Returns True only when a new <item> was inserted. An episode that is
    already in the feed still has its enclosure refreshed, so a re-encode can
    never leave the feed advertising a stale size.
    """
    channel = channel_of(tree)
    guid = "yt:video:%s" % episode["video_id"]
    if guid in existing_guids(channel):
        if update_episode_audio(
            channel,
            episode["video_id"],
            episode["audio_url"],
            episode["audio_bytes"],
            episode.get("duration_seconds"),
        ):
            log("    feed: %s already present — audio details refreshed" % guid)
        else:
            log("    feed: %s already present and unchanged" % guid)
        return False
    insert_item(channel, build_item(cfg, episode))
    return True
