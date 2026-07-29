"""Namespace Phase 3.3 new Type hosts by topic.

The Phase 3.3 resolver runs one bounded plan per canonical topic, and each strict
schema intentionally uses compact local keys such as ``NEW-HOST-0001``. Those
keys may repeat in another topic. This adapter makes the materialisation ledger
use ``(topic_id, local_key)`` identities so multiple topics can safely create a
necessary concept in the same chapter without overwriting one another.
"""
from __future__ import annotations

import copy
from typing import Any

from . import canonical_source_phase3 as phase3
from . import canonical_source_phase31_grounding_contract as phase31
from . import canonical_source_phase33_preflight_contract as phase33
from . import concept_refiner as cr

_CONTRACT_VERSION = 1


def _apply_host_plan(
    records: list[dict[str, Any]],
    *,
    plans: list[dict[str, Any]],
    concept_payload: list[dict[str, Any]],
    concept_index: dict[str, int],
    topic_by_id: dict[str, dict[str, Any]],
    graph: dict[str, Any],
    subtopic_by_block: dict[str, str],
    units: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    int,
]:
    out = [copy.deepcopy(row) for row in records]
    concept_by_id = {
        str(row.get("concept_id") or ""): row for row in concept_payload
    }
    unit_by_id = {
        str(row.get("_phase33_assignment_unit_id") or ""): row for row in units
    }
    unit_topic = {
        unit_id: str(unit.get("_semantic_topic_id") or "")
        for unit_id, unit in unit_by_id.items()
    }

    new_record_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for plan in plans:
        for definition in plan.get("new_concepts") or []:
            local_key = str(definition.get("new_concept_key") or "")
            topic_id = str(definition.get("topic_id") or "")
            identity = (topic_id, local_key)
            if identity in new_record_by_key:
                raise ValueError(
                    "Phase 3.3 repeated a topic-scoped new concept key: "
                    f"topic_id={topic_id!r}, key={local_key!r}"
                )
            topic = topic_by_id[topic_id]
            block_ids = [
                str(value)
                for value in definition.get("source_block_ids") or []
                if str(value)
            ]
            record = {
                "topic": str(topic.get("title") or ""),
                "parent_concept": str(definition.get("parent_concept") or ""),
                "concept_title": str(definition.get("concept_title") or ""),
                "concept_details": cr.join_sections(
                    [
                        (
                            "Description",
                            str(definition.get("description") or "").strip()
                            + "\nAchieving Mastery: "
                            + str(
                                definition.get("achieving_mastery") or ""
                            ).strip(),
                        )
                    ]
                ),
                "keywords": ", ".join(
                    str(value).strip()
                    for value in definition.get("keywords") or []
                    if str(value).strip()
                ),
                "_semantic_topic_id": topic_id,
                "_semantic_graph_contract": graph.get("source_contract_hash"),
                "_semantic_topic_contract": "phase3.3-api-verified-type-host",
                "_source_block_ids": block_ids,
                "_semantic_subtopic_ids": sorted(
                    {
                        subtopic_by_block.get(block_id, "")
                        for block_id in block_ids
                    }
                    - {""}
                ),
                "_source_grounding_contract": "api-created-missing-type-host",
                "_source_grounding_version": phase33._HOST_VERSION,
                "_source_grounding_confidence": float(
                    definition.get("confidence") or 0.0
                ),
                "_phase3_assignment_unit_ids": list(
                    definition.get("assignment_unit_ids") or []
                ),
                "_phase33_new_type_host": True,
                "_phase33_local_new_concept_key": local_key,
            }
            new_record_by_key[identity] = record
            out.append(record)

    host_map: dict[str, dict[str, Any]] = {}
    qid_map: dict[str, dict[str, Any]] = {}
    for plan in plans:
        for assignment in plan.get("assignments") or []:
            unit_id = str(assignment.get("assignment_unit_id") or "")
            if assignment.get("decision") == "existing":
                concept_id = str(assignment.get("existing_concept_id") or "")
                payload = concept_by_id[concept_id]
                record = out[concept_index[concept_id]]
            else:
                local_key = str(assignment.get("new_concept_key") or "")
                topic_id = unit_topic.get(unit_id, "")
                identity = (topic_id, local_key)
                record = new_record_by_key.get(identity)
                if record is None:
                    raise ValueError(
                        "Phase 3.3 create-new assignment could not resolve its "
                        "topic-scoped definition: "
                        f"assignment_unit_id={unit_id!r}, topic_id={topic_id!r}, "
                        f"new_concept_key={local_key!r}"
                    )
                payload = {
                    "concept_title": record.get("concept_title"),
                    "parent_concept": record.get("parent_concept"),
                    "topic": record.get("topic"),
                    "topic_id": record.get("_semantic_topic_id"),
                }
            destination = {
                "topic_id": str(
                    payload.get("topic_id")
                    or record.get("_semantic_topic_id")
                    or ""
                ),
                "topic": str(payload.get("topic") or record.get("topic") or ""),
                "parent_concept": str(
                    payload.get("parent_concept")
                    or record.get("parent_concept")
                    or ""
                ),
                "concept_title": str(
                    payload.get("concept_title")
                    or record.get("concept_title")
                    or ""
                ),
                "decision": str(assignment.get("decision") or ""),
                "confidence": float(assignment.get("confidence") or 0.0),
            }
            host_map[unit_id] = destination
            for qid in unit_by_id.get(unit_id, {}).get("source_question_ids") or []:
                qid = str(qid or "").strip()
                if not qid:
                    continue
                if qid in qid_map and phase3._sha256_json(qid_map[qid]) != phase3._sha256_json(destination):
                    raise ValueError(
                        f"Phase 3.3 host plan assigned qid {qid} to multiple destinations"
                    )
                qid_map[qid] = copy.deepcopy(destination)
    return out, host_map, qid_map, len(new_record_by_key)


def install(_generation: Any | None = None) -> None:
    if (
        getattr(phase33, "_PHASE333_MULTITOPIC_HOST_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return
    phase33._apply_host_plan = _apply_host_plan
    phase33._PHASE333_MULTITOPIC_HOST_VERSION = _CONTRACT_VERSION
