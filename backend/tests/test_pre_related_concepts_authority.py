"""Regression coverage for staged Pre ``related_concepts`` authority."""

from __future__ import annotations

import copy

import pytest

from app import models
from app.services import build_concepts_release as release
from app.services import build_concepts_release_files as release_files
from app.services import build_concepts_release_publication as publication
from tests.test_assessment_release_run import OWNER, _chapter_with_concepts
from tests.test_assessment_pre_release_lane import SOURCE_INVENTORY
from tests.test_pre_release_lane_wiring import (
    _both_lanes_job,
    _pre_map,
    _pre_questions,
)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(
            {
                "related_concepts": "stale imported relation",
                release.PRE_ROW_RELATED_CONCEPTS_FIELD: "",
            },
            "",
            id="present-empty-clears-stale-value",
        ),
        pytest.param(
            {
                "related_concepts": "stale imported relation",
                release.PRE_ROW_RELATED_CONCEPTS_FIELD: (
                    "06CBSE_Ch_PL_T01_C01"
                ),
            },
            "06CBSE_Ch_PL_T01_C01",
            id="present-resolved-id-wins",
        ),
        pytest.param(
            {"related_concepts": "authored public relation"},
            "authored public relation",
            id="absent-marker-preserves-public-column",
        ),
    ],
)
def test_pre_relation_marker_presence_is_authoritative_during_lift(
    row, expected,
):
    lifted = release._lift_resolved_related_concepts(row)
    assert lifted["related_concepts"] == expected

    stripped = release._strip_release_fields(lifted)
    assert stripped["related_concepts"] == expected
    assert release.PRE_ROW_RELATED_CONCEPTS_FIELD not in stripped


def test_empty_pre_relation_marker_wins_in_transient_and_persisted_rows(db):
    """An old cross-phase label cannot survive an explicit empty resolve."""

    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)

    pre_map = _pre_map()
    pre_row = pre_map["rows"][0]
    pre_row["related_concepts"] = "obsolete cross-phase concept label"
    pre_row["_aegis_needed_for"] = []
    pre_map["needed_for"] = {"PRC-0001": []}
    release.stage_pre_release(
        db,
        job,
        target_chapter_id=chapter.id,
        pre_map=pre_map,
        pre_questions=_pre_questions(),
        inventory=copy.deepcopy(SOURCE_INVENTORY),
        reason="authoritative-empty related-concepts regression",
    )
    db.refresh(job)

    payload = release.release_payload(job, lane=release.LANE_PRE)
    assert payload is not None
    staged = payload["records"][0]
    assert staged["related_concepts"] == "obsolete cross-phase concept label"
    assert staged[release.PRE_ROW_RELATED_CONCEPTS_FIELD] == ""

    _chapter, concepts, _records, defects = (
        release_files.transient_release_hierarchy(db, job, payload=payload)
    )
    assert defects == []
    assert concepts[0].related_concepts == ""

    result = publication.upload_release_to_database(
        db, job.id, owner_sub=OWNER, lane=release.LANE_PRE,
    )
    persisted_ids = (
        result["created_concept_ids"] + result["updated_concept_ids"]
    )
    assert len(persisted_ids) == 1
    persisted = db.get(models.Concept, persisted_ids[0])
    assert persisted is not None
    assert persisted.related_concepts == ""
