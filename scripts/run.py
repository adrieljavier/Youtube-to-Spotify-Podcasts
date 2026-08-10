"""Pipeline entry point: discover, extract, upload, publish.

Run with:  python -m scripts.run

Failure policy: every episode is processed inside its own try/except. A video
that fails is recorded with its error and left un-published, so the next run
retries it. One bad video never stops the rest of the run, and never gets
marked as done.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from . import archive_upload, audio, feed, youtube
from .config import ConfigError, WORK_DIR, log, warn
from .config import load as load_config
from .state import State


def discover(cfg, state: State) -> int:
    """Poll the public YouTube feed and queue anything new."""
    videos = youtube.fetch_channel_feed(cfg.channel_id)
    log("Channel feed returned %d videos (YouTube caps this at 15)." % len(videos))

    added = 0
    for video in videos:
        video_id = video["video_id"]
        if state.knows(video_id):
            continue

        eligible, reason = youtube.check_eligibility(cfg, video["title"], duration=None)
        if not eligible and reason != "live":
            state.mark_skipped(
                video_id, reason, title=video["title"], published=video["published"]
            )
            log("  skip  %s  %s (%s)" % (video_id, video["title"], reason))
            continue

        state.mark_queued(video_id, video["title"], video["published"])
        log("  queue %s  %s" % (video_id, video["title"]))
        added += 1

    if not added:
        log("  nothing new.")
    return added


def process_one(cfg, state: State, entry: dict, tree) -> str:
    """Take one video from wherever it stopped to published.

    Returns "published", "waiting", "deferred" or "skipped". "waiting" means
    the audio is safely on archive.org but not being served yet — no work is
    lost and a later run finishes it.
    """
    video_id = entry["video_id"]
    log("  -> %s  %s" % (video_id, entry.get("title") or ""))

    if entry.get("status") == "uploaded" and entry.get("audio_url"):
        # A previous run uploaded the audio but could not finish — usually
        # because archive.org had not started serving it yet. Resume at the
        # feed step; never pay for the download and upload twice.
        log("    resuming: audio already on archive.org")
        episode = {
            "video_id": video_id,
            "title": entry.get("title") or video_id,
            "description": entry.get("description") or "",
            "published": entry.get("published"),
            "audio_url": entry["audio_url"],
            "audio_bytes": entry["audio_bytes"],
            "duration_seconds": entry.get("duration_seconds", 0),
        }
        if not archive_upload.is_available(
            cfg, episode["audio_url"], expected_bytes=episode["audio_bytes"]
        ):
            return "waiting"
        feed.add_episode(cfg, tree, episode)
        state.mark_published(video_id)
        return "published"

    info = youtube.fetch_video_info(video_id, youtube.player_clients(cfg))
    title = info.get("title") or entry.get("title") or video_id
    description = info.get("description") or ""
    published = youtube.published_iso(info) or entry.get("published")
    duration = info.get("duration")

    eligible, reason = youtube.check_eligibility(
        cfg, title, duration, info.get("live_status")
    )
    if not eligible:
        if reason == "live":
            # Stream still running. Leave it queued and pick it up next hour.
            log("    still live, deferring")
            state.upsert(video_id, title=title, published=published)
            return "deferred"
        state.mark_skipped(video_id, reason, title=title, published=published)
        log("    skipping permanently (%s)" % reason)
        return "skipped"

    state.upsert(video_id, title=title, published=published)

    extracted = audio.download_audio(cfg, video_id, WORK_DIR)
    try:
        uploaded = archive_upload.upload(
            cfg,
            video_id,
            extracted["path"],
            title=title,
            description=description,
            published=published or "",
        )
    finally:
        # The mp3 exists on archive.org now, or it failed — either way it must
        # not stay in the repo checkout.
        audio.cleanup(WORK_DIR, video_id)

    # Record the upload BEFORE checking availability. The expensive work —
    # download, convert, upload — is done at this point, and it must survive
    # whatever happens next.
    state.mark_uploaded(
        video_id,
        identifier=uploaded["identifier"],
        audio_url=uploaded["url"],
        audio_bytes=uploaded["bytes"],
        duration_seconds=extracted["duration"] or int(duration or 0),
        encoded_kbps=int(cfg.get("audio.bitrate_kbps", 64)),
        encoded_channels=int(cfg.get("audio.channels", 1)),
    )
    # Kept only while the episode is in flight; dropped once published.
    state.upsert(video_id, description=description[:1500])
    state.save()

    if not archive_upload.is_available(
        cfg, uploaded["url"], expected_bytes=uploaded["bytes"]
    ):
        # Perfectly normal for a fresh item — archive.org can take half an hour
        # to start serving. The episode stays "uploaded" and the next run adds
        # it to the feed without re-downloading anything.
        return "waiting"

    feed.add_episode(
        cfg,
        tree,
        {
            "video_id": video_id,
            "title": title,
            "description": description,
            "published": published,
            "audio_url": uploaded["url"],
            "audio_bytes": uploaded["bytes"],
            "duration_seconds": extracted["duration"] or int(duration or 0),
        },
    )
    state.mark_published(video_id)
    state.videos[video_id].pop("description", None)
    return "published"


def summarize(state: State, published, waiting, failed, feed_written: bool) -> None:
    counts = state.counts()
    lines = [
        "",
        "Run summary",
        "-----------",
        "published this run : %d" % len(published),
        "uploaded, awaiting : %d  (on archive.org; feed entry follows)"
        % len(waiting),
        "failed this run    : %d" % len(failed),
        "feed rewritten     : %s" % ("yes" if feed_written else "no change"),
        "totals             : %s"
        % (", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "empty"),
    ]
    for video_id, error in failed:
        lines.append("  FAILED %s: %s" % (video_id, error))
    text = "\n".join(lines)
    log(text)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write("```\n%s\n```\n" % text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish YouTube sermons as a podcast.")
    parser.add_argument("--limit", type=int, help="max episodes to publish this run")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="discover and queue only; do not download, upload or publish",
    )
    parser.add_argument(
        "--retry",
        metavar="VIDEO_ID",
        action="append",
        default=[],
        help="un-park a video that exceeded its attempt limit (repeatable)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="write the feed scaffold from config.yml and exit",
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    state = State()
    tree = feed.load()
    channel = feed.channel_of(tree)
    previous_build_date = channel.findtext("lastBuildDate")

    if args.init:
        feed.refresh_channel(cfg, channel, build_date=previous_build_date)
        written = feed.commit(tree)
        log("Feed scaffold %s" % ("written" if written else "already current"))
        log("Feed URL: %s" % cfg.feed_url)
        return 0

    for video_id in args.retry:
        log("Un-parking %s: %s" % (video_id, state.unpark(video_id)))

    try:
        discover(cfg, state)
    except Exception as exc:
        # Losing discovery is survivable — anything already queued still runs.
        warn("Discovery failed: %s" % exc)
    state.save()

    published, waiting, failed = [], [], []
    limit = args.limit or int(cfg.get("run.max_episodes_per_run", 4))
    finish_cap = int(cfg.get("run.max_finish_per_run", 12))
    max_attempts = int(cfg.get("run.max_attempts", 5))
    pending = state.pending()

    # Two separate budgets. Downloading, transcoding and uploading an episode
    # takes minutes; finishing one whose audio is already on archive.org takes
    # a HEAD request. Charging both to the same allowance would let a backfill
    # keep finished episodes out of the feed for hours.
    selected, downloads, finishes = [], 0, 0
    for entry in pending:
        if entry.get("status") == "uploaded":
            if finishes < finish_cap:
                finishes += 1
                selected.append(entry)
        elif downloads < limit:
            downloads += 1
            selected.append(entry)
        if downloads >= limit and finishes >= finish_cap:
            break

    if args.dry_run:
        log("\nDry run: %d pending episode(s)" % len(pending))
        for entry in selected:
            log("  would %s %s  %s"
                % ("finish " if entry.get("status") == "uploaded" else "publish",
                   entry["video_id"], entry.get("title")))
        return 0

    log("\n%d pending: %d to download and publish, %d already uploaded to finish."
        % (len(pending), downloads, finishes))
    for entry in selected:
        video_id = entry["video_id"]
        try:
            result = process_one(cfg, state, entry, tree)
            if result == "published":
                published.append(video_id)
                log("    published.")
            elif result == "waiting":
                waiting.append(video_id)
        except Exception as exc:
            failed.append((video_id, str(exc)))
            state.mark_failure(video_id, str(exc), max_attempts)
            warn("%s failed: %s" % (video_id, exc))
            traceback.print_exc(file=sys.stderr)
        finally:
            # Persist after every episode: a mid-run crash or a cancelled
            # Actions job still keeps everything completed so far.
            state.save()
            audio.cleanup(WORK_DIR, video_id)

    feed.refresh_channel(cfg, channel, build_date=previous_build_date)
    feed_written = feed.commit(tree)
    state.save()

    summarize(state, published, waiting, failed, feed_written)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
