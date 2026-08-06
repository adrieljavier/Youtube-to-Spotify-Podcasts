"""One-time state seeding and catch-up.

The hourly watcher reads YouTube's public Atom feed, which only ever exposes
the latest 15 videos. That is fine forever after, but it cannot reach back to a
cutoff further in the past. This script walks the *entire* channel with yt-dlp
(still no API key) and writes the starting state:

  * everything at or older than the cutoff -> "skipped", never published
  * everything newer than the cutoff       -> "queued", drained by scripts.run

Nothing is written unless you pass --apply. Run it without that flag first and
read the plan.

Examples
--------
    # Preview: publish everything posted after this specific video.
    python -m scripts.backfill --after-video-id dQw4w9WgXcQ

    # Preview: publish everything whose title comes after this one.
    python -m scripts.backfill --after-title "BOLD FAITH"

    # Preview: publish everything uploaded after a date.
    python -m scripts.backfill --after-date 2026-01-27

    # Nothing historic at all: start clean from the next upload.
    python -m scripts.backfill --seed-only --apply
"""

from __future__ import annotations

import argparse
import sys

from . import youtube
from .config import ConfigError, log, warn
from .config import load as load_config
from .state import State

CUTOFF_REASON = "before backfill cutoff"


def _find_cutoff_by_id(videos, video_id: str) -> int:
    for index, video in enumerate(videos):
        if video["video_id"] == video_id:
            return index
    raise SystemExit(
        "Video ID %r is not among the videos this script can order. YouTube "
        "caps the uploads playlist at the 100 most recent, so a cutoff further "
        "back than that cannot be used. Run with --list to see what is "
        "available." % video_id
    )


def _find_cutoff_by_title(videos, needle: str) -> int:
    lowered = needle.lower()
    matches = [i for i, v in enumerate(videos) if lowered in (v["title"] or "").lower()]
    if not matches:
        raise SystemExit(
            "No video title contains %r. Run with --list to see the available "
            "videos." % needle
        )
    if len(matches) > 1:
        log("Note: %d titles matched %r; using the newest:" % (len(matches), needle))
        for i in matches:
            log("  %s  %s" % (videos[i]["video_id"], videos[i]["title"]))
    return min(matches)


def _find_cutoff_by_date(videos, cutoff_date: str) -> int:
    """Index of the newest video uploaded on or before cutoff_date.

    Walks newest to oldest fetching real upload dates, and stops as soon as it
    crosses the cutoff — so it costs one metadata request per video newer than
    the cutoff, not one per video on the channel.
    """
    for index, video in enumerate(videos):
        try:
            info = youtube.fetch_video_info(video["video_id"])
        except Exception as exc:
            warn("Could not date %s (%s); treating as newer than the cutoff"
                 % (video["video_id"], exc))
            continue
        published = (youtube.published_iso(info) or "")[:10]
        log("  %s  %s  %s" % (published or "????-??-??", video["video_id"], video["title"]))
        if published and published <= cutoff_date:
            return index
    raise SystemExit(
        "Every video on the channel is newer than %s. If that is right, use "
        "--after-video-id or --seed-only instead." % cutoff_date
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--after-video-id", help="publish videos newer than this ID")
    group.add_argument("--after-title", help="publish videos newer than this title")
    group.add_argument("--after-date", help="publish videos uploaded after YYYY-MM-DD")
    group.add_argument(
        "--seed-only",
        action="store_true",
        help="mark every existing video as done; publish nothing historic",
    )
    parser.add_argument(
        "--list", action="store_true", help="just list the channel and exit"
    )
    parser.add_argument(
        "--fetch-metadata",
        action="store_true",
        help="also record each video's date and duration while seeding. Costs "
             "one request per video and YouTube rate-limits bursts, so it is "
             "off by default — the pipeline collects both at publish time.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write state.json (default is a preview)"
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
        channel_id = cfg.channel_id
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    log("Reading the full channel history for %s ..." % channel_id)
    videos = youtube.list_channel_videos(channel_id)
    log("Found %d public videos (newest first).\n" % len(videos))

    if args.list:
        for index, video in enumerate(videos):
            log("  [%3d] %s  %s" % (index, video["video_id"], video["title"]))
        return 0

    if args.seed_only:
        cutoff = 0
    elif args.after_video_id:
        cutoff = _find_cutoff_by_id(videos, args.after_video_id)
    elif args.after_title:
        cutoff = _find_cutoff_by_title(videos, args.after_title)
    elif args.after_date:
        log("Dating videos newest-first until %s is crossed:" % args.after_date)
        # The returned index is the first video at or before the cutoff date,
        # and the cutoff itself is exclusive, so this index is the boundary.
        cutoff = _find_cutoff_by_date(videos, args.after_date)
        log("")
    else:
        parser.error(
            "choose one of --after-video-id / --after-title / --after-date / "
            "--seed-only (or --list to look around first)"
        )

    to_publish = videos[:cutoff]
    to_skip = videos[cutoff:]

    log("Cutoff: %d video(s) to consider publishing, %d marked as history.\n"
        % (len(to_publish), len(to_skip)))

    state = State()
    queued, filtered = [], []

    # Oldest first, so backfill_rank ascends with the date the sermon was
    # preached and the queue drains in chronological order.
    for rank, video in enumerate(reversed(to_publish), start=1):
        video_id = video["video_id"]
        if state.status(video_id) in ("published", "skipped"):
            continue

        title = video["title"]
        published = None
        duration = None
        live_status = None

        if args.fetch_metadata:
            try:
                info = youtube.fetch_video_info(video_id)
                title = info.get("title") or title
                published = youtube.published_iso(info)
                duration = info.get("duration")
                live_status = info.get("live_status")
            except Exception as exc:
                warn("No metadata for %s (%s); using the title alone."
                     % (video_id, str(exc).splitlines()[0][:80]))

        eligible, reason = youtube.check_eligibility(cfg, title, duration, live_status)
        if eligible or reason == "live":
            queued.append((video_id, title, published))
            log("  QUEUE  %-10s %s  %s" % (published or "", video_id, title[:62]))
            if args.apply:
                state.mark_queued(video_id, title, published or "")
                state.upsert(video_id, backfill_rank=rank)
        else:
            filtered.append((video_id, title, reason))
            log("  filter %-10s %s  %s  (%s)" % ("", video_id, title[:44], reason[:34]))
            if args.apply:
                state.mark_skipped(video_id, reason, title=title, published=published)

    history = list(to_skip)

    # The uploads playlist stops at 100 entries, so anything older is invisible
    # to it. Sweep the (unordered) full catalogue as well, purely so the rest of
    # the channel is recorded as history and can never be picked up later.
    if len(videos) >= 100:
        known = {v["video_id"] for v in videos}
        extra = [
            v
            for v in youtube.list_all_videos_unordered(channel_id)
            if v["video_id"] not in known
        ]
        if extra:
            log("Uploads playlist capped at %d entries; found %d older videos "
                "via the channel catalogue.\n" % (len(videos), len(extra)))
            history.extend(extra)

    for video in history:
        if state.knows(video["video_id"]):
            continue
        if args.apply:
            state.mark_skipped(
                video["video_id"], CUTOFF_REASON, title=video["title"]
            )

    log("")
    log("Would queue   : %d episode(s)" % len(queued))
    log("Would filter  : %d (title/duration rules in config.yml)" % len(filtered))
    log("Marked history: %d" % len(history))

    if not args.apply:
        log("\nPreview only — nothing written. Re-run with --apply to save.")
        return 0

    state.save()
    log("\nWrote %s" % state.path)
    per_run = int(cfg.get("run.max_episodes_per_run", 4))
    if queued:
        log(
            "The hourly workflow publishes up to %d per run, so the queue will "
            "clear in about %d run(s)." % (per_run, -(-len(queued) // per_run))
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
