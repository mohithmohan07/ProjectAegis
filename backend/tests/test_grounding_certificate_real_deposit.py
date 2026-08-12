"""Real DB/workbook coverage for the terminal grounding certificate.

The focused certificate tests prove that stale claims and evidence cannot be
verified.  This module pins the next boundary: the certificate rebuilt after
deposit-only normalization must describe the exact rows committed to SQLite
and published to the workbook, and both the input and deposited audit records
must survive the same commit.
"""
from __future__ import annotations

import copy

import openpyxl
import pytest

from app import models
from app.bulk_import import writer
from app.services import build_concepts, grounding_certificate


_SOURCE_CONTRACT_HASH = "a" * 64
_SEMANTIC_TOPOLOGY_SHA256 = "b" * 64
_SOURCE_BLOCK_ID = "BLK-00001"


def _sealed_post_records() -> list[dict]:
    records = [
        {
            "topic": "Certified Grounding Topic",
            "parent_concept": "Certified Grounding Parent",
            "concept_title": "Certified Grounding Concept",
            "concept_details": (
                "Description: Popular sovereignty locates political authority "
                "with citizens who participate in shaping public institutions.\n"
                "Achieving Mastery: Explaining how citizens exercise political "
                "authority in a representative system. // "
                "Misconception/ Error Analysis: Misconceptions: Students may "
                "believe sovereignty belongs only to a monarch.; Error "
                "Analysis: Students may omit citizens' role when explaining "
                "political authority."
            ),
            "keywords": "sovereignty, citizens",
            "_source_block_ids": [_SOURCE_BLOCK_ID],
            "_source_grounding_contract": (
                "api-verified-source-block-ids"
            ),
            "_source_grounding_version": "real-deposit-test-1",
        },
        {
            "topic": "Certified Grounding Topic",
            "parent_concept": "Culmination",
            "concept_title": "Culmination - Certified Grounding Topic",
            "concept_details": (
                "Description: Recap of Certified Grounding Concept."
            ),
            "keywords": "culmination, recap",
            "_source_block_ids": [_SOURCE_BLOCK_ID],
            "_source_grounding_contract": (
                "api-verified-source-block-ids"
            ),
            "_source_grounding_version": "real-deposit-test-1",
        },
    ]
    return grounding_certificate.seal_records(
        records,
        source_contract_hash=_SOURCE_CONTRACT_HASH,
        semantic_topology_sha256=_SEMANTIC_TOPOLOGY_SHA256,
        allowed_block_ids={_SOURCE_BLOCK_ID},
    )


def _chapter(db) -> models.Chapter:
    chapter = models.Chapter(
        chapter_code="10CBSS_CertifiedDeposit",
        board="CBSE",
        grade="10",
        subject="Social Science",
        unit="History",
        chapter_title="Certified Deposit Contract",
        chapter_display_name="Certified Deposit Contract",
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return chapter


def _audit_job(db, *, filename: str, input_certificate: dict) -> models.UploadJob:
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename=filename,
        mmd_text="## Certified Grounding Topic\nSource-grounded chapter text.",
        status="converted",
        question_inventory={"items": [], "stats": {}},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    build_concepts._store_inventory(job, {
        "question_task_inventory": {"items": [], "stats": {}},
        "mined_types": {"types": []},
        grounding_certificate.FINAL_CERTIFICATE_FIELD: input_certificate,
    })
    return job


def _public_payload(records: list[dict]) -> list[dict]:
    return [
        {
            field: copy.deepcopy(record.get(field, ""))
            for field in (
                "topic",
                "parent_concept",
                "concept_title",
                "concept_details",
                "keywords",
            )
        }
        for record in records
    ]


def _persisted_payload(db, concept_ids: list[int]) -> list[dict]:
    payload = []
    for concept_id in concept_ids:
        concept = db.get(models.Concept, concept_id)
        assert concept is not None
        payload.append({
            "topic": concept.topic.topic_title,
            "parent_concept": concept.parent_concept,
            "concept_title": concept.concept_title,
            "concept_details": concept.concept_details,
            "keywords": concept.keywords,
        })
    return payload


def _published_rows(path) -> list[dict]:
    workbook = openpyxl.load_workbook(path, data_only=False)
    sheet = workbook[writer.SHEET_BY_KIND["objective"]]
    header = [cell.value for cell in sheet[2]]
    indexes = {
        field: header.index(field)
        for field in (
            "topic_display_name",
            "concept_display_name",
            "parent_concept",
            "concept_details",
        )
    }
    return [
        {
            field: row[index]
            for field, index in indexes.items()
        }
        for row in sheet.iter_rows(min_row=3, values_only=True)
        if row[indexes["concept_display_name"]]
    ]


def test_real_certified_deposit_binds_db_audit_and_workbook_atomically(
    db,
    tmp_path,
    monkeypatch,
):
    chapter = _chapter(db)
    target = tmp_path / "certified-deposit.xlsx"
    records = _sealed_post_records()
    real_build_certificate = grounding_certificate.build_final_certificate
    input_certificate = real_build_certificate(records)
    job = _audit_job(
        db,
        filename="certified-deposit-first.mmd",
        input_certificate=input_certificate,
    )

    # Metadata generation is orthogonal to deposit integrity and is the only
    # provider-facing helper reachable from this boundary.
    monkeypatch.setattr(build_concepts.config, "BULK_IMPORT_OUTPUT", target)
    monkeypatch.setattr(
        build_concepts, "_chapter_meta_summary", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        build_concepts.generation,
        "_openai_json",
        lambda *_args, **_kwargs: pytest.fail(
            "a certified deterministic deposit must not call a provider"
        ),
    )

    certified_payloads: list[list[dict]] = []

    def capture_certificate(current_records, **certificate_options):
        certificate = real_build_certificate(
            current_records,
            **certificate_options,
        )
        certified_payloads.append(_public_payload(current_records))
        return certificate

    monkeypatch.setattr(
        grounding_certificate, "build_final_certificate", capture_certificate
    )

    created, merged, written = build_concepts._deposit_and_publish_concepts(
        db,
        chapter_id=chapter.id,
        records=records,
        pre_post="Post",
        source_book="Certified Source A",
        inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        source_text="",
        final_grounding_certificate=input_certificate,
        grounding_audit_job=job,
    )

    assert len(created) == 2
    assert merged == []
    assert target.exists()
    deposited_payload = certified_payloads[-1]
    assert _persisted_payload(db, created) == deposited_payload
    deposited_certificate = written["grounding_certificate"]
    assert deposited_certificate["public_payload_sha256"] == (
        grounding_certificate._sha256_json(deposited_payload)
    )

    db.expire_all()
    durable_job = db.get(models.UploadJob, job.id)
    assert durable_job is not None
    assert durable_job.question_inventory[
        grounding_certificate.FINAL_CERTIFICATE_FIELD
    ] == input_certificate
    assert durable_job.question_inventory[
        build_concepts._DEPOSITED_GROUNDING_CERTIFICATE_KEY
    ] == deposited_certificate
    assert written["grounding_certificate"] == durable_job.question_inventory[
        build_concepts._DEPOSITED_GROUNDING_CERTIFICATE_KEY
    ]

    workbook_rows = _published_rows(target)
    normal_payload = deposited_payload[0]
    published_normal = [
        row for row in workbook_rows
        if row["concept_display_name"] == normal_payload["concept_title"]
    ]
    assert published_normal == [{
        "topic_display_name": normal_payload["topic"],
        "concept_display_name": normal_payload["concept_title"],
        # Ships empty regardless of what the deposit authored.
        "parent_concept": None,
        "concept_details": normal_payload["concept_details"],
    }]

    # A second source for the same certified map must refresh the existing
    # concepts and workbook rows rather than creating duplicates. Its audit is
    # independently committed on the new UploadJob.
    rerun_records = _sealed_post_records()
    rerun_input_certificate = real_build_certificate(rerun_records)
    rerun_job = _audit_job(
        db,
        filename="certified-deposit-rerun.mmd",
        input_certificate=rerun_input_certificate,
    )
    created_again, merged_again, written_again = (
        build_concepts._deposit_and_publish_concepts(
            db,
            chapter_id=chapter.id,
            records=rerun_records,
            pre_post="Post",
            source_book="Certified Source B",
            inventory={"items": [], "stats": {}},
            mined_types={"types": []},
            source_text="",
            final_grounding_certificate=rerun_input_certificate,
            grounding_audit_job=rerun_job,
        )
    )

    assert created_again == []
    assert merged_again == created
    rerun_payload = certified_payloads[-1]
    assert _persisted_payload(db, merged_again) == rerun_payload
    assert written_again["grounding_certificate"] == deposited_certificate
    db.expire_all()
    durable_rerun = db.get(models.UploadJob, rerun_job.id)
    assert durable_rerun is not None
    assert durable_rerun.question_inventory[
        grounding_certificate.FINAL_CERTIFICATE_FIELD
    ] == rerun_input_certificate
    assert durable_rerun.question_inventory[
        build_concepts._DEPOSITED_GROUNDING_CERTIFICATE_KEY
    ] == written_again["grounding_certificate"]

    refreshed_rows = _published_rows(target)
    assert sum(
        row["concept_display_name"] == normal_payload["concept_title"]
        for row in refreshed_rows
    ) == 1
    refreshed = [db.get(models.Concept, concept_id) for concept_id in merged_again]
    assert all(concept is not None for concept in refreshed)
    assert all(
        set((concept.sources or "").split(", "))
        == {"Certified Source A", "Certified Source B"}
        for concept in refreshed
    )
