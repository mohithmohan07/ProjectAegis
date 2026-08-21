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
from . import concept_example_ownership
from . import progress
from . import build_concepts_release_files as release_files
from . import release_refiner
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
    _RELEASE_CAPTURE.set({
        "records": records,
        "inventory": inventory,
        "mined_types": mined_types,
        "final_grounding_certificate": final_certificate,
        "checkpoint": checkpoint,
        "target_chapter_id": int(values.get("chapter_id") or 0),
        "pre_post": str(values.get("pre_post") or ""),
        "source_book": str(values.get("source_book") or ""),
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
    reason: str,
) -> None:
    """Stage Outputs 03/04 beside whatever the Post lane just released.

    Called after EVERY ``stage_release`` on this path, not only after a
    clean capture. That is the point: this function's inputs are the
    Phase 03 snapshots already on disk (``phase3.runner`` writes them as
    phase 3 finishes), not the captured rows — so a run that completed
    Phase 03 and then failed after the deposit boundary, or reached an
    unresolved semantic boundary and released a checkpoint instead, HAS a
    Pre map available and its Outputs 03/04 should ship beside the Post
    release rather than vanish. Staged from only one of the four exits,
    their absence on the other three is indistinguishable from a chapter
    with no Pre lane at all — the same R4 confusion ``_run_snapshot``'s
    three states exist to prevent, one level up.

    Never raises, and always runs AFTER the Post release is staged:
    ``stage_pre_release_from_run`` logs and returns ``None`` on any
    failure, so a Pre-lane problem can never cost the finished Post rows.
    """

    db.refresh(job)
    release.stage_pre_release_from_run(
        db,
        job,
        target_chapter_id=target_chapter_id,
        inventory=inventory or {},
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
        # Q13/R4: public Examples whose wording has no exact inventory
        # owner get one recorded, reviewer-visible verdict on the staged
        # release. The recorder never raises; the staged rows above are
        # already durable whatever happens here.
        chapter = db.get(models.Chapter, int(target_chapter_id or 0))
        concept_example_ownership.adjudicate_and_record(
            db,
            job,
            lane=release.LANE_POST,
            records=refined_records,
            inventory=captured.get("inventory") or {},
            meta={
                "board": chapter.board if chapter else "",
                "grade": chapter.grade if chapter else "",
                "subject": chapter.subject if chapter else "",
                "chapter_title": chapter.chapter_title if chapter else "",
                "pre_post_learning": "Post",
            },
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


def _lane_has_staged_concept_release(
    db,
    job_id: int,
    lane: str,
    *,
    owner_sub: str | None = None,
) -> bool:
    """Is there a staged CONCEPT release in this lane's slot to build from?

    A mechanical precondition, not a judgment about content: the
    assessment lane reads the staged concept payload for ITS lane, and a
    lane whose slot is empty raises ``ReleaseRunError`` before the runner
    does anything at all.

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
        return release.release_payload(job, lane=lane) is not None
    except Exception:
        return True


def rebuild_lane_master(
    db,
    job_id: int,
    lane: str,
    *,
    owner_sub: str | None = None,
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
    try:
        return runner(db, job_id, owner_sub=owner_sub)
    except Exception as exc:
        try:
            # Serialized across the concurrent lanes: the recorder is a
            # read-modify-write of the shared job issue ledger, and the
            # rollback + fresh read happen INSIDE the lock so each lane
            # records onto the other's committed state, never over it.
            with _LANE_ISSUE_LOCK:
                db.rollback()
                job = uploads.get_job(
                    db,
                    job_id,
                    owner_sub=owner_sub,
                    module="build_concepts",
                )
                release.record_assessment_lane_unavailable(
                    db, job, lane=lane, error=exc)
        except Exception:
            # The recorder is already defensive; this is the second
            # ring, so that a database that cannot even be read back
            # still cannot mask the original failure.
            pass
        raise


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

    built: dict[str, dict[str, Any] | None] = {}
    lanes: list[str] = []
    for lane in (release.LANE_PRE, release.LANE_POST):
        if not _lane_has_staged_concept_release(
            db, job_id, lane, owner_sub=owner_sub,
        ):
            # No slot, so nothing to build a Master from and nowhere
            # to record a failure onto. Skipping is the honest
            # answer: the alternative is a doomed run whose reason is
            # thrown away. The lane's Master entry is still PRESENT
            # and disabled with the "not built for this run" reason,
            # so nothing about it is silent.
            built[lane] = None
            continue
        lanes.append(lane)
    if not lanes:
        return built

    progress.set_progress(
        0.965,
        label=(
            "Building both Master files (Outputs 02 and 04) in parallel…"
            if len(lanes) == 2
            else (
                "Building the Pre-Learning Master (Output 02)…"
                if lanes[0] == release.LANE_PRE
                else "Building the Post Master (Output 04)…"
            )
        ),
    )

    def _build_lane(lane: str) -> dict[str, Any] | None:
        from ..db import SessionLocal

        lane_db = SessionLocal()
        try:
            result = {"release_id": rebuild_lane_master(
                lane_db, job_id, lane, owner_sub=owner_sub).id}
            lane_db.commit()
            return result
        except Exception:  # noqa: BLE001 — see the docstring
            # ``rebuild_lane_master`` already recorded the failure as the
            # lane's ``assessment_lane_unavailable`` issue (and committed
            # it on this same session); here the re-raise is swallowed so
            # one Master-lane fault cannot cost the finished concept
            # outputs or the sibling Master.
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
            "Master · Output 02 (Pre)" if lane == release.LANE_PRE
            else "Master · Output 04 (Post)"
            for lane in lanes
        ],
        announce="Master files",
    )
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
    """Stage the two concept lanes, then build the two Master lanes.

    Three lines, and every one of them is load-bearing:

    * ``_stage_generation_release`` is today's whole body, moved verbatim.
      All four exits — clean/captured, clean/checkpoint, raised/captured,
      raised/checkpoint — and all four ``stage_release`` /
      ``_stage_pre_sibling`` pairs stay exactly where they were.
    * ``_build_master_siblings`` is called ONCE, on the single tail all
      four exits converge on. Not four times beside ``_stage_pre_sibling``,
      for three checkable reasons: (a) all four exits reach it, INCLUDING
      the two failure exits, which are exactly where "one run, four
      outputs" would otherwise silently degrade to two on the runs that
      most need the evidence — ``_stage_pre_sibling``'s own docstring
      makes this argument for the Pre lane and it applies unchanged one
      lane further; (b) it is OUTSIDE the ``_RELEASE_MODE`` /
      ``_RELEASE_CAPTURE`` context vars, whose ``finally`` has already run
      by the time control reaches here, so the deposit interceptor
      ``install()`` wires cannot see the assessment lane; (c) one site, so
      a fifth exit added later inherits it rather than being forgotten.
    * the staged result is returned unchanged — the assessment lane is a
      sibling of the concept release, never a gate on it.
    """

    staged = _stage_generation_release(
        original, db, job_id, target_chapter_id, *args, **kwargs)
    # HONEST PROGRESS (owner report, 2026-08-21: "after 100% it is still
    # running" — and paying). The deposit's own "Done" fires when the
    # CONCEPT outputs land, but the two Master lanes each run a full
    # per-question decision pipeline AFTER it — comparable model spend to
    # the concept run itself. The bar therefore steps back below 100%
    # with a label naming what is still being paid for, and only the true
    # end of all four outputs says Done.
    progress.set_progress(
        0.96,
        label=(
            "Outputs 01/03 staged — building the Master files "
            "(Outputs 02/04; this stage makes model calls)…"
        ),
    )
    _build_master_siblings(
        db, job_id, target_chapter_id, owner_sub=kwargs.get("owner_sub"))
    progress.set_progress(1.0, label="Done — all four outputs ready")
    return staged


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
                    reason=(
                        "Generation failed after its final rows were "
                        "materialized. The Phase 03 Pre-Learning outputs "
                        "this run had already recorded were staged beside "
                        "the released rows."
                    ),
                )
                return staged
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
            return staged
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
