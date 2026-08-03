"""Quality-first, durable Phase 3.8 convergence disposition."""
from __future__ import annotations

import copy
import json

import pytest

from app.services import (
    canonical_source_phase38_boundary_grounding_turnover_contract as phase38,
)
from app.services import early_semantic_gate, semantic_recovery


_GROUND_FAILURE = (
    "failed exact source-block grounding before freeze for topic "
    "'The Making of Germany and Italy': CONCEPT-GROUND-0031 is not fully "
    "supported by BLK-00176/BLK-00181"
)


def _row(title: str, topic: str) -> dict:
    return {
        "concept_title": title,
        "topic": topic,
        "parent_concept": "Parent",
        "concept_details": f"Description: {title}.",
        "_phase32_origin_concept_id": "",
    }


def _records() -> list[dict]:
    rows = [
        _row(f"Grounded Concept {index:02d}", "The Age of Revolutions")
        for index in range(1, 9)
    ]
    rows.append(_row("Ungroundable Concept", "The Making of Germany and Italy"))
    rows.append(_row("Second Ungroundable", "The Making of Germany and Italy"))
    for index, row in enumerate(rows, start=1):
        row["_phase32_origin_concept_id"] = (
            f"TOPOLOGY-CONCEPT-{index:04d}"
        )
    return rows


@pytest.fixture(autouse=True)
def _clean_ledger(monkeypatch):
    phase38.reset_convergence_state()
    monkeypatch.setattr(phase38.phase3, "active_graph", lambda: None)
    monkeypatch.setattr(
        phase38.progress, "log", lambda *_args, **_kwargs: None
    )
    yield
    phase38.reset_convergence_state()


def _run(original, records=None, *, transport=True):
    def dispatched(rows, *args, **kwargs):
        if transport:
            phase38.phase22._notify_openai_transport_started()
        return original(rows, *args, **kwargs)

    return phase38._phase32_adjudicate_with_targeted_convergence(
        dispatched, records if records is not None else _records()
    )


def test_human_decision_unwind_persists_json_safe_budget(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "3")
    records = _records()
    persisted: list[dict | None] = []
    calls = 0

    def original(_rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError(_GROUND_FAILURE)
        raise semantic_recovery.HumanDecisionRequired({
            "decision_id": "phase31-ground-test",
            "context_hash": "c" * 64,
        })

    with phase38.convergence_checkpoint_context(
        scope="upload-job:41",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(semantic_recovery.HumanDecisionRequired):
            _run(original, records)

    state = next(value for value in reversed(persisted) if value is not None)
    assert state["attempts"] == 1
    assert state["feedback"]
    assert state["status"] == "active"
    assert state["dispatch_status"] == "decision_returned"
    assert state["dispatch_decision_id"] == "phase31-ground-test"
    assert state["dispatch_decision_context_hash"] == "c" * 64
    assert "retired" not in state
    assert "suppressed" not in state
    # The observed rejection is never durable without the feedback/status that
    # governs the next turn. This pins the removal of the old two-write crash
    # window between attempts and feedback.
    assert all(
        not value
        or not value.get("attempts")
        or value.get("feedback")
        for value in persisted
    )
    # No set, tuple-key, or other process-only container reached persistence.
    json.dumps(state, ensure_ascii=False, sort_keys=True)

    # Simulate a process crash after the decision-returned seal but before the
    # outer orchestration commit stores the pending decision. Resume must not
    # call the provider again from this ambiguous boundary.
    replay_calls = 0

    def must_not_replay(rows, *_args, **_kwargs):
        nonlocal replay_calls
        replay_calls += 1
        return rows

    with phase38.convergence_checkpoint_context(
        scope="upload-job:41",
        state=state,
        persist=lambda *_args: pytest.fail(
            "decision_returned must remain durable until the pause commit"
        ),
    ):
        with pytest.raises(
            phase38.Phase38ConvergenceExhausted,
            match="decision_returned",
        ):
            _run(must_not_replay, records)
    assert replay_calls == 0


def test_budget_exhaustion_gets_one_final_atomisation_pass(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    records = _records()
    original_titles = [row["concept_title"] for row in records]
    seen_titles: list[list[str]] = []
    seen_feedback: list[dict[str, str]] = []
    persisted: list[dict | None] = []

    def original(rows, *_args, **_kwargs):
        seen_titles.append([row["concept_title"] for row in rows])
        feedback = copy.deepcopy(
            phase38.phase33._EXTERNAL_GROUNDING_FEEDBACK.get() or {}
        )
        seen_feedback.append(feedback)
        if len(seen_titles) < 3:
            raise ValueError(_GROUND_FAILURE)
        # Simulate the provider's independently verified minimal refinement.
        refined = copy.deepcopy(rows)
        refined[-2]["concept_title"] = "Minimal Source-Grounded Concept"
        return refined

    with phase38.convergence_checkpoint_context(
        scope="upload-job:42",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        result = _run(original, records)

    assert len(seen_titles) == 3
    # Every pass received the complete original topology; no reduced map was
    # ever offered as a way to force completion.
    assert seen_titles == [original_titles, original_titles, original_titles]
    assert "FINAL QUALITY-FIRST VERIFICATION PASS" in next(iter(
        seen_feedback[-1].values()
    ))
    assert "retirement, deletion" in next(iter(seen_feedback[-1].values()))
    assert len(result) == len(records)
    assert result[-2]["concept_title"] == "Minimal Source-Grounded Concept"
    # Successful exact grounding deletes the complete control ledger.
    assert persisted[-1] is None


def test_ordinary_candidate_cannot_converge_by_retiring_a_concept(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "3")
    records = _records()
    original = copy.deepcopy(records)
    persisted: list[dict | None] = []
    feedback_seen: list[dict[str, str]] = []
    calls = 0

    def retires_then_refines(rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        feedback_seen.append(copy.deepcopy(
            phase38.phase33._EXTERNAL_GROUNDING_FEEDBACK.get() or {}
        ))
        if calls == 1:
            return rows[:-1]
        return rows

    with phase38.convergence_checkpoint_context(
        scope="upload-job:42-retirement",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        result = _run(retires_then_refines, records)

    assert calls == 2
    assert result == original
    assert feedback_seen[0] == {}
    assert feedback_seen[1]
    assert all("Never retire" in text for text in feedback_seen[1].values())
    assert persisted[-1] is None


def test_failed_final_pass_is_terminal_and_preserves_original_rows(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    records = _records()
    original = copy.deepcopy(records)
    seen: list[list[dict]] = []
    persisted: list[dict | None] = []

    def always_fails(rows, *_args, **_kwargs):
        seen.append(copy.deepcopy(rows))
        raise ValueError(_GROUND_FAILURE)

    with phase38.convergence_checkpoint_context(
        scope="upload-job:43",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(
            phase38.Phase38ConvergenceExhausted,
            match="No concept was retired or deleted",
        ):
            _run(always_fails, records)

    assert len(seen) == 3  # two ordinary candidates plus one final pass
    assert all(candidate == original for candidate in seen)
    assert records == original
    terminal = next(value for value in reversed(persisted) if value is not None)
    assert terminal["status"] == "exhausted"
    assert terminal["final_verification_pending"] is False
    assert "retired" not in terminal
    json.dumps(terminal, ensure_ascii=False, sort_keys=True)


def test_final_pass_cannot_report_success_after_dropping_a_concept(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    records = _records()
    original = copy.deepcopy(records)
    seen: list[list[dict]] = []
    persisted: list[dict | None] = []

    def drops_one_on_final_pass(rows, *_args, **_kwargs):
        seen.append(copy.deepcopy(rows))
        if len(seen) < 3:
            raise ValueError(_GROUND_FAILURE)
        # Even if the downstream adjudicator accepts this reduced candidate,
        # Phase 3.8 must not treat deletion as successful convergence.
        return rows[:-1]

    with phase38.convergence_checkpoint_context(
        scope="upload-job:43-loss",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(
            phase38.Phase38ConvergenceExhausted,
            match="No concept was retired or deleted",
        ):
            _run(drops_one_on_final_pass, records)

    assert len(seen) == 3
    assert all(candidate == original for candidate in seen)
    assert records == original
    terminal = next(value for value in reversed(persisted) if value is not None)
    assert terminal["status"] == "exhausted"
    assert "omitted one or more original concept lineages" in (
        terminal["terminal_reason"]
    )


def test_restart_does_not_replay_an_exhausted_final_pass(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    records = _records()
    persisted: list[dict | None] = []

    def always_fails(_rows, *_args, **_kwargs):
        raise ValueError(_GROUND_FAILURE)

    with phase38.convergence_checkpoint_context(
        scope="upload-job:44",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(phase38.Phase38ConvergenceExhausted):
            _run(always_fails, records)

    terminal = next(value for value in reversed(persisted) if value is not None)
    restarted_calls = 0

    def must_not_run(_rows, *_args, **_kwargs):
        nonlocal restarted_calls
        restarted_calls += 1
        return _rows

    with phase38.convergence_checkpoint_context(
        scope="upload-job:44",
        state=terminal,
        persist=lambda _expected, _value: None,
    ):
        with pytest.raises(phase38.Phase38ConvergenceExhausted):
            _run(must_not_run, records)
    assert restarted_calls == 0


def test_materially_repaired_checkpoint_gets_a_fresh_verification_turn(
    monkeypatch,
):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "2")
    records = _records()
    persisted: list[dict | None] = []

    def always_fails(_rows, *_args, **_kwargs):
        raise ValueError(_GROUND_FAILURE)

    with phase38.convergence_checkpoint_context(
        scope="upload-job:44-repaired",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(phase38.Phase38ConvergenceExhausted):
            _run(always_fails, records)
    terminal = next(value for value in reversed(persisted) if value is not None)

    repaired_records = copy.deepcopy(records)
    repaired_records[-2]["concept_details"] = (
        "Description: A smaller claim now grounded in the exact source block."
    )
    calls = 0

    def succeeds(rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        return rows

    with phase38.convergence_checkpoint_context(
        scope="upload-job:44-repaired",
        state=terminal,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        assert _run(succeeds, repaired_records) == repaired_records
    assert calls == 1
    assert persisted[-1] is None


def test_resolution_suppression_is_persisted_then_cleared_on_success(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "4")
    records = _records()
    decision_id = "phase31-ground-1234567890abcdef12345678"
    persisted: list[dict | None] = []
    calls = 0

    def original(rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise early_semantic_gate.TopologyRepairRequired(
                _GROUND_FAILURE,
                decision_id=decision_id,
            )
        assert decision_id in early_semantic_gate._SUPPRESSED_RESOLUTION_IDS.get()
        return rows

    with phase38.convergence_checkpoint_context(
        scope="upload-job:45",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        assert _run(original, records) == records

    saved = [value for value in persisted if value is not None]
    assert any(
        decision_id in value["suppressed_resolution_ids"]
        for value in saved
    )
    assert persisted[-1] is None


def test_scope_is_per_job_even_for_identical_source_records():
    records = _records()
    states: dict[str, dict | None] = {}

    def pause_after_failure(_rows, *_args, **_kwargs):
        if phase38.phase33._EXTERNAL_GROUNDING_FEEDBACK.get():
            raise semantic_recovery.HumanDecisionRequired({
                "decision_id": "phase31-ground-pause",
                "context_hash": "d" * 64,
            })
        raise ValueError(_GROUND_FAILURE)

    for scope in ("upload-job:46", "upload-job:47"):
        with phase38.convergence_checkpoint_context(
            scope=scope,
            state=None,
            persist=lambda _expected, value, key=scope: states.__setitem__(
                key, copy.deepcopy(value)
            ),
        ):
            with pytest.raises(semantic_recovery.HumanDecisionRequired):
                _run(pause_after_failure, records)

    assert states["upload-job:46"]["scope"] == "upload-job:46"
    assert states["upload-job:47"]["scope"] == "upload-job:47"
    assert states["upload-job:46"] is not states["upload-job:47"]


def test_distinct_grounding_issues_have_independent_candidate_budgets(
    monkeypatch,
):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "3")
    records = _records()
    second_failure = _GROUND_FAILURE.replace(
        "CONCEPT-GROUND-0031", "CONCEPT-GROUND-0032"
    )
    failures = [_GROUND_FAILURE, second_failure, _GROUND_FAILURE, second_failure]
    calls = 0
    persisted: list[dict | None] = []

    def two_independent_issues(rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls <= len(failures):
            raise ValueError(failures[calls - 1])
        return rows

    with phase38.convergence_checkpoint_context(
        scope="upload-job:48",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        assert _run(two_independent_issues, records) == records

    assert calls == 5
    bucketed = [
        state for state in persisted
        if isinstance(state, dict) and len(state.get("issue_buckets") or {}) == 2
    ]
    assert bucketed
    assert sorted(
        bucket["attempts"]
        for bucket in bucketed[-1]["issue_buckets"].values()
    ) == [2, 2]
    assert persisted[-1] is None


def test_lineage_cannot_be_preserved_by_renaming_the_row_as_culmination():
    records = _records()
    candidate = copy.deepcopy(records)
    candidate[-1]["concept_title"] = (
        "Culmination - The Making of Germany and Italy"
    )

    assert not phase38._candidate_preserves_every_origin(records, candidate)


def test_same_count_substitution_without_origin_lineage_fails_closed():
    records = _records()
    candidate = copy.deepcopy(records)
    candidate[-1]["concept_title"] = "Unrelated Substitute Concept"
    for row in candidate:
        row.pop("_phase32_origin_concept_id", None)

    assert len(candidate) == len(records)
    assert not phase38._candidate_preserves_every_origin(records, candidate)


def test_old_ledger_defaults_dispatch_idle_and_migrates_budget_lazily():
    old = phase38._fresh_convergence_state(scope="upload-job:49")
    for field in (
        "issue_buckets",
        "active_issue_key",
        "legacy_unscoped_issue",
        "dispatch_status",
        "dispatch_sequence",
        "dispatch_candidate_sha256",
        "dispatch_issue_key",
        "dispatch_decision_id",
        "dispatch_decision_context_hash",
    ):
        old.pop(field)
    old["attempts"] = 2
    old["candidate_history"] = ["a" * 64]
    old["feedback"] = {"TOPOLOGY-CONCEPT-0001": "Retry this issue."}

    normalized = phase38._normalized_convergence_state(
        old, scope="upload-job:49"
    )

    assert normalized["dispatch_status"] == "idle"
    assert normalized["dispatch_sequence"] == 0
    assert normalized["legacy_unscoped_issue"] is True
    assert normalized["issue_buckets"] == {}
    assert normalized["attempts"] == 2


def test_restart_with_unknown_dispatch_fails_closed_before_provider_call():
    records = _records()
    candidate = phase38._candidate_sha256(None, fallback=records)
    state = phase38._fresh_convergence_state(scope="upload-job:50")
    state["base_candidate_sha256"] = candidate
    state["candidate_sha256"] = candidate
    state["dispatch_status"] = "request_started"
    state["dispatch_sequence"] = 1
    state["dispatch_candidate_sha256"] = candidate
    calls = 0

    def provider(_rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _rows

    with phase38.convergence_checkpoint_context(
        scope="upload-job:50",
        state=state,
        persist=lambda *_args: pytest.fail(
            "an unknown dispatch must not be advanced automatically"
        ),
    ):
        with pytest.raises(
            phase38.Phase38ConvergenceExhausted,
            match="request_started",
        ):
            _run(provider, records)
    assert calls == 0


def test_two_resumed_workers_cannot_both_cross_predispatch_claim():
    records = _records()
    scope = "upload-job:51"
    candidate = phase38._candidate_sha256(None, fallback=records)
    issue_key = "f" * 64
    state = phase38._fresh_convergence_state(scope=scope)
    state["base_candidate_sha256"] = candidate
    state["candidate_sha256"] = candidate
    bucket = phase38._fresh_issue_bucket()
    bucket["attempts"] = 1
    bucket["feedback"] = {
        "TOPOLOGY-CONCEPT-0001": "Recheck this exact source claim."
    }
    phase38._mirror_active_issue(state, issue_key, bucket)
    durable: dict | None = copy.deepcopy(state)
    worker_a_snapshot = copy.deepcopy(state)
    worker_b_snapshot = copy.deepcopy(state)
    provider_calls: list[str] = []

    def compare_and_swap(expected, replacement):
        nonlocal durable
        if expected != durable:
            raise phase38.Phase38ConvergenceExhausted(
                "another worker advanced the Phase 3.8 claim"
            )
        durable = copy.deepcopy(replacement)

    def worker_b_provider(rows, *_args, **_kwargs):
        provider_calls.append("worker-b")
        return rows

    def worker_a_provider(rows, *_args, **_kwargs):
        provider_calls.append("worker-a")
        # Worker B loaded the same idle checkpoint before A claimed it. A's
        # request_started CAS is durable now, but its provider call has not yet
        # returned. B must lose CAS before its provider function is entered.
        with phase38.convergence_checkpoint_context(
            scope=scope,
            state=worker_b_snapshot,
            persist=compare_and_swap,
        ):
            with pytest.raises(
                phase38.Phase38ConvergenceExhausted,
                match="another worker advanced",
            ):
                _run(worker_b_provider, records)
        return rows

    with phase38.convergence_checkpoint_context(
        scope=scope,
        state=worker_a_snapshot,
        persist=compare_and_swap,
    ):
        assert _run(worker_a_provider, records) == records

    assert provider_calls == ["worker-a"]
    assert durable is None


def test_unknown_non_grounding_error_retains_fail_closed_dispatch(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE38_TOPOLOGY_CONVERGENCE_PASSES", "3")
    records = _records()
    persisted: list[dict | None] = []
    calls = 0

    def original(_rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("phase 3.2 adjudication contract is unavailable")

    with phase38.convergence_checkpoint_context(
        scope="upload-job:52",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(
            phase38.Phase38ConvergenceExhausted,
            match="unrecognized provider outcome",
        ):
            _run(original, records)

    claim = next(value for value in reversed(persisted) if value is not None)
    assert claim["dispatch_status"] == "request_started"
    assert calls == 1
    with phase38.convergence_checkpoint_context(
        scope="upload-job:52",
        state=claim,
        persist=lambda *_args: pytest.fail(
            "an unknown paid outcome must remain fail-closed"
        ),
    ):
        with pytest.raises(
            phase38.Phase38ConvergenceExhausted,
            match="request_started",
        ):
            _run(original, records)
    assert calls == 1


def test_pretransport_validation_failure_does_not_poison_resume(monkeypatch):
    records = _records()
    persisted: list[dict | None] = []
    calls = 0

    def invalid_before_transport(_rows, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("local placement contract is incomplete")

    with phase38.convergence_checkpoint_context(
        scope="upload-job:53",
        state=None,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(ValueError, match="local placement contract"):
            _run(invalid_before_transport, records, transport=False)

    latest = next(
        (value for value in reversed(persisted) if value is not None),
        None,
    )
    assert latest is not None
    assert latest["dispatch_status"] == "idle"
    assert latest["dispatch_sequence"] == 0

    with phase38.convergence_checkpoint_context(
        scope="upload-job:53",
        state=latest,
        persist=lambda _expected, value: persisted.append(
            copy.deepcopy(value)
        ),
    ):
        with pytest.raises(ValueError, match="local placement contract"):
            _run(invalid_before_transport, records, transport=False)

    assert calls == 2
