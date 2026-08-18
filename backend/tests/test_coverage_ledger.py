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


def _complete_inventory() -> dict:
    inventory = _inventory()
    inventory["_type_case_qid_placement_ledger"]["placements"][
        "QINV-0002.2"] = {}
    return inventory


def test_q1_ledger_keys_analysis_off_the_allotment_ledger():
    """Q1: allotted rows missing their rendered section = incomplete;
    unallotted rows without one = fine; every LA-item accounted."""
    allotted = {
        "concept_title": "The Concept",
        "concept_details": (
            "Concept teaching text. // Misconception/ Error Analysis: "
            "Misconceptions: x; Error Analysis: y // Achieving Mastery: "
            "does the thing."
        ),
        "activity_hub": "Try the activity. [[QINV-0003]]",
        "types": "Example ![](img/map.png)",
        "_aegis_analysis_allotments": ["LA-0001", "LA-0002"],
    }
    unallotted = {
        "concept_title": "The Quiet Concept",
        "concept_details": (
            "Complete teaching text. // Achieving Mastery: applies it."
        ),
    }
    snapshot = {
        "inventory": [
            {"item_id": "LA-0001", "kind": "misconception", "text": "x"},
            {"item_id": "LA-0002", "kind": "error_analysis", "text": "y"},
        ],
        "allotments": {"LA-0001": "CONCEPT-0001", "LA-0002": "CONCEPT-0001"},
    }
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_complete_inventory(),
        records=[allotted, unallotted],
        analysis_snapshot=snapshot,
    )
    # The unallotted row owes no analysis section — complete.
    assert ledger["rows_missing_learner_analysis"] == []
    assert ledger["summary"]["learner_analysis_items"] == {
        "total": 2, "allotted": 2, "unaccounted": 0}
    assert ledger["complete"] is True

    # An ALLOTTED row missing its rendered section is incomplete.
    stripped = dict(allotted)
    stripped["concept_details"] = (
        "Concept teaching text. // Achieving Mastery: does the thing."
    )
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_complete_inventory(),
        records=[stripped, unallotted],
        analysis_snapshot=snapshot,
    )
    missing = ledger["rows_missing_learner_analysis"]
    assert [row["missing"] for row in missing] == [
        ["misconception_error_analysis"]
    ]
    assert ledger["complete"] is False


def test_q1_ledger_names_an_unallotted_inventory_item():
    """R4: an LA-item without an allotment is visible incompleteness,
    never a silent drop."""
    snapshot = {
        "inventory": [
            {"item_id": "LA-0001", "kind": "misconception", "text": "x"},
            {"item_id": "LA-0002", "kind": "error_analysis", "text": "y"},
        ],
        "allotments": {"LA-0001": "CONCEPT-0001"},
    }
    record = {
        "concept_title": "The Concept",
        "concept_details": (
            "Concept teaching text. // Misconception/ Error Analysis: "
            "Misconceptions: x // Achieving Mastery: does the thing."
        ),
        "activity_hub": "Try the activity. [[QINV-0003]]",
        "types": "Example ![](img/map.png)",
        "_aegis_analysis_allotments": ["LA-0001"],
    }
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_complete_inventory(),
        records=[record],
        analysis_snapshot=snapshot,
    )
    assert ledger["summary"]["learner_analysis_items"] == {
        "total": 2, "allotted": 1, "unaccounted": 1}
    assert ledger["complete"] is False
    rendered = coverage_ledger.render_coverage(ledger)
    assert "unaccounted analysis item LA-0002" in rendered

    # A legacy job (no snapshot, no markers) keeps the every-row
    # expectation — the analysis-carrying row is complete as before.
    legacy = coverage_ledger.build_coverage_ledger(
        question_inventory=_complete_inventory(), records=_records())
    assert legacy["summary"]["learner_analysis_items"] == {
        "total": 0, "allotted": 0, "unaccounted": 0}
    assert legacy["complete"] is True


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
    assert (
        "learner analysis: every owed section present" in complete
    )


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


# ---------------------------------------------------------------------------
# lane awareness (step-7 slice C2; map §6.6)
#
# Before this slice the ledger and the run report had ZERO lane dimension:
# a Pre row was charged every POST obligation — a source-question share, a
# figure attachment, and (through the legacy every-row branch) an analysis
# section it must not have — while the Pre lane's own obligations were
# accounted nowhere at all.


def _pre_row(
    pre_id: str = "PRC-0001",
    *,
    prerequisites=("PR-0001",),
    analysis_items=(),
    needed_for=(("CONCEPT-0001", "Sovereignty After 1789"),),
    mastery: bool = True,
) -> dict:
    details = "Description: Sovereignty is the highest law-making authority."
    if mastery:
        details += "\nAchieving Mastery: Saying who holds final authority."
    if analysis_items:
        details += (
            " // Misconception/ Error Analysis: Misconceptions: learners may "
            "believe a sovereign is merely the most powerful person."
        )
    row = {
        "topic": "Political Vocabulary",
        "concept_title": f"Pre Concept {pre_id}",
        "concept_details": details,
        "_pre_concept_id": pre_id,
        "_source_block_ids": [],
        "_source_grounding_contract": "derived-from-prerequisite-capture",
        "_aegis_pre_prerequisites": [
            {"prerequisite_id": ref, "text": f"text for {ref}"}
            for ref in prerequisites
        ],
        "_aegis_needed_for": [
            {"post_concept_id": ref, "post_concept_title": title}
            for ref, title in needed_for
        ],
    }
    if analysis_items:
        row["_aegis_analysis_allotments"] = list(analysis_items)
    return row


def _pre_map(rows=None, *, inventory=None, allotments=None, refused="") -> dict:
    rows = [_pre_row()] if rows is None else rows
    return {
        "rows": rows,
        "topics": [{
            "pre_topic_id": "PRT-0001",
            "title": "Political Vocabulary",
            "pre_concept_ids": [row["_pre_concept_id"] for row in rows],
        }],
        "needed_for": {},
        "analysis": {
            "inventory": list(inventory or []),
            "allotments": dict(allotments or {}),
            "rationales": {},
            "review_flags": {},
        },
        "review_flags": {},
        "decision_flags": {},
        "validation": [],
        **({"refused": refused} if refused else {}),
    }


def _capture(*refs: str) -> dict:
    return {"prerequisites": [
        {"prerequisite_id": ref, "text": f"text for {ref}"} for ref in refs
    ]}


def test_a_pre_row_is_never_charged_a_post_obligation():
    """A Pre row has no source question, no Type/Case and no figure — the
    no-extraction steer guarantees it. It must not be reported as owing
    an analysis section either, and it must not disturb the POST channel
    accounting by being in the records list."""
    post_only = coverage_ledger.build_coverage_ledger(
        question_inventory=_inventory(), records=_records())
    mixed = coverage_ledger.build_coverage_ledger(
        question_inventory=_inventory(),
        records=[*_records(), _pre_row()],
        pre_map_snapshot=_pre_map(),
        prelearn_snapshot=_capture("PR-0001"),
    )
    for key in ("questions", "hubs", "figures", "figure_blocks",
                "flagged_for_review"):
        assert mixed["summary"][key] == post_only["summary"][key], key
    # The Pre row is NOT in the POST analysis expectation, even though this
    # job takes the legacy every-row branch (no analysis snapshot anywhere).
    assert mixed["summary"]["rows_missing_learner_analysis"] == 0
    assert not any(
        "Pre Concept" in row["concept_title"]
        for row in mixed["rows_missing_learner_analysis"]
    )
    # The Pre row changes the completeness verdict in neither direction.
    assert mixed["complete"] == post_only["complete"]


def test_a_pre_row_without_its_own_inventory_owes_no_analysis_section():
    """§6.6's exact defect: a Pre run has no analysis snapshot and no
    allotment marker, so the legacy branch used to report EVERY Pre row as
    missing its section. The Pre lane is always allotment-aware — its own
    Q1 inventory is the only mechanism — so an unallotted Pre row is
    complete, and Achieving Mastery is still owed by every one."""
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[_pre_row()],
        pre_map_snapshot=_pre_map(), prelearn_snapshot=_capture("PR-0001"),
    )
    assert ledger["pre_learning"]["rows_missing_learner_analysis"] == []
    assert ledger["summary"]["pre_learning"]["rows_with_analysis_section"] == 0
    assert ledger["complete"] is True

    # …but a Pre row that lost its Achieving Mastery line IS reported.
    stripped = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[_pre_row(mastery=False)],
        pre_map_snapshot=_pre_map(rows=[_pre_row(mastery=False)]),
        prelearn_snapshot=_capture("PR-0001"),
    )
    assert [
        row["missing"]
        for row in stripped["pre_learning"]["rows_missing_learner_analysis"]
    ] == [["achieving_mastery"]]
    assert stripped["complete"] is False


def test_the_pre_lane_owns_obligations_of_its_own():
    """Every prerequisite carried into the map exactly once, every Pre
    inventory item allotted exactly once, every needed-for link resolved."""
    rows = [
        _pre_row("PRC-0001", prerequisites=("PR-0001",),
                 analysis_items=("PLA-0001",)),
        _pre_row("PRC-0002", prerequisites=("PR-0002",)),
    ]
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(
            rows,
            inventory=[{
                "item_id": "PLA-0001", "kind": "misconception",
                "text": "a prerequisite belief",
            }],
            allotments={"PLA-0001": "PRC-0001"},
        ),
        prelearn_snapshot=_capture("PR-0001", "PR-0002"),
    )
    summary = ledger["summary"]["pre_learning"]
    assert summary["rows"] == 2
    assert summary["topics"] == 1
    assert summary["prerequisites"] == {
        "total": 2, "mapped": 2, "unaccounted": 0}
    assert summary["analysis_items"] == {
        "total": 1, "allotted": 1, "unaccounted": 0}
    # Q1: NOT every pre-concept receives one — reported, never owed.
    assert summary["rows_with_analysis_section"] == 1
    assert summary["needed_for_links"]["resolved"] == 2
    assert summary["needed_for_links"]["unresolved"] == 0
    assert ledger["complete"] is True
    assert [
        (row["prerequisite_id"], row["status"], row["pre_concept_ids"])
        for row in ledger["pre_learning"]["prerequisites"]
    ] == [
        ("PR-0001", "mapped", ["PRC-0001"]),
        ("PR-0002", "mapped", ["PRC-0002"]),
    ]


def test_a_missing_pre_allotment_is_reported_incomplete():
    """R4 in the Pre lane: an inventory item that reached no pre-concept is
    visible incompleteness, named with its text — never a silent drop."""
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(
            inventory=[
                {"item_id": "PLA-0001", "kind": "misconception",
                 "text": "allotted"},
                {"item_id": "PLA-0002", "kind": "error_analysis",
                 "text": "a learner's item that reached no concept"},
            ],
            allotments={"PLA-0001": "PRC-0001"},
        ),
        prelearn_snapshot=_capture("PR-0001"),
    )
    assert ledger["summary"]["pre_learning"]["analysis_items"] == {
        "total": 2, "allotted": 1, "unaccounted": 1}
    assert ledger["complete"] is False
    rendered = coverage_ledger.render_coverage(ledger)
    assert "unaccounted analysis item PLA-0002" in rendered
    assert "a learner's item that reached no concept" in rendered


def test_an_unmapped_prerequisite_and_an_unresolved_link_are_reported():
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(rows=[
            _pre_row(
                "PRC-0001",
                needed_for=(("CONCEPT-0001", ""),),
            ),
        ]),
        prelearn_snapshot=_capture("PR-0001", "PR-0002"),
    )
    assert ledger["summary"]["pre_learning"]["prerequisites"] == {
        "total": 2, "mapped": 1, "unaccounted": 1}
    assert ledger["summary"]["pre_learning"]["needed_for_links"][
        "unresolved"] == 1
    assert ledger["complete"] is False
    rendered = coverage_ledger.render_coverage(ledger)
    assert "unmapped PR-0002" in rendered
    assert "text for PR-0002" in rendered
    assert "1 unresolved" in rendered


def test_a_pre_concept_linked_to_nothing_is_advisory_not_incomplete():
    """Necessity is a CRITIC dimension (Q10): a pre-concept nothing in this
    chapter requires ships flagged, and the ledger reports it without
    calling the run incomplete."""
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(rows=[_pre_row("PRC-0001", needed_for=())]),
        prelearn_snapshot=_capture("PR-0001"),
    )
    links = ledger["summary"]["pre_learning"]["needed_for_links"]
    assert links == {
        "total": 0, "resolved": 0, "unresolved": 0,
        "pre_concepts_without_links": 1,
    }
    assert ledger["complete"] is True
    assert "linked to nothing (advisory, Q10" in coverage_ledger.render_coverage(
        ledger
    )


def test_a_run_with_no_pre_lane_reports_no_pre_section():
    """The lane dimension is silent when there is no lane: the rendered
    COVERAGE section is byte-identical to what it was before slice C2."""
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory=_inventory(), records=_records())
    assert ledger["summary"]["pre_learning"]["rows"] == 0
    assert "PRE-LEARNING" not in coverage_ledger.render_coverage(ledger)


def test_a_refused_pre_map_is_reported_not_hidden():
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(rows=[], refused="carries QINV-0004"),
    )
    rendered = coverage_ledger.render_coverage(ledger)
    assert "REFUSED and not shipped: carries QINV-0004" in rendered


def test_a_lost_pre_map_snapshot_names_every_prerequisite_it_cannot_place():
    """R4: silent incompleteness is impossible. The Pre map snapshot is
    written best-effort, so a failed write leaves the capture holding
    prerequisites that reached no row — exactly the state in which a
    learner's prerequisite is lost. The ledger says INCOMPLETE, and the
    reviewer's first file must say WHY and name them, not go quiet
    because no Pre row reached it."""
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        prelearn_snapshot=_capture("PR-0001", "PR-0002"),
    )
    assert ledger["complete"] is False
    assert [
        (row["prerequisite_id"], row["status"])
        for row in ledger["pre_learning"]["prerequisites"]
    ] == [("PR-0001", "unmapped"), ("PR-0002", "unmapped")]

    rendered = coverage_ledger.render_coverage(ledger)
    assert "PRE-LEARNING (Phase 03)" in rendered
    assert "no Pre-Learning map is recorded for this run" in rendered
    for ref in ("PR-0001", "PR-0002"):
        assert f"unmapped {ref}" in rendered
        assert f"text for {ref}" in rendered


def test_a_missing_capture_cannot_confirm_the_map_and_says_so():
    """The capture is the authority on what there was to carry. When it is
    absent the ledger cannot confirm a single claim, and an absent
    authority is not a clean bill of health — every claimed prerequisite
    is reported ``capture_unavailable`` rather than ``mapped``."""
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(rows=[_pre_row(prerequisites=("PR-0009",))]),
    )
    assert [
        (row["prerequisite_id"], row["status"])
        for row in ledger["pre_learning"]["prerequisites"]
    ] == [("PR-0009", "capture_unavailable")]
    assert ledger["complete"] is False
    assert "capture_unavailable PR-0009" in coverage_ledger.render_coverage(
        ledger
    )

    # A capture that is PRESENT and captured nothing is a different fact,
    # and keeps its own name.
    empty = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(rows=[_pre_row(prerequisites=("PR-0009",))]),
        prelearn_snapshot=_capture(),
    )
    assert [
        row["status"] for row in empty["pre_learning"]["prerequisites"]
    ] == ["unknown_prerequisite"]


def test_a_pre_row_is_never_excused_by_what_the_model_called_it():
    """Rule 1 in the ledger's own verdict: the Post lane may read a
    culmination title back because it MINTED it, but the Pre lane mints
    none. A Pre row that lost the Achieving Mastery every row owes is
    incompleteness whatever the model happened to title it."""
    row = _pre_row(mastery=False, analysis_items=())
    row["concept_title"] = "Place Value as the Culmination of Counting"
    ledger = coverage_ledger.build_coverage_ledger(
        question_inventory={}, records=[],
        pre_map_snapshot=_pre_map(rows=[row]),
        prelearn_snapshot=_capture("PR-0001"),
    )
    reported = ledger["pre_learning"]["rows_missing_learner_analysis"]
    assert [entry["missing"] for entry in reported] == [["achieving_mastery"]]
    assert reported[0]["culmination"] is False
    assert ledger["complete"] is False
