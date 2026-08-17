"""Pass 4 — Question Polishing: textbook wording becomes test-ready wording.

Step 4 of ``docs/build-concepts-manual-process.md``. Textbook phrasing is
often unusable as a standalone test item — "Look at the figure once again and
guess why…" presumes the book is open at that page. This pass rewrites every
question in the inventory into a self-contained form (the referenced figure
ships with the question). Questions are NEVER split: a multi-part question
(sub-questions included) stays one item and is placed as one unit by the
routing rules — a question spanning concepts or topics goes to the
appropriate culmination concept, whole.

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
from . import containers
from . import progress, prompts

POLISHING_VERSION = 2

# Flags recorded on inventory items. Absent flag == untouched source wording.
FLAG_POLISHED = "polished_for_review"
# Legacy flag: splits are no longer produced (questions are never broken),
# but inventories persisted by earlier versions may still carry it.
FLAG_SPLIT = "split_for_review"
FLAG_KEPT = "kept_original"

# Hub rows (Activities / experiment tasks / info hubs) are placed as hub
# notes, not test questions, and Phase 3.9 compares their wire text exactly —
# never polished. The vocabulary is ``containers.HUB_INVENTORY_KINDS`` —
# the single container home; a lockstep test pins every consumer to it.
SKIP_KINDS = containers.HUB_INVENTORY_KINDS

_BATCH_SIZE = 12

_memory_lock = threading.Lock()
_memory_cache: dict[str, dict[str, Any]] = {}

# Appended to the Type-mining prompts so classification understands the
# artifacts this pass adds to inventory items.
FRAGMENT_MINING_NOTE = (
    "\n\nPolished inventory wording:\n"
    "- When an item carries polished_task, that is the question's shipping "
    "wording; raw_task is the source audit copy. Classify by what the "
    "polished wording asks, and copy the polished_task IN FULL as the "
    "example_prompt — never the raw textbook prose it replaced.\n"
    "- A multi-part question (sub-parts a), b), c) …) is ONE question. "
    "Classify it as a single item by everything it asks together — never "
    "break its parts into separate Cases or Examples. If its parts span "
    "different concepts or topics, that makes it a culmination-level "
    "question, not several questions.\n"
)

POLISH_SYSTEM = prompts.register(
    "concepts.question_polishing.system",
    label="Question polishing — standalone test wording",
    category="Question polishing (Pass 4)",
    description=(
        "Rewrites textbook questions into self-contained test items; never "
        "splits — a multi-part question stays one question."
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
        '{"items": [{"qid": "...", "polished_task": "...", "note": ""}]}\n'
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
        "7. NEVER split a question. A multi-part question (sub-questions "
        "a), b), c) …) is one question and stays one question, with every "
        "part kept in order inside polished_task. Do not drop, merge, or "
        "reorder parts.\n"
        "8. Textbooks often phrase checkpoints as teaching prose rather "
        "than test items. These are still questions and MUST become proper "
        "standalone test items:\n"
        "   - A rhetorical chain that answers itself ('Now, what do you "
        "need? … You will find that you need both a and d.') keeps only "
        "the real ask and DROPS every sentence that states or hints at "
        "the answer. Removing the textbook's own embedded answer is "
        "required — it is not a change to the ask.\n"
        "   - A pointer like 'It is left as an exercise for you to "
        "explain why each of the lists above is an AP' becomes a direct "
        "instruction, with the referenced lists/data carried into the "
        "question or described as provided.\n"
        "   - The polished item must read as something a student can be "
        "handed cold and answer.\n"
        "9. A question carrying a shared_context field references material "
        "printed near it in the book ('the lists above', 'the following "
        "table') that does NOT travel with the question. It is never "
        "standalone as printed: rewrite it as a direct instruction that "
        "EMBEDS the needed material from shared_context inside "
        "polished_task, so the item is complete without the book open.\n"
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


def _polish_is_usable(item: dict[str, Any], polished: str) -> str:
    """Empty string when usable, else the reason it is not.

    Only mechanical defects revert a polish: an empty rewrite, or a dropped
    MCQ option. Whether short wording is a usable test item is a judgment
    the polish model and its critic own — no length check second-guesses it.
    """
    if not str(polished or "").strip():
        return "empty polished wording"
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
                **(
                    {
                        "requires_context": True,
                        "shared_context": str(
                            item.get("shared_context") or ""
                        ),
                    }
                    if item.get("requires_context")
                    or str(item.get("shared_context") or "").strip()
                    else {}
                ),
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

    # Questions are never split: any fragments a model returns are ignored;
    # the whole question's polished form is the only shipping artifact.
    unchanged = _squash(polished) == _squash(_item_source_text(item))
    if unchanged:
        return {"note": str(row.get("note") or "")[:300]}
    return {
        "polished_task": polished,
        "flag": FLAG_POLISHED,
        "note": str(row.get("note") or "")[:300],
    }


def _fragment_items(parent: dict[str, Any]) -> list[dict[str, Any]]:
    """First-class inventory items for one split parent.

    A fragment keeps the parent's source audit identity — ``raw_task``,
    ``normalized_task``, ``source_label`` — so deterministic anchors still
    recognize where it came from, while its ``polished_task`` is its own
    self-contained ask and its dotted qid names the parent. Fragment qids are
    re-minted from the parent's current qid so a re-expansion after an
    inventory refresh can never drift from it.
    """
    parent_qid = str(parent.get("qid") or "").strip()
    items: list[dict[str, Any]] = []
    for offset, fragment in enumerate(
        parent.get("polish_fragments") or [], start=1
    ):
        if not isinstance(fragment, dict):
            continue
        item = {
            key: copy.deepcopy(value)
            for key, value in parent.items()
            if key != "polish_fragments"
        }
        item["qid"] = f"{parent_qid}.{offset}"
        item["parent_qid"] = parent_qid
        item["polished_task"] = str(fragment.get("polished_task") or "")
        item["polish_flag"] = FLAG_SPLIT
        item["polish_note"] = str(fragment.get("reason") or "")
        items.append(item)
    return items


def expand_split_items(inventory: dict[str, Any]) -> dict[str, Any]:
    """Replace each split parent with its fragment items, in place.

    The parent is preserved verbatim under ``split_parents`` so the split is
    auditable and reversible (:func:`collapse_split_items`). Idempotent: an
    already-expanded inventory has no item carrying ``polish_fragments``.
    """
    items = inventory.get("items") or []
    expanded: list[Any] = []
    parents = [
        parent for parent in inventory.get("split_parents") or []
        if isinstance(parent, dict)
    ]
    for item in items:
        if isinstance(item, dict) and item.get("polish_fragments"):
            fragments = _fragment_items(item)
            if fragments:
                expanded.extend(fragments)
                parents.append(copy.deepcopy(item))
                continue
        expanded.append(item)
    inventory["items"] = expanded
    if parents:
        inventory["split_parents"] = parents
    return inventory


def supersede_restored_parents(inventory: dict[str, Any]) -> dict[str, Any]:
    """Drop any item whose fragments are also present, in place.

    Self-healing against persistence gaps: several store paths rebuild the
    inventory from the ACSD task ledger or deterministic anchors, which know
    nothing of polishing and re-mint a split question's parent as a fresh
    item. Job 15 showed what that costs — the re-minted compound parent
    re-entered exact-once coverage on every replay ("2 missed items"), could
    never be certified, and ultimately stopped the run. The fragments are
    the authoritative form of a split question; a parent standing beside
    them is a resurrection artifact, moved back to ``split_parents``.
    """
    items = [
        item for item in inventory.get("items") or []
        if isinstance(item, dict)
    ]
    fragment_parent_qids = {
        str(item.get("parent_qid") or "").strip()
        for item in items
        if str(item.get("parent_qid") or "").strip()
        and item.get("polish_flag") == FLAG_SPLIT
    }
    if not fragment_parent_qids:
        return inventory
    kept: list[Any] = []
    superseded: list[dict[str, Any]] = []
    for item in inventory.get("items") or []:
        qid = (
            str(item.get("qid") or "").strip()
            if isinstance(item, dict) else ""
        )
        if qid and qid in fragment_parent_qids:
            superseded.append(copy.deepcopy(item))
            continue
        kept.append(item)
    if not superseded:
        return inventory
    inventory["items"] = kept
    inventory["split_parents"] = [
        *(inventory.get("split_parents") or []), *superseded,
    ]
    progress.log(
        "Question polishing superseded "
        f"{len(superseded)} restored split parent(s) "
        f"({', '.join(str(p.get('qid') or '') for p in superseded[:6])}); "
        "their fragments are the authoritative questions."
    )
    return inventory


def collapse_split_items(inventory: dict[str, Any]) -> dict[str, Any]:
    """Restore split parents in place of their fragments, in place.

    Used around the resumed-inventory anchor refresh: the parent — with its
    original source wording and label — is what the deterministic anchors
    know, and a fragment group standing where it stood could be mistaken for
    a redundant umbrella row and pruned. Parents whose fragments are no
    longer present stay in ``split_parents`` untouched.
    """
    parents = {
        str(parent.get("qid") or "").strip(): parent
        for parent in inventory.get("split_parents") or []
        if isinstance(parent, dict) and str(parent.get("qid") or "").strip()
    }
    if not parents:
        return inventory
    collapsed: list[Any] = []
    restored: set[str] = set()
    for item in inventory.get("items") or []:
        parent_qid = (
            str(item.get("parent_qid") or "").strip()
            if isinstance(item, dict) else ""
        )
        if parent_qid and parent_qid in parents:
            if parent_qid not in restored:
                collapsed.append(copy.deepcopy(parents[parent_qid]))
                restored.add(parent_qid)
            continue
        collapsed.append(item)
    inventory["items"] = collapsed
    inventory["split_parents"] = [
        parent for qid, parent in parents.items() if qid not in restored
    ]
    return inventory


def polish_inventory(
    inventory: dict[str, Any],
    *,
    meta: dict | None = None,
    api_call: Callable[..., dict] | None = None,
) -> dict[str, Any]:
    """Polish every eligible inventory question in place (on a copy).

    Adds ``polished_task`` / ``polish_flag`` / ``polish_note`` — the source
    fields stay untouched. Where the model split a question, the parent is
    replaced by first-class fragment items (dotted qids, parent preserved
    under ``split_parents``), so mining, Phase 3.3 certification and the
    closed-inventory coverage all treat each fragment as an ordinary
    question with its own exact-once placement.
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

    polished_count = kept_count = 0
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
        polished_count += 1
    progress.log(
        f"Question polishing: {polished_count} question(s) rewritten, "
        f"{kept_count} kept original and flagged, "
        f"{len(eligible) - polished_count - kept_count} already standalone. "
        "Questions are never split; multi-part questions stay whole."
    )
    # Legacy inventories persisted by earlier versions may still carry
    # expanded fragments; both helpers are no-ops on never-split inventories.
    return supersede_restored_parents(expand_split_items(result))
