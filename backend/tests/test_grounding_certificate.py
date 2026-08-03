"""Focused integrity tests for final concept-grounding certificates."""

from __future__ import annotations

import copy

import pytest

from app.services import generation
from app.services import grounding_certificate as certificate
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase31_grounding_contract as phase31
from app.services import checkpoints
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
    }
    assert final["evidence"][0]["row_identity_sha256"] == (
        records[0][certificate.ROW_IDENTITY_FIELD]
    )


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
    records = _sealed_records()
    final = certificate.build_final_certificate(records)
    checkpoint = generation._make_concept_checkpoint(
        "final_content_ready",
        records=records,
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
        final_grounding_certificate=final,
        grounding_certificate_required=True,
    )
    monkeypatch.setattr(phase3, "active_graph", lambda: SEMANTIC_GRAPH)
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
