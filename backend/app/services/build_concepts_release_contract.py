"""Unattended release staging for user-facing Build Concepts generation.

The low-level generation services retain their original contracts for internal
callers, tests, recovery tools and deliberately programmatic workflows. The
Build Concepts upload API calls the wrappers in this module, which stage a
release instead of publishing directly or surfacing a semantic choice.
"""
from __future__ import annotations

import copy
import inspect
import threading
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Mapping

from .. import models
from . import build_concepts, uploads
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
            "board": chapter.board if chapter else "",
            "grade": chapter.grade if chapter else "",
            "subject": chapter.subject if chapter else "",
            "unit": chapter.unit if chapter else "",
            "chapter_title": chapter.chapter_title if chapter else "",
            "chapter_code": chapter.chapter_code if chapter else "",
            "pre_post": "Pre" if job.learning_kind == "pre" else "Post",
            "source_book": job.source_book or job.filename or "",
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
            checkpoint=captured.get("checkpoint") or job.generation_checkpoint,
            pending_decision=pending,
            reason=(
                "Generation completed. The output was staged and was not "
                "uploaded to the database."
            ),
            refinements=refinements,
        )
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
        # Pre is a sibling projection of the same Concept run and has no
        # independent generation lifecycle. A non-terminal Post authority
        # therefore blocks both Master lanes even if an older/partial Pre slot
        # happens to look complete in isolation.
        post_payload = release.release_payload(job, lane=release.LANE_POST)
        if (
            post_payload is not None
            and not terminal_release.payload_terminal_generation_complete(
                post_payload
            )
        ):
            return False, (
                "the Concept run comes from a non-terminal generation "
                "checkpoint"
            )
        payload = release.release_payload(job, lane=lane)
        if payload is None:
            return False, "no staged Concept release"
        if not terminal_release.payload_terminal_generation_complete(payload):
            return False, (
                "the staged Concept release comes from a non-terminal "
                "generation checkpoint"
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
                value=0.955,
            )
            # The Master builds own 0.955 → 0.995 of the bar and fill it
            # as their stages (and the long fan-outs' units) finish, so
            # the console no longer freezes on one value for the entire
            # build — the "97% for hours" report. One shared span, one
            # equal-weight tracker per lane; emission is monotone, so the
            # two concurrent lanes cannot walk the bar backward.
            span = progress.Span(
                0.955, 0.995, label="Building Master files (Outputs 02/04)"
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

    staged = _stage_generation_release(
        original, db, job_id, target_chapter_id, *args, **kwargs)
    # HONEST PROGRESS (owner report, 2026-08-21: "after 100% it is still
    # running" — and paying). ``_build_master_siblings`` opens the real
    # stage boundary after it confirms at least one lane exists; only the
    # true end says Done, with the observed lane outcomes rather than a promise.
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
    if all_four_ready:
        done_label = "Done — all four outputs ready"
    else:
        missing = ", ".join(
            "Pre" if lane == release.LANE_PRE else "Post"
            for lane in (release.LANE_PRE, release.LANE_POST)
            if not master_outputs[lane]["ready"]
        )
        ready_count = sum(
            int(bool(master_outputs[lane]["ready"]))
            for lane in (release.LANE_PRE, release.LANE_POST)
        )
        done_label = (
            "Done — Concept stage complete; Master files ready "
            f"{ready_count}/2 (unavailable: {missing})"
        )
    progress.set_progress(1.0, label=done_label)
    result = dict(staged)
    result["master_outputs"] = master_outputs
    result["all_four_outputs_ready"] = all_four_ready
    return result


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

    message = (
        "Generation did NOT complete: "
        f"{type(exc).__name__}: {exc}. The rows already produced were "
        "staged so nothing paid for is lost, but this chapter's outputs "
        "are incomplete — resume from the saved checkpoint to finish the "
        "remaining outputs (the Pre-Learning lane included)."
    )
    progress.log(message, level="error")
    return {
        **staged,
        "run_incomplete": {
            "error": f"{type(exc).__name__}: {exc}",
            "message": message,
            "resume": (
                "Re-run generation: it resumes from the saved checkpoint, "
                "replays finished work from the decision store, and "
                "completes the remaining outputs."
            ),
        },
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
        payload = release.release_payload(job) or {}
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
