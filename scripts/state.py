"""Persistent processing state.

state.json is the single source of truth for what has been handled. It is
committed back to the repo after every run, so a run always starts from the
result of the previous one.

Status values
-------------
queued     Known, eligible, not yet processed.
uploaded   Audio is on archive.org but the feed entry was not written yet.
           A retry resumes here and does not re-download or re-upload.
published  Live in the RSS feed. Terminal.
skipped    Deliberately excluded (backfill cutoff, title filter, too short).
           Terminal.
parked     Failed run.max_attempts times in a row. No longer retried
           automatically; unpark with `python -m scripts.run --retry <id>`.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Dict, List, Optional

from .config import STATE_PATH

SCHEMA_VERSION = 1

TERMINAL = {"published", "skipped"}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class State:
    def __init__(self, path: pathlib.Path = STATE_PATH):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": SCHEMA_VERSION, "updated_at": None, "videos": {}}
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        data.setdefault("version", SCHEMA_VERSION)
        data.setdefault("videos", {})
        return data

    def save(self) -> None:
        """Atomic write with stable key order, so git diffs stay readable."""
        self.data["updated_at"] = _now()
        self.data["videos"] = dict(sorted(self.data["videos"].items()))
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.replace(self.path)

    # -- queries -----------------------------------------------------------

    @property
    def videos(self) -> Dict[str, dict]:
        return self.data["videos"]

    def knows(self, video_id: str) -> bool:
        return video_id in self.videos

    def get(self, video_id: str) -> Optional[dict]:
        return self.videos.get(video_id)

    def status(self, video_id: str) -> Optional[str]:
        entry = self.get(video_id)
        return entry.get("status") if entry else None

    def pending(self) -> List[dict]:
        """Work still to do, oldest video first.

        Oldest-first matters during a backfill: episodes then enter the feed in
        the order they were preached.

        Backfilled videos may not carry a publication date — seeding one costs
        a metadata request per video, which YouTube rate-limits — so they fall
        back to `backfill_rank`, assigned oldest-first when the queue was
        seeded. An empty date sorts before any real one, so a backfill drains
        ahead of newly discovered uploads.
        """
        items = [
            dict(entry, video_id=vid)
            for vid, entry in self.videos.items()
            if entry.get("status") in ("queued", "uploaded")
        ]
        items.sort(key=lambda e: (e.get("published") or "", e.get("backfill_rank") or 0))
        return items

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for entry in self.videos.values():
            key = entry.get("status", "unknown")
            out[key] = out.get(key, 0) + 1
        return out

    # -- mutations ---------------------------------------------------------

    def upsert(self, video_id: str, **fields) -> dict:
        entry = self.videos.setdefault(
            video_id,
            {"status": "queued", "attempts": 0, "first_seen": _now()},
        )
        entry.update({k: v for k, v in fields.items() if v is not None})
        return entry

    def mark_queued(self, video_id: str, title: str, published: str) -> dict:
        return self.upsert(
            video_id, title=title, published=published, status="queued"
        )

    def mark_skipped(self, video_id: str, reason: str, **fields) -> dict:
        return self.upsert(
            video_id, status="skipped", skip_reason=reason, **fields
        )

    def mark_uploaded(
        self,
        video_id: str,
        identifier: str,
        audio_url: str,
        audio_bytes: int,
        duration_seconds: int,
    ) -> dict:
        entry = self.upsert(
            video_id,
            status="uploaded",
            archive_identifier=identifier,
            audio_url=audio_url,
            audio_bytes=audio_bytes,
            duration_seconds=duration_seconds,
            uploaded_at=_now(),
        )
        # The upload succeeded, so any error from a previous attempt is history
        # and would otherwise sit in state.json looking like a live problem.
        entry.pop("last_error", None)
        entry["attempts"] = 0
        return entry

    def mark_published(self, video_id: str) -> dict:
        entry = self.upsert(video_id, status="published", published_at=_now())
        entry.pop("last_error", None)
        entry["attempts"] = 0
        return entry

    def mark_failure(self, video_id: str, error: str, max_attempts: int) -> dict:
        entry = self.upsert(video_id)
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_error"] = error[:500]
        entry["last_attempt"] = _now()
        if entry["attempts"] >= max_attempts and entry.get("status") != "uploaded":
            entry["status"] = "parked"
        return entry

    def unpark(self, video_id: str) -> bool:
        entry = self.get(video_id)
        if not entry:
            return False
        entry["status"] = "uploaded" if entry.get("audio_url") else "queued"
        entry["attempts"] = 0
        entry.pop("last_error", None)
        return True
