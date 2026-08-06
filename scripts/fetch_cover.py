"""Build docs/cover.jpg from the YouTube channel's own artwork.

Spotify and Apple require square cover art, 1400x1400 minimum and 3000x3000
recommended. A YouTube channel avatar is square but usually only 800x800, and
channel banners are wide, so neither is usable as-is. This pulls the best
available channel image, letterboxes it onto a square canvas at 3000x3000 and
writes a compliant JPEG.

    python -m scripts.fetch_cover

Pass --source PATH to use your own image file instead of the channel artwork —
same padding and resizing, so it comes out compliant either way.
"""

from __future__ import annotations

import argparse
import io
import sys

import requests
import yt_dlp
from PIL import Image

from .config import DOCS_DIR, ConfigError, log, warn
from .config import load as load_config

TARGET = 3000
CHANNEL_URL = "https://www.youtube.com/channel/%s"


def _channel_image_url(channel_id: str) -> str:
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlist_items": "0",  # metadata only, no entries
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(CHANNEL_URL % channel_id, download=False) or {}

    candidates = []
    for thumb in info.get("thumbnails") or []:
        url = thumb.get("url")
        if not url:
            continue
        width = int(thumb.get("width") or 0)
        height = int(thumb.get("height") or 0)
        # Prefer square-ish images (the avatar) over the wide banner.
        squareness = abs(width - height) / max(width, height, 1)
        candidates.append((squareness > 0.2, -min(width, height), url))

    if not candidates:
        raise SystemExit(
            "No channel artwork found. Save your own square image and run "
            "again with --source path/to/image.png"
        )
    candidates.sort()
    return candidates[0][2]


def _to_square(image: Image.Image, size: int) -> Image.Image:
    image = image.convert("RGB")
    if max(image.size) != size:
        scale = size / max(image.size)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )

    # Letterbox onto a square canvas, background sampled from the top-left
    # pixel so a solid-background logo stays seamless.
    canvas = Image.new("RGB", (size, size), image.getpixel((0, 0)))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", help="local image file to use instead")
    parser.add_argument("--size", type=int, default=TARGET, help="output edge in px")
    args = parser.parse_args(argv)
    size = max(1400, args.size)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print("Configuration error: %s" % exc, file=sys.stderr)
        return 2

    if args.source:
        image = Image.open(args.source)
        log("Using %s" % args.source)
    else:
        url = _channel_image_url(cfg.channel_id)
        log("Downloading channel artwork: %s" % url)
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))

    if min(image.size) < 1400:
        warn(
            "Source artwork is only %dx%d. It will be upscaled to %dpx to meet "
            "Spotify's minimum, which may look soft — supply a larger image "
            "with --source when you have one." % (image.width, image.height, size)
        )

    square = _to_square(image, size)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = DOCS_DIR / str(cfg.get("podcast.cover_image", "cover.jpg"))
    square.save(out, "JPEG", quality=90, optimize=True, progressive=False)

    log("Wrote %s (%dx%d, %.1f KB)"
        % (out, square.width, square.height, out.stat().st_size / 1024))
    try:
        log("Public URL once Pages is live: %s" % cfg.cover_url)
    except ConfigError:
        # Cosmetic only — the artwork is already written, and site.base_url is
        # not needed to produce it.
        warn("site.base_url is unset, so the public URL cannot be shown yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
