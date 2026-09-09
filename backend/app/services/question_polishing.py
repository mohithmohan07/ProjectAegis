"""Pass 4 — source-preserving wording for Concept Examples.

Step 4 of ``docs/build-concepts-manual-process.md``. Textbook phrasing is
often unusable as a standalone test item — "Look at the figure once again and
guess why…" presumes the book is open at that page. This pass may clarify
question wording using supplied context (the referenced figure ships with
the question). Oral, practical and collaborative tasks keep their original
response modality and assessed skill. Questions are NEVER split: a multi-part
question (sub-questions included) stays one item and is placed as one unit by the
routing rules — a question spanning concepts or topics goes to the
appropriate culmination concept, whole.

The polished form is a derived artifact. ``raw_task`` / ``normalized_task``
are never touched — they are the source audit copy, and every deterministic
anchor/match key downstream reads them. The polished wording travels in new
fields (``polished_task``, ``polish_flag``, ``polish_fragments``) that ride
the question-inventory checkpoint, and reaches the public workbook through
``generation._inventory_task_text`` (wrapped by the contract), which is the
single function all public Example wording flows through.

An independent critic reviews each batch against the original evidence;
its dissent is advisory and travels in ``polish_audit`` with the source and
author proposal. This derived Concept Example wording does not authorize
rewriting source questions in the Master (Q27).

Polished wording ships flagged for review (Rule 1, amended): a batch the
model fumbles keeps its original wording and is flagged ``kept_original`` —
the run continues. A definitive author quota denial still stops it; an
unavailable critic is recorded as an advisory review flag.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Callable

from .. import config
from . import column_spec, containers
from . import progress, prompts

POLISHING_VERSION = 3

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
    label="Question polishing — source-preserving Example wording",
    category="Question polishing (Pass 4)",
    description=(
        "Clarifies Concept Example wording without changing the source ask, "
        "skill or response modality; an independent critic records its review."
    ),
    default=(column_spec.OUTPUT_DISCIPLINE +
        "You are an assessment editor. You receive a batch of questions "
        "taken verbatim from a school textbook chapter. Textbook wording "
        "often presumes the book is open at a page: 'Look at the figure "
        "once again and guess why…', 'as discussed above', 'in the picture "
        "on the previous page'. Your job is to rewrite each question as a "
        "properly phrased, self-contained Concept Example wherever the supplied "
        "evidence permits. This is a derived display artifact only: never "
        "authorize rewriting source question/question_text in the Master (Q27).\n"
        "\n"
        "Return ONE JSON object:\n"
        '{"items": [{"qid": "...", "polished_task": "...", "note": ""}]}\n'
        "\n"
        "Polishing rules — all hard requirements:\n"
        "1. NEVER change what the question asks, its difficulty, or its "
        "answer, assessed skill, response modality, or source facts. You may "
        "clarify wording, not redesign the task.\n"
        "2. Make it standalone: replace page-relative references ('the "
        "figure above', 'look again', 'as discussed earlier in this "
        "chapter') with self-contained phrasing such as 'The illustration "
        "provided shows …'. The referenced figure or image travels with the "
        "question, so it may be referred to as provided. Book-referencing "
        "phrasing is the same defect: never keep wording that sends the "
        "learner to the book or names its apparatus — 'According to the "
        "passage about zero', \"According to 'At a Glance' in the book\", "
        "'as given in your textbook'. State the needed context inside the "
        "item instead.\n"
        "2a. Strip carried source numbering from the stem: a leading "
        "enumerator the book printed before the ask — '6.', 'Q3.', "
        "'(iii)', '(iv)' — is page apparatus, not part of the question. "
        "Internal sub-part labels a), b), c) inside a multi-part question "
        "stay exactly as given (rule 7).\n"
        "2b. Preserve oral, listening, practical and collaborative tasks as "
        "those tasks: read aloud, listen, discuss in pairs, act out, measure, "
        "or observe must not become written recall about the same content. "
        "Reading aloud for pronunciation is an oral skill, not a written "
        "question about the words. Retain the original ask and response "
        "modality; if it needs unavailable audio, materials or participants, "
        "keep the wording and name that limitation in note. Do not invent a "
        "substitute task or classify away an unsuitable item.\n"
        "3. Keep every image tag, every mathematical expression, and every "
        "multiple-choice option EXACTLY as given, character for character. "
        "Keep all image IDs/URLs and KaTeX delimiters; never fabricate visual "
        "detail from an image URL or replace it with a guessed description.\n"
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
        "   - Make the original task understandable from the supplied "
        "materials without changing what the learner must do. Missing "
        "evidence is a limitation to record, never permission to invent it.\n"
        "9. A question carrying a shared_context field references material "
        "printed near it in the book ('the lists above', 'the following "
        "table') that does NOT travel with the question. It is never "
        "standalone as printed: rewrite it as a direct instruction that "
        "EMBEDS the needed material from shared_context inside "
        "polished_task, so the item is complete without the book open. Copy "
        "only the required supplied context; preserve its numbers, facts, "
        "conditions, wording of passages and visual references. If it is "
        "insufficient, keep the source wording and explain the gap in note.\n"
        "\n"
        "Return every qid you were given, exactly once."
    ),
)

POLISH_CRITIC_SYSTEM = prompts.register(
    "concepts.question_polishing.critic",
    label="Question polishing — independent source review",
    category="Question polishing (Pass 4)",
    description="Advisory review of Concept Example wording against source evidence.",
    default=(column_spec.OUTPUT_DISCIPLINE + column_spec.REVIEW_QUALITY +
        "You are the independent advisory critic of Aegis question polishing. "
        "Compare each proposed_task with its original task and source evidence. "
        "Check preservation of the complete ask, assessed skill, answer space, "
        "difficulty, language, response modality, facts, conditions, ordered "
        "sub-parts, options, image IDs/URLs and exact mathematical expressions "
        "including KaTeX delimiters. Oral pronunciation, listening, discussion "
        "and practical performance must not become written recall. Context "
        "may only be embedded from supplied evidence; an image URL alone "
        "cannot justify invented visual details. Do not require a written "
        "substitute for an oral task. This is derived Concept Example wording, "
        "not permission to rewrite source Master questions under Q27. Source "
        "page numbering and an embedded textbook answer may be omitted from "
        "this derived display, but never remove an asked part or a condition "
        "needed to answer it. "
        "Keep unchanged source wording when evidence is insufficient; a note "
        "about the missing material is appropriate. Judge the actual proposal "
        "independently without rewriting it. Dissent is advisory and never "
        "removes, merges or gates an item. Return every supplied qid exactly "
        "once in one JSON object: "
        '{"items":[{"qid":"...","verdict":"verified|dissent",'
        '"issues":["specific defect and supporting source evidence"]}]}. '
        "Use verified with an empty issues array when there is no defect."
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

    Only mechanical defects revert a polish: an empty rewrite, a dropped
    MCQ option, or a dropped inline image URL. Whether wording is usable is a judgment
    the polish model and its critic own — no length check second-guesses it.
    """
    if not str(polished or "").strip():
        return "empty polished wording"
    for option in item.get("options") or []:
        text = str(option or "").strip()
        if text and text not in polished:
            return f"dropped MCQ option {text[:60]!r}"
    source_text = _item_source_text(item)
    for image_url in item.get("image_urls") or []:
        url = str(image_url or "")
        if url and url in source_text and url not in polished:
            return "dropped inline source image URL"
    return ""


def _cache_key(items: list[dict[str, Any]], meta: dict | None = None) -> str:
    # Source context/assets and both live prompt texts define the decision.
    # Version 2 author-only caches must never suppress the new review call.
    payload = "\0".join((
        f"question-polishing-v{POLISHING_VERSION}",
        config.OPENAI_MODEL,
        _sha256_text(prompts.get_text("concepts.question_polishing.system")),
        _sha256_text(prompts.get_text("concepts.question_polishing.critic")),
        _sha256_text(_batch_payload(meta or {}, items)),
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
                "image_urls": copy.deepcopy(item.get("image_urls") or []),
                "source_evidence": {
                    key: copy.deepcopy(item[key])
                    for key in (
                        "raw_task", "normalized_task", "raw_solution_or_answer",
                        "source_label", "page_hint", "block_ids", "image_ids",
                        "content_objects", "requires_visual",
                    )
                    if key in item
                },
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


def _review_batch(
    payload: dict[str, Any], api_call: Callable[..., dict],
) -> dict[str, dict[str, Any]]:
    """Record critic dissent or failure without changing the author's decision."""
    qids = [question["qid"] for question in payload["questions"]]
    try:
        data = api_call(
            prompts.get_text("concepts.question_polishing.critic"),
            json.dumps(payload, ensure_ascii=False),
            purpose="advisory_critic",
        )
    except Exception as exc:  # the advisory auditor cannot take the run down
        progress.log(
            "Question polishing critic failed to run "
            f"({type(exc).__name__}); retaining the proposals with review flags.",
            level="warning",
        )
        return {
            qid: {"verdict": "unavailable", "issues": [
                f"independent critic failed ({type(exc).__name__})",
            ]}
            for qid in qids
        }
    rows = data.get("items") if isinstance(data, dict) else None
    reviews: dict[str, dict[str, Any]] = {}
    for qid in qids:
        matches = [
            row for row in rows or []
            if isinstance(row, dict) and row.get("qid") == qid
        ] if isinstance(rows, list) else []
        row = matches[0] if len(matches) == 1 else {}
        issues = row.get("issues")
        verdict = row.get("verdict")
        if (
            verdict not in ("verified", "dissent")
            or not isinstance(issues, list)
            or any(not isinstance(issue, str) for issue in issues)
            or (verdict == "verified" and bool(issues))
        ):
            reviews[qid] = {
                "verdict": "malformed",
                "issues": ["independent critic response is missing or malformed"],
                "response": copy.deepcopy(data),
            }
        else:
            reviews[qid] = {"verdict": verdict, "issues": list(issues)}
    return reviews


def _decisions_via_api(
    meta: dict,
    eligible: list[dict[str, Any]],
    api_call: Callable[..., dict],
) -> dict[str, Any]:
    """One author and one independent advisory critic call per successful batch."""
    decisions: dict[str, Any] = {}
    system = prompts.get_text("concepts.question_polishing.system")
    batches = [
        eligible[start:start + _BATCH_SIZE]
        for start in range(0, len(eligible), _BATCH_SIZE)
    ]
    for index, batch in enumerate(batches, start=1):
        payload_text = _batch_payload(meta, batch)
        source_payload = json.loads(payload_text)
        try:
            data = api_call(
                system,
                payload_text,
                purpose="source_extraction",
            )
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("polishing response must contain an items array")
        except Exception as exc:  # noqa: BLE001 — flag-and-continue
            if _quota_stop(exc):
                raise
            progress.log(
                f"Question polishing batch {index}/{len(batches)} failed "
                f"({type(exc).__name__}); keeping original wording for its "
                f"{len(batch)} question(s) and flagging them for review.",
                level="warning",
            )
            for item, evidence in zip(batch, source_payload["questions"]):
                decisions[str(item["qid"])] = {
                    "flag": FLAG_KEPT, "note": "polishing batch failed",
                    "audit": {
                        "version": POLISHING_VERSION,
                        "source_evidence": evidence,
                        "critic": {"verdict": "not_run", "issues": [
                            "author failed; source wording retained unchanged",
                        ]},
                    },
                }
            continue
        review_payload = copy.deepcopy(source_payload)
        for item, evidence, review_item in zip(
            batch, source_payload["questions"], review_payload["questions"],
        ):
            qid = str(item["qid"])
            matches = [
                row for row in data["items"]
                if isinstance(row, dict) and row.get("qid") == qid
            ]
            row = matches[0] if len(matches) == 1 else None
            if row is None or not isinstance(row.get("polished_task"), str):
                decisions[qid] = {
                    "flag": FLAG_KEPT,
                    "note": "qid missing, duplicated, or malformed in response",
                }
            else:
                decisions[qid] = _decision_for(item, row)
            # Critic sees the proposed shipping wording and original evidence,
            # not the author's reasoning, so it can assess the edit independently.
            review_item["proposed_task"] = (
                decisions[qid].get("polished_task") or _item_source_text(item)
            )
            decisions[qid]["audit"] = {
                "version": POLISHING_VERSION,
                "source_evidence": evidence,
                "author": copy.deepcopy(row),
                "proposed_task": review_item["proposed_task"],
            }
        reviews = _review_batch(review_payload, api_call)
        for item in batch:
            qid = str(item["qid"])
            decisions[qid]["audit"]["critic"] = reviews[qid]
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

    Adds ``polished_task`` / ``polish_flag`` / ``polish_note`` and
    ``polish_audit`` — the source fields stay untouched. Questions remain
    whole; the split helpers at the end only preserve legacy checkpoints.
    """
    result = copy.deepcopy(inventory or {})
    items = [
        item for item in (result.get("items") or []) if isinstance(item, dict)
    ]
    eligible = [item for item in items if _eligible(item)]
    if not eligible or not config.use_live_generation():
        return result

    key = _cache_key(eligible, meta)
    decisions = _load_cached(key)
    if decisions is None:
        if api_call is None:
            from . import generation

            api_call = generation._openai_json
        progress.step(
            "Question polishing — clarify and review source-preserving "
            "Concept Example wording",
            value=0.705,
        )
        decisions = _decisions_via_api(meta or {}, eligible, api_call)
        _store_cached(key, decisions)

    polished_count = kept_count = 0
    for item in eligible:
        decision = decisions.get(str(item["qid"]))
        if not isinstance(decision, dict):
            continue
        # Applying a newly reviewed decision must not leave an obsolete polish
        # on an item when this author has returned to its source wording.
        for field in ("polished_task", "polish_flag", "polish_note"):
            item.pop(field, None)
        if isinstance(decision.get("audit"), dict):
            item["polish_audit"] = copy.deepcopy(decision["audit"])
            item["polish_review_required"] = (
                decision["audit"].get("critic", {}).get("verdict") != "verified"
            )
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
    review_count = sum(bool(item.get("polish_review_required")) for item in eligible)
    if review_count:
        progress.log(
            f"Question polishing: {review_count} independent review flag(s); "
            "source evidence, proposal and critic findings are in polish_audit. "
            "The review is advisory; every question is retained.",
            level="warning",
        )
    # Legacy inventories persisted by earlier versions may still carry
    # expanded fragments; both helpers are no-ops on never-split inventories.
    return supersede_restored_parents(expand_split_items(result))
