"""Unattended completion uses only quality-preserving fallback actions.

The audited production runs repeatedly reached the required output only to
pause for a manual review click whenever the bounded resolution agent
escalated (for example: "This source-critical action lacks issue-matched
canonical MMD evidence."). These tests pin the quality-first posture: an
explicit keep/no-change route may be applied, but a UI recommendation or list
position is never treated as semantic proof. Phase 3.3 preserves an unhosted
Type by creating a separate source-grounded concept. Decisions whose only
meaningful action is user-only (source replacement, custom instruction) are
carried: nothing is applied, the uploaded document is untouched, and the run
still produces a map the reviewer can read and correct. No setting restores a
pause.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from app import models
from app.services import (
    autonomous_resolution,
    build_concepts,
    semantic_recovery,
)


def _pending_raw(
    *,
    options: list[dict] | None = None,
    candidates: list[dict] | None = None,
) -> dict:
    return {
        "decision_id": "phase31-ground-test-decision-01",
        "kind": "phase31_source_grounding_semantic_conflict",
        "phase": "3.1",
        "conflict": "Two grounding routes remain plausible for one concept.",
        "diagnosis": "The verified source supports one bounded route.",
        "decision_question": "Which grounding route should be kept?",
        "item": {
            "unit_id": "CONCEPT-GROUND-0007",
            "type_id": "",
            "type_title": "",
            "qids": [],
            "questions": [],
            "topic": "Source Topic",
        },
        "candidates": candidates
        if candidates is not None
        else [
            {
                "target_id": "CAND-KEEP-0001",
                "concept_id": "CONCEPT-GROUND-0007",
                "title": "Keep the concept as accepted",
                "topic": "Source Topic",
                "coverage": "",
                "gap": "",
                "action": "keep",
                "source_topic_id": "TOPIC-0001",
                "target_topic_id": "",
                "boundary_relation": "inside",
                "source_kind": "",
                "source_page": "",
                "text_sha256": "",
                "binding_hash": "",
                "source_block_ids": ["BLK-0001"],
            },
        ],
        "evidence": [
            {
                "evidence_id": "PENDING-EVIDENCE-001",
                "page": "7",
                "label": "Verified statement",
                "text": "The chapter source supports the accepted claim.",
            }
        ],
        "options": options
        if options is not None
        else [
            {
                "choice": "select_candidate",
                "label": "Keep the accepted concept",
                "recommended": True,
                "target_id": "CAND-KEEP-0001",
                "target_concept_id": "",
            },
            {
                "choice": "replace_source",
                "label": "Replace the uploaded source",
                "recommended": False,
            },
        ],
    }


def _seed_paused_job(db, chapter, monkeypatch, *, filename: str, raw=None):
    job = models.UploadJob(
        module="build_concepts",
        upload_type="document",
        learning_kind="post",
        filename=filename,
        mmd_text="## Source Topic\nVerified source wording about the topic.",
        status="converted",
    )
    db.add(job)
    db.flush()
    stage = build_concepts.generation._make_concept_checkpoint(
        "pre_type_assignment",
        records=[{
            "topic": "Source Topic",
            "parent_concept": "Parent",
            "concept_title": "Accepted Concept",
            "concept_details": "Description: Accepted.",
            "keywords": "accepted",
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
        raw if raw is not None else _pending_raw(),
        fingerprint=fingerprint,
        target_chapter_id=chapter.id,
        owner_sub=None,
    )
    return job, pending


# --------------------------------------------------------------------------- #
# Deterministic safe-option selection
# --------------------------------------------------------------------------- #

def test_uncertified_mutation_is_taken_by_best_judgement_and_flagged():
    """An uncertified route is now applied rather than declined.

    While a declined decision could still reach a person, refusing was right.
    It cannot any more: generation does not pause, and a stopped run produces
    no map at all. So Aegis takes the least-destructive offered route and the
    placement is flagged for review, which a reviewer can correct — where a
    missing concept could not be.
    """

    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "consolidate_types", "recommended": True},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [],
    })
    assert selected == {
        "choice": "consolidate_types",
        "target_id": "",
        "target_concept_id": "",
    }
    # The user-only route is never what best judgement reaches for.
    assert selected["choice"] not in autonomous_resolution.USER_ONLY_CHOICES


def test_explicit_no_change_wins_over_recommended_consolidation():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "consolidate_types", "recommended": True},
            {"choice": "keep_distinct_types", "recommended": False},
        ],
        "candidates": [],
    })
    assert selected == {
        "choice": "keep_distinct_types",
        "target_id": "",
        "target_concept_id": "",
    }


def test_user_only_recommendation_is_never_taken():
    """A recommended replace_source is still never selected.

    What changed is the alternative: the decision now carries instead of
    stopping the run. The route a person would have to take is not taken.
    """

    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "replace_source", "recommended": True},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [],
    })
    assert selected["choice"] == "carry_forward"
    assert selected["choice"] not in autonomous_resolution.USER_ONLY_CHOICES


def test_single_keep_candidate_route_is_the_fallback():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "select_candidate", "recommended": False},
            {"choice": "replace_source", "recommended": True},
        ],
        "candidates": [
            {
                "target_id": "CAND-KEEP-0001",
                "concept_id": "CONCEPT-GROUND-0007",
                "action": "keep",
            },
            {
                "target_id": "CAND-MOVE-0002",
                "concept_id": "CONCEPT-GROUND-0008",
                "action": "move",
            },
        ],
    })
    assert selected == {
        "choice": "select_candidate",
        "target_id": "CAND-KEEP-0001",
        "target_concept_id": "CONCEPT-GROUND-0007",
    }


def test_best_judgement_falls_back_to_the_only_offered_candidate():
    """An unknown recommended target does not become the selected target."""

    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {
                "choice": "accept_recommended",
                "recommended": True,
                "target_id": "CAND-UNKNOWN",
            },
        ],
        "candidates": [
            {"target_id": "CAND-KEEP-0001", "action": "keep"},
        ],
    })
    # The uncertified recommendation is still not trusted as a target. With one
    # offered candidate, "best judgement" and "the only judgement" coincide, so
    # the route resolves against the candidate that actually exists.
    assert selected is not None
    assert selected["target_id"] == "CAND-KEEP-0001"
    assert selected["target_id"] != "CAND-UNKNOWN"


def test_multiple_keep_candidates_are_not_ranked_by_list_order():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "select_candidate", "recommended": False},
        ],
        "candidates": [
            {"target_id": "CAND-KEEP-0001", "action": "keep"},
            {"target_id": "CAND-KEEP-0002", "action": "keep"},
        ],
    })
    # List order still ranks nothing: neither candidate is chosen.
    # Nothing is selected: the decision carries, leaving the source and the
    # generated map untouched, flagged for the reviewer.
    assert selected == {
        "choice": "carry_forward",
        "target_id": "",
        "target_concept_id": "",
    }


def test_keep_candidate_with_stale_binding_is_not_selected():
    selected = autonomous_resolution.safe_continuation_option({
        "options": [
            {"choice": "select_candidate", "recommended": False},
        ],
        "candidates": [{
            "target_id": "CAND-KEEP-STALE",
            "action": "keep",
            "binding_hash": "0" * 64,
        }],
    })
    # A stale seal is a corrupt payload, not an ambiguous one, so the
    # candidate is still never selected.
    # Nothing is selected: the decision carries, leaving the source and the
    # generated map untouched, flagged for the reviewer.
    assert selected == {
        "choice": "carry_forward",
        "target_id": "",
        "target_concept_id": "",
    }


def test_hash_sealed_source_patch_is_the_only_certified_recommendation():
    material = {
        "version": "phase3-canonical-topic-patch-1",
        "kind": "canonical_topic_binding",
        "target": "working_derived_source",
        "raw_source_mutated": False,
        "source_contract_hash": "1" * 64,
        "semantic_context_hash": "2" * 64,
        "before_sha256": "3" * 64,
        "after_sha256": "4" * 64,
        "operations": ["Restore numbered main topic 2"],
    }
    patch_hash = hashlib.sha256(json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    target_id = f"canonical-topic-patch-{patch_hash[:24]}"
    pending = {
        "kind": "phase3_source_graph_review",
        "item": {"type_id": "numbered_main_topic_coverage"},
        "candidates": [{"target_id": target_id}],
        "options": [{
            "choice": "accept_recommended",
            "label": "Apply verified working-source patch",
            "recommended": True,
            "target_id": target_id,
        }],
        "source_patch": {
            **material,
            "verified": True,
            "patch_hash": patch_hash,
            "target_id": target_id,
        },
    }

    assert autonomous_resolution.safe_continuation_option(pending) == {
        "choice": "accept_recommended",
        "target_id": target_id,
        "target_concept_id": "",
    }

    pending["source_patch"]["after_sha256"] = "5" * 64
    # The tampered patch is never applied. The decision carries instead, so
    # the working source is left exactly as uploaded.
    assert autonomous_resolution.safe_continuation_option(pending) == {
        "choice": "carry_forward",
        "target_id": "",
        "target_concept_id": "",
    }


def test_unattended_completion_is_env_gated(monkeypatch):
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    assert autonomous_resolution.unattended_completion_enabled() is True
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")
    assert autonomous_resolution.unattended_completion_enabled() is False


# --------------------------------------------------------------------------- #
# Escalation degrades to a recorded safe continuation, not a pause
# --------------------------------------------------------------------------- #

def test_escalated_review_applies_safe_continuation(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="safe-continuation.mmd")
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: autonomous_resolution.ResolutionResult(
            "escalated",
            "This source-critical action lacks issue-matched canonical "
            "MMD evidence.",
        ),
    )

    decision_id = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert decision_id == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger.get("pending") is None
    resolutions = {
        str(row.get("decision_id") or ""): row
        for row in ledger.get("resolutions") or []
    }
    recorded = resolutions[pending["decision_id"]]
    assert recorded["choice"] == "select_candidate"
    assert recorded["target_id"] == "CAND-KEEP-0001"
    assert recorded["resolved_by"] == "agent"
    assert recorded["status"] == "consumed"
    review = recorded["pending_decision"]["agent_review"]
    assert review["status"] == "resolved"
    assert review["reason"].startswith("Safe continuation after escalation:")
    assert "lacks issue-matched canonical MMD evidence" in review["reason"]


def test_escalation_still_pauses_when_unattended_mode_is_off(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="safe-continuation-off.mmd")
    monkeypatch.setenv("AEGIS_UNATTENDED_COMPLETION", "0")
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: autonomous_resolution.ResolutionResult(
            "escalated",
            "Confidence did not meet the safety threshold.",
        ),
    )

    outcome = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert outcome is None
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    saved = ledger["pending"]
    assert saved["decision_id"] == pending["decision_id"]
    assert saved["agent_review"]["status"] == "escalated"


def test_user_only_decision_carries_forward_instead_of_pausing(
    db,
    first_chapter,
    monkeypatch,
):
    """A decision offering only user-authority routes is recorded, not parked.

    This test previously asserted the opposite -- that the run stopped here --
    which is the behaviour ``carry_forward`` exists to replace. Neither route
    on offer may be automated, so nothing is applied: the uploaded source is
    untouched and generation continues with what it already produced.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _pending_raw(
        options=[
            {
                "choice": "replace_source",
                "label": "Replace the uploaded source",
                "recommended": True,
            },
            {
                "choice": "custom_instruction",
                "label": "Give another instruction",
                "recommended": False,
            },
        ],
        candidates=[],
    )
    raw["decision_id"] = "phase31-ground-test-decision-02"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="safe-continuation-user-only.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: autonomous_resolution.ResolutionResult(
            "escalated",
            "Only user-authority actions remain for this decision.",
        ),
    )

    outcome = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert outcome == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger.get("pending") is None
    recorded = next(
        row for row in ledger["resolutions"]
        if row["decision_id"] == pending["decision_id"]
    )
    assert recorded["choice"] == "carry_forward"
    assert recorded["resolved_by"] == "agent"
    assert recorded["status"] == "consumed"
    # Carrying settles the decision; it never smuggles in a target to act on.
    assert recorded["target_id"] == ""
    assert recorded["target_concept_id"] == ""
    assert recorded["instruction"] == ""


def test_pathway_cap_uses_safe_continuation_without_model_request(
    db,
    first_chapter,
    monkeypatch,
):
    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _pending_raw()
    raw["decision_id"] = "phase31-ground-test-decision-03"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="safe-continuation-cap.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setattr(autonomous_resolution, "enabled", lambda: True)
    monkeypatch.setattr(
        autonomous_resolution, "maximum_pathway_turns", lambda: 0)
    monkeypatch.setattr(
        autonomous_resolution,
        "resolve_pending",
        lambda *_args, **_kwargs: pytest.fail(
            "a capped scope must not start a model request"),
    )

    decision_id = build_concepts._autonomously_resolve_pending_decision(
        db, job, pending, owner_sub=None)

    assert decision_id == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    resolutions = {
        str(row.get("decision_id") or ""): row
        for row in ledger.get("resolutions") or []
    }
    recorded = resolutions[pending["decision_id"]]
    assert recorded["choice"] == "select_candidate"
    assert recorded["resolved_by"] == "agent"
    review = recorded["pending_decision"]["agent_review"]
    assert review["status"] == "resolved"
    assert review["reason"].startswith("Safe continuation:")


# --------------------------------------------------------------------------- #
# Carry forward: the run ships even when only user-only routes are offered
# --------------------------------------------------------------------------- #

def test_user_only_options_carry_instead_of_stopping_the_run():
    """A source-review decision no longer ends the run.

    Replacing the uploaded document and writing an instruction are the two
    things no automation may do. Rather than stopping, Aegis leaves the source
    exactly as uploaded, keeps what generation produced, and flags the
    decision so the reviewer sees it in the delivered output.
    """

    selected = autonomous_resolution.safe_continuation_option({
        "kind": "phase3_source_graph_review",
        "options": [
            {"choice": "replace_source", "recommended": True},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [],
    })

    assert selected == {
        "choice": "carry_forward",
        "target_id": "",
        "target_concept_id": "",
    }
    # Carrying is emphatically not choosing to replace the source.
    assert selected["choice"] not in autonomous_resolution.USER_ONLY_CHOICES


def test_carry_forward_changes_nothing():
    """It settles a decision; it never carries a target to act on."""

    carried = autonomous_resolution.carry_forward_option()

    assert carried["target_id"] == ""
    assert carried["target_concept_id"] == ""
    assert autonomous_resolution.is_automatable_choice(carried["choice"])
    assert carried["choice"] not in autonomous_resolution.USER_ONLY_CHOICES


def test_every_decision_shape_now_ships():
    """No offered option set leaves a run without an output."""

    shapes = [
        {"options": [{"choice": "replace_source"}], "candidates": []},
        {"options": [{"choice": "custom_instruction"}], "candidates": []},
        {"options": [], "candidates": []},
        {"options": [{"choice": "create_new"}], "candidates": []},
        {"options": [{"choice": "keep_distinct_types"}], "candidates": []},
        {
            "options": [{"choice": "select_candidate"}],
            "candidates": [
                {"target_id": "A", "action": "keep"},
                {"target_id": "B", "action": "keep"},
            ],
        },
    ]
    for pending in shapes:
        selected = autonomous_resolution.safe_continuation_option(pending)
        assert selected is not None, pending
        assert selected["choice"] not in autonomous_resolution.USER_ONLY_CHOICES
        # Selecting a route is only half the guarantee. Run 4 selected
        # carry_forward and then stopped anyway, because recording it was
        # rejected for not being one of the server-offered options. A shape
        # only ships if the chosen route survives that gate too.
        assert build_concepts._decision_choice_is_recordable(
            pending, selected["choice"]), pending


def test_bare_candidate_selector_over_several_candidates_is_recorded(
    db,
    first_chapter,
    monkeypatch,
):
    """The exact shape that stopped run 4.

    Phase 3 offered a bare ``select_candidate`` over several ranked evidence
    blocks alongside the two user-only routes. List position ranks nothing, so
    best judgement declines the selection and carries the decision instead.
    Recording that must succeed: ``carry_forward`` is deliberately never one of
    the server-offered options, so a membership check that did not exempt it
    stopped the run at the one point carrying was built to rescue.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _pending_raw(
        options=[
            {
                "choice": "select_candidate",
                "label": "Choose another verified PDF evidence block",
                "recommended": False,
            },
            {
                "choice": "custom_instruction",
                "label": "Tell Aegis what to do",
                "recommended": False,
            },
            {
                "choice": "replace_source",
                "label": "Replace or correct the source file",
                "recommended": True,
            },
        ],
        candidates=[
            {
                "target_id": "CAND-KEEP-0001",
                "concept_id": "CONCEPT-GROUND-0007",
                "title": "First ranked evidence block",
                "topic": "Source Topic",
                "action": "keep",
                "source_block_ids": ["BLK-0001"],
            },
            {
                "target_id": "CAND-KEEP-0002",
                "concept_id": "CONCEPT-GROUND-0008",
                "title": "Second ranked evidence block",
                "topic": "Source Topic",
                "action": "keep",
                "source_block_ids": ["BLK-0002"],
            },
        ],
    )
    raw["decision_id"] = "phase31-ground-test-decision-04"
    job, pending = _seed_paused_job(
        db,
        chapter,
        monkeypatch,
        filename="safe-continuation-bare-selector.mmd",
        raw=raw,
    )
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)

    decision_id = build_concepts._apply_last_resort_safe_continuation(
        db, job, pending, owner_sub=None)

    assert decision_id == pending["decision_id"]
    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger.get("pending") is None
    recorded = next(
        row for row in ledger["resolutions"]
        if row["decision_id"] == pending["decision_id"]
    )
    assert recorded["choice"] == "carry_forward"
    # Neither ranked block was substituted into the source on a guess.
    assert recorded["target_id"] == ""


def test_carry_forward_is_the_only_choice_exempt_from_the_offered_gate():
    """The exemption is narrow: it covers the one route that mutates nothing.

    A caller must not be able to apply a route the server never offered. That
    stays true for every mutating choice; only the do-nothing route is let
    through, because there is nothing for the gate to sanction.
    """

    pending = {"options": [{"choice": "select_candidate", "label": "Pick"}]}

    assert build_concepts._decision_choice_is_recordable(
        pending, "carry_forward")
    assert build_concepts._decision_choice_is_recordable(
        pending, "select_candidate")
    for choice in (
        "expand_existing",
        "create_new",
        "select_existing",
        "accept_recommended",
        "consolidate_types",
        "keep_distinct_types",
        "replace_source",
        "custom_instruction",
    ):
        assert not build_concepts._decision_choice_is_recordable(
            pending, choice), choice


# --------------------------------------------------------------------------- #
# A spent budget settles one scope; it does not discard the whole run
# --------------------------------------------------------------------------- #

def test_exhausted_scope_is_carried_and_the_run_continues(
    db,
    first_chapter,
    monkeypatch,
):
    """The 06:25 loop: one concept burning the budget must not kill the map.

    A live run raised the same issue key eighteen times in ninety minutes --
    one concept, ~90s of resolver time per attempt -- heading for a ceiling
    that raised ``SemanticResolutionCyclesExhausted`` and produced nothing.

    A spent budget means stop paying for that scope. It has never meant throw
    away the other forty concepts, which by then are finished and cached. The
    decision is carried, flagged, and generation continues.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    raw = _pending_raw()
    raw["decision_id"] = "phase31-ground-exhausted-0001"
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="exhausted-scope.mmd", raw=raw)
    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    monkeypatch.setenv("AEGIS_MAX_EQUIVALENT_RESOLUTION_ATTEMPTS", "1")
    monkeypatch.setattr(
        autonomous_resolution, "maximum_pathway_turns", lambda: 1)
    monkeypatch.setattr(
        build_concepts,
        "_persist_pending_human_decision",
        lambda *_a, **_kw: pending,
    )
    resolver_calls = 0

    def resolve(*_a, **_kw):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls > 1:
            pytest.fail("an exhausted scope must not buy another paid repair")
        return pending["decision_id"]

    monkeypatch.setattr(
        build_concepts, "_autonomously_resolve_pending_decision", resolve)
    logged: list[str] = []
    monkeypatch.setattr(
        build_concepts.progress, "log",
        lambda message, **_kw: logged.append(str(message)),
    )

    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        # The same scope returns after its one paid repair, which is exactly
        # the shape that burned ninety minutes on one concept in production.
        if calls <= 2:
            raise semantic_recovery.HumanDecisionRequired(pending)
        return "generation-finished"

    paused, result = build_concepts._run_with_human_decision_pause(
        operation,
        db=db,
        job=job,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=None,
    )

    # The run finished rather than stopping at the ceiling.
    assert paused is None
    assert result == "generation-finished"

    assert resolver_calls == 1
    db.refresh(job)
    recorded = job.generation_checkpoint["human_decisions"]["resolutions"][-1]
    assert recorded["choice"] == "carry_forward"
    assert recorded["resolved_by"] == "agent"
    # Nothing was applied on the way past.
    assert recorded["target_id"] == ""
    assert recorded["instruction"] == ""

    # And the reviewer can find it: concept_revisions scans for this wording.
    assert any(
        "placed by best judgement" in row.casefold() for row in logged
    ), logged
    # The old behaviour would have logged a stop instead.
    assert not any("Generation stopped after" in row for row in logged)


# --------------------------------------------------------------------------- #
# A rejection batch settles every decision on one replay
# --------------------------------------------------------------------------- #

def test_rejection_batch_settles_every_decision_on_one_replay(
    db,
    first_chapter,
    monkeypatch,
):
    """N independent conflicts must not cost N full pipeline replays.

    A critic that rejects several concepts at once used to surface only
    ``sorted(rejected)[0]``; every further rejection needed its own complete
    replay just to come into view. In the audited 05:32-06:25 window that
    turned 37 decisions into 37 serial cycles at roughly a hundred seconds
    each. The batch now travels with the exception, orchestration settles
    each decision in turn, and the pipeline replays once.
    """

    chapter = db.get(models.Chapter, first_chapter["id"])
    primary = _pending_raw()
    primary["decision_id"] = "phase31-ground-batch-0001"
    job, pending = _seed_paused_job(
        db, chapter, monkeypatch, filename="batch-settle.mmd", raw=primary)

    companions = []
    for index, hash_char in ((2, "b"), (3, "c")):
        row = _pending_raw()
        row["decision_id"] = f"phase31-ground-batch-000{index}"
        # Phase packets always carry their identity's context hash; the
        # exception requires it of companions exactly as it does of the
        # primary decision.
        row["context_hash"] = hash_char * 64
        row["item"] = dict(row["item"], unit_id=f"CONCEPT-GROUND-000{index}")
        companions.append(row)

    monkeypatch.delenv("AEGIS_UNATTENDED_COMPLETION", raising=False)
    logged: list[str] = []
    monkeypatch.setattr(
        build_concepts.progress, "log",
        lambda message, **_kw: logged.append(str(message)),
    )

    settled: list[str] = []

    def resolve(db_, job_, pending_, *, owner_sub):
        # Record durably, as the real resolver does, so the single pending
        # slot is free for the next companion in the batch.
        recorded = build_concepts._record_human_semantic_decision_locked(
            db_, job_, str(pending_["decision_id"]),
            choice="carry_forward", instruction="", target_id="",
            target_concept_id="", resolved_by="agent",
            resolution_status="consumed",
        )
        settled.append(str(pending_["decision_id"]))
        return str(recorded["resolved_decision"]["decision_id"])

    monkeypatch.setattr(
        build_concepts, "_autonomously_resolve_pending_decision", resolve)

    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise semantic_recovery.HumanDecisionRequired(
                pending, companions=companions)
        return "generation-finished"

    paused, result = build_concepts._run_with_human_decision_pause(
        operation,
        db=db,
        job=job,
        fingerprint=job.generation_checkpoint["fingerprint"],
        target_chapter_id=chapter.id,
        owner_sub=None,
    )

    assert paused is None
    assert result == "generation-finished"
    # One replay for three decisions -- the entire point.
    assert calls == 2
    assert settled == [
        "phase31-ground-batch-0001",
        "phase31-ground-batch-0002",
        "phase31-ground-batch-0003",
    ]
    assert any(
        "Settling 3 independent semantic decision(s)" in row for row in logged
    ), logged

    db.refresh(job)
    ledger = job.generation_checkpoint["human_decisions"]
    assert ledger["pending"] is None
    recorded_ids = {
        row["decision_id"] for row in ledger["resolutions"]
    }
    assert recorded_ids >= set(settled)
