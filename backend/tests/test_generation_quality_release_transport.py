"""The carried quality policy survives Concept staging and Master snapshots."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import assessment_release_snapshot as snapshot
from app.services import assessment_materialization as materialization
from app.services import build_concepts_release as release
from app.services import build_concepts_release_contract as contract
from app.services import generation, generation_quality_policy as quality
from app.services import release_refiner
from app.services.phase3 import envelope, prequestions
from tests import test_build_concepts_release as release_fixtures
from tests import test_phase3_settle_golden as golden
from tests import test_pre_release_lane_wiring as lane_fixtures


@pytest.mark.parametrize("current", [False, True])
def test_same_policy_reaches_both_staged_lanes_and_master_metadata(db, monkeypatch, current):
    job, chapter = release_fixtures._job(db)
    pre_map = lane_fixtures._pre_map()
    prerequisites = [{
        "prerequisite_id": "PR-0001", "text": "Count a familiar small collection",
        "retained_atoms": [{"atom_id": "PA-0001", "text": "Match each object to one counting word."}],
    }]
    pre_map["rows"][0]["_aegis_pre_prerequisites"] = copy.deepcopy(prerequisites)
    if current:
        pre_map[quality.KEY] = quality.VERSION
    bundle = generation.phase3_pre_release_bundle(pre_map, lane_fixtures._pre_questions())
    captured = {generation.PHASE3_PRE_RELEASE_FIELD: bundle}
    release.stage_release(
        db, job, target_chapter_id=chapter.id,
        records=release_fixtures._rendered_records(),
        inventory=release_fixtures._inventory(), mined_types=release_fixtures._mined_types(),
        generation_policy=release.generation_quality_fields(captured),
        live_example_adjudication=False,
    )
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id, pre_map=pre_map,
        pre_questions=lane_fixtures._pre_questions(), inventory=release_fixtures._inventory(),
    )
    for lane in (release.LANE_POST, release.LANE_PRE):
        staged = release.release_payload(job, lane=lane)
        bridge = snapshot.build(db, job, staged)
        assert quality.active(staged) is current
        assert quality.active(bridge["metadata"]) is current
        assert (quality.KEY in staged) is current
        assert (quality.KEY in bridge["metadata"]) is current
        concepts = list(bridge["concept_records_by_key"].values())
        if current and lane == release.LANE_PRE:
            assert concepts[0]["_aegis_pre_prerequisites"] == prerequisites
            assert bridge["metadata"]["prerequisite_evidence"] == [{
                "concept_key": concepts[0]["concept_key"],
                "pre_concept_id": concepts[0]["pre_concept_id"],
                "prerequisites": prerequisites,
            }]
            payload = materialization._decision_payload(
                None, {"sheet_kind": "Subjective"}, candidate_id="CAND-1",
                meta=bridge["metadata"], context=concepts,
                descriptive_answer_capacity=20,
            )
            assert payload["metadata"]["prerequisite_evidence"][0]["prerequisites"] == prerequisites
            calls = []

            def record(system, user, **kwargs):
                calls.append(str(kwargs.get("prompt_cache_prefix") or "") + user)
                return {}

            monkeypatch.setattr(generation, "_openai_json", record)
            materialization._live_materialize(payload)
            materialization._live_critic({**payload, "proposed_decision": {}})
            assert len(calls) == 2
            assert all("Match each object to one counting word." in call for call in calls)
            assert all("QINV-" not in call for call in calls)
        else:
            assert "prerequisite_evidence" not in bridge["metadata"]
            assert all("_aegis_pre_prerequisites" not in concept for concept in concepts)


def test_empty_pre_authority_still_transports_post_policy_and_audit_is_private(db):
    job, chapter = release_fixtures._job(db)
    pre_map = {**lane_fixtures._pre_map(rows=False), quality.KEY: quality.VERSION}
    bundle = generation.phase3_pre_release_bundle(pre_map, {})

    def deposit(*, records, phase3_pre_release, grounding_audit_job):
        raise AssertionError("capture must not call the database deposit")

    token = contract._RELEASE_CAPTURE.set(None)
    try:
        contract._capture_deposit(deposit, (), {
            "records": release_fixtures._rendered_records(),
            "phase3_pre_release": bundle, "grounding_audit_job": job,
        })
        captured = copy.deepcopy(contract._RELEASE_CAPTURE.get())
    finally:
        contract._RELEASE_CAPTURE.reset(token)
    assert captured[quality.KEY] == quality.VERSION
    assert captured["phase3_pre_release"]["pre_map"]["rows"] == []
    assert release.generation_quality_fields({quality.KEY: "historical-version"}) == {}
    assert release.generation_quality_fields({}) == {}
    foreign = copy.deepcopy(bundle)
    foreign[generation.PRE_RUN_IDENTITY_FIELD] = {"chapter_id": chapter.id + 1}
    assert release.generation_quality_fields(
        {generation.PHASE3_PRE_RELEASE_FIELD: foreign}, chapter_id=chapter.id,
    ) == {}
    row = {"concept_title": "A concept", "_aegis_concept_coherence": {"decision_key": "recorded"}}
    assert release._strip_release_fields(row) == {"concept_title": "A concept"}


@pytest.mark.parametrize("lane", ["pre", "post"])
def test_concept_refiners_receive_only_the_carried_policy(db, monkeypatch, lane):
    job, chapter = release_fixtures._job(db)
    seen = []

    def refine(records, *, metadata, **kwargs):
        seen.append(copy.deepcopy(metadata))
        return records, {"changes": []}, []

    monkeypatch.setattr(release_refiner, "refine_release", refine)
    for current in (False, True):
        if lane == "pre":
            source = lane_fixtures._pre_map()
            if current:
                source[quality.KEY] = quality.VERSION
            release._refine_pre_records(db, job, source)
        else:
            source = {"records": release_fixtures._rendered_records()}
            if current:
                source[quality.KEY] = quality.VERSION
            contract._refine_captured_records(db, job, chapter.id, source)
    assert quality.KEY not in seen[0]
    assert seen[1][quality.KEY] == quality.VERSION
    if lane == "pre":
        assert seen[1]["inventory"] == {}
        assert seen[1]["source_text"] == ""


@pytest.mark.parametrize("current", [False, True])
def test_reviewed_pre_regeneration_preserves_policy_without_mutating_source_envelope(db, monkeypatch, tmp_path, current):
    job, chapter = release_fixtures._job(db)
    pre_map = lane_fixtures._pre_map()
    if current:
        pre_map[quality.KEY] = quality.VERSION
    release.stage_pre_release(
        db, job, target_chapter_id=chapter.id,
        pre_map=pre_map, pre_questions=lane_fixtures._pre_questions(), inventory={},
    )
    release.initialize_concept_review(db, job, target_chapter_id=chapter.id)
    release.update_concept_review_state(
        db, job, reviewed_lane="pre", corrected_filename="reviewed-pre.xlsx",
        corrected_changed=True,
    )
    staged = release.release_payload(job, lane=release.LANE_PRE)
    staged["_reviewed_pre_input"] = {"filename": "reviewed-pre.xlsx", "sha256": "a" * 64}
    inventory = copy.deepcopy(dict(job.question_inventory or {}))
    inventory[release.PRE_RELEASE_KEY] = staged
    job.question_inventory = inventory
    db.commit()
    env = envelope.load(golden.GOLDEN / "rne_envelope.json")
    # The reviewed staged release is the explicit policy authority; do not
    # mutate the original historical source envelope when deriving the review.
    original = json.dumps({"envelope": env})
    source_path = tmp_path / "source.phase3-envelope.json"
    source_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(contract.uploads, "source_artifact_directory", lambda _: tmp_path)
    seen = []

    def build(received_env, received_map, *, store):
        seen.append((copy.deepcopy(received_env), copy.deepcopy(received_map)))
        return lane_fixtures._pre_questions()

    monkeypatch.setattr(prequestions, "build", build)
    result = contract._regenerate_pre_questions_after_review(db, job)
    assert result is not None
    assert quality.active(seen[0][0]) is current
    assert quality.active(seen[0][1]) is current
    assert quality.active(release.release_payload(job, lane=release.LANE_PRE)) is current
    assert source_path.read_text(encoding="utf-8") == original
    if current:
        assert seen[0][0]["envelope_sha256"] != env["envelope_sha256"]
        assert seen[0][0]["metadata"]["_reviewed_pre_input"]["source_envelope_sha256"] == env["envelope_sha256"]
    else:
        assert seen[0][0] == env
