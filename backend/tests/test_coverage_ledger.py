"""The coverage ledger (docs/build-concepts-manual-process.md, completion).

"Everything is covered well" as an accounting: every question, hub, figure
and fragment either reached the output or is named, and every released row
carries its learner analysis. Pure function of durable job state, so every
diagnostics export can rebuild it for a finished or stopped run alike.
"""
from __future__ import annotations

from pathlib import Path

from app.services import build_concepts_release_files, coverage_ledger


def _inventory() -> dict:
    return {
        "items": [
            {"qid": "QINV-0001", "source_kind": "exercise",
             "raw_task": "Q1", "image_urls": ["img/map.png"]},
            {"qid": "QINV-0002.1", "source_kind": "exercise",
             "raw_task": "Q2", "parent_qid": "QINV-0002",
             "polish_flag": "split_for_review", "image_urls": []},
            {"qid": "QINV-0002.2", "source_kind": "exercise",
             "raw_task": "Q2", "parent_qid": "QINV-0002",
             "polish_flag": "split_for_review", "image_urls": []},
            {"qid": "QINV-0003", "source_kind": "activity",
             "raw_task": "A1", "image_urls": []},
        ],
        "_type_case_qid_placement_ledger": {
            "placements": {"QINV-0001": {}, "QINV-0002.1": {}},
        },
    }


def _records(*, hub_qid: str = "QINV-0003", analysis: bool = True) -> list[dict]:
    details = "Concept teaching text."
    if analysis:
        details += (
            " // Misconception/ Error Analysis: Misconceptions: x; "
            "Error Analysis: y // Achieving Mastery: does the thing."
        )
    return [{
        "concept_title": "The Concept",
        "concept_details": details,
        "activity_hub": f"Try the activity. [[{hub_qid}]]",
        "types": "Example ![](img/map.png)",
    }]


def test_ledger_accounts_for_every_channel():
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_inventory(), records=_records())

    assert ledger["summary"]["questions"] == {
        "total": 3, "placed": 2, "unaccounted": 1}
    assert ledger["summary"]["hubs"] == {
        "total": 1, "placed": 1, "unaccounted": 0}
    assert ledger["summary"]["figures"] == {
        "total": 1, "placed": 1, "unaccounted": 0}
    assert ledger["summary"]["flagged_for_review"] == 2
    assert ledger["complete"] is False  # QINV-0002.2 reached nothing

    unaccounted = [
        row for row in ledger["items"] if row["status"] != "placed"
    ]
    assert [row["qid"] for row in unaccounted] == ["QINV-0002.2"]
    assert unaccounted[0]["parent_qid"] == "QINV-0002"


def test_a_parent_qid_never_matches_inside_its_fragment():
    """QINV-0002 in a row means the parent, not QINV-0002.1."""
    assert coverage_ledger._qid_present("QINV-0002.1", "x QINV-0002.1 y")
    assert not coverage_ledger._qid_present("QINV-0002", "x QINV-0002.1 y")
    assert coverage_ledger._qid_present("QINV-0002", "x QINV-0002 y")


def test_missing_learner_analysis_is_named_per_row():
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_inventory(),
        records=_records(analysis=False))

    missing = ledger["rows_missing_learner_analysis"]
    assert len(missing) == 1
    assert set(missing[0]["missing"]) == {
        "achieving_mastery", "misconception_error_analysis"}
    assert ledger["summary"]["normal_rows_missing_learner_analysis"] == 1
    assert ledger["complete"] is False


def test_an_unplaced_ledger_row_never_counts_as_placed():
    """unplaced_pending_certification is a flagged gap, not a placement."""
    inventory = _inventory()
    inventory["_type_case_qid_placement_ledger"]["placements"][
        "QINV-0002.2"] = {"basis": "unplaced_pending_certification",
                          "certified": False, "qid": "QINV-0002.2"}

    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=inventory, records=_records())

    assert ledger["complete"] is False
    unaccounted = [
        row["qid"] for row in ledger["items"] if row["status"] != "placed"
    ]
    assert unaccounted == ["QINV-0002.2"]


def test_complete_when_everything_is_accounted():
    inventory = _inventory()
    inventory["_type_case_qid_placement_ledger"]["placements"][
        "QINV-0002.2"] = {}

    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=inventory, records=_records())

    assert ledger["complete"] is True


def test_render_names_the_unaccounted_and_the_flags():
    text = coverage_ledger.render_coverage(
        coverage_ledger.build_coverage_ledger(
            question_inventory=_inventory(), records=_records()))

    assert "COVERAGE" in text
    assert "INCOMPLETE" in text
    assert "QINV-0002.2" in text
    assert "[split_for_review]" in text

    inventory = _inventory()
    inventory["_type_case_qid_placement_ledger"]["placements"][
        "QINV-0002.2"] = {}
    complete = coverage_ledger.render_coverage(
        coverage_ledger.build_coverage_ledger(
            question_inventory=inventory, records=_records()))
    assert "everything accounted for" in complete
    assert "learner analysis: present on every released row" in complete


def test_diagnostics_export_ships_the_ledger():
    """The zip builder writes the ledger and appends the COVERAGE section."""
    source = Path(
        build_concepts_release_files.__file__).read_text(encoding="utf-8")

    assert "coverage_ledger.build_coverage_ledger" in source
    assert "context/coverage_ledger.json" in source
    assert "coverage_ledger.render_coverage" in source


def test_ledger_survives_empty_state():
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=None, records=None)

    assert ledger["summary"]["questions"]["total"] == 0
    assert ledger["complete"] is True
    assert "COVERAGE" in coverage_ledger.render_coverage(ledger)
