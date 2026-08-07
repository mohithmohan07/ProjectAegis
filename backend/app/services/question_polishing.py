"""Pass 4 — Question Polishing: textbook wording becomes test-ready wording.

Step 4 of ``docs/build-concepts-manual-process.md``. Textbook phrasing is
often unusable as a standalone test item — "Look at the figure once again and
guess why…" presumes the book is open at that page. This pass rewrites every
question in the inventory into a self-contained form (the referenced figure
ships with the question), and records a **semantic split** where one question
genuinely spans more than one concept: fragments minted as ``QINV-0009.1``,
``QINV-0009.2``, each carrying the parent QID in its own id.

The polished form is a derived artifact. ``raw_task`` / ``normalized_task``
are never touched — they are the source audit copy, and every deterministic
anchor/match key downstream reads them. The polished wording travels in new
fields (``polished_task``, ``polish_flag``, ``polish_fragments``) that ride
the question-inventory checkpoint, and reaches the public workbook through
``generation._inventory_task_text`` (wrapped by the contract), which is the
single function all public Example wording flows through.

Polished wording ships flagged for review (Rule 1, amended): a batch the
model fumbles keeps its original wording and is flagged ``kept_original`` —
the run continues. Only a definitive quota denial stops it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Callable

from .. import config
from . import progress, prompts

POLISHING_VERSION = 1

# Flags recorded on inventory items. Absent flag == untouched source wording.
FLAG_POLISHED = "polished_for_review"
FLAG_SPLIT = "split_for_review"
FLAG_KEPT = "kept_original"

# Hub rows (Activities / experiment tasks) are placed as hub notes, not test
# questions, and Phase 3.9 compares their wire text exactly — never polished.
# Kept in lockstep with ``generation._HUB_INVENTORY_KINDS`` by a drift test.
SKIP_KINDS = frozenset({"activity", "experiment_task"})

_BATCH_SIZE = 12
_MAX_FRAGMENTS = 6

_memory_lock = threading.Lock()
_memory_cache: dict[str, dict[str, Any]] = {}

POLISH_SYSTEM = prompts.register(
    "concepts.question_polishing.system",
    label="Question polishing — standalone test wording",
    category="Question polishing (Pass 4)",
    description=(
        "Rewrites textbook questions into self-contained test items and "
        "splits questions that genuinely span more than one concept."
    ),
    default=(
        "You are an assessment editor. You receive a batch of questions "
        "taken verbatim from a school textbook chapter. Textbook wording "
        "often presumes the book is open at a page: 'Look at the figure "
        "once again and guess why…', 'as discussed above', 'in the picture "
        "on the previous page'. Your job is to rewrite each question as a "
        "properly phrased, self-contained test item.\n"
        "\n"
        "Return ONE JSON object:\n"
        '{"items": [{"qid": "...", "polished_task": "...",\n'
        '  "fragments": [{"polished_task": "...", "reason": "..."}],\n'
        '  "note": ""}]}\n'
        "\n"
        "Polishing rules — all hard requirements:\n"
        "1. NEVER change what the question asks, its difficulty, or its "
        "answer. You are rephrasing the ask, not redesigning it.\n"
        "2. Make it standalone: replace page-relative references ('the "
        "figure above', 'look again', 'as discussed earlier in this "
        "chapter') with self-contained phrasing such as 'The illustration "
        "provided shows …'. The referenced figure or image travels with the "
        "question, so it may be referred to as provided.\n"
        "3. Keep every image tag, every mathematical expression, and every "
        "multiple-choice option EXACTLY as given, character for character.\n"
        "4. Keep the question's language; never translate.\n"
        "5. Never answer the question, and never add solution hints.\n"
        "6. If the source wording is already a clean standalone test item, "
        "return it unchanged as polished_task.\n"
        "\n"
        "Splitting — the exception, not the rule:\n"
        "Return fragments ONLY when one question genuinely asks several "
        "independently answerable things that belong to different concepts "
        "or topics. Each fragment must be a self-contained polished "
        "question, and together the fragments must cover everything the "
        "original asked — never drop a part. Most questions produce no "
        "fragments. A multi-part question whose parts all exercise the same "
        "concept stays whole. When you do split, still return the whole "
        "question's polished form as polished_task.\n"
        "\n"
        "Return every qid you were given, exactly once."
    ),
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _item_source_text(item: dict[str, Any]) -> str:
    return str(
        item.get("raw_task") or item.get("normalized_task") or ""
    ).strip()


def _eligible(item: dict[str, Any]) -> bool:
    kind = str(item.get("source_kind") or "").strip().lower()
    return (
        kind not in SKIP_KINDS
        and bool(str(item.get("qid") or "").strip())
        and bool(_item_source_text(item))
    )


def _squash(value: str) -> str:
    return " ".join(str(value or "").split())


def _too_short(text: str) -> bool:
    try:
        from . import concept_validator as cv

        return bool(cv._example_too_short(text))
    except Exception:  # noqa: BLE001 — the validator is advisory here
        return not str(text or "").strip()


def _polish_is_usable(item: dict[str, Any], polished: str) -> str:
    """Empty string when usable, else the reason it is not."""
    if not str(polished or "").strip():
        return "empty polished wording"
    if _too_short(polished):
        return "polished wording too short"
    for option in item.get("options") or []:
        text = str(option or "").strip()
        if text and text not in polished:
            return f"dropped MCQ option {text[:60]!r}"
    return ""


def _cache_key(items: list[dict[str, Any]]) -> str:
    identity = [
        [str(item.get("qid") or ""), _item_source_text(item)]
        for item in items
    ]
    payload = "\0".join((
        f"question-polishing-v{POLISHING_VERSION}",
        config.OPENAI_MODEL,
        _sha256_text(prompts.get_text("concepts.question_polishing.system")),
        _sha256_text(json.dumps(identity, ensure_ascii=False)),
    ))
    return _sha256_text(payload)[:32]


def _cache_path(key: str):
    directory = config.DATA_DIR / "question_polishing"
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
            if isinstance(data, dict):
                with _memory_lock:
                    _memory_cache[key] = copy.deepcopy(data)
                return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _store_cached(key: str, decisions: dict[str, Any]) -> None:
    with _memory_lock:
        _memory_cache[key] = copy.deepcopy(decisions)
    try:
        _cache_path(key).write_text(
            json.dumps(decisions, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def _quota_stop(exc: Exception) -> bool:
    return "insufficient_quota" in str(exc)


def _batch_payload(meta: dict, batch: list[dict[str, Any]]) -> str:
    return json.dumps({
        "chapter": {
            key: str(meta.get(key) or "")
            for key in ("subject", "board", "grade", "chapter_title")
        },
        "questions": [
            {
                "qid": str(item.get("qid") or ""),
                "source_kind": str(item.get("source_kind") or ""),
                "task": _item_source_text(item),
                "options": [str(o) for o in (item.get("options") or [])],
                "has_images": bool(item.get("image_urls")),
            }
            for item in batch
        ],
    }, ensure_ascii=False)


def _decisions_via_api(
    meta: dict,
    eligible: list[dict[str, Any]],
    api_call: Callable[..., dict],
) -> dict[str, Any]:
    """Per-qid polish decisions, one model call per batch, flag on failure."""
    decisions: dict[str, Any] = {}
    system = prompts.get_text("concepts.question_polishing.system")
    batches = [
        eligible[start:start + _BATCH_SIZE]
        for start in range(0, len(eligible), _BATCH_SIZE)
    ]
    for index, batch in enumerate(batches, start=1):
        try:
            data = api_call(
                system,
                _batch_payload(meta, batch),
                purpose="source_extraction",
            )
        except Exception as exc:  # noqa: BLE001 — flag-and-continue
            if _quota_stop(exc):
                raise
            progress.log(
                f"Question polishing batch {index}/{len(batches)} failed "
                f"({type(exc).__name__}); keeping original wording for its "
                f"{len(batch)} question(s) and flagging them for review.",
                level="warning",
            )
            for item in batch:
                decisions[str(item["qid"])] = {
                    "flag": FLAG_KEPT, "note": "polishing batch failed",
                }
            continue
        returned = {
            str(row.get("qid") or ""): row
            for row in (data.get("items") or [])
            if isinstance(row, dict)
        }
        for item in batch:
            qid = str(item["qid"])
            row = returned.get(qid)
            if row is None:
                decisions[qid] = {
                    "flag": FLAG_KEPT, "note": "qid missing from response",
                }
                continue
            decisions[qid] = _decision_for(item, row)
    return decisions


def _decision_for(item: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    polished = str(row.get("polished_task") or "").strip()
    defect = _polish_is_usable(item, polished)
    if defect:
        return {"flag": FLAG_KEPT, "note": defect}

    fragments: list[dict[str, str]] = []
    for offset, fragment in enumerate(
        list(row.get("fragments") or [])[:_MAX_FRAGMENTS], start=1
    ):
        if not isinstance(fragment, dict):
            continue
        text = str(fragment.get("polished_task") or "").strip()
        if not text or _too_short(text):
            # One unusable fragment invalidates the split, never the polish:
            # partial fragments could not cover everything the original asked.
            fragments = []
            break
        fragments.append({
            "fragment_qid": f"{item['qid']}.{offset}",
            "polished_task": text,
            "reason": str(fragment.get("reason") or "")[:300],
        })
    if len(fragments) == 1:
        fragments = []  # a "split" into one part is not a split

    unchanged = _squash(polished) == _squash(_item_source_text(item))
    if unchanged and not fragments:
        return {"note": str(row.get("note") or "")[:300]}
    decision: dict[str, Any] = {
        "polished_task": polished,
        "flag": FLAG_SPLIT if fragments else FLAG_POLISHED,
        "note": str(row.get("note") or "")[:300],
    }
    if fragments:
        decision["fragments"] = fragments
    return decision


def polish_inventory(
    inventory: dict[str, Any],
    *,
    meta: dict | None = None,
    api_call: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Polish every eligible inventory question in place (on a copy).

    Adds ``polished_task`` / ``polish_flag`` / ``polish_note`` and, where the
    model split, ``polish_fragments`` — the source fields stay untouched.
    Placement of fragments is Pass 5's job; this pass records them with
    stable minted fragment qids so the split survives the checkpoint.
    """
    result = copy.deepcopy(inventory or {})
    items = [
        item for item in (result.get("items") or []) if isinstance(item, dict)
    ]
    eligible = [item for item in items if _eligible(item)]
    if not eligible or not config.use_live_generation():
        return result

    key = _cache_key(eligible)
    decisions = _load_cached(key)
    if decisions is None:
        if api_call is None:
            from . import generation

            api_call = generation._openai_json
        progress.step(
            "Question polishing — rewriting textbook wording into "
            "standalone test items",
            value=0.705,
        )
        decisions = _decisions_via_api(meta or {}, eligible, api_call)
        _store_cached(key, decisions)

    polished_count = split_count = kept_count = 0
    for item in eligible:
        decision = decisions.get(str(item["qid"]))
        if not isinstance(decision, dict):
            continue
        note = str(decision.get("note") or "")
        flag = str(decision.get("flag") or "")
        if flag == FLAG_KEPT:
            kept_count += 1
            item["polish_flag"] = FLAG_KEPT
            item["polish_note"] = note
            continue
        if not flag:
            continue  # already standalone; nothing recorded
        item["polished_task"] = str(decision.get("polished_task") or "")
        item["polish_flag"] = flag
        item["polish_note"] = note
        fragments = decision.get("fragments") or []
        if fragments:
            item["polish_fragments"] = copy.deepcopy(fragments)
            split_count += 1
        else:
            polished_count += 1
    progress.log(
        f"Question polishing: {polished_count} question(s) rewritten, "
        f"{split_count} split into fragments, {kept_count} kept original "
        f"and flagged, "
        f"{len(eligible) - polished_count - split_count - kept_count} "
        "already standalone."
    )
    return result
