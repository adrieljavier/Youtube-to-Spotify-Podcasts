"""Import episodes from an existing podcast feed into docs/feed.xml.

Needed when a show already exists somewhere else — a Squarespace page, an old
host — and this pipeline is taking over. Pointing Spotify at a new feed makes
that feed the whole truth: any episode missing from it is dropped from the
show. Importing first makes the switch additive instead of destructive.

Episodes are copied **verbatim**, which matters for two reasons:

* The original <guid> is preserved, so podcast apps recognise each episode as
  the one they already have. Rewriting GUIDs would re-notify every subscriber
  about hundreds of "new" episodes.
* The original <enclosure> is preserved, so the audio keeps being served from
  wherever it already lives. Nothing is re-hosted or re-uploaded.

    python -m scripts.import_feed --url "https://example.com/feed?format=rss"

Nothing is written without --apply.
"""

from __future__ import annotations

import argparse
import copy
import sys
import xml.etree.ElementTree as ET

import requests

from . import feed
from .config import ConfigError, log, warn
from .config import load as load_config

# Namespaces commonly found in feeds from other hosts. Registering them keeps
# the merged output readable instead of littered with ns0: prefixes.
EXTRA_NAMESPACES = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://www.rssboard.org/media-rss",
    "wfw": "http://wellformedweb.org/CommentAPI/",
    "googleplay": "http://www.google.com/schemas/play-podcasts/1.0",
}
for _prefix, _uri in EXTRA_NAMESPACES.items():
    ET.register_namespace(_prefix, _uri)


def fetch(source: str) -> ET.Element:
    if source.startswith(("http://", "https://")):
        log("Fetching %s ..." % source)
        response = requests.get(source, timeout=60, headers={"User-Agent": "podcast-importer/1.0"})
        response.raise_for_status()
        data = response.content
    else:
        data = open(source, "rb").read()

    root = ET.fromstring(data)
    channel = root.find("channel")
    if channel is None:
        raise SystemExit("That feed has no <channel> element — is it really RSS?")
    return channel


def item_guid(item: ET.Element) -> str:
    text = item.findtext("guid")
    if text and text.strip():
        return text.strip()
    # Fall back to the enclosure URL: still stable, still unique per episode.
    enclosure = item.find("enclosure")
    return (enclosure.get("url") or "").strip() if enclosure is not None else ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True, help="feed URL, or a local file path")
    parser.add_argument("--apply", action="store_true", help="write docs/feed.xml")
    parser.add_argument(
        "--limit", type=int, help="import at most this many (newest first), for testing"
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    source_channel = fetch(args.url)
    source_items = source_channel.findall("item")
    log("Source feed: %r — %d episode(s)\n"
        % (source_channel.findtext("title"), len(source_items)))

    tree = feed.load()
    channel = feed.channel_of(tree)
    build_date = channel.findtext("lastBuildDate")
    existing = feed.existing_guids(channel)
    log("Current feed: %d episode(s)\n" % feed.episode_count(channel))

    imported = skipped_present = skipped_no_audio = 0
    for item in source_items[: args.limit] if args.limit else source_items:
        guid = item_guid(item)
        title = (item.findtext("title") or "(untitled)")[:64]

        if item.find("enclosure") is None:
            skipped_no_audio += 1
            warn("no audio, skipping: %s" % title)
            continue
        if not guid:
            skipped_no_audio += 1
            warn("no guid or enclosure URL, skipping: %s" % title)
            continue
        if guid in existing:
            skipped_present += 1
            continue

        if args.apply:
            feed.insert_item(channel, copy.deepcopy(item))
        existing.add(guid)
        imported += 1

    log("")
    log("to import      : %d" % imported)
    log("already present: %d" % skipped_present)
    log("no audio       : %d" % skipped_no_audio)

    if not args.apply:
        log("\nPreview only — nothing written. Re-run with --apply to merge.")
        return 0

    feed.refresh_channel(cfg, channel, build_date=build_date)
    feed.commit(tree)
    log("\nMerged feed now has %d episode(s)." % feed.episode_count(channel))
    log("Feed URL: %s" % cfg.feed_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
