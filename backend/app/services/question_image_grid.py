"""One stitched figure per picture-bank question (owner ruling, 2026-08-21).

The owner's job-61 review: a classification question rendered EIGHT
separate ``[img]`` links (Drum, Table surface, Reed pipe, …) — one per
picture — where the textbook shows one labelled figure panel. The ruling
("Build it"): a question whose body carries two or more canonical image
tags ships ONE stitched, labelled grid image instead, hosted from this
app's own signed ``/source-assets`` route, and MCQ-style items are exempt
— each option must render its own image (the caller owns that exemption,
because the caller holds the recorded kind: ``sheet_kind`` in the
assessment lane, the inventory item's recorded ``options`` upstream).

Everything here is mechanics, never judgment (CLAUDE.md Rule 1): tag
parsing, byte downloads, deterministic grid composition (tile order = tag
order, labels = each tag's own alt text), content-addressed naming, and a
recorded outcome either way. A stitch that cannot complete — a download
fails, the public base URL is unconfigured, Pillow is missing — leaves
the text EXACTLY as it was and returns the failure named, for the caller
to record as a review flag; it never blocks a run and never half-stitches.
"""
from __future__ import annotations

import hashlib
import html
import io
import logging
import math
import re
import urllib.request
from typing import Any, Callable

from . import katex_rules

_LOGGER = logging.getLogger(__name__)

# The one canonical tag shape (katex_rules owns it; aliased so the mint
# and this consolidation pass cannot drift apart).
IMAGE_TAG_RE = katex_rules._CANONICAL_IMAGE_TAG_RE

# Deterministic layout constants. Changing any of these changes the
# stitched bytes (and therefore the content-addressed filename) for every
# future stitch, so they are named once here.
TILE_WIDTH = 420
LABEL_HEIGHT = 34
LABEL_FONT_SIZE = 22
GRID_MARGIN = 16
GRID_GUTTER = 12
JPEG_QUALITY = 85
_DOWNLOAD_TIMEOUT_SECONDS = 20
_DOWNLOAD_BYTE_CAP = 8 * 1024 * 1024

Downloader = Callable[[str], bytes]


def image_tags(text: str) -> list[dict[str, str]]:
    """Every canonical image tag in ``text``, in reading order."""
    return [
        {
            "tag": match.group(0),
            "src": match.group("src"),
            "alt": html.unescape(
                match.group("alt").replace("&#91;", "[").replace("&#93;", "]")
            ),
        }
        for match in IMAGE_TAG_RE.finditer(str(text or ""))
    ]


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Aegis/1.0"})
    with urllib.request.urlopen(
        request, timeout=_DOWNLOAD_TIMEOUT_SECONDS
    ) as response:
        data = response.read(_DOWNLOAD_BYTE_CAP + 1)
    if len(data) > _DOWNLOAD_BYTE_CAP:
        raise ValueError(f"image at {url!r} exceeds the download byte cap")
    if not data:
        raise ValueError(f"image at {url!r} returned no bytes")
    return data


def _stitch(images: list[tuple[bytes, str]]) -> bytes:
    """Compose one labelled grid JPEG from ordered (bytes, label) tiles.

    Near-square deterministic layout: ``ceil(sqrt(n))`` columns, tiles
    scaled to a fixed width preserving aspect, each label drawn beneath
    its tile in the default bundled font. Same inputs, same bytes.
    """
    from PIL import Image, ImageDraw, ImageFont

    tiles: list[tuple[Any, str]] = []
    for data, label in images:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        scale = TILE_WIDTH / float(image.width)
        image = image.resize(
            (TILE_WIDTH, max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
        tiles.append((image, label))

    columns = max(1, math.ceil(math.sqrt(len(tiles))))
    rows = math.ceil(len(tiles) / columns)
    row_heights: list[int] = []
    for row in range(rows):
        members = tiles[row * columns:(row + 1) * columns]
        row_heights.append(
            max(tile.height for tile, _ in members) + LABEL_HEIGHT
        )
    width = GRID_MARGIN * 2 + columns * TILE_WIDTH + (columns - 1) * GRID_GUTTER
    height = (
        GRID_MARGIN * 2 + sum(row_heights) + (rows - 1) * GRID_GUTTER
    )
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=LABEL_FONT_SIZE)
    except TypeError:  # older Pillow without the size parameter
        font = ImageFont.load_default()

    y = GRID_MARGIN
    for row in range(rows):
        members = tiles[row * columns:(row + 1) * columns]
        tallest = max(tile.height for tile, _ in members)
        for column, (tile, label) in enumerate(members):
            x = GRID_MARGIN + column * (TILE_WIDTH + GRID_GUTTER)
            canvas.paste(tile, (x, y + (tallest - tile.height) // 2))
            if label:
                draw.text(
                    (x + TILE_WIDTH / 2, y + tallest + LABEL_HEIGHT / 2),
                    label[:80],
                    fill="black",
                    font=font,
                    anchor="mm",
                )
        y += row_heights[row] + GRID_GUTTER

    output = io.BytesIO()
    canvas.save(output, format="JPEG", quality=JPEG_QUALITY)
    return output.getvalue()


def _publish(data: bytes, *, job_id: int) -> str:
    """Pin the stitched bytes and return their signed public URL."""
    from . import canonical_source_phase221_fallback as fallback
    from . import source_asset_store

    filename = f"{hashlib.sha256(data).hexdigest()}.jpg"
    url = fallback.asset_url(int(job_id), filename)
    source_asset_store.pin_asset(data, job_id=int(job_id), asset_url=url)
    return url


def consolidate_images(
    text: str,
    *,
    job_id: int,
    downloader: Downloader | None = None,
    cache: dict | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Replace a picture bank's many tags with one stitched-figure tag.

    Returns ``(new_text, record)``. Text with fewer than two canonical
    image tags returns unchanged with no record. On success the record
    carries the combined URL and every source image (src + alt); the
    first tag's position carries the combined tag and the others are
    removed. On ANY failure the text returns unchanged and the record
    names the error — the caller records it as a review flag; nothing is
    ever half-stitched or silently lost.

    ``cache`` (one dict per run) makes repeated banks — the same question
    in ``question`` and ``question_text``, or re-used source figures —
    stitch and publish exactly once per distinct ordered src list.
    """
    source = str(text or "")
    tags = image_tags(source)
    if len(tags) < 2:
        return source, None

    key = tuple(tag["src"] for tag in tags)
    record: dict[str, Any] = {
        "source_images": [
            {"src": tag["src"], "alt": tag["alt"]} for tag in tags
        ],
    }
    cached = (cache or {}).get(key)
    if cached is None:
        fetch = downloader or _download
        try:
            images = [
                (fetch(tag["src"]), tag["alt"]) for tag in tags
            ]
            data = _stitch(images)
            cached = _publish(data, job_id=job_id)
        except Exception as error:  # noqa: BLE001 — every failure is recorded
            _LOGGER.warning(
                "image consolidation failed for job %s: %s", job_id, error
            )
            record["error"] = f"{type(error).__name__}: {error}"
            return source, record
        if cache is not None:
            cache[key] = cached
    record["combined_image_url"] = cached

    labels = "; ".join(
        tag["alt"] for tag in tags if str(tag["alt"] or "").strip()
    )
    combined_alt = f"Combined figure ({len(tags)} pictures)"
    if labels:
        combined_alt = f"{combined_alt}: {labels}"
    combined_tag = katex_rules.image(cached, combined_alt[:500])

    replaced_first = False
    def _swap(match: re.Match) -> str:
        nonlocal replaced_first
        if replaced_first:
            return ""
        replaced_first = True
        return combined_tag

    new_text = IMAGE_TAG_RE.sub(_swap, source)
    new_text = re.sub(r"[ \t]{2,}", " ", new_text).strip()
    return new_text, record
