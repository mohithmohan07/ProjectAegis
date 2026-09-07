"""Register Q29 — the Pre lane is bound to the run it was authored for.

Owner corpus (2026-09-06): the School Bell Rings Again run shipped Self
Help's Pre Master beside its own Pre Concept file; every Pre row of the
same runs carried ``keywords`` as a stringified Python list; and a Pre
concept planned at zero shipped as an unassessed hierarchy-only row. The
three are pinned here, each as the mechanics that now record or refuse
them — never as a judgment about what a prerequisite means (Rule 1).
"""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from app import bulk_import as bi
from app import models
from app.bulk_import import layouts, writer
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_publication as publication
from app.services import concept_topology_contract as topology
from app.services import generation
from app.services import release_core
from app.services import release_qc
from app.services.phase3 import premap, prequestions, prompts

from tests.test_four_output_technicalities import (
    CONCEPT_LAYOUT,
    _chapter_with_one_concept,
)
from tests.test_pre_release_error_reporting import _job as _bare_job
from tests.test_pre_release_lane_wiring import (
    OWNER,
    _chapter_with_concepts,
    _point_at,
    _post_records,
    _pre_map,
    _pre_questions,
    _snapshot_job,
)


def _identity(chapter_id, *, code="06MSEN_SelfHelpIsth", contract="a" * 64):
    return {
        "chapter_id": chapter_id,
        "chapter_code": code,
        "source_contract_hash": contract,
        "envelope_sha256": "b" * 64,
    }


def _map_for(chapter_id, **kwargs):
    pre_map = _pre_map(**kwargs)
    pre_map[premap.RUN_IDENTITY_FIELD] = _identity(chapter_id)
    return pre_map


# --------------------------------------------------------------------------- #
# 1. The identity: stamped by premap, lifted onto the bundle, compared once
# --------------------------------------------------------------------------- #

def test_the_map_and_the_bundle_name_the_identity_by_one_literal():
    assert generation.PRE_RUN_IDENTITY_FIELD == premap.RUN_IDENTITY_FIELD


def test_run_identity_is_read_off_the_envelope_never_off_content():
    env = {
        "metadata": {"chapter_id": "1759", "chapter_code": "10CBSS_X"},
        "source_contract_hash": "c" * 64,
        "envelope_sha256": "d" * 64,
        "graph": {"topics": [{"title": "content the identity ignores"}]},
    }
    assert premap.run_identity(env) == {
        "chapter_id": 1759,
        "chapter_code": "10CBSS_X",
        "source_contract_hash": "c" * 64,
        "envelope_sha256": "d" * 64,
    }
    # An unparseable chapter id is recorded as none, never as a guess.
    assert premap.run_identity({"metadata": {"chapter_id": "x"}})[
        "chapter_id"
    ] is None


def test_the_bundle_lifts_the_maps_identity_and_stays_valid_without_one():
    with_identity = generation.phase3_pre_release_bundle(
        _map_for(12), _pre_questions(),
    )
    assert with_identity[generation.PRE_RUN_IDENTITY_FIELD] == _identity(12)
    assert generation.valid_phase3_pre_release_bundle(with_identity)

    legacy = generation.phase3_pre_release_bundle(_pre_map(), _pre_questions())
    assert generation.PRE_RUN_IDENTITY_FIELD not in legacy
    assert generation.valid_phase3_pre_release_bundle(legacy)


def test_identity_defect_is_dormant_without_a_record_and_names_a_mismatch():
    # No recorded identity: nothing to compare, the gate is dormant.
    assert generation.pre_release_identity_defect(_pre_map(), chapter_id=5) == ""
    # No expected chapter: likewise.
    assert generation.pre_release_identity_defect(
        _map_for(12), chapter_id=None,
    ) == ""
    # The same chapter, on the map and on the bundle.
    assert generation.pre_release_identity_defect(
        _map_for(12), chapter_id=12,
    ) == ""
    assert generation.pre_release_identity_defect(
        generation.phase3_pre_release_bundle(_map_for(12), _pre_questions()),
        chapter_id=12,
    ) == ""
    # Another chapter: the defect names both identities.
    defect = generation.pre_release_identity_defect(
        _map_for(12), chapter_id=15,
    )
    assert "chapter 12" in defect
    assert "06MSEN_SelfHelpIsth" in defect
    assert "this run's chapter 15" in defect


# --------------------------------------------------------------------------- #
# 2. Staging: a foreign authority is refused, recorded, and fallen through
# --------------------------------------------------------------------------- #

def test_staging_refuses_a_foreign_in_memory_authority_and_uses_the_checkpoint(db):
    """The School Bell shape, at the seam that let it through: the
    in-memory transport carried another chapter's Pre map and staging
    preferred it above everything. It is now refused, recorded, and the
    job's own checkpoint authority stages instead."""

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    own = generation.phase3_pre_release_bundle(
        _map_for(chapter.id, topic="Checkpoint map"), _pre_questions(1),
    )
    job.generation_checkpoint = generation._make_concept_checkpoint(
        "post_type_assignment",
        records=_post_records(),
        question_task_inventory={"items": []},
        mined_types={"types": []},
        method_row_snapshot=[],
        **{generation.PHASE3_PRE_RELEASE_FIELD: own},
    )
    db.commit()
    db.refresh(job)

    foreign = generation.phase3_pre_release_bundle(
        _map_for(chapter.id + 1000, topic="Self Help map"), _pre_questions(1),
    )
    release.stage_pre_release_from_run(
        db, job, target_chapter_id=chapter.id, phase3_pre_release=foreign,
    )
    db.refresh(job)
    payload = release.release_payload(job, lane=release.LANE_PRE)

    assert payload["records"][0]["topic"] == "Checkpoint map"
    refused = payload[release.PRE_AUTHORITY_DEFECTS_FIELD]
    assert len(refused) == 1
    assert "in-memory" in refused[0]
    assert f"chapter {chapter.id + 1000}" in refused[0]
    recorded = [
        issue for issue in payload["issues"]
        if issue["code"] == "pre_learning_authority_not_this_run"
    ]
    assert len(recorded) == 1
    # The rows are this run's, so the refusal flags and does not block.
    assert recorded[0]["severity"] == "warning"
    assert release.structural_defects(payload) == []


def test_staging_with_only_foreign_authorities_stages_a_diagnostic_lane(
    db, monkeypatch, tmp_path,
):
    """Every authority the chain offers belongs to another chapter: the
    lane stages EMPTY and Diagnostic with each refusal recorded, and the
    foreign questions sidecar is never read as this run's."""

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    _point_at(monkeypatch, tmp_path)
    (tmp_path / release.PRE_MAP_SNAPSHOT).write_text(
        json.dumps(_map_for(chapter.id + 1000, topic="Sidecar map")),
        encoding="utf-8",
    )
    (tmp_path / release.PRE_QUESTIONS_SNAPSHOT).write_text(
        json.dumps({
            **_pre_questions(1),
            "refused": "FOREIGN-QUESTIONS-REFUSAL",
        }),
        encoding="utf-8",
    )
    foreign = generation.phase3_pre_release_bundle(
        _map_for(chapter.id + 1000, topic="Self Help map"), _pre_questions(1),
    )

    staged = release.stage_pre_release_from_run(
        db, job, target_chapter_id=chapter.id, phase3_pre_release=foreign,
    )
    assert staged is not None
    db.refresh(job)
    payload = release.release_payload(job, lane=release.LANE_PRE)

    assert payload["records"] == []
    refused = payload[release.PRE_AUTHORITY_DEFECTS_FIELD]
    assert len(refused) == 2
    assert any("in-memory" in defect for defect in refused)
    assert any(release.PRE_MAP_SNAPSHOT in defect for defect in refused)
    assert all(
        issue["severity"] == "error"
        for issue in payload["issues"]
        if issue["code"] == "pre_learning_authority_not_this_run"
    )
    defects = release.structural_defects(payload)
    assert any("belongs to another run" in defect for defect in defects)
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE
    # Nothing of the foreign questions was transcribed as ours.
    assert "FOREIGN-QUESTIONS-REFUSAL" not in json.dumps(payload)


# --------------------------------------------------------------------------- #
# 3. The sidecar restore: the job's directory, checked against its envelope
# --------------------------------------------------------------------------- #

def _write_sidecars(directory, *, topic, contract, envelope_contract):
    directory.mkdir(parents=True, exist_ok=True)
    pre_map = _pre_map(topic=topic)
    pre_map[premap.RUN_IDENTITY_FIELD] = _identity(7, contract=contract)
    (directory / topology.PREMAP_SNAPSHOT).write_text(
        json.dumps(pre_map), encoding="utf-8",
    )
    (directory / topology.PREQUESTIONS_SNAPSHOT).write_text(
        json.dumps(_pre_questions(1)), encoding="utf-8",
    )
    (directory / topology.PHASE3_ENVELOPE_SNAPSHOT).write_text(
        json.dumps({
            "envelope": {"source_contract_hash": envelope_contract},
        }),
        encoding="utf-8",
    )


def test_restored_pre_release_reads_the_given_directory_not_the_session(
    tmp_path, monkeypatch,
):
    from app.services import canonical_source_phase3 as phase3_core

    mine = tmp_path / "mine"
    other = tmp_path / "other"
    _write_sidecars(mine, topic="mine", contract="a" * 64,
                    envelope_contract="a" * 64)
    _write_sidecars(other, topic="other", contract="e" * 64,
                    envelope_contract="e" * 64)
    monkeypatch.setattr(
        phase3_core, "active_session", lambda: {"artifact_dir": str(other)},
    )

    restored, defects = topology.restored_pre_release(artifact_dir=mine)
    assert defects == []
    assert restored["pre_map"]["rows"][0]["topic"] == "mine"

    # The session is the fallback for a caller holding no job — and only
    # then.
    restored, defects = topology.restored_pre_release()
    assert defects == []
    assert restored["pre_map"]["rows"][0]["topic"] == "other"


def test_restored_pre_release_refuses_a_map_sealed_on_another_source(tmp_path):
    stale = tmp_path / "stale"
    _write_sidecars(stale, topic="stale", contract="a" * 64,
                    envelope_contract="f" * 64)

    restored, defects = topology.restored_pre_release(artifact_dir=stale)
    assert restored is None
    assert len(defects) == 1
    assert topology.PREMAP_SNAPSHOT in defects[0]
    assert "another source" in defects[0]


def test_restored_pre_release_is_dormant_for_a_map_that_recorded_nothing(
    tmp_path,
):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / topology.PREMAP_SNAPSHOT).write_text(
        json.dumps(_pre_map(topic="legacy")), encoding="utf-8",
    )
    (legacy / topology.PREQUESTIONS_SNAPSHOT).write_text(
        json.dumps(_pre_questions(1)), encoding="utf-8",
    )
    (legacy / topology.PHASE3_ENVELOPE_SNAPSHOT).write_text(
        json.dumps({"envelope": {"source_contract_hash": "f" * 64}}),
        encoding="utf-8",
    )
    restored, defects = topology.restored_pre_release(artifact_dir=legacy)
    assert defects == []
    assert restored["pre_map"]["rows"][0]["topic"] == "legacy"


# --------------------------------------------------------------------------- #
# 4. Output 02's manifest entry and the publication receipt: lineage
# --------------------------------------------------------------------------- #

def _live_master_row(db, job, *, lane, provider_identity):
    row = models.AssessmentRelease(
        release_uid=f"REL-{uuid.uuid4().hex[:8]}",
        version=1,
        owner_sub=OWNER,
        job_id=job.id,
        lane=lane,
        layout_id=release_core.layout_id(),
        state="ready_for_upload",
        publication={"manifest": {"master_xlsx": "master.xlsx"}},
        provider_identity=provider_identity,
    )
    db.add(row)
    db.commit()
    return row


def test_master_entry_disables_a_master_frozen_from_another_staging(db):
    """PRE-C-01: a live Pre Master frozen from an EARLIER staging of the
    lane must not be served enabled beside this run's Output 01."""

    job = _bare_job(db)
    release.stage_pre_release(
        db, job, target_chapter_id=1,
        pre_map={"rows": []}, pre_questions={"questions": {}},
    )
    db.refresh(job)
    staged_uid = release.release_payload(job, lane=release.LANE_PRE)[
        release.STAGED_RELEASE_UID_FIELD
    ]
    assert staged_uid
    row = _live_master_row(
        db, job, lane=release.LANE_PRE,
        provider_identity={"staged_release_uid": "an-earlier-staging"},
    )

    entry = release_files.master_entry(job, lane=release.LANE_PRE)
    assert entry["disabled"] is True
    assert entry["disabled_reason"] == release_files.MASTER_STALE_FOR_RUN
    assert entry["download_url"] == ""

    # The Master frozen from THIS staging serves.
    row.provider_identity = {"staged_release_uid": staged_uid}
    db.commit()
    entry = release_files.master_entry(job, lane=release.LANE_PRE)
    assert not entry.get("disabled")
    assert entry["download_url"]

    # A row frozen before the uid existed leaves nothing to compare.
    row.provider_identity = {}
    db.commit()
    entry = release_files.master_entry(job, lane=release.LANE_PRE)
    assert not entry.get("disabled")


def test_the_publication_receipt_records_a_live_masters_foreign_lineage(db):
    """The one gate that could notice used to ``continue`` in silence."""
    from app.services import assessment_release_snapshot as snapshot_mod

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    payload = release.release_payload(job, lane=release.LANE_POST)
    current_uid = payload[release.STAGED_RELEASE_UID_FIELD]
    sealed = snapshot_mod.source_release_sha256(payload)

    def _frozen(uid):
        row = models.AssessmentRelease(
            release_uid=f"REL-{uuid.uuid4().hex[:8]}",
            version=1,
            owner_sub=OWNER,
            job_id=job.id,
            lane=release.LANE_POST,
            layout_id=release_core.layout_id(),
            state="ready_for_upload",
            provider_identity={"staged_release_uid": uid},
            concept_snapshot={"source_concept_release_sha256": sealed},
        )
        db.add(row)
        db.commit()
        return row

    earlier = _frozen("an-earlier-staging")
    notes = publication._refuse_a_moved_seal(
        db, job, payload, release.LANE_POST,
    )
    assert len(notes) == 1
    assert earlier.release_uid in notes[0]
    assert "EARLIER staging" in notes[0]
    assert current_uid in notes[0]

    # The Master frozen from this draft supersedes the earlier one (the
    # real lifecycle): nothing to note, and the seal is still compared
    # (it matches here).
    earlier.state = "superseded"
    _frozen(current_uid)
    assert publication._refuse_a_moved_seal(
        db, job, payload, release.LANE_POST,
    ) == []


# --------------------------------------------------------------------------- #
# 5. keywords — contract §16 on the Pre rows, and the read-back that sees it
# --------------------------------------------------------------------------- #

def test_premap_joins_a_list_valued_keywords_answer_on_the_house_delimiter():
    assert premap.keywords_cell(
        ["connected verse", " reading fluency ", ""]
    ) == "connected verse | reading fluency"
    assert premap.keywords_cell("count | number") == "count | number"
    assert premap.keywords_cell(None) == ""
    # The prompt says the shape, in the same words the Post prompt uses.
    assert "keywords is ONE string" in prompts.PREMAP_SYSTEM
    assert '" | "' in prompts.PREMAP_SYSTEM


def test_list_token_defects_names_a_bracketed_literal_and_nothing_else():
    assert bi.list_token_defects("['connected verse', 'reading fluency']")
    assert bi.list_token_defects('["a", "b"]')
    assert bi.list_token_defects("a | b") == []
    assert bi.list_token_defects("[Katex] x^2 [/Katex] | b") == []
    assert bi.list_token_defects("Story Setting, Events (06MSEN_X) | b") == []
    assert bi.list_token_defects("") == []


def test_concept_file_read_back_names_a_serialized_list_cell(db):
    concept = _chapter_with_one_concept(db)
    concept.keywords = "['count', 'number']"
    db.commit()
    data = writer.write_concepts_workbook(
        db, [concept.id], layout_id=CONCEPT_LAYOUT, publication="Balbharati",
    )
    decisions = writer._validate_concepts_workbook_bytes(
        data,
        [concept],
        writer.ConceptExportScope([concept]),
        exact_rows=True,
        sheet_layout=layouts.sheet(CONCEPT_LAYOUT, "objective"),
    )
    named = [
        d for d in decisions if d["code"] == writer.READBACK_LIST_CELL_DEFECT
    ]
    assert len(named) == 1
    assert any("keywords" in f for f in named[0]["findings"]), named

    concept.keywords = "count | number"
    db.commit()
    data = writer.write_concepts_workbook(
        db, [concept.id], layout_id=CONCEPT_LAYOUT, publication="Balbharati",
    )
    assert writer._validate_concepts_workbook_bytes(
        data,
        [concept],
        writer.ConceptExportScope([concept]),
        exact_rows=True,
        sheet_layout=layouts.sheet(CONCEPT_LAYOUT, "objective"),
    ) == []


# --------------------------------------------------------------------------- #
# 6. §8.6 — a Pre concept with no routed question is a blocking QC finding
# --------------------------------------------------------------------------- #

def _pre_row(concept_id, *, questions):
    return {
        "topic": "Counting",
        "concept_title": f"Concept {concept_id}",
        "_pre_concept_id": concept_id,
        release.PRE_ROW_GENERATED_QUESTIONS_FIELD: list(questions),
    }


def _qc_payload(records, *, lane=release.LANE_PRE, plans=None, blocks=None):
    return {
        "records": records,
        "issues": [],
        "type_case_rows": [],
        release.RELEASE_LANE_FIELD: lane,
        "directory_metadata": {"chapter_duration": "40 minutes"},
        "source_book": "NCERT",
        "pre_question_plans": plans or {},
        "pre_question_blocks": blocks or {},
    }


def test_release_qc_blocks_a_pre_concept_with_no_generated_question():
    issues, blocking = release_qc.audit(_qc_payload(
        [
            _pre_row("PRC-0001", questions=[]),
            _pre_row("PRC-0002", questions=["PRC-0002 PRQ-0001"]),
        ],
        plans={
            "PRC-0001": {
                "total": 0, "split": [],
                "rationale": "the learner either counts or does not",
            },
            "PRC-0002": {"total": 1, "split": [], "rationale": "one check"},
        },
    ))
    named = [i for i in issues if i["code"] == release_qc.PRE_CONCEPT_UNASSESSED]
    assert len(named) == 1
    assert named[0]["unit_id"] == "PRC-0001"
    assert named[0]["severity"] == "error"
    assert "request to drop the concept" in named[0]["message"]
    assert "the learner either counts or does not" in named[0]["message"]
    assert "§8.6" in named[0]["message"]
    assert len(blocking) == 1
    assert blocking[0].startswith(release_qc.PRE_CONCEPT_UNASSESSED)


def test_release_qc_transcribes_a_recorded_authoring_block():
    issues, blocking = release_qc.audit(_qc_payload(
        [_pre_row("PRC-0001", questions=[])],
        plans={"PRC-0001": {"total": 2, "split": [], "rationale": "two"}},
        blocks={"PRC-0001": "the Fixer could not satisfy the checker"},
    ))
    named = [i for i in issues if i["code"] == release_qc.PRE_CONCEPT_UNASSESSED]
    assert len(named) == 1
    assert "authoring was blocked" in named[0]["message"]
    assert "the Fixer could not satisfy the checker" in named[0]["message"]
    assert len(blocking) == 1


def test_release_qc_leaves_the_post_lane_and_assessed_pre_rows_alone():
    issues, blocking = release_qc.audit(_qc_payload(
        [_pre_row("PRC-0001", questions=[])], lane=release.LANE_POST,
    ))
    assert not any(
        i["code"] == release_qc.PRE_CONCEPT_UNASSESSED for i in issues
    )
    issues, blocking = release_qc.audit(_qc_payload(
        [_pre_row("PRC-0001", questions=["PRC-0001 PRQ-0001"])],
    ))
    assert issues == []
    assert blocking == []


def test_a_zero_plan_makes_the_staged_pre_release_diagnostic(db, client):
    """End to end: the model planned zero, the concept staged without a
    question, the database write is refused and every download ships."""

    chapter = _chapter_with_concepts(db)
    job = _snapshot_job(db, chapter)
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id,
        pre_map=_pre_map(),
        pre_questions={
            "plans": {
                "PRC-0001": {
                    "total": 0, "split": [],
                    "rationale": "nothing here is worth verifying",
                },
            },
            "questions": {},
            "blocked": {},
            "review_flags": {},
            "decision_flags": {},
        },
        reason="zero plan",
    )
    db.refresh(job)
    payload = release.release_payload(job, lane=release.LANE_PRE)

    defects = release.structural_defects(payload)
    assert any(release_qc.PRE_CONCEPT_UNASSESSED in d for d in defects)
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE
    assert any(
        issue["code"] == release_qc.PRE_CONCEPT_UNASSESSED
        and "nothing here is worth verifying" in issue["message"]
        for issue in payload["issues"]
    )
    with pytest.raises(ValueError):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="pre",
        )
    for name in ("release-bulk-import.xlsx", "release.json"):
        response = client.get(
            f"/build-concepts/uploads/{job.id}/{name}?lane=pre")
        assert response.status_code == 200, name


def test_the_plan_rules_ask_for_a_drop_rather_than_licensing_zero():
    prose = prequestions._plan_rules("")
    assert "request to DROP the concept" in prose
    assert "§8.6" in prose
    assert "at least one diagnostic question" in prose
    assert "plan zero only when that is true" not in prose
    assert "never ships the concept as if it were assessed" in prose
