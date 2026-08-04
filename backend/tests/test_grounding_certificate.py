"""Focused integrity tests for final concept-grounding certificates."""

from __future__ import annotations

import copy

import pytest

from app.services import generation
from app.services import grounding_certificate as certificate
from app.services import build_concepts
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import checkpoints
from app.services import placement_policy
from app.services import semantic_recovery


SOURCE_CONTRACT = "a" * 64
ALLOWED_BLOCKS = {
    "BLK-00156",
    "BLK-00176",
    "BLK-00181",
    "BLK-00182",
}
SEMANTIC_GRAPH = {
    "source_contract_hash": SOURCE_CONTRACT,
    "blocks": [
        {"block_id": block_id}
        for block_id in sorted(ALLOWED_BLOCKS)
    ],
}
SEMANTIC_TOPOLOGY = certificate.semantic_topology_sha256(SEMANTIC_GRAPH)


def _record(number: int = 1) -> dict:
    return {
        "topic": "The Age of Revolutions: 1830-1848",
        "parent_concept": "Economic Hardship and Popular Revolt",
        "concept_title": f"Insecure Home-Based Textile Work {number:02d}",
        "concept_details": (
            "Description: Home-based textile workers faced insecure work "
            "when contractors reduced payments, while the Silesian weavers "
            "experienced exploitation and revolt.\nAchieving Mastery: "
            "Explain both parts using source evidence. // "
            "Misconception/ Error Analysis: Misconceptions: Students may "
            "treat home work as secure.; Error Analysis: Students may cite "
            "only the uprising and omit the home-based employment claim."
        ),
        "keywords": "textiles, home work, weavers",
        "_semantic_topic_id": "TOPIC-0007",
        "_semantic_subtopic_ids": ["SUBTOPIC-0012", "SUBTOPIC-0013"],
        "_source_block_ids": [
            "BLK-00156",
            "BLK-00176",
            "BLK-00181",
        ],
        "_source_grounding_contract": "api-verified-source-block-ids",
        "_source_grounding_version": "phase3.8-test-grounding-1",
        "_source_grounding_boundary_blocks": [
            {
                "block_id": "BLK-00156",
                "source_topic_id": "TOPIC-0006",
                "target_topic_id": "TOPIC-0007",
            }
        ],
    }


def _sealed_records(count: int = 1) -> list[dict]:
    records = [_record(number) for number in range(1, count + 1)]
    return certificate.seal_records(
        records,
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=SEMANTIC_TOPOLOGY,
        allowed_block_ids=ALLOWED_BLOCKS,
    )


def _placement_contract(record: dict) -> dict:
    claim_id = "TOPOLOGY-CONCEPT-0001#1"

    def relationship(
        topic_id: str,
        relationship_type: str,
        evidence_block_ids: list[str],
        reason: str,
        necessity: bool = True,
    ) -> dict:
        kind = placement_policy.relationship_type(relationship_type)
        assert kind is not None
        relation = placement_policy.TopicRelationship(
            claim_id=claim_id,
            topic_id=topic_id,
            relationship_type=kind,
            necessity=necessity,
            evidence_block_ids=tuple(evidence_block_ids),
        )
        return {
            "relationship_id": relation.relationship_id,
            "claim_id": claim_id,
            "topic_id": topic_id,
            "relationship_type": relationship_type,
            "necessity": necessity,
            "evidence_block_ids": evidence_block_ids,
            "provider_reason": reason,
            "critic_verdict": "accepted",
        }

    value = {
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": "b" * 64,
        "claim_id": claim_id,
        "normalized_claim": certificate.source_claim(record),
        "origin_claim_sha256": placement_policy.claim_text_sha256(
            certificate.source_claim(record)
        ),
        "source_location_topic_id": "TOPIC-0006",
        "owner_topic_id": "TOPIC-0007",
        "required_topic_ids": ["TOPIC-0006", "TOPIC-0007"],
        "prerequisite_topic_ids": ["TOPIC-0006"],
        "reference_edges": [["TOPIC-0005", "RETROSPECTIVE_REFERENCE"]],
        "illustration_topic_ids": [],
        "topic_relationships": [
            relationship(
                "TOPIC-0007",
                "CORE_TEACHING",
                ["BLK-00176", "BLK-00181"],
                "The later topic teaches the revolt.",
            ),
            relationship(
                "TOPIC-0006",
                "REQUIRED_PREREQUISITE",
                ["BLK-00156"],
                "The work arrangement is prerequisite.",
            ),
            relationship(
                "TOPIC-0005",
                "RETROSPECTIVE_REFERENCE",
                ["BLK-00182"],
                "The later claim refers back without changing ownership.",
                necessity=False,
            ),
        ],
        "origin_claim_ids": ["TOPOLOGY-CONCEPT-0001"],
        "split_group_id": "",
        "protected_source_items": ["QINV-0001", "FIG-0001"],
    }
    value["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(value)
    )
    return value


def _sealed_placement_records() -> list[dict]:
    row = _record()
    row[certificate.PLACEMENT_CONTRACT_FIELD] = _placement_contract(row)
    return certificate.seal_records(
        [row],
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=SEMANTIC_TOPOLOGY,
        allowed_block_ids=ALLOWED_BLOCKS,
    )


def _active_relationship_graph() -> dict:
    return {
        "source_contract_hash": SOURCE_CONTRACT,
        "topics": [
            {"topic_id": "TOPIC-0005", "order": 1, "title": "Earlier"},
            {"topic_id": "TOPIC-0006", "order": 2, "title": "Foundation"},
            {"topic_id": "TOPIC-0007", "order": 3, "title": "Revolt"},
        ],
        "blocks": [
            {
                "block_id": "BLK-00182",
                "topic_id": "TOPIC-0005",
                "order": 1,
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-00156",
                "topic_id": "TOPIC-0006",
                "order": 2,
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-00176",
                "topic_id": "TOPIC-0007",
                "order": 3,
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-00181",
                "topic_id": "TOPIC-0007",
                "order": 4,
                "kind": "paragraph",
            },
        ],
    }


def _seal_active_placement_record(
    graph: dict,
    *,
    contract: dict | None = None,
) -> list[dict]:
    row = _record()
    value = copy.deepcopy(contract or _placement_contract(row))
    value["teaching_order_sha256"] = placement_policy.seal_teaching_order(
        graph,
        source_contract_hash=SOURCE_CONTRACT,
    ).sha256
    value["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(value)
    )
    row[certificate.PLACEMENT_CONTRACT_FIELD] = value
    return certificate.seal_records(
        [row],
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=certificate.semantic_topology_sha256(graph),
        allowed_block_ids=ALLOWED_BLOCKS,
    )


def _type_case_contract(
    *,
    qid: str = "QINV-0001",
    source_topic_id: str = "TOPIC-0006",
    claim_id: str | None = None,
) -> dict:
    claim_id = claim_id or f"TASK:{qid}"
    relation = placement_policy.TopicRelationship(
        claim_id=claim_id,
        topic_id="TOPIC-0007",
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=("BLK-00176",),
    )
    value = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": "b" * 64,
        "source_contract_hash": SOURCE_CONTRACT,
        "qid": qid,
        "claim_id": claim_id,
        "normalized_claim": "Explain the exploitation of textile workers.",
        "source_location_topic_id": source_topic_id,
        "source_location_topic_ids": [source_topic_id],
        "owner_topic_id": "TOPIC-0007",
        "owner_topic_title": "The Age of Revolutions: 1830-1848",
        "required_topic_ids": ["TOPIC-0007"],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [{
            "relationship_id": relation.relationship_id,
            "claim_id": claim_id,
            "topic_id": "TOPIC-0007",
            "relationship_type": "CORE_TEACHING",
            "necessity": True,
            "evidence_block_ids": ["BLK-00176"],
            "provider_reason": "The topic directly teaches this task.",
            "critic_verdict": "accepted",
        }],
    }
    value["placement_certificate_sha256"] = (
        generation._type_case_placement_digest(value)
    )
    return value


def _type_case_manifest_and_ledger(
    records: list[dict],
    *,
    source_topic_id: str = "TOPIC-0006",
    claim_id: str | None = None,
) -> tuple[dict, dict]:
    qid = "QINV-0001"
    child = _type_case_contract(
        qid=qid,
        source_topic_id=source_topic_id,
        claim_id=claim_id,
    )
    manifest = {
        "version": 2,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": "b" * 64,
        "source_contract_hash": SOURCE_CONTRACT,
        "placements": {
            qid: {
                "qid": qid,
                "claim_id": child["claim_id"],
                "source_location_topic_ids": list(
                    child["source_location_topic_ids"]
                ),
                "owner_topic_id": "TOPIC-0007",
                "required_topic_ids": ["TOPIC-0007"],
                "prerequisite_topic_ids": [],
                "reference_edges": [],
                "illustration_topic_ids": [],
                "topic_relationships": copy.deepcopy(
                    child["topic_relationships"]
                ),
                "placement_contract": copy.deepcopy(child),
                "placement_certificate_sha256": child[
                    "placement_certificate_sha256"
                ],
                "assignment_unit_id": "UNIT-0001",
                "host_topic_id": "TOPIC-0007",
                "host_parent_concept": records[0]["parent_concept"],
                "host_concept_title": records[0]["concept_title"],
                "host_decision": "existing",
            }
        },
    }
    manifest["certificate_sha256"] = certificate._sha256_json(manifest)
    ledger = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "source_contract_hash": SOURCE_CONTRACT,
        "teaching_order_sha256": "b" * 64,
        "placements": {qid: copy.deepcopy(child)},
    }
    ledger["certificate_sha256"] = certificate._sha256_json(ledger)
    return manifest, ledger


def test_final_certificate_reports_ground_0023_and_removed_blk_before_hash():
    records = _sealed_records(23)
    final = certificate.build_final_certificate(records)
    stale = copy.deepcopy(records)
    stale[22]["_source_block_ids"] = ["BLK-00176", "BLK-00181"]

    with pytest.raises(certificate.GroundingCertificateError) as caught:
        certificate.verify_final_certificate(stale, final)

    message = str(caught.value)
    assert "CONCEPT-GROUND-0023" in message
    assert "missing attested source block(s): BLK-00156" in message
    assert "grounded concept identity or metadata changed" not in message


def test_final_certificate_rejects_description_mutation():
    records = _sealed_records()
    final = certificate.build_final_certificate(records)
    changed = copy.deepcopy(records)
    changed[0]["concept_details"] = changed[0]["concept_details"].replace(
        "faced insecure work",
        "had guaranteed secure work",
    )

    with pytest.raises(certificate.GroundingCertificateError) as caught:
        certificate.verify_final_certificate(changed, final)

    message = str(caught.value)
    assert "CONCEPT-GROUND-0001" in message
    assert "source-facing Description changed after grounding" in message


def test_final_certificate_exposes_readable_hashed_row_identity():
    records = _sealed_records()

    final = certificate.build_final_certificate(records)
    identity = final["evidence"][0]["row_identity"]

    assert identity == {
        "version": certificate.ROW_IDENTITY_VERSION,
        "topic": "The Age of Revolutions: 1830-1848",
        "parent_concept": "Economic Hardship and Popular Revolt",
        "concept_title": "Insecure Home-Based Textile Work 01",
        "semantic_topic_id": "TOPIC-0007",
        "placement_contract": {},
    }
    assert final["evidence"][0]["row_identity_sha256"] == (
        records[0][certificate.ROW_IDENTITY_FIELD]
    )


def test_certified_placement_contract_is_bound_into_row_and_final_seals():
    records = _sealed_placement_records()

    final = certificate.build_final_certificate(records)
    placement = final["evidence"][0]["placement_contract"]

    assert placement["certified"] is True
    assert placement["owner_topic_id"] == "TOPIC-0007"
    assert placement["prerequisite_topic_ids"] == ["TOPIC-0006"]
    assert placement["protected_source_items"] == ["FIG-0001", "QINV-0001"]
    assert final["placement_policy_version"] == placement_policy.POLICY_VERSION
    assert final["teaching_order_sha256"] == "b" * 64
    assert final["placement_manifest"][0]["placement_contract"] == placement
    assert final["placement_manifest_sha256"]


def test_production_final_certificate_requires_every_normal_row_placement():
    legacy_records = _sealed_records()

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="without independent placement authority",
    ):
        certificate.build_final_certificate(
            legacy_records,
            require_placement_contracts=True,
        )

    placed_records = _sealed_placement_records()
    final = certificate.build_final_certificate(
        placed_records,
        require_placement_contracts=True,
    )
    assert certificate.verify_final_certificate(
        placed_records,
        final,
        require_placement_contracts=True,
    ) == final


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("policy_version", "aegis-placement-policy-tampered"),
        ("teaching_order_sha256", "c" * 64),
        ("owner_topic_id", "TOPIC-0006"),
        ("required_topic_ids", ["TOPIC-0007"]),
        ("prerequisite_topic_ids", []),
        ("reference_edges", []),
        ("origin_claim_ids", ["TOPOLOGY-CONCEPT-9999"]),
        ("split_group_id", "SPLIT-9999"),
        ("protected_source_items", ["QINV-0001"]),
    ],
)
def test_placement_mutation_invalidates_grounding_certificate(
    field,
    replacement,
):
    records = _sealed_placement_records()
    final = certificate.build_final_certificate(records)
    changed = copy.deepcopy(records)
    changed[0][certificate.PLACEMENT_CONTRACT_FIELD][field] = replacement

    with pytest.raises(
        certificate.GroundingCertificateError,
        match=(
            "stale policy version|stale or mismatched placement certificate"
        ),
    ):
        certificate.verify_final_certificate(changed, final)


def test_relationship_evidence_mutation_invalidates_placement_certificate():
    records = _sealed_placement_records()
    final = certificate.build_final_certificate(records)
    changed = copy.deepcopy(records)
    prerequisite = next(
        relation
        for relation in changed[0][certificate.PLACEMENT_CONTRACT_FIELD][
            "topic_relationships"
        ]
        if relation["relationship_type"] == "REQUIRED_PREREQUISITE"
    )
    prerequisite["evidence_block_ids"] = ["BLK-00182"]

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="stale or mismatched placement certificate",
    ):
        certificate.verify_final_certificate(changed, final)


def test_every_relationship_type_requires_exact_evidence():
    row = _record()
    contract = _placement_contract(row)
    reference = contract["topic_relationships"][2]
    reference["evidence_block_ids"] = []
    relation = placement_policy.TopicRelationship(
        claim_id=reference["claim_id"],
        topic_id=reference["topic_id"],
        relationship_type=(
            placement_policy.RelationshipType.RETROSPECTIVE_REFERENCE
        ),
        necessity=False,
        evidence_block_ids=(),
    )
    reference["relationship_id"] = relation.relationship_id
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    row[certificate.PLACEMENT_CONTRACT_FIELD] = contract

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="lacks exact evidence",
    ):
        certificate.seal_records(
            [row],
            source_contract_hash=SOURCE_CONTRACT,
            semantic_topology_sha256=SEMANTIC_TOPOLOGY,
            allowed_block_ids=ALLOWED_BLOCKS,
        )


def test_active_teaching_order_recomputes_and_rejects_wrong_owner():
    graph = {
        "source_contract_hash": SOURCE_CONTRACT,
        "topics": [
            {"topic_id": "TOPIC-EARLY", "order": 1, "title": "Early"},
            {"topic_id": "TOPIC-LATE", "order": 2, "title": "Late"},
        ],
        "blocks": [
            {
                "block_id": "BLK-EARLY",
                "topic_id": "TOPIC-EARLY",
                "order": 1,
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-LATE",
                "topic_id": "TOPIC-LATE",
                "order": 2,
                "kind": "paragraph",
            },
        ],
    }
    row = _record()
    row["_semantic_topic_id"] = "TOPIC-EARLY"
    row["_source_block_ids"] = ["BLK-EARLY", "BLK-LATE"]
    claim_id = "TOPOLOGY-CONCEPT-0001#1"
    relationships = []
    for topic_id, kind_name, block_id in (
        ("TOPIC-EARLY", "CORE_TEACHING", "BLK-EARLY"),
        ("TOPIC-LATE", "REQUIRED_LATER_METHOD", "BLK-LATE"),
    ):
        kind = placement_policy.RelationshipType(kind_name)
        relation = placement_policy.TopicRelationship(
            claim_id=claim_id,
            topic_id=topic_id,
            relationship_type=kind,
            necessity=True,
            evidence_block_ids=(block_id,),
        )
        relationships.append({
            "relationship_id": relation.relationship_id,
            "claim_id": claim_id,
            "topic_id": topic_id,
            "relationship_type": kind_name,
            "necessity": True,
            "evidence_block_ids": [block_id],
            "provider_reason": "Exact evidence.",
            "critic_verdict": "accepted",
        })
    contract = {
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": placement_policy.seal_teaching_order(
            graph,
            source_contract_hash=SOURCE_CONTRACT,
        ).sha256,
        "claim_id": claim_id,
        "normalized_claim": certificate.source_claim(row),
        "origin_claim_sha256": placement_policy.claim_text_sha256(
            certificate.source_claim(row)
        ),
        "source_location_topic_id": "TOPIC-EARLY",
        # Internally consistent, but wrong: TOPIC-LATE is the latest owning
        # relationship in the active teaching order.
        "owner_topic_id": "TOPIC-EARLY",
        "required_topic_ids": ["TOPIC-EARLY", "TOPIC-LATE"],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": relationships,
        "origin_claim_ids": ["TOPOLOGY-CONCEPT-0001"],
        "split_group_id": "",
        "protected_source_items": [],
    }
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    row[certificate.PLACEMENT_CONTRACT_FIELD] = contract
    records = certificate.seal_records(
        [row],
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=certificate.semantic_topology_sha256(graph),
        allowed_block_ids={"BLK-EARLY", "BLK-LATE"},
    )
    final = certificate.build_final_certificate(records)

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="placement disagrees with the active teaching order",
    ):
        certificate.verify_final_certificate(
            records,
            final,
            semantic_graph=graph,
            require_semantic_graph=True,
        )


def test_active_teaching_order_recomputes_nested_type_case_qid_owner():
    graph = {
        "source_contract_hash": SOURCE_CONTRACT,
        "topics": [
            {"topic_id": "TOPIC-EARLY", "order": 1, "title": "Early"},
            {"topic_id": "TOPIC-LATE", "order": 2, "title": "Late"},
        ],
        "blocks": [
            {
                "block_id": "BLK-EARLY",
                "topic_id": "TOPIC-EARLY",
                "order": 1,
                "kind": "paragraph",
            },
            {
                "block_id": "BLK-LATE",
                "topic_id": "TOPIC-LATE",
                "order": 2,
                "kind": "paragraph",
            },
        ],
    }
    order = placement_policy.seal_teaching_order(
        graph,
        source_contract_hash=SOURCE_CONTRACT,
    )
    qid = "QINV-0001"
    claim_id = f"TASK-{qid}"
    relationships = []
    for topic_id, kind, block_id in (
        (
            "TOPIC-EARLY",
            placement_policy.RelationshipType.CORE_TEACHING,
            "BLK-EARLY",
        ),
        (
            "TOPIC-LATE",
            placement_policy.RelationshipType.REQUIRED_LATER_METHOD,
            "BLK-LATE",
        ),
    ):
        relation = placement_policy.TopicRelationship(
            claim_id=claim_id,
            topic_id=topic_id,
            relationship_type=kind,
            necessity=True,
            evidence_block_ids=(block_id,),
        )
        relationships.append({
            "relationship_id": relation.relationship_id,
            "claim_id": claim_id,
            "topic_id": topic_id,
            "relationship_type": kind.value,
            "necessity": True,
            "evidence_block_ids": [block_id],
            "provider_reason": "Exact task evidence.",
            "critic_verdict": "accepted",
        })
    # This is internally self-addressed and structurally valid, but wrong:
    # TOPIC-LATE is the latest accepted owning relationship.
    child = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "source_contract_hash": SOURCE_CONTRACT,
        "qid": qid,
        "claim_id": claim_id,
        "normalized_claim": "Apply the later method to the early task.",
        "source_location_topic_id": "TOPIC-EARLY",
        "source_location_topic_ids": ["TOPIC-EARLY"],
        "owner_topic_id": "TOPIC-EARLY",
        "owner_topic_title": "Early",
        "required_topic_ids": ["TOPIC-EARLY", "TOPIC-LATE"],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": relationships,
    }
    child["placement_certificate_sha256"] = (
        generation._type_case_placement_digest(child)
    )

    row = _record()
    row["topic"] = "Early"
    row["_semantic_topic_id"] = "TOPIC-EARLY"
    row["_source_block_ids"] = ["BLK-EARLY", "BLK-LATE"]
    row_claim_id = "TOPOLOGY-CONCEPT-0001#1"
    row_relation = placement_policy.TopicRelationship(
        claim_id=row_claim_id,
        topic_id="TOPIC-EARLY",
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=("BLK-EARLY",),
    )
    row_contract = {
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "claim_id": row_claim_id,
        "normalized_claim": certificate.source_claim(row),
        "origin_claim_sha256": placement_policy.claim_text_sha256(
            certificate.source_claim(row)
        ),
        "source_location_topic_id": "TOPIC-EARLY",
        "owner_topic_id": "TOPIC-EARLY",
        "required_topic_ids": ["TOPIC-EARLY"],
        "prerequisite_topic_ids": [],
        "reference_edges": [],
        "illustration_topic_ids": [],
        "topic_relationships": [{
            "relationship_id": row_relation.relationship_id,
            "claim_id": row_claim_id,
            "topic_id": "TOPIC-EARLY",
            "relationship_type": "CORE_TEACHING",
            "necessity": True,
            "evidence_block_ids": ["BLK-EARLY"],
            "provider_reason": "The early topic teaches this concept.",
            "critic_verdict": "accepted",
        }],
        "origin_claim_ids": ["TOPOLOGY-CONCEPT-0001"],
        "split_group_id": "",
        "protected_source_items": [],
    }
    row_contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(row_contract)
    )
    row[certificate.PLACEMENT_CONTRACT_FIELD] = row_contract
    records = certificate.seal_records(
        [row],
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=certificate.semantic_topology_sha256(graph),
        allowed_block_ids={"BLK-EARLY", "BLK-LATE"},
    )
    manifest = {
        "version": 2,
        "policy_version": placement_policy.POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "source_contract_hash": SOURCE_CONTRACT,
        "placements": {
            qid: {
                "qid": qid,
                "claim_id": claim_id,
                "source_location_topic_ids": ["TOPIC-EARLY"],
                "owner_topic_id": "TOPIC-EARLY",
                "required_topic_ids": ["TOPIC-EARLY", "TOPIC-LATE"],
                "prerequisite_topic_ids": [],
                "reference_edges": [],
                "illustration_topic_ids": [],
                "topic_relationships": copy.deepcopy(relationships),
                "placement_contract": copy.deepcopy(child),
                "placement_certificate_sha256": child[
                    "placement_certificate_sha256"
                ],
                "host_topic_id": "TOPIC-EARLY",
                "host_parent_concept": records[0]["parent_concept"],
                "host_concept_title": records[0]["concept_title"],
            }
        },
    }
    manifest["certificate_sha256"] = certificate._sha256_json(manifest)
    records[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD] = manifest
    ledger = {
        "version": 2,
        "certified": True,
        "policy_version": placement_policy.POLICY_VERSION,
        "source_contract_hash": SOURCE_CONTRACT,
        "teaching_order_sha256": order.sha256,
        "placements": {qid: copy.deepcopy(child)},
    }
    ledger["certificate_sha256"] = certificate._sha256_json(ledger)
    final = certificate.build_final_certificate(
        records,
        type_case_qid_placement_ledger=ledger,
    )

    # Portable verification deliberately remains possible without a graph.
    assert certificate.verify_final_certificate(
        records,
        final,
        type_case_qid_placement_ledger=ledger,
    ) == final
    with pytest.raises(
        certificate.GroundingCertificateError,
        match="Type/Case QID QINV-0001 placement disagrees with the active teaching order",
    ):
        certificate.verify_final_certificate(
            records,
            final,
            semantic_graph=graph,
            require_semantic_graph=True,
            type_case_qid_placement_ledger=ledger,
        )


def test_active_graph_accepts_relationship_evidence_in_asserted_topics():
    graph = _active_relationship_graph()
    records = _seal_active_placement_record(graph)
    final = certificate.build_final_certificate(records)

    assert certificate.verify_final_certificate(
        records,
        final,
        semantic_graph=graph,
        require_semantic_graph=True,
    ) == final


def test_active_graph_rejects_rehashed_relationship_citing_wrong_topic_block():
    graph = _active_relationship_graph()
    row = _record()
    contract = _placement_contract(row)
    core = contract["topic_relationships"][0]
    core["evidence_block_ids"] = ["BLK-00156"]
    core_relation = placement_policy.TopicRelationship(
        claim_id=core["claim_id"],
        topic_id=core["topic_id"],
        relationship_type=placement_policy.RelationshipType.CORE_TEACHING,
        necessity=True,
        evidence_block_ids=("BLK-00156",),
    )
    core["relationship_id"] = core_relation.relationship_id
    # Recompute every visible placement hash.  The active graph remains the
    # independent authority that proves BLK-00156 belongs to TOPIC-0006.
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    records = _seal_active_placement_record(graph, contract=contract)
    final = certificate.build_final_certificate(records)

    with pytest.raises(
        certificate.GroundingCertificateError,
        match=(
            "asserts topic TOPIC-0007, but active semantic graph source block "
            "BLK-00156 belongs to TOPIC-0006"
        ),
    ):
        certificate.verify_final_certificate(
            records,
            final,
            semantic_graph=graph,
            require_semantic_graph=True,
        )


def test_active_graph_rejects_non_content_relationship_evidence():
    graph = _active_relationship_graph()
    graph["blocks"][0]["kind"] = "navigation"
    records = _seal_active_placement_record(graph)
    final = certificate.build_final_certificate(records)

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="uses non-content source block BLK-00182 \\(kind=navigation\\)",
    ):
        certificate.verify_final_certificate(
            records,
            final,
            semantic_graph=graph,
            require_semantic_graph=True,
        )


def test_seal_rejects_declared_but_uncertified_placement_metadata():
    row = _record()
    row[certificate.PLACEMENT_CONTRACT_FIELD] = _placement_contract(row)
    row[certificate.PLACEMENT_CONTRACT_FIELD]["certified"] = False

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="not independently certified",
    ):
        certificate.seal_records(
            [row],
            source_contract_hash=SOURCE_CONTRACT,
            semantic_topology_sha256=SEMANTIC_TOPOLOGY,
            allowed_block_ids=ALLOWED_BLOCKS,
        )


def test_seal_rejects_certified_placement_with_unknown_relationship_block():
    row = _record()
    contract = _placement_contract(row)
    relation = contract["topic_relationships"][1]
    relation["evidence_block_ids"] = ["BLK-99999"]
    relation["relationship_id"] = placement_policy.TopicRelationship(
        claim_id=relation["claim_id"],
        topic_id=relation["topic_id"],
        relationship_type=placement_policy.RelationshipType(
            relation["relationship_type"]
        ),
        necessity=relation["necessity"],
        evidence_block_ids=("BLK-99999",),
    ).relationship_id
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    row[certificate.PLACEMENT_CONTRACT_FIELD] = contract

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="references unknown source block.*BLK-99999",
    ):
        certificate.seal_records(
            [row],
            source_contract_hash=SOURCE_CONTRACT,
            semantic_topology_sha256=SEMANTIC_TOPOLOGY,
            allowed_block_ids=ALLOWED_BLOCKS,
        )


def test_seal_rejects_stale_content_addressed_relationship_identity():
    row = _record()
    contract = _placement_contract(row)
    contract["topic_relationships"][0]["relationship_id"] = (
        "REL-000000000000000000000000"
    )
    contract["placement_certificate_sha256"] = (
        placement_policy.placement_contract_sha256(contract)
    )
    row[certificate.PLACEMENT_CONTRACT_FIELD] = contract

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="stale content-addressed identity",
    ):
        certificate.seal_records(
            [row],
            source_contract_hash=SOURCE_CONTRACT,
            semantic_topology_sha256=SEMANTIC_TOPOLOGY,
            allowed_block_ids=ALLOWED_BLOCKS,
        )


def test_type_case_qid_host_manifest_is_bound_into_final_certificate():
    records = _sealed_placement_records()
    manifest, ledger = _type_case_manifest_and_ledger(records)
    records[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD] = manifest
    final = certificate.build_final_certificate(
        records,
        type_case_qid_placement_ledger=ledger,
    )
    changed = copy.deepcopy(records)
    changed[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD]["placements"][
        "QINV-0001"
    ]["owner_topic_id"] = "TOPIC-0006"

    assert final["type_case_qid_host_placement_manifest"]
    assert final["type_case_qid_placement_ledger_sha256"] == (
        ledger["certificate_sha256"]
    )
    assert final["type_case_qid_placement_qid_count"] == 1
    with pytest.raises(
        certificate.GroundingCertificateError,
        match="stale self-certificate",
    ):
        certificate.verify_final_certificate(
            changed,
            final,
            type_case_qid_placement_ledger=ledger,
        )


def test_type_case_host_manifest_requires_authoritative_qid_ledger():
    records = _sealed_placement_records()
    manifest, _ledger = _type_case_manifest_and_ledger(records)
    records[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD] = manifest

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="without the authoritative placement ledger",
    ):
        certificate.build_final_certificate(records)


def test_type_case_host_manifest_requires_exact_ledger_contract():
    records = _sealed_placement_records()
    manifest, _ledger = _type_case_manifest_and_ledger(records)
    records[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD] = manifest
    _other_manifest, other_ledger = _type_case_manifest_and_ledger(
        records,
        source_topic_id="TOPIC-0005",
    )

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="different certified placement contract",
    ):
        certificate.build_final_certificate(
            records,
            type_case_qid_placement_ledger=other_ledger,
        )


def test_type_case_certificate_envelope_requires_ledger_beside_it():
    records = _sealed_placement_records()
    manifest, ledger = _type_case_manifest_and_ledger(records)
    records[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD] = manifest
    final = certificate.build_final_certificate(
        records,
        type_case_qid_placement_ledger=ledger,
    )

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="cannot be verified without the authoritative placement ledger",
    ):
        certificate.verify_certificate_envelope(final)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("topic", "A Different Main Topic"),
        ("parent_concept", "A Different Parent"),
        ("concept_title", "A Renamed Concept"),
        ("_semantic_topic_id", "TOPIC-9999"),
    ],
)
def test_row_identity_attestation_reports_topology_drift(field, replacement):
    record = _sealed_records()[0]
    record[field] = replacement

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="identity/topology drift for CONCEPT-GROUND-0001",
    ) as caught:
        certificate.verify_row_identity(record, row_index=0)

    message = str(caught.value)
    assert "topic=" in message
    assert "parent_concept=" in message
    assert "concept_title=" in message
    assert "semantic_topic_id=" in message


def test_final_certificate_rejects_added_evidence_block():
    records = _sealed_records()
    final = certificate.build_final_certificate(records)
    changed = copy.deepcopy(records)
    changed[0]["_source_block_ids"].append("BLK-00182")

    with pytest.raises(certificate.GroundingCertificateError) as caught:
        certificate.verify_final_certificate(changed, final)

    message = str(caught.value)
    assert "CONCEPT-GROUND-0001" in message
    assert "added unattested source block(s): BLK-00182" in message


def test_final_certificate_rejects_reordered_evidence_blocks():
    records = _sealed_records()
    final = certificate.build_final_certificate(records)
    changed = copy.deepcopy(records)
    changed[0]["_source_block_ids"] = [
        "BLK-00181",
        "BLK-00176",
        "BLK-00156",
    ]

    with pytest.raises(certificate.GroundingCertificateError) as caught:
        certificate.verify_final_certificate(changed, final)

    message = str(caught.value)
    assert "CONCEPT-GROUND-0001" in message
    assert "evidence order or multiplicity changed" in message
    assert "attested=BLK-00156,BLK-00176,BLK-00181" in message


def test_final_certificate_cannot_be_reminted_for_grounded_subset():
    records = _sealed_records(2)

    with pytest.raises(certificate.GroundingCertificateError) as caught:
        certificate.build_final_certificate(records[:1])

    message = str(caught.value)
    assert "lineage mismatch" in message
    assert "grounding attested 2 record(s)" in message
    assert "final payload contains 1" in message


def test_final_certificate_cannot_be_reminted_after_row_reorder():
    records = _sealed_records(2)
    reordered = [copy.deepcopy(records[1]), copy.deepcopy(records[0])]

    with pytest.raises(certificate.GroundingCertificateError) as caught:
        certificate.build_final_certificate(reordered)

    message = str(caught.value)
    assert "lineage mismatch at row 0" in message
    assert "expected CONCEPT-GROUND-0001" in message
    assert "found CONCEPT-GROUND-0002" in message


def test_unverified_grounding_contract_cannot_build_final_certificate():
    records = [_record()]
    records[0]["_source_grounding_contract"] = "topic-bounded-deterministic"
    certificate.seal_records(
        records,
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=SEMANTIC_TOPOLOGY,
        allowed_block_ids=ALLOWED_BLOCKS,
    )

    with pytest.raises(
        certificate.GroundingCertificateError,
        match=(
            r"CONCEPT-GROUND-0001 is not independently verified "
            r"\(contract=topic-bounded-deterministic\)"
        ),
    ):
        certificate.build_final_certificate(records)


def test_certificate_hashes_are_stable_across_mapping_key_order():
    first = _record()
    second = {
        key: copy.deepcopy(value)
        for key, value in reversed(list(first.items()))
    }
    second["_source_grounding_boundary_blocks"] = [{
        key: value
        for key, value in reversed(list(
            first["_source_grounding_boundary_blocks"][0].items()
        ))
    }]

    first_records = certificate.seal_records(
        [first],
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=SEMANTIC_TOPOLOGY,
        allowed_block_ids=ALLOWED_BLOCKS,
    )
    second_records = certificate.seal_records(
        [second],
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=SEMANTIC_TOPOLOGY,
        allowed_block_ids=reversed(sorted(ALLOWED_BLOCKS)),
    )

    assert first_records[0][certificate.ROW_CERTIFICATE_FIELD] == (
        second_records[0][certificate.ROW_CERTIFICATE_FIELD]
    )
    assert certificate.build_final_certificate(first_records) == (
        certificate.build_final_certificate(second_records)
    )


def test_inventory_audit_accepts_bounded_input_and_deposited_certificates():
    records = _sealed_records()
    final = certificate.build_final_certificate(records)

    checkpoints._validate_inventory(
        {
            "items": [],
            "stats": {},
            "mined_types": [],
            certificate.FINAL_CERTIFICATE_FIELD: copy.deepcopy(final),
            "deposited_grounding_certificate": copy.deepcopy(final),
        },
        "payload.question_inventory",
    )


def test_inventory_audit_rejects_tampered_grounding_certificate_hash():
    records = _sealed_records()
    final = certificate.build_final_certificate(records)
    final["certificate_sha256"] = "f" * 64

    with pytest.raises(ValueError, match="self-hash is stale"):
        checkpoints._validate_inventory(
            {
                certificate.FINAL_CERTIFICATE_FIELD: final,
            },
            "payload.question_inventory",
        )


def test_deposit_cleanup_cannot_drop_type_case_host_manifest(
    db,
    first_chapter,
    monkeypatch,
):
    records = _sealed_placement_records()
    manifest, ledger = _type_case_manifest_and_ledger(records)
    records[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD] = manifest
    final = certificate.build_final_certificate(
        records,
        type_case_qid_placement_ledger=ledger,
    )
    chapter = db.get(build_concepts.models.Chapter, first_chapter["id"])

    monkeypatch.setattr(
        build_concepts.concept_cleanup,
        "clean_concept_record",
        lambda row: {
            key: copy.deepcopy(value)
            for key, value in row.items()
            if key != certificate.TYPE_CASE_HOST_MANIFEST_FIELD
        },
    )
    monkeypatch.setattr(
        build_concepts.concept_cleanup,
        "filter_review_violations",
        lambda current, **_kwargs: current,
    )
    monkeypatch.setattr(
        build_concepts.concept_cleanup,
        "dedupe_similar_titles_chapter_wide",
        lambda current: current,
    )
    monkeypatch.setattr(
        build_concepts.concept_refiner,
        "refine_chapter",
        lambda current: current,
    )
    monkeypatch.setattr(
        build_concepts.concept_validator,
        "ensure_valid_learner_analysis",
        lambda current: current,
    )
    for name in (
        "_ensure_mastery_lines_via_api",
        "_ensure_terminal_culmination_contract",
        "_canonicalize_concept_rich_text",
    ):
        monkeypatch.setattr(
            build_concepts.generation,
            name,
            lambda current, *_args, **_kwargs: current,
        )
    monkeypatch.setattr(
        build_concepts.concept_validator,
        "validate_concept_rows",
        lambda *_args, **_kwargs: {
            "ok": True,
            "errors": [],
            "summary": {"warnings": 0},
        },
    )

    with pytest.raises(
        build_concepts.DepositValidationError,
        match=r"does not exactly cover.*missing=QINV-0001",
    ):
        build_concepts._deposit_concepts(
            db,
            chapter,
            records,
            "Post",
            "",
            mined_types={
                generation._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: ledger,
            },
            final_grounding_certificate=final,
            grounding_certificate_sink={},
        )


def test_inventory_rejects_same_lineage_divergent_type_case_host_manifest():
    records = _sealed_placement_records()
    manifest, ledger = _type_case_manifest_and_ledger(records)
    records[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD] = manifest
    generation_final = certificate.build_final_certificate(
        records,
        type_case_qid_placement_ledger=ledger,
    )

    changed = copy.deepcopy(records)
    changed_manifest = changed[0][certificate.TYPE_CASE_HOST_MANIFEST_FIELD]
    changed_manifest["placements"]["QINV-0001"][
        "assignment_unit_id"
    ] = "UNIT-0002"
    changed_manifest.pop("certificate_sha256")
    changed_manifest["certificate_sha256"] = certificate._sha256_json(
        changed_manifest
    )
    deposited_final = certificate.build_final_certificate(
        changed,
        type_case_qid_placement_ledger=ledger,
    )

    assert generation_final["lineage_sha256"] == deposited_final[
        "lineage_sha256"
    ]
    assert generation_final[
        "type_case_qid_host_placement_manifest_sha256"
    ] != deposited_final["type_case_qid_host_placement_manifest_sha256"]

    with pytest.raises(
        ValueError,
        match="does not match the exact generation-time payload",
    ):
        checkpoints._validate_inventory(
            {
                "items": [],
                "stats": {},
                "mined_types": [],
                generation._TYPE_CASE_QID_PLACEMENT_LEDGER_KEY: ledger,
                certificate.FINAL_CERTIFICATE_FIELD: generation_final,
                "deposited_grounding_certificate": deposited_final,
            },
            "payload.question_inventory",
        )


def test_validation_row_replacement_retains_rejectable_grounding_lineage(
    monkeypatch,
):
    records = _sealed_records()
    replacement = _record()
    replacement["concept_details"] = replacement["concept_details"].replace(
        "faced insecure work",
        "had secure and steadily increasing payments",
    )
    reports = iter([
        {
            "errors": [{
                "severity": "error",
                "code": "description_prefix",
                "row_index": 0,
                "field": "concept_details",
            }],
            "summary": {"warnings": 0},
        },
        {"errors": [], "summary": {"warnings": 0}},
    ])
    monkeypatch.setattr(
        generation.cv,
        "validate_concept_rows",
        lambda *_args, **_kwargs: next(reports),
    )
    monkeypatch.setattr(
        generation,
        "_openai_json",
        lambda *_args, **_kwargs: {
            "rows": [{
                "topic": replacement["topic"],
                "parent_concept": replacement["parent_concept"],
                "concept": replacement["concept_title"],
                "concept_description": replacement["concept_details"],
                "keywords": replacement["keywords"],
            }],
        },
    )

    repaired = generation._repair_records_via_api(
        records,
        meta={},
        stage="description",
        max_attempts=1,
    )

    assert repaired[0][certificate.CONCEPT_ID_FIELD] == (
        "CONCEPT-GROUND-0001"
    )
    assert repaired[0][certificate.ROW_CERTIFICATE_FIELD] == (
        records[0][certificate.ROW_CERTIFICATE_FIELD]
    )
    certificate.verify_lineage(repaired)
    with pytest.raises(
        certificate.GroundingCertificateError,
        match="source-facing Description changed after grounding",
    ):
        certificate.verify_row(repaired[0], row_index=0)


def test_final_source_claim_drift_is_regrounded_once_and_is_idempotent(
    monkeypatch,
):
    records = _sealed_records(2)
    records[1]["concept_details"] = records[1]["concept_details"].replace(
        "faced insecure work",
        "endured more precarious home-based work",
    )
    graph = {
        "source_contract_hash": SOURCE_CONTRACT,
        "blocks": [
            {"block_id": block_id}
            for block_id in sorted(ALLOWED_BLOCKS)
        ],
    }
    monkeypatch.setattr(phase3, "active_graph", lambda: graph)
    monkeypatch.setattr(
        phase3,
        "active_session",
        lambda: {"canonical": {"blocks": graph["blocks"]}},
    )
    calls: list[list[dict]] = []

    def reground(rows, **_kwargs):
        calls.append(copy.deepcopy(rows))
        assert rows[0].get(certificate.ROW_CERTIFICATE_FIELD)
        assert "_source_block_ids" not in rows[1]
        assert "_source_grounding_contract" not in rows[1]
        rows[1]["_source_block_ids"] = [
            "BLK-00156",
            "BLK-00176",
            "BLK-00181",
        ]
        rows[1]["_source_grounding_contract"] = (
            "api-verified-source-block-ids"
        )
        rows[1]["_source_grounding_version"] = "final-reground-test-1"
        return certificate.seal_records(
            rows,
            source_contract_hash=SOURCE_CONTRACT,
            semantic_topology_sha256=SEMANTIC_TOPOLOGY,
            allowed_block_ids=ALLOWED_BLOCKS,
        )

    monkeypatch.setattr(phase31, "ground_concepts", reground)

    regrounded = generation._reground_drifted_final_source_claims(records)
    rerun = generation._reground_drifted_final_source_claims(regrounded)

    assert rerun == regrounded
    assert len(calls) == 1
    certificate.build_final_certificate(regrounded)


def test_final_source_claim_reground_has_no_internal_retry(monkeypatch):
    records = _sealed_records()
    records[0]["concept_details"] = records[0]["concept_details"].replace(
        "faced insecure work",
        "received guaranteed long-term work",
    )
    graph = {
        "source_contract_hash": SOURCE_CONTRACT,
        "blocks": [{"block_id": value} for value in ALLOWED_BLOCKS],
    }
    monkeypatch.setattr(phase3, "active_graph", lambda: graph)
    monkeypatch.setattr(phase3, "active_session", lambda: {"canonical": {}})
    calls = []

    def incomplete_grounding(rows, **_kwargs):
        calls.append(1)
        return rows

    monkeypatch.setattr(phase31, "ground_concepts", incomplete_grounding)

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="did not produce one complete independently verified payload",
    ):
        generation._reground_drifted_final_source_claims(records)

    assert calls == [1]


def test_final_evidence_drift_is_eligible_for_one_reground(monkeypatch):
    records = _sealed_records()
    records[0]["_source_block_ids"] = ["BLK-00176", "BLK-00181"]
    graph = {
        "source_contract_hash": SOURCE_CONTRACT,
        "blocks": [{"block_id": value} for value in ALLOWED_BLOCKS],
    }
    monkeypatch.setattr(phase3, "active_graph", lambda: graph)
    monkeypatch.setattr(phase3, "active_session", lambda: {"canonical": {}})
    calls = []

    def reground(rows, **_kwargs):
        calls.append(1)
        assert "_source_block_ids" not in rows[0]
        rows[0]["_source_block_ids"] = [
            "BLK-00156",
            "BLK-00176",
            "BLK-00181",
        ]
        rows[0]["_source_grounding_contract"] = (
            "api-verified-source-block-ids"
        )
        rows[0]["_source_grounding_version"] = "evidence-reground-test-1"
        return certificate.seal_records(
            rows,
            source_contract_hash=SOURCE_CONTRACT,
            semantic_topology_sha256=SEMANTIC_TOPOLOGY,
            allowed_block_ids=ALLOWED_BLOCKS,
        )

    monkeypatch.setattr(phase31, "ground_concepts", reground)

    repaired = generation._reground_drifted_final_source_claims(records)

    assert calls == [1]
    certificate.build_final_certificate(repaired)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("topic", "A Different Main Topic"),
        ("parent_concept", "A Different Parent"),
        ("concept_title", "A Renamed Concept"),
        ("_semantic_topic_id", "TOPIC-9999"),
    ],
)
def test_final_reground_rejects_identity_drift_before_phase31(
    monkeypatch, field, replacement,
):
    records = _sealed_records()
    records[0][field] = replacement
    monkeypatch.setattr(
        phase3,
        "active_graph",
        lambda: {"source_contract_hash": SOURCE_CONTRACT},
    )
    calls = []
    monkeypatch.setattr(
        phase31,
        "ground_concepts",
        lambda rows, **_kwargs: calls.append(1) or rows,
    )

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="identity/topology drift",
    ):
        generation._reground_drifted_final_source_claims(records)

    assert calls == []


def test_final_reground_rejects_active_source_contract_drift(monkeypatch):
    records = _sealed_records()
    monkeypatch.setattr(
        phase3,
        "active_graph",
        lambda: {"source_contract_hash": "b" * 64},
    )
    calls = []
    monkeypatch.setattr(
        phase31,
        "ground_concepts",
        lambda rows, **_kwargs: calls.append(1) or rows,
    )

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="source/topology contract drift",
    ):
        generation._reground_drifted_final_source_claims(records)

    assert calls == []


def test_final_reground_rejects_same_source_contract_with_changed_topology(
    monkeypatch,
):
    records = _sealed_records()
    changed_graph = copy.deepcopy(SEMANTIC_GRAPH)
    changed_graph["topics"] = [{
        "topic_id": "TOPIC-9999",
        "title": "Regenerated Topic Assignment",
    }]
    changed_graph["blocks"][0]["topic_id"] = "TOPIC-9999"
    monkeypatch.setattr(phase3, "active_graph", lambda: changed_graph)
    calls = []
    monkeypatch.setattr(
        phase31,
        "ground_concepts",
        lambda rows, **_kwargs: calls.append(1) or rows,
    )

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="semantic graph/topology drift",
    ):
        generation._reground_drifted_final_source_claims(records)

    assert calls == []


def test_saved_final_reground_decision_is_not_swallowed_or_redispatched(
    monkeypatch,
):
    active_graph = _active_relationship_graph()
    row = _record()
    row[certificate.PLACEMENT_CONTRACT_FIELD] = _placement_contract(row)
    row[certificate.PLACEMENT_CONTRACT_FIELD]["teaching_order_sha256"] = (
        placement_policy.seal_teaching_order(
            active_graph,
            source_contract_hash=SOURCE_CONTRACT,
        ).sha256
    )
    row[certificate.PLACEMENT_CONTRACT_FIELD][
        "placement_certificate_sha256"
    ] = placement_policy.placement_contract_sha256(
        row[certificate.PLACEMENT_CONTRACT_FIELD]
    )
    records = certificate.seal_records(
        [row],
        source_contract_hash=SOURCE_CONTRACT,
        semantic_topology_sha256=certificate.semantic_topology_sha256(
            active_graph
        ),
        allowed_block_ids={
            str(block.get("block_id") or "")
            for block in active_graph["blocks"]
        },
    )
    final = certificate.build_final_certificate(
        records,
        require_placement_contracts=True,
    )
    checkpoint = generation._make_concept_checkpoint(
        "final_content_ready",
        records=records,
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
        final_grounding_certificate=final,
        grounding_certificate_required=True,
    )
    monkeypatch.setattr(phase3, "active_graph", lambda: active_graph)
    monkeypatch.setattr(
        generation,
        "_final_checkpoint_refresh_reasons",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        generation,
        "_run_live_concept_pre_final_stages",
        lambda *_args, **_kwargs: (
            copy.deepcopy(records),
            {"items": [], "stats": {}},
            {"types": []},
            {},
        ),
    )
    monkeypatch.setattr(
        generation,
        "_missing_source_topic_excerpts",
        lambda *_args, **_kwargs: [],
    )
    for name in (
        "_ensure_terminal_culmination_contract",
        "_canonicalize_concept_rich_text",
        "_disambiguate_certified_split_type_cases",
        "_normalize_activity_hubs_at_final_boundary",
    ):
        monkeypatch.setattr(generation, name, lambda current, *_a, **_kw: current)
    monkeypatch.setattr(
        generation,
        "_ensure_mastery_lines_via_api",
        lambda current, **_kwargs: current,
    )
    monkeypatch.setattr(
        generation,
        "_enforce_rendered_inventory_coverage",
        lambda current, *_args, **_kwargs: current,
    )
    monkeypatch.setattr(
        generation,
        "_reconcile_explicit_figure_images",
        lambda current, *_args, **_kwargs: (current, 0),
    )
    monkeypatch.setattr(
        generation,
        "_repair_final_rich_text_via_api",
        lambda current, **_kwargs: (current, False),
    )
    pending = {
        "decision_id": "phase31-saved-final-no-replay-0001",
        "context_hash": "c" * 64,
    }
    calls = []

    def pause_once(_records):
        calls.append(1)
        raise semantic_recovery.HumanDecisionRequired(pending)

    monkeypatch.setattr(
        generation,
        "_reground_drifted_final_source_claims",
        pause_once,
    )
    checkpoint_events = []

    with pytest.raises(semantic_recovery.HumanDecisionRequired):
        generation.concepts_from_mmd(
            "## The Age of Revolutions: 1830-1848\nSource text.",
            subject="Social Science",
            chapter_title="Revolutions",
            live=True,
            resume_checkpoint=checkpoint,
            checkpoint_callback=checkpoint_events.append,
        )

    assert calls == [1]
    assert not any(
        event.get("checkpoint_action") == "discard_stage"
        for event in checkpoint_events
    )


def test_final_reground_cannot_reseal_deleted_grounded_row(monkeypatch):
    records = _sealed_records(2)[1:]
    monkeypatch.setattr(
        phase3,
        "active_graph",
        lambda: {"source_contract_hash": SOURCE_CONTRACT},
    )
    calls = []
    monkeypatch.setattr(
        phase31,
        "ground_concepts",
        lambda rows, **_kwargs: calls.append(1) or rows,
    )

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="grounded concept was removed, duplicated, or reordered",
    ):
        generation._reground_drifted_final_source_claims(records)

    assert calls == []


def _repair_response_row(before: dict, concept_details: str) -> dict:
    """Return the public-fields-only row a validation repair response yields."""

    return {
        "topic": before["topic"],
        "parent_concept": before["parent_concept"],
        "concept_title": before["concept_title"],
        "concept_details": concept_details,
        "keywords": before["keywords"],
    }


def test_validation_repair_keeps_placement_authority_on_a_pedagogy_rewrite():
    before = _sealed_placement_records()[0]
    after = _repair_response_row(
        before,
        before["concept_details"].replace(
            "Students may treat home work as secure.",
            "Students may assume the contractor guaranteed a fixed payment.",
        ),
    )

    generation._carry_source_grounding_attestation(before, after)

    assert after[certificate.PLACEMENT_CONTRACT_FIELD] == (
        before[certificate.PLACEMENT_CONTRACT_FIELD]
    )
    # The rewrite touched generated pedagogy, which the contract's source claim
    # excludes, so the repaired row remains under placement authority instead
    # of failing every later resume as an uncertified escape.
    phase31._verify_preserved_placement_contracts([after], require_all=True)


def test_validation_repair_reports_a_changed_claim_not_a_missing_contract():
    before = _sealed_placement_records()[0]
    after = _repair_response_row(
        before,
        before["concept_details"].replace(
            "faced insecure work",
            "endured more precarious home-based work",
        ),
    )

    generation._carry_source_grounding_attestation(before, after)

    with pytest.raises(
        certificate.GroundingCertificateError,
        match="placement contract row 0 source claim changed before grounding",
    ):
        phase31._verify_preserved_placement_contracts(
            [after], require_all=True
        )
