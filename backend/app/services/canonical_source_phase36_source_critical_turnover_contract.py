"""Phase 3.6 automatic source-critical turnover to verified GPT PDF ACSD.

A live Electricity acceptance run exposed a gap between two otherwise strict
source lanes.  Phase 2 correctly blocked generation when Mathpix-derived ACSD
contained duplicate task identities, malformed public KaTeX, or unresolved
Figure references.  Phase 2.2, however, only adjudicated three bounded heading /
local-ownership issue codes, while the full GPT PDF-to-ACSD fallback was invoked
only after a broad conversion-quality failure.  A PDF with a small number of
serious source defects could therefore be too broken to generate but not broken
enough to enter the repair lane.

This contract closes that gap in two places:

* conversion-time: any non-Phase-2.2-adjudicable source-critical issue promotes
  the PDF to the existing full, independently verified GPT PDF-to-ACSD fallback;
* generation/resume-time: if bounded Phase 2.2 adjudication still leaves any
  source-critical issue, Aegis rebuilds the canonical source from the original
  PDF, clears only source-dependent generation state, validates the replacement,
  and continues the same run without requiring re-upload or manual restart.

The replacement remains evidence-bound.  GPT may reconstruct content visible on
the original pages and repair structure/ownership, but the existing deterministic
Phase 2 gate and independent page verifier must approve the complete replacement
before generation continues.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase221_fallback as fallback
from . import progress

_CONTRACT_VERSION = 1
_TURNOVER_VERSION = "phase3.6-source-critical-turnover-1"
_TURNOVER_FILENAME = "source.phase36-source-critical-turnover.json"
_ENABLED_ENV = "AEGIS_SOURCE_CRITICAL_TURNOVER_ENABLED"


PrepareProvider = Callable[[Any, Any], dict[str, Any]]
QualityProvider = Callable[..., dict[str, Any]]


def _enabled() -> bool:
    return os.environ.get(_ENABLED_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _issue_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the Phase 2 blocking issue ledger from one source report."""
    report = report if isinstance(report, dict) else {}
    rows = report.get("phase2_issues")
    if not isinstance(rows, list):
        rows = []
    return [copy.deepcopy(row) for row in rows if isinstance(row, dict)]


def _issue_codes(issues: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(row.get("code") or "unknown_source_issue").strip()
            or "unknown_source_issue"
            for row in issues
            if isinstance(row, dict)
        }
    )


def _turnover_reason(issues: list[dict[str, Any]]) -> str:
    codes = _issue_codes(issues)
    return "phase36_source_critical_turnover:" + ",".join(codes)


def _non_adjudicable_issues(
    report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Issues that bounded Phase 2.2 cannot currently repair by packet."""
    return [
        row
        for row in _issue_rows(report)
        if str(row.get("code") or "") not in phase22.ELIGIBLE_ISSUE_CODES
    ]


def _assess_quality_with_source_turnover(
    original: QualityProvider,
    mmd_text: str,
    source_path: Path,
    *,
    report: dict[str, Any] | None = None,
    hard_failure: str = "",
) -> dict[str, Any]:
    """Promote any non-adjudicable PDF source defect to full reconstruction."""
    result = original(
        mmd_text,
        source_path,
        report=report,
        hard_failure=hard_failure,
    )
    result = copy.deepcopy(result if isinstance(result, dict) else {})
    path = Path(source_path)
    if (
        not _enabled()
        or not fallback._enabled()
        or path.suffix.lower() != ".pdf"
        or not isinstance(report, dict)
    ):
        return result

    unresolved = _non_adjudicable_issues(report)
    if not unresolved:
        return result

    reason = _turnover_reason(unresolved)
    reasons = [str(value) for value in result.get("reasons") or [] if str(value)]
    if reason not in reasons:
        reasons.append(reason)
    result.update(
        {
            "eligible": True,
            "use_fallback": True,
            "reasons": reasons,
            "source_critical_turnover": True,
            "source_critical_issue_codes": _issue_codes(unresolved),
        }
    )
    return result


def _load_source_state(job: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    canonical, report = phase2._load_or_refresh_for_job(job)
    canonical = canonical if isinstance(canonical, dict) else {}
    report = report if isinstance(report, dict) else {}
    return canonical, report, phase2.phase2_inventory_issues(canonical, report)


def _source_sha256(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _turnover_marker_path(directory: Path) -> Path:
    return Path(directory) / _TURNOVER_FILENAME


def _read_turnover_marker(directory: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            _turnover_marker_path(directory).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_turnover_marker(directory: Path, value: dict[str, Any]) -> None:
    canonical_source._atomic_write(
        _turnover_marker_path(directory),
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _purge_source_dependent_semantic_caches(directory: Path) -> list[str]:
    """Remove only artifacts whose meaning depends on the replaced ACSD graph.

    The independent original-PDF page evidence file is intentionally retained;
    it is keyed to the unchanged PDF and can be safely reused.  Source graph,
    topology, grounding, hierarchy, and Type-host caches are not reusable after
    canonical source replacement even when their own keying would reject them.
    Removing them also keeps the artifact directory truthful for operators.
    """
    directory = Path(directory)
    patterns = (
        "source.semantic-graph.json",
        "source.semantic-graph-report.json",
        "source.semantic.mmd",
        "source.phase31-*.json",
        "source.phase32-*.json",
        "source.phase33-*.json",
        "source.phase34-*.json",
        "source.phase35-*.json",
    )
    removed: list[str] = []
    for pattern in patterns:
        for path in directory.glob(pattern):
            if not path.is_file():
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed.append(path.name)
    return sorted(set(removed))


def _can_turn_over(job: Any) -> tuple[bool, str]:
    if not _enabled():
        return False, "automatic source-critical turnover is disabled"
    if not fallback._enabled():
        return False, "GPT PDF-to-ACSD fallback is disabled"
    try:
        from .. import config
        from . import uploads

        if not config.use_live_generation():
            return False, "OPENAI_API_KEY/live generation is unavailable"
        path = uploads.upload_file_path(job)
    except Exception as exc:
        return False, f"the original upload path is unavailable: {exc}"
    if not path.exists():
        return False, "the original uploaded file is missing"
    if path.suffix.lower() != ".pdf":
        return False, "automatic full source turnover requires the original PDF"
    return True, ""


def _replace_job_source_from_pdf(
    db: Any,
    job: Any,
    *,
    issues: list[dict[str, Any]],
    original_error: Exception,
) -> dict[str, Any]:
    """Run the full verified fallback and atomically adopt its source bundle."""
    from . import uploads

    allowed, reason = _can_turn_over(job)
    if not allowed:
        raise ValueError(
            f"{original_error} Automatic GPT source turnover was unavailable: "
            f"{reason}."
        ) from original_error

    path = uploads.upload_file_path(job)
    artifact_dir = uploads.source_artifact_directory(int(job.id))
    issue_codes = _issue_codes(issues)
    previous_mmd = str(job.mmd_text or "")
    previous_checkpoint = copy.deepcopy(job.generation_checkpoint or {})
    previous_inventory = copy.deepcopy(job.question_inventory or {})
    previous_status = str(job.status or "converted")
    previous_detail = str(job.detail or "")

    progress.step("Canonical source — full GPT turnover from original PDF")
    progress.log(
        "Phase 2 still has "
        f"{len(issues)} source-critical issue(s) after bounded adjudication "
        f"({', '.join(issue_codes)}). Promoting the original PDF to the full "
        "verified GPT PDF-to-ACSD reconstruction lane instead of stopping.",
        level="warning",
    )
    bundle = fallback.reconstruct_pdf_to_acsd(
        path,
        job_id=int(job.id),
        artifact_dir=artifact_dir,
        fallback_reason=[_turnover_reason(issues)],
        mathpix_mmd=previous_mmd,
    )
    replacement_mmd = str((bundle or {}).get("mmd_text") or "")
    canonical = (bundle or {}).get("canonical")
    reconstruction = (bundle or {}).get("reconstruction") or {}
    if not replacement_mmd or not isinstance(canonical, dict):
        raise RuntimeError(
            "verified GPT PDF-to-ACSD turnover returned no canonical replacement"
        )

    removed_caches = _purge_source_dependent_semantic_caches(artifact_dir)
    job.mmd_text = replacement_mmd
    job.question_inventory = {}
    job.generation_checkpoint = {}
    job.status = "converted"
    job.detail = (
        "Automatically replaced the defective Mathpix canonical source with a "
        "verified GPT PDF-to-ACSD reconstruction; generation continues from the "
        "new source contract."
    )
    try:
        db.commit()
        db.refresh(job)
    except Exception:
        db.rollback()
        # Keep the in-memory object coherent for callers/tests. The fallback's
        # artifact publication is itself rollback-safe and will be revalidated on
        # the next run if the database commit failed.
        job.mmd_text = previous_mmd
        job.question_inventory = previous_inventory
        job.generation_checkpoint = previous_checkpoint
        job.status = previous_status
        job.detail = previous_detail
        raise

    marker = {
        "version": _TURNOVER_VERSION,
        "status": "verified",
        "created_at": time.time(),
        "job_id": int(job.id),
        "source_filename": str(job.filename or path.name),
        "original_pdf_sha256": str(reconstruction.get("pdf_sha256") or ""),
        "previous_mmd_sha256": _source_sha256(previous_mmd),
        "replacement_mmd_sha256": _source_sha256(replacement_mmd),
        "issue_codes": issue_codes,
        "issue_count": len(issues),
        "cleared_checkpoint_stage": str(previous_checkpoint.get("stage") or ""),
        "cleared_question_inventory_items": len(
            (previous_inventory or {}).get("items") or []
        ),
        "removed_semantic_cache_files": removed_caches,
        "source_origin": str(
            reconstruction.get("source_origin") or fallback.FALLBACK_ORIGIN
        ),
        "page_count": int(reconstruction.get("page_count") or 0),
        "asset_count": int(reconstruction.get("asset_count") or 0),
    }
    try:
        _write_turnover_marker(artifact_dir, marker)
    except Exception as exc:  # source replacement remains valid without metadata
        progress.log(
            f"Source turnover succeeded, but its audit marker could not be "
            f"written ({exc}).",
            level="warning",
        )

    progress.log(
        "Verified full-PDF source turnover complete: "
        f"{marker['page_count']} page(s), {marker['asset_count']} source visual(s), "
        f"{len(removed_caches)} stale semantic cache file(s) removed. The current "
        "generation run will continue from the rebuilt source contract.",
        level="success",
    )
    return canonical


def _prepare_job_context_with_turnover(
    original: PrepareProvider,
    db: Any,
    job: Any,
) -> dict[str, Any]:
    """Run normal bounded adjudication first, then full PDF turnover if needed."""
    try:
        return original(db, job)
    except ValueError as source_error:
        try:
            _canonical, _report, issues = _load_source_state(job)
        except Exception:
            # The original diagnostic is more trustworthy when even the saved
            # source artifacts cannot be reloaded.
            raise source_error
        if not issues:
            raise source_error

        try:
            _replace_job_source_from_pdf(
                db,
                job,
                issues=issues,
                original_error=source_error,
            )
        except Exception as turnover_error:
            raise ValueError(
                "Automatic GPT PDF source turnover failed after the canonical "
                f"source gate found {len(issues)} issue(s): {turnover_error}"
            ) from turnover_error

        # Re-enter the ordinary source contract. The full fallback itself already
        # passed the deterministic gate, but this second call ensures every active
        # wrapper/overlay marker is refreshed before Phase 3 starts.
        canonical = original(db, job)
        progress.log(
            "The rebuilt canonical source passed Phase 2/2.2 validation; "
            "continuing this same generation attempt.",
            level="success",
        )
        return canonical


def install() -> None:
    if getattr(phase2, "_PHASE36_SOURCE_TURNOVER_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_quality = fallback.assess_mathpix_quality
    original_prepare = phase2.prepare_job_context
    original_manifest = phase2.artifact_manifest
    fallback._PHASE36_ORIGINAL_ASSESS_MATHPIX_QUALITY = original_quality
    phase2._PHASE36_ORIGINAL_PREPARE_JOB_CONTEXT = original_prepare
    phase2._PHASE36_ORIGINAL_ARTIFACT_MANIFEST = original_manifest

    @wraps(original_quality)
    def assess_mathpix_quality(
        mmd_text: str,
        source_path: Path,
        *,
        report: dict[str, Any] | None = None,
        hard_failure: str = "",
    ) -> dict[str, Any]:
        return _assess_quality_with_source_turnover(
            fallback._PHASE36_ORIGINAL_ASSESS_MATHPIX_QUALITY,
            mmd_text,
            source_path,
            report=report,
            hard_failure=hard_failure,
        )

    @wraps(original_prepare)
    def prepare_job_context(db: Any, job: Any) -> dict[str, Any]:
        return _prepare_job_context_with_turnover(
            phase2._PHASE36_ORIGINAL_PREPARE_JOB_CONTEXT,
            db,
            job,
        )

    @wraps(original_manifest)
    def artifact_manifest(directory: Path) -> dict[str, Any]:
        manifest = phase2._PHASE36_ORIGINAL_ARTIFACT_MANIFEST(directory)
        marker = _read_turnover_marker(Path(directory))
        if marker:
            manifest["source_critical_turnover"] = marker
        return manifest

    fallback.assess_mathpix_quality = assess_mathpix_quality
    phase2.prepare_job_context = prepare_job_context
    phase2.artifact_manifest = artifact_manifest
    phase2._PHASE36_SOURCE_TURNOVER_VERSION = _CONTRACT_VERSION
