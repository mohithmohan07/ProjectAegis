"""Unattended release staging for user-facing Build Concepts generation.

The low-level generation services retain their original contracts for internal
callers, tests, recovery tools and deliberately programmatic workflows. The
Build Concepts upload API calls the wrappers in this module, which stage a
release instead of publishing directly or surfacing a semantic choice.
"""
from __future__ import annotations

import copy
import inspect
import json
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping

from .. import models
from . import build_concepts, generation_recovery, uploads
from . import build_concepts_release as release
from . import build_concepts_terminal_release_contract as terminal_release
from . import progress
from . import build_concepts_release_files as release_files
from . import release_refiner
from . import storage_capacity
from .phase3 import kernel

# The two Master lanes build concurrently (owner "Go", 2026-08-21), each
# on its own database session. Their failure recorders read-modify-write
# the SAME job issue ledger; this lock serializes that write so two lanes
# failing at once cannot lose one lane's recorded reason (R4).
_LANE_ISSUE_LOCK = threading.Lock()


_CONTRACT_VERSION = 4
_RELEASE_MODE: ContextVar[bool] = ContextVar(
    "aegis_build_concepts_release_mode", default=False
)
_RELEASE_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_build_concepts_release_capture", default=None
)


def _capture_deposit(original, args, kwargs) -> tuple[list[int], list[int], dict[str, Any]]:
    signature = inspect.signature(original)
    bound = signature.bind_partial(*args, **kwargs)
    values = bound.arguments
    records = [
        copy.deepcopy(dict(row))
        for row in values.get("records") or []
        if isinstance(row, Mapping)
    ]
    job = values.get("grounding_audit_job")
    inventory = copy.deepcopy(
        values.get("inventory")
        or (
            job.question_inventory
            if isinstance(job, models.UploadJob)
            and isinstance(job.question_inventory, dict)
            else {}
        )
    )
    mined_types = copy.deepcopy(
        values.get("mined_types")
        or (
            inventory.get("mined_types")
            if isinstance(inventory, dict)
            else {}
        )
        or {}
    )
    final_certificate = copy.deepcopy(
        values.get("final_grounding_certificate") or {}
    )
    checkpoint = copy.deepcopy(
        job.generation_checkpoint
        if isinstance(job, models.UploadJob)
        else {}
    )
    phase3_pre_release = copy.deepcopy(
        values.get("phase3_pre_release")
        if isinstance(values.get("phase3_pre_release"), Mapping)
        else None
    )
    _RELEASE_CAPTURE.set({
        **release.generation_quality_fields({
            "phase3_pre_release": phase3_pre_release, "checkpoint": checkpoint,
        }, chapter_id=int(values.get("chapter_id") or 0)),
        "records": records,
        "inventory": inventory,
        "mined_types": mined_types,
        "final_grounding_certificate": final_certificate,
        "checkpoint": checkpoint,
        "target_chapter_id": int(values.get("chapter_id") or 0),
        "pre_post": str(values.get("pre_post") or ""),
        "source_book": str(values.get("source_book") or ""),
        "phase3_pre_release": phase3_pre_release,
    })
    # The legacy generation function reads this object after its deposit call.
    # It is deliberately truthful: no DB row and no shared workbook has been
    # written yet; the complete rows were captured for the released artifact.
    written = {
        "written": len(records),
        "sources_updated": 0,
        "grounding_certificate": final_certificate,
        "publication_status": "staged_release_only",
        "database_uploaded": False,
    }
    return [], [], written


def _refine_captured_records(
    db,
    job: models.UploadJob,
    target_chapter_id: int,
    captured: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The Refiner seam (docs/aegis-restructure.md §8.3), release mode.

    Runs after ``_capture_deposit`` produced the captured records and before
    ``release.stage_release``. Returns the rows to stage plus the recorded
    ``refinements`` payload entry. The Refiner must never block a release:
    any failure here stages the UNREFINED rows with an availability flag.
    """

    records = [
        copy.deepcopy(dict(row))
        for row in captured.get("records") or []
        if isinstance(row, Mapping)
    ]
    try:
        chapter = db.get(models.Chapter, int(target_chapter_id or 0))
        metadata = {
            **release.generation_quality_fields(captured, chapter_id=target_chapter_id),
            "board": chapter.board if chapter else "",
            "grade": chapter.grade if chapter else "",
            "subject": chapter.subject if chapter else "",
            "unit": chapter.unit if chapter else "",
            "chapter_title": chapter.chapter_title if chapter else "",
            "chapter_code": chapter.chapter_code if chapter else "",
            "pre_post": "Pre" if job.learning_kind == "pre" else "Post",
            # Contract v2.0 §18: the publication only, never a filename.
            "source_book": job.source_book or "",
            "inventory": captured.get("inventory") or {},
            "mined_types": captured.get("mined_types") or {},
            "source_text": str(job.mmd_text or ""),
        }
        refined, diff, flags = release_refiner.refine_release(
            records,
            metadata=metadata,
            instruction_set=release._instruction_set_summary(job),
            store=release_refiner.decision_store_for_job(int(job.id)),
        )
        return refined, {**diff, "review_flags": list(flags)}
    except Exception as exc:  # noqa: BLE001 - the Refiner never blocks
        flag = f"refiner unavailable: {type(exc).__name__}: {exc}"
        return records, {
            "policy_version": release_refiner.REFINER_POLICY_VERSION,
            "output_kind": "concepts_release",
            "changes": [],
            "summary": flag,
            "resealed_after_refinement": False,
            "review_flags": [flag],
        }


def _stage_pre_sibling(
    db,
    job,
    target_chapter_id: int,
    *,
    inventory: Mapping[str, Any] | None,
    phase3_pre_release: Mapping[str, Any] | None = None,
    checkpoint_envelope: Mapping[str, Any] | None = None,
    reason: str,
) -> None:
    """Stage Pre Outputs 01/02 beside whatever the Post lane just released.

    Called after EVERY ``stage_release`` on this path, not only after a
    clean capture. A clean path hands the exact in-memory authority through
    the deposit interceptor; checkpoint exits recover the same bundle, with
    legacy sidecars as the final compatibility source. Thus a run that
    completed Phase 03 and failed later still ships its Pre sibling rather
    than looking like a chapter with no Pre lane — the R4 distinction the
    three-state legacy reader exists to preserve.

    Never raises, and always runs AFTER the Post release is staged:
    ``stage_pre_release_from_run`` logs and returns ``None`` on any
    failure, so a Pre-lane problem can never cost the finished Post rows.
    """

    try:
        db.refresh(job)
    except Exception as exc:  # noqa: BLE001 - the guarantee above is total
        # ``stage_pre_release_from_run`` never raises, but this refresh
        # sits OUTSIDE it and a session error here would take the staged
        # Post release down with it — exactly what "never raises" exists
        # to prevent.
        progress.log(
            "The Pre-Learning outputs could not be staged (the job row "
            f"could not be refreshed: {type(exc).__name__}: {exc}); the "
            "Post-Learning release is unaffected and ships.",
            level="error",
        )
        return
    release.stage_pre_release_from_run(
        db,
        job,
        target_chapter_id=target_chapter_id,
        inventory=inventory or {},
        phase3_pre_release=phase3_pre_release,
        # The envelope captured at the deposit boundary — the direct
        # transport of the Pre authority's checkpoint source, independent
        # of the clear/restore the success path performs on the job row
        # between deposit and this staging.
        checkpoint_envelope=checkpoint_envelope,
        terminal_checkpoint_proof=copy.deepcopy(
            build_concepts._PRE_RELEASE_TERMINAL_PROOF.get()
        ),
        reason=reason,
    )


def _release_after_result(
    db,
    job_id: int,
    target_chapter_id: int,
    *,
    owner_sub: str | None,
    result: object,
    captured: dict[str, Any] | None,
) -> dict[str, Any]:
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    pending = None
    if isinstance(result, Mapping):
        raw_pending = result.get("pending_decision")
        if isinstance(raw_pending, Mapping):
            pending = dict(raw_pending)
    if captured:
        # A real stage boundary (usage attribution reads the last step and
        # the console renders stage cards from step events), and a fresh
        # bar value: the concept Refiner still runs one recorded decision
        # per rendered row before anything is staged.
        progress.step(
            "Refining and staging release outputs (Outputs 01/03)",
            value=0.945,
        )
        refined_records, refinements = _refine_captured_records(
            db,
            job,
            target_chapter_id,
            captured,
        )
        # The run's own completion fact, handed to the terminal contract:
        # this staging comes from a CAPTURED TERMINAL DEPOSIT (generation
        # finished and delivered its final rows through the deposit
        # interceptor). The recorded verdict must come from that first-hand
        # fact — never from how the checkpoint snapshot happens to
        # re-validate under the strict resume filter.
        deposit_token = release.TERMINAL_DEPOSIT_STAGING.set(True)
        try:
            staged = release.stage_release(
                db,
                job,
                target_chapter_id=target_chapter_id,
                records=refined_records,
                inventory=captured.get("inventory") or {},
                mined_types=captured.get("mined_types") or {},
                final_grounding_certificate=(
                    captured.get("final_grounding_certificate") or {}
                ),
                checkpoint=captured.get("checkpoint")
                or job.generation_checkpoint,
                pending_decision=pending,
                reason=(
                    "Generation completed. The output was staged and was not "
                    "uploaded to the database."
                ),
                refinements=refinements,
                generation_policy=release.generation_quality_fields(
                    captured, chapter_id=target_chapter_id,
                ),
            )
        finally:
            release.TERMINAL_DEPOSIT_STAGING.reset(deposit_token)
        # Outputs 03/04 (§5, spec T3): the SIBLING slot on this same job.
        # One run produces all four outputs (Q3), so the Pre release is
        # staged here beside the Post one rather than on a job of its own
        # — ``learning_kind`` stays "post" on the row and the lane rides
        # the key.
        _stage_pre_sibling(
            db,
            job,
            target_chapter_id,
            inventory=captured.get("inventory") or {},
            phase3_pre_release=captured.get("phase3_pre_release"),
            checkpoint_envelope=captured.get("checkpoint"),
            reason=(
                "Generation completed. The Phase 03 Pre-Learning outputs "
                "were staged and were not uploaded to the database."
            ),
        )
        return staged
    staged = release.stage_release(
        db,
        job,
        target_chapter_id=target_chapter_id,
        pending_decision=pending,
        reason=(
            "Generation reached an unresolved semantic boundary. Aegis "
            "released the newest durable checkpoint instead of asking the "
            "user to choose during generation."
        ),
    )
    _stage_pre_sibling(
        db,
        job,
        target_chapter_id,
        inventory=None,
        reason=(
            "Generation reached an unresolved semantic boundary. The "
            "Phase 03 Pre-Learning outputs this run had already recorded "
            "were staged beside the released checkpoint."
        ),
    )
    return staged


def _lane_master_eligibility(
    db,
    job_id: int,
    lane: str,
    *,
    owner_sub: str | None = None,
) -> tuple[bool, str]:
    """Can this lane's staged Concept release safely author a Master file?

    The assessment lane reads the staged Concept payload for ITS lane. An
    empty slot cannot be built. Neither can a non-terminal checkpoint
    release: the model-heavy Master pipeline cannot repair its missing
    generation authority; it can only spend against an output whose database
    upload is already blocked. Job 81 demonstrated that failure mode after
    its 81% checkpoint.

    Asking first matters because the failure is otherwise unrecordable.
    ``record_assessment_lane_unavailable`` writes onto the lane's staged
    payload, so with no payload there is nothing to write onto: the
    exception is caught and then DROPPED — neither built nor recorded.
    [measured] a job with no Pre slot attempts the Pre lane, raises
    ``ReleaseRunError``, and records nothing anywhere. A chapter with no
    Phase-03 pre map is the ordinary shape of that, so every Post-only
    run was paying for a doomed attempt and discarding its reason.

    An unreadable answer ATTEMPTS. This may skip a lane only on positive
    evidence that its slot is empty, never on a database it could not
    read — a lane skipped by accident is exactly the silent loss the
    whole containment exists to prevent.
    """

    try:
        job = uploads.get_job(
            db, job_id, owner_sub=owner_sub, module="build_concepts")
        from . import reviewed_file_input
        if (((job.question_inventory or {}).get(reviewed_file_input.INPUTS) or {}).get(lane)
                or reviewed_file_input.active(release.release_payload(job, lane=lane))):
            return True, ""
        # Restructure A (owner approval, 2026-08-29): "is this run
        # finished" was decided ONCE, at staging, and recorded on the
        # payload as its explicit terminal verdict. This gate READS that
        # recorded fact; it never re-derives it from the live generation
        # checkpoint. [measured] job 'Patterns': a fully completed run
        # whose staged payload echoed a mid-run stage flunked the derived
        # check on every Master click while both Concept files sat staged
        # and healthy. Legacy payloads staged before the verdict existed
        # are backfilled once from durable evidence by
        # ``ensure_explicit_terminal_verdict``.
        #
        # Pre is a sibling projection of the same Concept run and has no
        # independent generation lifecycle. A non-terminal Post authority
        # therefore blocks both Master lanes even if an older/partial Pre
        # slot happens to look complete in isolation.
        post_verdict = terminal_release.ensure_explicit_terminal_verdict(
            db, job, lane=release.LANE_POST
        )
        if post_verdict is False:
            return False, (
                "the staged Concept release records a non-terminal "
                "generation run"
            )
        lane_verdict = terminal_release.ensure_explicit_terminal_verdict(
            db, job, lane=lane
        )
        if lane_verdict is None:
            return False, "no staged Concept release"
        if lane_verdict is False:
            return False, (
                "the staged Concept release records a non-terminal "
                "generation run"
            )
        return True, ""
    except Exception:
        # An unreadable answer ATTEMPTS.  A transient read failure must not
        # silently turn a promised healthy Master output into a skip; the
        # runner's normal failure ledger will record the concrete problem.
        return True, ""


def _record_master_failure(
    db,
    job_id: int,
    lane: str,
    *,
    owner_sub: str | None,
    error: BaseException,
) -> None:
    """The single serialized write seam for every Master-lane failure."""

    try:
        with _LANE_ISSUE_LOCK:
            db.rollback()
            job = uploads.get_job(
                db,
                job_id,
                owner_sub=owner_sub,
                module="build_concepts",
            )
            release.record_assessment_lane_unavailable(
                db, job, lane=lane, error=error,
            )
    except Exception:
        # The underlying recorder is deliberately non-raising. This second
        # ring preserves the original failure even when the full volume also
        # prevents the database from recording its diagnosis.
        pass


def rebuild_lane_master(
    db,
    job_id: int,
    lane: str,
    *,
    owner_sub: str | None = None,
    claim_job_lock: bool = False,
    stage_progress=None,
):
    """Build one Master lane, recording any failure before it propagates.

    The one recorder site for both triggers of a Master build. The in-run
    sibling build swallows the re-raise (losing a lane must not cost
    Outputs 01/03 — see ``_build_master_siblings``); the explicit re-build
    routes let it propagate to an HTTP status. Either way the failure is
    already recorded as that lane's ``assessment_lane_unavailable`` issue,
    so the outputs card carries the reason whether the lane failed inside
    the run or from the reviewer's re-build (Rule G's idempotent second
    act).
    """

    from . import assessment_release_run

    runner = (
        assessment_release_run.run_pre_release_for_job
        if lane == release.LANE_PRE
        else assessment_release_run.run_release_for_job
    )

    def _require_terminal_concept_release() -> None:
        job = uploads.get_job(
            db,
            job_id,
            owner_sub=owner_sub,
            module="build_concepts",
        )
        db.refresh(job)
        from . import reviewed_file_input
        if not reviewed_file_input.has_input(job, lane):
            generation_recovery.require_mutation_allowed(
                job, operation=f"rebuild the {lane} Master file"
            )
        eligible, reason = _lane_master_eligibility(
            db,
            job_id,
            lane,
            owner_sub=owner_sub,
        )
        if not eligible:
            raise assessment_release_run.ReleaseRunError(
                f"The {lane} Master file cannot be built: {reason}. "
                "Resume Concept generation first."
            )

    def _run_and_record():
        try:
            # A lane reserves capacity before the runner can create its
            # decision store or make its first provider call. The reservation
            # is mechanical filesystem accounting; no authored content enters
            # the decision.
            with storage_capacity.reserve_master_capacity(
                job_id=job_id,
                lane=lane,
            ) as capacity:
                progress.log(
                    f"Master storage preflight passed for the {lane} lane "
                    f"before provider spend ({capacity.available_bytes} "
                    "bytes available)."
                )
                from . import reviewed_file_input
                reviewed_job = uploads.get_job(db, job_id, owner_sub=owner_sub, module="build_concepts")
                reviewed_file_input.prepare(db, reviewed_job, lane=lane, owner_sub=owner_sub)
                if lane == release.LANE_PRE:
                    reviewed_job = uploads.get_job(
                        db, job_id, owner_sub=owner_sub, module="build_concepts"
                    )
                    _regenerate_pre_questions_after_review(
                        db, reviewed_job, owner_sub=owner_sub,
                    )
                return runner(
                    db,
                    job_id,
                    owner_sub=owner_sub,
                    stage_progress=stage_progress,
                )
        except Exception as exc:
            storage_error = storage_capacity.capacity_error_from(
                exc,
                phase="Master generation",
            )
            recorded_error = storage_error or exc
            if storage_error is not None:
                progress.log(
                    f"Master storage refused the {lane} lane at "
                    f"{storage_error.phase}: {storage_error}",
                    level="warning",
                )
            _record_master_failure(
                db,
                job_id,
                lane,
                owner_sub=owner_sub,
                error=recorded_error,
            )
            if storage_error is not None and storage_error is not exc:
                raise storage_error from exc
            raise

    if not claim_job_lock:
        # Automatic Pre/Post siblings are already inside the original Build
        # Concepts operation lock. Re-acquiring its non-reentrant lock from
        # their worker threads would reject the run that owns it.
        _require_terminal_concept_release()
        return _run_and_record()

    # Explicit rebuilds are independent HTTP mutations. Verify ownership
    # before consulting the lock, then claim the same lock used by generation
    # so a click cannot overlap the original run or a second rebuild.
    uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    with uploads.exclusive_job_operation(job_id):
        _require_terminal_concept_release()
        return _run_and_record()


def _build_master_siblings(
    db,
    job_id: int,
    target_chapter_id: int,
    *,
    owner_sub: str | None = None,
    progress_start: float = 0.955,
    progress_end: float = 0.990,
) -> dict[str, dict[str, Any] | None]:
    """Outputs 02 and 04, in the same run that produced 01 and 03.

    THE OWNER'S RULING (OD1 / spec-step8 T15): one Build Concepts run
    produces all four outputs. There is no option, no fallback and no
    mailbox — the two API routes that build these lanes stay, but as the
    reviewer's explicit RE-BUILD against the already-frozen release row
    (Rule G's idempotent second act), not as the only trigger.

    **It never propagates, and that is a Q13 requirement rather than a
    robustness nicety.** By the time this runs the two CONCEPT outputs are
    finished and durable: the payload is staged for both lanes and
    ``release-bulk-import.xlsx`` already renders. An exception escaping
    here would propagate out of ``generate_post_learning`` and take them
    with it — a mid-run halt after the model budget is spent, losing
    finished work, which CLAUDE.md and Q13 forbid. One Master-lane fault
    would cost all four outputs.

    So each lane is wrapped in ``try/except Exception``. Deliberately
    broad, and the reason is stated rather than apologised for: an
    enumerated tuple of exception types is a list that goes stale, and the
    one thing that must never happen is a NEW exception type costing
    Outputs 01/03. [verified] the types it will actually see today are
    ``assessment_release_run.ReleaseRunError`` and its subclasses
    ``GeneratedLaneError`` / ``SourceQuestionLeak``,
    ``assessment_release_service.UploadRefused``,
    ``assessment_workbook.WorkbookRenderError`` and
    ``phase3.premap.PreExtractionError``.

    **Caught is not swallowed.** Each failure becomes a named
    ``assessment_lane_unavailable`` issue on THAT LANE'S CONCEPT release,
    through the issue ledger that already exists, carrying the lane, the
    exception class, its message and the staged draft version. The concept
    release's ``release_state`` is unchanged and its database upload stays
    open — only that lane's Master manifest entry goes ``disabled``,
    carrying the recorded issue as its reason.

    In the owner's numbering: Output 02 (Pre) and Output 04 (Post) — and
    since the owner's "Go" (2026-08-21) they build CONCURRENTLY: neither
    lane's outcome is an input to the other's, each lane runs on its own
    database session (one SQLAlchemy session is not thread-safe), their
    durable audit snapshots live in per-lane subdirectories, and the
    failure recorder is lock-serialized. A Pre failure still never skips
    the Post build, and vice versa.
    """

    from . import assessment_release_run

    built: dict[str, dict[str, Any] | None] = {}
    lanes: list[str] = []
    for lane in (release.LANE_PRE, release.LANE_POST):
        eligible, skip_reason = _lane_master_eligibility(
            db, job_id, lane, owner_sub=owner_sub,
        )
        if not eligible:
            # No slot, so nothing to build a Master from and nowhere
            # to record a failure onto. Skipping is the honest
            # answer: the alternative is a doomed run whose reason is
            # thrown away. The lane's Master entry is still PRESENT
            # and disabled with the "not built for this run" reason,
            # so nothing about it is silent — and neither is this skip:
            # the journal says which lane was skipped and why.
            progress.log(
                f"The {lane} lane Master File is not built because "
                f"{skip_reason}. Output "
                f"{'02' if lane == release.LANE_PRE else '04'} remains "
                "unavailable on this run without provider spend.",
                level="warning",
            )
            built[lane] = None
            continue
        lanes.append(lane)
    if not lanes:
        return built

    # The two automatic siblings are one promised output batch. Check their
    # combined reservation before either worker can spend; otherwise a nearly
    # full volume could let the first lane start while immediately refusing
    # the second. Explicit one-lane rebuilds retain the per-lane guard above.
    try:
        # Admission and reservation are ONE operation: all lane tokens appear
        # atomically. Each remains until its worker has joined, at which point
        # statvfs already carries that lane's real consumption.
        with storage_capacity.reserve_master_batch_capacity(
            job_id=job_id,
            lanes=lanes,
        ) as batch_reservation:
            batch_capacity = batch_reservation.snapshot
            progress.log(
                f"Master storage preflight passed for the {len(lanes)}-lane "
                f"batch before provider spend "
                f"({batch_capacity.available_bytes} bytes available)."
            )

            # This must be a real stage boundary, not only a progress-label
            # change: usage attribution reads the last ``progress.step`` and
            # the frontend creates stage cards from step events.
            progress.step(
                "Building Master files (Outputs 02/04)",
                value=progress_start,
            )
            # The Master builds own the caller's fixed band (0.955 → 0.990
            # for legacy runs; 0.70 → 0.99 after Concept review) and fill it
            # as their stages (and the long fan-outs' units) finish, so
            # the console no longer freezes on one value for the entire
            # build — the "97% for hours" report. One shared span, one
            # equal-weight tracker per lane; emission is monotone, so the
            # two concurrent lanes cannot walk the bar backward. Keep the
            # ceiling at 0.990, not 0.995: the phone UI rounds to a whole
            # percentage, and 99.5% rendered as the same misleading 100% as
            # a genuinely complete four-output set.
            span = progress.Span(
                progress_start, progress_end,
                label="Building Master files (Outputs 02/04)"
            )
            stage_index = {
                name: position
                for position, name in enumerate(
                    assessment_release_run.MASTER_BUILD_STAGES
                )
            }
            stage_index["done"] = len(
                assessment_release_run.MASTER_BUILD_STAGES
            )

            def _lane_observer(lane: str):
                tracker = span.tracker(float(stage_index["done"]))
                lane_name = "Pre" if lane == release.LANE_PRE else "Post"

                def observe(stage, done=None, total=None) -> None:
                    position = stage_index.get(str(stage))
                    if position is None:
                        return
                    units = float(position)
                    suffix = ""
                    if done is not None and total:
                        units += max(0.0, min(1.0, float(done) / float(total)))
                        suffix = f" {int(done)}/{int(total)}"
                    tracker.set_units(
                        units,
                        label=(
                            f"Master files — {lane_name}: {stage}{suffix}"
                        ),
                    )

                return observe

            # Both trackers register BEFORE the fan-out starts: a lazily
            # registered second tracker would briefly let the first lane's
            # fraction fill the whole band, and the monotone guard would
            # then hold the bar flat until the true mean caught up.
            lane_observers = {lane: _lane_observer(lane) for lane in lanes}

            def _build_lane(lane: str) -> dict[str, Any] | None:
                from ..db import SessionLocal

                lane_db = SessionLocal()
                try:
                    # Bind inside the worker itself. Context propagation by a
                    # pool is not assumed, and the lane's normal reservation
                    # then borrows (rather than double-counts) its batch slice.
                    with storage_capacity.use_master_batch_lane(
                        batch_reservation,
                        job_id=job_id,
                        lane=lane,
                    ):
                        result = {"release_id": rebuild_lane_master(
                            lane_db,
                            job_id,
                            lane,
                            owner_sub=owner_sub,
                            stage_progress=lane_observers[lane],
                        ).id}
                        lane_db.commit()
                    return result
                except Exception:  # noqa: BLE001 — see the docstring
                    # ``rebuild_lane_master`` already recorded the failure as
                    # this lane's issue. Swallow only here so the sibling and
                    # both finished Concept outputs remain available.
                    try:
                        lane_db.rollback()
                    except Exception:
                        pass
                    return None
                finally:
                    lane_db.close()

            results = kernel.parallel_map_in_order(
                lanes,
                _build_lane,
                max_workers=len(lanes),
                # These workers orchestrate independent releases. Each lane
                # still needs its bounded per-decision pool; treating the
                # lane wrapper as that pool serialized every Master stage.
                orchestration=True,
                labels=[
                    "Master · Output 02 (Pre)"
                    if lane == release.LANE_PRE
                    else "Master · Output 04 (Post)"
                    for lane in lanes
                ],
                announce="Master files",
            )
    except storage_capacity.StorageCapacityError as exc:
        for lane in lanes:
            _record_master_failure(
                db,
                job_id,
                lane,
                owner_sub=owner_sub,
                error=exc,
            )
            built[lane] = None
        progress.log(str(exc), level="warning")
        return built

    built.update(dict(zip(lanes, results)))
    # The lanes committed on their own sessions; drop this session's
    # cached state so the caller reads their results, not stale rows.
    db.expire_all()
    return built


def _run_generation_release(
    original: Callable[..., object],
    db,
    job_id: int,
    target_chapter_id: int,
    *args,
    **kwargs,
) -> dict[str, Any]:
    """Stage the two concept lanes, then build each eligible Master lane.

    Three lines, and every one of them is load-bearing:

    * ``_stage_generation_release`` is today's whole body, moved verbatim.
      All four exits — clean/captured, clean/checkpoint, raised/captured,
      raised/checkpoint — and all four ``stage_release`` /
      ``_stage_pre_sibling`` pairs stay exactly where they were.
    * ``_build_master_siblings`` is called ONCE, on the single tail all
      four exits converge on. Its eligibility boundary refuses non-terminal
      Concept releases before any Master provider spend, while completed
      Pre/Post lanes retain their parallel build. Keeping the call here,
      rather than four times beside ``_stage_pre_sibling``, also means it is
      OUTSIDE the ``_RELEASE_MODE`` /
      ``_RELEASE_CAPTURE`` context vars, whose ``finally`` has already run
      by the time control reaches here, so the deposit interceptor
      ``install()`` wires cannot see the assessment lane. One site means
      a fifth exit added later inherits it rather than being forgotten.
    * the staged Concept result remains the authority, with one additive
      ``master_outputs`` operation summary so the streamed result cannot say
      four files are ready when either Master sibling was refused.
    """

    # This flag is an API lifecycle choice, not a low-level generation
    # argument.  Remove it before calling the historical generation service so
    # direct/internal callers keep their old signature and behavior.
    pause_for_concept_review = bool(kwargs.pop("pause_for_concept_review", False))
    if pause_for_concept_review:
        # Historical Concept stages report their own 0..1 values, and some
        # terminal paths report 0.98/1.0 before the review handoff. Keep the
        # whole Concept half in its explicit 0..0.70 allocation so the review
        # pause can never look like a completed run.
        with progress.fixed_allocation(0.0, 0.70):
            staged = _stage_generation_release(
                original, db, job_id, target_chapter_id, *args, **kwargs)
    else:
        staged = _stage_generation_release(
            original, db, job_id, target_chapter_id, *args, **kwargs)
    if pause_for_concept_review and not isinstance(staged.get("run_incomplete"), Mapping):
        owner_sub = kwargs.get("owner_sub")
        current_job = uploads.get_job(
            db, job_id, owner_sub=owner_sub, module="build_concepts"
        )
        concept_review = release.initialize_concept_review(
            db, current_job, target_chapter_id=target_chapter_id
        )
        uploads.pause_run_for_review(
            db,
            job_id,
            progress_value=0.70,
            stage="Concept files ready for review",
            owner_sub=owner_sub,
        )
        progress.step("Concept files ready for review", value=0.70)
        progress.log(
            "Concept files are staged and ready for reviewer corrections. "
            "Master authoring is paused until the reviewed input is submitted."
        )
        result = dict(staged)
        result.update({
            "concept_review": concept_review,
            "review_required": True,
            "master_outputs": {
                lane: {"ready": False, "reason": "awaiting Concept review"}
                for lane in (release.LANE_PRE, release.LANE_POST)
            },
            "all_four_outputs_ready": False,
            "output_completion": {
                "ready_count": sum(
                    1 for lane in (release.LANE_PRE, release.LANE_POST)
                    if release.release_payload(current_job, lane=lane) is not None
                ),
                "total_count": 4,
                "all_ready": False,
                "missing": [
                    {
                        "number": "02" if lane == release.LANE_PRE else "04",
                        "lane": lane,
                        "label": (
                            "Pre-Learning Master File"
                            if lane == release.LANE_PRE
                            else "Post-Learning Master File"
                        ),
                    }
                    for lane in (release.LANE_PRE, release.LANE_POST)
                ],
            },
        })
        return result
    # HONEST PROGRESS (owner report, 2026-08-21: "after 100% it is still
    # running" — and paying). ``_build_master_siblings`` opens the real
    # stage boundary after it confirms at least one lane exists; only a true
    # four-file end says Done, while a terminal lane failure stays at 99% and
    # names the observed missing output rather than presenting a promise.
    master_builds = _build_master_siblings(
        db, job_id, target_chapter_id, owner_sub=kwargs.get("owner_sub"))
    master_outputs = {
        lane: {
            "ready": master_builds.get(lane) is not None,
            **(master_builds.get(lane) or {}),
        }
        for lane in (release.LANE_PRE, release.LANE_POST)
    }
    all_four_ready = all(
        bool(master_outputs[lane]["ready"])
        for lane in (release.LANE_PRE, release.LANE_POST)
    )
    incomplete = staged.get("run_incomplete")
    missing_lanes = [
        lane
        for lane in (release.LANE_PRE, release.LANE_POST)
        if not master_outputs[lane]["ready"]
    ]
    ready_output_count = 4 - len(missing_lanes)
    output_completion = {
        "ready_count": ready_output_count,
        "total_count": 4,
        "all_ready": all_four_ready,
        "missing": [
            {
                "number": "02" if lane == release.LANE_PRE else "04",
                "lane": lane,
                "label": (
                    "Pre-Learning Master File"
                    if lane == release.LANE_PRE
                    else "Post-Learning Master File"
                ),
            }
            for lane in missing_lanes
        ],
    }
    if isinstance(incomplete, Mapping):
        if incomplete.get("resume_allowed") is False:
            done_label = "Incomplete — new upload and conversion required"
        else:
            done_label = "Incomplete — resume from the saved checkpoint"
    elif all_four_ready:
        done_label = "Done — all four outputs ready"
    else:
        missing = ", ".join(
            "Pre Master" if lane == release.LANE_PRE else "Post Master"
            for lane in missing_lanes
        )
        done_label = (
            f"Incomplete — {ready_output_count}/4 outputs ready "
            f"(unavailable: {missing})"
        )
    if isinstance(incomplete, Mapping):
        try:
            current_job = uploads.get_job(
                db,
                job_id,
                owner_sub=kwargs.get("owner_sub"),
                module="build_concepts",
            )
            final_progress = min(
                0.99, max(0.0, float(current_job.checkpoint_progress))
            )
        except Exception:  # pragma: no cover - the marker remains authoritative
            final_progress = 0.0
    elif all_four_ready:
        final_progress = 1.0
    else:
        # A terminal Master-lane failure is recoverable from the frozen
        # Concept release, but it is not 100% complete. Leave the bar at the
        # same 99% ceiling used by the Master span; the explicit lane rebuild
        # is the act that can turn this run into a four-file completion.
        final_progress = 0.99
    progress.set_progress(final_progress, label=done_label)
    result = dict(staged)
    result["master_outputs"] = master_outputs
    result["all_four_outputs_ready"] = all_four_ready
    if not isinstance(incomplete, Mapping):
        # A non-terminal generation exit may not have both Concept lanes, so
        # its own ``run_incomplete`` checkpoint contract remains the only
        # completion authority. The 2+Master count is valid only after the
        # terminal Concept staging path has made Outputs 01/03 durable.
        result["output_completion"] = output_completion
    return result


def _reviewed_pre_missing_question_ids(payload: Mapping[str, Any]) -> list[str]:
    """Exact retained-row to question membership, with no content judgment."""
    bank_ids = {
        str(row.get("pre_question_id") or "")
        for row in payload.get("generated_questions") or [] if isinstance(row, Mapping)
    }
    owners = {
        str(row.get("pre_concept_id") or "")
        for row in payload.get("generated_questions") or [] if isinstance(row, Mapping)
    }
    return [
        str(row.get("_pre_concept_id") or "")
        for row in payload.get("records") or []
        if isinstance(row, Mapping)
        and str(row.get("_pre_concept_id") or "") not in owners
        and not (set(row.get("_aegis_pre_generated_questions") or []) & bank_ids)
    ]


def _reviewed_pre_recovery_needed(job, state: Mapping[str, Any]) -> bool:
    from . import generation_repair_policy as repair

    corrected = (state.get("corrected_inputs") or {}).get(release.LANE_PRE)
    payload = release.release_payload(job, lane=release.LANE_PRE) or {}
    changed = isinstance(corrected, Mapping) and corrected.get("changed")
    return bool((changed or repair.active(payload)) and _reviewed_pre_missing_question_ids(payload))


def _regenerate_pre_questions_after_review(
    db,
    job: models.UploadJob,
    *,
    owner_sub: str | None = None,
) -> dict[str, Any] | None:
    """Recover a changed Pre bank or missing questions in accepted Q48 scope.

    Pre questions are generated from the accepted Pre concept map by the
    existing Phase 03 ``prequestions.build`` path.  A corrected Pre workbook
    invalidates only that generated bank; Post questions remain the exact
    reviewed source inventory.  The regenerated bank is staged as one new
    Pre Concept/Pre Master sibling payload before either Master lane runs.
    """

    state = release.concept_review_state(job)
    pre_input = (state.get("corrected_inputs") or {}).get(release.LANE_PRE)
    corrected_changed = isinstance(pre_input, Mapping) and bool(pre_input.get("changed"))
    pre_payload = release.release_payload(job, lane=release.LANE_PRE)
    if pre_payload is None:
        return None
    from . import reviewed_file_input
    if reviewed_file_input.active(pre_payload):
        return reviewed_file_input.ensure_pre_questions(db, job, owner_sub=owner_sub)
    if not corrected_changed and not _reviewed_pre_recovery_needed(job, state):
        return None
    current_uid = str(pre_payload.get(release.STAGED_RELEASE_UID_FIELD) or "")
    if (
        current_uid
        and str(state.get("pre_questions_regenerated_for_uid") or "")
        == current_uid
        and not _reviewed_pre_missing_question_ids(pre_payload)
    ):
        return None

    # Reuse the canonical-source artifact locator.  ``uploads`` receives the
    # public helper during service-contract installation, but direct recovery
    # harnesses can call this workflow before that attribute is attached; the
    # canonical contract's locator is the same path and keeps the sealed
    # envelope/decision store join intact in both cases.
    artifact_helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(artifact_helper):
        from . import canonical_source_contract
        artifact_helper = canonical_source_contract._artifact_directory
    try:
        artifact_dir = Path(artifact_helper(int(job.id)))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(
            "the job has no Phase-3 artifact directory for Pre regeneration"
        ) from exc
    envelope_path = artifact_dir / "source.phase3-envelope.json"
    try:
        wrapper = json.loads(envelope_path.read_text(encoding="utf-8"))
        envelope = wrapper.get("envelope") if isinstance(wrapper, Mapping) else wrapper
        if not isinstance(envelope, Mapping):
            raise ValueError("the stored Phase-3 envelope is not an object")
        from .phase3 import envelope as phase3_envelope
        env = phase3_envelope.validate(dict(envelope))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "the job's sealed Phase-3 envelope is unavailable for Pre regeneration: "
            + str(exc)
        ) from exc

    from . import prelearning_foundation_policy, generation_quality_policy
    from . import generation_repair_policy as repair, model_provider
    from . import release_workbook_edits
    from .phase3 import pre_coverage

    # Continue is an explicit new Pre revision. A prior empty-bank success is
    # recoverable here; no passive read upgrades sealed history or Post.
    pre_payload = copy.deepcopy(pre_payload)
    source_rows = list(pre_payload.get("records") or [])
    reviewed_input = copy.deepcopy(dict(pre_payload.get("_reviewed_pre_input") or {}))
    changed_ids = set(reviewed_input.get("changed_pre_concept_ids") or [])
    if not repair.active(pre_payload):
        changed_ids.update(reviewed_input.get("added_pre_concept_ids") or [])
        changed_ids.update(
            str(row.get("_pre_concept_id") or "")
            for row in source_rows if isinstance(row, Mapping)
            and row.get(release.MANUAL_EDIT_TRAIL_FIELD)
        )
        release_workbook_edits.prepare_reviewed_pre_scope(pre_payload, changed_ids)
    else:
        # Scope was already invalidated on upload; record acceptance without
        # archiving it again or erasing evidence belonging to unchanged rows.
        release_workbook_edits.prepare_reviewed_pre_scope(pre_payload, set())
    reviewed_input.update({
        "generation_repair_policy": repair.VERSION,
        "accepted_pre_concept_ids": [
            str(row.get("_pre_concept_id") or "") for row in source_rows if isinstance(row, Mapping)
        ],
    })
    pre_payload["_reviewed_pre_input"] = reviewed_input
    revised_profile = model_provider.new_profile()
    pre_payload[model_provider.PROFILE_KEY] = revised_profile

    reviewed_generation: dict[str, Any] = {}
    reviewed_policies = {
        **prelearning_foundation_policy.fields({"metadata": pre_payload}),
        **generation_quality_policy.fields(pre_payload),
        repair.KEY: repair.VERSION,
        model_provider.PROFILE_KEY: revised_profile,
        pre_coverage.RULE_FIELD: pre_coverage.owner_rule(),
    }
    if (
        isinstance(reviewed_input, Mapping)
        and reviewed_policies
    ):
        # An explicit corrected workbook is a new Pre authority, including
        # when its original run predates the foundational-readiness policy.
        # Derive a separate sealed decision input; never overwrite the source
        # envelope or change already paid Post decisions/model policies.
        source_seal = str(env.get("envelope_sha256") or "")
        env["metadata"] = copy.deepcopy(dict(env.get("metadata") or {}))
        env["metadata"].update(reviewed_policies)
        env["metadata"]["_reviewed_pre_input"] = {
            **copy.deepcopy(dict(reviewed_input)),
            "release_uid": current_uid,
            "source_envelope_sha256": source_seal,
        }
        env["envelope_sha256"] = phase3_envelope.seal_sha256(env)
        env = phase3_envelope.validate(env)
        reviewed_generation = {
            "reviewed_release_uid": current_uid,
            "source_envelope_sha256": source_seal,
            "generation_envelope_sha256": env["envelope_sha256"],
            repair.KEY: repair.VERSION,
            model_provider.PROFILE_KEY: copy.deepcopy(revised_profile),
        }

    pre_map = {
        **prelearning_foundation_policy.fields({"metadata": pre_payload}),
        **generation_quality_policy.fields(pre_payload),
        repair.KEY: repair.VERSION,
        model_provider.PROFILE_KEY: copy.deepcopy(revised_profile),
        "rows": [
            copy.deepcopy(dict(row))
            for row in pre_payload.get("records") or []
            if isinstance(row, Mapping)
        ],
        "topics": copy.deepcopy(pre_payload.get("pre_topics") or []),
        "needed_for": copy.deepcopy(pre_payload.get("needed_for") or {}),
        "analysis": copy.deepcopy(pre_payload.get("analysis") or {}),
        "refused": str(pre_payload.get("refused") or ""),
        "pre_lane_verdict": copy.deepcopy(
            pre_payload.get(release.PRE_LANE_VERDICT_FIELD) or {}
        ),
    }
    if isinstance(reviewed_input, Mapping):
        pre_map["_reviewed_pre_input"] = copy.deepcopy(dict(reviewed_input))
    if reviewed_generation:
        pre_map["_reviewed_pre_generation"] = reviewed_generation
    if isinstance(pre_payload.get("_reviewed_pre_superseded"), Mapping):
        pre_map["_reviewed_pre_superseded"] = copy.deepcopy(
            pre_payload["_reviewed_pre_superseded"]
        )
    from .phase3 import prequestions

    decision_store = kernel.DecisionStore(artifact_dir / "phase3-decisions")
    progress.log(
        "Pre Concept corrections changed the prerequisite base; regenerating "
        "the Pre question bank from the reviewed concepts."
        if corrected_changed else
        "The accepted Pre question bank is incomplete; recovering missing "
        "questions while retaining completed concept questions."
    )
    generation_map = pre_map
    preserved_questions: dict[str, list[dict[str, Any]]] = {}
    if not corrected_changed:
        # An unchanged accepted Q48 bank may have completed some authors before
        # another failed. Preserve those exact records and recover only missing
        # identities; retry does not reauthor already successful questions.
        missing_ids = set(_reviewed_pre_missing_question_ids(pre_payload))
        for row in pre_map["rows"]:
            cid = str(row.get("_pre_concept_id") or "")
            if cid in missing_ids:
                continue
            question_ids = set(row.get("_aegis_pre_generated_questions") or [])
            preserved_questions[cid] = [
                copy.deepcopy(dict(question)) for question in pre_payload.get("generated_questions") or []
                if isinstance(question, Mapping) and (
                    str(question.get("pre_concept_id") or "") == cid
                    or str(question.get("pre_question_id") or "") in question_ids
                )
            ]
        generation_map = {**pre_map, "rows": [
            row for row in pre_map["rows"] if str(row.get("_pre_concept_id") or "") in missing_ids
        ]}
    with model_provider.bind_profile(revised_profile):
        regenerated = prequestions.build(env, generation_map, store=decision_store)
    if preserved_questions:
        regenerated = copy.deepcopy(dict(regenerated))
        regenerated["questions"] = {**preserved_questions, **dict(regenerated.get("questions") or {})}
        regenerated["plans"] = {
            **{key: copy.deepcopy(value) for key, value in (pre_payload.get("generated_question_plans") or {}).items()
               if str(key) in preserved_questions},
            **dict(regenerated.get("plans") or {}),
        }
    questions_by_id = regenerated.get("questions") or {}
    missing = [
        str(row.get("_pre_concept_id") or "") for row in pre_map["rows"]
        if not questions_by_id.get(str(row.get("_pre_concept_id") or ""))
    ]
    if missing:
        # Preserve paid/partial output for inspection, but never call an empty
        # or incomplete accepted bank a successful regeneration. Repeating
        # Continue reuses this same derived decision identity.
        marker = copy.deepcopy(release.concept_review_state(job))
        marker.pop("pre_questions_regenerated_for_uid", None)
        marker["pre_questions_regeneration_failure"] = {
            "release_uid": current_uid,
            "generation": reviewed_generation,
            "missing_pre_concept_ids": missing,
            "questions": copy.deepcopy(dict(regenerated)),
        }
        marker["status"] = release.CONCEPT_REVIEW_MASTER_FAILED
        durable = copy.deepcopy(dict(job.question_inventory or {}))
        durable[release.CONCEPT_REVIEW_KEY] = marker
        job.question_inventory = durable
        db.commit()
        db.refresh(job)
        raise ValueError(
            "Pre question generation is incomplete for accepted concepts: "
            + ", ".join(missing)
            + ". The accepted Concept file and recovery details are retained; retry Continue."
        )
    post_payload = release.release_payload(job, lane=release.LANE_POST) or {}
    release.stage_pre_release(
        db,
        job,
        target_chapter_id=int(state.get("target_chapter_id") or 0),
        pre_map=pre_map,
        pre_questions=regenerated,
        inventory=copy.deepcopy(post_payload.get("question_task_inventory") or {}),
        reason=(
            "The reviewed Pre Concept base changed; its generated questions were regenerated before Master authoring."
            if corrected_changed else
            "Missing questions were recovered for the accepted Pre Concept base before Master authoring."
        ),
    )
    db.refresh(job)
    regenerated_payload = release.release_payload(job, lane=release.LANE_PRE) or {}
    regenerated_uid = str(
        regenerated_payload.get(release.STAGED_RELEASE_UID_FIELD) or ""
    )
    marker = copy.deepcopy(release.concept_review_state(job))
    marker.pop("pre_questions_regeneration_failure", None)
    marker["pre_questions_regenerated_for_uid"] = regenerated_uid
    marker["pre_questions_regenerated_at"] = datetime.now(timezone.utc).isoformat()
    marker["pre_questions_count"] = sum(
        len(rows)
        for rows in (regenerated.get("questions") or {}).values()
        if isinstance(rows, list)
    )
    durable = copy.deepcopy(dict(job.question_inventory or {}))
    durable[release.CONCEPT_REVIEW_KEY] = marker
    job.question_inventory = durable
    db.commit()
    db.refresh(job)
    return marker


def build_review_masters(
    db,
    job_id: int,
    *,
    owner_sub: str | None = None,
) -> dict[str, Any]:
    """Resume the same run and build Masters from the corrected Concept slots.

    The review marker is the lifecycle gate.  Post must be explicitly
    reviewed; Pre may be uploaded through its own control, but retaining the
    original staged Pre draft is valid when no Pre correction is needed.
    ``_build_master_siblings`` remains the one existing Master orchestration
    seam, preserving its storage reservation, lane isolation, failure ledger,
    and source snapshot freeze behavior.
    """
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts"
    )
    db.refresh(job)
    state = release.concept_review_state(job)
    if not state:
        raise ValueError(
            "this upload is not paused for Concept review; use the legacy "
            "release or Master workflow"
        )
    if (
        state.get("status") == release.CONCEPT_REVIEW_MASTER_READY
        and not _reviewed_pre_recovery_needed(job, state)
    ):
        return {
            "job_id": int(job_id),
            "concept_review": state,
            "review_required": False,
            "master_outputs": copy.deepcopy(state.get("master_outputs") or {}),
            "all_four_outputs_ready": True,
        }
    required = set(state.get("required_lanes") or [])
    reviewed = set(state.get("reviewed_lanes") or [])
    if not required.issubset(reviewed):
        # Clicking Build Master is also the explicit acceptance action for
        # unchanged downloaded files.  Record every available lane under the
        # review marker so an optional Pre upload is never compulsory and the
        # original Concept payload remains the exact Master input.
        state = release.accept_concept_review(db, job)
        required = set(state.get("required_lanes") or [])
        reviewed = set(state.get("reviewed_lanes") or [])
        if not required.issubset(reviewed):
            missing = sorted(required - reviewed)
            raise ValueError(
                "Concept review is incomplete; submit the corrected "
                + ", ".join(missing)
                + " Concept workbook before building Master files"
            )
    if state.get("status") == release.CONCEPT_REVIEW_MASTER_BUILDING:
        # A worker crash can leave the durable marker at ``master_building``
        # after the process-local lock has been released.  The API has already
        # checked that no active operation owns the lock, so this is a safe
        # resumable retry from the reviewed Concept slots.
        state = release.update_concept_review_state(
            db, job, status=release.CONCEPT_REVIEW_REVIEWED
        )

    release.update_concept_review_state(db, job, status=release.CONCEPT_REVIEW_MASTER_BUILDING)
    try:
        # Step 2 owns extraction from the reviewed files; previous inventories
        # and matching rules do not enter these independent decisions.
        from . import reviewed_file_input
        for reviewed_lane in (release.LANE_POST, release.LANE_PRE):
            from . import reviewed_file_workflow_policy as workflow
            if ((job.question_inventory or {}).get(reviewed_file_input.INPUTS)
                    or workflow.active(release.release_payload(job, lane=reviewed_lane))):
                with storage_capacity.reserve_master_capacity(job_id=job_id, lane=reviewed_lane):
                    reviewed_file_input.prepare(db, job, lane=reviewed_lane, owner_sub=owner_sub)

        # A corrected Pre Concept workbook changes the prerequisite evidence that
        # owns its generated question bank. Re-enter the existing Phase 03
        # prequestions path once per corrected Pre release UID before Master
        # authoring; Post's reviewed source bank is left untouched.
        if _reviewed_pre_recovery_needed(job, state) or (
            isinstance(state.get("corrected_inputs"), Mapping)
            and isinstance(
                (state.get("corrected_inputs") or {}).get(release.LANE_PRE),
                Mapping,
            )
            and bool(
                ((state.get("corrected_inputs") or {}).get(release.LANE_PRE) or {}).get(
                    "changed"
                )
            )
        ):
            _regenerate_pre_questions_after_review(db, job, owner_sub=owner_sub)
            job = uploads.get_job(
                db, job_id, owner_sub=owner_sub, module="build_concepts"
            )
            state = release.concept_review_state(job)
    except Exception as exc:
        release.update_concept_review_state(db, job, status=release.CONCEPT_REVIEW_MASTER_FAILED)
        progress.log("Step 2 could not prepare the reviewed files: " + str(exc), level="error")
        raise

    started = datetime.now(timezone.utc).isoformat()
    uploads.update_run_stage(
        db,
        job_id,
        "Building Master files from reviewed Concept files",
        progress_value=0.70,
        owner_sub=owner_sub,
    )
    db.refresh(job)
    release.update_concept_review_state(
        db, job, status=release.CONCEPT_REVIEW_MASTER_BUILDING,
        master_started_at=started,
    )
    try:
        builds = _build_master_siblings(
            db, job_id, int(state.get("target_chapter_id") or 0),
            owner_sub=owner_sub,
            progress_start=0.70,
            progress_end=0.99,
        )
    except Exception as exc:
        db.rollback()
        job = uploads.get_job(
            db, job_id, owner_sub=owner_sub, module="build_concepts"
        )
        release.update_concept_review_state(
            db, job, status=release.CONCEPT_REVIEW_MASTER_FAILED,
            master_completed_at=datetime.now(timezone.utc).isoformat(),
        )
        progress.log(
            f"Master authoring failed after Concept review: {exc}",
            level="error",
        )
        raise

    master_outputs = {
        lane: {
            "ready": builds.get(lane) is not None,
            **(builds.get(lane) or {}),
            **({} if builds.get(lane) is not None else {
                "reason": "Master lane unavailable; inspect release issues or retry"
            }),
        }
        for lane in (release.LANE_PRE, release.LANE_POST)
    }
    concept_ready = all(
        release.release_payload(job, lane=lane) is not None
        for lane in (release.LANE_PRE, release.LANE_POST)
    )
    all_ready = concept_ready and all(
        bool(master_outputs[lane].get("ready"))
        for lane in (release.LANE_PRE, release.LANE_POST)
    )
    completed = datetime.now(timezone.utc).isoformat()
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts"
    )
    review_status = (
        release.CONCEPT_REVIEW_MASTER_READY
        if all_ready else release.CONCEPT_REVIEW_MASTER_FAILED
    )
    state = release.update_concept_review_state(
        db, job, status=review_status, master_outputs=master_outputs,
        master_completed_at=completed,
    )
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts"
    )
    ready_count = sum(
        release.release_payload(job, lane=lane) is not None
        for lane in (release.LANE_PRE, release.LANE_POST)
    ) + sum(bool(master_outputs[lane].get("ready")) for lane in master_outputs)
    job.status = "generated" if all_ready else "concept_review"
    job.detail = (
        "All Concept and Master files are ready for explicit publication."
        if all_ready else
        f"Master authoring completed with {ready_count}/4 outputs ready; "
        "retry the unavailable Master lane(s)."
    )
    db.commit()
    progress.set_progress(
        1.0 if all_ready else 0.99,
        label=("All four outputs ready" if all_ready else "Master outputs incomplete"),
    )
    uploads.finish_run(
        db,
        job_id,
        progress_value=1.0 if all_ready else 0.99,
        stage=("All four outputs ready" if all_ready else "Master outputs incomplete"),
        status="completed",
        owner_sub=owner_sub,
    )
    return {
        "job_id": int(job_id),
        "concept_review": state,
        "review_required": False,
        "master_outputs": master_outputs,
        "all_four_outputs_ready": all_ready,
        "output_completion": {
            "ready_count": int(ready_count),
            "total_count": 4,
            "all_ready": all_ready,
            "missing": [
                {"lane": lane, "number": "02" if lane == release.LANE_PRE else "04"}
                for lane in (release.LANE_PRE, release.LANE_POST)
                if not master_outputs[lane].get("ready")
            ],
        },
    }


def _mark_run_incomplete(
    staged: dict[str, Any], exc: Exception,
) -> dict[str, Any]:
    """Make a failure-exit release read as INCOMPLETE, never as a clean run.

    "Finished work always ships": the wrapper stages whatever the run had
    already paid for instead of returning nothing. But the terminal result
    used to look identical to a completed run's, so a generation that died
    mid-way (before Phase 3 sealed the Pre authority) was mistaken for a
    finished chapter with a mysteriously missing Pre lane (owner report,
    2026-08-28). The marker rides the result for the console to render as
    an incomplete end-state, and the log says the same in words.
    """

    resume_allowed = getattr(exc, "resume_allowed", True) is not False
    recovery_action = str(
        getattr(exc, "recovery_action", "resume_checkpoint") or ""
    )
    recovery_message = str(getattr(exc, "recovery_message", "") or "")
    prefix = (
        "Generation did NOT complete: "
        f"{type(exc).__name__}: {exc}. The rows already produced were "
        "staged so nothing paid for is lost, but this chapter's outputs "
        "are incomplete. "
    )
    if resume_allowed:
        message = (
            prefix
            + "Resume from the saved checkpoint to finish the remaining "
            "outputs (the Pre-Learning lane included)."
        )
        resume_message = (
            "Re-run generation: it resumes from the saved checkpoint, "
            "replays finished work from the decision store, and completes "
            "the remaining outputs."
        )
        recovery_message = recovery_message or resume_message
    else:
        recovery_message = recovery_message or (
            "This saved checkpoint cannot complete by resuming. Start a new "
            "upload and conversion before generation."
        )
        message = prefix + recovery_message
    progress.log(message, level="error")
    marker = {
        "error": f"{type(exc).__name__}: {exc}",
        "message": message,
        "resume_allowed": resume_allowed,
        "recovery_action": recovery_action,
        "recovery": recovery_message,
    }
    if resume_allowed:
        # Compatibility for existing clients, deliberately absent from a
        # non-resumable result so no generic renderer can offer the dead-end
        # action by merely checking that this string exists.
        marker["resume"] = resume_message
    return {
        **staged,
        "run_incomplete": marker,
    }


def _stage_generation_release(
    original: Callable[..., object],
    db,
    job_id: int,
    target_chapter_id: int,
    *args,
    **kwargs,
) -> dict[str, Any]:
    owner_sub = kwargs.get("owner_sub")
    mode_token = _RELEASE_MODE.set(True)
    capture_token = _RELEASE_CAPTURE.set(None)
    proof_token = build_concepts._PRE_RELEASE_TERMINAL_PROOF.set(None)
    try:
        try:
            result = original(
                db,
                job_id,
                target_chapter_id,
                *args,
                **kwargs,
            )
        except Exception as exc:
            # A failure after the deposit boundary may occur after the final
            # rows were already captured. Releasing only the newest checkpoint
            # here would throw away the most complete candidate.
            captured = copy.deepcopy(_RELEASE_CAPTURE.get())
            db.rollback()
            job = uploads.get_job(
                db,
                job_id,
                owner_sub=owner_sub,
                module="build_concepts",
            )
            if captured:
                staged = release.stage_release(
                    db,
                    job,
                    target_chapter_id=target_chapter_id,
                    records=captured.get("records") or [],
                    inventory=captured.get("inventory") or {},
                    mined_types=captured.get("mined_types") or {},
                    final_grounding_certificate=(
                        captured.get("final_grounding_certificate") or {}
                    ),
                    checkpoint=(
                        captured.get("checkpoint")
                        or job.generation_checkpoint
                    ),
                    error=exc,
                    generation_policy=release.generation_quality_fields(
                        captured, chapter_id=target_chapter_id,
                    ),
                    reason=(
                        "Generation failed after its final rows were "
                        "materialized. Aegis released those captured rows "
                        "with the failure attached instead of falling back "
                        "to an older or empty checkpoint."
                    ),
                )
                _stage_pre_sibling(
                    db,
                    job,
                    target_chapter_id,
                    inventory=captured.get("inventory") or {},
                    phase3_pre_release=captured.get("phase3_pre_release"),
                    checkpoint_envelope=captured.get("checkpoint"),
                    reason=(
                        "Generation failed after its final rows were "
                        "materialized. The Phase 03 Pre-Learning outputs "
                        "this run had already recorded were staged beside "
                        "the released rows."
                    ),
                )
                return _mark_run_incomplete(staged, exc)
            staged = release.stage_release(
                db,
                job,
                target_chapter_id=target_chapter_id,
                error=exc,
                reason=(
                    "Generation failed after creating a durable checkpoint. "
                    "Aegis released the newest available rows with the "
                    "failure attached instead of returning no output."
                ),
            )
            _stage_pre_sibling(
                db,
                job,
                target_chapter_id,
                inventory=None,
                reason=(
                    "Generation failed after creating a durable checkpoint. "
                    "The Phase 03 Pre-Learning outputs this run had already "
                    "recorded were staged beside the released rows."
                ),
            )
            return _mark_run_incomplete(staged, exc)
        captured = copy.deepcopy(_RELEASE_CAPTURE.get())
        return _release_after_result(
            db,
            job_id,
            target_chapter_id,
            owner_sub=owner_sub,
            result=result,
            captured=captured,
        )
    finally:
        build_concepts._PRE_RELEASE_TERMINAL_PROOF.reset(proof_token)
        _RELEASE_CAPTURE.reset(capture_token)
        _RELEASE_MODE.reset(mode_token)


def generate_post_learning(
    db,
    job_id: int,
    target_chapter_id: int,
    *args,
    **kwargs,
) -> dict[str, Any]:
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=kwargs.get("owner_sub"),
        module="build_concepts",
    )
    db.refresh(job)
    generation_recovery.require_mutation_allowed(
        job, operation="generate from this checkpoint"
    )
    return _run_generation_release(
        build_concepts.generate_post_learning,
        db,
        job_id,
        target_chapter_id,
        *args,
        **kwargs,
    )


def _wrap_generation(original):
    """Test/helper adapter retaining a wrapper-shaped interface."""

    @wraps(original)
    def wrapped(db, job_id: int, target_chapter_id: int, *args, **kwargs):
        return _run_generation_release(
            original,
            db,
            job_id,
            target_chapter_id,
            *args,
            **kwargs,
        )

    return wrapped


def _install_manifest_extension() -> None:
    current = getattr(models.UploadJob, "source_artifacts", None)
    if not isinstance(current, property):
        return
    original_getter = current.fget
    if original_getter is None or getattr(original_getter, "_aegis_release", False):
        return

    def source_artifacts(job: models.UploadJob) -> dict[str, Any]:
        base = original_getter(job)
        manifest = copy.deepcopy(base) if isinstance(base, dict) else {
            "available": False,
            "status": "unavailable",
            "files": [],
            "summary": {},
        }
        entries = release_files.release_artifact_entries(job)
        if not entries:
            return manifest
        files = [
            copy.deepcopy(row)
            for row in manifest.get("files") or []
            if isinstance(row, dict)
        ]
        existing = {str(row.get("kind") or "") for row in files}
        files.extend(
            copy.deepcopy(row)
            for row in entries
            if str(row.get("kind") or "") not in existing
        )
        payload = release.release_payload(job)
        if payload is None:
            # Entries without a release exist (the run-diagnostics
            # export). They ride the manifest, but neither a
            # ``release_output`` block nor ``available`` is touched:
            # "available" states that CANONICAL SOURCE artifacts exist
            # (test_source_artifact_flow pins that a replaced source
            # reads False), and the diagnostics export neither needs nor
            # implies them — its first-class download lives on the
            # checkpoint card, not behind this manifest's gate.
            manifest.update({"files": files})
            return manifest
        summary = copy.deepcopy(manifest.get("summary") or {})
        release_summary = copy.deepcopy(payload.get("summary") or {})
        summary["release_rows"] = int(release_summary.get("row_count") or 0)
        summary["release_issues"] = int(release_summary.get("issue_count") or 0)
        manifest.update({
            "available": True,
            "files": files,
            "summary": summary,
            "release_output": {
                "available": True,
                "status": (
                    "uploaded"
                    if release_summary.get("database_uploaded")
                    else "released"
                ),
                **release_summary,
            },
        })
        return manifest

    source_artifacts._aegis_release = True
    models.UploadJob.source_artifacts = property(source_artifacts)


def _install_deposit_interceptor() -> None:
    current = build_concepts._deposit_and_publish_concepts
    if getattr(current, "_aegis_release_interceptor", False):
        return
    original = current

    @wraps(original)
    def deposit_and_publish(*args, **kwargs):
        if not _RELEASE_MODE.get():
            return original(*args, **kwargs)
        return _capture_deposit(original, args, kwargs)

    deposit_and_publish._aegis_release_interceptor = True
    deposit_and_publish._aegis_release_original = original
    build_concepts._deposit_and_publish_concepts = deposit_and_publish


def install() -> None:
    """Install context-aware release capture and artifact projection.

    Only the user-facing API wrappers set ``_RELEASE_MODE``. Every other caller
    traverses the original deposit and generation contracts unchanged.
    """

    if getattr(models.UploadJob, "_RELEASE_STAGING_CONTRACT_VERSION", 0) >= (
        _CONTRACT_VERSION
    ):
        return
    _install_deposit_interceptor()
    _install_manifest_extension()
    models.UploadJob._RELEASE_STAGING_CONTRACT_VERSION = _CONTRACT_VERSION
