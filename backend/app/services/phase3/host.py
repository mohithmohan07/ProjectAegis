"""Pass 2 — Host: one certified concept host per Type/Case unit and QID.

Every mined Type case becomes one assignment unit; the model chooses an
existing settled concept for it (or, rarely, defines a new source-grounded
concept when no settled row can host a distinct durable idea). The unit's
example QIDs inherit its host. One decision per unit, through the kernel:
bounded mechanical corrections, advisory critic, dissent as flags.

Units are certified in parallel batches over one concept payload, so a batch
never sees another batch's create_new. Under generation-quality v3 every unit
that created a concept is re-decided once more, sequentially, with every
batch's first-pass creation visible (``_resolve_blind_creations``); the first
pass is untouched, so earlier runs replay it byte for byte.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from .evidence import block_context, block_text, decide_with_visual_evidence, image_inputs
from ... import config
from .. import generation_quality_policy as quality
from .. import progress
from .. import semantic_confidence_policy as confidence_policy

_BATCH_SIZE = 8

#: Identity of the v3 second Host pass. Its decisions are keyed apart from
#: the first pass (unit_id ``units#resolve#<start>`` and this suffix on the
#: policy), so a rule change here re-keys only the resolution, never the
#: recorded first-pass batches.
RESOLUTION_POLICY_VERSION = "host-blind-batch-resolution-2026-09-13-v1"


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def derive_units(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every mined Type case is one assignment unit; caseless Types get one."""

    units: list[dict[str, Any]] = []
    for mined in env["mined_types"].get("types") or []:
        if not isinstance(mined, Mapping):
            continue
        type_id = str(mined.get("type_id") or "")
        cases = [
            row for row in mined.get("case_prompts") or []
            if isinstance(row, Mapping)
        ]
        if not cases:
            units.append({
                "unit_id": type_id,
                "type_id": type_id,
                "case_id": "",
                "topic_id": str(
                    mined.get("owner_topic_id")
                    or mined.get("source_location_topic_id")
                    or ""
                ),
                "task": _normal(mined.get("type_title")),
                "pattern": _normal(mined.get("type_description")),
                "concept_match_hint": _normal(
                    mined.get("concept_match_hint")
                ),
                "qids": [
                    str(value)
                    for value in mined.get("source_question_ids") or []
                    if str(value)
                ],
                **_mining_hints(mined),
            })
            continue
        for ordinal, case in enumerate(cases, start=1):
            case_id = str(case.get("case_id") or f"CASE-{ordinal:04d}")
            qids: list[str] = []
            for example in case.get("examples") or []:
                if not isinstance(example, Mapping):
                    continue
                qid = str(example.get("source_question_id") or "")
                if qid and qid not in qids:
                    qids.append(qid)
            units.append({
                "unit_id": f"{type_id}::{case_id}::{ordinal:04d}",
                "type_id": type_id,
                "case_id": case_id,
                "topic_id": str(
                    case.get("_semantic_topic_id")
                    or mined.get("owner_topic_id")
                    or ""
                ),
                "task": _normal(case.get("case_title")),
                "pattern": _normal(case.get("case_signature")),
                "concept_match_hint": _normal(
                    case.get("concept_match_hint")
                ),
                "qids": qids,
                **_mining_hints(case),
            })
    return units


#: The miner's per-Case judgments the mining prompt requires it to record.
#: They were dropped on the way to Host, so its payload carried no difficulty
#: or "latest topic" evidence and the printed-position rule pulled advanced
#: in-text tasks onto the early concept they sit beside (the owner's
#: Triangles report, 13 September 2026). Forwarding them is transport; the
#: placement stays the Host model's verdict.
_MINING_HINT_FIELDS = (
    "difficulty_hint", "placement_scope", "topic_match_hint",
    "cognitive_skill_hint",
)


def _mining_hints(row: Mapping[str, Any]) -> dict[str, str]:
    return {field: _normal(row.get(field)) for field in _MINING_HINT_FIELDS}


#: The one additive sentence of the resolution pass. It is spliced directly
#: after the first-pass rule it extends ("create_new only when no existing
#: row can host … complete source-grounded concept.") and is empty on the
#: first pass, whose rules string every sealed run recorded byte for byte.
_RESOLUTION_RULE = (
    "RESOLUTION PASS: every unit in this request answered create_new in "
    "its first-pass batch, which decided in parallel with the other "
    "batches and could not see the concepts they created. "
    "settled_concepts now also lists every concept created by any "
    "first-pass batch (rows whose _source_grounding_contract is "
    "api-created-missing-type-host, each naming the batch and unit that "
    "created it), and each unit carries its own first-pass creation as "
    "first_pass_created. Decide the unit again with all of them visible: "
    "when one of those created rows — yours or a sibling batch's — "
    "teaches this unit's durable source idea, decide existing and name "
    "it EXACTLY; when several created rows describe one capability, host "
    "every unit of that capability on the SAME one of them; create_new "
    "again only when no settled row and no created row can host the "
    "idea, never to re-mint a created row under a new wording. "
    "resolved_units lists the verdicts this pass already returned ahead "
    "of this request; stay consistent with them. "
)


def _settled_index(
    settled_rows: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in settled_rows:
        title = _normal(row.get("concept_title")).casefold()
        if title and title not in index:
            index[title] = dict(row)
    return index



def _resolve_host_title(
    title: object,
    settled_titles: Mapping[str, Mapping[str, Any]],
) -> str | None:
    """Resolve a named host to a settled title key, tolerating close matches.

    The model sometimes paraphrases capitalisation or trims a word; a
    UNIQUE near-match (prefix either way, or >=0.90 similarity) resolves
    to the settled title. Ambiguous or distant names stay unresolved.
    """
    import difflib

    wanted = _normal(title).casefold()
    if not wanted:
        return None
    if wanted in settled_titles:
        return wanted
    candidates = [
        key for key in settled_titles
        if key.startswith(wanted) or wanted.startswith(key)
        or difflib.SequenceMatcher(None, wanted, key).ratio() >= 0.90
    ]
    return candidates[0] if len(candidates) == 1 else None


def _route_question(
    rows: list[Mapping[str, Any]],
    *,
    topic_order: Mapping[str, int],
    culmination_by_topic: Mapping[str, Mapping[str, Any]],
) -> tuple[Mapping[str, Any], str]:
    """Deterministic question destination under the house routing rule.

    One concept -> that concept. Several concepts, one topic -> that
    topic's culmination. Two concepts across two topics -> the involved
    concept in the LATER topic (teaching order). Three or more concepts
    across topics -> the later topic's culmination. The question itself
    is never split; sub-questions travel with it.
    """

    unique: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("_semantic_topic_id") or ""),
            _normal(row.get("concept_title")).casefold(),
        )
        unique.setdefault(key, row)
    concepts = list(unique.values())
    if len(concepts) == 1:
        return concepts[0], "single_concept"
    topics = {
        str(row.get("_semantic_topic_id") or "") for row in concepts
    }
    later = max(topics, key=lambda tid: (topic_order.get(tid, -1), tid))
    in_later = [
        row for row in concepts
        if str(row.get("_semantic_topic_id") or "") == later
    ]
    if len(topics) == 1:
        culmination = culmination_by_topic.get(later)
        if culmination is not None:
            return culmination, "same_topic_culmination"
        return in_later[-1], "same_topic_culmination_missing"
    if len(concepts) == 2:
        return in_later[0], "later_topic_concept"
    culmination = culmination_by_topic.get(later)
    if culmination is not None:
        return culmination, "later_topic_culmination"
    return in_later[-1], "later_topic_culmination_missing"


def _host_checker(
    units: list[dict[str, Any]],
    *,
    settled_titles: Mapping[str, Mapping[str, Any]],
    known_block_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = {row["unit_id"] for row in units}
    qids_by_unit = {row["unit_id"]: list(row.get("qids") or []) for row in units}

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("assignments")
        if not isinstance(rows, list):
            return ["response has no assignments array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("an assignment entry is not an object")
                continue
            unit_id = str(row.get("unit_id") or "")
            if unit_id not in expected or unit_id in seen:
                defects.append(
                    f"unknown or repeated unit_id {unit_id or '<empty>'}"
                )
                continue
            seen.add(unit_id)
            decision = str(row.get("decision") or "").strip().lower()
            try:
                confidence = float(row.get("confidence"))
            except (TypeError, ValueError):
                # A missing or non-numeric score is a SHAPE defect (the
                # bounded corrections ask for the number), never an honest
                # sub-floor score that ships flagged on the first attempt.
                defects.append(
                    f"{unit_id} host confidence must be a number between "
                    "0 and 1"
                )
                confidence = None
            if confidence is not None and not confidence_policy.accepts(
                confidence
            ):
                defects.append(
                    f"[confidence] {unit_id} host confidence "
                    f"{confidence:.3f} is below "
                    f"{confidence_policy.threshold_text()}"
                )
            if decision == "existing":
                if _resolve_host_title(
                    row.get("host_concept_title"), settled_titles
                ) is None:
                    defects.append(
                        f"{unit_id} names host {str(row.get('host_concept_title'))[:60]!r} "
                        "which is not a settled concept title; name an "
                        "exact existing title or decide create_new"
                    )
            elif decision == "create_new":
                new = row.get("new_concept")
                if not isinstance(new, Mapping):
                    defects.append(f"{unit_id} create_new has no new_concept")
                    continue
                for field in (
                    "concept_title",
                    "parent_concept",
                    "concept_details",
                ):
                    if not _normal(new.get(field)):
                        defects.append(
                            f"{unit_id} new_concept is missing {field}"
                        )
                if _normal(new.get("concept_title")).casefold() in (
                    settled_titles
                ):
                    defects.append(
                        f"{unit_id} create_new duplicates an existing "
                        "settled concept title; decide existing instead"
                    )
                blocks = [
                    str(value)
                    for value in new.get("source_block_ids") or []
                    if str(value)
                ]
                if not blocks:
                    defects.append(
                        f"{unit_id} new_concept has no source_block_ids"
                    )
                unknown = [b for b in blocks if b not in known_block_ids]
                if unknown:
                    hint = ""
                    if any(b.upper().startswith("QINV") for b in unknown):
                        hint = (
                            " (QINV ids are question ids, never source "
                            "blocks; cite BLK ids from the request's "
                            "source_blocks)"
                        )
                    defects.append(
                        f"{unit_id} new_concept cites unknown block(s): "
                        + ", ".join(unknown[:4])
                        + hint
                    )
            else:
                defects.append(
                    f"{unit_id} decision {decision!r} is not one of "
                    "existing/create_new"
                )
            expected_qids = qids_by_unit.get(unit_id) or []
            placements = row.get("qid_placements")
            if expected_qids:
                if not isinstance(placements, Mapping):
                    defects.append(
                        f"{unit_id} has no qid_placements object placing "
                        "each question under its concept"
                    )
                else:
                    new_title = ""
                    if decision == "create_new" and isinstance(
                        row.get("new_concept"), Mapping
                    ):
                        new_title = _normal(
                            row["new_concept"].get("concept_title")
                        ).casefold()

                    def _resolves(title: object) -> bool:
                        if _resolve_host_title(
                            title, settled_titles
                        ) is not None:
                            return True
                        return bool(
                            new_title
                            and _normal(title).casefold() == new_title
                        )

                    for qid in expected_qids:
                        placement = placements.get(qid)
                        if not isinstance(placement, Mapping):
                            defects.append(
                                f"{unit_id} qid_placements is missing {qid}"
                            )
                            continue
                        falls_under = placement.get("falls_under")
                        if not isinstance(falls_under, list) or not falls_under:
                            defects.append(
                                f"{unit_id} placement for {qid} must list "
                                "at least one concept in falls_under"
                            )
                        else:
                            for title in falls_under:
                                if not _resolves(title):
                                    defects.append(
                                        f"{unit_id} falls_under names "
                                        f"{str(title)[:60]!r} for {qid} "
                                        "which is not a settled concept "
                                        "title"
                                    )
                        destination = placement.get(
                            "destination_concept_title"
                        )
                        if not _resolves(destination):
                            defects.append(
                                f"{unit_id} destination "
                                f"{str(destination)[:60]!r} for {qid} is "
                                "not a settled concept title; place the "
                                "question under an exact settled concept "
                                "(a Culmination row when the rules call "
                                "for it)"
                            )
                    unknown_qids = sorted(
                        set(placements) - set(expected_qids)
                    )
                    if unknown_qids:
                        defects.append(
                            f"{unit_id} qid_placements names unknown "
                            "qid(s): " + ", ".join(unknown_qids[:4])
                        )
        missing = sorted(expected - seen)
        if missing:
            defects.append("undecided unit(s): " + ", ".join(missing))
        return defects

    return check


def _live_host(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.host_system(payload), prompts.render(payload), purpose="concept_mapping", image_urls=image_inputs(payload)
    )


def _live_type_owner(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.TYPE_OWNER_SYSTEM, prompts.render(payload),
        purpose="concept_mapping", image_urls=image_inputs(payload),
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.CRITIC_SYSTEM, prompts.render(payload),
        purpose="advisory_critic", image_urls=image_inputs(payload),
    )


def _identity(row: Mapping[str, Any]) -> tuple[str, str]:
    """The exact host identity Assemble, coherence and the plan contract key on."""
    return (
        str(row.get("topic_id") or row.get("_semantic_topic_id") or ""),
        _normal(row.get("concept_title")).casefold(),
    )


def _created_projection(
    row: Mapping[str, Any], *, batch_id: str, unit_id: str,
) -> dict[str, Any]:
    """How a Host-created row appears in a resolution request."""
    return {
        "concept_title": row.get("concept_title"),
        "parent_concept": row.get("parent_concept"),
        "topic": row.get("topic"),
        "_semantic_topic_id": row.get("_semantic_topic_id"),
        "concept_details": row.get("concept_details"),
        "_source_grounding_contract": row.get("_source_grounding_contract"),
        "created_by_host_batch": batch_id,
        "created_for_unit": unit_id,
    }


def _resolve_blind_creations(
    *,
    units: list[dict[str, Any]],
    host_map: dict[str, dict[str, Any]],
    qid_map: dict[str, dict[str, Any]],
    new_concepts: list[dict[str, Any]],
    created_by: Mapping[str, tuple[str, dict[str, Any]]],
    settled_rows: list[Mapping[str, Any]],
    concepts_payload: list[dict[str, Any]],
    decide: Callable[..., tuple[dict, dict, list, dict]],
) -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[str, Any]],
    list[dict[str, Any]], int, list[dict[str, Any]],
]:
    """Re-decide every creating unit once, with every batch's creation visible.

    Mechanics only: which units are re-decided (those whose first-pass
    verdict was create_new — no other verdict can reference a creation),
    the sequential order, the projection of the created rows into the
    request, the exact-identity index the checker resolves against, and
    the accounting of which created rows a final verdict still hosts on.
    Which row hosts a unit is the model's verdict, critic-advised and
    Fixer-backed exactly as on the first pass.
    """
    creating = [
        unit for unit in units if str(unit["unit_id"]) in created_by
    ]
    first_pass: dict[str, dict[str, Any]] = {}
    visible_rows: list[Mapping[str, Any]] = list(settled_rows)
    projected: list[dict[str, Any]] = []
    for unit in creating:
        unit_id = str(unit["unit_id"])
        batch_id, row = created_by[unit_id]
        first_pass[unit_id] = _created_projection(
            row, batch_id=batch_id, unit_id=unit_id,
        )
        visible_rows.append(row)
        projected.append(first_pass[unit_id])
    # Identity accounting only: the Host protocol names a host by TITLE and
    # ``_settled_index`` is first-wins by casefold title, so two first-pass
    # creations sharing a title in DIFFERENT topics cannot both be named on
    # the resolution pass — an ``existing`` answer lands on the first topic's
    # row. Say so on the affected units, visibly; never decide which is meant.
    topics_by_title: dict[str, set[str]] = {}
    for _, row in created_by.values():
        topics_by_title.setdefault(
            _normal(row.get("concept_title")).casefold(), set()
        ).add(str(row.get("_semantic_topic_id") or ""))
    clashing_titles = {
        title for title, topics in topics_by_title.items() if len(topics) > 1
    }
    progress.log(
        f"Host: {len(creating)} unit(s) created a concept in parallel "
        "batches that could not see one another; re-deciding each once "
        f"with every first-pass creation visible ({len(projected)} "
        "created row(s))."
    )
    resolved_units: list[dict[str, Any]] = []
    resolved_hosts: dict[str, dict[str, Any]] = {}
    resolved_qids: dict[str, dict[str, Any]] = {}
    second_pass_rows: list[dict[str, Any]] = []
    for start in range(0, len(creating), _BATCH_SIZE):
        batch = creating[start:start + _BATCH_SIZE]
        batch_id = f"units#resolve#{start}"
        batch_hosts, batch_qids, batch_new, batch_created_by = decide(
            batch,
            unit_id=batch_id,
            concepts=[*concepts_payload, *projected],
            titles=_settled_index(visible_rows),
            resolution={
                "first_pass": {
                    str(unit["unit_id"]): first_pass[str(unit["unit_id"])]
                    for unit in batch
                },
                "resolved_units": list(resolved_units),
            },
        )
        resolved_hosts.update(batch_hosts)
        resolved_qids.update(batch_qids)
        second_pass_rows.extend(batch_new)
        for unit_id, row in batch_created_by.items():
            visible_rows.append(row)
            projected.append(
                _created_projection(row, batch_id=batch_id, unit_id=unit_id)
            )
        resolved_units.extend(
            {
                "unit_id": unit_id,
                "decision": entry.get("decision"),
                "host_concept_title": entry.get("concept_title"),
            }
            for unit_id, entry in batch_hosts.items()
        )
    host_map = {**host_map, **resolved_hosts}
    qid_map = {**qid_map, **resolved_qids}
    referenced = {
        _identity(entry)
        for entry in [*host_map.values(), *qid_map.values()]
    }
    # First-pass rows keep the order they were minted in (batch order, the
    # order new_concepts already had); second-pass rows follow.
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in [*new_concepts, *second_pass_rows]:
        key = _identity(row)
        if key in referenced and key not in seen:
            kept.append(row)
            seen.add(key)
    retired: list[dict[str, Any]] = []
    for unit in creating:
        unit_id = str(unit["unit_id"])
        batch_id, first_row = created_by[unit_id]
        retained = any(row is first_row for row in kept)
        if not retained:
            retired.append(first_row)
        entry = dict(host_map[unit_id])
        entry["host_resolution"] = {
            "policy_version": RESOLUTION_POLICY_VERSION,
            "first_pass_batch": batch_id,
            "first_pass_created": copy.deepcopy(first_row),
            "first_pass_retained": retained,
            "decision": str(entry.get("decision") or ""),
        }
        flag = (
            f"{unit_id}: Host re-decided this unit with every batch's "
            f"creation visible — its first-pass batch ({batch_id}) created "
            f"'{_normal(first_row.get('concept_title'))[:60]}' without "
            "sight of the other batches; final host "
            f"'{_normal(entry.get('concept_title'))[:60]}' "
            f"({entry.get('decision')}); the first-pass row is "
            + ("retained" if retained else "retired into this unit's audit")
        )
        entry["review_flags"] = [*(entry.get("review_flags") or []), flag]
        if _normal(first_row.get("concept_title")).casefold() in clashing_titles:
            entry["review_flags"].append(
                f"{unit_id}: first-pass batches created "
                f"'{_normal(first_row.get('concept_title'))[:60]}' in more "
                "than one topic; the resolution pass names hosts by title, so "
                "this unit's verdict resolved to the first such row "
                f"(topic {entry.get('topic_id')}) — confirm the topic."
            )
        host_map[unit_id] = entry
        for qid in unit["qids"]:
            if qid in qid_map:
                qid_entry = dict(qid_map[qid])
                qid_entry["review_flags"] = [
                    *(qid_entry.get("review_flags") or []), flag,
                ]
                qid_map[qid] = qid_entry
    return host_map, qid_map, kept, len(creating), retired


def host(
    env: Mapping[str, Any],
    settled_rows: list[Mapping[str, Any]],
    *,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Certify one host per unit. Returns host_map, qid_map, new_concepts."""

    from . import fixer as fixer_mod

    env = envelope_mod.validate(env)
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_host
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    policy = confidence_policy.POLICY_VERSION
    from . import prompts as prompts_mod

    # The Architect's run instructions ride the sealed envelope metadata;
    # empty slots append nothing, so payloads stay byte-identical.
    rules_suffix = prompts_mod.instruction_rules_suffix(env)

    settled_titles = _settled_index(settled_rows)
    known_blocks = {
        str(row.get("block_id") or "")
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    }
    topic_title_by_id = {
        str(row.get("topic_id") or ""): str(row.get("title") or "")
        for row in env["graph"]["topics"]
        if isinstance(row, Mapping)
    }
    topic_order = {
        str(row.get("topic_id") or ""): index
        for index, row in enumerate(env["graph"]["topics"])
        if isinstance(row, Mapping)
    }
    from .. import concept_refiner as cr

    culmination_by_topic: dict[str, dict[str, Any]] = {}
    for row in settled_rows:
        if cr.is_culmination(_normal(row.get("concept_title"))):
            culmination_by_topic.setdefault(
                str(row.get("_semantic_topic_id") or ""), dict(row)
            )

    units = derive_units(env)
    progress.log(
        f"Host: certifying one concept host for each of {len(units)} "
        "Type/Case assignment unit(s)."
    )
    host_map: dict[str, dict[str, Any]] = {}
    qid_map: dict[str, dict[str, Any]] = {}
    new_concepts: list[dict[str, Any]] = []

    concepts_payload = [
        {
            "concept_title": row.get("concept_title"),
            "parent_concept": row.get("parent_concept"),
            "topic": row.get("topic"),
            "_semantic_topic_id": row.get("_semantic_topic_id"),
            "concept_details": row.get("concept_details"),
        }
        for row in settled_rows
    ]
    # A create_new decision must cite exact source blocks, so the model
    # sees every block it may cite (job 24 failed closed here because the
    # payload carried none and the model could only fabricate).
    context_by_id = {
        str(block.get("block_id") or ""): block_context(block)
        for block in env["canonical"]["blocks"] if isinstance(block, Mapping)
    }
    text_by_id = {
        str(row.get("block_id") or ""): block_text(row)
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }
    blocks_payload = [
        {
            "block_id": str(row.get("block_id") or ""),
            "topic_id": str(row.get("topic_id") or ""),
            "kind": str(row.get("kind") or ""),
            "text": text_by_id.get(str(row.get("block_id") or ""), ""),
            **context_by_id.get(str(row.get("block_id") or ""), {}),
        }
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    ]
    # Placement needs each question's own evidence, not just its Case's
    # summary: reviewers found visually themed questions clustering into the
    # chapter's first image concept and end-of-chapter exercises drifting to
    # unrelated topics. Give the model the wording, the kind, and where the
    # book prints the question.
    question_info: dict[str, dict[str, Any]] = {}
    for item in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid:
            continue
        from .. import generation

        text = generation._inventory_task_text(item)
        question_info[qid] = {
            "qid": qid,
            "kind": str(item.get("source_kind") or ""),
            "printed_under_topic": str(
                item.get("source_location_topic_title")
                or item.get("topic_hint")
                or ""
            ),
            "chapter_wide": bool(item.get("_chapter_wide_task")),
            "text": text,
        }

    def _decide_units(
        batch: list[dict[str, Any]],
        *,
        unit_id: str,
        concepts: list[dict[str, Any]],
        titles: Mapping[str, Mapping[str, Any]],
        resolution: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, dict], dict[str, dict], list[dict], dict[str, dict]]:
        """Certify one unit batch against ``concepts``/``titles``.

        ``resolution`` is None on the first pass — its payload is byte-
        identical to the one every sealed run recorded — and, on the v3
        second pass, carries the first-pass creations each unit is
        re-decided against (an additive rule, per-unit evidence, and the
        verdicts already resolved ahead of this batch).
        """
        batch_hosts: dict[str, dict[str, Any]] = {}
        batch_qids: dict[str, dict[str, Any]] = {}
        batch_new: list[dict[str, Any]] = []
        batch_created_by: dict[str, dict[str, Any]] = {}
        resolution_rule = "" if resolution is None else _RESOLUTION_RULE
        payload = {
            "stage": "host",
            "rules": (
                "Choose one host concept for every assignment unit. Use an "
                "existing settled concept whenever it teaches the unit's "
                "durable source idea; create_new only when no existing row "
                "can host a distinct durable idea, and then define the "
                "complete source-grounded concept. " + resolution_rule
                + "State your honest "
                "confidence; a low-confidence decision ships flagged for "
                "review. Separately, place EVERY qid in qid_placements "
                "by understanding the whole question against the settled "
                "concepts. Placement rules: (1) a question that falls "
                "under one specific concept alone goes under that same "
                "concept; (2) a question that falls under two or more "
                "concepts within the same topic goes under that topic's "
                "Culmination concept; (3) a question that falls under two "
                "different concepts across different topics goes under "
                "the involved concept belonging to the LATER topic in "
                "topics_in_teaching_order; (4) a question that falls "
                "under more than one concept in a topic and another "
                "concept in another topic goes in the Culmination "
                "concept of the later topic. Placement is MOST-LIKELY, "
                "not sure-shot: when a question is close to what a "
                "concept teaches, place it there — never leave a "
                "question unplaced and never drop one. A case can hold "
                "several questions; place every one of them. Never break "
                "a question apart — sub-questions stay with their "
                "question, exactly as it is. List its genuine concepts "
                "in falls_under and name your placement in "
                "destination_concept_title. Use each question's own "
                "entry in the unit's questions list — its full wording, "
                "kind, and printed_under_topic — as primary evidence: "
                "an in-text or checkpoint question belongs with the "
                "material it is printed inside, so weigh that topic's "
                "concepts first; an end-of-chapter exercise is printed "
                "under the last section, so its printing position means "
                "nothing — place it purely by what it asks. A question "
                "whose difficulty_hint is Advanced, or whose method needs "
                "a concept taught LATER in topics_in_teaching_order, "
                "belongs with that later concept, never with an earlier "
                "concept it is printed beside — a learner reaches it only "
                "after the later teaching. Prefer the most granular "
                "method/application/modeling concept; do not file a task "
                "under a nearby definition, broad formula, or final "
                "concept merely for convenience. A textbook Activity, "
                "experiment or discussion unit goes to the related NORMAL "
                "concept, never to a Culmination. Major concepts assessed "
                "by exercises must receive their own Types; do not park "
                "those Types only on a Culmination. Surface "
                "similarity is NOT ownership: a question about a "
                "picture, caricature, map, or table belongs to the "
                "concept that teaches THAT content, never to another "
                "concept merely because it also involves images. A "
                "definitional or recall question about one quantity, "
                "device, or term belongs to the concept that TEACHES "
                "that quantity — never to a broader neighbouring "
                "concept where the term merely appears. A question that "
                "asks the learner to interpret or comment on a specific "
                "source account, print, or passage belongs to the "
                "concept dedicated to that source when one exists. A "
                "question set in an application or appliance context "
                "belongs to the chapter's application concept when one "
                "exists, even when solving it uses an earlier concept's "
                "formula. A research or find-out-more activity that "
                "extends ONE concept's material belongs to that concept "
                "— a Culmination hosts only questions genuinely "
                "spanning several concepts. Each "
                "question gets EXACTLY ONE destination; when the rules "
                "leave two candidates, the later topic in teaching "
                "order wins." + rules_suffix
            ),
            "topics_in_teaching_order": [
                {
                    "topic_id": str(row.get("topic_id") or ""),
                    "title": str(row.get("title") or ""),
                }
                for row in env["graph"]["topics"]
                if isinstance(row, Mapping)
            ],
            "units": [
                {
                    "unit_id": row["unit_id"],
                    "topic_id": row["topic_id"],
                    "task": row["task"],
                    "pattern": row["pattern"],
                    "concept_match_hint": row["concept_match_hint"],
                    **{
                        field: row.get(field, "")
                        for field in _MINING_HINT_FIELDS
                    },
                    "qids": row["qids"],
                    "questions": [
                        question_info[qid]
                        for qid in row["qids"]
                        if qid in question_info
                    ],
                    # Resolution pass only: what this unit's own batch
                    # minted blind. Absent on the first pass, whose
                    # payload every sealed run already recorded.
                    **({} if resolution is None else {
                        "first_pass_created": copy.deepcopy(
                            resolution["first_pass"][row["unit_id"]]
                        ),
                    }),
                }
                for row in batch
            ],
            "settled_concepts": concepts,
            "source_blocks": blocks_payload,
            **({} if resolution is None else {
                "resolved_units": copy.deepcopy(
                    resolution["resolved_units"]
                ),
            }),
        }
        if quality.semantic_case_ownership(env):
            from . import prompts as prompts_mod
            payload.update(quality.fields(env))
            payload["rules"] = payload["rules"].replace(
                "A textbook Activity, experiment or discussion unit goes to the "
                "related NORMAL concept, never to a Culmination.",
                prompts_mod.SEMANTIC_ACTIVITY_RULE,
            )
        decision = decide_with_visual_evidence(
            kind="host.units",
            unit_id=unit_id,
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_host_checker(
                batch,
                settled_titles=titles,
                known_block_ids=known_blocks,
            ),
            critic=critic,
            store=store,
            policy_version=(
                policy if resolution is None
                else policy + ";" + RESOLUTION_POLICY_VERSION
            ),
            fixer=fixer,
        )
        assigned = {
            str(row.get("unit_id") or ""): row
            for row in decision["response"].get("assignments") or []
            if isinstance(row, Mapping)
        }
        flags = list(decision.get("review_flags") or [])
        # A flag naming one unit or QID lands only on that unit/QID, never
        # on its whole batch (kernel.pin_flags — settle's staging fix).
        batch_flag_ids = [
            str(unit["unit_id"]) for unit in batch
        ] + [str(qid) for unit in batch for qid in unit["qids"]]
        for unit in batch:
            verdict = assigned[unit["unit_id"]]
            if str(verdict.get("decision")) == "create_new":
                new = dict(verdict.get("new_concept") or {})
                topic_id = str(
                    new.get("_semantic_topic_id") or unit["topic_id"]
                )
                created = {
                    "topic": topic_title_by_id.get(topic_id, ""),
                    "parent_concept": _normal(new.get("parent_concept")),
                    "concept_title": _normal(new.get("concept_title")),
                    "concept_details": str(
                        new.get("concept_details") or ""
                    ).strip(),
                    "keywords": _normal(new.get("keywords")),
                    "_semantic_topic_id": topic_id,
                    "_source_block_ids": [
                        str(value)
                        for value in new.get("source_block_ids") or []
                        if str(value)
                    ],
                    "_source_grounding_contract": (
                        "api-created-missing-type-host"
                    ),
                }
                batch_new.append(created)
                batch_created_by[unit["unit_id"]] = created
                entry_source: Mapping[str, Any] = created
            else:
                entry_source = titles[
                    _resolve_host_title(
                        verdict.get("host_concept_title"), titles
                    )
                ]
            try:
                confidence = float(verdict.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            entry = {
                "decision": str(verdict.get("decision")),
                "concept_title": _normal(entry_source.get("concept_title")),
                "parent_concept": _normal(
                    entry_source.get("parent_concept")
                ),
                "topic": _normal(entry_source.get("topic")),
                "topic_id": str(entry_source.get("_semantic_topic_id") or ""),
                "confidence": confidence,
            }
            unit_flags = kernel.pin_flags(
                flags, batch_flag_ids, str(unit["unit_id"])
            )
            if unit_flags:
                entry["review_flags"] = list(unit_flags)
            batch_hosts[unit["unit_id"]] = entry
            # House routing rule, applied BY THE MODEL: placing a question
            # means understanding it against the concepts, so the API names
            # each question's destination itself. The deterministic reading
            # of the rules is computed only as an advisory cross-check —
            # a disagreement ships as a review flag, never a block.
            placements = verdict.get("qid_placements")

            def _resolve_row(title: object) -> Mapping[str, Any] | None:
                key = _resolve_host_title(title, titles)
                if key is not None:
                    return titles[key]
                if (
                    str(verdict.get("decision")) == "create_new"
                    and _normal(title).casefold()
                    == _normal(entry_source.get("concept_title")).casefold()
                ):
                    return entry_source
                return None

            for qid in unit["qids"]:
                placement = (
                    placements.get(qid)
                    if isinstance(placements, Mapping)
                    else None
                )
                placement = placement if isinstance(placement, Mapping) else {}
                destination = _resolve_row(
                    placement.get("destination_concept_title")
                )
                if destination is None:
                    # Replayed decisions from before the routing rule carry
                    # no placement; the unit host is their destination.
                    destination = entry_source
                qid_entry = {
                    "decision": "api_placement",
                    "concept_title": _normal(
                        destination.get("concept_title")
                    ),
                    "parent_concept": _normal(
                        destination.get("parent_concept")
                    ),
                    "topic": _normal(destination.get("topic")),
                    "topic_id": str(
                        destination.get("_semantic_topic_id") or ""
                    ),
                    "confidence": confidence,
                    "falls_under": [
                        _normal(title)
                        for title in placement.get("falls_under") or []
                        if _normal(title)
                    ],
                    "placement_reason": _normal(placement.get("reason")),
                }
                qid_flags = kernel.pin_flags(
                    flags, batch_flag_ids, str(qid)
                )
                membership_rows = [
                    row
                    for row in (
                        _resolve_row(title)
                        for title in placement.get("falls_under") or []
                    )
                    if row is not None
                ]
                if membership_rows:
                    expected, rule = _route_question(
                        membership_rows,
                        topic_order=topic_order,
                        culmination_by_topic=culmination_by_topic,
                    )
                    if _normal(expected.get("concept_title")).casefold() != (
                        _normal(destination.get("concept_title")).casefold()
                    ):
                        qid_flags.append(
                            f"{qid}: the API placed this question under "
                            f"'{_normal(destination.get('concept_title'))[:60]}' "
                            "but a literal reading of the house routing "
                            f"rules ({rule}) would place it under "
                            f"'{_normal(expected.get('concept_title'))[:60]}'"
                        )
                if qid_flags:
                    qid_entry["review_flags"] = qid_flags
                batch_qids[qid] = qid_entry
        return batch_hosts, batch_qids, batch_new, batch_created_by

    def _decide_units_batch(
        start: int,
    ) -> tuple[dict[str, dict], dict[str, dict], list[dict], dict[str, dict]]:
        """Certify one first-pass unit batch; outputs merge in batch order."""
        return _decide_units(
            units[start:start + _BATCH_SIZE],
            unit_id=f"units#{start}",
            concepts=concepts_payload,
            titles=settled_titles,
        )

    # Unit batches are independent decisions (the concept payload is built
    # once and never grows mid-run), so they overlap up to the shared
    # OpenAI concurrency gate; merging in batch order keeps host_map,
    # qid_map, and new_concepts identical to the sequential path.
    # Default is sequential: per-run parallelism is a deployment choice
    # sized against concurrent creator runs (see phase3_decision_workers).
    workers = config.phase3_decision_workers()
    batch_total = max(1, (len(units) + _BATCH_SIZE - 1) // _BATCH_SIZE)
    batches_done = 0
    # unit_id -> (first-pass batch unit_id, the row that batch created).
    created_by: dict[str, tuple[str, dict[str, Any]]] = {}
    for start, (batch_hosts, batch_qids, batch_new, batch_created_by) in zip(
        range(0, len(units), _BATCH_SIZE),
        kernel.parallel_map_in_order(
            range(0, len(units), _BATCH_SIZE),
            _decide_units_batch,
            max_workers=workers,
        ),
    ):
        host_map.update(batch_hosts)
        qid_map.update(batch_qids)
        new_concepts.extend(batch_new)
        for created_unit, row in batch_created_by.items():
            created_by[created_unit] = (f"units#{start}", row)
        batches_done += 1
        # The Host band is 0.86 → 0.88, the slice runner.py allocates.
        progress.set_progress(
            0.86 + 0.02 * batches_done / batch_total,
            label=(
                "Phase 3 — Host: assignment batch "
                f"{batches_done}/{batch_total} certified"
            ),
        )

    resolved = 0
    retired: list[dict[str, Any]] = []
    if created_by and quality.host_creations_resolved(env):
        # v3 second pass: the first-pass batches decided in parallel over
        # ONE concept payload, so no batch saw another's create_new and
        # three same-meaning concepts were minted for Triangles. Every
        # unit that created a concept is re-decided ONCE, sequentially,
        # with every first-pass creation in settled_concepts; the model
        # chooses the host, and a first-pass row nobody hosts on retires
        # into the unit's audit. Pass-one decisions and payloads are
        # untouched, so their keys — and every pre-v3 run — replay as is.
        host_map, qid_map, new_concepts, resolved, retired = (
            _resolve_blind_creations(
                units=units, host_map=host_map, qid_map=qid_map,
                new_concepts=new_concepts, created_by=created_by,
                settled_rows=settled_rows, concepts_payload=concepts_payload,
                decide=_decide_units,
            )
        )

    flagged = sum(
        1 for entry in host_map.values() if entry.get("review_flags")
    )
    progress.log(
        f"Host: {len(host_map)} unit(s) certified, {len(qid_map)} QID(s) "
        f"mapped, {len(new_concepts)} new concept(s) created"
        + (
            f"; {resolved} creating unit(s) re-decided with every batch's "
            f"creation visible, {len(retired)} blind creation(s) retired "
            "into their unit's audit"
            if resolved else ""
        )
        + (f"; {flagged} unit(s) carrying review flags." if flagged else "."),
        level="success",
    )
    return {
        "host_map": host_map,
        "qid_map": qid_map,
        "new_concepts": new_concepts,
    }


def consolidate_type_ownership(
    env: Mapping[str, Any],
    hosts: dict[str, Any],
    *,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """One concept owns each Type (register Q14, owner ruling 21 Aug 2026).

    The Host pass certifies per-Case hosts; those verdicts stay recorded
    as evidence. When one mined Type's Cases resolved onto DIFFERENT
    concepts, this pass takes one ownership verdict per split Type —
    model-decided over the Cases and the candidate hosts, critic-advised,
    Fixer-backed — and every Case and QID of the Type moves to the owner
    (Type ownership outranks per-question routing for its members). Moves
    are flagged per unit and per QID, never silent. A Type whose Cases
    already share one host costs nothing here, and a concept left with no
    Types by the choice is a legitimate outcome — the verdict's rules
    forbid spreading.
    """

    if quality.semantic_case_ownership(env):
        # Per-question and per-Case model verdicts remain authoritative.
        # Assemble gives each decided concept its own Type identity, rather
        # than moving content to satisfy a shared answering-form identity.
        return {**hosts, "semantic_case_ownership": quality.V5}

    from . import fixer as fixer_mod
    from . import prompts as prompts_mod

    env = envelope_mod.validate(env)
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_type_owner
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    policy = confidence_policy.POLICY_VERSION
    rules_suffix = prompts_mod.instruction_rules_suffix(env)

    host_map: dict[str, Any] = dict(hosts.get("host_map") or {})
    qid_map: dict[str, Any] = dict(hosts.get("qid_map") or {})
    units = derive_units(env)
    units_by_type: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        units_by_type.setdefault(str(unit["type_id"]), []).append(unit)

    types_by_id = {
        str(mined.get("type_id") or ""): mined
        for mined in env["mined_types"].get("types") or []
        if isinstance(mined, Mapping)
    }
    from .. import generation

    question_text_by_qid = {
        str(item.get("qid") or ""): generation._inventory_task_text(item)
        for item in (env.get("inventory") or {}).get("items") or []
        if isinstance(item, Mapping)
    }

    def _destination_key(entry: Mapping[str, Any]) -> tuple[str, str]:
        return (
            str(entry.get("topic_id") or ""),
            _normal(entry.get("concept_title")).casefold(),
        )

    moved_types = 0
    projected_qids = 0
    for type_id, type_units in units_by_type.items():
        hosted = [
            (unit, host_map.get(str(unit["unit_id"])))
            for unit in type_units
        ]
        hosted = [(unit, entry) for unit, entry in hosted if entry]
        destinations: dict[tuple[str, str], Mapping[str, Any]] = {}
        for _, entry in hosted:
            destinations.setdefault(_destination_key(entry), entry)
        if not destinations:
            continue
        decision_flags: list[str] = []
        if len(destinations) == 1:
            # Every Case already certified the same semantic owner.  No
            # additional judgment or provider call is needed, but the QID
            # map can still contain an earlier per-question destination.
            # Q14 says Type ownership outranks that route, so the projection
            # below must run for single-host Types too (job 100 / TYPE-0009).
            owner_entry = next(iter(destinations.values()))
        else:
            mined = types_by_id.get(type_id) or {}
            candidates = list(destinations.values())
            payload = {
                "stage": "host.type_owner",
                "rules": (
                    "One concept owns this Type (Q14). Choose "
                    "owner_concept_title as the EXACT concept_title of one "
                    "candidate below — the concepts its Cases were certified "
                    "onto — judging which concept's teaching the Type as a "
                    "whole most genuinely exercises. Never the first Case's "
                    "host, the most common host, or position arithmetic; a "
                    "candidate left with no Types is a legitimate outcome "
                    "and coverage balance must not influence the choice."
                    + rules_suffix
                ),
                "type": {
                    "type_id": type_id,
                    "type_title": _normal(mined.get("type_title")),
                    "type_description": _normal(
                        mined.get("type_description")
                    ),
                },
                "cases": [
                    {
                        "unit_id": str(unit["unit_id"]),
                        "case_id": str(unit["case_id"]),
                        "task": unit["task"],
                        "qids": list(unit["qids"]),
                        "questions": [
                            {
                                "qid": qid,
                                "question": question_text_by_qid.get(
                                    qid, ""
                                ),
                            }
                            for qid in unit["qids"]
                            if question_text_by_qid.get(qid)
                        ],
                        "certified_host": {
                            "concept_title": _normal(
                                entry.get("concept_title")
                            ),
                            "topic": _normal(entry.get("topic")),
                            "confidence": entry.get("confidence"),
                        },
                    }
                    for unit, entry in hosted
                ],
                "candidate_concepts": [
                    {
                        "concept_title": _normal(
                            entry.get("concept_title")
                        ),
                        "parent_concept": _normal(
                            entry.get("parent_concept")
                        ),
                        "topic": _normal(entry.get("topic")),
                        "topic_id": str(entry.get("topic_id") or ""),
                    }
                    for entry in candidates
                ],
            }
            candidate_titles = {
                _normal(entry.get("concept_title")).casefold(): entry
                for entry in candidates
            }

            def _owner_checker(
                response: Mapping[str, Any],
            ) -> list[str]:
                if not isinstance(response, Mapping):
                    return ["response is not an object"]
                defects: list[str] = []
                if str(response.get("type_id") or "") != type_id:
                    defects.append(f"type_id must echo {type_id!r}")
                owner = _normal(
                    response.get("owner_concept_title")
                ).casefold()
                if owner not in candidate_titles:
                    defects.append(
                        "owner_concept_title must be the exact "
                        "concept_title of one candidate host concept (got "
                        f"{response.get('owner_concept_title')!r})"
                    )
                try:
                    float(response.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    defects.append("confidence must be numeric")
                return defects

            decision = decide_with_visual_evidence(
                kind="host.type_owner",
                unit_id=type_id,
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=provider,
                checker=_owner_checker,
                critic=critic,
                store=store,
                policy_version=policy,
                fixer=fixer,
            )
            response = decision["response"]
            owner_entry = candidate_titles[
                _normal(response.get("owner_concept_title")).casefold()
            ]
            decision_flags = list(decision.get("review_flags") or [])
            moved_types += 1

        owner_title = _normal(owner_entry.get("concept_title"))

        type_qids = {
            qid for unit in type_units for qid in unit["qids"]
        }
        for unit in type_units:
            unit_id = str(unit["unit_id"])
            entry = host_map.get(unit_id)
            if not entry:
                continue
            if _destination_key(entry) != _destination_key(owner_entry):
                moved_from = _normal(entry.get("concept_title"))
                entry = dict(entry)
                entry["concept_title"] = owner_title
                entry["parent_concept"] = _normal(
                    owner_entry.get("parent_concept")
                )
                entry["topic"] = _normal(owner_entry.get("topic"))
                entry["topic_id"] = str(owner_entry.get("topic_id") or "")
                entry.setdefault("review_flags", []).append(
                    f"{unit_id}: Q14 Type ownership moved this Case from "
                    f"'{moved_from[:60]}' to the Type's owning concept "
                    f"'{owner_title[:60]}'"
                )
                host_map[unit_id] = entry
        for qid in sorted(type_qids):
            entry = qid_map.get(qid)
            if not entry:
                continue
            if _destination_key(entry) != _destination_key(owner_entry):
                moved_from = _normal(entry.get("concept_title"))
                entry = dict(entry)
                entry["concept_title"] = owner_title
                entry["parent_concept"] = _normal(
                    owner_entry.get("parent_concept")
                )
                entry["topic"] = _normal(owner_entry.get("topic"))
                entry["topic_id"] = str(owner_entry.get("topic_id") or "")
                entry.setdefault("review_flags", []).append(
                    f"{qid}: Q14 Type ownership moved this question with "
                    f"its Type from '{moved_from[:60]}' to "
                    f"'{owner_title[:60]}'"
                )
                qid_map[qid] = entry
                projected_qids += 1
        if decision_flags:
            # The ownership decision is ABOUT the whole Type, so its
            # flags (critic dissent, Fixer notes) land on every unit of
            # the Type — each unit is one rendering of that Type.
            for unit in type_units:
                unit_id = str(unit["unit_id"])
                entry = host_map.get(unit_id)
                if not entry:
                    continue
                entry = dict(entry)
                existing = entry.setdefault("review_flags", [])
                for flag in decision_flags:
                    if flag not in existing:
                        existing.append(flag)
                host_map[unit_id] = entry

    if moved_types:
        progress.log(
            f"Host: {moved_types} split Type(s) consolidated onto one "
            "owning concept each (Q14); every move is flagged for review.",
            level="success",
        )
    if projected_qids:
        progress.log(
            "Host: Q14 projected the certified Type owner onto "
            f"{projected_qids} per-question destination(s); every move "
            "is flagged for review.",
            level="success",
        )
    return {
        "host_map": host_map,
        "qid_map": qid_map,
        "new_concepts": list(hosts.get("new_concepts") or []),
    }
