"""Three-step workflow (Q51): Step 1 extracts questions as is; Step 2 polishes.

New uploads mint ``reviewed_file_workflow_policy.V2``. Under V2 the Step 1
inventory join makes no polishing request and records an explicit deferral;
V1 and historical runs keep the recorded Step 1 polish byte-for-byte.
"""
from __future__ import annotations

import copy
import json
from types import ModuleType

import pytest

from app import config, models
from app.services import canonical_source_phase3, checkpoints, generation
from app.services import model_provider, model_routing_run, progress
from app.services import question_polishing, question_polishing_contract as contract
from app.services import reviewed_file_workflow_policy as workflow
from app.services import source_task_polishing_policy as source_format
from tests.test_model_routing_run import _job as source_job

UNKNOWN = "concepts-then-reviewed-masters-2026-09-11-v9"
SOURCE = {"qid": "QINV-1", "raw_task": "Tick the box.", "source_kind": "exercise"}
POLISH_FIELDS = (
    "polished_task", "frozen_task_text", "polish_audit", "polish_flag",
    "polish_note", "polish_review_required", source_format.FIELD,
)


@pytest.fixture
def isolated_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")


@pytest.fixture
def polish_state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()
    yield
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()


def _forbid_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Step 1 under the current workflow must not spend on question polishing")
    monkeypatch.setattr(generation, "_openai_json", forbidden)


def _scripted_polish(monkeypatch):
    recorded = []

    def api(system, user, **kwargs):
        recorded.append(kwargs["purpose"])
        if kwargs["purpose"] == "advisory_critic":
            return {"items": [{"qid": "QINV-1", "verdict": "verified", "issues": []}]}
        return {"items": [{"qid": "QINV-1", "polished_task": "Select the box.", "note": ""}]}

    monkeypatch.setattr(generation, "_openai_json", api)
    return recorded


def _join(meta):
    return generation._finish_inventory_with_topics(
        {"items": [copy.deepcopy(SOURCE)]}, [], meta=meta, sections=[], records=None,
    )


def _log_messages(events):
    return [event["message"] for event in events if event.get("type") == "log"]


# --- (a) the policy module -------------------------------------------------

def test_versions_are_recorded_and_current_is_v2():
    assert workflow.VERSIONS == (workflow.V1, workflow.V2)
    assert workflow.VERSION == workflow.V2
    assert workflow.V1 != workflow.V2


@pytest.mark.parametrize("version", [workflow.V1, workflow.V2])
@pytest.mark.parametrize("shape", ["flat", "nested"])
def test_policy_echoes_the_recorded_version(version, shape):
    payload = ({workflow.KEY: version} if shape == "flat"
               else {"metadata": {workflow.KEY: version}, "subject": "Mathematics"})
    assert workflow.version_of(payload) == version
    assert workflow.active(payload) is True
    assert workflow.fields(payload) == {workflow.KEY: version}
    assert workflow.defers_polishing(payload) is (version == workflow.V2)


def test_flat_stamp_wins_over_nested_metadata():
    payload = {workflow.KEY: workflow.V1, "metadata": {workflow.KEY: workflow.V2}}
    assert workflow.version_of(payload) == workflow.V1
    assert not workflow.defers_polishing(payload)


@pytest.mark.parametrize("value", [
    None, {}, {"metadata": {}}, {workflow.KEY: UNKNOWN},
    {"metadata": {workflow.KEY: UNKNOWN}}, {workflow.KEY: None}, "text", 1, [],
])
def test_unstamped_or_unknown_payloads_record_no_version(value):
    assert workflow.version_of(value) is None
    assert workflow.active(value) is False
    assert workflow.fields(value) == {}
    assert workflow.defers_polishing(value) is False


@pytest.mark.parametrize("version", [None, workflow.V1, workflow.V2])
def test_bind_run_accepts_none_and_recorded_versions(version):
    assert workflow.bound_version() is None
    with workflow.bind_run(version):
        assert workflow.bound_version() == version
        assert workflow.run_fields() == ({workflow.KEY: version} if version else {})
        assert workflow.run_defers_polishing() is (version == workflow.V2)
    assert workflow.bound_version() is None
    assert workflow.run_fields() == {}
    assert workflow.run_defers_polishing() is False


@pytest.mark.parametrize("version", [UNKNOWN, "", 1, {}])
def test_bind_run_rejects_unknown_versions(version):
    with pytest.raises(ValueError, match="reviewed-file workflow policy"):
        with workflow.bind_run(version):
            pytest.fail("an unknown workflow version must never bind")
    assert workflow.bound_version() is None


def test_nested_bindings_restore_the_outer_version():
    with workflow.bind_run(workflow.V1):
        with workflow.bind_run(workflow.V2):
            assert workflow.run_defers_polishing()
        assert workflow.bound_version() == workflow.V1
        assert not workflow.run_defers_polishing()


# --- (b)/(c) routing records ------------------------------------------------

def test_fresh_upload_mints_v2_and_historical_upload_binds_none(isolated_artifacts):
    fresh = source_job()
    with model_routing_run.bind_job(fresh):
        metadata = generation._metadata(subject="Mathematics", grade="1")
        assert workflow.bound_version() == workflow.V2
        assert workflow.run_fields() == {workflow.KEY: workflow.V2}
        assert workflow.run_defers_polishing()
    assert metadata[workflow.KEY] == workflow.V2
    assert workflow.defers_polishing(metadata)
    record = json.loads(model_routing_run._record_path(fresh).read_text(encoding="utf-8"))
    assert record[workflow.KEY] == workflow.V2
    assert record["profile"] == model_provider.new_profile()

    historical = source_job(upload_storage_key="42/converted.pdf", mmd_text="already paid source")
    with model_routing_run.bind_job(historical):
        assert workflow.bound_version() is None
        assert workflow.run_fields() == {}
        assert workflow.KEY not in generation._metadata(subject="Mathematics", grade="1")
    record = json.loads(model_routing_run._record_path(historical).read_text(encoding="utf-8"))
    assert record == {"version": 1, "profile": None}


def test_recorded_v1_upload_binds_v1_without_upgrade(isolated_artifacts):
    job = source_job()
    model_routing_run.save_profile_for_job(
        job, model_provider.new_profile(), workflow_version=workflow.V1,
    )
    saved = model_routing_run._record_path(job).read_bytes()
    assert json.loads(saved)[workflow.KEY] == workflow.V1
    with model_routing_run.bind_job(job):
        assert workflow.bound_version() == workflow.V1
        assert generation._metadata()[workflow.KEY] == workflow.V1
        assert not workflow.run_defers_polishing()
    assert model_routing_run._record_path(job).read_bytes() == saved
    assert model_routing_run.profile_for_job(job) == model_provider.new_profile()
    assert model_routing_run._record_path(job).read_bytes() == saved


def test_routing_record_refuses_unknown_workflow_versions(isolated_artifacts):
    job = source_job()
    with pytest.raises(ValueError, match="reviewed-file workflow policy"):
        model_routing_run.save_profile_for_job(
            job, model_provider.new_profile(), workflow_version=UNKNOWN,
        )
    assert not model_routing_run._record_path(job).exists()
    model_routing_run.save_profile_for_job(job, model_provider.new_profile())
    path = model_routing_run._record_path(job)
    record = json.loads(path.read_text(encoding="utf-8"))
    record[workflow.KEY] = UNKNOWN
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed-file workflow policy"):
        model_routing_run.recorded_profile_for_job(job)


# --- (d) the Step 1 inventory wrappers -------------------------------------

def test_deferred_marker_names_the_policy_and_the_owning_step():
    marker = contract.deferred_marker()
    assert marker["policy"] == workflow.V2
    assert marker["stage"] == "step_2_reviewed_file"
    assert "Step 1" in marker["reason"] and "Step 2" in marker["reason"]
    assert contract.deferred_marker() is not marker
    assert contract.polishing_deferred(None) is False
    assert contract.polishing_deferred({}) is False
    assert contract.polishing_deferred({workflow.KEY: workflow.V1}) is False
    assert contract.polishing_deferred({workflow.KEY: workflow.V2}) is True
    with workflow.bind_run(workflow.V2):
        assert contract.polishing_deferred(None) is True
        assert contract.polishing_deferred({workflow.KEY: workflow.V1}) is True


@pytest.mark.parametrize("binding", ["bound_run", "stamped_meta"])
def test_step_1_join_extracts_as_is_without_provider_calls_under_v2(polish_state, monkeypatch, binding):
    _forbid_provider(monkeypatch)
    assert generation._finish_inventory_with_topics._question_polishing_installed
    meta = {"subject": "Mathematics", "grade": "1"}
    if binding == "stamped_meta":
        meta[workflow.KEY] = workflow.V2
    with progress.capture_history() as events, workflow.bind_run(
        workflow.V2 if binding == "bound_run" else None,
    ):
        result = _join(meta)
    item = result["items"][0]
    assert item == SOURCE
    assert not any(field in item for field in POLISH_FIELDS)
    assert result[contract.DEFERRED_KEY] == contract.deferred_marker()
    assert result["stats"] == generation._inventory_stats(result["items"])
    assert result["stats"]["exercise_questions"] == 1
    deferral_lines = [
        line for line in _log_messages(events) if "Step 1" in line and "Step 2" in line
    ]
    assert len(deferral_lines) == 1
    assert "polishing" in deferral_lines[0]
    # The AS-IS wording is what every public Example flows through.
    assert generation._inventory_task_text(item) == "Tick the box."
    assert not source_format.applies(item)


@pytest.mark.parametrize("binding", ["bound_v1", "stamped_v1", "historical"])
def test_step_1_join_still_polishes_under_v1_and_historical_runs(polish_state, monkeypatch, binding):
    recorded = _scripted_polish(monkeypatch)
    meta = {"subject": "Mathematics", "grade": "1"}
    if binding == "stamped_v1":
        meta[workflow.KEY] = workflow.V1
    with progress.capture_history() as events, workflow.bind_run(
        workflow.V1 if binding == "bound_v1" else None,
    ):
        result = _join(meta)
    item = result["items"][0]
    assert item["raw_task"] == "Tick the box."
    assert item["frozen_task_text"] == item["polished_task"] == "Select the box."
    assert item[source_format.FIELD] == source_format.VERSION
    assert recorded == ["source_extraction", "advisory_critic"]
    assert contract.DEFERRED_KEY not in result
    assert not any("Step 2" in line for line in _log_messages(events))
    assert generation._inventory_task_text(item) == "Select the box."


def test_v2_polish_cache_from_a_v1_run_is_never_consulted_in_step_1(polish_state, monkeypatch):
    # A V1 run records its polish; the same inventory under V2 still ships
    # as extracted rather than replaying the recorded decision.
    recorded = _scripted_polish(monkeypatch)
    meta = {"subject": "Mathematics", "grade": "1"}
    with workflow.bind_run(workflow.V1):
        polished = _join(meta)
    assert recorded == ["source_extraction", "advisory_critic"]
    _forbid_provider(monkeypatch)
    with workflow.bind_run(workflow.V2):
        deferred = _join(meta)
    assert deferred["items"][0] == SOURCE
    assert polished["items"][0]["frozen_task_text"] == "Select the box."


def test_inline_extraction_wrapper_defers_once_and_matches_the_early_join(polish_state, monkeypatch):
    _forbid_provider(monkeypatch)
    stub = ModuleType("step_1_generation")
    stub._finish_inventory_with_topics = (
        lambda inventory, anchors, **kwargs: copy.deepcopy(inventory)
    )
    stub._extract_question_task_inventory_via_api = (
        lambda **kwargs: stub._finish_inventory_with_topics(
            {"items": [copy.deepcopy(SOURCE)]}, [], **kwargs,
        )
    )
    stub._inventory_task_text = lambda item: item["raw_task"]
    stub._refresh_inventory_from_source_anchors = lambda inventory, sections: inventory
    stub._inventory_stats = lambda items: {"count": len(items)}
    contract.install(stub)
    contract.install(stub)
    with workflow.bind_run(workflow.V2):
        with progress.capture_history() as early_events:
            early = stub._finish_inventory_with_topics(
                {"items": [copy.deepcopy(SOURCE)]}, [], meta={}, sections=[], records=[],
            )
        with progress.capture_history() as inline_events:
            inline = stub._extract_question_task_inventory_via_api(
                meta={}, sections=[], records=[],
            )
        refreshed = stub._refresh_inventory_from_source_anchors(copy.deepcopy(early), [])
    assert early == inline
    assert early["items"] == [SOURCE]
    assert early["stats"] == {"count": 1}
    assert early[contract.DEFERRED_KEY] == contract.deferred_marker()
    # The inline path passes the join wrapper and then this wrapper: one line.
    assert sum("Step 2" in line for line in _log_messages(early_events)) == 1
    assert sum("Step 2" in line for line in _log_messages(inline_events)) == 1
    assert refreshed[contract.DEFERRED_KEY] == contract.deferred_marker()
    assert refreshed["items"] == [SOURCE]


def test_anchor_refresh_keeps_the_deferral_marker_on_a_resumed_v2_inventory(polish_state, monkeypatch):
    _forbid_provider(monkeypatch)
    with workflow.bind_run(workflow.V2):
        inventory = _join({"subject": "Mathematics", "grade": "1"})
        refreshed = generation._refresh_inventory_from_source_anchors(inventory, [])
    assert refreshed[contract.DEFERRED_KEY] == contract.deferred_marker()
    assert refreshed["items"][0]["raw_task"] == "Tick the box."
    assert not any(field in refreshed["items"][0] for field in POLISH_FIELDS)


# --- (e) AS-IS display -------------------------------------------------------

def test_unpolished_item_renders_its_raw_wording_with_required_context():
    item = {
        "qid": "QINV-7", "raw_task": "Which plant is the tallest?",
        "source_kind": "exercise",
        "shared_context": "Look at the three plants in the picture.",
        "requires_context": True,
    }
    assert generation._inventory_task_text._question_polishing_installed
    assert generation._inventory_task_text(item) == (
        "Look at the three plants in the picture. Which plant is the tallest?"
    )
    assert generation._inventory_task_text({**item, "requires_context": False}) == (
        "Which plant is the tallest?"
    )
    assert generation._inventory_source_examples({"items": [item]}) == [
        "Look at the three plants in the picture. Which plant is the tallest?"
    ]


# --- (f) semantic identity ---------------------------------------------------

def test_workflow_version_changes_source_semantic_identity_per_version():
    metadata = {"subject": "Mathematics", "grade": "1"}
    unstamped = canonical_source_phase3.semantic_context_hash(metadata)
    v1 = canonical_source_phase3.semantic_context_hash({**metadata, workflow.KEY: workflow.V1})
    v2 = canonical_source_phase3.semantic_context_hash({**metadata, workflow.KEY: workflow.V2})
    assert len({unstamped, v1, v2}) == 3
    assert canonical_source_phase3.semantic_context_hash(dict(metadata)) == unstamped


# --- (g) portable checkpoints -----------------------------------------------

@pytest.mark.parametrize("version", [workflow.V1, workflow.V2])
def test_workflow_record_roundtrips_through_a_portable_checkpoint(db, version):
    from tests.test_concept_checkpoint_bundles import _job as checkpoint_job
    job = checkpoint_job(db)
    model_routing_run.save_profile_for_job(
        job, model_provider.new_profile(), workflow_version=version,
    )
    # Export may run in a worker bound to an unrelated run.
    with model_provider.bind_profile(None), workflow.bind_run(None):
        _, raw = checkpoints.export_bundle(db, job.id)
    payload = json.loads(raw)["payload"]
    assert payload[workflow.KEY] == version
    restored = checkpoints.import_bundle(db, raw)
    assert restored.id != job.id
    assert restored.question_inventory == job.question_inventory
    record = json.loads(model_routing_run._record_path(restored).read_text(encoding="utf-8"))
    assert record[workflow.KEY] == version
    with model_routing_run.bind_job(restored):
        assert workflow.bound_version() == version
        assert generation._metadata()[workflow.KEY] == version
        assert workflow.run_defers_polishing() is (version == workflow.V2)
    _, reexported = checkpoints.export_bundle(db, restored.id)
    assert json.loads(reexported)["payload"][workflow.KEY] == version


@pytest.mark.parametrize("invalid", [UNKNOWN, None, 1, {}, True])
def test_unknown_portable_workflow_stamp_is_refused_before_any_write(db, monkeypatch, invalid):
    from tests.test_concept_checkpoint_bundles import _job as checkpoint_job, _resign
    original = checkpoint_job(db)
    _, raw = checkpoints.export_bundle(db, original.id)
    bundle = json.loads(raw)
    bundle["payload"][workflow.KEY] = invalid
    _resign(bundle)
    count = db.query(models.UploadJob).count()

    def unexpected_write(*args, **kwargs):
        pytest.fail("an unknown workflow policy reached a routing-record write")

    monkeypatch.setattr(model_routing_run, "save_profile_for_job", unexpected_write)
    with pytest.raises(ValueError, match="reviewed-file workflow policy"):
        checkpoints.import_bundle(db, checkpoints._json_bytes(bundle))
    assert db.query(models.UploadJob).count() == count
