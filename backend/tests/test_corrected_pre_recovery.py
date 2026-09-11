"""Job 129: corrected scope must not replay an empty-success Pre bank."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services import build_concepts_release as release
from app.services import build_concepts_release_contract as contract
from app.services import assessment_release_run as master_run
from app.services import generation_quality_policy as quality
from app.services import generation_repair_policy as repair
from app.services import model_provider, release_workbook_edits as edits
from app.services.phase3 import envelope, prequestions, pre_coverage
from tests import test_build_concepts_release as fixtures
from tests import test_phase3_settle_golden as golden


FIXTURE = Path(__file__).parent / "golden" / "job129_reviewed_pre_scope.json"


def _source():
    return json.loads(FIXTURE.read_text())


def _job129(db, tmp_path, monkeypatch):
    source = _source()
    job, chapter = fixtures._job(db)
    release.stage_release(
        db, job, target_chapter_id=chapter.id, records=fixtures._rendered_records(),
        inventory=fixtures._inventory(), mined_types=fixtures._mined_types(),
    )
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id,
        pre_map={"rows": source["records"], "topics": source["pre_topics"],
                 "analysis": source["analysis"], "needed_for": source["needed_for"]},
        pre_questions={"plans": source["generated_question_plans"], "questions": {}}, inventory={},
    )
    release.initialize_concept_review(db, job, target_chapter_id=chapter.id)
    release.update_concept_review_state(
        db, job, reviewed_lane="pre", corrected_filename="reviewed-pre.xlsx", corrected_changed=True,
    )
    payload = release.release_payload(job, lane="pre")
    payload["_reviewed_pre_input"] = source["_reviewed_pre_input"]
    durable = copy.deepcopy(dict(job.question_inventory))
    durable[release.PRE_RELEASE_KEY] = payload
    durable[release.CONCEPT_REVIEW_KEY].update({
        "pre_questions_regenerated_for_uid": payload[release.STAGED_RELEASE_UID_FIELD],
        "pre_questions_count": 0,
    })
    job.question_inventory = durable
    db.commit()
    db.refresh(job)
    env = envelope.load(golden.GOLDEN / "rne_envelope.json")
    env["metadata"].update({"subject": "English", "grade": "10", model_provider.PROFILE_KEY: model_provider.legacy_profile()})
    env["envelope_sha256"] = envelope.seal_sha256(env)
    source_path = tmp_path / "source.phase3-envelope.json"
    original_source = json.dumps({"envelope": env})
    source_path.write_text(original_source)
    monkeypatch.setattr(contract.uploads, "source_artifact_directory", lambda _: tmp_path)
    return job, source, env, source_path, original_source


def _verified(_request):
    return {"verdict": "verified", "confidence": 0.99, "issues": []}


def test_job129_revised_scope_authors_all_eight_and_replays_without_spend(db, monkeypatch, tmp_path):
    job, source, original_env, source_path, original_source = _job129(db, tmp_path, monkeypatch)
    before_post = copy.deepcopy(release.release_payload(job, lane="post"))
    before_pre = copy.deepcopy(release.release_payload(job, lane="pre"))
    expected_ids = [row["_pre_concept_id"] for row in source["records"]]
    calls = []
    seen_generation = []
    real_build = prequestions.build

    def provider(request):
        calls.append(copy.deepcopy(request))
        assert model_provider.bound_profile() == model_provider.new_profile()
        assert repair.active(request)
        if request["stage"] == "prequestions.plan":
            assert [row["pre_concept_id"] for row in request["pre_concepts"]] == expected_ids
            for row, accepted in zip(request["pre_concepts"], source["records"]):
                assert row["concept_title"] == accepted["concept_title"]
                assert row[repair.REVIEWED_PRE_SCOPE_FIELD]["authored_scope"]["concept_details"] == accepted["concept_details"]
                assert row["prerequisites"] == []
                assert row["needed_for"] == []
            return {"plans": [{"pre_concept_id": cid, "total": 1,
                    "split": [{"tier": "Basic", "count": 1}],
                    "rationale": "One independent diagnostic checks the accepted foundational scope."} for cid in expected_ids]}
        row = request["pre_concept"]
        return {"questions": [{"question_id": "PRQ-0001", "tier": "Basic",
                "question_text": "Give a short example of " + row["concept_title"] + ".",
                "answer": "A brief example demonstrating the accepted foundation.",
                "rationale": "Diagnoses the accepted concept in a new example."}]}

    def build(env, pre_map, *, store):
        seen_generation.append((copy.deepcopy(env), copy.deepcopy(pre_map)))
        return real_build(env, pre_map, store=store, provider=provider, critic=_verified)

    monkeypatch.setattr(prequestions, "build", build)
    with model_provider.bind_profile(model_provider.legacy_profile()):
        result = contract._regenerate_pre_questions_after_review(db, job)
        assert model_provider.bound_profile() == model_provider.legacy_profile()
    assert result["pre_questions_count"] == 8
    accepted = release.release_payload(job, lane="pre")
    assert len(accepted["generated_questions"]) == 8
    assert [row["pre_concept_id"] for row in accepted["generated_questions"]] == expected_ids
    assert contract._reviewed_pre_missing_question_ids(accepted) == []
    assert accepted[model_provider.PROFILE_KEY] == model_provider.new_profile()
    assert repair.active(accepted) and quality.active(accepted)
    for revised, authored in zip(accepted["records"], source["records"]):
        for field in ("concept_title", "concept_details", "topic", "keywords"):
            assert revised[field] == authored[field]
        assert revised["_aegis_pre_prerequisites"] == []
        assert revised["_aegis_needed_for"] == []
        assert "_source_grounding_contract" not in revised
    audit = accepted["_reviewed_pre_superseded"]["scope_revisions"][0]
    assert audit["rows"]["PRC-0001"]["_aegis_pre_prerequisites"] == source["records"][0]["_aegis_pre_prerequisites"]
    assert accepted["analysis"]["inventory"] == []
    assert accepted["needed_for"] == {} and accepted["pre_topics"] == []
    assert release.release_payload(job, lane="post") == before_post
    assert source_path.read_text() == original_source
    assert seen_generation[0][0]["envelope_sha256"] != original_env["envelope_sha256"]
    assert seen_generation[0][0]["metadata"][pre_coverage.RULE_FIELD] == pre_coverage.owner_rule()
    assert before_pre["generated_questions"] == []
    assert len(calls) == 9
    # Crash before staging can replay every complete API decision unpaid.
    again = real_build(*seen_generation[0], store=contract.kernel.DecisionStore(tmp_path / "phase3-decisions"),
                       provider=lambda _: pytest.fail("paid decision replayed"), critic=_verified)
    assert sum(map(len, again["questions"].values())) == 8
    monkeypatch.setattr(prequestions, "build", lambda *a, **k: pytest.fail("successful review resume regenerated"))
    assert contract._regenerate_pre_questions_after_review(db, job) is None


@pytest.mark.parametrize("question_count", [0, 7])
def test_missing_accepted_scope_never_records_success_or_replaces_concepts(db, monkeypatch, tmp_path, question_count):
    job, source, _, source_path, original_source = _job129(db, tmp_path, monkeypatch)
    original_pre = copy.deepcopy(release.release_payload(job, lane="pre"))
    original_post = copy.deepcopy(release.release_payload(job, lane="post"))
    questions = {
        row["_pre_concept_id"]: [{"pre_question_id": row["_pre_concept_id"] + "-PRQ-0001"}]
        for row in source["records"][:question_count]
    }
    monkeypatch.setattr(prequestions, "build", lambda *a, **k: {"questions": questions, "plans": source["generated_question_plans"]})
    with pytest.raises(ValueError, match="Pre question generation is incomplete"):
        contract._regenerate_pre_questions_after_review(db, job)
    state = release.concept_review_state(job)
    assert "pre_questions_regenerated_for_uid" not in state
    failure = state["pre_questions_regeneration_failure"]
    assert failure["missing_pre_concept_ids"] == [row["_pre_concept_id"] for row in source["records"][question_count:]]
    assert failure["questions"]["questions"] == questions
    assert release.release_payload(job, lane="pre") == original_pre
    assert release.release_payload(job, lane="post") == original_post
    assert source_path.read_text() == original_source


def test_edited_scope_invalidation_preserves_unedited_evidence_and_exact_prose():
    source = _source()
    previous = copy.deepcopy(source["records"])
    previous[0]["concept_details"] = "Original cells teaching."
    candidate = copy.deepcopy(source)
    edits.prepare_reviewed_pre_scope(candidate, {"PRC-0001"}, previous_records=previous)
    assert candidate["records"][0]["concept_details"] == source["records"][0]["concept_details"]
    assert candidate["records"][0]["_aegis_pre_prerequisites"] == []
    assert candidate["records"][1]["_aegis_pre_prerequisites"] == source["records"][1]["_aegis_pre_prerequisites"]
    assert candidate["_reviewed_pre_superseded"]["scope_revisions"][0]["rows"]["PRC-0001"]["concept_details"] == "Original cells teaching."
    assert candidate["analysis"]["allotments"] == {"PLA-0001": "PRC-0002"}
    assert candidate["pre_topics"][0]["pre_concept_ids"] == ["PRC-0002"]
    assert all(row[repair.REVIEWED_PRE_SCOPE_FIELD]["authority"] == "reviewer_accepted_concept" for row in candidate["records"])


@pytest.mark.parametrize("lane, stamped, expected_current", [("pre", True, True), ("pre", False, False), ("post", True, False)])
def test_only_new_pre_revision_binds_recorded_mini_profile(db, monkeypatch, tmp_path, lane, stamped, expected_current):
    job, _, _, _, _ = _job129(db, tmp_path, monkeypatch)
    durable = copy.deepcopy(dict(job.question_inventory))
    if stamped:
        durable[release.PRE_RELEASE_KEY][repair.KEY] = repair.VERSION
    durable[release.PRE_RELEASE_KEY][model_provider.PROFILE_KEY] = model_provider.new_profile()
    job.question_inventory = durable
    db.commit()

    @master_run._with_revised_lane_model_profile
    def read_profile(db, job_id, **kwargs):
        return model_provider.bound_profile()

    with model_provider.bind_profile(model_provider.legacy_profile()):
        observed = read_profile(db, job.id, lane=lane, owner_sub=job.owner_sub)
        assert observed == (model_provider.new_profile() if expected_current else model_provider.legacy_profile())
        assert model_provider.bound_profile() == model_provider.legacy_profile()


def test_explicit_continue_does_not_accept_prior_empty_success(db, monkeypatch, tmp_path):
    job, _, _, _, _ = _job129(db, tmp_path, monkeypatch)
    state = release.concept_review_state(job)
    state["status"] = release.CONCEPT_REVIEW_MASTER_READY
    assert contract._reviewed_pre_recovery_needed(job, state)
    # This is a pure status read; no staged payload or original envelope changes.
    before = copy.deepcopy(dict(job.question_inventory))
    assert contract._reviewed_pre_recovery_needed(job, state)
    assert dict(job.question_inventory) == before
    durable = copy.deepcopy(before)
    durable[release.CONCEPT_REVIEW_KEY] = state
    job.question_inventory = durable
    db.commit()

    def reached(*args, **kwargs):
        raise RuntimeError("recovery reached")

    monkeypatch.setattr(contract, "_regenerate_pre_questions_after_review", reached)
    with pytest.raises(RuntimeError, match="recovery reached"):
        contract.build_review_masters(db, job.id, owner_sub=job.owner_sub)


def test_explicit_pre_master_retry_reenters_question_recovery(db, monkeypatch, tmp_path):
    from types import SimpleNamespace

    job, _, _, _, _ = _job129(db, tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(contract, "_lane_master_eligibility", lambda *a, **k: (True, ""))
    monkeypatch.setattr(contract, "_regenerate_pre_questions_after_review", lambda *a, **k: calls.append("recover"))

    def master(*args, **kwargs):
        assert calls == ["recover"]
        return SimpleNamespace(id=42)

    monkeypatch.setattr(master_run, "run_pre_release_for_job", master)
    result = contract.rebuild_lane_master(db, job.id, "pre", owner_sub=job.owner_sub)
    assert result.id == 42


def _unchanged_current_bank(job, db, *, count=0, current=True):
    durable = copy.deepcopy(dict(job.question_inventory))
    payload = durable[release.PRE_RELEASE_KEY]
    if current:
        edits.prepare_reviewed_pre_scope(payload, {row["_pre_concept_id"] for row in payload["records"]})
        payload[model_provider.PROFILE_KEY] = model_provider.new_profile()
    payload["generated_questions"] = [
        {"pre_concept_id": row["_pre_concept_id"], "pre_question_id": row["_pre_concept_id"] + "-PRQ-0001",
         "question_text": "Already accepted question " + str(index), "answer": "Accepted answer", "rationale": "Accepted scope", "tier": "Basic"}
        for index, row in enumerate(payload["records"][:count])
    ]
    for row in payload["records"][:count]:
        payload["generated_question_plans"][row["_pre_concept_id"]] = {
            "total": 1, "split": [{"tier": "Basic", "count": 1}],
            "rationale": "Accepted plan for the completed question.",
        }
    state = durable[release.CONCEPT_REVIEW_KEY]
    state["corrected_inputs"] = {"pre": {"changed": False, "accepted_original": True}}
    state["reviewed_lanes"] = ["pre", "post"]
    state["status"] = release.CONCEPT_REVIEW_REVIEWED
    job.question_inventory = durable
    db.commit()
    db.refresh(job)
    return copy.deepcopy(payload["generated_questions"])


@pytest.mark.parametrize("count", [0, 7])
def test_unchanged_current_q48_bank_recovers_only_missing_ids(db, monkeypatch, tmp_path, count):
    job, source, _, source_path, original_source = _job129(db, tmp_path, monkeypatch)
    existing = _unchanged_current_bank(job, db, count=count)
    before_post = copy.deepcopy(release.release_payload(job, lane="post"))
    calls = []

    def recover(env, pre_map, *, store):
        calls.append([row["_pre_concept_id"] for row in pre_map["rows"]])
        return {"plans": {row["_pre_concept_id"]: {
            "total": 1, "split": [{"tier": "Basic", "count": 1}], "rationale": "Accepted scope.",
        } for row in pre_map["rows"]}, "questions": {row["_pre_concept_id"]: [{
            "pre_concept_id": row["_pre_concept_id"],
            "pre_question_id": row["_pre_concept_id"] + "-PRQ-0001",
            "question_text": "Recovered question", "answer": "Recovered answer",
            "rationale": "Accepted scope", "tier": "Basic",
        }] for row in pre_map["rows"]}}

    monkeypatch.setattr(prequestions, "build", recover)
    assert contract._reviewed_pre_recovery_needed(job, release.concept_review_state(job))
    result = contract._regenerate_pre_questions_after_review(db, job)
    assert calls == [[row["_pre_concept_id"] for row in source["records"][count:]]]
    assert result["pre_questions_count"] == 8
    bank = release.release_payload(job, lane="pre")["generated_questions"]
    assert bank[:count] == existing
    assert release.release_payload(job, lane="post") == before_post
    assert source_path.read_text() == original_source
    assert contract._regenerate_pre_questions_after_review(db, job) is None
    assert len(calls) == 1


def test_unchanged_historical_empty_bank_is_not_implicitly_revised(db, monkeypatch, tmp_path):
    job, _, _, _, _ = _job129(db, tmp_path, monkeypatch)
    _unchanged_current_bank(job, db, current=False)
    before = copy.deepcopy(dict(job.question_inventory))
    monkeypatch.setattr(prequestions, "build", lambda *a, **k: pytest.fail("historical work implicitly upgraded"))
    assert not contract._reviewed_pre_recovery_needed(job, release.concept_review_state(job))
    assert contract._regenerate_pre_questions_after_review(db, job) is None
    assert dict(job.question_inventory) == before


@pytest.mark.parametrize("count", [0, 7])
def test_current_pre_master_cannot_start_with_missing_accepted_questions(db, monkeypatch, tmp_path, count):
    job, _, _, _, _ = _job129(db, tmp_path, monkeypatch)
    bank = _unchanged_current_bank(job, db, count=count)
    monkeypatch.setattr(master_run.release_snapshot, "build", lambda *a, **k: pytest.fail("Master snapshot started for incomplete bank"))
    with pytest.raises(master_run.ReleaseRunError, match="Pre Master requires generated questions"):
        master_run.run_release_for_job(db, job.id, owner_sub=job.owner_sub, lane="pre", generated_questions=bank)


def test_current_empty_concept_map_does_not_trigger_missing_question_gate(db, monkeypatch, tmp_path):
    job, _, _, _, _ = _job129(db, tmp_path, monkeypatch)
    _unchanged_current_bank(job, db)
    durable = copy.deepcopy(dict(job.question_inventory))
    durable[release.PRE_RELEASE_KEY]["records"] = []
    job.question_inventory = durable
    db.commit()

    def reached(*args, **kwargs):
        raise RuntimeError("normal snapshot reached")

    monkeypatch.setattr(master_run.release_snapshot, "build", reached)
    with pytest.raises(RuntimeError, match="normal snapshot reached"):
        master_run.run_release_for_job(db, job.id, owner_sub=job.owner_sub, lane="pre", generated_questions=[])


def test_explicit_post_revision_uses_its_profile_without_changing_pre_or_source(db, monkeypatch, tmp_path):
    job, _, _, _, _ = _job129(db, tmp_path, monkeypatch)
    durable = copy.deepcopy(dict(job.question_inventory))
    durable[release.RELEASE_KEY][repair.KEY] = repair.VERSION
    durable[release.RELEASE_KEY][model_provider.PROFILE_KEY] = model_provider.new_profile()
    job.question_inventory = durable
    db.commit()

    @master_run._with_revised_lane_model_profile
    def profile(db, job_id, **kwargs):
        return model_provider.bound_profile()

    with model_provider.bind_profile(model_provider.legacy_profile()):
        assert profile(db, job.id, lane="post", owner_sub=job.owner_sub) == model_provider.new_profile()
        assert profile(db, job.id, lane="pre", owner_sub=job.owner_sub) == model_provider.legacy_profile()
        assert model_provider.bound_profile() == model_provider.legacy_profile()
