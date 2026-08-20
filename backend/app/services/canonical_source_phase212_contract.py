"""Phase 2.1.2 contract — semantic task-membership adjudication at compile.

Installs between Phase 2.1.1 and Phase 2.2, so its compile wrapper runs
INSIDE Phase 2.2's and OUTSIDE Phase 2.1's: it receives the hardened
candidate ledger and returns the adjudicated one (docs/spec-qx.md).

The polarity, in one sentence: on the active text lane the parser's tasks
are candidates until the QX author has ruled, and a bundle nobody ruled is
never generation-ready — `task_membership_unadjudicated` joins the Phase 2
blocking issues and the existing pre-spend validation refuses before any
paid call. No silent parser fallback exists: either the ledger is
model-authored (live or replayed from the sealed cache), the lane already
has a model authority (the PDF page ledger / chapter outline), the compile
is a shadow compile, or the run is blocked with the named reason.
"""
from __future__ import annotations

import copy
from functools import wraps
from types import ModuleType
from typing import Any

from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase212 as phase212
from . import progress

_CONTRACT_VERSION = 1


def _gpt_page_ledger_reader(canonical: dict[str, Any]) -> bool:
    """The recorded MMD reader identifies the GPT PDF page ledger.

    The PDF lane's rendered MMD stamps its reader into the header
    (``<!-- source_reader: gpt-pdf-to-acsd-... -->``), and phase2's
    ``_reader_stamp`` records that header into
    ``source_contract.source_reader``. On that lane task membership is
    already a model verdict — every block's kind was model-decided and
    independently verified against the original page images — so QX must
    not re-judge (or re-bill) it, whether or not the chapter outline has
    been attached to the compiled canonical yet (the reconstruct path
    attaches it only AFTER the compile). Reading a recorded reader
    identity is mechanical provenance, not a content judgment.
    """
    from . import canonical_source_phase221_fallback as fallback

    contract = canonical.get("source_contract")
    if not isinstance(contract, dict):
        return False
    reader = str(contract.get("source_reader") or "")
    compiler_id = reader.split("+", 1)[0]
    return bool(compiler_id) and compiler_id.startswith(
        fallback.MMD_SOURCE_ORIGIN
    )


def adjudication_exempt(canonical: dict[str, Any]) -> bool:
    """A model membership authority already ruled this bundle (PDF lane)."""
    outline = canonical.get("chapter_outline")
    if isinstance(outline, dict) and bool(outline.get("version")):
        return True
    return _gpt_page_ledger_reader(canonical)


def membership_adjudicated(canonical: dict[str, Any]) -> bool:
    ledger = canonical.get(phase212.LEDGER_KEY)
    return (
        isinstance(ledger, dict)
        and str(ledger.get("membership_authority") or "") == "model_verdict"
        and str(ledger.get("version") or "") == phase212.QX_VERSION
    )


def qx_artifact_valid(canonical: Any, report: Any) -> bool:
    """Whether a persisted bundle may be reused without re-adjudication.

    The checkpoint re-key: a bundle authored under parser authority is
    stale on the active text lane and must recompile (live or from the
    sealed verdict cache) before generation reads it.
    """
    if not isinstance(canonical, dict):
        return False
    if not canonical.get("used_for_generation"):
        return True
    if adjudication_exempt(canonical):
        return True
    return membership_adjudicated(canonical)


def install(generation: ModuleType | None = None) -> None:
    """Activate Phase 2.1.2 after Phase 2.1/2.1.1 and before Phase 2.2."""
    if getattr(phase2, "_PHASE212_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_issue_reader = phase2.phase2_inventory_issues
    original_compile = phase2.compile_phase2_source
    original_load = phase2._load_or_refresh_for_job

    phase2._PHASE212_ORIGINAL_ISSUES = original_issue_reader
    phase2._PHASE212_ORIGINAL_COMPILE = original_compile
    phase2._PHASE212_ORIGINAL_LOAD = original_load

    @wraps(original_issue_reader)
    def phase2_inventory_issues(canonical, report=None):
        issues = list(original_issue_reader(canonical, report))
        if (
            isinstance(canonical, dict)
            and canonical.get("used_for_generation")
            and not adjudication_exempt(canonical)
            and not membership_adjudicated(canonical)
        ):
            membership = canonical.get("task_membership") or {}
            reason = str(
                membership.get("reason")
                or "task membership has not been adjudicated by the QX "
                "author; the deterministic candidates must not stand in"
            )
            issues.append({
                "severity": "error",
                "code": "task_membership_unadjudicated",
                "message": reason,
            })
        return issues

    @wraps(original_compile)
    def compile_phase2_source(*args, **kwargs):
        compiled = original_compile(*args, **kwargs)
        consumer_module = str(kwargs.get("consumer_module") or "")
        if consumer_module != "build_concepts":
            return compiled

        canonical = copy.deepcopy(compiled.canonical)
        report = copy.deepcopy(compiled.report)

        if adjudication_exempt(canonical):
            canonical["task_membership"] = {
                "status": "exempt",
                "authority": "gpt_page_ledger",
            }
        else:
            result = phase212.adjudicate_task_membership(canonical)
            if result["status"] == "adjudicated":
                accounting = result["ledger"]["accounting"]
                # Replay provenance is run metadata, not source content:
                # the canonical stays byte-identical across recompiles.
                canonical["task_membership"] = {
                    "status": "adjudicated",
                    "authority": "model_verdict",
                }
                progress.log(
                    "QX task-membership adjudication "
                    + ("replayed" if result.get("replayed") else "complete")
                    + f": {accounting.get('tasks_after', 0)} task(s), "
                    f"{accounting.get('candidates_rejected', 0)} candidate(s) "
                    "ruled not_task, "
                    f"{accounting.get('created_from_missed_asks', 0)} ask(s) "
                    "recovered beyond the parser.",
                    level="success",
                )
            else:
                canonical["task_membership"] = {
                    "status": "unadjudicated",
                    "reason_code": str(result.get("reason_code") or ""),
                    "reason": str(result.get("reason") or ""),
                }
                progress.log(
                    "QX task-membership adjudication unavailable "
                    f"({result.get('reason_code')}): {result.get('reason')}; "
                    "source-critical generation stays blocked pre-spend.",
                    level="warning",
                )

        tasks = [
            task for task in canonical.get("tasks") or []
            if isinstance(task, dict)
        ]
        source_contract = canonical.setdefault("source_contract", {})
        source_contract["task_count"] = len(tasks)
        if isinstance(canonical.get("statistics"), dict):
            canonical["statistics"]["tasks"] = len(tasks)

        # Audit F1: a count-changing adjudication must leave the Phase-2.1
        # seals CONSISTENT, or the persisted artifact fails
        # `hardening_artifact_valid` on the next load, recompiles into the
        # same mismatch, and raises. Reconciliation already refreshed the
        # canonical marker/source-contract counts (materialize_task_leaf_
        # cases stamps them); what must be resynced here is the rest of the
        # marker, the phase21 issue list, and the REPORT's copy of the
        # marker — all from the same post-adjudication ledger.
        from . import canonical_source_phase21 as phase21
        from . import canonical_source_phase21_structure as p21_structure

        source_issues = phase21.source_boundary_issues(canonical)
        canonical["phase21_issues"] = copy.deepcopy(source_issues)
        marker = canonical.get("phase21_hardening")
        if isinstance(marker, dict):
            parent_count, decomposed_count, inventory_items = (
                p21_structure.inventory_shape(canonical)
            )
            marker.update({
                "parent_task_count": parent_count,
                "decomposed_parent_task_count": decomposed_count,
                "inventory_item_count": inventory_items,
                "followup_task_prompts_recovered": (
                    p21_structure.followup_prompt_count(canonical)
                ),
                "blocking_issues": len(source_issues),
            })
            report["phase21_hardening"] = copy.deepcopy(marker)
        report["phase21_issues"] = copy.deepcopy(source_issues)
        shadow_seal = canonical.setdefault("shadow_validation", {})
        shadow_seal["phase21_blocking_issues"] = len(source_issues)

        all_issues = phase2.phase2_inventory_issues(canonical, report)
        ready = not all_issues
        canonical["phase2_inventory_ready"] = ready
        shadow = canonical.setdefault("shadow_validation", {})
        shadow["phase2_inventory_ready"] = ready
        shadow["phase2_blocking_issues"] = len(all_issues)

        report.update({
            "task_membership": copy.deepcopy(canonical["task_membership"]),
            "phase2_issues": copy.deepcopy(all_issues),
            "phase2_inventory_ready": ready,
        })
        summary = report.setdefault("summary", {})
        summary["tasks"] = len(tasks)
        summary["phase2_blocking_issues"] = len(all_issues)
        if all_issues:
            report["status"] = "failed"

        aegis_mmd = canonical_source._render_aegis_mmd(canonical)
        aegis_mmd = aegis_mmd.replace(
            f"<!-- schema_version: {canonical_source.SCHEMA_VERSION} -->",
            f"<!-- schema_version: {canonical.get('schema_version') or canonical_source.SCHEMA_VERSION} -->",
            1,
        ).replace(
            f"<!-- compiler_version: {canonical_source.COMPILER_VERSION} -->",
            f"<!-- compiler_version: {canonical.get('compiler_version') or canonical_source.COMPILER_VERSION} -->",
            1,
        ).replace(
            "AEGIS CANONICAL SOURCE SHADOW",
            "AEGIS CANONICAL SOURCE PHASE 2.1",
            1,
        ).replace(
            "<!-- used_for_generation: false -->",
            "<!-- used_for_generation: source-critical -->",
            1,
        )
        return canonical_source.CompiledSource(
            canonical=canonical,
            aegis_mmd=aegis_mmd,
            report=report,
        )

    @wraps(original_load)
    def load_or_refresh_for_job(job):
        canonical, report = original_load(job)
        if qx_artifact_valid(canonical, report):
            return canonical, report
        from . import uploads

        directory = uploads.source_artifact_directory(int(job.id))
        phase2.write_phase2_artifacts(
            directory,
            mmd_text=str(job.mmd_text or ""),
            source_filename=str(job.filename or "source.mmd"),
            consumer_module="build_concepts",
        )
        canonical_path = (
            directory
            / canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"]
        )
        report_path = (
            directory / canonical_source.ARTIFACT_SPECS["report"]["filename"]
        )
        canonical = phase2._read_json(canonical_path)
        report = phase2._read_json(report_path)
        # Deliberately no raise here when the recompile is still
        # unadjudicated (e.g. provider down): the blocking issue carries
        # the named reason and the pre-spend validation refuses with it.
        return canonical, report

    phase2.phase2_inventory_issues = phase2_inventory_issues
    phase2.compile_phase2_source = compile_phase2_source
    phase2._load_or_refresh_for_job = load_or_refresh_for_job
    phase2._PHASE212_CONTRACT_VERSION = _CONTRACT_VERSION
