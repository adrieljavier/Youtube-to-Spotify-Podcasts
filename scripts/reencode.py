"""Bring already-published episodes up to the current audio settings.

Change `audio.bitrate_kbps` or `audio.channels` in config.yml and episodes
published before the change are now inconsistent with everything after it. This
re-downloads them from YouTube, re-encodes at the current settings, and
overwrites the audio on archive.org.

Two things are deliberately left alone:

* The episode's <guid>, so podcast apps see the same episode being corrected
  rather than a new one to announce.
* The archive.org identifier and download URL, which are derived from the video
  ID. The file is replaced in place, so nothing already published ever 404s —
  at worst a listener gets the previous encode for a few minutes while
  archive.org catches up.

    python -m scripts.reencode            # report what is out of date
    python -m scripts.reencode --apply    # do it (needs archive.org credentials)

Bounded by run.max_reencode_per_run so a large catalogue is spread over several
runs rather than one enormous one.
"""

from __future__ import annotations

import argparse
import sys

from . import archive_upload, audio, feed
from .config import ConfigError, WORK_DIR, log, warn
from .config import load as load_config
from .state import State


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="actually re-encode")
    parser.add_argument("--limit", type=int, help="override the per-run limit")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    kbps = int(cfg.get("audio.bitrate_kbps", 64))
    channels = int(cfg.get("audio.channels", 1))
    limit = args.limit or int(cfg.get("run.max_reencode_per_run", 4))

    state = State()
    stale = state.stale_encodings(kbps, channels)

    if not stale:
        log("Every published episode already matches %dkbps/%dch." % (kbps, channels))
        return 0

    log("Target: %d kbps, %d channel(s)" % (kbps, channels))
    log("%d episode(s) out of date; doing up to %d this run.\n" % (len(stale), limit))

    if not args.apply:
        for entry in stale[:limit]:
            log("  would re-encode %s  (was %skbps/%sch)  %s"
                % (entry["video_id"], entry.get("encoded_kbps") or "?",
                   entry.get("encoded_channels") or "?", (entry.get("title") or "")[:48]))
        log("\nPreview only — nothing changed. Re-run with --apply.")
        return 0

    tree = feed.load()
    channel = feed.channel_of(tree)
    build_date = channel.findtext("lastBuildDate")

    done = failures = 0
    for entry in stale[:limit]:
        video_id = entry["video_id"]
        title = entry.get("title") or video_id
        log("-> %s  %s" % (video_id, title[:58]))

        try:
            extracted = audio.download_audio(cfg, video_id, WORK_DIR)
            try:
                uploaded = archive_upload.upload(
                    cfg,
                    video_id,
                    extracted["path"],
                    title=title,
                    description=entry.get("description") or "",
                    published=entry.get("published") or "",
                )
            finally:
                audio.cleanup(WORK_DIR, video_id)

            duration = extracted["duration"] or entry.get("duration_seconds") or 0
            feed.update_episode_audio(
                channel, video_id, uploaded["url"], uploaded["bytes"], duration
            )
            state.upsert(
                video_id,
                audio_url=uploaded["url"],
                audio_bytes=uploaded["bytes"],
                duration_seconds=duration,
                encoded_kbps=kbps,
                encoded_channels=channels,
            )
            state.save()
            done += 1
            log("    now %.1f MB at %dkbps" % (uploaded["bytes"] / 1_048_576, kbps))
            # Deliberately no availability check. The URL is unchanged and
            # already serves the previous encode, so nothing is ever broken;
            # waiting for the new size to propagate would just burn 90s an
            # episode for no benefit.
        except Exception as exc:
            failures += 1
            warn("could not re-encode %s: %s" % (video_id, str(exc)[:200]))

    feed.refresh_channel(cfg, channel, build_date=build_date)
    written = feed.commit(tree)
    state.save()

    remaining = len(state.stale_encodings(kbps, channels))
    log("\nre-encoded : %d" % done)
    log("failed     : %d" % failures)
    log("remaining  : %d" % remaining)
    log("feed       : %s" % ("updated" if written else "unchanged"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
