"""P-item pins from the approved 2026-08-29 audit change proposal.

P1/P2/P4/P5/P9 are prompt rules (pinned as text contracts, matching
test_prompt_clarity_contracts' doctrine); P3 is the recorded source-lane
duplicate verdict; P6 the entity-ID uniqueness defect gate; P8 the
one-decimal display format. P7 needed no change — assessment_release
already refuses ``question != question_text`` on every candidate.
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
    from tests.test_release_core import (
        _both_lanes_job, _chapter_with_concepts, _run_both_lanes,
    )

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    _pre, post = _run_both_lanes(db, job, chapter)

    before = release_core.release_state(post)
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
# P8 — marks/durations/weightages display with one decimal
# --------------------------------------------------------------------------- #

def test_numeric_master_cells_display_one_decimal():
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
        assert by_field[field].number_format == "0.0", field
        assert by_field[field].value in (1.0, 2.0)
    # chapter_title (a string) and chapter_duration (excluded by A11's
    # rule — the corrected files carry it plain) keep the default format.
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


def test_descriptive_rubric_weights_default_to_uniform_one():
    from app.services import assessment_marking

    prompt = assessment_marking.MARKING_SYSTEM
    assert "uniform 1.0 weight" in prompt
    assert "number of criteria satisfied" in prompt
