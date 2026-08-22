"""S11 — the QC checklist and the polarity inversion (spec-step8 T9/T10).

The named S11 regressions: the closed blocking set at both gates, judgment
codes shipping flagged, the writer's content judgments gone, the read-back
routing to the Fixer, the T9 identity codes blocking the upload while every
download ships, the coverage ledger producing release issues, and the audit
running at staging for both lanes without ever raising.
"""
from __future__ import annotations

import copy
import inspect
import io
import json
import zipfile

import openpyxl
import pytest

from app import models
from app.bulk_import import writer
from app.services import build_concepts
from app.services import build_concepts_release as release
from app.services import build_concepts_release_publication as publication
from app.services import generation as g
from app.services import release_qc

from tests.test_build_concepts_release import (
    _inventory,
    _job,
    _mined_types,
    _rendered_records,
)

OWNER = "local:default"


def _strict_row(*, title="Electric Current Relationship",
                misconception=(
                    "Students may believe current is consumed as it moves "
                    "through a circuit."
                )):
    return {
        "topic": "Electric Current",
        "parent_concept": "Circuit Quantities",
        "concept_title": title,
        "concept_details": (
            "Description: Electric current relates charge flow to time."
            "\nAchieving Mastery: Applying the current relationship."
            " // Misconception/ Error Analysis: Misconceptions: "
            f"{misconception}; Error Analysis: Students may swap the "
            "current and resistance values while substituting into the "
            "relationship."
        ),
        "keywords": "current",
        "_aegis_analysis_allotments": ["LA-0001"],
    }


def _culmination():
    return {
        "topic": "Electric Current",
        "parent_concept": "Culmination",
        "concept_title": "Culmination - Electric Current Relationship",
        "concept_details": "Description: Recap of the relationship.",
        "keywords": "",
    }


# --------------------------------------------------------------------------- #
# 1. The blocking set is a pinned literal, shared by BOTH gates
# --------------------------------------------------------------------------- #

def test_the_blocking_set_is_exactly_the_closed_list():
    """T10-2: an explicit ALLOW-list, written literally here so "just add
    one more code to the fatal set" is no longer a quiet one-line change.
    """
    assert g._BLOCKING_CODES == frozenset({"required", "required_parent"})
    assert g._FIXER_UNACCEPTABLE_CODES == frozenset(
        {"required", "required_parent"})
    # The fatal FAMILY keeps its name and its classify role.
    assert g._BLOCKING_CODES < g._FATAL_CODES


def test_the_final_gate_uses_the_same_blocking_set_as_the_deposit_gate():
    """T10-1: Cluster D's edit list touched only the deposit gate, which
    would have left 41 codes halting the run at the final gate after the
    "polarity inversion" shipped. Both gates split through the ONE helper.
    """
    def _code_lines(source):
        return [line.split("#")[0] for line in source.splitlines()]

    assert any(
        "_split_blocking(" in line
        for line in _code_lines(inspect.getsource(g._validate_final_or_raise))
    )
    assert any(
        "generation._split_blocking(" in line
        for line in _code_lines(
            inspect.getsource(build_concepts._deposit_concepts))
    )
    assert any(
        "_BLOCKING_CODES" in line
        for line in _code_lines(inspect.getsource(g._split_blocking))
    )


# --------------------------------------------------------------------------- #
# 2. Judgment codes ship flagged; identity mechanics still fail closed
# --------------------------------------------------------------------------- #

def test_a_generic_misconception_ships_flagged_not_blocked(db):
    rows = [
        _strict_row(
            misconception="Students may misunderstand the concept."),
        _culmination(),
    ]
    g._validate_final_or_raise(rows)
    assert any(
        "validation: generic_misconception" in flag
        for flag in rows[0].get("review_flags") or []
    )

    # The release issue exists: the audit transcribes the recorded flag.
    job, chapter = _job(db)
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=rows, inventory={"items": [], "stats": {}},
    )
    payload = release.release_payload(job)
    assert any(
        issue["code"] == release_qc.FLAGGED_VALIDATION_FINDING
        and "generic_misconception" in issue["message"]
        for issue in payload["issues"]
    )
    assert release.structural_defects(payload) == []


def test_two_same_titled_concepts_in_one_topic_do_not_kill_the_run():
    """T10-3: [measured pre-S11] ``duplicate_topic_concept`` could not be
    cleared by any legal Fixer move, so a shape a real chapter produces
    killed the run. Both duplicate codes ship flagged now.
    """
    rows = [_strict_row(), _strict_row(), _culmination()]
    g._validate_final_or_raise(rows)
    flags = [
        flag for row in rows for flag in row.get("review_flags") or []
    ]
    # Step 11 retired chapter-wide duplicate_title; the topic-scoped
    # identity code is the one that survives and it ships flagged.
    assert any(
        "validation: duplicate_topic_concept" in flag for flag in flags
    )
    assert not any(
        "duplicate_title" in flag and "duplicate_topic_concept" not in flag
        for flag in flags
    )


# --------------------------------------------------------------------------- #
# 3. The writer no longer judges content; the read-back records, not raises
# --------------------------------------------------------------------------- #

def _own_chapter_with_concepts(db, code, rows):
    chapter = models.Chapter(
        chapter_code=code, board="CBSE", grade="07", subject="Science",
        unit="Unit", chapter_title=f"S11 {code}",
        chapter_display_name=f"S11 {code}",
    )
    db.add(chapter)
    db.flush()
    topic = models.Topic(
        chapter_id=chapter.id, topic_title="Writer Topic",
        topic_display_name="Writer Topic", pre_post_learning="Post",
        topic_description="A topic description.", source_order=1,
    )
    db.add(topic)
    db.flush()
    concepts = []
    for order, (title, details) in enumerate(rows, start=1):
        concept = models.Concept(
            topic_id=topic.id, concept_title=title,
            concept_display_name=title, concept_details=details,
            keywords="", source_order=order,
        )
        db.add(concept)
        concepts.append(concept)
    db.flush()
    return chapter, topic, concepts


def test_the_writer_no_longer_judges_a_culmination_or_a_hub(db, tmp_path):
    """T10-4: a 200-char Culmination title and a hub whose gist repeats
    its visible marker were [measured] pure content judgments (a keyword
    classifier, a length threshold, a marker-repeat regex) whose firing no
    test anywhere pinned. ``append_concepts`` now succeeds and writes both
    rows — the first test ever to drive that function through this shape.
    """
    long_title = "Culmination - " + "Consolidating Every Idea " * 8
    assert len(long_title) > 120
    _chapter, _topic, concepts = _own_chapter_with_concepts(
        db, "07CBSC_S11Writer",
        [
            (long_title.strip(), "Description: The consolidation."),
            (
                "Hub Host Concept",
                "Description: Activity — Water Cycle Walk: Water Cycle "
                "Walk around the school garden.",
            ),
        ],
    )
    result = writer.append_concepts(
        db, tmp_path / "wb.xlsx", [c.id for c in concepts])
    assert result["written"] == 2
    assert (tmp_path / "wb.xlsx").exists()


def test_the_read_back_identity_check_routes_to_the_fixer_and_the_run_completes(
    db, tmp_path,
):
    """T10-4: the surviving mechanical read-back KEEPS its location and its
    comparison; what changed is the consequence — one recorded, flagged,
    content-addressed decision and the workbook still lands, instead of a
    raise nothing caught.
    """
    _chapter, topic, concepts = _own_chapter_with_concepts(
        db, "07CBSC_S11ReadBack",
        [("Stable Concept", "Description: The stable claim.")],
    )
    data = writer.write_concepts_workbook(db, [concepts[0].id])
    assert data.startswith(b"PK")

    # The DB contract moves under the already-serialized artifact: the
    # read-back sees the drift and RECORDS it — one content-addressed
    # decision — instead of raising.
    topic.topic_description = "A reworded topic description."
    db.flush()
    decisions = writer._validate_concepts_workbook_bytes(
        data,
        concepts,
        writer.ConceptExportScope(concepts),
        exact_rows=True,
    )
    assert len(decisions) == 1
    mismatch = decisions[0]
    assert mismatch["code"] == writer.READBACK_TOPOLOGY_MISMATCH
    assert mismatch["review_flag"] is True
    assert mismatch["decision_sha256"]
    assert any(
        "stale topic_description" in finding
        for finding in mismatch["findings"]
    )

    # And the export path SHIPS despite the finding — the raise nothing
    # caught used to cost the download (Rule E).
    shipped = writer.write_concepts_workbook(db, [concepts[0].id])
    assert shipped.startswith(b"PK")


# --------------------------------------------------------------------------- #
# 4. T9: identity corruption blocks the WRITE; meaning never does
# --------------------------------------------------------------------------- #

def _sound_records():
    return [{
        "topic": "Sound Topic",
        "concept_title": "Sound Concept",
        "concept_details": "Description: A sound row.",
        "keywords": "sound",
    }]


def test_a_duplicate_qid_assignment_blocks_the_upload_and_ships_every_download(
    db, client,
):
    job, chapter = _job(db)
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=_sound_records(),
        inventory=_inventory(),
        mined_types=_mined_types(duplicate=True),
    )
    payload = release.release_payload(job)
    assert any(
        issue["code"] == "duplicate_qid_assignment"
        for issue in payload["issues"]
    )
    defects = release.structural_defects(payload)
    assert any("duplicate_qid_assignment" in defect for defect in defects)
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE

    for name in (
        "release-bulk-import.xlsx", "release.xlsx",
        "diagnostics.zip", "release.json",
    ):
        response = client.get(f"/build-concepts/uploads/{job.id}/{name}")
        assert response.status_code == 200, name
        assert response.content, name

    with pytest.raises(ValueError, match="duplicate_qid_assignment"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")


def test_an_unassigned_inventory_qid_ships_flagged_and_publishes(db):
    """T9-2's negative control — the line between corruption and coverage:
    ``inventory_qids - assigned_qids`` is a coverage verdict, R4 says
    Placed OR Flagged, and this issue IS the flag.
    """
    job, chapter = _job(db)
    inventory = _inventory()
    inventory["items"].append(
        {"qid": "QINV-0009", "raw_task": "An unplaced extra question?"})
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=_rendered_records(),
        inventory=inventory,
        mined_types=_mined_types(),
    )
    payload = release.release_payload(job)
    assert any(
        issue["code"] == "unassigned_inventory_qid"
        and "QINV-0009" in (issue.get("qids") or [])
        for issue in payload["issues"]
    )
    assert release.structural_defects(payload) == []
    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert result["database_uploaded"] is True


def test_a_reviewer_reword_of_concept_details_never_blocks_the_upload():
    """T9-2: the two text-derived ``case_uniqueness_*`` codes stay flags —
    a reviewer reword must never refuse the upload on a deterministic
    prose comparison (§7:577) — while the identity codes in the closed set
    block.
    """
    base = {
        "records": [dict(row) for row in _sound_records()],
        "staged_row_defects": [],
        "issues": [],
    }
    reworded = copy.deepcopy(base)
    reworded["issues"] = [
        {"code": "case_uniqueness_qid_render_count_mismatch",
         "severity": "error", "message": "reword moved a rendered count"},
        {"code": "case_uniqueness_example_less_case_shell",
         "severity": "error", "message": "reword reshaped a case shell"},
    ]
    assert release.structural_defects(reworded) == []

    corrupted = copy.deepcopy(base)
    corrupted["issues"] = [
        {"code": "duplicate_qid_assignment", "severity": "error",
         "message": "QINV-0001 is assigned twice"},
    ]
    assert any(
        "duplicate_qid_assignment" in defect
        for defect in release.structural_defects(corrupted)
    )


def test_a_malformed_type_catalog_is_a_named_defect_not_a_swallowed_exception(
    monkeypatch,
):
    """T9-3: ``except Exception: cases = {}`` let a malformed catalog
    silently disable the uniqueness gate — exactly where an unusual source
    lands. It is a NAMED defect now, and the closed set blocks on it.
    """
    from app.services.phase3 import assemble as p3_assemble

    def boom(_payload):
        raise ValueError("catalog will not parse")

    monkeypatch.setattr(p3_assemble, "_type_catalog", boom)
    issues = release._case_uniqueness_issues(
        _sound_records(), {"types": []})
    named = [
        issue for issue in issues
        if issue["code"] == "type_catalog_unreadable"
    ]
    assert len(named) == 1
    assert "catalog will not parse" in named[0]["message"]
    assert named[0]["severity"] == "error"

    payload = {
        "records": [dict(row) for row in _sound_records()],
        "staged_row_defects": [],
        "issues": named,
    }
    assert any(
        "type_catalog_unreadable" in defect
        for defect in release.structural_defects(payload)
    )


# --------------------------------------------------------------------------- #
# 5. The coverage ledger produces release issues (item 22 — R4's net)
# --------------------------------------------------------------------------- #

def test_the_coverage_ledger_produces_release_issues():
    payload = {
        "records": [],
        "issues": [],
        "type_case_rows": [],
        "question_task_inventory": {
            "items": [
                {"qid": "QINV-0100", "source_kind": "exercise",
                 "raw_task": "Where did this question go?"},
            ],
        },
        "learning_kind": "post",
    }
    issues, blocking = release_qc.audit(payload)
    unaccounted = [
        issue for issue in issues
        if issue["code"] == release_qc.COVERAGE_UNACCOUNTED
    ]
    assert len(unaccounted) == 1
    assert unaccounted[0]["severity"] == "error"
    assert "QINV-0100" in unaccounted[0]["message"]
    assert len(blocking) == 1
    assert "QINV-0100" in blocking[0]

    # Placed OR Flagged: a recorded issue naming the QID IS the flag, so
    # the same item with its ``unassigned_inventory_qid`` record produces
    # neither a duplicate issue nor a blocking finding.
    flagged = copy.deepcopy(payload)
    flagged["issues"] = [{
        "code": "unassigned_inventory_qid", "severity": "error",
        "message": "QINV-0100 has no Type/Case assignment.",
        "qids": ["QINV-0100"],
    }]
    issues, blocking = release_qc.audit(flagged)
    assert not [
        issue for issue in issues
        if issue["code"] == release_qc.COVERAGE_UNACCOUNTED
    ]
    assert blocking == []

    # A QID placed in the staged Type/Case rows is Placed.
    placed = copy.deepcopy(payload)
    placed["type_case_rows"] = [{
        "row_kind": "example", "example_qid": "QINV-0100",
        "example_prompt": "Where did this question go?",
    }]
    issues, blocking = release_qc.audit(placed)
    assert issues == []
    assert blocking == []


# --------------------------------------------------------------------------- #
# 6. The audit runs at staging for both lanes and never raises (T10-0)
# --------------------------------------------------------------------------- #

def test_the_audit_runs_at_staging_for_both_lanes_and_never_raises(
    db, client, monkeypatch,
):
    from tests.test_pre_release_lane_wiring import _both_lanes_job

    injected_issue = {
        "code": "release_qc_injected", "severity": "error",
        "message": "one checklist item reported a blocking finding",
        "qids": [], "block_ids": [],
    }

    def fake_audit(_payload, **_kwargs):
        return [dict(injected_issue)], [
            "release_qc_injected: one checklist item reported a blocking "
            "finding"
        ]

    monkeypatch.setattr(release_qc, "audit", fake_audit)
    chapter = models.Chapter(
        chapter_code="07CBSC_S11Audit", board="CBSE", grade="07",
        subject="Science", unit="Unit", chapter_title="S11 Audit",
        chapter_display_name="S11 Audit",
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    job = _both_lanes_job(db, chapter)

    for lane in ("post", "pre"):
        payload = release.release_payload(job, lane=lane)
        assert any(
            issue["code"] == "release_qc_injected"
            for issue in payload["issues"]
        ), lane
        # Round 9: the blocking finding rides its OWN key — never the
        # input-snapshot transport, whose reader would stamp a false
        # "could not be read" preamble (and mint a spurious
        # ``pre_learning_snapshot_unreadable`` issue on the Pre lane).
        assert any(
            "release_qc_injected" in defect
            for defect in payload[release.QC_BLOCKING_FIELD]
        ), lane
        assert payload["snapshot_defects"] == [], lane
        assert not any(
            issue["code"] == "pre_learning_snapshot_unreadable"
            for issue in payload["issues"]
        ), lane
        defects = release.structural_defects(payload)
        assert any(
            "the release QC audit recorded a blocking finding" in defect
            and "release_qc_injected" in defect
            for defect in defects
        ), lane
        assert not any(
            "an input snapshot could not be read" in defect
            for defect in defects
        ), lane
        assert release.release_state(payload) == (
            release.DIAGNOSTIC_RELEASE
        ), lane
        for name in (
            "release-bulk-import.xlsx", "release.xlsx",
            "diagnostics.zip", "release.json",
        ):
            response = client.get(
                f"/build-concepts/uploads/{job.id}/{name}?lane={lane}")
            assert response.status_code == 200, (lane, name)
            assert response.content, (lane, name)

    # The REAL audit never raises: a poisoned payload comes back as named
    # AND BLOCKING findings (Round 9) — a net that could not run to
    # completion must not certify the release.
    monkeypatch.undo()
    issues, blocking = release_qc.audit({"records": object()})
    assert issues
    assert all(
        issue["code"] == release_qc.AUDIT_UNAVAILABLE for issue in issues
    )
    assert blocking
    assert all(release_qc.AUDIT_UNAVAILABLE in entry for entry in blocking)


def test_a_failed_audit_pass_cannot_void_an_earlier_blocking_finding():
    """Round 9 (T9-3): one try around all three passes [measured] let a
    later pass's crash discard a blocking finding the coverage pass had
    already computed. Each pass is isolated now, and the failed pass adds
    its own blocking finding beside the survivor.
    """
    payload = {
        "records": [{
            "topic": "Sound Topic",
            "concept_title": "Sound Concept",
            "concept_details": "Description: A sound row.",
            # Truthy non-iterable: poisons the needed-for pass only.
            "_aegis_pre_related_concepts_unresolved": 7,
        }],
        "issues": [],
        "type_case_rows": [],
        "question_task_inventory": {
            "items": [{
                "qid": "QINV-0200", "source_kind": "exercise",
                "raw_task": "Where did this one go?",
            }],
        },
        "learning_kind": "post",
    }
    issues, blocking = release_qc.audit(payload)
    assert any("QINV-0200" in entry for entry in blocking)
    assert any(release_qc.AUDIT_UNAVAILABLE in entry for entry in blocking)
    assert any(
        issue["code"] == release_qc.COVERAGE_UNACCOUNTED for issue in issues
    )
    assert any(
        issue["code"] == release_qc.AUDIT_UNAVAILABLE for issue in issues
    )


def test_the_pre_lane_skips_the_post_inventory_coverage():
    """§6.6: the chapter's question inventory is the Post lane's debt;
    charging it to the Pre lane would block a lane that owes nothing.
    """
    payload = {
        "records": [],
        "issues": [],
        "type_case_rows": [],
        "question_task_inventory": {
            "items": [{
                "qid": "QINV-0300", "source_kind": "exercise",
                "raw_task": "A Post-lane question.",
            }],
        },
        release.RELEASE_LANE_FIELD: release.LANE_PRE,
    }
    issues, blocking = release_qc.audit(payload)
    assert issues == []
    assert blocking == []


def test_stage_release_carries_an_explicit_snapshot_defect(db):
    """The V2 parity parameter, exercised end to end: an input-artifact
    finding a Post caller records rides ``snapshot_defects`` and blocks
    under the input-snapshot reader — whose sentence is TRUE for it.
    """
    job, chapter = _job(db)
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=_sound_records(), inventory={"items": [], "stats": {}},
        snapshot_defects=["the settled-rows snapshot failed its checksum"],
    )
    payload = release.release_payload(job)
    assert payload["snapshot_defects"] == [
        "the settled-rows snapshot failed its checksum"
    ]
    defects = release.structural_defects(payload)
    assert any(
        "an input snapshot could not be read" in defect
        and "settled-rows snapshot" in defect
        for defect in defects
    )
    assert release.release_state(payload) == release.DIAGNOSTIC_RELEASE


def test_a_warning_shaped_identity_issue_still_blocks():
    """Round 9: T9-1 conditions blocking on the CODE; a display severity
    is not a side door out of the closed set.
    """
    payload = {
        "records": [dict(row) for row in _sound_records()],
        "staged_row_defects": [],
        "issues": [{
            "code": "duplicate_qid_assignment", "severity": "warning",
            "message": "QINV-0001 is assigned twice",
        }],
    }
    assert any(
        "duplicate_qid_assignment" in defect
        for defect in release.structural_defects(payload)
    )


def test_a_stripped_issues_key_cannot_pass_the_identity_gate():
    """Round 9: like the row gate one block above it, the identity gate
    FAILS CLOSED — a payload with no ``issues`` key at all recomputes the
    identity issues from its own mined-Type material.
    """
    payload = {
        "records": [dict(row) for row in _sound_records()],
        "staged_row_defects": [],
        "mined_types": _mined_types(duplicate=True),
        "question_task_inventory": _inventory(),
    }
    assert "issues" not in payload
    assert any(
        "duplicate_qid_assignment" in defect
        for defect in release.structural_defects(payload)
    )


def test_a_stale_empty_issues_list_cannot_hide_a_duplicate_qid():
    """Round 10 (audit finding 9a): Round 9 recomputed only when the
    ``issues`` key was ABSENT, so a present-but-empty list won and one
    in-place edit (``issues = []``) hid ``duplicate_qid_assignment``. The
    recompute is unconditional now and UNIONS with the recorded list.
    """
    payload = {
        "records": [dict(row) for row in _sound_records()],
        "staged_row_defects": [],
        "issues": [],
        "mined_types": _mined_types(duplicate=True),
        "question_task_inventory": _inventory(),
    }
    assert any(
        "duplicate_qid_assignment" in defect
        for defect in release.structural_defects(payload)
    )


def test_a_cleared_qc_blocking_list_cannot_pass_the_gate():
    """Round 10 (audit finding 9b): the recorded ``qc_blocking_defects``
    key was trusted verbatim, so clearing the list was one in-place edit
    away from publishing an unaccounted question. The gate now unions the
    recorded key with a live ``release_qc.audit`` recompute of the same
    payload material.
    """
    payload = {
        "records": [],
        "staged_row_defects": [],
        "issues": [],
        "type_case_rows": [],
        "question_task_inventory": {
            "items": [{
                "qid": "QINV-0100", "source_kind": "exercise",
                "raw_task": "Where did this question go?",
            }],
        },
        "learning_kind": "post",
        release.QC_BLOCKING_FIELD: [],
    }
    defects = release.structural_defects(payload)
    assert any("QINV-0100" in defect for defect in defects)
    assert any(
        "recomputes a blocking finding this payload does not record"
        in defect
        for defect in defects
    )


# --------------------------------------------------------------------------- #
# 7. T9-1 B1's machine-id pair blocks at the publication act (Round 9)
# --------------------------------------------------------------------------- #

def _own_staged_job(db, chapter, records):
    job = models.UploadJob(
        owner_sub=OWNER, module="build_concepts", upload_type="textbook",
        filename="ch.mmd", mmd_text="# Chapter", status="generated",
        deposit_scope_type="chapter", deposit_scope_ids=[chapter.id],
        question_inventory={},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=records, inventory={"items": [], "stats": {}},
    )
    db.refresh(job)
    return job


def test_a_blank_mint_refuses_the_publication(db, monkeypatch):
    chapter = models.Chapter(
        chapter_code="07CBSC_S11BlankMint", board="CBSE", grade="07",
        subject="Science", unit="Unit", chapter_title="S11 Blank Mint",
        chapter_display_name="S11 Blank Mint",
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    job = _own_staged_job(db, chapter, _sound_records())
    monkeypatch.setattr(
        publication.identity, "machine_id_for_concept",
        lambda _concept, **_kwargs: "",
    )
    with pytest.raises(ValueError, match="could not be minted"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")


def test_a_duplicate_persisted_machine_id_refuses_the_publication(db):
    chapter = models.Chapter(
        chapter_code="07CBSC_S11DupId", board="CBSE", grade="07",
        subject="Science", unit="Unit", chapter_title="S11 Dup Id",
        chapter_display_name="S11 Dup Id",
    )
    db.add(chapter)
    db.flush()
    topic = models.Topic(
        chapter_id=chapter.id, topic_title="Corrupt Topic",
        topic_display_name="Corrupt Topic", pre_post_learning="Post",
        source_order=1,
    )
    db.add(topic)
    db.flush()
    for order, title in enumerate(("First Twin", "Second Twin"), start=1):
        db.add(models.Concept(
            topic_id=topic.id, concept_title=title,
            concept_display_name=title,
            concept_details="Description: A twin.", keywords="",
            source_order=order,
            machine_id="07CBSC_S11DupId_PL_T01_C01",
        ))
    db.commit()
    job = _own_staged_job(db, chapter, _sound_records())
    with pytest.raises(
        ValueError, match="duplicate persisted machine identity"
    ):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")


def test_the_uploaded_latch_revalidates_before_repeating_its_receipt(db):
    """Round 10 (audit finding 9c): the ``database_uploaded`` latch used to
    return its success receipt BEFORE any gate ran, so one successful
    publication turned the function into an unconditional receipt printer
    — a staged payload mutated in place after the upload re-earned
    ``database_uploaded: True`` unseen. The latch runs after the
    structural gate and the seal now: an honest re-request stays an
    idempotent success, a tampered one is refused.
    """
    job, chapter = _job(db)
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=_rendered_records(),
        inventory=_inventory(),
        mined_types=_mined_types(),
    )
    first = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert first["database_uploaded"] is True

    # The honest repeat is still the idempotent success it always was.
    again = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane="post")
    assert again["database_uploaded"] is True

    # Post-upload strip-tamper: the audit trail is cleared and the staged
    # mined-Type material now implies a duplicate QID assignment.
    inventory_col = copy.deepcopy(dict(job.question_inventory or {}))
    slot = copy.deepcopy(dict(inventory_col[release.RELEASE_KEY]))
    slot["mined_types"] = _mined_types(duplicate=True)
    slot["issues"] = []
    slot[release.QC_BLOCKING_FIELD] = []
    inventory_col[release.RELEASE_KEY] = slot
    job.question_inventory = inventory_col
    db.commit()
    db.refresh(job)

    with pytest.raises(ValueError, match="duplicate_qid_assignment"):
        publication.upload_release_to_database(
            db, job.id, owner_sub=OWNER, lane="post")


def test_audit_issues_reach_the_release_workbook_issues_sheet(
    db, client, monkeypatch,
):
    def fake_audit(_payload, **_kwargs):
        return [{
            "code": "release_qc_injected", "severity": "error",
            "message": "the injected audit finding, visible end to end",
            "qids": [], "block_ids": [],
        }], []

    monkeypatch.setattr(release_qc, "audit", fake_audit)
    job, chapter = _job(db)
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=_sound_records(), inventory={"items": [], "stats": {}},
    )

    response = client.get(f"/build-concepts/uploads/{job.id}/release.xlsx")
    assert response.status_code == 200
    book = openpyxl.load_workbook(
        io.BytesIO(response.content), read_only=True)
    text = "\n".join(
        str(cell)
        for sheet in book.worksheets
        for row in sheet.iter_rows(values_only=True)
        for cell in row
        if cell is not None
    )
    book.close()
    assert "release_qc_injected" in text

    response = client.get(
        f"/build-concepts/uploads/{job.id}/diagnostics.zip")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    evidence = json.loads(archive.read("context/source_evidence.json"))
    archive.close()
    assert "release_qc_injected" in json.dumps(evidence)
