"""No semantic pause survives when a quality-safe action exists.

Unattended completion started as a fallback inside the resolution agent, so
branches that declined *before* the agent ran — a disabled resolver, a
crash-recovery guard, an already-inspected workspace on resume, another
worker's claim — still paused the run. In production that surfaced as a
manual review appearing after 90%, during Phase 3.3 Type-host assignment,
where the offered options can carry no ``recommended`` flag at all.

These tests pin the invariant at the two choke points: whatever declined, the
run continues. It prefers an explicit no-change route, a verifiably certified
recommendation, or a source-preserving new concept; failing those it takes the
least-destructive offered action and flags it; failing even that -- a genuinely
user-only decision -- it carries the decision, leaving the uploaded document
untouched. Candidate and option order never authorize an existing host, a stale
binding seal is never selected, and no setting restores a pause.
"""
from __future__ import annotations

import copy

import pytest

from app import models
from app.services import autonomous_resolution, build_concepts
from app.services import semantic_recovery


def _phase33_pending(*, concepts: bool = True, target_id: str = "") -> dict:
    """Reproduce the Phase 3.3 post-90% Type-host conflict packet.

    With existing concepts but no pre-selected target, the contract marks
    *no* option as recommended — the exact shape that kept pausing.
    """

    options: list[dict] = []
    if concepts:
        expand: dict = {
            "choice": "expand_existing",
            "label": "Expand the existing concept",
            "recommended": bool(target_id),
        }
        if target_id:
            expand["target_concept_id"] = target_id
        options.append(expand)
    options.append({
        "choice": "create_new",
        "label": "Create a separate source-grounded concept",
        "recommended": not concepts,
    })
    if concepts:
        options.append({
            "choice": "select_existing",
            "label": "Select another existing concept",
            "recommended": False,
        })
    options.append({
        "choice": "custom_instruction",
        "label": "Give a custom instruction",
        "recommended": False,
    })
    return {
        "decision_id": "phase33-host-postninety-0001",
        "kind": "phase33_type_host_semantic_conflict",
        "phase": "3.3",
        "conflict": "TYPE-0003 has no certified host concept.",
        "diagnosis": "Two hosts remain plausible for the mined Type.",
        "decision_question": "Where should TYPE-0003 be hosted?",
        "item": {
            "unit_id": "UNIT-0003",
            "type_id": "TYPE-0003",
            "type_title": "Explain a nationalist symbol",
            "qids": ["QINV-0011"],
            "questions": ["Explain the symbolism of Germania."],
            "topic": "Visualising the Nation",
        },
        "candidates": [
            {
                "concept_id": "CONCEPT-0021",
                "title": "Allegories of the nation",
                "topic": "Visualising the Nation",
                "coverage": "",
                "gap": "",
                "action": "",
                "source_block_ids": ["BLK-00201"],
            },
            {
                "concept_id": "CONCEPT-0022",
                "title": "Germania and her attributes",
                "topic": "Visualising the Nation",
                "coverage": "",
                "gap": "",
                "action": "",
                "source_block_ids": ["BLK-00204"],
            },
        ] if concepts else [],
        "evidence": [{
            "evidence_id": "PENDING-EVIDENCE-001",
            "page": "21",
            "label": "BLK-00201",
            "text": "Germania wears a crown of oak leaves.",
        }],
        "options": options,
    }


def _material_equivalence_pending() -> dict:
    """Populate every semantic identity used by the durable loop key."""

    pending = _phase33_pending(target_id="CONCEPT-0022")
    for index, candidate in enumerate(pending["candidates"], start=1):
        candidate.update({
            "target_id": f"HOST-TARGET-{index:04d}",
            "coverage": f"Verified coverage pathway {index}.",
            "gap": f"Remaining source gap {index}.",
            "source_block_ids": [
                f"BLK-{index:05d}",
                f"BLK-{index + 10:05d}",
            ],
            "source_topic_id": "TOPIC-0004",
            "target_topic_id": "TOPIC-0004",
            "boundary_relation": "same_topic",
            "source_kind": "type_host_candidate",
            "source_page": str(20 + index),
            "text_sha256": str(index) * 64,
            "binding_hash": chr(96 + index) * 64,
        })
    pending["evidence"][0]["evidence_id"] = "EVIDENCE-0001"
    pending["evidence"].append({
        "evidence_id": "EVIDENCE-0002",
        "page": "22",
        "label": "BLK-00012",
        "text": "Germania's sword represents readiness to fight.",
    })
    pending["options"][0].update({
        "target_id": "OPTION-TARGET-0001",
        "target_concept_id": "CONCEPT-0022",
    })
    pending["options"][2].update({
        "target_id": "OPTION-TARGET-0002",
        "target_concept_id": "CONCEPT-0021",
    })
    return pending


def _seed_paused_job(db, chapter, monkeypatch, *, filename: str, raw: dict):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename=filename,
        mmd_text="## Visualising the Nation\nGermania wears oak leaves.",
        status="converted",
    )
    db.add(job)
    db.flush()
    stage = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "Visualising the Nation",
            "parent_concept": "Parent",
            "concept_title": "Allegories of the nation",
            "concept_details": "Description: Accepted.",
            "keywords": "allegory",
        }],
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    fingerprint = build_concepts._generation_checkpoint_fingerprint(
        job, chapter)
    job.generation_checkpoint = (
        build_concepts._merge_generation_checkpoint_history(
            {},
            stage,
            fingerprint=fingerprint,
            target_identity=build_concepts._generation_target_identity(
                chapter),
            target_chapter_id=chapter.id,
        )
    )
    db.commit()
    db.refresh(job)
    monkeypatch.setattr(
        build_concepts.drive_checkpoints,
        "schedule_checkpoint_backup",
        lambda *_args, **_kwargs: None,
    )
    pending = build_concepts._persist_pending_human_decision(
        db,
        job,
        raw,
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=None,
    )
    return job, pending


# --------------------------------------------------------------------------- #
# Selector: the post-90% shape with no recommended option
# --------------------------------------------------------------------------- #

def test_phase33_host_conflict_without_certified_host_creates_new():
    selected = autonomous_resolution.safe_continuation_option(
        _phase33_pending())
    # Neither candidate was certified. Candidate order must not turn the
    # general "Allegories" row into a host for the Germania-specific Type.
    assert selected == {
        "choice": "create_new",
        "target_id": "",
        "target_concept_id": "",
    }


def test_phase33_without_concepts_falls_back_to_create_new():
    selected = autonomous_resolution.safe_continuation_option(
        _phase33_pending(concepts=False))
    assert selected == {
        "choice": "create_new",
        "target_id": "",
        "target_concept_id": "",
    }


def test_uncertified_recommended_host_does_not_override_create_new():
    selected = autonomous_resolution.safe_continuation_option(
        _phase33_pending(target_id="CONCEPT-0022"))
    assert selected == {
        "choice": "create_new",
        "target_id": "",
        "target_concept_id": "",
    }


def test_only_user_authority_actions_carry_the_decision():
    """Neither user-only route is taken, and the run still ships.

    Replacing the uploaded document and writing an instruction remain outside
    automation. What no longer follows is a stopped run: the decision carries,
    the source stays exactly as uploaded, and the reviewer sees the flag in the
    delivered output.
    """

    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "replace_source", "recommended": True},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [{"concept_id": "CONCEPT-0021"}],
    })
    assert selected == {
        "choice": "carry_forward",
        "target_id": "",
        "target_concept_id": "",
    }
    assert selected["choice"] not in autonomous_resolution.USER_ONLY_CHOICES


# --------------------------------------------------------------------------- #
# Choke point: every resolver-declined branch still continues
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "decline_reason",
    [
        "resolver_disabled",
        "already_inspected",
        "worker_claim_conflict",
    ],
)
def test_resolver_declined_branches_continue_without_manual_review(
    db,
    first_chapter,
    monkeypatch,
    decline_reason,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = f"phase33-host-{decline_reason[:16]}"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename=f"{decline_reason}.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    # Every one of these declines returns None from the resolver entry point.
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    continued = build_concepts._apply_last_resort_safe_continuation(
        db, job, pending, owner_sub=None)

    assert continued == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"] is None
    recorded = ledger["resolutions"][-1]
    assert recorded["choice"] == "create_new"
    assert recorded["target_concept_id"] == ""
    assert recorded["resolved_by"] == "agent"
    assert recorded["status"] == "consumed"


def test_resume_path_continues_instead_of_returning_a_pause(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = "phase33-host-resume-0001"
    job, _pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="resume-continues.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    checkpoint = dict(job.generation_checkpoint or {})
    agent_ids: set[str] = set()
    outcome = build_concepts._existing_human_decision_pause(
        db,
        job,
        checkpoint,
        agent_resolution_ids=agent_ids,
        owner_sub=None,
    )

    # None means "no pause — keep generating".
    assert outcome is None
    assert agent_ids == {"phase33-host-resume-0001"}
    db.refresh(job)
    assert job.generation_checkpoint["human_decisions"]["pending"] is None
    # The refreshed checkpoint was handed back to the caller in place.
    assert checkpoint["human_decisions"]["pending"] is None


def test_user_only_decision_carries_forward_instead_of_parking_it(
    db,
    first_chapter,
    monkeypatch,
):
    """Unattended runs never park in ``awaiting_decision``, and now never stop.

    A decision whose only routes are replacing the uploaded document or
    writing an instruction cannot be synthesized, and this test used to pin
    the run ending there. It no longer does: the decision is carried, nothing
    is applied, and generation ships a map the reviewer can read and correct.
    A stalled job remains strictly worse than either outcome.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending(concepts=False)
    raw["decision_id"] = "phase33-host-useronly-unattended"
    raw["options"] = [
        {
            "choice": "replace_source",
            "label": "Replace the uploaded source",
            "recommended": True,
        },
        {
            "choice": "custom_instruction",
            "label": "Give a custom instruction",
            "recommended": False,
        },
    ]
    job, _pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="user-only-unattended.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    outcome = build_concepts._existing_human_decision_pause(
        db,
        job,
        dict(job.generation_checkpoint or {}),
        agent_resolution_ids=set(),
        owner_sub=None,
    )

    # None means "no pause -- keep generating".
    assert outcome is None
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"] is None
    recorded = next(
        row for row in ledger["resolutions"]
        if row["decision_id"] == "phase33-host-useronly-unattended"
    )
    # Neither user-only route was taken; the decision was simply settled.
    assert recorded["choice"] == "carry_forward"
    assert recorded["target_id"] == ""
    assert recorded["instruction"] == ""


def test_no_setting_can_restore_a_mid_run_pause(
    db,
    first_chapter,
    monkeypatch,
):
    """Generation has no pause in any configuration.

    ``AEGIS_UNATTENDED_COMPLETION=0`` used to hand a user-only decision back to
    a person mid-run. It now only chooses between two unattended outcomes: with
    continuation on the decision is carried and the run ships, with it off the
    run ends with the reason recorded. Neither waits for an answer, and no
    setting brings the waiting back. A stalled job holds its worker with nobody
    watching, so it is strictly worse than either.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending(concepts=False)
    raw["decision_id"] = "phase33-host-useronly-0001"
    raw["options"] = [
        {
            "choice": "replace_source",
            "label": "Replace the uploaded source",
            "recommended": True,
        },
        {
            "choice": "custom_instruction",
            "label": "Give a custom instruction",
            "recommended": False,
        },
    ]
    job, _pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="user-only-pauses.mmd",
        raw=raw,
    )
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: None,
    )

    for setting in ("0", "false", "off", "no"):
        monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", setting)
        with pytest.raises(build_concepts.UnattendedDecisionUnavailable) as caught:
            build_concepts._existing_human_decision_pause(
                db,
                job,
                dict(job.generation_checkpoint or {}),
                agent_resolution_ids=set(),
                owner_sub=None,
            )
        # The run ends. It is never handed back to a person to answer, so
        # bounded semantic recovery must not retry it either.
        assert semantic_recovery.classify_failure(
            caught.value).recoverable is False
        db.refresh(job)
        assert job.status != "awaiting_decision"

    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "1")
    assert build_concepts._existing_human_decision_pause(
        db,
        job,
        dict(job.generation_checkpoint or {}),
        agent_resolution_ids=set(),
        owner_sub=None,
    ) is None
    db.refresh(job)
    assert job.status != "awaiting_decision"
    assert job.generation_checkpoint["human_decisions"]["pending"] is None


def test_opt_out_disables_automatic_continuation_but_restores_no_pause(
    db,
    first_chapter,
    monkeypatch,
):
    """The setting still means something, just not what its name suggested.

    With continuation off, the safest-offered-action route declines and the
    pending decision stays on the checkpoint. What no longer follows is a
    pause: the caller ends the run instead of waiting for an answer.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = "phase33-host-optout-0001"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="opt-out-pauses.mmd",
        raw=raw,
    )
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")

    assert build_concepts._apply_last_resort_safe_continuation(
        db, job, pending, owner_sub=None) is None
    db.refresh(job)
    assert job.generation_checkpoint["human_decisions"]["pending"] is not None


# --------------------------------------------------------------------------- #
# The decision/resolution loop itself is bounded
# --------------------------------------------------------------------------- #

def test_resolution_cycles_are_bounded_and_name_the_returning_decision(
    db,
    first_chapter,
    monkeypatch,
):
    """An always-resolvable decision that keeps returning must terminate.

    Every mechanism under this loop is individually bounded, but their
    composition was not: an action that resolves one decision can regenerate
    an equivalent decision under a fresh context hash, which the per-issue
    caps never match.

    Termination is the invariant, not the stop. A spent budget now carries the
    scope forward and continues, because a ceiling firing is a reason to stop
    paying for one concept, never a reason to discard the other forty. The run
    ends here only because this operation is rigged to re-demand a decision it
    has already been given an answer to.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    raw["decision_id"] = "phase33-host-cyclebound-0001"
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="cycle-bound.mmd", raw=raw)
    monkeypatch.setenv("AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS", "3")
    logs: list[str] = []
    monkeypatch.setattr(
        build_concepts.progress, "log",
        lambda message, **_kw: logs.append(str(message)),
    )
    # The agent always succeeds, and the operation always needs another
    # decision — the exact shape seen in production.
    resolution_calls = 0

    def resolve(*_args, **_kwargs):
        nonlocal resolution_calls
        resolution_calls += 1
        return "resolved-decision"

    monkeypatch.setattr(
        build_concepts, "_autonomously_resolve_pending_decision", resolve)
    monkeypatch.setattr(
        build_concepts,
        "_persist_pending_human_decision",
        lambda *_a, **_kw: pending,
    )

    operation_calls = 0

    def never_converges():
        nonlocal operation_calls
        operation_calls += 1
        raise semantic_recovery.HumanDecisionRequired({
            "decision_id": pending["decision_id"],
            "context_hash": pending["context_hash"],
        })

    with pytest.raises(
        build_concepts.SemanticResolutionCyclesExhausted,
    ) as excinfo:
        build_concepts._run_with_human_decision_pause(
            never_converges,
            db=db,
            job=job,
            fingerprint=str(job.generation_checkpoint.get("fingerprint") or ""),
            target_chapter_id=chapter.id,
            owner_sub=None,
        )

    # The budget still bounds the paid repairs: three, then no more.
    assert resolution_calls == 3
    # Spending the budget no longer ends the run on its own. The scope is
    # carried -- nothing applied, flagged for the reviewer -- and generation
    # continues, which is what lets the other concepts reach the output.
    assert sum(
        "Placed by best judgement" in row
        and "were spent without converging" in row
        for row in logs
    ) == 1

    # A scope is carried once. This operation is rigged to demand the identical
    # decision forever, even after it has been settled, so the scope returns
    # having already been carried -- the phase is ignoring the answer, and
    # continuing would spin. The ceiling ends the run then, naming the
    # decision so the loop is diagnosable.
    message = str(excinfo.value)
    assert "4 verified autonomous repair attempt(s)" in message
    assert "phase33_type_host_semantic_conflict" in message
    assert "TYPE-0003" in message
    assert "AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS" in message
    assert sum("Generation stopped after" in row for row in logs) == 1
    assert operation_calls == 5
    # Bounded semantic recovery must not retry a non-converging loop.
    assert semantic_recovery.classify_failure(
        excinfo.value).recoverable is False


def test_last_allowed_resolution_receives_a_successful_verification_turn(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    pending = _phase33_pending()
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="last-turn.mmd", raw=pending)
    monkeypatch.setenv("AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS", "1")
    monkeypatch.setattr(
        build_concepts,
        "_persist_pending_human_decision",
        lambda *_a, **_kw: pending,
    )
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_a, **_kw: "resolved-decision",
    )
    calls = 0

    def succeeds_after_repair():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise semantic_recovery.HumanDecisionRequired(pending)
        return "finished"

    paused, result = build_concepts._run_with_human_decision_pause(
        succeeds_after_repair,
        db=db,
        job=job,
        fingerprint=str(job.generation_checkpoint.get("fingerprint") or ""),
        target_chapter_id=chapter.id,
        owner_sub=None,
    )

    assert paused is None
    assert result == "finished"
    assert calls == 2


def test_distinct_material_decisions_do_not_share_the_loop_budget(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    job, first = _seed_paused_job(
        db, chapter, monkeypatch, filename="distinct.mmd", raw=raw)
    second = copy.deepcopy(first)
    second["decision_id"] = "phase33-host-distinct-0002"
    second["context_hash"] = "b" * 64
    second["item"]["type_id"] = "TYPE-0099"
    monkeypatch.setenv("AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS", "1")
    monkeypatch.setattr(
        build_concepts,
        "_persist_pending_human_decision",
        lambda _db, _job, raw, **_kw: raw,
    )
    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_a, **_kw: "resolved-decision",
    )
    calls = 0

    def two_distinct_decisions():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise semantic_recovery.HumanDecisionRequired(first)
        if calls == 2:
            raise semantic_recovery.HumanDecisionRequired(second)
        return "finished"

    paused, result = build_concepts._run_with_human_decision_pause(
        two_distinct_decisions,
        db=db,
        job=job,
        fingerprint=str(job.generation_checkpoint.get("fingerprint") or ""),
        target_chapter_id=chapter.id,
        owner_sub=None,
    )

    assert paused is None
    assert result == "finished"
    assert calls == 3


def test_recommendation_flip_cannot_reset_durable_budget_after_restart(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _phase33_pending()
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="recommendation-flip.mmd",
        raw=raw,
    )
    monkeypatch.setenv("AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS", "1")
    assert build_concepts._apply_last_resort_safe_continuation(
        db, job, pending, owner_sub=None
    ) == pending["decision_id"]
    db.refresh(job)
    stored_resolution = job.generation_checkpoint["human_decisions"][
        "resolutions"
    ][0]
    assert stored_resolution["equivalence_key"]

    followup = copy.deepcopy(pending)
    followup["decision_id"] = "phase33-host-recommendation-flip-0002"
    followup["context_hash"] = "b" * 64
    for option in followup["options"]:
        option["recommended"] = not bool(option.get("recommended"))
    assert stored_resolution["equivalence_key"] == (
        build_concepts._decision_equivalence_key(followup)
    )

    monkeypatch.setattr(
        build_concepts,
        "_autonomously_resolve_pending_decision",
        lambda *_args, **_kwargs: pytest.fail(
            "a presentation-only recommendation flip must not get a new turn"
        ),
    )

    def recurring_after_restart():
        raise semantic_recovery.HumanDecisionRequired(followup)

    # The durable budget is spent, so no paid turn is granted -- the monkeypatch
    # above fails the test if one is. The scope is carried forward once, and
    # the run terminates when the same decision returns anyway.
    with pytest.raises(RuntimeError) as excinfo:
        build_concepts._run_with_human_decision_pause(
            recurring_after_restart,
            db=db,
            job=job,
            fingerprint=str(job.generation_checkpoint.get("fingerprint") or ""),
            target_chapter_id=chapter.id,
            owner_sub=None,
        )

    assert semantic_recovery.classify_failure(
        excinfo.value).recoverable is False
    db.refresh(job)
    # Exactly one further resolution: the carry. A flipped recommendation
    # bought no new repair attempt.
    resolutions = job.generation_checkpoint["human_decisions"]["resolutions"]
    assert [row["choice"] for row in resolutions[1:]] == ["carry_forward"]


def test_legacy_resolution_without_material_seal_gets_upgrade_budget():
    pending = _phase33_pending()
    legacy_snapshot = copy.deepcopy(pending)
    legacy_snapshot["evidence"] = []
    legacy_snapshot["candidates"] = []
    checkpoint = {
        "human_decisions": {
            "version": 1,
            "resolutions": [{
                "resolved_by": "agent",
                "status": "consumed",
                "pending_decision": legacy_snapshot,
            }],
        },
    }
    changed_candidate = copy.deepcopy(pending)
    changed_candidate["decision_id"] = "phase33-host-postupgrade-0002"
    changed_candidate["context_hash"] = "c" * 64
    changed_candidate["evidence"][0]["text"] = (
        "Germania carries a sword and wears a crown of oak leaves."
    )

    assert build_concepts._equivalent_agent_resolution_count(
        checkpoint, changed_candidate
    ) == 0


def test_equivalence_ignores_order_and_transport_or_ui_volatility():
    original = _material_equivalence_pending()
    reordered = copy.deepcopy(original)
    reordered["decision_id"] = "phase33-host-transport-replay-0002"
    reordered["context_hash"] = "d" * 64
    reordered["checkpoint_progress"] = 0.99
    reordered["cumulative_usage"] = {"input_tokens": 999_999}
    reordered["agent_review"] = {"volatile": "transport audit state"}
    reordered["conflict"] = "  " + "   ".join(
        original["conflict"].upper().split()
    )
    reordered["diagnosis"] = "  " + "   ".join(
        original["diagnosis"].upper().split()
    )
    reordered["decision_question"] = "  " + "   ".join(
        original["decision_question"].upper().split()
    )
    reordered["candidates"].reverse()
    for candidate in reordered["candidates"]:
        candidate["source_block_ids"].reverse()
    reordered["evidence"].reverse()
    reordered["options"].reverse()
    for option in reordered["options"]:
        option["label"] = "Changed presentation label"
        option["recommended"] = not bool(option.get("recommended"))

    assert build_concepts._decision_equivalence_key(original) == (
        build_concepts._decision_equivalence_key(reordered)
    )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("conflict",), "The critic found a different semantic conflict."),
        (("diagnosis",), "A different repair pathway is now supported."),
        (("decision_question",), "Should the Type use a new host?"),
        (("candidates", 0, "target_id"), "HOST-TARGET-CHANGED"),
        (("candidates", 0, "concept_id"), "CONCEPT-CHANGED"),
        (("candidates", 0, "binding_hash"), "f" * 64),
        (("candidates", 0, "coverage"), "Different verified coverage."),
        (("candidates", 0, "gap"), "Different remaining source gap."),
        (("candidates", 0, "boundary_relation"), "cross_topic"),
        (("candidates", 0, "source_kind"), "topology_repair"),
        (("candidates", 0, "source_page"), "99"),
        (("evidence", 0, "evidence_id"), "EVIDENCE-CHANGED"),
        (("options", 0, "target_id"), "OPTION-TARGET-CHANGED"),
        (("options", 0, "target_concept_id"), "CONCEPT-CHANGED"),
    ],
    ids=[
        "conflict",
        "diagnosis",
        "decision-question",
        "candidate-target",
        "candidate-concept",
        "candidate-binding",
        "candidate-coverage",
        "candidate-gap",
        "candidate-boundary",
        "candidate-source-kind",
        "candidate-source-page",
        "evidence-identity",
        "option-target",
        "option-concept",
    ],
)
def test_changed_critic_or_candidate_pathway_gets_fresh_budget(
    path,
    replacement,
):
    original = _material_equivalence_pending()
    changed = copy.deepcopy(original)
    cursor = changed
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = replacement
    checkpoint = {
        "human_decisions": {
            "version": 1,
            "resolutions": [{
                "resolved_by": "agent",
                "status": "consumed",
                "equivalence_key": (
                    build_concepts._decision_equivalence_key(original)
                ),
            }],
        },
    }

    assert build_concepts._decision_equivalence_key(changed) != (
        build_concepts._decision_equivalence_key(original)
    )
    assert build_concepts._equivalent_agent_resolution_count(
        checkpoint, changed
    ) == 0


def _regenerated_identity_pending(index: int) -> dict:
    """One semantic scope, re-raised with freshly minted candidate identities."""

    return {
        "decision_id": f"phase33-host-{index:04d}",
        "context_hash": f"{index:064d}",
        "kind": "phase33_type_host_semantic_conflict",
        "phase": "3.3",
        "conflict": "Two Type hosts remain plausible.",
        "diagnosis": "The source supports one bounded choice.",
        "decision_question": "How should TYPE-0001 be hosted?",
        "item": {
            "unit_id": "UNIT-1",
            "type_id": "TYPE-0001",
            "qids": ["QID-1"],
            "topic": "Nationalism",
        },
        "candidates": [{
            "target_id": f"TARGET-REGENERATED-{index}",
            "concept_id": f"HOST-REGENERATED-{index}",
            "action": "keep",
            "title": "host",
            "topic": "Nationalism",
            "coverage": "identical coverage",
            "gap": "identical gap",
        }],
        "evidence": [{"evidence_id": "E1", "text": "identical evidence"}],
        "options": [{
            "choice": "select_candidate",
            "target_id": f"TARGET-REGENERATED-{index}",
            "target_concept_id": f"HOST-REGENERATED-{index}",
        }],
    }


def test_regenerated_identities_do_not_reset_the_equivalence_budget():
    """The 81% spin: a new concept_id must not buy an unlimited budget.

    The equivalence key hashes candidate identities, so a stage that
    regenerates them mints a fresh key every pass and charges nothing. Only the
    issue key stays fixed across those passes, so only it can retire the scope.
    """

    ledger: dict = {"human_decisions": {"version": 1, "resolutions": []}}
    scope_keys = set()
    equivalence_keys = set()

    for index in range(1, 8):
        pending = _regenerated_identity_pending(index)
        scope_key = build_concepts.autonomous_resolution.issue_key(pending)
        equivalence_key = build_concepts._decision_equivalence_key(pending)
        scope_keys.add(scope_key)
        equivalence_keys.add(equivalence_key)

        # The volatile budget is defeated by the regenerated identity ...
        assert build_concepts._equivalent_agent_resolution_count(
            ledger, pending
        ) == 0
        # ... while the stable one keeps counting every prior repair.
        assert build_concepts._issue_agent_resolution_count(
            ledger, scope_key
        ) == index - 1

        ledger["human_decisions"]["resolutions"].append({
            "resolved_by": "agent",
            "status": "consumed",
            "equivalence_key": equivalence_key,
            "issue_key": scope_key,
        })

    assert len(scope_keys) == 1, "the scope never actually changed"
    assert len(equivalence_keys) == 7, "every pass minted a new equivalence key"


def test_issue_pathway_ceiling_stops_the_scope():
    pending = _regenerated_identity_pending(1)

    build_concepts._raise_if_issue_pathways_exhausted(
        pending, attempts=23, maximum=24
    )
    with pytest.raises(build_concepts.SemanticResolutionCyclesExhausted) as err:
        build_concepts._raise_if_issue_pathways_exhausted(
            pending, attempts=24, maximum=24
        )
    assert "same semantic scope" in str(err.value)
    assert "TYPE-0001" in str(err.value)


def test_issue_count_reads_legacy_rows_without_a_stored_issue_key():
    """Rows written before the scope seal still charge their scope."""

    pending = _regenerated_identity_pending(1)
    scope_key = build_concepts.autonomous_resolution.issue_key(pending)
    ledger = {
        "human_decisions": {
            "version": 1,
            "resolutions": [{
                "resolved_by": "agent",
                "status": "consumed",
                "pending_decision": _regenerated_identity_pending(2),
            }],
        },
    }

    assert build_concepts._issue_agent_resolution_count(ledger, scope_key) == 1
