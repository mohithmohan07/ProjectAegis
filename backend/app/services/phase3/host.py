"""Pass 2 — Host: one certified concept host per Type/Case unit and QID.

Every mined Type case becomes one assignment unit; the model chooses an
existing settled concept for it (or, rarely, defines a new source-grounded
concept when no settled row can host a distinct durable idea). The unit's
example QIDs inherit its host. One decision per unit, through the kernel:
bounded mechanical corrections, advisory critic, dissent as flags.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from ... import config
from .. import progress
from .. import semantic_confidence_policy as confidence_policy

_BATCH_SIZE = 8


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
            })
    return units


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
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if not confidence_policy.accepts(confidence):
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
        prompts.HOST_SYSTEM, prompts.render(payload), purpose="concept_mapping"
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def host(
    env: Mapping[str, Any],
    settled_rows: list[Mapping[str, Any]],
    *,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
) -> dict[str, Any]:
    """Certify one host per unit. Returns host_map, qid_map, new_concepts."""

    env = envelope_mod.validate(env)
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_host
        critic = critic if critic is not None else _live_critic
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    policy = confidence_policy.threshold_text()

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
    text_by_id = {
        str(row.get("block_id") or ""): str(row.get("display_text") or "")
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }
    blocks_payload = [
        {
            "block_id": str(row.get("block_id") or ""),
            "topic_id": str(row.get("topic_id") or ""),
            "kind": str(row.get("kind") or ""),
            "text": text_by_id.get(str(row.get("block_id") or ""), "")[:400],
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
        text = str(
            item.get("polished_task")
            or item.get("normalized_task")
            or item.get("raw_task")
            or ""
        )
        question_info[qid] = {
            "qid": qid,
            "kind": str(item.get("source_kind") or ""),
            "printed_under_topic": str(
                item.get("source_location_topic_title")
                or item.get("topic_hint")
                or ""
            ),
            "chapter_wide": bool(item.get("_chapter_wide_task")),
            "text": text[:600],
        }

    def _decide_units_batch(
        start: int,
    ) -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
        """Certify one unit batch; outputs merge in batch order."""
        batch_hosts: dict[str, dict[str, Any]] = {}
        batch_qids: dict[str, dict[str, Any]] = {}
        batch_new: list[dict[str, Any]] = []
        batch = units[start:start + _BATCH_SIZE]
        payload = {
            "stage": "host",
            "rules": (
                "Choose one host concept for every assignment unit. Use an "
                "existing settled concept whenever it teaches the unit's "
                "durable source idea; create_new only when no existing row "
                "can host a distinct durable idea, and then define the "
                "complete source-grounded concept. Confidence floor is "
                f"{policy}. Separately, place EVERY qid in qid_placements "
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
                "nothing — place it purely by what it asks. Surface "
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
                "order wins."
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
                    "qids": row["qids"],
                    "questions": [
                        question_info[qid]
                        for qid in row["qids"]
                        if qid in question_info
                    ],
                }
                for row in batch
            ],
            "settled_concepts": concepts_payload,
            "source_blocks": blocks_payload,
        }
        decision = kernel.decide(
            kind="host.units",
            unit_id=f"units#{start}",
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_host_checker(
                batch,
                settled_titles=settled_titles,
                known_block_ids=known_blocks,
            ),
            critic=critic,
            store=store,
            policy_version=policy,
        )
        assigned = {
            str(row.get("unit_id") or ""): row
            for row in decision["response"].get("assignments") or []
            if isinstance(row, Mapping)
        }
        flags = list(decision.get("review_flags") or [])
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
                entry_source: Mapping[str, Any] = created
            else:
                entry_source = settled_titles[
                    _resolve_host_title(
                        verdict.get("host_concept_title"), settled_titles
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
            if flags:
                entry["review_flags"] = list(flags)
            batch_hosts[unit["unit_id"]] = entry
            # House routing rule, applied BY THE MODEL: placing a question
            # means understanding it against the concepts, so the API names
            # each question's destination itself. The deterministic reading
            # of the rules is computed only as an advisory cross-check —
            # a disagreement ships as a review flag, never a block.
            placements = verdict.get("qid_placements")

            def _resolve_row(title: object) -> Mapping[str, Any] | None:
                key = _resolve_host_title(title, settled_titles)
                if key is not None:
                    return settled_titles[key]
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
                qid_flags = list(flags)
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
        return batch_hosts, batch_qids, batch_new

    # Unit batches are independent decisions (the concept payload is built
    # once and never grows mid-run), so they overlap up to the shared
    # OpenAI concurrency gate; merging in batch order keeps host_map,
    # qid_map, and new_concepts identical to the sequential path.
    # Default is sequential: per-run parallelism is a deployment choice
    # sized against concurrent creator runs (see phase3_decision_workers).
    workers = config.phase3_decision_workers()
    batch_total = max(1, (len(units) + _BATCH_SIZE - 1) // _BATCH_SIZE)
    batches_done = 0
    for batch_hosts, batch_qids, batch_new in kernel.parallel_map_in_order(
        range(0, len(units), _BATCH_SIZE),
        _decide_units_batch,
        max_workers=workers,
    ):
        host_map.update(batch_hosts)
        qid_map.update(batch_qids)
        new_concepts.extend(batch_new)
        batches_done += 1
        progress.set_progress(
            0.91 + 0.03 * batches_done / batch_total,
            label=(
                "Phase 3 — Host: assignment batch "
                f"{batches_done}/{batch_total} certified"
            ),
        )

    flagged = sum(
        1 for entry in host_map.values() if entry.get("review_flags")
    )
    progress.log(
        f"Host: {len(host_map)} unit(s) certified, {len(qid_map)} QID(s) "
        f"mapped, {len(new_concepts)} new concept(s) created"
        + (f"; {flagged} unit(s) carrying review flags." if flagged else "."),
        level="success",
    )
    return {
        "host_map": host_map,
        "qid_map": qid_map,
        "new_concepts": new_concepts,
    }
