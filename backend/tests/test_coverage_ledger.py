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


def test_info_hubs_are_pooled_hub_rows():
    """Step 3 pools info hubs with activities; the ledger accounts for both."""
    inventory = {
        "items": [
            {"qid": "QINV-0001", "source_kind": "activity",
             "raw_task": "A1", "image_urls": []},
            {"qid": "QINV-0002", "source_kind": "info_hub",
             "raw_task": "Do you know? The metre was defined in 1799.",
             "image_urls": []},
        ],
        "_type_case_qid_placement_ledger": {"placements": {}},
    }
    record = {
        "concept_title": "The Concept",
        "concept_details": (
            "Teaching. // Misconception/ Error Analysis: Misconceptions: x; "
            "Error Analysis: y // Achieving Mastery: does the thing."
        ),
        "activity_hub": "Try it. [[QINV-0001]] [[QINV-0002]]",
        "types": "",
    }
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=inventory, records=[record])
    assert ledger["summary"]["hubs"] == {
        "total": 2, "placed": 2, "unaccounted": 0}

    # An info hub that reached no output row is named, never silently complete.
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=inventory,
        records=[{**record, "activity_hub": "Try it. [[QINV-0001]]"}])
    assert ledger["summary"]["hubs"] == {
        "total": 2, "placed": 1, "unaccounted": 1}
    assert ledger["complete"] is False


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


def _projections() -> dict:
    return {
        "figure_blocks": [
            {"block_id": "BLK-0002",
             "image_urls": ["https://cdn.example.com/pinhole.jpg"],
             "unclaimed_image_urls": ["https://cdn.example.com/pinhole.jpg"],
             "caption": "Fig. 2 - A pinhole camera",
             "claimed_by_qids": []},
            {"block_id": "BLK-0003",
             "image_urls": ["img/map.png"],
             "unclaimed_image_urls": [],
             "caption": "Fig. 3",
             "claimed_by_qids": ["QINV-0001"]},
            {"block_id": "BLK-0004",
             "image_urls": ["https://cdn.example.com/banner.jpg"],
             "unclaimed_image_urls": ["https://cdn.example.com/banner.jpg"],
             "caption": "",
             "claimed_by_qids": []},
            {"block_id": "BLK-0005",
             "image_urls": [],
             "unclaimed_image_urls": [],
             "caption": "",
             "claimed_by_qids": []},
            {"block_id": "BLK-0006",
             "image_urls": ["https://cdn.example.com/lost.jpg"],
             "unclaimed_image_urls": ["https://cdn.example.com/lost.jpg"],
             "caption": "",
             "claimed_by_qids": []},
        ],
        "furniture": {
            "chapter_reading": ["SCIENCE 6", "Page 14"],
            "acsd": ["NATIONALISM IN EUROPE"],
        },
    }


def _place_snapshot() -> dict:
    return {
        "hub_placements": {"QINV-0003": "CONCEPT-0001"},
        "figure_placements": {
            "BLK-0002": "CONCEPT-0001",
            "BLK-0004": "decorative_or_duplicate",
        },
        "rationales": {"BLK-0004": "a decorative banner"},
    }


def test_every_canonical_figure_block_is_accounted():
    """The blind spot closes: placed / attached / disposition-recorded /
    no-image-evidence / unaccounted — every canonical figure block has a
    named state, and a genuinely unaccounted one blocks completeness."""
    inventory = _inventory()
    inventory["_type_case_qid_placement_ledger"]["placements"][
        "QINV-0002.2"] = {}
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=inventory,
        records=_records(),
        container_projections=_projections(),
        place_snapshot=_place_snapshot(),
    )
    by_id = {row["block_id"]: row for row in ledger["figure_blocks"]}
    assert by_id["BLK-0002"]["status"] == "placed"
    assert by_id["BLK-0003"]["status"] == "attached_to_item"
    assert by_id["BLK-0004"]["status"] == "disposition_recorded"
    assert by_id["BLK-0004"]["disposition"] == "decorative_or_duplicate"
    assert by_id["BLK-0005"]["status"] == "no_image_evidence"
    assert by_id["BLK-0006"]["status"] == "unaccounted"
    assert ledger["summary"]["figure_blocks"]["total"] == 5
    assert ledger["summary"]["figure_blocks"]["unaccounted"] == 1
    assert ledger["complete"] is False

    rendered = coverage_ledger.render_coverage(ledger)
    assert "figure blocks:" in rendered
    assert "BLK-0006: unaccounted" in rendered
    assert "BLK-0004: disposition_recorded (decorative_or_duplicate)" in (
        rendered
    )


def test_hub_channel_reads_the_place_snapshot_too():
    """A hub item the placement pass ruled counts placed even when the
    records text carries no marker (a legacy payload shape)."""
    inventory = {
        "items": [
            {"qid": "QINV-0003", "source_kind": "activity",
             "raw_task": "A1", "image_urls": []},
        ],
        "_type_case_qid_placement_ledger": {"placements": {}},
    }
    record = {
        "concept_title": "The Concept",
        "concept_details": (
            "Teaching. // Misconception/ Error Analysis: Misconceptions: x "
            "// Achieving Mastery: does the thing."
        ),
    }
    without = coverage_ledger.build_coverage_ledger(
        question_inventory=inventory, records=[record])
    assert without["summary"]["hubs"]["unaccounted"] == 1
    with_place = coverage_ledger.build_coverage_ledger(
        question_inventory=inventory, records=[record],
        place_snapshot=_place_snapshot(),
    )
    assert with_place["summary"]["hubs"] == {
        "total": 1, "placed": 1, "unaccounted": 0}


def test_dropped_furniture_is_listed_verbatim_never_a_count():
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_inventory(),
        records=_records(),
        container_projections=_projections(),
    )
    assert ledger["dropped_furniture"] == {
        "chapter_reading": ["SCIENCE 6", "Page 14"],
        "acsd": ["NATIONALISM IN EUROPE"],
    }
    rendered = coverage_ledger.render_coverage(ledger)
    assert "dropped furniture (3 line(s), verbatim):" in rendered
    assert "[chapter_reading] SCIENCE 6" in rendered
    assert "[acsd] NATIONALISM IN EUROPE" in rendered


def test_mmd_lane_furniture_falls_back_to_job_state_lines():
    """Without the containers artifact, the chapter-reading lines from the
    durable job state still reach the artifact JSON — as lines."""
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_inventory(),
        records=_records(),
        chapter_reading={
            "provenance": {}, "census_rows": 4,
            "dropped_furniture": ["MATHS STD 4", "41"],
        },
    )
    assert ledger["dropped_furniture"]["chapter_reading"] == [
        "MATHS STD 4", "41",
    ]
