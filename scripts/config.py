"""Configuration loading.

Non-secret settings come from config.yml. Credentials come from the
environment only and are never read from, or written to, any file in the repo.
"""

from __future__ import annotations

import os
import pathlib
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yml"
STATE_PATH = REPO_ROOT / "state.json"
DOCS_DIR = REPO_ROOT / "docs"
FEED_PATH = DOCS_DIR / "feed.xml"
WORK_DIR = REPO_ROOT / "work"


class ConfigError(RuntimeError):
    pass


class Config:
    def __init__(self, data: dict):
        self._data = data

    def get(self, dotted: str, default=None):
        node = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str):
        value = self.get(dotted)
        if value in (None, ""):
            raise ConfigError(
                "Missing required setting '%s' in config.yml" % dotted
            )
        return value

    # -- derived values ----------------------------------------------------

    @property
    def channel_id(self) -> str:
        """Environment wins over config so the ID can live in a repo secret."""
        env = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
        value = env or str(self.get("youtube.channel_id", "")).strip()
        if not value or value.startswith("UCxxxx"):
            raise ConfigError(
                "YouTube channel ID is not set. Put it in config.yml under "
                "youtube.channel_id, or set the YOUTUBE_CHANNEL_ID secret."
            )
        return value

    @property
    def base_url(self) -> str:
        """Public GitHub Pages root, no trailing slash.

        Falls back to deriving the standard Pages URL from $GITHUB_REPOSITORY
        so the workflow works without extra configuration.
        """
        explicit = str(self.get("site.base_url", "") or "").strip()
        if explicit:
            return explicit.rstrip("/")
        repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
        if "/" in repo:
            owner, name = repo.split("/", 1)
            return "https://%s.github.io/%s" % (owner.lower(), name)
        raise ConfigError(
            "site.base_url is not set and $GITHUB_REPOSITORY is unavailable. "
            "Set site.base_url in config.yml to your GitHub Pages URL."
        )

    @property
    def feed_url(self) -> str:
        return "%s/feed.xml" % self.base_url

    @property
    def cover_url(self) -> str:
        return "%s/%s" % (self.base_url, self.get("podcast.cover_image", "cover.jpg"))

    @property
    def archive_credentials(self):
        """(access_key, secret_key) from the environment. Never from disk."""
        access = os.environ.get("IA_ACCESS_KEY", "").strip()
        secret = os.environ.get("IA_SECRET_KEY", "").strip()
        if not access or not secret:
            raise ConfigError(
                "archive.org credentials missing. Set the IA_ACCESS_KEY and "
                "IA_SECRET_KEY environment variables (GitHub Actions secrets). "
                "Generate them at https://archive.org/account/s3.php"
            )
        return access, secret


def load() -> Config:
    if not CONFIG_PATH.exists():
        raise ConfigError("config.yml not found at %s" % CONFIG_PATH)
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError("config.yml did not parse to a mapping")
    return Config(data)


def log(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print("WARNING: %s" % message, file=sys.stderr, flush=True)
