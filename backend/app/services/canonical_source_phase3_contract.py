"""Install the Phase 3 semantic graph as Build Concepts' semantic source."""
from __future__ import annotations

import copy
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Any

from . import canonical_source_phase2 as phase2
from . import canonical_source_phase22 as phase22
from . import canonical_source_phase3 as phase3

_CONTRACT_VERSION = 1


def install(generation: ModuleType | None = None) -> None:
    if not phase3.enabled():
        return
    if getattr(phase2, "_PHASE3_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return
    if generation is None:
        from . import generation as generation
    from . import progress, uploads

    original_convert = uploads.convert_job
    original_run = uploads.run_with_openai_usage
    original_manifest = phase2.artifact_manifest
    original_download = uploads.source_artifact_download

    original_concepts_wrapper = generation.concepts_from_mmd
    # Phase 2.2 wraps only the top-level source argument. Phase 3 renders from
    # the already-adjudicated canonical graph, so call the underlying pipeline
    # directly to avoid applying byte-offset overlays a second time.
    original_concepts = getattr(
        original_concepts_wrapper, "__wrapped__", original_concepts_wrapper
    )
    original_topic_headings = generation._topic_headings
    original_topic_excerpts = generation._group_source_topic_excerpts
    original_snap_topics = generation._snap_topics_to_headings
    original_extract_inventory = generation._extract_question_task_inventory_via_api
    original_refresh_inventory = generation._refresh_inventory_from_source_anchors
    original_mine_types = generation._mine_types_from_inventory_via_api
    original_consolidate_types = generation._consolidate_semantic_types_via_api
    original_expand_units = generation._expand_mined_types_to_assignment_units
    original_assign_types = generation._assign_mined_types_via_api

    def _compile_shadow(job: Any) -> dict[str, Any] | None:
        canonical, _report = phase2._load_or_refresh_for_job(job)
        semantic = phase22.semantic_source(canonical, str(job.mmd_text or ""))
        graph, report = phase3.compile_semantic_graph(
            canonical,
            source_text=semantic,
            metadata={
                "chapter_title": "",
                "subject": "",
                "board": "",
                "grade": "",
                "unit": "",
                "source_filename": str(job.filename or "source.mmd"),
            },
        )
        rendered = phase3.render_semantic_source(graph, canonical)
        phase3.write_artifacts(
            uploads.source_artifact_directory(int(job.id)),
            graph=graph,
            report=report,
            semantic_source=rendered,
        )
        return graph

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
            graph = _compile_shadow(job)
            if graph:
                progress.log(
                    "Phase 3 semantic-graph shadow compiled: "
                    f"{len(graph.get('topics') or [])} topic(s), "
                    f"{len(graph.get('subtopics') or [])} subtopic(s), and "
                    f"{len(graph.get('tasks') or [])} stable QID task(s). "
                    "Generation will independently verify the hierarchy and "
                    "original-PDF evidence before semantic cutover.",
                    level=("success" if graph.get("status") == "ready" else "warning"),
                )
            if isinstance(result, dict):
                result = {
                    **result,
                    "source_artifacts": uploads.source_artifact_manifest(job),
                }
        except Exception as exc:
            progress.log(
                "Phase 3 semantic-graph shadow could not be compiled "
                f"({exc}); Phase 2 source artifacts remain preserved, and "
                "generation will retry the graph compiler before any semantic "
                "model call.",
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
            return original_run(db, job_id, fn, owner_sub=owner_sub)

        def phase3_fn():
            current = uploads.get_job(
                db,
                job_id,
                owner_sub=owner_sub,
                module="build_concepts",
            )
            canonical = phase2.active_canonical()
            if not isinstance(canonical, dict):
                canonical, _report = phase2._load_or_refresh_for_job(current)
            try:
                source_path = uploads.upload_file_path(current)
            except Exception:
                source_path = None
            session = {
                "job_id": int(current.id),
                "job": current,
                "canonical": canonical,
                "source_path": source_path,
                "artifact_dir": uploads.source_artifact_directory(int(current.id)),
                "raw_source": str(current.mmd_text or ""),
                "graph": None,
            }
            with phase3.activate_session(session):
                return fn()

        return original_run(db, job_id, phase3_fn, owner_sub=owner_sub)

    @wraps(original_concepts_wrapper)
    def concepts_from_mmd(mmd_text: str, *args, **kwargs):
        session = phase3.active_session()
        canonical = phase2.active_canonical()
        if not isinstance(session, dict) or not isinstance(canonical, dict):
            return original_concepts_wrapper(mmd_text, *args, **kwargs)
        semantic = phase22.active_semantic_source(mmd_text)
        metadata = {
            "board": kwargs.get("board") or "",
            "grade": kwargs.get("grade") or "",
            "subject": kwargs.get("subject") or "",
            "unit": kwargs.get("unit") or "",
            "chapter_title": kwargs.get("chapter_title") or "",
            "chapter_id": kwargs.get("chapter_id"),
            "chapter_code": kwargs.get("chapter_code") or "",
            "learning_kind": kwargs.get("learning_kind") or "",
        }
        resume_review = generation._newest_compatible_concept_checkpoint(
            kwargs.get("resume_checkpoint"),
            allowed_stages={"source_graph_review"},
        )
        resume_review_graph = (
            copy.deepcopy(resume_review.get("source_review_graph"))
            if isinstance(resume_review, dict)
            and isinstance(resume_review.get("source_review_graph"), dict)
            else None
        )
        # A compatible concept checkpoint preserves completed generation work, but
        # it never waives source verification.  The hierarchy call is avoided only
        # when a matching API-classified and independently verified graph is already
        # cached for this exact ACSD/overlay and academic metadata contract.
        try:
            graph = phase3.prepare_generation_graph(
                canonical=canonical,
                source_text=semantic,
                metadata=metadata,
                source_path=session.get("source_path"),
                artifact_dir=Path(session["artifact_dir"]),
                verify_semantics=True,
                resume_review_graph=resume_review_graph,
            )
        except phase3.semantic_recovery.HumanDecisionRequired as exc:
            graph = session.get("graph")
            callback = kwargs.get("checkpoint_callback")
            if isinstance(graph, dict) and callback is not None:
                callback(generation._make_concept_checkpoint(
                    "source_graph_review",
                    records=[],
                    source_review_graph=copy.deepcopy(graph),
                    source_review_context_hash=exc.context_hash,
                    source_review_decision_id=exc.decision_id,
                ))
            raise
        if graph.get("status") == "ready" and (
            graph.get(phase3._DOWNSTREAM_INVALIDATION_KEY)
            or (
                isinstance(resume_review_graph, dict)
                and resume_review_graph.get(
                    phase3._DOWNSTREAM_INVALIDATION_KEY)
            )
            or (
                isinstance(resume_review_graph, dict)
                and resume_review_graph.get("status") != "ready"
            )
        ):
            # A human-selected verified source block changes the semantic
            # source consumed by concept generation. Persist the now-ready
            # Phase 3 graph, atomically prune every stale downstream concept
            # stage, and start concept generation from its first stage. The
            # verified hierarchy/critic graph itself remains reusable.
            callback = kwargs.get("checkpoint_callback")
            if callback is not None:
                resolution_audit = (
                    graph.get("source_review_resolution") or {})
                review_context_hash = str(
                    (resume_review or {}).get(
                        "source_review_context_hash")
                    or resolution_audit.get("context_hash")
                    or ""
                )
                review_decision_id = str(
                    (resume_review or {}).get(
                        "source_review_decision_id")
                    or resolution_audit.get("decision_id")
                    or ""
                )
                flagged_graph = copy.deepcopy(graph)
                flagged_graph[
                    phase3._DOWNSTREAM_INVALIDATION_KEY
                ] = True
                callback(generation._make_concept_checkpoint(
                    "source_graph_review",
                    records=[],
                    source_review_graph=flagged_graph,
                    source_review_context_hash=review_context_hash,
                    source_review_decision_id=review_decision_id,
                    source_review_resolution_applied=True,
                ))
                # The first durable callback atomically pruned every concept
                # stage while retaining this flag. Only then may the flag be
                # cleared and the reusable ready Phase 3 graph persisted.
                graph.pop(phase3._DOWNSTREAM_INVALIDATION_KEY, None)
                rendered_ready = phase3.render_semantic_source(
                    graph, canonical)
                graph["semantic_source_sha256"] = phase3._sha256_text(
                    rendered_ready)
                phase3.write_artifacts(
                    Path(session["artifact_dir"]),
                    graph=graph,
                    report=phase3._updated_graph_report(graph),
                    semantic_source=rendered_ready,
                    page_bundle=None,
                )
                callback(generation._make_concept_checkpoint(
                    "source_graph_review",
                    records=[],
                    source_review_graph=copy.deepcopy(graph),
                    source_review_context_hash=review_context_hash,
                    source_review_decision_id=review_decision_id,
                    source_review_resolution_applied=True,
                ))
            kwargs["resume_checkpoint"] = None
        session["graph"] = graph
        rendered = phase3.render_semantic_source(graph, canonical)
        with phase3.activate(graph):
            result = original_concepts(rendered, *args, **kwargs)
        if isinstance(result, list):
            return phase3.canonicalize_record_topics(result, graph)
        return result

    @wraps(original_topic_headings)
    def topic_headings(sections):
        headings = phase3.graph_topic_headings()
        return headings or original_topic_headings(sections)

    @wraps(original_topic_excerpts)
    def group_source_topic_excerpts(sections):
        excerpts = phase3.graph_topic_excerpts()
        return excerpts or original_topic_excerpts(sections)

    @wraps(original_snap_topics)
    def snap_topics_to_headings(records, headings, *args, **kwargs):
        graph = phase3.active_graph()
        if not isinstance(graph, dict):
            return original_snap_topics(records, headings, *args, **kwargs)
        canonicalized = phase3.canonicalize_record_topics(records, graph)
        snapped = original_snap_topics(
            canonicalized,
            phase3.graph_topic_headings(graph) or headings,
            *args,
            **kwargs,
        )
        return phase3.canonicalize_record_topics(snapped, graph)

    @wraps(original_extract_inventory)
    def extract_question_task_inventory(*args, **kwargs):
        result = original_extract_inventory(*args, **kwargs)
        return phase3.annotate_inventory(result)

    @wraps(original_refresh_inventory)
    def refresh_inventory_from_source_anchors(inventory, sections):
        result = original_refresh_inventory(inventory, sections)
        return phase3.annotate_inventory(result)

    @wraps(original_mine_types)
    def mine_types_from_inventory_via_api(*args, **kwargs):
        result = original_mine_types(*args, **kwargs)
        return phase3.annotate_mined_types(result)

    @wraps(original_consolidate_types)
    def consolidate_semantic_types_via_api(*args, **kwargs):
        result = original_consolidate_types(*args, **kwargs)
        return phase3.annotate_mined_types(result)

    @wraps(original_expand_units)
    def expand_mined_types_to_assignment_units(types):
        units = original_expand_units(types)
        return phase3.annotate_assignment_units(units)

    @wraps(original_assign_types)
    def assign_mined_types_via_api(records, *args, **kwargs):
        graph = phase3.active_graph()
        if not isinstance(graph, dict):
            return original_assign_types(records, *args, **kwargs)
        mined_types = kwargs.get("mined_types")
        if isinstance(mined_types, dict):
            annotated = phase3.annotate_mined_types(mined_types, graph)
            # Preserve identity because later host certifications and checkpoint
            # artifacts use this exact dictionary instance.
            mined_types.clear()
            mined_types.update(annotated)
        canonicalized = phase3.canonicalize_record_topics(records, graph)
        if isinstance(mined_types, dict):
            canonicalized = phase3.ensure_type_scope_hosts(
                canonicalized,
                mined_types=mined_types,
                graph=graph,
                canonical=(phase3.active_session() or {}).get("canonical"),
            )
        grounded = phase3.ground_concepts(
            canonicalized,
            graph=graph,
            canonical=(phase3.active_session() or {}).get("canonical"),
        )
        return original_assign_types(grounded, *args, **kwargs)

    @wraps(original_manifest)
    def artifact_manifest(directory: Path):
        manifest = original_manifest(directory)
        phase3_manifest = phase3.artifact_manifest(directory)
        manifest["semantic_graph"] = {
            key: value
            for key, value in phase3_manifest.items()
            if key != "files"
        }
        existing = {
            str(row.get("kind") or "")
            for row in manifest.get("files") or []
            if isinstance(row, dict)
        }
        for row in phase3_manifest.get("files") or []:
            if str(row.get("kind") or "") not in existing:
                manifest.setdefault("files", []).append(row)
        ready = (
            phase3_manifest.get("status") == "ready"
            and phase3_manifest.get("classification_mode")
            == "api_classified_and_verified"
        )
        if ready:
            manifest["phase"] = phase3.PHASE
            manifest["ready_for_future_cutover"] = True
            usage = copy.deepcopy(manifest.get("generation_usage") or {})
            components = list(usage.get("components") or [])
            for component in (
                "semantic_topic_graph",
                "semantic_concept_extraction",
                "source_grounded_concepts",
                "qid_derived_type_scope",
                "deterministic_semantic_rendering",
            ):
                if component not in components:
                    components.append(component)
            usage["components"] = components
            usage["raw_mmd_components"] = []
            usage["mode"] = "phase3-semantic-graph"
            manifest["generation_usage"] = usage
        return manifest

    @wraps(original_download)
    def source_artifact_download(job: Any, kind: str):
        try:
            return original_download(job, kind)
        except ValueError:
            return phase3.artifact_path(
                uploads.source_artifact_directory(int(job.id)), kind
            )

    uploads.convert_job = convert_job
    uploads.run_with_openai_usage = run_with_openai_usage
    phase2.artifact_manifest = artifact_manifest
    uploads.source_artifact_download = source_artifact_download
    generation.concepts_from_mmd = concepts_from_mmd
    generation._topic_headings = topic_headings
    generation._group_source_topic_excerpts = group_source_topic_excerpts
    generation._snap_topics_to_headings = snap_topics_to_headings
    generation._extract_question_task_inventory_via_api = extract_question_task_inventory
    generation._refresh_inventory_from_source_anchors = refresh_inventory_from_source_anchors
    generation._mine_types_from_inventory_via_api = mine_types_from_inventory_via_api
    generation._consolidate_semantic_types_via_api = consolidate_semantic_types_via_api
    generation._expand_mined_types_to_assignment_units = expand_mined_types_to_assignment_units
    generation._assign_mined_types_via_api = assign_mined_types_via_api

    phase2._PHASE3_CONTRACT_VERSION = _CONTRACT_VERSION
    generation._PHASE3_SEMANTIC_GRAPH_VERSION = _CONTRACT_VERSION
