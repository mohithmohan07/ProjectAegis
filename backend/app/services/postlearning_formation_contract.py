"""Make the model-authored literary plan authoritative for Post-Learning.

Step 11 already authors a source-grounded poem/prose plan before the 81% Phase
3 boundary, and the semantic graph already materializes its Topics.  The
remaining gap was the concept half: Phase 3 still received the generic skeleton
made before that plan and could therefore turn Warm-ups, recitation cues,
Word-Basket blocks or exercise clusters into concepts while omitting the
stanza/episode concepts the plan had actually decided.

This contract closes that gap without adding a deterministic literary parser.
Before ``runner.run`` seals its decisions, the recorded plan is projected into
Phase 3 skeleton rows by opaque plan IDs and graph Topic IDs.  Settle still does
all source-grounded topology, grounding and learner-facing authoring.  After
Settle, the plan identities and semantic roles are restored onto the rows; the
common one-to-one path costs no extra model call.  A split, missing culmination,
merged plan identity or other drift goes to one content-addressed model
conformance decision, with the ordinary critic/Fixer doctrine and exact-once
row accounting.

Deterministic work is limited to plan/graph ID validation, envelope resealing,
exact-once transport, source-grounding transport and application of the
recorded verdict.  No stanza detection, line pairing, episode formation,
subject keyword or grade threshold lives here.
"""
from __future__ import annotations

import copy
import importlib
import json
import re
import sys
from functools import wraps
from typing import Any, Callable, Mapping, Sequence


CONTRACT_VERSION = 1
MATERIALIZATION_VERSION = "post-language-plan-materialization-1"
CONFORMANCE_POLICY_VERSION = "post-language-plan-conformance-1"

PLAN_IDENTITY_FIELD = "_aegis_language_plan_identity"
SEMANTIC_ROLE_FIELD = "_aegis_language_semantic_role"
PLANNED_QIDS_FIELD = "_aegis_language_plan_task_qids"
POST_LANGUAGE_AUDIT_FIELDS = frozenset({
    PLAN_IDENTITY_FIELD,
    SEMANTIC_ROLE_FIELD,
    PLANNED_QIDS_FIELD,
})

_CULMINATION_ROLES = frozenset({
    "stanza_culmination",
    "topic_culmination",
    "chapter_culmination",
})
_SPACE_RE = re.compile(r"\s+")
_FIELD_LABEL_RE = re.compile(
    r"^(?:Description|Achieving\s+Mastery)\s*:\s*",
    re.IGNORECASE,
)

CONFORMANCE_SYSTEM = """\
You are the final Post-Learning conformance author for one literary chapter.
The recorded language plan is authoritative: return exactly one learner-facing
concept for every supplied plan_concept_id, in the supplied order, under its
supplied Topic and semantic role. Do not create, omit, merge or split plan
concept identities.

The current settled rows are drafts produced from that same plan. Usually one
row already belongs to one plan concept. If Settle split one identity, merge
those drafts back into the one planned concept. If a planned culmination is
missing, author it from the final member concepts and source evidence. Account
for every current settled_row_id exactly once in source_row_ids; a missing plan
concept may legitimately have an empty source_row_ids list, but an existing row
may never disappear or be used twice.

For each concept, concept_title must equal the supplied wire_title exactly.
Description must TEACH at the named grade: explain the idea, its important
terms, the reasoning or literary effect, and the relevant source detail. It
must not be a plot retelling, exercise summary, copied sentence, placeholder or
bare cross-reference. achieving_mastery is one distinct learner capability.
parent_concept is a concise grouping label. keywords are useful curriculum
terms, not IDs. rationale explains how the draft rows/evidence were reconciled.

A stanza/topic culmination teaches what its member concepts and form do
together, never a list of names. The chapter culmination synthesizes the whole
work. Warm-ups, reading directions, exercise instructions, Word-Basket boxes,
device boxes and facilitator notes do not become extra concepts unless the
language plan itself contains that concept identity. Preserve Grade/board
terminology and never copy a source question into Description or mastery."""

CONFORMANCE_CRITIC_SYSTEM = """\
Independently audit the proposed Post-Learning conformance against the recorded
language plan, source evidence and current settled drafts. Check that every
plan concept exists once, every settled row is accounted once, stanza/episode
meaning was not replaced by pedagogy or exercise structure, descriptions teach
rather than summarize, culminations synthesize rather than list, mastery lines
are distinct, and no source question was copied into concept prose. Dissent is
advisory and must identify the plan_concept_id it concerns."""


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def _normal_key(value: object) -> str:
    return _normal(value).casefold()


def _list_strings(value: object) -> list[str]:
    if isinstance(value, (str, bytes)):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(
        _normal(item) for item in value if _normal(item)
    ))


def _parse_plan(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        parsed = copy.deepcopy(dict(value))
    elif isinstance(value, str) and value.strip():
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return None
        parsed = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
    else:
        return None
    if isinstance(parsed.get("plan"), Mapping):
        parsed = copy.deepcopy(dict(parsed["plan"]))
    topics = parsed.get("topics")
    return parsed if isinstance(topics, list) and topics else None


def _plan_candidates(env: Mapping[str, Any]) -> list[object]:
    candidates: list[object] = []
    for metadata in (
        env.get("metadata"),
        (env.get("graph") or {}).get("metadata")
        if isinstance(env.get("graph"), Mapping) else None,
    ):
        if not isinstance(metadata, Mapping):
            continue
        candidates.append(metadata.get("language_topology_plan"))
        for key in ("instruction_slots", "slots"):
            slots = metadata.get(key)
            if isinstance(slots, Mapping):
                candidates.append(slots.get("language_topology_plan"))
        instruction_set = metadata.get("instruction_set")
        if isinstance(instruction_set, Mapping):
            slots = instruction_set.get("slots")
            if isinstance(slots, Mapping):
                candidates.append(slots.get("language_topology_plan"))
    return candidates


def language_plan(env: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the first readable transported plan body, or ``None``."""
    for candidate in _plan_candidates(env):
        parsed = _parse_plan(candidate)
        if parsed is not None:
            return parsed
    return None


def _wire_title(display_name: object, semantic_role: object) -> str:
    title = _normal(display_name)
    role = _normal(semantic_role)
    if role in _CULMINATION_ROLES and not title.casefold().startswith(
        "culmination"
    ):
        return f"Culmination: {title}"
    return title


def _known_ids(env: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    block_ids = {
        str(row.get("block_id") or "")
        for row in (env.get("graph") or {}).get("blocks") or []
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    }
    qids = {
        str(row.get("qid") or "")
        for row in (env.get("inventory") or {}).get("items") or []
        if isinstance(row, Mapping) and str(row.get("qid") or "")
    }
    return block_ids, qids


def _recorded_split_children(env: Mapping[str, Any]) -> dict[str, list[str]]:
    """Model-split child QIDs keyed by the parent QID the inventory recorded.

    The plan is authored and replayed against the canonical bundle's task
    QIDs, while this boundary validates against the inventory — where the
    reader may have split one printed task into recorded sub-questions.
    A parent reference resolves only through that recorded ``parent_qid``
    linkage, in inventory order; nothing is matched by wording or shape.
    """
    children: dict[str, list[str]] = {}
    for row in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(row, Mapping):
            continue
        qid = str(row.get("qid") or "")
        parent = str(
            row.get("parent_qid") or row.get("_acsd_parent_qid") or ""
        )
        if qid and parent and parent != qid:
            children.setdefault(parent, []).append(qid)
    return children


def plan_entries(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate and flatten the transported plan in its authored order."""
    plan = language_plan(env)
    if plan is None:
        return []
    graph_topics = [
        row for row in (env.get("graph") or {}).get("topics") or []
        if isinstance(row, Mapping)
    ]
    topics_by_plan_id: dict[str, list[Mapping[str, Any]]] = {}
    for row in graph_topics:
        plan_topic_id = str(row.get("plan_topic_id") or "")
        if plan_topic_id:
            topics_by_plan_id.setdefault(plan_topic_id, []).append(row)

    known_blocks, known_qids = _known_ids(env)
    split_children = _recorded_split_children(env)
    seen_topics: set[str] = set()
    seen_concepts: set[str] = set()
    seen_task_owners: set[str] = set()
    entries: list[dict[str, Any]] = []
    normal_position = 0

    for topic_position, topic in enumerate(plan.get("topics") or [], start=1):
        if not isinstance(topic, Mapping):
            raise ValueError("language topology plan contains a non-object topic")
        plan_topic_id = str(topic.get("plan_topic_id") or "")
        if not plan_topic_id or plan_topic_id in seen_topics:
            raise ValueError("language topology plan has a missing/duplicate topic id")
        seen_topics.add(plan_topic_id)
        graph_matches = topics_by_plan_id.get(plan_topic_id) or []
        if len(graph_matches) != 1:
            raise ValueError(
                f"language plan topic {plan_topic_id!r} does not map to exactly "
                "one semantic-graph topic"
            )
        graph_topic = graph_matches[0]
        topic_title = _normal(
            graph_topic.get("title") or topic.get("display_name")
        )
        concepts = [
            row for row in topic.get("concepts") or []
            if isinstance(row, Mapping)
        ]
        if not concepts:
            raise ValueError(f"language plan topic {plan_topic_id} has no concepts")
        for concept_position, concept in enumerate(concepts, start=1):
            plan_concept_id = str(concept.get("plan_concept_id") or "")
            if not plan_concept_id or plan_concept_id in seen_concepts:
                raise ValueError(
                    "language topology plan has a missing/duplicate concept id"
                )
            seen_concepts.add(plan_concept_id)
            display_name = _normal(concept.get("display_name"))
            role = _normal(concept.get("semantic_role"))
            mastery = _normal(concept.get("achieving_mastery"))
            if not display_name or not role or not mastery:
                raise ValueError(
                    f"language plan concept {plan_concept_id} is missing title, "
                    "semantic_role or achieving_mastery"
                )
            source_block_ids = _list_strings(concept.get("source_block_ids"))
            unknown_blocks = [
                value for value in source_block_ids if value not in known_blocks
            ]
            if unknown_blocks:
                raise ValueError(
                    f"language plan concept {plan_concept_id} names unknown "
                    "source block(s): " + ", ".join(unknown_blocks[:6])
                )
            resolved_qids: list[str] = []
            expansions: dict[str, list[str]] = {}
            unknown_qids: list[str] = []
            for qid in _list_strings(concept.get("task_qids")):
                if qid in known_qids:
                    resolved_qids.append(qid)
                elif qid in split_children:
                    expansions[qid] = list(split_children[qid])
                    resolved_qids.extend(split_children[qid])
                else:
                    unknown_qids.append(qid)
            if unknown_qids:
                raise ValueError(
                    f"language plan concept {plan_concept_id} names unknown "
                    "task(s): " + ", ".join(unknown_qids[:6])
                )
            task_qids = list(dict.fromkeys(resolved_qids))
            repeated_qids = [value for value in task_qids if value in seen_task_owners]
            if repeated_qids:
                raise ValueError(
                    "language topology plan routes task(s) to more than one "
                    "concept: " + ", ".join(repeated_qids[:6])
                )
            seen_task_owners.update(task_qids)
            culmination = role in _CULMINATION_ROLES
            if not culmination:
                normal_position += 1
            entries.append({
                "order": len(entries) + 1,
                "topic_position": topic_position,
                "concept_position": concept_position,
                "plan_topic_id": plan_topic_id,
                "plan_concept_id": plan_concept_id,
                "topic_id": str(graph_topic.get("topic_id") or ""),
                "topic_title": topic_title,
                "display_name": display_name,
                "wire_title": _wire_title(display_name, role),
                "semantic_role": role,
                "facets": _list_strings(concept.get("facets")),
                "source_block_ids": source_block_ids,
                "task_qids": task_qids,
                "task_qid_expansions": expansions,
                "achieving_mastery": mastery,
                "rationale": _normal(concept.get("rationale")),
                "culmination": culmination,
                "origin_concept_id": (
                    "" if culmination
                    else f"TOPOLOGY-CONCEPT-{normal_position:04d}"
                ),
            })
    return entries


def _draft_details(entry: Mapping[str, Any]) -> str:
    description = _normal(entry.get("rationale")) or _normal(
        entry.get("display_name")
    )
    return (
        "Description: " + description
        + "\nAchieving Mastery: "
        + _normal(entry.get("achieving_mastery"))
    )


def _skeleton_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "topic": entry["topic_title"],
        "parent_concept": (
            "Culmination"
            if entry["culmination"]
            else (
                "Detailed Analysis"
                if entry["semantic_role"] == "detailed_analysis"
                else entry["topic_title"]
            )
        ),
        "concept_title": entry["wire_title"],
        "concept_details": _draft_details(entry),
        "keywords": ", ".join(entry["facets"]),
        "_semantic_topic_id": entry["topic_id"],
        PLAN_IDENTITY_FIELD: {
            "version": MATERIALIZATION_VERSION,
            "plan_topic_id": entry["plan_topic_id"],
            "plan_concept_id": entry["plan_concept_id"],
            "source_block_ids": list(entry["source_block_ids"]),
            "facets": list(entry["facets"]),
            "rationale": entry["rationale"],
        },
        SEMANTIC_ROLE_FIELD: entry["semantic_role"],
        PLANNED_QIDS_FIELD: list(entry["task_qids"]),
    }


def materialize_envelope(env: Mapping[str, Any]) -> dict[str, Any]:
    """Replace a generic skeleton with the plan's concepts and reseal it."""
    from .phase3 import envelope as envelope_mod

    original = envelope_mod.validate(env)
    entries = plan_entries(original)
    if not entries:
        return original
    marker = (original.get("metadata") or {}).get(
        "post_language_plan_materialization"
    )
    if (
        isinstance(marker, Mapping)
        and marker.get("version") == MATERIALIZATION_VERSION
        and len(original.get("skeleton_rows") or []) == len(entries)
    ):
        return original

    out = copy.deepcopy(original)
    prior_count = len(out.get("skeleton_rows") or [])
    out["skeleton_rows"] = [_skeleton_row(entry) for entry in entries]
    metadata = copy.deepcopy(dict(out.get("metadata") or {}))
    metadata["post_language_plan_materialization"] = {
        "version": MATERIALIZATION_VERSION,
        "prior_skeleton_row_count": prior_count,
        "plan_topic_count": len({entry["plan_topic_id"] for entry in entries}),
        "plan_concept_count": len(entries),
    }
    out["metadata"] = metadata
    out["envelope_sha256"] = envelope_mod.seal_sha256(out)
    return envelope_mod.validate(out)


def _origins(row: Mapping[str, Any]) -> set[str]:
    origins = {
        str(row.get("_phase32_origin_concept_id") or "").strip()
    }
    origins.update(
        str(value).strip()
        for value in row.get("_phase32_origin_concept_ids") or []
    )
    return {value for value in origins if value}


def _append_mastery(details: object, mastery: str) -> str:
    text = str(details or "").strip()
    if re.search(r"\bAchieving\s+Mastery\s*:", text, re.IGNORECASE):
        return text
    return text + "\nAchieving Mastery: " + mastery


def _stamp_row(row: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    stamped = copy.deepcopy(dict(row))
    stamped["topic"] = entry["topic_title"]
    stamped["_semantic_topic_id"] = entry["topic_id"]
    stamped["concept_title"] = entry["wire_title"]
    stamped["parent_concept"] = (
        "Culmination"
        if entry["culmination"]
        else stamped.get("parent_concept") or entry["topic_title"]
    )
    stamped["concept_details"] = _append_mastery(
        stamped.get("concept_details"), entry["achieving_mastery"]
    )
    if entry["source_block_ids"]:
        stamped["_source_block_ids"] = list(entry["source_block_ids"])
    stamped[PLAN_IDENTITY_FIELD] = {
        "version": MATERIALIZATION_VERSION,
        "plan_topic_id": entry["plan_topic_id"],
        "plan_concept_id": entry["plan_concept_id"],
        "source_block_ids": list(entry["source_block_ids"]),
        "facets": list(entry["facets"]),
        "rationale": entry["rationale"],
    }
    stamped[SEMANTIC_ROLE_FIELD] = entry["semantic_role"]
    stamped[PLANNED_QIDS_FIELD] = list(entry["task_qids"])
    return stamped


def _clean_alignment(
    entries: Sequence[Mapping[str, Any]],
    settled_rows: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]] | None:
    """Return the exact one-plan-id/one-row alignment, or ``None``."""
    available = list(settled_rows)
    used: set[int] = set()
    aligned: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for entry in entries:
        if entry["culmination"]:
            matches = [
                (index, row)
                for index, row in enumerate(available)
                if index not in used
                and str(row.get("_semantic_topic_id") or "") == entry["topic_id"]
                and _normal_key(row.get("concept_title"))
                == _normal_key(entry["wire_title"])
            ]
        else:
            expected_origin = str(entry["origin_concept_id"])
            matches = [
                (index, row)
                for index, row in enumerate(available)
                if index not in used and expected_origin in _origins(row)
            ]
        if len(matches) != 1:
            return None
        index, row = matches[0]
        # A row carrying two planned origins is a merge of plan identities and
        # therefore needs the semantic conformance decision below.
        if not entry["culmination"] and len(_origins(row)) != 1:
            return None
        if str(row.get("_semantic_topic_id") or "") != entry["topic_id"]:
            return None
        used.add(index)
        aligned.append((entry, row))
    if len(used) != len(available):
        return None
    return aligned


def _strip_field_label(value: object) -> str:
    return _FIELD_LABEL_RE.sub("", _normal(value), count=1)


def _conformance_checker(
    entries: Sequence[Mapping[str, Any]], row_ids: Sequence[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected_ids = [str(entry["plan_concept_id"]) for entry in entries]
    expected_rows = set(row_ids)
    title_by_id = {
        str(entry["plan_concept_id"]): str(entry["wire_title"])
        for entry in entries
    }

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        concepts = response.get("concepts")
        if not isinstance(concepts, list):
            return ["response has no concepts array"]
        if len(concepts) != len(entries):
            defects.append(
                f"response has {len(concepts)} concepts; expected {len(entries)}"
            )
        seen_ids: set[str] = set()
        seen_rows: set[str] = set()
        for position, raw in enumerate(concepts):
            if not isinstance(raw, Mapping):
                defects.append("a conformed concept is not an object")
                continue
            concept_id = str(raw.get("plan_concept_id") or "")
            expected = expected_ids[position] if position < len(expected_ids) else ""
            if concept_id != expected or concept_id in seen_ids:
                defects.append(
                    f"concept at position {position + 1} carries "
                    f"{concept_id or '<empty>'!r}; expected {expected!r}"
                )
            seen_ids.add(concept_id)
            if _normal(raw.get("concept_title")) != title_by_id.get(concept_id, ""):
                defects.append(
                    f"{concept_id or expected} changed its authoritative wire title"
                )
            for field in (
                "parent_concept", "description", "achieving_mastery", "rationale"
            ):
                if not _normal(raw.get(field)):
                    defects.append(f"{concept_id or expected} has empty {field}")
            refs = _list_strings(raw.get("source_row_ids"))
            for ref in refs:
                if ref not in expected_rows or ref in seen_rows:
                    defects.append(
                        f"{concept_id or expected} names unknown or repeated "
                        f"settled row {ref}"
                    )
                else:
                    seen_rows.add(ref)
        if seen_ids != set(expected_ids):
            defects.append("not every plan_concept_id was returned exactly once")
        missing_rows = sorted(expected_rows - seen_rows)
        if missing_rows:
            defects.append(
                "unaccounted settled row(s): " + ", ".join(missing_rows)
            )
        return defects

    return check


def _live_conformance(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        CONFORMANCE_SYSTEM,
        prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_conformance_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        CONFORMANCE_CRITIC_SYSTEM,
        prompts.render(payload),
        purpose="concept_mapping",
    )


def _source_blocks_payload(
    env: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    requested = {
        value
        for entry in entries
        for value in entry.get("source_block_ids") or []
    }
    requested.update(
        str(value)
        for row in rows
        for value in row.get("_source_block_ids") or []
        if str(value)
    )
    text_by_id = {
        str(row.get("block_id") or ""): str(
            row.get("display_text") or row.get("raw_text") or ""
        )
        for row in (env.get("canonical") or {}).get("blocks") or []
        if isinstance(row, Mapping)
    }
    return [
        {
            "block_id": str(row.get("block_id") or ""),
            "topic_id": str(row.get("topic_id") or ""),
            "kind": str(row.get("kind") or ""),
            "text": text_by_id.get(str(row.get("block_id") or ""), "")[:1800],
        }
        for row in (env.get("graph") or {}).get("blocks") or []
        if isinstance(row, Mapping)
        and str(row.get("block_id") or "") in requested
    ]


def _apply_conformance(
    entries: Sequence[Mapping[str, Any]],
    settled_rows: Sequence[Mapping[str, Any]],
    response: Mapping[str, Any],
    decision_flags: Sequence[str],
) -> list[dict[str, Any]]:
    row_by_id = {
        f"SETTLED-{index:04d}": copy.deepcopy(dict(row))
        for index, row in enumerate(settled_rows, start=1)
    }
    response_by_id = {
        str(row.get("plan_concept_id") or ""): row
        for row in response.get("concepts") or []
        if isinstance(row, Mapping)
    }
    plan_ids = [str(entry["plan_concept_id"]) for entry in entries]
    output: list[dict[str, Any]] = []
    topic_output: dict[str, list[dict[str, Any]]] = {}

    from .phase3 import kernel

    for entry in entries:
        concept_id = str(entry["plan_concept_id"])
        authored = response_by_id[concept_id]
        refs = _list_strings(authored.get("source_row_ids"))
        sources = [row_by_id[ref] for ref in refs]
        base = copy.deepcopy(sources[0]) if sources else _skeleton_row(entry)
        source_blocks = list(entry["source_block_ids"])
        if not source_blocks:
            source_blocks = list(dict.fromkeys(
                str(value)
                for row in sources
                for value in row.get("_source_block_ids") or []
                if str(value)
            ))
        if entry["culmination"] and not source_blocks:
            source_blocks = list(dict.fromkeys(
                str(value)
                for prior in topic_output.get(str(entry["topic_id"]), [])
                for value in prior.get("_source_block_ids") or []
                if str(value)
            ))

        base["topic"] = entry["topic_title"]
        base["_semantic_topic_id"] = entry["topic_id"]
        base["concept_title"] = entry["wire_title"]
        base["parent_concept"] = _normal(authored.get("parent_concept"))
        base["concept_details"] = (
            "Description: " + _strip_field_label(authored.get("description"))
            + "\nAchieving Mastery: "
            + _strip_field_label(authored.get("achieving_mastery"))
        )
        base["keywords"] = _normal(authored.get("keywords")) or ", ".join(
            entry["facets"]
        )
        base["_source_block_ids"] = source_blocks
        base["_source_grounding_contract"] = (
            "derived-from-verified-topic-concepts"
            if entry["culmination"]
            else "api-verified-source-block-ids"
        )
        origins = list(dict.fromkeys(
            value for row in sources for value in _origins(row)
        ))
        if origins:
            base["_phase32_origin_concept_id"] = origins[0]
            base["_phase32_origin_concept_ids"] = origins
        inherited_flags = list(dict.fromkeys(
            str(flag)
            for row in sources
            for flag in row.get("review_flags") or []
            if str(flag)
        ))
        pinned = kernel.pin_flags(list(decision_flags), plan_ids, concept_id)
        if pinned:
            inherited_flags.extend(flag for flag in pinned if flag not in inherited_flags)
        if inherited_flags:
            base["review_flags"] = inherited_flags
        elif "review_flags" in base:
            base.pop("review_flags", None)
        base = _stamp_row(base, entry)
        output.append(base)
        topic_output.setdefault(str(entry["topic_id"]), []).append(base)
    return output


def conform_rows(
    env: Mapping[str, Any],
    settled_rows: Sequence[Mapping[str, Any]],
    *,
    provider: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    critic: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    store: Any = None,
    fixer: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    allow_live: bool = True,
) -> list[dict[str, Any]]:
    """Restore plan identities; spend one decision only when rows drifted."""
    entries = plan_entries(env)
    if not entries:
        return [copy.deepcopy(dict(row)) for row in settled_rows]
    alignment = _clean_alignment(entries, settled_rows)
    if alignment is not None:
        return [_stamp_row(row, entry) for entry, row in alignment]

    # Injected legacy/unit providers often exercise Settle in isolation and do
    # not provide the Post conformance seam. Preserve their compatibility while
    # production remains fail-closed/model-backed.
    if provider is None and not allow_live:
        return [copy.deepcopy(dict(row)) for row in settled_rows]

    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod
    from .phase3 import kernel

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_conformance
        critic = critic if critic is not None else _live_conformance_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    row_ids = [
        f"SETTLED-{index:04d}"
        for index in range(1, len(settled_rows) + 1)
    ]
    metadata = dict(env.get("metadata") or {})
    payload = {
        "stage": "postlearning.language_plan_conformance",
        "rules": CONFORMANCE_SYSTEM,
        "chapter": {
            key: metadata.get(key)
            for key in (
                "board", "grade", "subject", "unit", "chapter_title",
                "chapter_display_name",
            )
        },
        "plan_concepts": [
            {
                key: copy.deepcopy(entry[key])
                for key in (
                    "plan_topic_id", "plan_concept_id", "topic_id",
                    "topic_title", "wire_title", "semantic_role", "facets",
                    "source_block_ids", "task_qids", "achieving_mastery",
                    "rationale",
                )
            }
            for entry in entries
        ],
        "settled_rows": [
            {
                "settled_row_id": row_id,
                "topic_id": str(row.get("_semantic_topic_id") or ""),
                "topic": str(row.get("topic") or ""),
                "concept_title": str(row.get("concept_title") or ""),
                "parent_concept": str(row.get("parent_concept") or ""),
                "concept_details": str(row.get("concept_details") or ""),
                "source_block_ids": _list_strings(row.get("_source_block_ids")),
                "origin_concept_ids": sorted(_origins(row)),
            }
            for row_id, row in zip(row_ids, settled_rows)
        ],
        "source_blocks": _source_blocks_payload(env, entries, settled_rows),
    }
    decision = kernel.decide(
        kind="postlearning.language_plan_conformance",
        unit_id="chapter",
        envelope_sha256=str(env.get("envelope_sha256") or ""),
        payload=payload,
        provider=provider,
        checker=_conformance_checker(entries, row_ids),
        critic=critic,
        store=store,
        policy_version=CONFORMANCE_POLICY_VERSION,
        fixer=fixer,
    )
    return _apply_conformance(
        entries,
        settled_rows,
        decision.get("response") or {},
        decision.get("review_flags") or [],
    )


def _register_release_audit_fields() -> None:
    """Keep plan transport visible in review but out of concept DB rows."""
    try:
        release = importlib.import_module("app.services.build_concepts_release")
    except Exception:
        return
    current = set(getattr(release, "_RELEASE_AUDIT_FIELDS", frozenset()))
    release._RELEASE_AUDIT_FIELDS = frozenset(current | set(POST_LANGUAGE_AUDIT_FIELDS))


def install() -> None:
    """Install the plan projection and post-Settle role transport once."""
    runner = importlib.import_module("app.services.phase3.runner")
    settle = importlib.import_module("app.services.phase3.settle")

    current_run = runner.run
    if not getattr(current_run, "_aegis_postlearning_plan_runner", False):
        @wraps(current_run)
        def run_with_plan(env, *args, **kwargs):
            return current_run(materialize_envelope(env), *args, **kwargs)

        run_with_plan._aegis_postlearning_plan_runner = True
        run_with_plan._aegis_postlearning_original = current_run
        runner.run = run_with_plan

    current_settle = settle.settle
    if not getattr(current_settle, "_aegis_postlearning_plan_settle", False):
        @wraps(current_settle)
        def settle_with_plan(env, *args, **kwargs):
            post_provider = kwargs.pop("post_plan_provider", None)
            post_critic = kwargs.pop("post_plan_critic", None)
            explicit_topology = kwargs.get("topology_provider") is not None
            store = kwargs.get("store")
            fixer = kwargs.get("fixer")
            rows = current_settle(env, *args, **kwargs)
            return conform_rows(
                env,
                rows,
                provider=post_provider,
                critic=post_critic,
                store=store,
                fixer=fixer,
                allow_live=(not explicit_topology or post_provider is not None),
            )

        settle_with_plan._aegis_postlearning_plan_settle = True
        settle_with_plan._aegis_postlearning_original = current_settle
        settle.settle = settle_with_plan

    _register_release_audit_fields()
