"""A suspended sibling build keeps finished releases and durable decisions."""
import copy
import threading

import pytest

from app import models
from app.services import assessment_release_run as run
from app.services import build_concepts_release as release
from app.services import build_concepts_release_contract as contract
from app.services import run_control
from app.services.phase3 import kernel
from tests.test_release_core import (
    OWNER, _authorities, _both_lanes_job, _chapter_with_concepts,
    _decision_context, _run_both_lanes,
)
from tests.test_assessment_pre_release_lane import _cells, _generated_authorities


def test_review_master_resume_reuses_finished_lane_and_paid_decisions(
    db, monkeypatch, tmp_path,
):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    release.initialize_concept_review(db, job, target_chapter_id=chapter.id)
    pre_finished = threading.Event()
    post_calls = {}
    authorities, _ = _authorities(db, chapter, calls=post_calls)
    generated, _ = _generated_authorities()
    pre_key = next(iter(run.release_snapshot.build(
        db, job, release.release_payload(job, lane="pre"),
    )["concept_records_by_key"]))
    lane_calls = []
    should_defer = True

    def rebuild(lane_db, job_id, lane, **kwargs):
        lane_calls.append(lane)
        if lane == "pre":
            result = run.run_pre_release_for_job(
                lane_db, job_id, owner_sub=OWNER,
                blueprint_cells=_cells(2, pre_key), authorities=generated,
                **_decision_context(kernel.DecisionStore(tmp_path / "pre")),
            )
            pre_finished.set()
            return result

        def stage(stage, done=None, total=None):
            if stage == "routing" and should_defer:
                assert pre_finished.wait(20)
                raise run_control.RunDeferred("Post provider wave is pending")
            kwargs["stage_progress"](stage, done, total)

        return run.run_release_for_job(
            lane_db, job_id, owner_sub=OWNER, authorities=authorities,
            stage_progress=stage,
            **_decision_context(kernel.DecisionStore(tmp_path / "post")),
        )

    monkeypatch.setattr(contract, "rebuild_lane_master", rebuild)
    with pytest.raises(run_control.RunDeferred):
        contract.build_review_masters(db, job.id, owner_sub=OWNER)
    db.expire_all()
    saved_pre = db.query(models.AssessmentRelease).filter_by(job_id=job.id, lane="pre").one()
    frozen_pre = copy.deepcopy((saved_pre.id, saved_pre.release_uid, saved_pre.version,
                                saved_pre.payload, saved_pre.workbook_hashes, saved_pre.publication))
    assert release.concept_review_state(job)["status"] == release.CONCEPT_REVIEW_MASTER_BUILDING
    paid_before = {key: len(post_calls[key]) for key in ("cells", "materialize", "marking")}
    assert all(paid_before.values())
    decision_bytes = {path.name: path.read_bytes() for path in (tmp_path / "post").glob("*.json")}
    assert decision_bytes

    should_defer = False
    resumed = contract.build_review_masters(db, job.id, owner_sub=OWNER)
    db.expire_all()
    saved_pre = db.query(models.AssessmentRelease).filter_by(job_id=job.id, lane="pre").one()
    assert (saved_pre.id, saved_pre.release_uid, saved_pre.version, saved_pre.payload,
            saved_pre.workbook_hashes, saved_pre.publication) == frozen_pre
    assert lane_calls.count("pre") == 1 and lane_calls.count("post") == 2
    assert {key: len(post_calls[key]) for key in paid_before} == paid_before
    assert all((tmp_path / "post" / name).read_bytes() == data
               for name, data in decision_bytes.items())
    assert resumed["all_four_outputs_ready"] is True
    assert resumed["master_outputs"]["pre"]["release_id"] == saved_pre.id
    assert db.query(models.AssessmentRelease).filter_by(job_id=job.id).count() == 2


@pytest.mark.parametrize("changed", ["uid", "version", "source", "unpublished", "superseded"])
def test_master_resume_requires_exact_published_review_input(db, changed):
    chapter = _chapter_with_concepts(db)
    job = _both_lanes_job(db, chapter)
    _pre, post = _run_both_lanes(db, job, chapter)
    assert contract._completed_master_for_reviewed_input(
        db, job.id, "post", owner_sub=OWNER,
    ).id == post.id
    if changed in {"uid", "version"}:
        context = dict(post.provider_identity)
        context["staged_release_" + changed] = "different" if changed == "uid" else 999
        post.provider_identity = context
    elif changed == "source":
        post.payload = {**post.payload, "source_concept_release_sha256": "different"}
    else:
        post.state = "materialized" if changed == "unpublished" else "superseded"
    db.commit()
    assert contract._completed_master_for_reviewed_input(
        db, job.id, "post", owner_sub=OWNER,
    ) is None
