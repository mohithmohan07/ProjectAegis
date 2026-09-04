"""Phase 2.1.2 — semantic task-membership adjudication for text sources.

Rule 1 repair (docs/spec-qx.md): on the ``.mmd``/``.md``/``.txt`` lane,
whether a learner-visible ask exists was decided by finite English regex
vocabularies and numeric bounds. This module makes task membership a
closed-world, per-block MODEL verdict:

* every content block of the canonical source receives exactly one author
  verdict (``contains_tasks`` / ``task_continuation`` / ``task_context`` /
  ``not_task`` / ``uncertain``);
* the deterministic parser's tasks (anchors + Phase-2.1 recovery) are
  presented as CANDIDATES — span and granularity evidence the author
  confirms or rejects, never an authority;
* asks no candidate covers are created directly from quoted block
  evidence (the text-lane analogue of the PDF page ledger's direct task
  creation);
* an independent critic reviews the complete accounting — its dissent is
  a durable review flag, never a gate (Q10);
* a block the author could not decide goes to the Fixer: one recorded,
  content-addressed best-judgment verdict, a flagged occurrence, and a
  completed adjudication (Q13);
* only provider unavailability (or a verdict contract that cannot be made
  mechanically applicable) ends without a decision — loudly, as an
  ``unadjudicated`` result the compile seam turns into a pre-spend block.

Everything deterministic in here is mechanics: schema validation, span
location, ordering, ID minting, caching, accounting. No regex, keyword,
length bound, or count decides what the source means.

Wiring note (QX2): this module is invoked from the Phase 2.1.2 compile
contract after Phase 2.1 hardening; it never runs inside artifact
builders. Visual-ownership normalization runs BEFORE adjudication, so a
task created here from missed-ask evidence carries no visual ownership
pass — recorded in the ledger rather than assumed away.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Mapping, Sequence

from .. import config
from . import canonical_source
from . import canonical_source_phase21_structure as structure
from . import progress

QX_VERSION = "1.0.0"
QX_COMPILER = "qx-task-membership-1"
LEDGER_KEY = "task_verdict_ledger"

_VERDICTS = (
    "contains_tasks",
    "task_continuation",
    "task_context",
    "not_task",
    "uncertain",
)
# The Fixer must decide; "uncertain" is the author's honesty valve only.
_FINAL_VERDICTS = tuple(v for v in _VERDICTS if v != "uncertain")

_CACHE_DIR = config.DATA_DIR / "qx-task-verdict-cache"


def _batch_size() -> int:
    # Transport only: how many blocks share one provider call. Never a
    # membership decision — reconciliation is batch-blind.
    try:
        return max(1, int(os.environ.get("AEGIS_QX_BATCH_BLOCKS", "40")))
    except ValueError:
        return 40


def _max_output_tokens() -> int:
    try:
        return max(
            1000, int(os.environ.get("AEGIS_QX_MAX_OUTPUT_TOKENS", "20000"))
        )
    except ValueError:
        return 20000


def _provider_ready() -> bool:
    return config.use_live_generation()


def _call_provider(
    *,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    purpose: str = "source_extraction",
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """One bounded strict-schema text call through the shared Aegis controls.

    ``model`` overrides the configured model for THIS call only. The Fixer
    supplies ``phase3.fixer.fixer_model()``, which shares the deployment's
    active model; ``None`` keeps that configured model.
    """
    from . import canonical_source_phase22 as phase22

    return phase22._openai_multimodal_json(
        system=system,
        prompt=prompt,
        pages=[],
        response_schema=schema,
        purpose=purpose,  # type: ignore[arg-type]
        max_tokens=max_tokens or _max_output_tokens(),
        model=model,
    )


# ---------------------------------------------------------------------------
# Evidence assembly (mechanics)
# ---------------------------------------------------------------------------

def _content_blocks(
    canonical: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """All judgeable blocks in reading order, plus the layout exclusions.

    ``layout`` blocks are whitespace-only runs: excluding them is parsing
    (there is no content to judge), and they are listed in the accounting
    so the closed world stays inspectable.
    """
    blocks: list[dict[str, Any]] = []
    layout_ids: list[str] = []
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("kind") or "") == "layout":
            layout_ids.append(str(block.get("block_id") or ""))
            continue
        blocks.append(block)
    blocks.sort(key=lambda block: int(block.get("source_start") or 0))
    return blocks, layout_ids


def _section_titles(canonical: Mapping[str, Any]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for section in canonical.get("sections") or []:
        if isinstance(section, dict):
            titles[str(section.get("section_id") or "")] = str(
                section.get("title") or ""
            )
    return titles


def _candidate_payloads(
    canonical: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The hardened parser/Phase-2.1 tasks as candidate evidence.

    Candidate ids are minted here in ledger order and are only adjudication
    handles; the surviving tasks are renumbered afterwards by the ordinary
    mechanics.
    """
    candidates: list[dict[str, Any]] = []
    for index, task in enumerate(canonical.get("tasks") or [], start=1):
        if not isinstance(task, dict):
            continue
        start = int(task.get("source_start") or 0)
        end = max(start, int(task.get("source_end") or start))
        overlapping = [
            str(block.get("block_id") or "")
            for block in blocks
            if start < int(block.get("source_end") or 0)
            and end > int(block.get("source_start") or 0)
        ]
        nearest = ""
        if not overlapping and blocks:
            nearest = str(min(
                blocks,
                key=lambda block: abs(
                    int(block.get("source_start") or 0) - start
                ),
            ).get("block_id") or "")
        candidates.append({
            "candidate_id": f"CAND-{index:04d}",
            "task_index": index - 1,
            "qid_before": str(task.get("qid") or ""),
            "raw_prompt": str(task.get("raw_prompt") or ""),
            "display_prompt": str(task.get("display_prompt") or ""),
            "source_start": start,
            "source_end": end,
            "source_kind": str(task.get("source_kind") or ""),
            "provenance": str(
                (task.get("phase21_recovery") or {}).get("reason")
                or task.get("source_location_confidence")
                or "parser_anchor"
            ),
            "block_ids": overlapping,
            "nearest_block_id": nearest,
        })
    return candidates


def _block_identity_sha(blocks: Sequence[Mapping[str, Any]]) -> str:
    material = "␟".join(
        f"{block.get('block_id')}:{block.get('raw_sha256')}"
        for block in blocks
    )
    return canonical_source._sha256_text(material)


def _candidate_identity_sha(candidates: Sequence[Mapping[str, Any]]) -> str:
    material = "␟".join(
        "␞".join([
            str(candidate.get("candidate_id") or ""),
            str(candidate.get("source_start") or 0),
            str(candidate.get("source_end") or 0),
            structure.normal_text(candidate.get("raw_prompt") or ""),
        ])
        for candidate in candidates
    )
    return canonical_source._sha256_text(material)


# ---------------------------------------------------------------------------
# Prompts and schemas
# ---------------------------------------------------------------------------

_AUTHOR_SYSTEM = """\
You are the question-inventory author for a school-textbook pipeline.

You receive the COMPLETE ordered content blocks of one chapter source and a
list of CANDIDATE tasks a deterministic parser nominated. The parser only
recognises a finite cue vocabulary; it misses asks in other languages, other
phrasings, and other layouts, and it sometimes nominates non-tasks. YOU are
the authority on membership: for every block you must decide whether the
book asks the learner to do something there.

Rules:
- Judge the printed content, in any language. A task needs no question mark,
  no English cue word, no recognised heading, and no particular length.
- A rhetorical device the book answers itself is not a learner task.
- Confirm a candidate when it is a genuine learner ask; reject it when it is
  not (heading banners, exposition fragments, mangled spans).
- Report asks the candidates missed under missed_asks, quoting the ask
  VERBATIM from the block text as evidence_text, and give each a stable
  task_ref (e.g. NEW-1). If one ask spans several blocks, declare it once
  and mark the later blocks task_continuation with that reference.
- A block that only sets up or supports a task (a data table, a scenario)
  is task_context for the tasks that need it.
- A block with no learner ask at all is not_task.
- Use uncertain only when you genuinely cannot decide from the evidence;
  it routes the block to a recorded review decision.
Every listed block must receive exactly one verdict."""

_CORRECTION_SYSTEM = """\
You are correcting your previous question-inventory verdicts. The listed
defects are MECHANICAL contract violations (missing or duplicate block
coverage, unknown ids, unlocatable evidence quotes, unruled candidates).
Return the corrected complete verdict set for the listed blocks. Do not
change decisions the defects do not touch."""

_FIXER_SYSTEM = """\
You are The Fixer for a school-textbook question inventory. One source
block could not be decided by the ordinary author pass. Read the block, its
neighbours, and the candidate tasks, and record your best-judgment final
verdict for this block. You MUST decide: "uncertain" is not available to
you. Your decision is recorded verbatim, flagged for human review, and the
run completes with it."""

_CRITIC_SYSTEM = """\
You are the independent critic of a question-inventory adjudication. You
receive every block verdict, the candidate rulings, and the resulting task
list. Look for asks ruled not_task that are really learner tasks, confirmed
candidates that are not asks, missed groupings, and accounting gaps.
Your dissent is an advisory review flag for a human reviewer — it blocks
nothing — so dissent freely and precisely."""


def _missed_ask_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["evidence_text", "task_ref"],
        "properties": {
            "evidence_text": {"type": "string"},
            "task_ref": {"type": "string"},
        },
    }


def _verdict_entry_schema(block_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "block_id",
            "verdict",
            "confirmed_candidate_ids",
            "rejected_candidate_ids",
            "missed_asks",
            "continues_task_ref",
            "context_for_task_refs",
        ],
        "properties": {
            "block_id": {"type": "string", "enum": list(block_ids)},
            "verdict": {"type": "string", "enum": list(_VERDICTS)},
            "confirmed_candidate_ids": {
                "type": "array", "items": {"type": "string"},
            },
            "rejected_candidate_ids": {
                "type": "array", "items": {"type": "string"},
            },
            "missed_asks": {"type": "array", "items": _missed_ask_schema()},
            "continues_task_ref": {"type": "string"},
            "context_for_task_refs": {
                "type": "array", "items": {"type": "string"},
            },
        },
    }


def author_schema(block_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "name": "qx_block_verdicts",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["blocks"],
            "properties": {
                "blocks": {
                    "type": "array",
                    "items": _verdict_entry_schema(block_ids),
                },
            },
        },
        "strict": True,
    }


def fixer_schema(block_id: str) -> dict[str, Any]:
    entry = _verdict_entry_schema([block_id])
    entry["properties"]["verdict"] = {
        "type": "string", "enum": list(_FINAL_VERDICTS),
    }
    entry["required"] = list(entry["required"]) + ["rationale"]
    entry["properties"]["rationale"] = {"type": "string"}
    return {"name": "qx_fixer_verdict", "schema": entry, "strict": True}


def critic_schema() -> dict[str, Any]:
    return {
        "name": "qx_critic_review",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "dissents"],
            "properties": {
                "verdict": {"type": "string", "enum": ["concur", "dissent"]},
                "dissents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["target", "reason"],
                        "properties": {
                            "target": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
            },
        },
        "strict": True,
    }


def _block_payload(
    block: Mapping[str, Any],
    titles: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "block_id": str(block.get("block_id") or ""),
        "kind": str(block.get("kind") or ""),
        "section_title": titles.get(str(block.get("section_id") or ""), ""),
        "text": str(block.get("raw_text") or ""),
    }


def _author_prompt(
    batch: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    titles: Mapping[str, str],
    *,
    context_before: str,
    context_after: str,
) -> str:
    batch_ids = {str(block.get("block_id") or "") for block in batch}
    payload = {
        "instruction": (
            "Return one verdict for every listed block. Candidates listed "
            "here overlap these blocks; rule on each of them."
        ),
        "context_before": context_before,
        "context_after": context_after,
        "blocks": [_block_payload(block, titles) for block in batch],
        "candidates": [
            {
                "candidate_id": candidate["candidate_id"],
                "text": candidate["raw_prompt"],
                "block_ids": candidate["block_ids"],
                "provenance": candidate["provenance"],
            }
            for candidate in candidates
            if (set(candidate["block_ids"]) & batch_ids)
            or (
                not candidate["block_ids"]
                and candidate.get("nearest_block_id") in batch_ids
            )
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _prompt_identity_sha() -> str:
    material = "␟".join([
        _AUTHOR_SYSTEM,
        _CORRECTION_SYSTEM,
        _FIXER_SYSTEM,
        _CRITIC_SYSTEM,
        json.dumps(author_schema(["BLK-00001"]), sort_keys=True),
        json.dumps(critic_schema(), sort_keys=True),
    ])
    return canonical_source._sha256_text(material)


# ---------------------------------------------------------------------------
# Deterministic validation (mechanics)
# ---------------------------------------------------------------------------

def _locate_evidence(block_text: str, evidence: str, start: int = 0) -> int:
    """Verbatim offset of the quoted evidence inside its block, or -1.

    ``start`` makes repeated identical evidence resolvable: the Nth declared
    ask with the same quote binds to the Nth printed occurrence (audit F16),
    so two genuinely repeated asks in one block receive distinct spans and
    therefore distinct identities.
    """
    if not evidence:
        return -1
    return block_text.find(evidence, start)


def verdict_defects(
    merged: Mapping[str, Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Mechanical contract violations in a merged verdict set.

    An ``uncertain`` verdict is NOT a defect — it is the author's recorded
    honesty and routes to the Fixer separately.
    """
    defects: list[dict[str, Any]] = []
    block_by_id = {str(b.get("block_id") or ""): b for b in blocks}
    candidate_ids = {c["candidate_id"] for c in candidates}
    candidate_first_block = {
        c["candidate_id"]: (c["block_ids"][0] if c["block_ids"] else "")
        for c in candidates
    }

    def defect(code: str, message: str, **extra: Any) -> None:
        defects.append({"code": code, "message": message, **extra})

    for block_id in block_by_id:
        if block_id not in merged:
            defect(
                "missing_block_verdict",
                f"block {block_id} received no verdict",
                block_id=block_id,
            )
    for block_id in merged:
        if block_id not in block_by_id:
            defect(
                "unknown_block_id",
                f"a verdict names unknown block {block_id}",
                block_id=block_id,
            )

    rulings: dict[str, dict[str, str]] = {}
    declared_refs: dict[str, str] = {}
    for block_id, entry in merged.items():
        if block_id not in block_by_id:
            continue
        verdict = str(entry.get("verdict") or "")
        if verdict not in _VERDICTS:
            defect(
                "invalid_verdict_value",
                f"block {block_id} verdict {verdict!r} is not in the contract",
                block_id=block_id,
            )
            continue
        confirmed = [str(v) for v in entry.get("confirmed_candidate_ids") or []]
        rejected = [str(v) for v in entry.get("rejected_candidate_ids") or []]
        missed = [
            item for item in entry.get("missed_asks") or []
            if isinstance(item, Mapping)
        ]
        for cid in confirmed + rejected:
            if cid not in candidate_ids:
                defect(
                    "unknown_candidate_ref",
                    f"block {block_id} rules unknown candidate {cid}",
                    block_id=block_id,
                    candidate_id=cid,
                )
        for cid in confirmed:
            if cid in rejected:
                defect(
                    "candidate_ruled_inconsistently",
                    f"block {block_id} both confirms and rejects {cid}",
                    block_id=block_id,
                    candidate_id=cid,
                )
        for cid in confirmed:
            previous = rulings.setdefault(cid, {})
            if previous.get("ruling") == "rejected":
                defect(
                    "candidate_ruled_inconsistently",
                    f"candidate {cid} is confirmed by {block_id} and rejected "
                    f"by {previous.get('block_id')}",
                    block_id=block_id,
                    candidate_id=cid,
                )
            else:
                previous.update({"ruling": "confirmed", "block_id": block_id})
        for cid in rejected:
            previous = rulings.setdefault(cid, {})
            if previous.get("ruling") == "confirmed":
                defect(
                    "candidate_ruled_inconsistently",
                    f"candidate {cid} is rejected by {block_id} and confirmed "
                    f"by {previous.get('block_id')}",
                    block_id=block_id,
                    candidate_id=cid,
                )
            else:
                previous.update({"ruling": "rejected", "block_id": block_id})

        if verdict == "contains_tasks" and not confirmed and not missed:
            defect(
                "contains_tasks_empty",
                f"block {block_id} says contains_tasks but confirms no "
                "candidate and reports no missed ask",
                block_id=block_id,
            )
        block_text = str(block_by_id[block_id].get("raw_text") or "")
        evidence_counts: dict[str, int] = {}
        for item in missed:
            ref = str(item.get("task_ref") or "").strip()
            evidence = str(item.get("evidence_text") or "")
            if not ref:
                defect(
                    "missed_ask_ref_missing",
                    f"a missed ask in block {block_id} has no task_ref",
                    block_id=block_id,
                )
            elif ref in declared_refs:
                defect(
                    "duplicate_missed_ask_ref",
                    f"task_ref {ref} is declared in both "
                    f"{declared_refs[ref]} and {block_id}",
                    block_id=block_id,
                )
            else:
                declared_refs[ref] = block_id
            # Occurrence-aware (audit F16): the Nth identical quote needs an
            # Nth printed occurrence, or two asks would share one span and
            # collide into one minted identity.
            seen_before = evidence_counts.get(evidence, 0)
            evidence_counts[evidence] = seen_before + 1
            if not evidence or block_text.count(evidence) <= seen_before:
                defect(
                    "evidence_not_locatable",
                    f"missed-ask evidence in block {block_id} is not a "
                    "verbatim quote of that block (occurrence "
                    f"{seen_before + 1} not found)",
                    block_id=block_id,
                )

    for candidate in candidates:
        cid = candidate["candidate_id"]
        if cid not in rulings:
            defect(
                "candidate_unruled",
                f"candidate {cid} was neither confirmed nor rejected",
                candidate_id=cid,
                block_id=candidate_first_block.get(cid, ""),
            )

    confirmed_ids = {
        cid for cid, row in rulings.items() if row.get("ruling") == "confirmed"
    }
    for block_id, entry in merged.items():
        if block_id not in block_by_id:
            continue
        verdict = str(entry.get("verdict") or "")
        refs: list[str] = []
        if verdict == "task_continuation":
            refs.append(str(entry.get("continues_task_ref") or ""))
        if verdict == "task_context":
            refs.extend(
                str(v) for v in entry.get("context_for_task_refs") or []
            )
            if not [r for r in refs if r]:
                # Audit F3: context that supports no resolvable task is a
                # dead accounting entry — the stimulus would never reach
                # the learner's question. Mechanical anchoring requirement.
                defect(
                    "task_context_unanchored",
                    f"block {block_id} is task_context but names no task "
                    "it supports",
                    block_id=block_id,
                )
        for ref in refs:
            if not ref:
                if verdict == "task_continuation":
                    defect(
                        "unresolvable_task_ref",
                        f"block {block_id} continues an unnamed task",
                        block_id=block_id,
                    )
                continue
            if ref in declared_refs:
                continue
            if ref in candidate_ids:
                if ref not in confirmed_ids:
                    defect(
                        "continuation_target_rejected",
                        f"block {block_id} references candidate {ref} which "
                        "no block confirmed",
                        block_id=block_id,
                        candidate_id=ref,
                    )
                continue
            defect(
                "unresolvable_task_ref",
                f"block {block_id} references unknown task {ref}",
                block_id=block_id,
            )
    return defects


# ---------------------------------------------------------------------------
# Reconciliation (mechanics)
# ---------------------------------------------------------------------------

def _section_by_id(canonical: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(section.get("section_id") or ""): section
        for section in canonical.get("sections") or []
        if isinstance(section, dict)
    }


def _created_task(
    canonical: Mapping[str, Any],
    block: Mapping[str, Any],
    evidence: str,
    *,
    task_ref: str,
    block_id: str,
    search_from: int = 0,
) -> dict[str, Any]:
    """A task built directly from quoted block evidence.

    Field template follows the Phase-2.1 recovery append so every later
    mechanic (renumbering, leaf materialization, inventory render) sees a
    complete task object. Membership is model-decided; the KIND is a
    neutral default awaiting a kind ruling, and says so. Explicit figure
    references in the evidence are resolved mechanically against the
    canonical Figure ledger (audit F3) — resolved figures ride the task;
    genuinely unresolved/ambiguous references stay recorded and follow the
    existing citation policy instead of being cleared.
    """
    sections = _section_by_id(canonical)
    section_id = str(block.get("section_id") or "")
    section = sections.get(section_id, {})
    offset = max(0, _locate_evidence(
        str(block.get("raw_text") or ""), evidence, search_from
    ))
    source_start = int(block.get("source_start") or 0) + offset
    figure_refs, image_urls, unresolved, ambiguous = (
        structure._resolved_leaf_figures(canonical, prompt=evidence)
    )
    return {
        "task_id": "",
        "qid": "",
        "order": 0,
        "order_index": 0,
        "source_kind": "checkpoint_question",
        "source_label": "",
        "parent_source_label": "",
        "topic_hint": structure.topic_for_position(canonical, source_start),
        "raw_prompt": evidence,
        "display_prompt": structure.canonical_task_display(evidence),
        "identity_key": "",
        "section_id": section_id,
        "source_section_index": max(0, int(section.get("order") or 1) - 1),
        "source_position": max(
            0, source_start - int(section.get("source_start") or 0)
        ),
        "source_start": source_start,
        "source_end": source_start + len(evidence),
        "source_location_confidence": "qx_model_missed_ask",
        "source_kind_ruling": "not_model_ruled_flagged",
        "review_flags": [
            "source kind defaulted pending a kind ruling; task membership "
            "was decided by the QX author verdict"
        ],
        "chapter_wide": False,
        "activity_origin": False,
        # requires_visual only when a canonical image actually resolved:
        # unresolved/ambiguous references keep their own recorded fields and
        # flow through the existing citation policy — marking them
        # required-with-no-image would trip the wrong gate.
        "requires_visual": bool(image_urls),
        "requires_context": False,
        "image_urls": list(image_urls),
        "figure_refs": list(figure_refs),
        "explicit_figure_reference_ids": structure._figure_reference_ids(
            evidence
        ),
        "unresolved_figure_reference_ids": list(unresolved),
        "ambiguous_figure_reference_ids": list(ambiguous),
        "display_overrides": [],
        "membership_authority": "model_verdict",
        "origin": "qx_model_missed_ask",
        "qx_ruling": {"task_ref": task_ref, "block_id": block_id},
    }


def _continuation_row(
    canonical: Mapping[str, Any],
    block: Mapping[str, Any],
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    prompt = str(block.get("raw_text") or "").strip()
    sections = _section_by_id(canonical)
    section_id = str(block.get("section_id") or owner.get("section_id") or "")
    section = sections.get(section_id, {})
    block_start = int(block.get("source_start") or 0)
    return {
        "raw_prompt": prompt,
        "display_prompt": structure.canonical_task_display(prompt),
        "source_start": block_start,
        "source_end": int(block.get("source_end") or block_start + len(prompt)),
        "source_section_index": int(owner.get("source_section_index") or 0),
        "source_position": max(
            0, block_start - int(section.get("source_start") or 0)
        ),
        "section_id": section_id,
        "block_id": str(block.get("block_id") or ""),
        "explicit_figure_reference_ids": structure._figure_reference_ids(
            prompt
        ),
        "recovery": "qx_task_continuation",
        "cue_ruling": "model_verdict",
    }


def reconcile(
    canonical: dict[str, Any],
    ledger_core: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Deterministically rebuild ``canonical['tasks']`` from the verdicts.

    ``ledger_core`` holds ``verdicts`` (block_id → final entry, Fixer
    replacements already applied), ``fixer_decisions`` and ``critic``.
    Replay calls this with the sealed core and must produce byte-identical
    tasks. Mutates ``canonical`` in place and returns the accounting.
    """
    verdicts: Mapping[str, Mapping[str, Any]] = ledger_core["verdicts"]
    blocks, layout_ids = _content_blocks(canonical)
    block_by_id = {str(b.get("block_id") or ""): b for b in blocks}
    tasks_before = [
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ]
    task_by_candidate = {
        candidate["candidate_id"]: tasks_before[candidate["task_index"]]
        for candidate in candidates
        if candidate["task_index"] < len(tasks_before)
    }

    confirmed: dict[str, list[str]] = {}
    rejected: dict[str, str] = {}
    for block_id in sorted(
        verdicts,
        key=lambda bid: int(block_by_id.get(bid, {}).get("source_start") or 0),
    ):
        entry = verdicts[block_id]
        for cid in entry.get("confirmed_candidate_ids") or []:
            confirmed.setdefault(str(cid), []).append(block_id)
        for cid in entry.get("rejected_candidate_ids") or []:
            rejected.setdefault(str(cid), block_id)

    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        cid = candidate["candidate_id"]
        task = task_by_candidate.get(cid)
        if task is None or cid not in confirmed:
            continue
        task = copy.deepcopy(task)
        task["membership_authority"] = "model_verdict"
        task["qx_ruling"] = {
            "candidate_id": cid,
            "confirmed_by_blocks": confirmed[cid],
        }
        kept.append(task)

    rejected_records: list[dict[str, Any]] = []
    for candidate in candidates:
        cid = candidate["candidate_id"]
        if cid in confirmed or cid not in rejected:
            continue
        # R4: dropped and listed as dropped, with what it said.
        rejected_records.append({
            "candidate_id": cid,
            "qid_before": candidate["qid_before"],
            "raw_prompt": candidate["raw_prompt"],
            "provenance": candidate["provenance"],
            "ruled_by_block": rejected[cid],
            "ruling": "candidate_ruled_not_task",
        })

    created_by_ref: dict[str, dict[str, Any]] = {}
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        entry = verdicts.get(block_id) or {}
        block_text = str(block.get("raw_text") or "")
        cursors: dict[str, int] = {}
        for item in entry.get("missed_asks") or []:
            ref = str(item.get("task_ref") or "")
            evidence = str(item.get("evidence_text") or "")
            # Audit F16: the Nth identical quote binds to the Nth printed
            # occurrence so repeated asks mint distinct spans/identities.
            search_from = cursors.get(evidence, 0)
            created = _created_task(
                canonical, block, evidence, task_ref=ref,
                block_id=block_id, search_from=search_from,
            )
            found_at = _locate_evidence(block_text, evidence, search_from)
            cursors[evidence] = (
                found_at + 1 if found_at >= 0 else search_from + 1
            )
            created_by_ref[ref] = created

    fixer_flagged: set[str] = set()
    for decision in ledger_core.get("fixer_decisions") or []:
        fixer_flagged.add(str(decision.get("block_id") or ""))

    for block in blocks:
        block_id = str(block.get("block_id") or "")
        entry = verdicts.get(block_id) or {}
        if str(entry.get("verdict") or "") != "task_continuation":
            continue
        ref = str(entry.get("continues_task_ref") or "")
        owner: dict[str, Any] | None = None
        if ref in created_by_ref:
            owner = created_by_ref[ref]
        else:
            for task in kept:
                ruling = task.get("qx_ruling") or {}
                if ruling.get("candidate_id") == ref:
                    owner = task
                    break
        if owner is None:
            continue  # validation guarantees resolvability; defensive only
        owner.setdefault("source_followup_prompts", []).append(
            _continuation_row(canonical, block, owner)
        )

    # Audit F3: task_context is ATTACHED, not merely recorded — the ruled
    # stimulus (a data table, a scenario) travels on the task the learner
    # receives, via the same shared_context field the leaf machinery and
    # inventory render already carry.
    def _owner_for_ref(ref: str) -> dict[str, Any] | None:
        if ref in created_by_ref:
            return created_by_ref[ref]
        for task in kept:
            if (task.get("qx_ruling") or {}).get("candidate_id") == ref:
                return task
        return None

    context_links: list[dict[str, Any]] = []
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        entry = verdicts.get(block_id) or {}
        if str(entry.get("verdict") or "") != "task_context":
            continue
        refs = [
            str(v) for v in entry.get("context_for_task_refs") or [] if v
        ]
        attached: list[str] = []
        context_text = str(block.get("raw_text") or "").strip()
        for ref in refs:
            owner = _owner_for_ref(ref)
            if owner is None:
                continue  # validation guarantees resolvability; defensive
            existing = str(owner.get("shared_context") or "").strip()
            if context_text and context_text not in existing:
                owner["shared_context"] = "\n\n".join(
                    part for part in (existing, context_text) if part
                )
            owner["requires_context"] = True
            owner.setdefault("qx_context_block_ids", [])
            if block_id not in owner["qx_context_block_ids"]:
                owner["qx_context_block_ids"].append(block_id)
            attached.append(ref)
        context_links.append({
            "block_id": block_id,
            "task_refs": refs,
            "attached_task_refs": attached,
        })

    new_tasks = kept + [
        created_by_ref[ref] for ref in sorted(
            created_by_ref,
            key=lambda r: (
                int(created_by_ref[r].get("source_start") or 0), r
            ),
        )
    ]
    fixer_blocks_with_occurrence: set[str] = set()
    for task in new_tasks:
        ruling = task.get("qx_ruling") or {}
        touched_blocks = set(ruling.get("confirmed_by_blocks") or [])
        if ruling.get("block_id"):
            touched_blocks.add(str(ruling.get("block_id")))
        hit = touched_blocks & fixer_flagged
        if hit:
            fixer_blocks_with_occurrence.update(hit)
            flags = task.setdefault("review_flags", [])
            note = (
                "qx_fixer_decided: membership for this occurrence was "
                "recorded by The Fixer's best-judgment verdict"
            )
            if note not in flags:
                flags.append(note)
    # Audit F6: a Fixer decision that left NO surviving occurrence (e.g. it
    # rejected the candidate) still gets a durable, truthful record — on the
    # ledger, since there is no row to flag.
    orphan_fixer_flags = [
        f"qx_fixer_decided: block {block_id} was decided by The Fixer and "
        "no task occurrence survived to carry the flag"
        for block_id in sorted(fixer_flagged - fixer_blocks_with_occurrence)
        if block_id
    ]

    canonical["tasks"] = new_tasks
    structure.renumber_tasks(canonical)
    structure.materialize_task_leaf_cases(canonical)

    critic = ledger_core.get("critic") or {}
    critic_flags: list[str] = []
    if str(critic.get("verdict") or "") == "dissent":
        by_handle: dict[str, dict[str, Any]] = {}
        for task in canonical.get("tasks") or []:
            ruling = task.get("qx_ruling") or {}
            for handle in (
                str(task.get("qid") or ""),
                str(ruling.get("candidate_id") or ""),
                str(ruling.get("task_ref") or ""),
            ):
                if handle:
                    by_handle[handle] = task
        for dissent in critic.get("dissents") or []:
            target = str(dissent.get("target") or "")
            reason = str(dissent.get("reason") or "")
            note = f"qx critic dissent: {reason}" if reason else (
                "qx critic dissent"
            )
            task = by_handle.get(target)
            if task is not None:
                flags = task.setdefault("review_flags", [])
                if note not in flags:
                    flags.append(note)
            else:
                critic_flags.append(
                    f"{note} (target {target or 'unspecified'})"
                )
    if critic.get("unavailable"):
        critic_flags.append(
            "qx critic unavailable: "
            + str(critic.get("unavailable"))
        )
    critic_flags.extend(orphan_fixer_flags)

    verdict_counts: dict[str, int] = {}
    for entry in verdicts.values():
        verdict = str(entry.get("verdict") or "")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    unaccounted = [
        str(block.get("block_id") or "")
        for block in blocks
        if str(block.get("block_id") or "") not in verdicts
    ]
    accounting = {
        "blocks_total": len(blocks) + len(layout_ids),
        "ruled_blocks": len(verdicts),
        "layout_no_content": layout_ids,
        "verdict_counts": verdict_counts,
        "candidates_total": len(candidates),
        "candidates_confirmed": len(
            [c for c in candidates if c["candidate_id"] in confirmed]
        ),
        "candidates_rejected": len(rejected_records),
        "rejected_candidates": rejected_records,
        "created_from_missed_asks": len(created_by_ref),
        "task_context_links": context_links,
        "tasks_after": len(canonical.get("tasks") or []),
        # The closed-world proof: nothing may appear here.
        "unaccounted_block_ids": unaccounted,
        "ledger_review_flags": critic_flags,
    }
    return accounting


# ---------------------------------------------------------------------------
# Cache (decide once)
# ---------------------------------------------------------------------------

def _source_sha(canonical: Mapping[str, Any]) -> str:
    contract = canonical.get("source_contract")
    if isinstance(contract, Mapping):
        return str(contract.get("source_sha256") or "")
    return ""


def _author_context_sha(
    canonical: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    """Identity of everything ELSE the author actually sees (audit F15).

    Block kind and section title, candidate provenance and block mapping,
    the batching policy that assigns neighbour context, and the Phase-2
    compiler/schema identity all shape the provider's evidence; a sealed
    verdict must not replay when any of them changed.
    """
    titles = _section_titles(canonical)
    contract = canonical.get("source_contract") or {}
    material = "␟".join([
        str(contract.get("schema_version") or ""),
        str(contract.get("compiler_version") or ""),
        str(contract.get("hardening_version") or ""),
        str(_batch_size()),
        json.dumps(fixer_schema("BLK-00001"), sort_keys=True),
        "␞".join(
            "␝".join([
                str(b.get("block_id") or ""),
                str(b.get("kind") or ""),
                titles.get(str(b.get("section_id") or ""), ""),
            ])
            for b in blocks
        ),
        "␞".join(
            "␝".join([
                str(c.get("candidate_id") or ""),
                str(c.get("provenance") or ""),
                ",".join(c.get("block_ids") or []),
                str(c.get("nearest_block_id") or ""),
            ])
            for c in candidates
        ),
    ])
    return canonical_source._sha256_text(material)


def ledger_cache_key(
    canonical: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    material = "␟".join([
        QX_VERSION,
        QX_COMPILER,
        config.OPENAI_MODEL,
        _source_sha(canonical),
        _block_identity_sha(blocks),
        _candidate_identity_sha(candidates),
        _prompt_identity_sha(),
        _author_context_sha(canonical, blocks, candidates),
    ])
    return canonical_source._sha256_text(material)


def _cache_path(key: str):
    return _CACHE_DIR / f"{key}.json"


def _read_sealed_ledger(key: str) -> dict[str, Any] | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("status") != "sealed":
        return None
    return value


def _write_sealed_ledger(key: str, value: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    canonical_source._atomic_write(
        _cache_path(key), canonical_source._json_text(value)
    )


# ---------------------------------------------------------------------------
# The adjudication pipeline
# ---------------------------------------------------------------------------

def _merge_batch_payloads(
    payloads: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    merged: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for payload in payloads:
        for entry in payload.get("blocks") or []:
            if not isinstance(entry, Mapping):
                continue
            block_id = str(entry.get("block_id") or "")
            if block_id in merged:
                duplicates.append({
                    "code": "duplicate_block_verdict",
                    "message": f"block {block_id} received two verdicts",
                    "block_id": block_id,
                })
                continue
            merged[block_id] = dict(entry)
    return merged, duplicates


def _author_batches(
    blocks: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    titles: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Live author calls over block batches; returns raw payloads."""
    size = _batch_size()
    starts = list(range(0, len(blocks), size))

    def _author_one(start: int) -> dict[str, Any]:
        batch = list(blocks[start:start + size])
        before = blocks[start - 1] if start else None
        after_index = start + size
        after = blocks[after_index] if after_index < len(blocks) else None
        prompt = _author_prompt(
            batch,
            candidates,
            titles,
            context_before=str((before or {}).get("raw_text") or ""),
            context_after=str((after or {}).get("raw_text") or ""),
        )
        return _call_provider(
            system=_AUTHOR_SYSTEM,
            prompt=prompt,
            schema=author_schema(
                [str(b.get("block_id") or "") for b in batch]
            ),
        )

    # Batches read only their own static slice plus fixed neighbours, and
    # the payload list merges in start order — byte-identical to the
    # sequential path. The shared OpenAI gate bounds real concurrency.
    from .phase3 import kernel as _kernel
    from .. import config as _config

    return _kernel.parallel_map_in_order(
        starts,
        _author_one,
        max_workers=_config.phase3_decision_workers(),
        labels=[
            f"Questions · blocks {start + 1}-{min(start + size, len(blocks))}"
            f"/{len(blocks)}"
            for start in starts
        ],
        announce="Question adjudication",
    )


def _fixer_decide(
    canonical: Mapping[str, Any],
    block: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    titles: Mapping[str, str],
    *,
    reason: str,
    blocks: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    block_id = str(block.get("block_id") or "")
    # Audit F6: a candidate attributed here through nearest_block_id (no
    # overlap anywhere) must still reach the Fixer's evidence, and the
    # neighbouring blocks travel too — a bare "Why?" is undecidable
    # without its stem.
    overlapping = [
        {
            "candidate_id": c["candidate_id"],
            "text": c["raw_prompt"],
            "provenance": c["provenance"],
        }
        for c in candidates
        if block_id in c["block_ids"]
        or (not c["block_ids"] and c.get("nearest_block_id") == block_id)
    ]
    ordered = list(blocks)
    index = next(
        (i for i, b in enumerate(ordered)
         if str(b.get("block_id") or "") == block_id),
        -1,
    )
    context_before = (
        str(ordered[index - 1].get("raw_text") or "") if index > 0 else ""
    )
    context_after = (
        str(ordered[index + 1].get("raw_text") or "")
        if 0 <= index < len(ordered) - 1 else ""
    )
    prompt = json.dumps({
        "reason_this_reached_you": reason,
        "context_before": context_before,
        "block": _block_payload(block, titles),
        "context_after": context_after,
        "candidates_overlapping_this_block": overlapping,
    }, ensure_ascii=False)
    from .phase3 import fixer as fixer_mod

    response = _call_provider(
        system=_FIXER_SYSTEM,
        prompt=prompt,
        schema=fixer_schema(block_id),
        # The Fixer follows the same active model as the rest of this run.
        model=fixer_mod.fixer_model(),
    )
    decision_sha = canonical_source._sha256_text(
        "␟".join([
            QX_VERSION,
            block_id,
            str(block.get("raw_sha256") or ""),
            reason,
            json.dumps(response, ensure_ascii=False, sort_keys=True),
        ])
    )
    return {
        "block_id": block_id,
        "reason": reason,
        "decision_sha256": decision_sha,
        "rationale": str(response.get("rationale") or ""),
        "entry": {
            key: value for key, value in response.items() if key != "rationale"
        },
    }


def _critic_review(
    verdicts: Mapping[str, Mapping[str, Any]],
    accounting_preview: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    *,
    blocks: Sequence[Mapping[str, Any]] = (),
    candidates: Sequence[Mapping[str, Any]] = (),
    titles: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    # Audit F6: the critic is asked to catch asks wrongly ruled not_task —
    # it cannot do that without the words, so it receives every source
    # block and candidate alongside the verdicts.
    prompt = json.dumps({
        "blocks": [
            _block_payload(block, titles or {}) for block in blocks
        ],
        "candidates": [
            {
                "candidate_id": c["candidate_id"],
                "text": c["raw_prompt"],
                "block_ids": c["block_ids"],
            }
            for c in candidates
        ],
        "verdicts": {
            block_id: dict(entry) for block_id, entry in verdicts.items()
        },
        "accounting": dict(accounting_preview),
        "resulting_tasks": [
            {
                "qid": str(task.get("qid") or ""),
                "prompt": str(task.get("display_prompt") or ""),
                "membership_authority": str(
                    task.get("membership_authority") or ""
                ),
            }
            for task in tasks
        ],
    }, ensure_ascii=False)
    try:
        return _call_provider(
            system=_CRITIC_SYSTEM,
            prompt=prompt,
            schema=critic_schema(),
            purpose="advisory_critic",
        )
    except Exception as exc:  # advisory pass: never a gate, never a halt (Q10)
        return {"verdict": "concur", "dissents": [], "unavailable": str(exc)}


def _unadjudicated(reason_code: str, reason: str) -> dict[str, Any]:
    return {
        "status": "unadjudicated",
        "reason_code": reason_code,
        "reason": reason,
    }


def adjudicate_task_membership(canonical: dict[str, Any]) -> dict[str, Any]:
    """Run the closed-world membership adjudication over a hardened bundle.

    On success, rewrites ``canonical['tasks']`` and stamps
    ``canonical['task_verdict_ledger']``; returns ``{"status":
    "adjudicated", "replayed": bool, "ledger": …}``. On the named
    impossibilities returns ``{"status": "unadjudicated", …}`` and leaves
    the candidate ledger untouched — the compile seam turns that into a
    pre-spend block, never a silent parser fallback.
    """
    blocks, layout_ids = _content_blocks(canonical)
    titles = _section_titles(canonical)
    candidates = _candidate_payloads(canonical, blocks)
    if not blocks:
        if candidates:
            return _unadjudicated(
                "no_block_evidence",
                "the bundle carries tasks but no source block ledger; a "
                "closed-world verdict is impossible without the blocks",
            )
        # An empty source adjudicates trivially: there is nothing to judge
        # and nothing to lose; the accounting records exactly that.
        ledger_core = {"verdicts": {}, "fixer_decisions": [], "critic": {}}
        accounting = reconcile(canonical, ledger_core, candidates)
        ledger = _build_ledger(
            canonical, blocks, candidates, ledger_core, accounting,
            correction_history=[], empty_source=True,
        )
        canonical[LEDGER_KEY] = ledger
        return {"status": "adjudicated", "replayed": False, "ledger": ledger}

    key = ledger_cache_key(canonical, blocks, candidates)
    sealed = _read_sealed_ledger(key)
    if sealed is not None:
        ledger_core = {
            "verdicts": dict(sealed.get("verdicts") or {}),
            "fixer_decisions": list(sealed.get("fixer_decisions") or []),
            "critic": dict(sealed.get("critic") or {}),
        }
        defects = verdict_defects(ledger_core["verdicts"], blocks, candidates)
        if defects:
            # A sealed ledger that no longer satisfies the contract is
            # stale evidence, not authority; fall through to live.
            progress.log(
                "QX sealed verdict ledger no longer matches the bundle; "
                "re-adjudicating.",
                level="warning",
            )
        else:
            accounting = reconcile(canonical, ledger_core, candidates)
            ledger = _build_ledger(
                canonical, blocks, candidates, ledger_core, accounting,
                correction_history=list(sealed.get("correction_history") or []),
            )
            canonical[LEDGER_KEY] = ledger
            return {
                "status": "adjudicated", "replayed": True, "ledger": ledger,
            }

    if not _provider_ready():
        return _unadjudicated(
            "provider_unavailable",
            "no live provider is configured; task membership cannot be "
            "adjudicated and the parser candidates must not stand in",
        )

    correction_history: list[dict[str, Any]] = []
    try:
        payloads = _author_batches(blocks, candidates, titles)
    except Exception as exc:
        return _unadjudicated(
            "provider_error", f"author pass failed: {exc}"
        )

    merged, duplicates = _merge_batch_payloads(payloads)
    defects = duplicates + verdict_defects(merged, blocks, candidates)
    if defects:
        correction_history.append({
            "stage": "deterministic_validation",
            "defects": copy.deepcopy(defects),
        })
        # Audit F7: the correction is BOUNDED to the defective blocks (plus
        # the candidates that overlap them) — never the whole chapter — so
        # one omitted verdict cannot construct a request larger than any
        # author batch and discard every already-paid batch on transport
        # failure. Successful verdicts stay in `merged`; only the patched
        # blocks are replaced.
        block_index = {
            str(b.get("block_id") or ""): b for b in blocks
        }
        defective_ids: list[str] = []
        for item in defects:
            block_id = str(item.get("block_id") or "")
            if not block_id or block_id not in block_index:
                candidate_id = str(item.get("candidate_id") or "")
                match = next(
                    (c for c in candidates
                     if c["candidate_id"] == candidate_id), None,
                )
                if match:
                    block_id = (
                        (match["block_ids"] or [""])[0]
                        or match.get("nearest_block_id") or ""
                    )
            if block_id in block_index and block_id not in defective_ids:
                defective_ids.append(block_id)
        window = [block_index[bid] for bid in defective_ids]
        window_ids = set(defective_ids)
        window_candidates = [
            c for c in candidates
            if (set(c["block_ids"]) & window_ids)
            or c.get("nearest_block_id") in window_ids
        ]
        prompt = json.dumps({
            "defects": defects,
            "previous_verdicts": {
                block_id: merged[block_id]
                for block_id in defective_ids if block_id in merged
            },
            "blocks": [_block_payload(b, titles) for b in window],
            "candidates": [
                {
                    "candidate_id": c["candidate_id"],
                    "text": c["raw_prompt"],
                    "block_ids": c["block_ids"],
                }
                for c in window_candidates
            ],
        }, ensure_ascii=False)
        try:
            corrected = _call_provider(
                system=_CORRECTION_SYSTEM,
                prompt=prompt,
                schema=author_schema(defective_ids or ["BLK-00000"]),
            )
        except Exception as exc:
            return _unadjudicated(
                "provider_error", f"bounded correction failed: {exc}"
            )
        corrected_merged, corrected_duplicates = _merge_batch_payloads(
            [corrected]
        )
        # Patched blocks replace their originals; everything the defects
        # did not touch is carried verbatim.
        for block_id, entry in corrected_merged.items():
            if block_id in window_ids or block_id not in merged:
                merged[block_id] = entry
        defects = corrected_duplicates + verdict_defects(
            merged, blocks, candidates
        )

    block_by_id = {str(b.get("block_id") or ""): b for b in blocks}
    fixer_decisions: list[dict[str, Any]] = []
    unresolved: dict[str, str] = {}
    for defect in defects:
        block_id = str(defect.get("block_id") or "")
        if not block_id or block_id not in block_by_id:
            candidate_id = str(defect.get("candidate_id") or "")
            candidate = next(
                (c for c in candidates if c["candidate_id"] == candidate_id),
                None,
            )
            if candidate and candidate["block_ids"]:
                block_id = candidate["block_ids"][0]
            elif candidate and candidate.get("nearest_block_id"):
                block_id = str(candidate["nearest_block_id"])
            else:
                return _unadjudicated(
                    "adjudication_incomplete",
                    "a verdict defect could not be attributed to any source "
                    f"block: {defect.get('message')}",
                )
        unresolved.setdefault(block_id, str(defect.get("message") or ""))
    for block_id, entry in merged.items():
        if str(entry.get("verdict") or "") == "uncertain":
            unresolved.setdefault(
                block_id, "the author recorded an uncertain verdict"
            )

    fixer_order = sorted(
        unresolved,
        key=lambda bid: int(block_by_id[bid].get("source_start") or 0),
    )
    # Each unresolved block is an independent Fixer decision reading only
    # static evidence; decisions fan out, and the ledger/merge below applies
    # them in source order — byte-identical to the sequential path.
    from .phase3 import kernel as _kernel
    from .. import config as _config

    try:
        fixer_results = _kernel.parallel_map_in_order(
            fixer_order,
            lambda bid: _fixer_decide(
                canonical,
                block_by_id[bid],
                candidates,
                titles,
                reason=unresolved[bid],
                blocks=blocks,
            ),
            max_workers=_config.phase3_decision_workers(),
            labels=[f"Fixer · {bid}" for bid in fixer_order],
            announce="Fixer decisions" if fixer_order else "",
        )
    except Exception as exc:
        return _unadjudicated(
            "provider_error", f"the Fixer call failed: {exc}"
        )
    for block_id, decision in zip(fixer_order, fixer_results):
        fixer_decisions.append(decision)
        merged[block_id] = dict(decision["entry"])

    final_defects = verdict_defects(merged, blocks, candidates)
    final_defects += [
        {
            "code": "uncertain_after_fixer",
            "message": f"block {block_id} is still uncertain",
            "block_id": block_id,
        }
        for block_id, entry in merged.items()
        if str(entry.get("verdict") or "") == "uncertain"
    ]
    if final_defects:
        return _unadjudicated(
            "fixer_verdict_invalid",
            "the verdict contract could not be made mechanically applicable "
            "after the bounded correction and Fixer round: "
            + "; ".join(
                str(d.get("message") or d.get("code")) for d in final_defects
            ),
        )

    ordered_verdicts = {
        block_id: merged[block_id]
        for block_id in sorted(
            merged,
            key=lambda bid: int(
                block_by_id.get(bid, {}).get("source_start") or 0
            ),
        )
        if block_id in block_by_id
    }
    ledger_core: dict[str, Any] = {
        "verdicts": ordered_verdicts,
        "fixer_decisions": fixer_decisions,
        "critic": {},
    }

    # Reconcile on a working copy first so the critic reviews the real
    # resulting inventory, then commit the reviewed reconciliation.
    preview = copy.deepcopy(canonical)
    preview_accounting = reconcile(preview, ledger_core, candidates)
    ledger_core["critic"] = _critic_review(
        ordered_verdicts, preview_accounting, preview.get("tasks") or [],
        blocks=blocks, candidates=candidates, titles=titles,
    )

    accounting = reconcile(canonical, ledger_core, candidates)
    ledger = _build_ledger(
        canonical, blocks, candidates, ledger_core, accounting,
        correction_history=correction_history,
    )
    canonical[LEDGER_KEY] = ledger
    _write_sealed_ledger(key, {
        "status": "sealed",
        "qx_version": QX_VERSION,
        "model": config.OPENAI_MODEL,
        "verdicts": ordered_verdicts,
        "fixer_decisions": fixer_decisions,
        "critic": ledger_core["critic"],
        "correction_history": correction_history,
    })
    return {"status": "adjudicated", "replayed": False, "ledger": ledger}


def _build_ledger(
    canonical: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    ledger_core: Mapping[str, Any],
    accounting: Mapping[str, Any],
    *,
    correction_history: list[dict[str, Any]],
    empty_source: bool = False,
) -> dict[str, Any]:
    return {
        "version": QX_VERSION,
        "compiler": QX_COMPILER,
        "model": config.OPENAI_MODEL,
        "prompt_sha256": _prompt_identity_sha(),
        "source_sha256": _source_sha(canonical),
        "block_identity_sha256": _block_identity_sha(blocks),
        "candidate_identity_sha256": _candidate_identity_sha(candidates),
        "membership_authority": "model_verdict",
        "empty_source": empty_source,
        "verdicts": copy.deepcopy(dict(ledger_core.get("verdicts") or {})),
        "candidates": [
            {
                "candidate_id": c["candidate_id"],
                "qid_before": c["qid_before"],
                "raw_prompt": c["raw_prompt"],
                "source_start": c["source_start"],
                "source_end": c["source_end"],
                "provenance": c["provenance"],
                "block_ids": c["block_ids"],
            }
            for c in candidates
        ],
        "correction_history": copy.deepcopy(correction_history),
        "fixer_decisions": copy.deepcopy(
            list(ledger_core.get("fixer_decisions") or [])
        ),
        "critic": copy.deepcopy(dict(ledger_core.get("critic") or {})),
        "accounting": copy.deepcopy(dict(accounting)),
        # Recorded limit: visual-ownership normalization ran before this
        # adjudication, so a task created from missed-ask evidence has no
        # visual ownership pass of its own.
        "recorded_limits": [
            "qx_created_tasks_skip_visual_ownership_pass",
        ],
    }
