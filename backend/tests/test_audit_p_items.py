"""P-item pins from the approved 2026-08-29 audit change proposal.

P1/P2/P4/P5/P9 are prompt rules (pinned as text contracts, matching
test_prompt_clarity_contracts' doctrine); P3 is the recorded source-lane
duplicate verdict; P6 the entity-ID uniqueness defect gate; P8 the
numeric display format (``0.##`` since Master Governing Contract v2.0
§32 superseded the audit's one-decimal rule). P7 needed no change —
assessment_release already refuses ``question != question_text`` on every
candidate.
"""
from __future__ import annotations

import copy
import io
from types import SimpleNamespace

import openpyxl
import pytest

from app.services import assessment_dedup
from app.services import release_core
from app.services.phase3 import kernel


# --------------------------------------------------------------------------- #
# P3 — one recorded duplicate verdict across the whole source set
# --------------------------------------------------------------------------- #

def _atom(qid: str, text: str, *, label: str = "", alt: str = "") -> dict:
    return {
        "source_qid": qid,
        "source_paper_number": label or qid,
        "source_kind": "exercise",
        "raw_text": text,
        "normalized_public_text": text,
        "source_answer": "0",
        "shared_context": "",
        "alternative_set_id": alt or None,
    }


_META = {"board": "MSBSHSE", "grade": "6", "subject": "Mathematics"}
_SHA = "d" * 64


def test_a_double_shipped_source_question_folds_into_its_survivor():
    atoms = [
        _atom("QINV-0001", "Is 5-5=0 a natural number? Choose True or False."),
        _atom("QINV-0007", "Is 5 - 5 = 0 a natural number?"),
        _atom("QINV-0003", "Write 4,05,308 in words."),
    ]

    def provider(request):
        assert request["stage"] == "assessment.source_dedup"
        assert len(request["questions"]) == 3
        return {
            "duplicate_sets": [{
                "survivor_source_qid": "QINV-0001",
                "removed": [{
                    "source_qid": "QINV-0007",
                    "reason": "the same true-or-false ask shipped twice",
                }],
            }],
            "confidence": 0.9,
            "rationale": "verbatim double-ship under two labels",
        }

    kept, represented = assessment_dedup.decide_source_duplicates(
        atoms,
        meta=_META,
        envelope_sha256=_SHA,
        provider=provider,
        critic=None,
        store=kernel.DecisionStore(),
    )
    assert [a["source_qid"] for a in kept] == ["QINV-0001", "QINV-0003"]
    assert len(represented) == 1
    record = represented[0]
    assert record["source_qid"] == "QINV-0007"
    assert record["duplicate_of"] == "QINV-0001"
    assert "true-or-false" in record["reason"]
    assert record["flags"], "the fold must be a reviewable disposition"


def test_an_empty_verdict_removes_nothing_and_a_single_atom_costs_nothing():
    atoms = [_atom("QINV-0001", "One"), _atom("QINV-0002", "Two")]
    kept, represented = assessment_dedup.decide_source_duplicates(
        atoms,
        meta=_META,
        envelope_sha256=_SHA,
        provider=lambda _r: {
            "duplicate_sets": [], "confidence": 1.0, "rationale": "distinct",
        },
        critic=None,
        store=kernel.DecisionStore(),
    )
    assert kept == atoms
    assert represented == []

    # One atom: the provider must never be called.
    def never(_request):
        raise AssertionError("a single atom must not spend a verdict")

    kept, represented = assessment_dedup.decide_source_duplicates(
        [_atom("QINV-0001", "Only one")],
        meta=_META,
        envelope_sha256=_SHA,
        provider=never,
        critic=None,
        store=kernel.DecisionStore(),
    )
    assert len(kept) == 1 and represented == []


def test_the_source_dedup_checker_refuses_a_broken_verdict():
    atoms = [_atom("QINV-0001", "One"), _atom("QINV-0002", "Two")]

    def bad(_request):
        return {
            "duplicate_sets": [{
                "survivor_source_qid": "QINV-0009",
                "removed": [{"source_qid": "QINV-0009", "reason": ""}],
            }],
            "confidence": 1.0,
            "rationale": "broken",
        }

    with pytest.raises(kernel.ContractError):
        assessment_dedup.decide_source_duplicates(
            atoms,
            meta=_META,
            envelope_sha256=_SHA,
            provider=bad,
            critic=None,
            store=kernel.DecisionStore(),
        )


def test_a_source_duplicate_fold_names_the_release_ready_with_flags(db):
    """The fold is read by the live projection of a release that deposited:
    the borrowed both-lanes fixtures must clear the v2.0 materialization
    gates, since a release with no depositable candidate is structurally
    diagnostic and no flag can be observed on it."""
    from tests.test_release_core import (
        _both_lanes_job, _chapter_with_concepts, _run_both_lanes,
    )

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    _pre, post = _run_both_lanes(db, job, chapter)

    before = release_core.release_state(post)
    assert before in (release_core.READY, release_core.READY_WITH_FLAGS), (
        before,
        [
            blocked.get("flags")
            for blocked in post.payload.get("materialization_blocked") or []
        ],
    )
    post.payload = dict(post.payload, source_duplicates_represented=[{
        "source_qid": "QINV-0007", "duplicate_of": "QINV-0001",
        "reason": "double-ship",
    }])
    db.commit()
    after = release_core.release_state(post)
    assert after == release_core.READY_WITH_FLAGS
    # And the flag is what changed the answer (a live reader, not a grep).
    if before == release_core.READY:
        assert before != after


# --------------------------------------------------------------------------- #
# P6 — entity-ID uniqueness is a named defect, never a silent merge
# --------------------------------------------------------------------------- #

def test_duplicate_carried_machine_ids_are_a_named_defect(db):
    from app.services import build_concepts_release as release
    from app.services import build_concepts_release_files as release_files
    from tests.test_build_concepts_release import _job, _records

    job, chapter = _job(db)
    records = [copy.deepcopy(_records()[0]) for _ in range(2)]
    records[0]["concept_title"] = "Concept Alpha"
    records[1]["concept_title"] = "Concept Beta"
    for record in records:
        # A carried id wins verbatim (Round 7) — an imported payload
        # collapsed the way the audit's hand-corrected files were.
        record["machine_id"] = "06MSEN_Collapsed_PL_T01"
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=records,
        reason="P6 fixture",
    )
    db.refresh(job)

    payload = release.release_payload(job)
    _c, concepts, _r, defects = release_files.transient_release_hierarchy(
        db, job, payload=payload)
    codes = [d.get("code") for d in defects]
    assert "duplicate_entity_machine_id" in codes
    message = next(
        d["message"] for d in defects
        if d.get("code") == "duplicate_entity_machine_id"
    )
    assert "silently merge" in message

    # And a healthy projection carries no such defect.
    job2, chapter2 = _job(db)
    release.stage_release(
        db, job2, target_chapter_id=chapter2.id, records=_records(),
        reason="P6 healthy fixture",
    )
    db.refresh(job2)
    payload2 = release.release_payload(job2)
    _c, _concepts, _r, defects2 = release_files.transient_release_hierarchy(
        db, job2, payload=payload2)
    assert "duplicate_entity_machine_id" not in [
        d.get("code") for d in defects2
    ]


# --------------------------------------------------------------------------- #
# P8 — marks/durations/weightages are numeric cells with the 0.## display
# --------------------------------------------------------------------------- #

def test_numeric_master_cells_display_with_the_contract_number_format():
    """Contract v2.0 §32/§42.10 supersedes P8's one-decimal "0.0": numeric
    cells stay numeric and display as ``0.##`` (0.5, 1, 1.5, 2)."""
    from app.bulk_import import assessment_workbook as workbook

    schema = workbook.output_schema("master")
    fields = schema["fields"]["Objective"]
    record = {field: "" for field in fields}
    record.update({
        "question_label": "X Q01",
        "marks": 1.0,
        "question_duration": 2.0,
        "answer_weightage_1": 1.0,
        # A string stays a string; the format applies to numerics only.
        "chapter_title": "Chapter",
    })

    book = openpyxl.Workbook()
    ws = book.active
    workbook._write_headers(ws, "Objective", schema)
    workbook._append_record(ws, "Objective", record, schema=schema)

    by_field = {
        field: ws.cell(row=3, column=index)
        for index, field in enumerate(fields, start=1)
    }
    for field in ("marks", "question_duration", "answer_weightage_1"):
        assert by_field[field].number_format == "0.##", field
        assert by_field[field].value in (1.0, 2.0)
    # chapter_title (a string) keeps the default format: the display rule
    # applies to numerics only.
    assert by_field["chapter_title"].number_format == "General"
    book.close()


# --------------------------------------------------------------------------- #
# P1 / P2 / P4 / P5 / P9 — prompt rules pinned as text contracts
# --------------------------------------------------------------------------- #

def test_plain_numerals_stay_plain_on_every_prompt_surface():
    from app.services import assessment_master_refiner
    from app.services import assessment_materialization
    from app.services import katex_rules

    assert "49,38,67,521" in katex_rules.PROMPT_PREAMBLE
    assert "₹87,000" in katex_rules.PROMPT_PREAMBLE
    assert "49,38,67,521" in assessment_materialization.MATERIALIZE_SYSTEM
    assert (
        "genuine mathematical notation"
        in assessment_master_refiner.CANDIDATE_SYSTEM
    )


def test_type_consolidation_spans_the_whole_set():
    from app.services import prompts

    text = prompts.get_text("concepts.type_semantic_consolidation.system")
    assert "spans the WHOLE supplied set" in text
    assert "Interpreting Integers" in text


def test_case_titles_are_authored_wording():
    from app.services import prompts

    text = prompts.get_text("concepts.types_guidance.descriptive")
    assert "AUTHORED wording" in text
    assert "writing number in words i" in text


def test_figure_placement_forbids_repeats_and_generic_captions():
    from app.services.phase3 import prompts as phase3_prompts

    assert "at most ONE placement" in phase3_prompts.PLACE_SYSTEM
    assert "Source visual" in phase3_prompts.PLACE_SYSTEM


def test_descriptive_rubric_weights_are_exactly_half_or_one():
    """Contract v2.0 §27.5/§32 retires P9's "uniform 1.0 default": every
    Descriptive rubric criterion carries exactly 0.5 or 1 mark."""
    from app.services import assessment_marking

    prompt = assessment_marking.MARKING_SYSTEM
    assert "EXACTLY 0.5 or 1 mark" in prompt
    assert "number of criteria satisfied" in prompt


# --------------------------------------------------------------------------- #
# D8 — a Culmination exists only for a topic with two or more concepts
# --------------------------------------------------------------------------- #

def _concept_row(topic: str, title: str) -> dict:
    return {
        "topic": topic,
        "concept_title": title,
        "parent_concept": "P",
        "concept_details": "Description: d",
        "keywords": "k",
    }


def test_an_authored_culmination_never_lands_on_a_single_concept_topic():
    from app.services import generation

    records = [
        _concept_row("Topic One", "Only Concept"),
        _concept_row("Topic Two", "Concept A"),
        _concept_row("Topic Two", "Concept B"),
    ]
    authored = [
        {
            "topic": "Topic One",
            "concept_title": "Culmination - Only Concept",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
        {
            "topic": "Topic Two",
            "concept_title": "Culmination - Concept A and Concept B",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
    ]
    merged = generation._merge_culmination_rows(records, authored)
    titles_by_topic: dict[str, list[str]] = {}
    for row in merged:
        titles_by_topic.setdefault(row["topic"], []).append(
            row["concept_title"]
        )
    # Topic One (single concept) got NO culmination (owner decision D8);
    # Topic Two got its authored one, last.
    assert titles_by_topic["Topic One"] == ["Only Concept"]
    assert titles_by_topic["Topic Two"][-1] == (
        "Culmination - Concept A and Concept B"
    )


def test_the_terminal_contract_keeps_a_legacy_single_concept_culmination():
    """D8 is enforced at authoring; a legacy row that reaches the terminal
    contract (a pre-D8 checkpoint whose grounding certificate already
    attested it) ships FLAGGED, never dropped — dropping an attested row
    breaks certificate lineage and refuses finished work (R4)."""

    from app.services import concept_validator as cv
    from app.services import generation

    rows = [
        _concept_row("Topic One", "Only Concept"),
        {
            "topic": "Topic One",
            "concept_title": "Culmination - Only Concept",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
    ]
    out = generation._ensure_terminal_culmination_contract(rows)
    assert [r["concept_title"] for r in out] == [
        "Only Concept", "Culmination - Only Concept",
    ]
    report = cv.validate_concept_rows(rows, require_culmination=True)
    assert any(
        e["code"] == "culmination_single_concept" for e in report["errors"]
    )


def test_a_co_occurring_error_does_not_drop_an_attested_single_concept_culmination():
    """The repair path itself honors D8. A duplicate culmination in Topic
    Two routes ALL records through ``_enforce_culminations``; Topic One's
    well-formed attested single-concept culmination must ride through
    flagged, never dropped (the pre-fix branch discarded it)."""

    from app.services import generation

    rows = [
        _concept_row("Topic One", "Only Concept"),
        {
            "topic": "Topic One",
            "concept_title": "Culmination - Only Concept",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
        _concept_row("Topic Two", "Concept A"),
        _concept_row("Topic Two", "Concept B"),
        {
            "topic": "Topic Two",
            "concept_title": "Culmination - First",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
        {
            "topic": "Topic Two",
            "concept_title": "Culmination - Duplicate",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
    ]
    out = generation._ensure_terminal_culmination_contract(rows)
    titles_by_topic: dict[str, list[str]] = {}
    for row in out:
        titles_by_topic.setdefault(row["topic"], []).append(
            row["concept_title"]
        )
    # Topic One keeps its attested culmination; Topic Two keeps exactly one.
    assert titles_by_topic["Topic One"] == [
        "Only Concept", "Culmination - Only Concept",
    ]
    assert titles_by_topic["Topic Two"] == [
        "Concept A", "Concept B", "Culmination - First",
    ]


def test_a_misplaced_single_concept_culmination_is_repositioned_not_dropped():
    from app.services import generation

    rows = [
        {
            "topic": "Topic One",
            "concept_title": "Culmination - Only Concept",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
        _concept_row("Topic One", "Only Concept"),
    ]
    out = generation._ensure_terminal_culmination_contract(rows)
    assert [r["concept_title"] for r in out] == [
        "Only Concept", "Culmination - Only Concept",
    ]


def test_a_topic_with_zero_normal_concepts_flags_its_culmination():
    """``< 2``, not ``== 1``: a topic whose ONLY row is a culmination is
    the same D8 violation and must be visible to review, not silent."""

    from app.services import concept_validator as cv

    rows = [
        {
            "topic": "Topic Zero",
            "concept_title": "Culmination - Nothing",
            "parent_concept": "Culmination",
            "concept_details": "Description: Recap",
        },
        _concept_row("Topic Two", "Concept A"),
        _concept_row("Topic Two", "Concept B"),
    ]
    report = cv.validate_concept_rows(rows, require_culmination=True)
    assert any(
        e["code"] == "culmination_single_concept"
        and e["severity"] == "warning"
        for e in report["errors"]
    )


def test_the_culmination_prompts_carry_the_two_concept_floor():
    from app.services import prompts

    culmination = prompts.get_text("concepts.culmination.system")
    assert "TWO OR MORE" in culmination.replace("\n  ", " ")
    assert "single concept" in culmination
    assert "D8" in culmination
