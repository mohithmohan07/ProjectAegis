"""Pass 1 — Settle: topology, grounding, and learner analysis.

Consumes the sealed envelope, decides every normal concept exactly once
through the kernel, and emits the settled row set — the artifact the old
pipeline knew as the validated final concept topology. Culminations are
derived, never decided.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from .. import concept_refiner as cr
from .. import progress
from .. import semantic_confidence_policy as confidence_policy

_BATCH_SIZE = 12

_DECISIONS = {"keep", "refine", "split"}

_ANALYSIS_SPLIT = re.compile(
    r"\s*//\s*Misconception/?\s*Error Analysis:\s*", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# envelope projections


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _description_of(details: object) -> str:
    match = re.search(
        r"Description:\s*(.*?)(?=\n[A-Z][A-Za-z ]{2,24}:|//|$)",
        str(details or ""),
        re.DOTALL,
    )
    return _normal(match.group(1)) if match else ""


def _strip_analysis(details: str) -> str:
    return _ANALYSIS_SPLIT.split(details, maxsplit=1)[0].rstrip()


def resolve_topic_id(
    row: Mapping[str, Any],
    topics: list[Mapping[str, Any]],
) -> str:
    """Resolve a row's semantic topic id, falling back to its topic title."""

    explicit = str(row.get("_semantic_topic_id") or "")
    if explicit:
        return explicit
    wanted = _normal(row.get("topic")).casefold()
    for topic in topics:
        if _normal(topic.get("title")).casefold() == wanted:
            return str(topic.get("topic_id") or "")
    return ""


def _topic_rows(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in env["graph"]["topics"]
        if isinstance(row, Mapping) and str(row.get("topic_id") or "")
    ]


def _blocks_by_topic(env: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    text_by_id = {
        str(row.get("block_id") or ""): str(row.get("display_text") or "")
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in env["graph"]["blocks"]:
        if not isinstance(row, Mapping):
            continue
        block_id = str(row.get("block_id") or "")
        topic_id = str(row.get("topic_id") or "")
        if not block_id or not topic_id:
            continue
        grouped.setdefault(topic_id, []).append({
            "block_id": block_id,
            "subtopic_id": str(row.get("subtopic_id") or ""),
            "kind": str(row.get("kind") or ""),
            "text": text_by_id.get(block_id, ""),
        })
    return grouped


def _known_block_ids(env: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("block_id") or "")
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    }


def _batched(values: list, size: int = _BATCH_SIZE) -> list[list]:
    return [values[i:i + size] for i in range(0, len(values), size)]


# ---------------------------------------------------------------------------
# stage 1: topology



def _pin_flags(
    flags: list[str],
    batch_ids: list[str],
    row_id: str,
) -> list[str]:
    """Attach concept-specific flags to their concept; general ones to all.

    A critic issue naming a specific concept must land only on that row
    (staging showed whole batches flagged for one concept's dissent).
    """
    pinned = [flag for flag in flags if row_id in flag]
    general = [
        flag for flag in flags
        if not any(candidate in flag for candidate in batch_ids)
    ]
    return pinned + general


def _topology_checker(
    batch: list[dict[str, Any]],
) -> Callable[[Mapping[str, Any]], list[str]]:
    by_id = {row["concept_id"]: row for row in batch}

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("decisions")
        if not isinstance(rows, list):
            return ["response has no decisions array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a decision entry is not an object")
                continue
            concept_id = str(row.get("concept_id") or "")
            if concept_id not in by_id:
                defects.append(f"unknown concept_id {concept_id or '<empty>'}")
                continue
            if concept_id in seen:
                defects.append(f"{concept_id} decided more than once")
                continue
            seen.add(concept_id)
            decision = str(row.get("decision") or "").strip().lower()
            if decision not in _DECISIONS:
                defects.append(
                    f"{concept_id} decision {decision!r} is not one of "
                    "keep/refine/split"
                )
                continue
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if not confidence_policy.accepts(confidence):
                defects.append(
                    f"{concept_id} confidence {confidence:.3f} is below "
                    f"{confidence_policy.threshold_text()}"
                )
            segments = row.get("segments")
            if not isinstance(segments, list) or not segments:
                defects.append(f"{concept_id} has no segments")
                continue
            if decision in {"keep", "refine"} and len(segments) != 1:
                defects.append(
                    f"{concept_id} {decision} must carry exactly one segment"
                )
            if decision == "split" and len(segments) < 2:
                defects.append(
                    f"{concept_id} split must carry at least two segments"
                )
            for segment in segments:
                if not isinstance(segment, Mapping):
                    defects.append(f"{concept_id} segment is not an object")
                    continue
                if not _normal(segment.get("concept_title")):
                    defects.append(f"{concept_id} segment has no title")
                if not _description_of(segment.get("concept_details")):
                    defects.append(
                        f"{concept_id} segment has no Description claim"
                    )
            if decision == "keep" and segments and isinstance(
                segments[0], Mapping
            ):
                original = _description_of(by_id[concept_id]["concept_details"])
                kept = _description_of(segments[0].get("concept_details"))
                if original and kept and original.casefold() != (
                    kept.casefold()
                ):
                    defects.append(
                        f"{concept_id} keep decision rewrote the claim; a "
                        "changed claim must be a refine"
                    )
        missing = sorted(set(by_id) - seen)
        if missing:
            defects.append("undecided concept(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# stage 2: grounding


def _grounding_checker(
    concept_ids: list[str],
    *,
    topic_block_ids: set[str],
    known_block_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = set(concept_ids)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("concepts")
        if not isinstance(rows, list):
            return ["response has no concepts array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a grounding entry is not an object")
                continue
            concept_id = str(row.get("concept_id") or "")
            if concept_id not in expected or concept_id in seen:
                defects.append(
                    f"unknown or repeated concept_id {concept_id or '<empty>'}"
                )
                continue
            seen.add(concept_id)
            block_ids = [
                str(value)
                for value in row.get("source_block_ids") or []
                if str(value)
            ]
            if not block_ids:
                defects.append(f"{concept_id} has no source block")
            wrong_topic = [b for b in block_ids if b not in topic_block_ids]
            if wrong_topic:
                # Rule 4a: print position is provenance. Cross-topic blocks
                # may support a claim only as references, never as grounding.
                defects.append(
                    f"{concept_id} grounded on block(s) outside its topic: "
                    + ", ".join(wrong_topic[:4])
                    + " (cite them in reference_block_ids instead and ground "
                    "on at least one block from the concept's own topic)"
                )
            references = [
                str(value)
                for value in row.get("reference_block_ids") or []
                if str(value)
            ]
            unknown_refs = [b for b in references if b not in known_block_ids]
            if unknown_refs:
                defects.append(
                    f"{concept_id} cites unknown reference block(s): "
                    + ", ".join(unknown_refs[:4])
                )
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if not confidence_policy.accepts(confidence):
                defects.append(
                    f"{concept_id} confidence {confidence:.3f} is below "
                    f"{confidence_policy.threshold_text()}"
                )
        missing = sorted(expected - seen)
        if missing:
            defects.append("ungrounded concept(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# stage 3: learner analysis


def _analysis_checker(
    concept_ids: list[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = set(concept_ids)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("rows")
        if not isinstance(rows, list):
            return ["response has no rows array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("an analysis entry is not an object")
                continue
            concept_id = str(row.get("concept_id") or "")
            if concept_id not in expected or concept_id in seen:
                defects.append(
                    f"unknown or repeated concept_id {concept_id or '<empty>'}"
                )
                continue
            seen.add(concept_id)
            body = str(row.get("misconception_error_analysis") or "")
            lowered = body.casefold()
            if "misconception" not in lowered or "error analysis" not in (
                lowered
            ):
                defects.append(
                    f"{concept_id} analysis must contain both a "
                    "Misconceptions and an Error Analysis part"
                )
                continue
            if "learner" not in lowered and "student" not in lowered:
                defects.append(
                    f"{concept_id} Error Analysis must name the learner or "
                    "student and a concrete faulty action or reasoning step"
                )
        missing = sorted(expected - seen)
        if missing:
            defects.append("unanalysed concept(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# live API adapters (production defaults; tests inject providers)


def _api_json(system: str, user: str) -> dict[str, Any]:
    from .. import generation

    return generation._openai_json(system, user, purpose="concept_mapping")


def _live_topology(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts

    return _api_json(prompts.TOPOLOGY_SYSTEM, prompts.render(payload))


def _live_grounding(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts

    return _api_json(prompts.GROUNDING_SYSTEM, prompts.render(payload))


def _live_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts

    return _api_json(prompts.ANALYSIS_SYSTEM, prompts.render(payload))


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts

    return _api_json(prompts.CRITIC_SYSTEM, prompts.render(payload))


# ---------------------------------------------------------------------------
# the pass


def settle(
    env: Mapping[str, Any],
    *,
    topology_provider: kernel.Provider | None = None,
    grounding_provider: kernel.Provider | None = None,
    analysis_provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
) -> list[dict[str, Any]]:
    """Settle every concept: one decision each, flags attached, no pauses."""

    env = envelope_mod.validate(env)
    explicit = topology_provider is not None
    if not explicit:
        envelope_mod.require_live_api()
        topology_provider = _live_topology
        grounding_provider = grounding_provider or _live_grounding
        analysis_provider = analysis_provider or _live_analysis
        critic = critic or _live_critic
    if grounding_provider is None or analysis_provider is None:
        raise ValueError(
            "settle needs grounding and analysis providers when the "
            "topology provider is injected explicitly"
        )
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    policy = confidence_policy.threshold_text()

    topics = _topic_rows(env)
    blocks_by_topic = _blocks_by_topic(env)
    known_blocks = _known_block_ids(env)

    normal_rows: list[dict[str, Any]] = []
    culmination_rows: list[dict[str, Any]] = []
    for row in env["skeleton_rows"]:
        resolved = copy.deepcopy(dict(row))
        topic_id = resolve_topic_id(resolved, topics)
        if not topic_id:
            raise envelope_mod.EnvelopeError(
                "skeleton row resolves to no graph topic: "
                + _normal(resolved.get("concept_title"))[:80]
            )
        resolved["_semantic_topic_id"] = topic_id
        title = str(
            resolved.get("concept_title") or resolved.get("concept") or ""
        )
        if cr.is_culmination(title):
            culmination_rows.append(resolved)
        else:
            normal_rows.append(resolved)
    for index, row in enumerate(normal_rows, start=1):
        row["concept_id"] = f"TOPOLOGY-CONCEPT-{index:04d}"

    progress.log(
        f"Settle: deciding {len(normal_rows)} concept(s) across "
        f"{len(topics)} topic(s); {len(culmination_rows)} culmination "
        "recap(s) will be derived without model calls.",
    )
    settled: list[dict[str, Any]] = []
    flags_by_row: dict[int, list[str]] = {}

    for topic in topics:
        topic_id = str(topic.get("topic_id") or "")
        topic_title = str(topic.get("title") or topic_id)
        topic_blocks = blocks_by_topic.get(topic_id, [])
        topic_block_ids = {row["block_id"] for row in topic_blocks}
        topic_concepts = [
            row
            for row in normal_rows
            if str(row.get("_semantic_topic_id") or "") == topic_id
        ]
        if not topic_concepts:
            continue

        # -- stage 1: topology -------------------------------------------
        topic_settled: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(_batched(topic_concepts), 1):
            payload = {
                "stage": "topology",
                "rules": (
                    "Decide keep, refine, or split for every concept, under "
                    "the written placement rules. keep = the claim is "
                    "correct and singular (do not rewrite it). refine = the "
                    "claim needs correction against the source. split = the "
                    "claim bundles distinct teachable ideas; emit one "
                    "segment per idea. Confidence must reflect source "
                    f"evidence; the acceptance floor is {policy}."
                ),
                "topic": {"topic_id": topic_id, "title": topic_title},
                "concepts": [
                    {
                        "concept_id": row["concept_id"],
                        "concept_title": row.get("concept_title"),
                        "parent_concept": row.get("parent_concept"),
                        "concept_details": row.get("concept_details"),
                        "keywords": row.get("keywords"),
                    }
                    for row in batch
                ],
                "source_blocks": topic_blocks,
            }
            decision = kernel.decide(
                kind="settle.topology",
                unit_id=f"{topic_id}#batch{batch_index}",
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=topology_provider,
                checker=_topology_checker(batch),
                critic=critic,
                store=store,
                policy_version=policy,
            )
            response_by_id = {
                str(row.get("concept_id") or ""): row
                for row in decision["response"].get("decisions") or []
                if isinstance(row, Mapping)
            }
            for row in batch:
                verdict = response_by_id[row["concept_id"]]
                choice = str(verdict.get("decision") or "").strip().lower()
                for order, segment in enumerate(
                    verdict.get("segments") or [], start=1
                ):
                    settled_row = {
                        "topic": row.get("topic"),
                        "parent_concept": _normal(
                            segment.get("parent_concept")
                            or row.get("parent_concept")
                        ),
                        "concept_title": _normal(
                            segment.get("concept_title")
                        ),
                        "concept_details": _strip_analysis(
                            str(segment.get("concept_details") or "")
                        ),
                        "keywords": _normal(
                            segment.get("keywords") or row.get("keywords")
                        ),
                        "_semantic_topic_id": topic_id,
                        "_phase32_topology_decision": choice,
                        "_phase32_origin_concept_id": row["concept_id"],
                        "_phase32_segment_order": order,
                    }
                    index = len(settled) + len(topic_settled)
                    flags = _pin_flags(
                        list(decision.get("review_flags") or []),
                        [c["concept_id"] for c in batch],
                        row["concept_id"],
                    )
                    if flags:
                        flags_by_row[index] = flags
                    topic_settled.append(settled_row)

        # -- stage 2: grounding ------------------------------------------
        for offset_batch in _batched(list(range(len(topic_settled)))):
            batch_rows = [topic_settled[i] for i in offset_batch]
            concept_ids = [
                f"{row['_phase32_origin_concept_id']}"
                f"#{row['_phase32_segment_order']}"
                for row in batch_rows
            ]
            payload = {
                "stage": "grounding",
                "rules": (
                    "Ground every claim on the minimal exact source blocks "
                    "from the concept's own topic. Print position is "
                    "provenance, never grounding: a block from another "
                    "topic may only appear in reference_block_ids. The "
                    f"acceptance floor is {policy}."
                ),
                "topic": {"topic_id": topic_id, "title": topic_title},
                "concepts": [
                    {
                        "concept_id": concept_id,
                        "source_claim": _description_of(
                            row["concept_details"]
                        ),
                    }
                    for concept_id, row in zip(concept_ids, batch_rows)
                ],
                "source_blocks": topic_blocks,
            }
            decision = kernel.decide(
                kind="settle.grounding",
                unit_id=f"{topic_id}#ground{offset_batch[0]}",
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=grounding_provider,
                checker=_grounding_checker(
                    concept_ids,
                    topic_block_ids=topic_block_ids,
                    known_block_ids=known_blocks,
                ),
                critic=critic,
                store=store,
                policy_version=policy,
            )
            grounded_by_id = {
                str(row.get("concept_id") or ""): row
                for row in decision["response"].get("concepts") or []
                if isinstance(row, Mapping)
            }
            subtopic_by_block = {
                row["block_id"]: row["subtopic_id"] for row in topic_blocks
            }
            for position, (concept_id, row) in zip(
                offset_batch, zip(concept_ids, batch_rows)
            ):
                grounded = grounded_by_id[concept_id]
                block_ids = [
                    str(v) for v in grounded.get("source_block_ids") or []
                ]
                row["_source_block_ids"] = block_ids
                references = [
                    str(v)
                    for v in grounded.get("reference_block_ids") or []
                    if str(v)
                ]
                if references:
                    row["_reference_block_ids"] = references
                row["_semantic_subtopic_ids"] = sorted({
                    subtopic_by_block.get(block_id, "")
                    for block_id in block_ids
                } - {""})
                row["_source_grounding_contract"] = (
                    "api-verified-source-block-ids"
                )
                try:
                    row["_source_grounding_confidence"] = float(
                        grounded.get("confidence") or 0.0
                    )
                except (TypeError, ValueError):
                    row["_source_grounding_confidence"] = 0.0
                flags = _pin_flags(
                    list(decision.get("review_flags") or []),
                    concept_ids,
                    concept_id,
                )
                if flags:
                    flags_by_row.setdefault(
                        len(settled) + position, []
                    ).extend(flags)

        # -- stage 3: learner analysis -----------------------------------
        for offset_batch in _batched(list(range(len(topic_settled)))):
            batch_rows = [topic_settled[i] for i in offset_batch]
            concept_ids = [
                f"{row['_phase32_origin_concept_id']}"
                f"#{row['_phase32_segment_order']}"
                for row in batch_rows
            ]
            payload = {
                "stage": "learner_analysis",
                "rules": (
                    "Write Misconceptions and Error Analysis for each "
                    "concept. Error Analysis must name the learner and a "
                    "concrete faulty action or reasoning step, not another "
                    "belief."
                ),
                "topic": {"topic_id": topic_id, "title": topic_title},
                "concepts": [
                    {
                        "concept_id": concept_id,
                        "concept_title": row["concept_title"],
                        "concept_details": row["concept_details"],
                    }
                    for concept_id, row in zip(concept_ids, batch_rows)
                ],
            }
            decision = kernel.decide(
                kind="settle.analysis",
                unit_id=f"{topic_id}#analysis{offset_batch[0]}",
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=analysis_provider,
                checker=_analysis_checker(concept_ids),
                critic=None,
                store=store,
                policy_version=policy,
            )
            analysed_by_id = {
                str(row.get("concept_id") or ""): row
                for row in decision["response"].get("rows") or []
                if isinstance(row, Mapping)
            }
            for concept_id, row in zip(concept_ids, batch_rows):
                body = _normal(
                    analysed_by_id[concept_id].get(
                        "misconception_error_analysis"
                    )
                )
                row["concept_details"] = (
                    row["concept_details"].rstrip()
                    + " // Misconception/ Error Analysis: "
                    + body
                )

        topic_flag_count = sum(
            1
            for index in flags_by_row
            if len(settled) <= index < len(settled) + len(topic_settled)
        )
        progress.log(
            f"Settle: {topic_title!r} settled — {len(topic_settled)} "
            "concept(s) decided, grounded, and analysed"
            + (
                f"; {topic_flag_count} carrying review flags."
                if topic_flag_count
                else "."
            ),
            level="success",
        )
        settled.extend(topic_settled)

        # -- culminations: derived, never decided ------------------------
        for row in culmination_rows:
            if str(row.get("_semantic_topic_id") or "") != topic_id:
                continue
            derived_blocks: list[str] = []
            for source in topic_settled:
                for block_id in source.get("_source_block_ids") or []:
                    if block_id not in derived_blocks:
                        derived_blocks.append(block_id)
            settled.append({
                "topic": row.get("topic"),
                "parent_concept": _normal(row.get("parent_concept")),
                "concept_title": _normal(row.get("concept_title")),
                "concept_details": str(row.get("concept_details") or ""),
                "keywords": _normal(row.get("keywords")),
                "_semantic_topic_id": topic_id,
                "_source_block_ids": derived_blocks,
                "_source_grounding_contract": (
                    "derived-from-verified-topic-concepts"
                ),
            })

    for index, flags in flags_by_row.items():
        if 0 <= index < len(settled) and flags:
            settled[index]["review_flags"] = [
                *(settled[index].get("review_flags") or []),
                *flags,
            ]

    # The deposit chain verifies the grounding-certificate lineage on every
    # row (attempt 6 failed there); stamp and seal in settled order.
    from .. import canonical_source_phase31_grounding_contract as phase31
    from .. import grounding_certificate

    for number, row in enumerate(settled, start=1):
        row["_source_grounding_concept_id"] = f"CONCEPT-GROUND-{number:04d}"
        row["_source_grounding_version"] = phase31._GROUNDING_VERSION
    grounding_certificate.seal_records(
        settled,
        source_contract_hash=str(env.get("source_contract_hash") or ""),
        semantic_topology_sha256=(
            grounding_certificate.semantic_topology_sha256(env["graph"])
        ),
        allowed_block_ids=known_blocks,
    )
    return settled
