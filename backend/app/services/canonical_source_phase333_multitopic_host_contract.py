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

_CONTRACT_VERSION = 2


def _qid_placement_contract(
    generation: Any,
    unit: dict[str, Any],
    qid: str,
) -> dict[str, Any] | None:
    for case in unit.get("case_prompts") or []:
        if not isinstance(case, dict):
            continue
        for example in generation._case_examples(case):
            if str(example.get("source_question_id") or "").strip() != qid:
                continue
            contract = generation._certified_type_case_placement_contract(
                example.get("_type_case_placement_contract"))
            if contract is not None:
                return contract
    contract = generation._certified_type_case_placement_contract(
        unit.get("_type_case_placement_contract"))
    if contract is not None and (
        len(unit.get("source_question_ids") or []) == 1
        or qid in (contract.get("qid_claim_ids") or {})
    ):
        return contract
    return None


def _attach_qid_host_placement_manifest(
    generation: Any,
    record: dict[str, Any],
    *,
    unit: dict[str, Any],
    unit_id: str,
    destination: dict[str, Any],
) -> None:
    qids = [
        str(value).strip()
        for value in unit.get("source_question_ids") or []
        if str(value).strip()
    ]
    unit_contract = generation._certified_type_case_placement_contract(
        unit.get("_type_case_placement_contract"))
    if unit_contract is None:
        return
    manifest = copy.deepcopy(
        record.get("_type_case_qid_host_placement_manifest") or {})
    if manifest and int(manifest.get("version") or 0) != 2:
        raise ValueError(
            "Phase 3.3 cannot mix legacy and v2 QID host placement manifests"
        )
    identity = {
        "version": 2,
        "policy_version": str(unit_contract.get("policy_version") or ""),
        "teaching_order_sha256": str(
            unit_contract.get("teaching_order_sha256") or ""),
        "source_contract_hash": str(
            unit_contract.get("source_contract_hash") or ""),
    }
    for key, value in identity.items():
        if manifest.get(key) not in (None, "", value):
            raise ValueError(
                "Phase 3.3 QID host manifest mixed incompatible "
                f"{key} values"
            )
        manifest[key] = value
    placements = manifest.setdefault("placements", {})
    for qid in qids:
        contract = _qid_placement_contract(generation, unit, qid)
        if contract is None:
            raise ValueError(
                "type_case_owner_uncertified: Phase 3.3 has no certified "
                f"QID placement for {qid}"
            )
        owner_topic_id = str(contract.get("owner_topic_id") or "")
        if str(destination.get("topic_id") or "") != owner_topic_id:
            raise ValueError(
                "Phase 3.3 verified host destination disagrees with certified "
                f"owner for {qid}: owner={owner_topic_id!r}, "
                f"destination={destination.get('topic_id')!r}"
            )
        entry = {
            "qid": qid,
            "claim_id": str(
                (contract.get("qid_claim_ids") or {}).get(qid)
                or contract.get("claim_id")
                or ""
            ),
            "source_location_topic_ids": list(
                contract.get("source_location_topic_ids") or []),
            "owner_topic_id": owner_topic_id,
            "required_topic_ids": list(
                contract.get("required_topic_ids") or []),
            "prerequisite_topic_ids": list(
                contract.get("prerequisite_topic_ids") or []),
            "reference_edges": copy.deepcopy(
                contract.get("reference_edges") or []),
            "illustration_topic_ids": list(
                contract.get("illustration_topic_ids") or []),
            "topic_relationships": copy.deepcopy(
                contract.get("topic_relationships") or []),
            # Carry the complete independently certified per-QID contract.
            # The final grounding certificate must be able to recompute this
            # contract's digest instead of trusting a detached hash copied
            # into the host attestation.
            "placement_contract": copy.deepcopy(contract),
            "placement_certificate_sha256": str(
                (
                    contract.get("qid_placement_sha256s") or {}
                ).get(qid)
                or contract.get("placement_certificate_sha256")
                or ""
            ),
            "assignment_unit_id": unit_id,
            "host_topic_id": str(destination.get("topic_id") or ""),
            "host_parent_concept": str(
                destination.get("parent_concept") or ""),
            "host_concept_title": str(
                destination.get("concept_title") or ""),
            "host_decision": str(destination.get("decision") or ""),
        }
        prior = placements.get(qid)
        if prior is not None and phase3._sha256_json(prior) != phase3._sha256_json(
            entry
        ):
            raise ValueError(
                f"Phase 3.3 assigned QID {qid} conflicting certified hosts"
            )
        placements[qid] = entry
    material = copy.deepcopy(manifest)
    material.pop("certificate_sha256", None)
    manifest["certificate_sha256"] = phase3._sha256_json(material)
    record["_type_case_qid_host_placement_manifest"] = manifest


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
    from . import generation

    out = [copy.deepcopy(row) for row in records]
    concept_by_id = {
        str(row.get("concept_id") or ""): row for row in concept_payload
    }
    unit_by_id = {
        str(row.get("_phase33_assignment_unit_id") or ""): row for row in units
    }
    unit_topic: dict[str, str] = {}
    for unit_id, unit in unit_by_id.items():
        placement = generation._certified_type_case_placement_contract(
            unit.get("_type_case_placement_contract"))
        unit_topic[unit_id] = str(
            placement.get("owner_topic_id")
            if placement is not None
            else unit.get("_semantic_topic_id")
            or ""
        )

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
            expected_owner = unit_topic.get(unit_id, "")
            if expected_owner and destination["topic_id"] != expected_owner:
                raise ValueError(
                    "Phase 3.3 host destination changed certified assignment-"
                    f"unit owner: unit={unit_id!r}, owner={expected_owner!r}, "
                    f"destination={destination['topic_id']!r}"
                )
            _attach_qid_host_placement_manifest(
                generation,
                record,
                unit=unit_by_id.get(unit_id, {}),
                unit_id=unit_id,
                destination=destination,
            )
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
