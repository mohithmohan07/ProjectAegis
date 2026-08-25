"""API-authoritative Pre-Learning formation and row-identity recovery.

Job 79 exposed two semantic seams behind the rewritten Phase 3 boundary:

* the running prerequisite capture is intentionally broad, but its merge
  contract forced every capture into the Pre map even when the critic had
  correctly identified chapter-taught or instruction-supplied material; and
* Settle could turn activity-only skeleton rows into duplicate concept rows
  because its written rule said "not a concept" while its mechanical response
  contract still required a concept segment. Assemble then failed on duplicate
  ``(topic, title)`` identity before Premap could run.

This contract adds two content-addressed model decisions. Deterministic code
only transports evidence, mints ids, checks exact-once accounting, and applies
recorded verdicts. The model decides prerequisite membership and pedagogical
row identity. No regex, subject switch, grade threshold, count quota, or title
suffix authors a semantic result.
"""
from __future__ import annotations

import copy
import importlib
from functools import wraps
from typing import Any, Mapping


CONTRACT_VERSION = 1
PREREQUISITE_POLICY_VERSION = "prelearn-adjudication-1"
ROW_IDENTITY_POLICY_VERSION = "settle-row-identity-reconciliation-1"
CULMINATION_POLICY_VERSION = "settle-row-identity-culmination-1"

PREREQUISITE_DISPOSITIONS = (
    "chapter_taught",
    "instruction_supplied",
    "general_procedure_not_preconcept",
    "not_required_for_this_chapter",
    "insufficient_evidence",
)
ROW_DISPOSITIONS = (
    "activity_support",
    "instruction_or_container",
    "enrichment_support",
    "duplicate_without_independent_teaching",
)

PREREQUISITE_SYSTEM = """\
You are the final prerequisite adjudicator for a school content pipeline.

Earlier stages kept a deliberately broad running capture of things the chapter
might assume. That capture is evidence, not authority. Decide the final
Pre-Learning set now from the chapter, grade/board instructions, and the exact
capture evidence.

A kept prerequisite is knowledge or a skill the learner must ALREADY hold before
this chapter can be understood: prior-year learning, vocabulary used without
being taught, or a narrow underlying fundamental on which the chapter's own
teaching depends. Do not keep the chapter's own definition, explanation,
practice target, response direction, activity, or facilitator instruction as a
prerequisite. A printed instruction such as "tick the correct answer" supplies
the procedure; it does not prove a Pre concept. A chapter may practise a broad
skill while still assuming a narrower underlying fundamental—keep only the
narrow fundamental when the evidence supports it.

You may split an over-broad capture, combine captures that mean one teachable
fundamental, and rewrite the final prerequisite precisely. Account for EVERY
capture_ref exactly once: either under one final prerequisite or under one
explicit disposition. Dispositions record why a capture is not Pre-Learning;
they never disappear silently. Use only the disposition classes supplied in the
request. There is no target number of prerequisites, topics, or concepts.

Return JSON with:
- prerequisites: [{prerequisite_id, text, captures, rationale}]
- dispositions: [{disposition_id, classification, captures, rationale}]
Mint PR-0001... and PD-0001... positionally. Do not copy a current-chapter
question into prerequisite text and do not answer any source question."""

PREREQUISITE_CRITIC_SYSTEM = """\
Audit a final prerequisite adjudication against the supplied chapter evidence.
Dissent when chapter-taught content or a printed procedure is retained as prior
knowledge, when a necessary underlying fundamental is discarded, when captures
that mean different things are merged, when one prerequisite is too broad for a
single teachable concept, or when wording/level is inappropriate for the named
grade. Every capture must remain explicitly accounted for. Return the standard
critic object {verdict, confidence, issues}; dissent is advisory."""

ROW_IDENTITY_SYSTEM = """\
You are resolving duplicate concept identities before Type/Case hosting.
Several model-authored rows now share the same topic and title. Decide their
pedagogical identity from their complete descriptions, mastery statements,
source grounding, chapter instructions, and topic evidence.

For each collision group, produce one or more genuinely distinct substantial
concepts. Merge rows that teach the same concept. Differentiate rows only when a
teacher would lesson-plan them separately. An activity, warm-up, recitation
instruction, discussion cue, heading, or enrichment container that merely
supports a substantive concept is not a separate concept: dispose it explicitly
and attach its evidence to one output concept. Never solve a collision by adding
numbers, punctuation, grade labels, or cosmetic synonyms.

Account for EVERY source_row_id exactly once, either inside one output concept's
source_row_ids or inside one disposition's source_row_ids. Every disposition
must name attach_to_concept_title, which must be one of that group's output
concept titles. Return JSON with groups, each containing group_id, concepts,
dispositions, and rationale. Each concept contains source_row_ids,
concept_title, parent_concept, description, achieving_mastery, keywords, and
rationale. Use only the disposition classes supplied in the request. There is
no target concept count."""

ROW_IDENTITY_CRITIC_SYSTEM = """\
Audit duplicate-concept reconciliation. Dissent when two outputs still teach
the same thing, when genuinely separate teaching is collapsed, when an activity
or instruction survives as a concept, when chapter evidence is lost, when a
disposition attaches to the wrong concept, or when the resolution is calibrated
poorly for the named grade and sourcebook. Return the standard critic object
{verdict, confidence, issues}; dissent is advisory."""

CULMINATION_SYSTEM = """\
You are refreshing topic culminations after duplicate concept identities were
resolved by a recorded model decision. For each supplied topic, author one short
learner-facing consolidation paragraph that ties the FINAL member concepts
together—what a learner can now understand or do with them combined. Do not list
concept names, repeat one member's description, introduce a new concept, or
recreate a disposed activity as teaching content. Return
{topics: [{topic_id, consolidation, rationale}]} and decide every supplied topic
exactly once."""

CULMINATION_CRITIC_SYSTEM = """\
Audit refreshed culmination paragraphs against the final topic members. Dissent
when a paragraph is a title list, repeats one concept, omits a member, revives a
disposed activity as a concept, or adds unsupported teaching. Return the
standard critic object {verdict, confidence, issues}; dissent is advisory."""


def _normal(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _chapter(env: Mapping[str, Any]) -> dict[str, Any]:
    meta = env.get("metadata") or {}
    return {
        "subject": str(meta.get("subject") or ""),
        "board": str(meta.get("board") or ""),
        "grade": str(meta.get("grade") or ""),
        "unit": str(meta.get("unit") or ""),
        "chapter_title": str(
            meta.get("chapter_title")
            or meta.get("chapter_display_name")
            or ""
        ),
        "instruction_slots": copy.deepcopy(
            meta.get("instruction_slots")
            or (meta.get("instruction_set") or {}).get("slots")
            or {}
        ),
    }


def _live_json(system: str, payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        system,
        prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_prerequisite(payload: dict[str, Any]) -> dict[str, Any]:
    return _live_json(PREREQUISITE_SYSTEM, payload)


def _live_prerequisite_critic(payload: dict[str, Any]) -> dict[str, Any]:
    return _live_json(PREREQUISITE_CRITIC_SYSTEM, payload)


def _live_row_identity(payload: dict[str, Any]) -> dict[str, Any]:
    return _live_json(ROW_IDENTITY_SYSTEM, payload)


def _live_row_identity_critic(payload: dict[str, Any]) -> dict[str, Any]:
    return _live_json(ROW_IDENTITY_CRITIC_SYSTEM, payload)


def _live_culmination(payload: dict[str, Any]) -> dict[str, Any]:
    return _live_json(CULMINATION_SYSTEM, payload)


def _live_culmination_critic(payload: dict[str, Any]) -> dict[str, Any]:
    return _live_json(CULMINATION_CRITIC_SYSTEM, payload)


# ---------------------------------------------------------------------------
# Final prerequisite authority


def _capture_lookup(merged: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for stage, items in (merged.get("captures") or {}).items():
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("prerequisite_id") or "")
            if not item_id:
                continue
            ref = f"{stage}:{item_id}"
            found[ref] = {
                "capture_ref": ref,
                "stage": str(stage),
                "text": _normal(item.get("text")),
                "rationale": _normal(item.get("rationale")),
                "evidence": [str(value) for value in item.get("evidence") or []],
            }
    return found


def _evidence_index(
    env: Mapping[str, Any], capture_rows: list[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    wanted = {
        str(evidence)
        for row in capture_rows
        for evidence in row.get("evidence") or []
        if str(evidence)
    }
    index: dict[str, dict[str, Any]] = {}
    for block in (env.get("canonical") or {}).get("blocks") or []:
        if not isinstance(block, Mapping):
            continue
        block_id = str(block.get("block_id") or "")
        if block_id in wanted:
            index[block_id] = {
                "kind": str(block.get("kind") or ""),
                "text": str(
                    block.get("display_text") or block.get("raw_text") or ""
                ),
            }
    for item in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "")
        if qid in wanted:
            index[qid] = {
                "kind": str(item.get("source_kind") or "task"),
                "task": str(
                    item.get("polished_task")
                    or item.get("normalized_task")
                    or item.get("raw_task")
                    or ""
                ),
                "shared_context": str(item.get("shared_context") or ""),
            }
    for topic in (env.get("graph") or {}).get("topics") or []:
        if not isinstance(topic, Mapping):
            continue
        topic_id = str(topic.get("topic_id") or "")
        if topic_id in wanted:
            index[topic_id] = {
                "kind": "topic",
                "title": str(topic.get("title") or ""),
            }
    return index


def _prerequisite_checker(expected_refs: set[str]):
    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        prerequisites = response.get("prerequisites")
        dispositions = response.get("dispositions")
        if not isinstance(prerequisites, list):
            defects.append("response has no prerequisites array")
            prerequisites = []
        if dispositions is None:
            dispositions = []
        if not isinstance(dispositions, list):
            defects.append("response dispositions is not an array")
            dispositions = []
        seen: set[str] = set()
        texts: set[str] = set()
        for position, row in enumerate(prerequisites, 1):
            if not isinstance(row, Mapping):
                defects.append("a prerequisite is not an object")
                continue
            expected_id = f"PR-{position:04d}"
            item_id = str(row.get("prerequisite_id") or "")
            if item_id != expected_id:
                defects.append(
                    f"prerequisite at position {position} must be {expected_id}"
                )
            text = _normal(row.get("text"))
            if not text:
                defects.append(f"{item_id or expected_id} has empty text")
            key = text.casefold()
            if key and key in texts:
                defects.append(
                    f"{item_id or expected_id} duplicates another final prerequisite"
                )
            if key:
                texts.add(key)
            if not _normal(row.get("rationale")):
                defects.append(f"{item_id or expected_id} has no rationale")
            refs = [str(value) for value in row.get("captures") or [] if str(value)]
            if not refs:
                defects.append(f"{item_id or expected_id} names no capture_ref")
            for ref in refs:
                if ref not in expected_refs:
                    defects.append(f"{item_id or expected_id} names unknown {ref}")
                elif ref in seen:
                    defects.append(f"capture_ref {ref} is accounted more than once")
                else:
                    seen.add(ref)
        for position, row in enumerate(dispositions, 1):
            if not isinstance(row, Mapping):
                defects.append("a disposition is not an object")
                continue
            expected_id = f"PD-{position:04d}"
            item_id = str(row.get("disposition_id") or "")
            if item_id != expected_id:
                defects.append(
                    f"disposition at position {position} must be {expected_id}"
                )
            classification = str(row.get("classification") or "")
            if classification not in PREREQUISITE_DISPOSITIONS:
                defects.append(
                    f"{item_id or expected_id} has invalid classification "
                    f"{classification!r}"
                )
            if not _normal(row.get("rationale")):
                defects.append(f"{item_id or expected_id} has no rationale")
            refs = [str(value) for value in row.get("captures") or [] if str(value)]
            if not refs:
                defects.append(f"{item_id or expected_id} names no capture_ref")
            for ref in refs:
                if ref not in expected_refs:
                    defects.append(f"{item_id or expected_id} names unknown {ref}")
                elif ref in seen:
                    defects.append(f"capture_ref {ref} is accounted more than once")
                else:
                    seen.add(ref)
        missing = sorted(expected_refs - seen)
        if missing:
            defects.append("unaccounted capture_ref(s): " + ", ".join(missing))
        return defects

    return check


def adjudicate_prerequisites(
    env: Mapping[str, Any],
    merged: Mapping[str, Any],
    *,
    provider=None,
    critic=None,
    store=None,
    fixer=None,
) -> dict[str, Any]:
    """Turn broad stage captures into the final Pre-Learning authority."""
    from . import progress
    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod
    from .phase3 import kernel

    env = envelope_mod.validate(env)
    lookup = _capture_lookup(merged)
    if not lookup:
        result = copy.deepcopy(dict(merged))
        result.setdefault("dispositions", [])
        return result
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_prerequisite
        critic = critic or _live_prerequisite_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    capture_rows = [lookup[ref] for ref in sorted(lookup)]
    payload = {
        "stage": "prelearn.adjudicate",
        "rules": (
            "Use the model judgment described by the system message. "
            "Disposition classes: " + ", ".join(PREREQUISITE_DISPOSITIONS)
        ),
        "chapter": _chapter(env),
        "captures": capture_rows,
        "evidence_index": _evidence_index(env, capture_rows),
        "merge_suggestions": copy.deepcopy(merged.get("prerequisites") or []),
    }
    decision = kernel.decide(
        kind="prelearn.adjudicate",
        unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""),
        payload=payload,
        provider=provider,
        checker=_prerequisite_checker(set(lookup)),
        critic=critic,
        store=store,
        policy_version=PREREQUISITE_POLICY_VERSION,
        fixer=fixer,
    )
    response = decision.get("response") or {}
    flags = list(decision.get("review_flags") or [])
    response_ids = [
        str(entry.get("prerequisite_id") or "")
        for entry in response.get("prerequisites") or []
        if isinstance(entry, Mapping)
    ]

    prerequisites: list[dict[str, Any]] = []
    review_flags: dict[str, list[str]] = {}
    for row in response.get("prerequisites") or []:
        refs = [str(value) for value in row.get("captures") or []]
        source_rows = [lookup[ref] for ref in refs]
        item_id = str(row.get("prerequisite_id") or "")
        prerequisites.append({
            "prerequisite_id": item_id,
            "text": _normal(row.get("text")),
            "captures": refs,
            "stages": list(dict.fromkeys(
                source["stage"] for source in source_rows
            )),
            "evidence": list(dict.fromkeys(
                evidence
                for source in source_rows
                for evidence in source.get("evidence") or []
            )),
            "rationale": _normal(row.get("rationale")),
        })
        pinned = kernel.pin_flags(flags, response_ids, item_id)
        if pinned:
            review_flags[item_id] = pinned

    dispositions: list[dict[str, Any]] = []
    for row in response.get("dispositions") or []:
        refs = [str(value) for value in row.get("captures") or []]
        source_rows = [lookup[ref] for ref in refs]
        dispositions.append({
            "disposition_id": str(row.get("disposition_id") or ""),
            "classification": str(row.get("classification") or ""),
            "captures": refs,
            "stages": list(dict.fromkeys(
                source["stage"] for source in source_rows
            )),
            "evidence": list(dict.fromkeys(
                evidence
                for source in source_rows
                for evidence in source.get("evidence") or []
            )),
            "rationale": _normal(row.get("rationale")),
        })

    result = copy.deepcopy(dict(merged))
    result["prerequisites"] = prerequisites
    result["dispositions"] = dispositions
    result["review_flags"] = review_flags
    stage_flags = copy.deepcopy(result.get("stage_flags") or {})
    if flags:
        stage_flags["adjudication"] = flags
    result["stage_flags"] = stage_flags
    result["adjudication"] = {
        "policy_version": PREREQUISITE_POLICY_VERSION,
        "decision_key": str(decision.get("key") or ""),
        "capture_count": len(lookup),
        "prerequisite_count": len(prerequisites),
        "disposed_capture_count": sum(
            len(row.get("captures") or []) for row in dispositions
        ),
    }
    progress.log(
        "Pre-Learning authority: the model retained "
        f"{len(prerequisites)} prerequisite(s) and explicitly disposed "
        f"{sum(len(row.get('captures') or []) for row in dispositions)} "
        "broad capture(s) as chapter-taught, supplied, procedural, or "
        "unsupported evidence.",
        level="success",
    )
    return result


# ---------------------------------------------------------------------------
# Duplicate concept identity reconciliation before Host/Assemble


def _row_key(row: Mapping[str, Any]) -> tuple[str, str]:
    topic = str(row.get("_semantic_topic_id") or "") or _normal(
        row.get("topic")
    ).casefold()
    return topic, _normal(row.get("concept_title")).casefold()


def _duplicate_groups(rows: list[Mapping[str, Any]]) -> list[list[int]]:
    by_key: dict[tuple[str, str], list[int]] = {}
    for index, row in enumerate(rows):
        key = _row_key(row)
        if key[1]:
            by_key.setdefault(key, []).append(index)
    return [indexes for indexes in by_key.values() if len(indexes) > 1]


def _description_and_mastery(details: object) -> tuple[str, str]:
    from . import concept_refiner as cr

    description = ""
    mastery = ""
    for label, content in cr.split_sections(str(details or "")):
        name = _normal(label).casefold()
        if name.startswith("description"):
            description = _normal(content)
        elif name.startswith("achieving mastery"):
            mastery = _normal(content)
    return description, mastery


def _row_identity_checker(
    groups: list[dict[str, Any]],
    unaffected_titles: dict[str, set[str]],
):
    expected = {
        group["group_id"]: {
            row["source_row_id"] for row in group["rows"]
        }
        for group in groups
    }

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        decided = response.get("groups")
        if not isinstance(decided, list):
            return ["response has no groups array"]
        seen_groups: set[str] = set()
        for group in decided:
            if not isinstance(group, Mapping):
                defects.append("a reconciliation group is not an object")
                continue
            group_id = str(group.get("group_id") or "")
            if group_id not in expected or group_id in seen_groups:
                defects.append(f"unknown or repeated group_id {group_id!r}")
                continue
            seen_groups.add(group_id)
            if not _normal(group.get("rationale")):
                defects.append(f"{group_id} has no reconciliation rationale")
            concepts = group.get("concepts")
            dispositions = group.get("dispositions")
            if not isinstance(concepts, list) or not concepts:
                defects.append(f"{group_id} must retain at least one concept")
                concepts = []
            if dispositions is None:
                dispositions = []
            if not isinstance(dispositions, list):
                defects.append(f"{group_id} dispositions is not an array")
                dispositions = []
            accounted: set[str] = set()
            titles: set[str] = set()
            for concept in concepts:
                if not isinstance(concept, Mapping):
                    defects.append(f"{group_id} concept is not an object")
                    continue
                title = _normal(concept.get("concept_title"))
                title_key = title.casefold()
                if not title:
                    defects.append(f"{group_id} output concept has no title")
                if title_key in titles:
                    defects.append(f"{group_id} repeats output title {title!r}")
                if title_key:
                    titles.add(title_key)
                topic_id = str(group.get("topic_id") or "")
                if title_key in unaffected_titles.get(topic_id, set()):
                    defects.append(
                        f"{group_id} output title {title!r} collides with an "
                        "unaffected concept in the same topic"
                    )
                for field in (
                    "parent_concept",
                    "description",
                    "achieving_mastery",
                    "keywords",
                    "rationale",
                ):
                    if not _normal(concept.get(field)):
                        defects.append(
                            f"{group_id} output {title or '<untitled>'} has no {field}"
                        )
                refs = [str(value) for value in concept.get("source_row_ids") or []]
                if not refs:
                    defects.append(f"{group_id} output {title!r} has no source rows")
                for ref in refs:
                    if ref not in expected[group_id]:
                        defects.append(f"{group_id} names unknown source row {ref}")
                    elif ref in accounted:
                        defects.append(f"{group_id} accounts {ref} more than once")
                    else:
                        accounted.add(ref)
            for disposition in dispositions:
                if not isinstance(disposition, Mapping):
                    defects.append(f"{group_id} disposition is not an object")
                    continue
                classification = str(disposition.get("classification") or "")
                if classification not in ROW_DISPOSITIONS:
                    defects.append(
                        f"{group_id} disposition has invalid classification "
                        f"{classification!r}"
                    )
                target = _normal(disposition.get("attach_to_concept_title"))
                if target.casefold() not in titles:
                    defects.append(
                        f"{group_id} disposition attaches to unknown output "
                        f"concept {target!r}"
                    )
                if not _normal(disposition.get("rationale")):
                    defects.append(f"{group_id} disposition has no rationale")
                refs = [
                    str(value) for value in disposition.get("source_row_ids") or []
                ]
                if not refs:
                    defects.append(f"{group_id} disposition has no source rows")
                for ref in refs:
                    if ref not in expected[group_id]:
                        defects.append(f"{group_id} names unknown source row {ref}")
                    elif ref in accounted:
                        defects.append(f"{group_id} accounts {ref} more than once")
                    else:
                        accounted.add(ref)
            missing = sorted(expected[group_id] - accounted)
            if missing:
                defects.append(
                    f"{group_id} leaves source row(s) unaccounted: "
                    + ", ".join(missing)
                )
        missing_groups = sorted(set(expected) - seen_groups)
        if missing_groups:
            defects.append(
                "unresolved collision group(s): " + ", ".join(missing_groups)
            )
        return defects

    return check


def _union_list(rows: list[Mapping[str, Any]], field: str) -> list[Any]:
    result: list[Any] = []
    for row in rows:
        for value in row.get(field) or []:
            if value not in result:
                result.append(copy.deepcopy(value))
    return result


def _culmination_checker(expected_topic_ids: set[str]):
    def check(response: Mapping[str, Any]) -> list[str]:
        rows = response.get("topics")
        if not isinstance(rows, list):
            return ["response has no topics array"]
        defects: list[str] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a culmination response is not an object")
                continue
            topic_id = str(row.get("topic_id") or "")
            if topic_id not in expected_topic_ids or topic_id in seen:
                defects.append(f"unknown or repeated topic_id {topic_id!r}")
                continue
            seen.add(topic_id)
            if not _normal(row.get("consolidation")):
                defects.append(f"{topic_id} has no consolidation")
            if not _normal(row.get("rationale")):
                defects.append(f"{topic_id} has no rationale")
        missing = sorted(expected_topic_ids - seen)
        if missing:
            defects.append("unrefreshed culmination topic(s): " + ", ".join(missing))
        return defects

    return check


def _refresh_affected_culminations(
    env: Mapping[str, Any],
    rows: list[dict[str, Any]],
    affected_topic_ids: set[str],
    *,
    provider=None,
    critic=None,
    store=None,
    fixer=None,
) -> list[dict[str, Any]]:
    from . import concept_refiner as cr
    from . import progress
    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod
    from .phase3 import kernel

    by_topic: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_topic.setdefault(str(row.get("_semantic_topic_id") or ""), []).append(row)

    # Preserve Settle's existing structural rule: one concept has nothing to
    # consolidate. Removing a now-redundant culmination is bookkeeping over the
    # model-decided final concept set, not a semantic judgment.
    removable: set[int] = set()
    requests: list[dict[str, Any]] = []
    culmination_by_topic: dict[str, dict[str, Any]] = {}
    for topic_id in sorted(affected_topic_ids):
        topic_rows = by_topic.get(topic_id, [])
        concepts = [
            row for row in topic_rows
            if not cr.is_culmination(_normal(row.get("concept_title")))
        ]
        culminations = [
            row for row in topic_rows
            if cr.is_culmination(_normal(row.get("concept_title")))
        ]
        if len(culminations) > 1:
            raise kernel.ContractError(
                f"topic {topic_id} carries multiple culmination rows before "
                "identity reconciliation"
            )
        if not culminations:
            continue
        if len(concepts) < 2:
            removable.add(id(culminations[0]))
            continue
        culmination_by_topic[topic_id] = culminations[0]
        requests.append({
            "topic_id": topic_id,
            "topic_title": str(concepts[0].get("topic") or ""),
            "final_concepts": [
                {
                    "concept_title": str(row.get("concept_title") or ""),
                    "description": _description_and_mastery(
                        row.get("concept_details")
                    )[0],
                    "achieving_mastery": _description_and_mastery(
                        row.get("concept_details")
                    )[1],
                }
                for row in concepts
            ],
        })
    if removable:
        rows = [row for row in rows if id(row) not in removable]
    if not requests:
        return rows

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_culmination
        critic = critic or _live_culmination_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    expected = {row["topic_id"] for row in requests}
    payload = {
        "stage": "settle.row_identity_culmination",
        "chapter": _chapter(env),
        "topics": requests,
    }
    decision = kernel.decide(
        kind="settle.row_identity_culmination",
        unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""),
        payload=payload,
        provider=provider,
        checker=_culmination_checker(expected),
        critic=critic,
        store=store,
        policy_version=CULMINATION_POLICY_VERSION,
        fixer=fixer,
    )
    response = {
        str(row.get("topic_id") or ""): row
        for row in (decision.get("response") or {}).get("topics") or []
        if isinstance(row, Mapping)
    }
    flags = list(decision.get("review_flags") or [])
    for request in requests:
        topic_id = request["topic_id"]
        culmination = culmination_by_topic[topic_id]
        topic_rows = [
            row for row in rows
            if str(row.get("_semantic_topic_id") or "") == topic_id
            and not cr.is_culmination(_normal(row.get("concept_title")))
        ]
        titles = [str(row.get("concept_title") or "") for row in topic_rows]
        decided = response[topic_id]
        culmination["concept_title"] = "Culmination - " + ", ".join(titles)
        culmination["parent_concept"] = "Culmination"
        culmination["concept_details"] = (
            "Description: " + _normal(decided.get("consolidation"))
        )
        culmination["keywords"] = ", ".join(titles)
        culmination["_source_block_ids"] = _union_list(
            topic_rows, "_source_block_ids"
        )
        culmination["_semantic_subtopic_ids"] = _union_list(
            topic_rows, "_semantic_subtopic_ids"
        )
        culmination["_source_grounding_contract"] = (
            "derived-from-verified-topic-concepts"
        )
        culmination["_phase32_culmination_reconciliation"] = {
            "rationale": _normal(decided.get("rationale")),
            "decision_key": str(decision.get("key") or ""),
        }
        review = list(culmination.get("review_flags") or [])
        review.append(
            "culmination refreshed after row-identity reconciliation: "
            + _normal(decided.get("rationale"))
        )
        for flag in flags:
            if flag not in review:
                review.append(flag)
        culmination["review_flags"] = review
    progress.log(
        "Settle identity reconciliation: refreshed culmination teaching for "
        f"{len(requests)} affected topic(s) against the final concept set.",
        level="success",
    )
    return rows


def reconcile_duplicate_rows(
    env: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    *,
    provider=None,
    critic=None,
    culmination_provider=None,
    culmination_critic=None,
    store=None,
    fixer=None,
) -> list[dict[str, Any]]:
    """Resolve duplicate topic/title identities through model verdicts."""
    from . import progress
    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod
    from .phase3 import kernel

    env = envelope_mod.validate(env)
    copied = [copy.deepcopy(dict(row)) for row in rows]
    duplicate_indexes = _duplicate_groups(copied)
    if not duplicate_indexes:
        return copied
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_row_identity
        critic = critic or _live_row_identity_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()

    index_to_id = {index: f"ROW-{index + 1:04d}" for index in range(len(copied))}
    duplicate_set = {index for group in duplicate_indexes for index in group}
    unaffected: dict[str, set[str]] = {}
    for index, row in enumerate(copied):
        if index in duplicate_set:
            continue
        topic_id = str(row.get("_semantic_topic_id") or "")
        unaffected.setdefault(topic_id, set()).add(
            _normal(row.get("concept_title")).casefold()
        )

    block_text = {
        str(block.get("block_id") or ""): str(
            block.get("display_text") or block.get("raw_text") or ""
        )
        for block in (env.get("canonical") or {}).get("blocks") or []
        if isinstance(block, Mapping)
    }
    groups: list[dict[str, Any]] = []
    affected_topic_ids: set[str] = set()
    for number, indexes in enumerate(duplicate_indexes, 1):
        group_rows = [copied[index] for index in indexes]
        source_ids = _union_list(group_rows, "_source_block_ids")
        topic_id = str(group_rows[0].get("_semantic_topic_id") or "")
        affected_topic_ids.add(topic_id)
        groups.append({
            "group_id": f"DUP-{number:04d}",
            "topic_id": topic_id,
            "topic_title": str(group_rows[0].get("topic") or ""),
            "colliding_title": str(group_rows[0].get("concept_title") or ""),
            "rows": [
                {
                    "source_row_id": index_to_id[index],
                    "concept_title": str(copied[index].get("concept_title") or ""),
                    "parent_concept": str(copied[index].get("parent_concept") or ""),
                    "description": _description_and_mastery(
                        copied[index].get("concept_details")
                    )[0],
                    "achieving_mastery": _description_and_mastery(
                        copied[index].get("concept_details")
                    )[1],
                    "keywords": str(copied[index].get("keywords") or ""),
                    "source_block_ids": list(
                        copied[index].get("_source_block_ids") or []
                    ),
                    "origin_concept_id": str(
                        copied[index].get("_phase32_origin_concept_id") or ""
                    ),
                    "review_flags": list(copied[index].get("review_flags") or []),
                }
                for index in indexes
            ],
            "source_blocks": [
                {"block_id": block_id, "text": block_text.get(block_id, "")}
                for block_id in source_ids
            ],
        })
    payload = {
        "stage": "settle.row_identity_reconciliation",
        "rules": (
            "Use the model judgment described by the system message. "
            "Disposition classes: " + ", ".join(ROW_DISPOSITIONS)
        ),
        "chapter": _chapter(env),
        "groups": groups,
    }
    decision = kernel.decide(
        kind="settle.row_identity_reconciliation",
        unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""),
        payload=payload,
        provider=provider,
        checker=_row_identity_checker(groups, unaffected),
        critic=critic,
        store=store,
        policy_version=ROW_IDENTITY_POLICY_VERSION,
        fixer=fixer,
    )
    response_groups = {
        str(group.get("group_id") or ""): group
        for group in (decision.get("response") or {}).get("groups") or []
        if isinstance(group, Mapping)
    }
    flags = list(decision.get("review_flags") or [])
    group_by_index: dict[int, tuple[str, list[int]]] = {}
    for group, indexes in zip(groups, duplicate_indexes):
        for index in indexes:
            group_by_index[index] = (group["group_id"], indexes)

    emitted_groups: set[str] = set()
    output: list[dict[str, Any]] = []
    for index, row in enumerate(copied):
        group_info = group_by_index.get(index)
        if group_info is None:
            output.append(row)
            continue
        group_id, indexes = group_info
        if group_id in emitted_groups:
            continue
        emitted_groups.add(group_id)
        source_by_id = {index_to_id[i]: copied[i] for i in indexes}
        resolved = response_groups[group_id]
        concepts = [
            concept for concept in resolved.get("concepts") or []
            if isinstance(concept, Mapping)
        ]
        built_by_title: dict[str, dict[str, Any]] = {}
        for concept in concepts:
            refs = [str(value) for value in concept.get("source_row_ids") or []]
            source_rows = [source_by_id[ref] for ref in refs]
            base = copy.deepcopy(source_rows[0])
            title = _normal(concept.get("concept_title"))
            base["concept_title"] = title
            base["parent_concept"] = _normal(concept.get("parent_concept"))
            base["concept_details"] = (
                "Description: " + _normal(concept.get("description"))
                + "\nAchieving Mastery: "
                + _normal(concept.get("achieving_mastery"))
            )
            base["keywords"] = _normal(concept.get("keywords"))
            base["_source_block_ids"] = _union_list(source_rows, "_source_block_ids")
            base["_semantic_subtopic_ids"] = _union_list(
                source_rows, "_semantic_subtopic_ids"
            )
            origin_ids = list(dict.fromkeys(
                str(source.get("_phase32_origin_concept_id") or "")
                for source in source_rows
                if str(source.get("_phase32_origin_concept_id") or "")
            ))
            if origin_ids:
                base["_phase32_origin_concept_id"] = origin_ids[0]
            base["_phase32_origin_concept_ids"] = origin_ids
            base["_phase32_identity_reconciliation"] = {
                "group_id": group_id,
                "source_row_ids": refs,
                "rationale": _normal(concept.get("rationale")),
                "decision_key": str(decision.get("key") or ""),
            }
            review = _union_list(source_rows, "review_flags")
            review.append(
                f"row-identity reconciliation {group_id}: "
                + _normal(concept.get("rationale"))
            )
            for flag in flags:
                if flag not in review:
                    review.append(flag)
            base["review_flags"] = review
            built_by_title[title.casefold()] = base
            output.append(base)

        for disposition in resolved.get("dispositions") or []:
            if not isinstance(disposition, Mapping):
                continue
            refs = [str(value) for value in disposition.get("source_row_ids") or []]
            source_rows = [source_by_id[ref] for ref in refs]
            target_key = _normal(
                disposition.get("attach_to_concept_title")
            ).casefold()
            target = built_by_title[target_key]
            target["_source_block_ids"] = list(dict.fromkeys([
                *(target.get("_source_block_ids") or []),
                *(
                    value
                    for source in source_rows
                    for value in source.get("_source_block_ids") or []
                ),
            ]))
            target["_semantic_subtopic_ids"] = list(dict.fromkeys([
                *(target.get("_semantic_subtopic_ids") or []),
                *(
                    value
                    for source in source_rows
                    for value in source.get("_semantic_subtopic_ids") or []
                ),
            ]))
            disposition_record = {
                "classification": str(disposition.get("classification") or ""),
                "source_row_ids": refs,
                "rationale": _normal(disposition.get("rationale")),
            }
            target.setdefault("_phase32_nonconcept_support", []).append(
                disposition_record
            )
            for source in source_rows:
                for flag in source.get("review_flags") or []:
                    if flag not in target.setdefault("review_flags", []):
                        target["review_flags"].append(flag)
            target.setdefault("review_flags", []).append(
                "attached non-concept support "
                f"({disposition_record['classification']}): "
                + disposition_record["rationale"]
            )

    if _duplicate_groups(output):
        raise kernel.ContractError(
            "row identity reconciliation returned duplicate topic/title keys"
        )
    output = _refresh_affected_culminations(
        env,
        output,
        affected_topic_ids,
        provider=culmination_provider,
        critic=culmination_critic,
        store=store,
        fixer=fixer,
    )
    progress.log(
        "Settle identity reconciliation: the model resolved "
        f"{len(duplicate_indexes)} duplicate topic/title group(s) before "
        "Host and Assemble; supporting activities/instructions were attached "
        "explicitly rather than emitted as duplicate concepts.",
        level="success",
    )
    return output


# ---------------------------------------------------------------------------
# Reload-safe installation


def install() -> None:
    """Install both semantic seams on the current live Phase 3 modules."""
    prelearn_mod = importlib.import_module("app.services.phase3.prelearn")
    settle_mod = importlib.import_module("app.services.phase3.settle")

    current_merge = prelearn_mod.merge
    if not getattr(current_merge, "_FORMATION_CONTRACT_WRAPPER", False):
        original_merge = current_merge

        @wraps(original_merge)
        def merge(*args, **kwargs):
            adjudication_provider = kwargs.pop("adjudication_provider", None)
            adjudication_critic = kwargs.pop("adjudication_critic", None)
            explicit_merge_provider = kwargs.get("provider") is not None
            merged = original_merge(*args, **kwargs)
            # Existing injected golden providers model only the historical
            # merge decision. Production never injects one; focused tests may
            # opt into the new semantic seam explicitly.
            if explicit_merge_provider and adjudication_provider is None:
                return merged
            env = args[0] if args else kwargs.get("env")
            return adjudicate_prerequisites(
                env,
                merged,
                provider=adjudication_provider,
                critic=adjudication_critic,
                store=kwargs.get("store"),
                fixer=kwargs.get("fixer"),
            )

        merge._FORMATION_CONTRACT_WRAPPER = True
        merge._FORMATION_CONTRACT_VERSION = CONTRACT_VERSION
        prelearn_mod._FORMATION_ORIGINAL_MERGE = original_merge
        prelearn_mod.merge = merge
    prelearn_mod._FORMATION_CONTRACT_VERSION = CONTRACT_VERSION

    current_settle = settle_mod.settle
    if not getattr(current_settle, "_ROW_IDENTITY_CONTRACT_WRAPPER", False):
        original_settle = current_settle

        @wraps(original_settle)
        def settle(*args, **kwargs):
            identity_provider = kwargs.pop("identity_provider", None)
            identity_critic = kwargs.pop("identity_critic", None)
            culmination_provider = kwargs.pop("identity_culmination_provider", None)
            culmination_critic = kwargs.pop("identity_culmination_critic", None)
            explicit_topology_provider = kwargs.get("topology_provider") is not None
            rows = original_settle(*args, **kwargs)
            if not _duplicate_groups(rows):
                return rows
            if explicit_topology_provider and identity_provider is None:
                # Backward-compatible test injection: focused tests exercise
                # the new seam explicitly. Production never takes this branch.
                return rows
            env = args[0] if args else kwargs.get("env")
            return reconcile_duplicate_rows(
                env,
                rows,
                provider=identity_provider,
                critic=identity_critic,
                culmination_provider=culmination_provider,
                culmination_critic=culmination_critic,
                store=kwargs.get("store"),
                fixer=kwargs.get("fixer"),
            )

        settle._ROW_IDENTITY_CONTRACT_WRAPPER = True
        settle._ROW_IDENTITY_CONTRACT_VERSION = CONTRACT_VERSION
        settle_mod._ROW_IDENTITY_ORIGINAL_SETTLE = original_settle
        settle_mod.settle = settle
    settle_mod._ROW_IDENTITY_CONTRACT_VERSION = CONTRACT_VERSION
