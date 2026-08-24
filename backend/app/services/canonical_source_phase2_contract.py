"""Install the Phase 2 ACSD source-critical Build Concepts contract."""
from __future__ import annotations

import copy
from functools import wraps
from types import ModuleType
from typing import Any

from .. import models
from . import canonical_source, canonical_source_phase2 as phase2

_CONTRACT_VERSION = 2


def _job_manifest(uploads: ModuleType, job: models.UploadJob) -> dict[str, Any]:
    directory = uploads.source_artifact_directory(int(job.id))
    manifest = phase2.artifact_manifest(directory)
    manifest["manifest_url"] = f"/source-artifacts/uploads/{int(job.id)}"
    for item in manifest.get("files") or []:
        if isinstance(item, dict) and item.get("kind"):
            item["download_url"] = (
                f"/source-artifacts/uploads/{int(job.id)}/{item['kind']}"
            )
    return manifest


def install() -> None:
    """Activate Phase 2 after Phase 1's upload/artifact wrappers are installed."""
    from . import generation, progress, uploads

    if getattr(uploads, "_CANONICAL_SOURCE_PHASE2_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_convert = uploads.convert_job
    original_run = uploads.run_with_openai_usage
    original_extract_inventory = generation._extract_question_task_inventory_via_api
    original_pre_join_inventory = generation._extract_inventory_pre_join
    original_finish_inventory = generation._finish_inventory_with_topics
    original_refresh_inventory = generation._refresh_inventory_from_source_anchors
    original_inventory_text = generation._inventory_task_text

    uploads._PHASE2_ORIGINAL_CONVERT = original_convert
    uploads._PHASE2_ORIGINAL_RUN_WITH_USAGE = original_run
    generation._PHASE2_ORIGINAL_EXTRACT_INVENTORY = original_extract_inventory
    generation._PHASE2_ORIGINAL_PRE_JOIN_INVENTORY = original_pre_join_inventory
    generation._PHASE2_ORIGINAL_FINISH_INVENTORY = original_finish_inventory
    generation._PHASE2_ORIGINAL_REFRESH_INVENTORY = original_refresh_inventory
    generation._PHASE2_ORIGINAL_INVENTORY_TEXT = original_inventory_text

    @wraps(original_convert)
    def convert_job(*args, **kwargs):
        result = original_convert(*args, **kwargs)
        db = args[0] if args else kwargs.get("db")
        job_id = args[1] if len(args) > 1 else kwargs.get("job_id")
        module = str(kwargs.get("module") or "")
        if module != "build_concepts" or not job_id:
            return result
        owner_sub = kwargs.get("owner_sub")
        try:
            job = uploads.get_job(
                db,
                int(job_id),
                owner_sub=owner_sub,
                module="build_concepts",
            )
            phase2.write_phase2_artifacts(
                uploads.source_artifact_directory(job.id),
                mmd_text=job.mmd_text,
                source_filename=job.filename,
                consumer_module="build_concepts",
            )
            manifest = _job_manifest(uploads, job)
            summary = manifest.get("summary") or {}
            progress.log(
                "Phase 2 canonical source ready for Build Concepts: "
                f"{summary.get('tasks', 0)} stable task(s), "
                f"{summary.get('figures', 0)} Figure object(s), "
                f"{summary.get('math_spans', 0)} math span(s); source-critical "
                "generation is "
                + (
                    "ready."
                    if manifest.get("phase2_inventory_ready")
                    else "blocked pending source review."
                ),
                level=(
                    "success"
                    if manifest.get("phase2_inventory_ready")
                    else "warning"
                ),
            )
            if isinstance(result, dict):
                result = {**result, "source_artifacts": manifest}
        except Exception as exc:  # conversion/raw MMD remains independently valid
            progress.log(
                "Phase 2 canonical-source preparation failed "
                f"({exc}); the immutable raw MMD and Phase 1 audit files remain "
                "available, but Build Concepts will fail closed before paid "
                "generation until the canonical source is valid.",
                level="warning",
            )
        return result

    @wraps(original_run)
    def run_with_openai_usage(
        db,
        job_id: int,
        fn,
        *,
        owner_sub: str | None = None,
    ):
        job = uploads.get_job(db, job_id, owner_sub=owner_sub)
        if job.module != "build_concepts":
            return original_run(
                db,
                job_id,
                fn,
                owner_sub=owner_sub,
            )

        def phase2_fn():
            current = uploads.get_job(
                db,
                job_id,
                owner_sub=owner_sub,
                module="build_concepts",
            )
            canonical = phase2.prepare_job_context(db, current)
            with phase2.activate(canonical):
                return fn()

        return original_run(
            db,
            job_id,
            phase2_fn,
            owner_sub=owner_sub,
        )

    def _validate_canonical_inventory(inventory: dict) -> dict:
        inventory["stats"] = generation._inventory_stats(
            list(inventory.get("items") or [])
        )
        invalid = generation._invalid_inventory_items(inventory)
        if invalid:
            details = ", ".join(
                f"{item.get('qid') or item.get('index')}:{item.get('reason')}"
                for item in invalid[:10]
            )
            raise RuntimeError(
                "Phase 2 ACSD produced an invalid Question / Task Inventory: "
                + details
            )
        qids = [
            str(item.get("qid") or "")
            for item in inventory.get("items") or []
        ]
        if len(qids) != len(set(qids)):
            raise RuntimeError(
                "Phase 2 ACSD Question / Task Inventory contains duplicate qids"
            )
        return inventory

    def _finish_canonical_inventory(
        inventory: dict,
        *,
        meta: dict,
        sections: list[dict],
        records: list[dict] | None,
    ) -> dict:
        """Finish ACSD inventory mechanically; never re-judge task membership.

        Q19's early inventory track used to call the raw section extractor
        directly and therefore bypassed the Phase 2 wrapper.  That created a
        second semantic authority over the same source: the ACSD could record
        a paragraph as context for QINV-0012 while the later completeness
        reviewer promoted the same paragraph into a new inventory item.  The
        canonical task/context relationship is already source-critical
        authority.  Downstream may route a chapter-wide task to a topic, but it
        must not rediscover whether source material is itself a task.
        """
        canonical = copy.deepcopy(inventory)
        if records:
            chapter_wide = [
                item for item in canonical.get("items") or []
                if item.get("_topic_scope") == "chapter"
            ]
            if chapter_wide:
                canonical = generation._assign_chapter_wide_inventory_topics_via_api(
                    meta=meta,
                    inventory=canonical,
                    records=records,
                    source_topic_excerpts=generation._group_source_topic_excerpts(
                        sections
                    ),
                )
        for item in canonical.get("items") or []:
            if item.get("_topic_scope") == "chapter":
                item["_chapter_wide_task"] = True
            item.pop("_topic_scope", None)
        _validate_canonical_inventory(canonical)
        qids = [
            str(item.get("qid") or "")
            for item in canonical.get("items") or []
        ]
        progress.log(
            "Phase 2 ACSD remains the sole Question / Task Inventory "
            "membership authority through the Q19 join; canonical task/context "
            "relationships were preserved without a second extraction or "
            "completeness judgment.",
            level="success",
        )
        progress.log(
            "Phase 2 ACSD Question / Task Inventory items: "
            f"{len(qids)}; stable qids {qids[0] if qids else '(none)'}"
            + (f"–{qids[-1]}" if len(qids) > 1 else "")
            + ".",
            level="success",
        )
        return canonical

    @wraps(original_extract_inventory)
    def extract_question_task_inventory(*args, **kwargs):
        canonical = phase2.active_canonical()
        if canonical is None:
            return original_extract_inventory(*args, **kwargs)

        meta = kwargs.get("meta") or {}
        sections = kwargs.get("sections") or []
        records = kwargs.get("records") or []
        inventory = phase2.inventory_from_canonical(canonical)
        progress.log(
            "Building Question / Task Inventory from the authoritative Phase 2 "
            "ACSD task ledger; no downstream task-membership extraction model "
            "call is permitted."
        )
        return _finish_canonical_inventory(
            inventory,
            meta=meta,
            sections=sections,
            records=records,
        )

    @wraps(original_pre_join_inventory)
    def extract_inventory_pre_join(*args, **kwargs):
        canonical = phase2.active_canonical()
        if canonical is None:
            return original_pre_join_inventory(*args, **kwargs)
        # Q19 runs this function inside a copied context, so the active ACSD
        # reaches the worker.  Returning the canonical ledger here keeps the
        # performance fork but removes the accidental second source extractor.
        inventory = phase2.inventory_from_canonical(canonical)
        _validate_canonical_inventory(inventory)
        progress.log(
            "Q19 early inventory track reused the Phase 2 ACSD task ledger; "
            "source task membership was not re-extracted.",
            level="success",
        )
        return inventory, []

    @wraps(original_finish_inventory)
    def finish_inventory_with_topics(
        inventory,
        anchors,
        *,
        meta,
        sections,
        records,
    ):
        items = [
            item for item in (inventory or {}).get("items") or []
            if isinstance(item, dict)
        ]
        canonical_inventory = bool(items) and all(
            item.get("_acsd_source_contract") == phase2.SOURCE_CONTRACT_MODE
            for item in items
        )
        if canonical_inventory:
            return _finish_canonical_inventory(
                inventory,
                meta=meta,
                sections=sections,
                records=records,
            )
        return original_finish_inventory(
            inventory,
            anchors,
            meta=meta,
            sections=sections,
            records=records,
        )

    @wraps(original_refresh_inventory)
    def refresh_inventory_from_source_anchors(inventory, sections):
        canonical = phase2.active_canonical()
        if canonical is None:
            return original_refresh_inventory(inventory, sections)
        refreshed = phase2.inventory_from_canonical(canonical)
        previous = {
            str(item.get("qid") or ""): item
            for item in (inventory or {}).get("items") or []
            if isinstance(item, dict) and item.get("qid")
        }
        # Chapter-wide semantic topic placement is downstream classification,
        # not source extraction. Preserve its reviewed result when the same
        # canonical qid resumes from a Phase 2 checkpoint.
        for item in refreshed.get("items") or []:
            qid = str(item.get("qid") or "")
            old = previous.get(qid)
            if (
                old
                and item.get("_chapter_wide_task")
                and str(old.get("topic_hint") or "").strip()
            ):
                item["topic_hint"] = old["topic_hint"]
                item["_topic_scope"] = old.get("_topic_scope") or "chapter"
        _validate_canonical_inventory(refreshed)
        progress.log(
            "Refreshed the resumed Question / Task Inventory from the Phase 2 "
            "ACSD task ledger.",
            level="success",
        )
        return refreshed

    @wraps(original_inventory_text)
    def inventory_task_text(item: dict) -> str:
        if (
            isinstance(item, dict)
            and item.get("_acsd_source_contract")
            == phase2.SOURCE_CONTRACT_MODE
        ):
            display = str(item.get("_acsd_display_prompt") or "").strip()
            if not display:
                raise RuntimeError(
                    "Phase 2 ACSD inventory item has no canonical display prompt"
                )
            return display
        return original_inventory_text(item)

    def source_artifact_manifest(job: models.UploadJob) -> dict[str, Any]:
        return _job_manifest(uploads, job)

    def source_artifacts_property(job: models.UploadJob) -> dict[str, Any]:
        if not getattr(job, "id", None):
            return {
                "available": False,
                "shadow_mode": True,
                "used_for_generation": False,
                "phase": "unavailable",
                "status": "unavailable",
                "files": [],
            }
        return _job_manifest(uploads, job)

    uploads.convert_job = convert_job
    uploads.run_with_openai_usage = run_with_openai_usage
    uploads.source_artifact_manifest = source_artifact_manifest
    generation._extract_question_task_inventory_via_api = (
        extract_question_task_inventory
    )
    generation._extract_inventory_pre_join = extract_inventory_pre_join
    generation._finish_inventory_with_topics = finish_inventory_with_topics
    generation._refresh_inventory_from_source_anchors = (
        refresh_inventory_from_source_anchors
    )
    generation._inventory_task_text = inventory_task_text
    models.UploadJob.source_artifacts = property(source_artifacts_property)
    uploads._CANONICAL_SOURCE_PHASE2_VERSION = _CONTRACT_VERSION
