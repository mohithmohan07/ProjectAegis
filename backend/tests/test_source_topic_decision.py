from __future__ import annotations

import copy

import pytest

from app import schemas
from app.services import build_concepts
from app.services import checkpoints
from app.services import generation as g
from app.services import semantic_recovery
from app.services import source_topic_decision as gate


def _records() -> list[dict]:
    return [{
        "topic": "The French Revolution and the Idea of the Nation",
        "parent_concept": "National Identity",
        "concept_title": "Revolutionary Nationhood",
        "concept_details": "Description: Revolution reshaped national identity.",
        "keywords": "revolution, nation",
    }]


def _source_topics() -> list[dict]:
    return [
        {
            "topic": "The French Revolution and the Idea of the Nation",
            "excerpt": "The revolution transferred sovereignty to citizens.",
        },
        {
            "topic": "The Making of Nationalism in Europe",
            "excerpt": "Political domination and new middle classes shaped nationalism.",
        },
    ]


def _missing() -> list[dict]:
    return _source_topics()[1:]


def _pending() -> dict:
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            records=_records(),
            source_topics=_source_topics(),
            missing_topics=_missing(),
            meta={"subject": "History", "chapter_title": "Nationalism"},
        )
    return caught.value.pending_decision


def _resolution(pending: dict, *, choice: str = "accept_recommended") -> dict:
    return {
        **copy.deepcopy(pending),
        "status": "ready",
        "choice": choice,
        "instruction": "Preserve both numbered main topics separately.",
    }


def test_source_topic_gate_pauses_with_valid_clickable_options():
    pending = _pending()

    validated = schemas.PendingSemanticDecision.model_validate(pending)
    assert validated.kind == "source_topic_coverage_review"
    assert validated.phase == "concept_topology"
    assert validated.item.questions == [
        "The Making of Nationalism in Europe"
    ]
    assert [option.choice for option in validated.options] == [
        "accept_recommended",
        "replace_source",
        "custom_instruction",
    ]
    assert validated.options[0].recommended is True
    assert validated.options[0].target_id == "preserve-all-source-topics"


def test_source_topic_gate_consumes_only_exact_context_resolution():
    pending = _pending()
    with gate.human_resolution_context([_resolution(pending)]):
        directive = gate.resolve_or_pause(
            records=_records(),
            source_topics=_source_topics(),
            missing_topics=_missing(),
            meta={"subject": "History", "chapter_title": "Nationalism"},
        )
        changed = _records()
        changed[0]["concept_details"] += " Changed after the answer."
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
            gate.resolve_or_pause(
                records=changed,
                source_topics=_source_topics(),
                missing_topics=_missing(),
                meta={"subject": "History", "chapter_title": "Nationalism"},
            )

    assert directive["action"] == "recover"
    assert directive["decision_id"] == pending["decision_id"]
    assert caught.value.pending_decision["decision_id"] != pending["decision_id"]


def test_failed_authorized_recovery_requires_a_fresh_decision():
    pending = _pending()
    failure = "The bounded recovery still omitted the second main topic."

    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        gate.resolve_or_pause(
            records=_records(),
            source_topics=_source_topics(),
            missing_topics=_missing(),
            meta={"subject": "History", "chapter_title": "Nationalism"},
            prior_decision_id=pending["decision_id"],
            failure=failure,
        )

    follow_up = caught.value.pending_decision
    assert follow_up["decision_id"] != pending["decision_id"]
    assert failure in follow_up["diagnosis"]
    assert next(
        option for option in follow_up["options"]
        if option["choice"] == "custom_instruction"
    )["recommended"] is True


def test_consumed_exact_context_resolution_requires_a_fresh_decision():
    pending = _pending()
    consumed = {**_resolution(pending), "status": "consumed"}

    with gate.human_resolution_context([consumed]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
            gate.resolve_or_pause(
                records=_records(),
                source_topics=_source_topics(),
                missing_topics=_missing(),
                meta={"subject": "History", "chapter_title": "Nationalism"},
            )

    assert caught.value.pending_decision["decision_id"] != pending[
        "decision_id"
    ]
    assert "already used" in caught.value.pending_decision["diagnosis"]


def test_topology_checkpoint_pauses_before_paid_recovery_and_resumes_once(
    monkeypatch,
):
    calls: list[str] = []

    def recover(*args, **kwargs):
        assert kwargs["single_attempt"] is True
        assert kwargs["max_attempts"] == 1
        calls.append(str(kwargs.get("instruction") or ""))
        return [
            *args[0],
            {
                "topic": "The Making of Nationalism in Europe",
                "parent_concept": "European Nationalism",
                "concept_title": "Nationalism and Social Transformation",
                "concept_details": "Description: Social groups reshaped nationalism.",
                "keywords": "nationalism, Europe",
            },
        ]

    monkeypatch.setattr(g, "_recover_missing_topic_concepts_via_api", recover)
    emitted: list[dict] = []
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        g._recover_missing_topics_after_human_direction(
            _records(),
            meta={"subject": "History", "chapter_title": "Nationalism"},
            source_topic_excerpts=_source_topics(),
            checkpoint_callback=emitted.append,
        )

    assert calls == []
    assert [row["stage"] for row in emitted] == ["source_topic_review"]
    assert g._compatible_concept_checkpoint_entry(emitted[0])

    resumed_checkpoints: list[dict] = []
    with gate.human_resolution_context([_resolution(
        caught.value.pending_decision
    )]):
        result = g._recover_missing_topics_after_human_direction(
            _records(),
            meta={"subject": "History", "chapter_title": "Nationalism"},
            source_topic_excerpts=_source_topics(),
            checkpoint_callback=resumed_checkpoints.append,
        )

    assert calls == ["Preserve both numbered main topics separately."]
    assert not g._missing_source_topic_excerpts(result, _source_topics())
    assert [row["stage"] for row in resumed_checkpoints] == [
        "source_topic_review", "source_topic_review", "source_topic_review"
    ]
    assert resumed_checkpoints[-2]["source_topic_recovery"][
        "last_attempt"
    ]["status"] == "request_started"
    applied = resumed_checkpoints[-1]
    assert applied["source_topic_recovery"]["last_attempt"] == {
        "decision_id": caught.value.pending_decision["decision_id"],
        "context_hash": caught.value.pending_decision["context_hash"],
        "status": "succeeded",
    }
    assert not g._missing_source_topic_excerpts(
        applied["records"], _source_topics())

    # Simulate a crash after the paid response but before canonical_skeleton.
    # The immediately persisted rows must resume without replaying recovery.
    def replay_forbidden(*_args, **_kwargs):
        raise AssertionError("persisted successful recovery must not replay")

    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api", replay_forbidden)
    replayed = g._recover_missing_topics_after_human_direction(
        applied["records"],
        meta={"subject": "History", "chapter_title": "Nationalism"},
        source_topic_excerpts=_source_topics(),
        checkpoint_callback=lambda _checkpoint: None,
        recovery_state=applied["source_topic_recovery"],
    )
    assert replayed == applied["records"]


def test_source_topic_review_invalidates_stale_downstream_checkpoints():
    stale = g._make_concept_checkpoint(
        "post_type_assignment",
        records=_records(),
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    review = g._make_concept_checkpoint(
        "source_topic_review",
        records=_records(),
        source_topic_recovery={},
        skeleton_method_row_snapshot=[],
    )
    common = {
        "fingerprint": "f" * 64,
        "target_identity": {"chapter_title": "Nationalism"},
        "target_chapter_id": 1,
    }
    stored = build_concepts._merge_generation_checkpoint_history(
        {}, stale, **common)

    merged = build_concepts._merge_generation_checkpoint_history(
        stored, review, **common)

    assert merged["stage"] == "source_topic_review"
    assert [entry["stage"] for entry in merged["checkpoints"]] == [
        "source_topic_review"
    ]


def test_applied_recovery_consumes_ledger_and_cannot_rebill_if_topic_reappears(
    monkeypatch,
):
    pending = _pending()
    common = {
        "fingerprint": "f" * 64,
        "target_identity": {"chapter_title": "Nationalism"},
        "target_chapter_id": 1,
    }
    review = g._make_concept_checkpoint(
        "source_topic_review",
        records=_records(),
        source_topic_recovery={},
        skeleton_method_row_snapshot=[],
    )
    stored = build_concepts._merge_generation_checkpoint_history(
        {}, review, **common)
    stored[build_concepts._HUMAN_DECISIONS_KEY] = {
        "version": 1,
        "context": {
            "fingerprint": common["fingerprint"],
            "target_chapter_id": 1,
        },
        "pending": None,
        "deferred_assignment_unit_ids": [],
        "resolutions": [{
            "decision_id": pending["decision_id"],
            "context_hash": pending["context_hash"],
            "choice": "accept_recommended",
            "instruction": "Preserve both numbered main topics separately.",
            "target_id": "preserve-all-source-topics",
            "target_concept_id": "",
            "resolved_at": "2026-08-01T00:00:00+00:00",
            "status": "ready",
            "pending_decision": copy.deepcopy(pending),
        }],
    }
    recovered_records = [
        *_records(),
        {
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "European Nationalism",
            "concept_title": "Nationalism and Social Transformation",
            "concept_details": "Description: Social groups transformed Europe.",
            "keywords": "nationalism, Europe",
        },
    ]
    applied = g._make_concept_checkpoint(
        "source_topic_review",
        records=recovered_records,
        source_topic_recovery={
            "last_attempt": {
                "decision_id": pending["decision_id"],
                "context_hash": pending["context_hash"],
                "status": "request_started",
            },
        },
        skeleton_method_row_snapshot=[],
    )
    stored = build_concepts._merge_generation_checkpoint_history(
        stored, applied, **common)
    assert stored[build_concepts._HUMAN_DECISIONS_KEY]["resolutions"][0][
        "status"
    ] == "consumed"
    consumed = stored[build_concepts._HUMAN_DECISIONS_KEY]["resolutions"][0]
    assert consumed["consumed_at"] == applied["saved_at"]
    checkpoints._validate_human_decisions(
        stored[build_concepts._HUMAN_DECISIONS_KEY],
        checkpoint=stored,
        path="checkpoint.human_decisions",
    )

    # A hypothetical later-stage mutation loses the topic again. The exact old
    # ready answer must be unavailable even though its context recurs.
    calls: list[int] = []

    def forbidden(*_args, **_kwargs):
        calls.append(1)
        return recovered_records

    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api", forbidden)
    with build_concepts._human_decision_resolution_context(stored):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
            g._recover_missing_topics_after_human_direction(
                _records(),
                meta={"subject": "History", "chapter_title": "Nationalism"},
                source_topic_excerpts=_source_topics(),
                checkpoint_callback=lambda _checkpoint: None,
            )

    assert calls == []
    assert caught.value.pending_decision["decision_id"] != pending[
        "decision_id"
    ]


def test_unsuccessful_authorized_recovery_makes_one_call_then_pauses_again(
    monkeypatch,
):
    pending = _pending()
    calls: list[int] = []

    def unchanged(records, **_kwargs):
        assert _kwargs["single_attempt"] is True
        calls.append(1)
        return copy.deepcopy(records)

    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api", unchanged)
    emitted: list[dict] = []
    with gate.human_resolution_context([_resolution(pending)]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
            g._recover_missing_topics_after_human_direction(
                _records(),
                meta={"subject": "History", "chapter_title": "Nationalism"},
                source_topic_excerpts=_source_topics(),
                checkpoint_callback=emitted.append,
            )

    assert calls == [1]
    assert [row["stage"] for row in emitted] == [
        "source_topic_review", "source_topic_review", "source_topic_review"
    ]
    assert emitted[-2]["source_topic_recovery"]["last_attempt"][
        "status"
    ] == "request_started"
    assert caught.value.pending_decision["decision_id"] != pending["decision_id"]
    assert emitted[-1]["source_topic_recovery"]["pending_followup"][
        "prior_decision_id"
    ] == pending["decision_id"]
    assert emitted[-1]["source_topic_recovery"]["last_attempt"][
        "status"
    ] == "incomplete"


def test_failed_provider_request_is_checkpointed_then_repaused(monkeypatch):
    pending = _pending()

    def fail_once(_records, **kwargs):
        assert kwargs["single_attempt"] is True
        raise RuntimeError("malformed bounded response")

    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api", fail_once)
    emitted: list[dict] = []
    with gate.human_resolution_context([_resolution(pending)]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
            g._recover_missing_topics_after_human_direction(
                _records(),
                meta={"subject": "History", "chapter_title": "Nationalism"},
                source_topic_excerpts=_source_topics(),
                checkpoint_callback=emitted.append,
            )

    assert [row["stage"] for row in emitted] == [
        "source_topic_review", "source_topic_review", "source_topic_review"
    ]
    assert emitted[-2]["source_topic_recovery"]["last_attempt"][
        "status"
    ] == "request_started"
    assert emitted[-1]["source_topic_recovery"]["last_attempt"][
        "status"
    ] == "failed"
    assert caught.value.pending_decision["decision_id"] != pending["decision_id"]
    assert "malformed bounded response" in caught.value.pending_decision[
        "diagnosis"
    ]


def test_source_topic_checkpoint_requires_complete_resume_shape():
    valid = g._make_concept_checkpoint(
        "source_topic_review",
        records=_records(),
        source_topic_recovery={},
        skeleton_method_row_snapshot=[],
    )
    assert g._compatible_concept_checkpoint_entry(valid)

    no_snapshot = copy.deepcopy(valid)
    no_snapshot.pop("skeleton_method_row_snapshot")
    assert not g._compatible_concept_checkpoint_entry(no_snapshot)

    malformed_state = copy.deepcopy(valid)
    malformed_state["source_topic_recovery"] = {
        "last_attempt": {"status": "succeeded"}
    }
    assert not g._compatible_concept_checkpoint_entry(malformed_state)

    request_started = copy.deepcopy(valid)
    request_started["source_topic_recovery"] = {
        "last_attempt": {
            "decision_id": "source-topology-example",
            "context_hash": "a" * 64,
            "status": "request_started",
        },
        "pending_followup": {
            "prior_decision_id": "source-topology-example",
            "failure": "The dispatched request has no saved result.",
        },
    }
    assert g._compatible_concept_checkpoint_entry(request_started)


def test_late_lossy_candidate_restores_latest_complete_topology_without_api(
    monkeypatch,
):
    baseline = [
        *_records(),
        {
            "topic": "The Making of Nationalism in Europe",
            "parent_concept": "European Nationalism",
            "concept_title": "Nationalism and Social Transformation",
            "concept_details": "Description: Social groups transformed Europe.",
            "keywords": "nationalism, Europe",
        },
    ]
    candidate = copy.deepcopy(baseline[:1])
    monkeypatch.setattr(
        g,
        "_recover_missing_topic_concepts_via_api",
        lambda *_args, **_kwargs: pytest.fail(
            "late deterministic rollback must not spend an API request"
        ),
    )

    accepted = g._accept_source_topic_complete_candidate(
        baseline,
        candidate,
        _source_topics(),
        stage="test cleanup",
    )

    assert accepted == baseline
    assert accepted is not baseline


def test_source_request_started_checkpoint_precedes_dispatch_and_blocks_replay(
    monkeypatch,
):
    pending = _pending()
    captured: list[dict] = []
    calls: list[int] = []

    def stop_after_durable_start(checkpoint):
        captured.append(copy.deepcopy(checkpoint))
        state = checkpoint["source_topic_recovery"]
        if (state.get("last_attempt") or {}).get("status") == (
            "request_started"
        ):
            raise RuntimeError("simulated worker stop after durable start")

    def forbidden_provider(*_args, **_kwargs):
        calls.append(1)
        return copy.deepcopy(_records())

    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api", forbidden_provider)
    with gate.human_resolution_context([_resolution(pending)]):
        with pytest.raises(RuntimeError, match="durable start"):
            g._recover_missing_topics_after_human_direction(
                _records(),
                meta={"subject": "History", "chapter_title": "Nationalism"},
                source_topic_excerpts=_source_topics(),
                checkpoint_callback=stop_after_durable_start,
            )

    assert calls == []
    started = captured[-1]["source_topic_recovery"]
    assert started["last_attempt"]["status"] == "request_started"

    # Even if only the old ready answer is supplied, the durable unknown-outcome
    # marker chains to a fresh decision before any second provider request.
    with gate.human_resolution_context([_resolution(pending)]):
        with pytest.raises(semantic_recovery.HumanDecisionRequired) as fresh:
            g._recover_missing_topics_after_human_direction(
                _records(),
                meta={"subject": "History", "chapter_title": "Nationalism"},
                source_topic_excerpts=_source_topics(),
                checkpoint_callback=lambda _checkpoint: None,
                recovery_state=started,
            )
    assert calls == []
    assert fresh.value.pending_decision["decision_id"] != pending[
        "decision_id"
    ]


def test_stale_post_type_checkpoint_rewinds_to_human_topic_gate_before_api(
    monkeypatch,
):
    source = (
        "## 1 First Main Topic\nSource evidence for the first topic.\n\n"
        "## 2 Second Main Topic\nSource evidence for the second topic.\n"
    )
    record = {
        "topic": "First Main Topic",
        "parent_concept": "First Main Topic",
        "concept_title": "First Topic Concept",
        "concept_details": "Description: Source-grounded content.",
        "keywords": "first",
    }
    stale = g._make_concept_checkpoint(
        "post_type_assignment",
        records=[record],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("no downstream semantic call may precede the gate")

    monkeypatch.setattr(g, "_reconcile_resumed_mined_types", forbidden)
    emitted: list[dict] = []
    with pytest.raises(semantic_recovery.HumanDecisionRequired) as caught:
        g.concepts_from_mmd(
            source,
            subject="History",
            live=True,
            resume_checkpoint=stale,
            checkpoint_callback=emitted.append,
        )

    assert caught.value.pending_decision["kind"] == (
        "source_topic_coverage_review"
    )
    assert caught.value.pending_decision["item"]["questions"] == [
        "Second Main Topic"
    ]
    assert [row["stage"] for row in emitted] == ["source_topic_review"]
