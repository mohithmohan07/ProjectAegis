"""Pass 1 — Chapter Reading: a model reads the source before any compiler.

Step 1 of ``docs/build-concepts-manual-process.md``. Textbooks vary too much
across boards, subjects and grades for pattern-matching to read them, so the
raw converted MMD is handed to the model first: it classifies every block
(prose / heading / figure / activity / info hub / question / furniture),
strips layout furniture, and returns a normalized MMD the deterministic
compiler can trust. Page position survives only as provenance (Rule 4a).

The pass never blocks a run. A chunk whose reading comes back unusable keeps
its original text and is flagged in the census for the reviewer; only a
definitive provider stop (exhausted quota) propagates. Readings are cached on
disk by content hash so a resumed run re-reads nothing and is never re-billed
— and, just as important, so every resume of one run sees the identical
normalized text.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from typing import Any, Callable

from .. import config
from . import progress, prompts

READING_VERSION = 1

# Census kinds, exactly as the process document names them.
BLOCK_KINDS = (
    "prose", "heading", "figure", "activity", "info_hub", "question",
    "furniture",
)

# Kind recorded when a chunk's reading was unusable and the original text was
# kept verbatim. Distinct from the model vocabulary on purpose: it marks a
# flagged fallback, not a classification.
UNREAD_KIND = "unread"

_MAX_CHUNK_CHARS = 9_000
_PREVIOUS_TAIL_CHARS = 400

_memory_lock = threading.Lock()
_memory_cache: dict[str, dict[str, Any]] = {}

NORMALIZE_SYSTEM = prompts.register(
    "chapter_reading.normalize.system",
    label="Chapter reading — classify and normalize",
    category="Chapter reading (Pass 1)",
    description=(
        "Reads one chunk of raw textbook MMD, classifies every block, drops "
        "layout furniture, and returns normalized MMD plus a block census."
    ),
    default=(
        "You are an expert textbook editor reading one chunk of a school "
        "textbook chapter that was OCR-converted to Mathpix Markdown (MMD). "
        "Conversions are messy: heading levels are unreliable, page headers "
        "and footers are interleaved with teaching text, and figures drift "
        "away from the material they illustrate.\n"
        "\n"
        "Read the chunk and return ONE JSON object:\n"
        "{\n"
        '  "normalized_mmd": "the chunk rewritten as clean MMD",\n'
        '  "blocks": [{"kind": "...", "label": "...", "page": null,'
        ' "note": ""}],\n'
        '  "dropped_furniture": ["each removed line, verbatim"]\n'
        "}\n"
        "\n"
        "Classify every block of the chunk into exactly one kind:\n"
        "- prose: teaching text.\n"
        "- heading: a topic or section boundary (record its title as label).\n"
        "- figure: an image, map, diagram or illustration, with its caption.\n"
        "- activity: a source-set activity or exercise embedded in the flow.\n"
        "- info_hub: a boxed aside, biography box, source excerpt, 'do you "
        "know?' panel.\n"
        "- question: anything that asks the learner to do something.\n"
        "- furniture: running headers, footers, page numbers, watermarks, "
        "reprint lines — layout debris with no teaching content.\n"
        "Set page to the printed page number when one is visible near the "
        "block, else null. Page is provenance only; never let it influence "
        "classification.\n"
        "\n"
        "Normalization rules — all of them are hard requirements:\n"
        "1. NEVER paraphrase, translate, summarize, reorder or invent "
        "teaching content. Every prose, figure, activity, info_hub and "
        "question block keeps its text verbatim.\n"
        "2. Keep every image tag of the form ![...](...) exactly as it "
        "appears, character for character, in normalized_mmd. Never drop or "
        "rewrite one.\n"
        "3. Keep all mathematics ($...$, $$...$$, \\( ... \\)) untouched.\n"
        "4. Rewrite headings as Markdown '#' headings whose depth is "
        "consistent with the outline of headings seen so far (provided in "
        "the input). Keep any section number in the heading text.\n"
        "5. Remove furniture blocks from normalized_mmd, and list every "
        "removed line verbatim in dropped_furniture. Remove nothing else.\n"
        "6. Keep question numbering exactly as printed.\n"
        "The chunk boundary is arbitrary: never add an introduction or a "
        "closing line, and never repeat the tail of the previous chunk."
    ),
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _image_tags(text: str) -> list[str]:
    """Every ``![...](...)`` occurrence — the tags the reading must preserve.

    Bookkeeping, not parsing: the tags were minted by the converter with an
    exact shape, and the census check only asks that each survives verbatim.
    """
    return re.findall(r"!\[[^\]]*\]\([^)]*\)", str(text or ""))


def _chunks(text: str, *, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split at blank-line boundaries so ``"".join(chunks) == text``."""
    source = str(text or "")
    if len(source) <= max_chars:
        return [source] if source else []
    # Paragraph units keep their trailing separators, so reassembly is exact.
    units = [u for u in re.split(r"(?<=\n)(?=\s*\n)", source) if u]
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current += unit
    if current:
        chunks.append(current)
    return chunks


def _cache_key(mmd_text: str) -> str:
    payload = "\0".join((
        f"chapter-reading-v{READING_VERSION}",
        config.OPENAI_MODEL,
        _sha256_text(prompts.get_text("chapter_reading.normalize.system")),
        _sha256_text(mmd_text),
    ))
    return _sha256_text(payload)[:32]


def _cache_path(key: str):
    directory = config.DATA_DIR / "chapter_reading"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}.json"


def _load_cached(key: str) -> dict[str, Any] | None:
    with _memory_lock:
        hit = _memory_cache.get(key)
    if hit is not None:
        return copy.deepcopy(hit)
    try:
        path = _cache_path(key)
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("normalized_mmd"):
                with _memory_lock:
                    _memory_cache[key] = copy.deepcopy(data)
                return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _store_cached(key: str, reading: dict[str, Any]) -> None:
    with _memory_lock:
        _memory_cache[key] = copy.deepcopy(reading)
    try:
        _cache_path(key).write_text(
            json.dumps(reading, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        # The in-memory copy still guarantees a consistent reading for every
        # replay inside this process; disk is only cross-restart insurance.
        pass


def _quota_stop(exc: Exception) -> bool:
    return "insufficient_quota" in str(exc)


def _chunk_census(
    decision: dict[str, Any], *, chunk_index: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for block in decision.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("kind") or "")
        if kind not in BLOCK_KINDS:
            kind = "prose"
        page = block.get("page")
        rows.append({
            "kind": kind,
            "label": str(block.get("label") or "")[:300],
            "page": int(page) if isinstance(page, (int, float)) else None,
            "note": str(block.get("note") or "")[:500],
            "chunk_index": chunk_index,
        })
    return rows


def _read_chunk(
    chunk: str,
    *,
    api_call: Callable[..., dict],
    chunk_index: int,
    chunk_total: int,
    source_filename: str,
    outline: list[str],
    previous_tail: str,
) -> tuple[str, list[dict[str, Any]], list[str], bool]:
    """Return (normalized_text, census_rows, dropped_furniture, fell_back)."""
    payload = {
        "source_filename": source_filename,
        "chunk_index": chunk_index + 1,
        "chunk_total": chunk_total,
        "headings_seen_so_far": outline[-40:],
        "previous_chunk_normalized_tail": previous_tail,
        "chunk_mmd": chunk,
    }
    try:
        decision = api_call(
            prompts.get_text("chapter_reading.normalize.system"),
            json.dumps(payload, ensure_ascii=False),
            purpose="source_extraction",
        )
    except Exception as exc:  # noqa: BLE001 — flag-and-continue, per Rule 1
        if _quota_stop(exc):
            raise
        progress.log(
            f"Chapter reading could not read chunk {chunk_index + 1}/"
            f"{chunk_total} ({type(exc).__name__}); keeping its original "
            "text and flagging it for review.",
            level="warning",
        )
        return chunk, _fallback_census(chunk_index, "provider failure"), [], True

    normalized = str((decision or {}).get("normalized_mmd") or "")
    if not normalized.strip():
        return (
            chunk,
            _fallback_census(chunk_index, "empty normalized text"),
            [],
            True,
        )
    for tag in _image_tags(chunk):
        if tag not in normalized:
            # A lost image is lost coverage; the original chunk keeps it.
            return (
                chunk,
                _fallback_census(chunk_index, f"image tag dropped: {tag[:120]}"),
                [],
                True,
            )
    dropped = [
        str(line)[:300]
        for line in (decision.get("dropped_furniture") or [])
        if str(line or "").strip()
    ]
    return normalized, _chunk_census(decision, chunk_index=chunk_index), dropped, False


def _fallback_census(chunk_index: int, reason: str) -> list[dict[str, Any]]:
    return [{
        "kind": UNREAD_KIND,
        "label": "",
        "page": None,
        "note": f"chunk kept verbatim: {reason}"[:500],
        "chunk_index": chunk_index,
    }]


def cached_reading(raw_source: str) -> dict[str, Any] | None:
    """The stored reading for this exact raw text — never a model call."""
    raw = str(raw_source or "")
    if not raw:
        return None
    return _load_cached(_cache_key(raw))


def normalized_for(raw_source: str) -> str:
    """The cached reading for this exact raw text, else the text unchanged.

    Cache-lookup only — never a model call. Deposit validation and semantic
    recovery resolve the raw audit copy through this so they see the same
    derived source the run compiled, mirroring how Phase 2.2 overlays are
    applied at those sites.
    """
    raw = str(raw_source or "")
    if not raw:
        return raw
    cached = cached_reading(raw)
    if cached is None:
        return raw
    return str(cached.get("normalized_mmd") or "") or raw


def read_chapter(
    mmd_text: str,
    *,
    source_filename: str = "",
    api_call: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Read one chapter: classify every block and normalize the MMD.

    Returns ``{"mode", "normalized_mmd", "census", "dropped_furniture",
    "provenance"}``. In dry mode (no live generation) the text passes through
    unchanged so the rest of the flow stays exercised.
    """
    raw = str(mmd_text or "")
    if not config.use_live_generation():
        return {
            "mode": "dry",
            "normalized_mmd": raw,
            "census": [],
            "dropped_furniture": [],
            "provenance": {
                "version": READING_VERSION,
                "input_sha256": _sha256_text(raw),
                "normalized_sha256": _sha256_text(raw),
                "chunk_count": 0,
                "fallback_chunks": 0,
            },
        }

    key = _cache_key(raw)
    cached = _load_cached(key)
    if cached is not None:
        return cached

    if api_call is None:
        from . import generation

        api_call = generation._openai_json

    chunks = _chunks(raw)
    progress.step(
        "Chapter reading — the model reads the source before compilation",
        value=0.005,
    )
    normalized_parts: list[str] = []
    census: list[dict[str, Any]] = []
    dropped: list[str] = []
    outline: list[str] = []
    fallbacks = 0
    for index, chunk in enumerate(chunks):
        text, rows, chunk_dropped, fell_back = _read_chunk(
            chunk,
            api_call=api_call,
            chunk_index=index,
            chunk_total=len(chunks),
            source_filename=source_filename,
            outline=outline,
            previous_tail=(
                normalized_parts[-1][-_PREVIOUS_TAIL_CHARS:]
                if normalized_parts else ""
            ),
        )
        normalized_parts.append(text)
        census.extend(rows)
        dropped.extend(chunk_dropped)
        fallbacks += 1 if fell_back else 0
        outline.extend(
            row["label"] for row in rows
            if row["kind"] == "heading" and row["label"]
        )

    normalized = "\n\n".join(
        part.strip("\n") for part in normalized_parts if part.strip()
    ) + ("\n" if normalized_parts else "")
    if not normalized.strip():
        normalized = raw
    counts: dict[str, int] = {}
    for row in census:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    reading = {
        "mode": "live",
        "normalized_mmd": normalized,
        "census": census,
        "dropped_furniture": dropped,
        "provenance": {
            "version": READING_VERSION,
            "model": config.OPENAI_MODEL,
            "input_sha256": _sha256_text(raw),
            "normalized_sha256": _sha256_text(normalized),
            "chunk_count": len(chunks),
            "fallback_chunks": fallbacks,
            "kind_counts": counts,
        },
    }
    _store_cached(key, reading)
    summary = ", ".join(
        f"{counts[k]} {k}" for k in sorted(counts) if counts[k]
    ) or "no blocks classified"
    progress.log(
        f"Chapter reading finished: {len(chunks)} chunk(s), {summary}; "
        f"{len(dropped)} furniture line(s) dropped"
        + (
            f"; {fallbacks} chunk(s) kept verbatim and flagged for review."
            if fallbacks else "."
        )
    )
    return reading
