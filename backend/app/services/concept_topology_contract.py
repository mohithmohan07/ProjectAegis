"""Final-topology Type allocation and lossless rich-text safeguards.

Type mining still happens early because it can reveal missing method concepts. Type
allocation, however, is deferred until every semantic concept pass has completed.
The final rows are then treated as immutable topology: only Type/Case/Example,
Activity/Info Hub, image, and KaTeX presentation may change.

The module is installed from ``app.services`` so the contract applies to every Build
Concepts entry point without duplicating the large generation pipeline.
"""
from __future__ import annotations

import copy
import re
from contextvars import ContextVar
from functools import wraps
from types import ModuleType
from typing import Any

_CONTRACT_VERSION = 1
_DEFER_INITIAL_ALLOCATION: ContextVar[bool] = ContextVar(
    "aegis_defer_initial_type_allocation", default=False
)

# A model/JSON boundary can return the two characters ``\n`` instead of a line
# break. Convert only delimiter-shaped occurrences. TeX commands such as ``\nu``
# and ``\neq`` do not match this expression.
_LITERAL_LINEBREAK_RE = re.compile(
    r"\\n(?=(?:\s|Achieving\s+Mastery\b|(?:Miscellaneous\s+)?Type\s+\d{1,2}:|"
    r"Case\s+\d{1,2}:|Examples?(?:\s+0*\d+)?\s*:|"
    r"Misconception(?:s)?\b|Error\s+Analysis\b|Activity/Info\s+Hub\b))",
    re.IGNORECASE,
)


def _normalize_literal_linebreaks(value: str) -> str:
    return _LITERAL_LINEBREAK_RE.sub("\n", str(value or ""))


def _run_rewritten_phase3(
    generation: ModuleType,
    out: list[dict],
    kwargs: dict[str, Any],
) -> list[dict]:
    """Route everything after the 81% boundary through the rewritten Phase 3.

    Seals the boundary envelope from exactly the material this seam holds
    (docs/phase3-rewrite-spec.md §3), runs Settle → Host → Assemble with
    the decision store in the job's artifact directory, and returns the
    assembled rows — Types embedded in the house format, QIDs routed —
    for the unchanged deposit and release chain downstream.
    """
    import json

    from pathlib import Path

    from . import canonical_source_phase3 as phase3_core
    from .phase3 import envelope as p3_envelope
    from .phase3 import runner as p3_runner

    session = phase3_core.active_session() or {}
    graph = phase3_core.active_graph() or {}
    store_dir = None
    envelope_path = None
    artifact_dir = session.get("artifact_dir")
    if artifact_dir:
        store_dir = Path(artifact_dir) / "phase3-decisions"
        envelope_path = Path(artifact_dir) / "source.phase3-envelope.json"

    # Decide-once extends to the envelope itself: resume-time inventory
    # refreshes and re-mined Type coverage produce a semantically
    # equivalent but byte-different envelope, which changes every
    # decision key and re-bills the entire run (~40 minutes instead of a
    # free replay). Reuse the sealed envelope while the source contract
    # and the boundary skeleton are unchanged.
    skeleton_sha = phase3_core._sha256_json(list(out))
    env = None
    if envelope_path is not None and envelope_path.exists():
        try:
            wrapper = json.loads(envelope_path.read_text(encoding="utf-8"))
            stored = p3_envelope.validate(wrapper.get("envelope") or {})
            if (
                str(wrapper.get("boundary_skeleton_sha256") or "")
                == skeleton_sha
                and str(stored.get("source_contract_hash") or "")
                == str(graph.get("source_contract_hash") or "")
            ):
                env = stored
                generation.progress.log(
                    "Reusing the sealed Phase 3 envelope "
                    f"{str(env.get('envelope_sha256'))[:12]}; every stored "
                    "decision replays without a model call.",
                    level="success",
                )
        except Exception:  # noqa: BLE001 - a stale artifact never blocks
            env = None
    if env is None:
        env = p3_envelope.build(
            graph=graph,
            canonical=session.get("canonical") or {},
            skeleton_rows=list(out),
            inventory=kwargs.get("question_task_inventory") or {},
            mined_types=kwargs.get("mined_types") or {},
            metadata=kwargs.get("meta") or {},
        )
        if envelope_path is not None:
            try:
                envelope_path.write_text(
                    json.dumps(
                        {
                            "boundary_skeleton_sha256": skeleton_sha,
                            "envelope": env,
                        },
                        ensure_ascii=False,
                        indent=1,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass  # persistence is best-effort; the run proceeds
    result = p3_runner.run(env, store_dir=store_dir)
    summary = result["summary"]
    generation.progress.log(
        "Rewritten Phase 3 complete: "
        f"{summary['row_count']} row(s) settled and assembled, "
        f"{summary['routed_qids']} QID(s) routed, "
        f"{summary['unrouted_items']} inventory item(s) unrouted, "
        f"{summary['flagged_row_count']} row(s) carrying review flags.",
        level="success",
    )
    return result["records"]


def _topology_signature(generation: ModuleType, records: list[dict]) -> tuple:
    """Semantic row topology, excluding every renderable Type/Hub field."""
    return tuple(
        (
            str(record.get("topic") or "").strip(),
            str(record.get("parent_concept") or "").strip(),
            str(
                record.get("concept_title") or record.get("concept") or ""
            ).strip(),
            bool(
                generation.cr.is_culmination(
                    record.get("concept_title")
                    or record.get("concept")
                    or ""
                )
            ),
        )
        for record in records
    )


def _strip_source_owned_allocations(
    generation: ModuleType, records: list[dict]
) -> list[dict]:
    """Remove stale Types and Hubs while preserving all semantic row content."""
    cleaned: list[dict] = []
    for raw in records:
        record = copy.deepcopy(raw)
        sections = [
            (label, content)
            for label, content in generation.cr.split_sections(
                record.get("concept_details") or ""
            )
            if not (
                str(label or "").strip().lower().startswith("type")
                or generation.cr.is_activity_hub_label(label)
            )
        ]
        record["concept_details"] = generation.cr.join_sections(sections)
        record.pop("_activity_hub_qids", None)
        cleaned.append(record)
    return cleaned


def _empty_inventory() -> dict:
    return {"items": [], "stats": {}}


def _safe_display_text(generation: ModuleType, value: str) -> str:
    """Normalize presentation only; never paraphrase source wording."""
    text = _normalize_literal_linebreaks(value)
    text = generation.kr.canonicalize_rich_text(text)
    repaired = generation.kr.repair_unwrapped_math(text)
    if generation.kr.unwrap_katex(repaired) != generation.kr.unwrap_katex(text):
        raise RuntimeError(
            "deterministic source display repair changed the unwrapped source text"
        )
    return generation.kr.canonicalize_rich_text(repaired).strip()


def _canonicalize_concept_rows(
    generation: ModuleType, records: list[dict]
) -> list[dict]:
    """Canonicalize rows and add only provably lossless math wrappers."""
    original = generation._TOPOLOGY_CONTRACT_ORIGINAL_CANONICALIZE_ROWS
    normalized = [copy.deepcopy(record) for record in records]
    for record in normalized:
        if "concept_details" in record:
            record["concept_details"] = _normalize_literal_linebreaks(
                record.get("concept_details") or ""
            )
    out = original(normalized)
    for record in out:
        details = str(record.get("concept_details") or "")
        defects = set(generation.kr.rich_text_issues(details))
        if not defects or not defects.issubset(
            {"raw_latex", "raw_math_expression"}
        ):
            continue
        repaired = generation.kr.repair_unwrapped_math(details)
        if (
            repaired != details
            and not generation.kr.rich_text_issues(repaired)
            and generation.kr.unwrap_katex(repaired)
            == generation.kr.unwrap_katex(details)
        ):
            record["concept_details"] = repaired
    return out


def _protected_spans(generation: ModuleType, text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = list(
        generation.kr._markdown_code_ranges(text))
    for pattern in (
        generation.kr._KATEX_TAG_RE,
        generation.kr._IMAGE_TAG_RE,
        generation.kr._MARKDOWN_LINK_RE,
    ):
        spans.extend(match.span() for match in pattern.finditer(text))
    return spans


def _is_unprotected(position: int, spans: list[tuple[int, int]]) -> bool:
    return not any(start <= position < end for start, end in spans)


def rich_text_defect_detail(
    generation: ModuleType, details: str
) -> dict[str, Any]:
    """Return the exact first malformed token, section, offset, and context."""
    value = str(details or "")
    issues = generation.kr.rich_text_issues(value)
    if not issues:
        return {}
    spans = _protected_spans(generation, value)
    candidates: list[tuple[int, str, str]] = []

    for pattern in (
        generation.kr._RAW_LATEX_RE,
        generation.kr._RAW_SCRIPT_TAIL_RE,
    ):
        match = next(
            (
                item
                for item in pattern.finditer(value)
                if _is_unprotected(item.start(), spans)
            ),
            None,
        )
        if match is not None:
            candidates.append((match.start(), "raw_latex", match.group(0)))

    for pattern in (
        *generation.kr._RAW_BLOCK_MATH_PATTERNS,
        generation.kr._SINGLE_DOLLAR_MATH_RE,
    ):
        match = next(
            (
                item
                for item in pattern.finditer(value)
                if _is_unprotected(item.start(), spans)
            ),
            None,
        )
        if match is not None:
            candidates.append(
                (match.start(), "raw_math_delimiter", match.group(0))
            )

    if "raw_math_expression" in issues:
        match = next(
            (
                item
                for item in generation.kr._RAW_EQUATION_RE.finditer(value)
                if _is_unprotected(item.start(), spans)
            ),
            None,
        )
        if match is not None:
            candidates.append(
                (match.start(), "raw_math_expression", match.group(0))
            )

    position, code, matched = min(
        candidates, default=(0, issues[0], "")
    )
    section = "concept_details"
    cursor = 0
    for label, content in generation.cr.split_sections(value):
        start = value.find(content, cursor)
        if start < 0:
            continue
        end = start + len(content)
        if start <= position <= end:
            section = str(label or "concept_details").strip()
            break
        cursor = end
    left = max(0, position - 90)
    right = min(len(value), position + max(1, len(matched)) + 110)
    return {
        "defect": code,
        "match": matched,
        "offset": position,
        "section": section,
        "context": value[left:right],
    }


def _assert_topology_unchanged(
    generation: ModuleType,
    expected: tuple,
    records: list[dict],
    *,
    stage: str,
) -> None:
    if _topology_signature(generation, records) != expected:
        raise RuntimeError(
            f"{stage} changed frozen concept topology; refusing to deposit "
            "Type content after a row was renamed, moved, added, removed, or "
            "reordered"
        )


def _allocate_after_freeze(
    generation: ModuleType,
    records: list[dict],
    *,
    subject: str,
    mmd_text: str,
    meta: dict,
    source_sections: list[dict],
    question_task_inventory: dict,
    mined_types: dict,
    method_anchors: list[dict],
) -> list[dict]:
    """Allocate once on final rows, then allow presentation-only operations."""
    topology = _strip_source_owned_allocations(generation, records)
    signature = _topology_signature(generation, topology)
    generation._reset_placement_certifications(mined_types)
    generation.progress.step(
        "Concept extraction — allocating Types on frozen concept topology",
        value=0.94,
    )

    assigned = generation._TOPOLOGY_CONTRACT_ORIGINAL_ASSIGN_TYPES(
        topology,
        subject=subject,
        mmd_text=mmd_text,
        meta=meta,
        sections=source_sections,
        question_task_inventory=question_task_inventory,
        mined_types=mined_types,
    )
    assigned = generation._TOPOLOGY_CONTRACT_ORIGINAL_POPULATE_HUBS(
        assigned,
        question_task_inventory,
        meta=meta,
        mined_types=mined_types,
    )
    if not generation._TOPOLOGY_CONTRACT_ORIGINAL_CERT_COMPLETE(
        mined_types, question_task_inventory
    ):
        raise RuntimeError(
            "post-freeze Type allocation did not certify every source qid to "
            "one durable concept host"
        )

    assigned = generation._salvage_short_case_examples(
        assigned, inventory=question_task_inventory
    )
    assigned = generation._neutralize_unrepaired_rows(
        assigned, inventory=question_task_inventory
    )
    assigned = generation._enforce_rendered_inventory_coverage(
        assigned, question_task_inventory, mined_types
    )
    assigned = generation._normalize_activity_hubs_from_inventory(
        assigned, question_task_inventory, mined_types
    )
    assigned = generation._disambiguate_certified_split_type_cases(
        assigned, question_task_inventory, mined_types
    )
    assigned = generation.cr.renumber_types_continuously(assigned)
    assigned = generation._canonicalize_concept_rich_text(assigned)
    assigned, rich_text_repaired = generation._repair_final_rich_text_via_api(
        assigned,
        meta=meta,
        inventory=question_task_inventory,
        mined_types=mined_types,
    )
    if rich_text_repaired:
        assigned, _ = generation._reconcile_explicit_figure_images(
            assigned, source_sections
        )
    assigned = generation._canonicalize_concept_rich_text(assigned)
    _assert_topology_unchanged(
        generation,
        signature,
        assigned,
        stage="post-freeze Type allocation",
    )
    generation._validate_final_or_raise(
        assigned,
        stage="post-freeze allocation",
        inventory=question_task_inventory,
        mined_types=mined_types,
        method_anchors=method_anchors,
        source_text=mmd_text,
    )
    mined_types["_topology_allocation_contract"] = {
        "version": _CONTRACT_VERSION,
        "state": "allocated_after_freeze",
        "row_count": len(assigned),
    }
    generation.progress.log(
        "Concept topology frozen before Type allocation; Types and "
        "Activity/Info Hubs were assigned once on the final row map.",
        level="success",
    )
    return assigned


def install(generation: ModuleType) -> None:
    """Install the contract exactly once."""
    if (
        getattr(generation, "_CONCEPT_TOPOLOGY_CONTRACT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    # Keep originals visible so regression tests can isolate each boundary.
    generation._TOPOLOGY_CONTRACT_ORIGINAL_RUN_STAGES = (
        generation._run_live_concept_pre_final_stages
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_PREPARE = (
        generation._prepare_final_concept_content
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_ASSIGN_TYPES = (
        generation._assign_types_via_api
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_POPULATE_HUBS = (
        generation._populate_activity_hubs_via_api
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_CERT_COMPLETE = (
        generation._placement_certification_contract_complete
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_EMIT_CHECKPOINT = (
        generation._emit_concept_checkpoint
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_INVENTORY_TEXT = (
        generation._inventory_task_text
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_HUB_NOTE = (
        generation._compact_activity_hub_note
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_CANONICALIZE_ROWS = (
        generation._canonicalize_concept_rich_text
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_VALIDATION_CONTEXT = (
        generation._validation_error_context
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_REFRESH_REASONS = (
        generation._final_checkpoint_refresh_reasons
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_CHAPTER_TOPIC_ASSIGN = (
        generation._assign_chapter_wide_inventory_topics_via_api
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_PROGRESS_STEP = (
        generation.progress.step
    )
    generation._TOPOLOGY_CONTRACT_ORIGINAL_SET_PROGRESS = (
        generation.progress.set_progress
    )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_INVENTORY_TEXT)
    def inventory_task_text(item: dict) -> str:
        source = generation._TOPOLOGY_CONTRACT_ORIGINAL_INVENTORY_TEXT(item)
        return _safe_display_text(generation, source)

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_HUB_NOTE)
    def compact_hub_note(item: dict) -> str:
        source = generation._TOPOLOGY_CONTRACT_ORIGINAL_HUB_NOTE(item)
        return _safe_display_text(generation, source)

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_ASSIGN_TYPES)
    def assign_types(*args, **kwargs):
        if not _DEFER_INITIAL_ALLOCATION.get():
            return generation._TOPOLOGY_CONTRACT_ORIGINAL_ASSIGN_TYPES(
                *args, **kwargs
            )
        generation.progress.log(
            "Deferred Type allocation until semantic concept topology is final.",
            level="success",
        )
        return _strip_source_owned_allocations(generation, args[0])

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_POPULATE_HUBS)
    def populate_hubs(*args, **kwargs):
        if not _DEFER_INITIAL_ALLOCATION.get():
            return generation._TOPOLOGY_CONTRACT_ORIGINAL_POPULATE_HUBS(
                *args, **kwargs
            )
        return _strip_source_owned_allocations(generation, args[0])

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_CERT_COMPLETE)
    def certification_complete(*args, **kwargs):
        if _DEFER_INITIAL_ALLOCATION.get():
            return True
        return generation._TOPOLOGY_CONTRACT_ORIGINAL_CERT_COMPLETE(
            *args, **kwargs
        )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_EMIT_CHECKPOINT)
    def emit_checkpoint(callback, stage: str, **payload):
        if _DEFER_INITIAL_ALLOCATION.get() and stage == "post_type_assignment":
            # The 81% mined-Type checkpoint remains the durable resume boundary.
            # Do not persist a misleading 91% artifact before allocation exists.
            return generation._make_concept_checkpoint(
                stage,
                stage_label=(
                    "Topology preparation complete; final Type allocation pending"
                ),
                **payload,
            )
        return generation._TOPOLOGY_CONTRACT_ORIGINAL_EMIT_CHECKPOINT(
            callback, stage, **payload
        )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_PROGRESS_STEP)
    def progress_step(message: str, *args, **kwargs):
        if _DEFER_INITIAL_ALLOCATION.get() and "assigning Types" in message:
            message = (
                "Concept extraction — preparing final topology before Type "
                "allocation"
            )
        return generation._TOPOLOGY_CONTRACT_ORIGINAL_PROGRESS_STEP(
            message, *args, **kwargs
        )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_SET_PROGRESS)
    def set_progress(value, *args, **kwargs):
        if _DEFER_INITIAL_ALLOCATION.get() and kwargs.get("label") == (
            "Concept extraction — Type assignment complete"
        ):
            kwargs["label"] = (
                "Concept extraction — topology ready for final Type allocation"
            )
        return generation._TOPOLOGY_CONTRACT_ORIGINAL_SET_PROGRESS(
            value, *args, **kwargs
        )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_RUN_STAGES)
    def run_stages(*args, **kwargs):
        token = _DEFER_INITIAL_ALLOCATION.set(True)
        try:
            out, inventory, mined_types, snapshot = (
                generation._TOPOLOGY_CONTRACT_ORIGINAL_RUN_STAGES(
                    *args, **kwargs
                )
            )
        finally:
            _DEFER_INITIAL_ALLOCATION.reset(token)
        out = _strip_source_owned_allocations(generation, out)
        generation._reset_placement_certifications(mined_types)
        mined_types["_topology_allocation_contract"] = {
            "version": _CONTRACT_VERSION,
            "state": "deferred",
        }
        return out, inventory, mined_types, snapshot

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_PREPARE)
    def prepare_final(out: list[dict], **kwargs):
        from .phase3 import runner as p3_runner

        if p3_runner.rewrite_enabled():
            return _run_rewritten_phase3(generation, out, kwargs)
        real_inventory = kwargs["question_task_inventory"]
        real_mined_types = kwargs["mined_types"]
        generation._reset_placement_certifications(real_mined_types)

        topology_kwargs = dict(kwargs)
        topology_kwargs["question_task_inventory"] = _empty_inventory()
        topology_kwargs["mined_types"] = {"types": []}
        topology_kwargs["refresh_chapter_wide_assignments"] = False
        topology = generation._TOPOLOGY_CONTRACT_ORIGINAL_PREPARE(
            _strip_source_owned_allocations(generation, out),
            **topology_kwargs,
        )
        topology = _strip_source_owned_allocations(generation, topology)

        if kwargs.get("refresh_chapter_wide_assignments"):
            refreshed = (
                generation._TOPOLOGY_CONTRACT_ORIGINAL_CHAPTER_TOPIC_ASSIGN(
                    meta=kwargs["meta"],
                    inventory=real_inventory,
                    records=topology,
                    source_topic_excerpts=kwargs["source_topic_excerpts"],
                )
            )
            for item in refreshed.get("items") or []:
                item.pop("_topic_scope", None)
            real_inventory.clear()
            real_inventory.update(refreshed)

        return _allocate_after_freeze(
            generation,
            topology,
            subject=kwargs["subject"],
            mmd_text=kwargs["mmd_text"],
            meta=kwargs["meta"],
            source_sections=kwargs["source_sections"],
            question_task_inventory=real_inventory,
            mined_types=real_mined_types,
            method_anchors=kwargs["method_anchors"],
        )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_VALIDATION_CONTEXT)
    def validation_error_context(records: list[dict], error: dict):
        row_index, title, field, snippet = (
            generation._TOPOLOGY_CONTRACT_ORIGINAL_VALIDATION_CONTEXT(
                records, error
            )
        )
        if error.get("code") != "rich_text_format" or not (
            0 <= row_index < len(records)
        ):
            return row_index, title, field, snippet
        detail = rich_text_defect_detail(
            generation,
            records[row_index].get(
                error.get("field") or "concept_details", ""
            ),
        )
        if not detail:
            return row_index, title, field, snippet
        return (
            row_index,
            title,
            field,
            (
                f"section={detail['section']!r}; "
                f"defect={detail['defect']!r}; offset={detail['offset']}; "
                f"match={detail['match']!r}; context="
                f"{generation._diagnostic_snippet(detail['context'])!r}"
            ),
        )

    @wraps(generation._TOPOLOGY_CONTRACT_ORIGINAL_REFRESH_REASONS)
    def checkpoint_refresh_reasons(checkpoint, **kwargs):
        reasons = list(
            generation._TOPOLOGY_CONTRACT_ORIGINAL_REFRESH_REASONS(
                checkpoint, **kwargs
            )
        )
        if checkpoint:
            contract = (
                (checkpoint.get("mined_types") or {}).get(
                    "_topology_allocation_contract"
                )
                or {}
            )
            if (
                contract.get("version") != _CONTRACT_VERSION
                or contract.get("state") != "allocated_after_freeze"
            ):
                reasons.append(
                    "final checkpoint predates topology-frozen Type allocation"
                )
        return list(dict.fromkeys(reasons))

    generation._inventory_task_text = inventory_task_text
    generation._compact_activity_hub_note = compact_hub_note
    generation._assign_types_via_api = assign_types
    generation._populate_activity_hubs_via_api = populate_hubs
    generation._placement_certification_contract_complete = (
        certification_complete
    )
    generation._emit_concept_checkpoint = emit_checkpoint
    generation.progress.step = progress_step
    generation.progress.set_progress = set_progress
    generation._run_live_concept_pre_final_stages = run_stages
    generation._prepare_final_concept_content = prepare_final
    generation._canonicalize_concept_rich_text = (
        lambda records: _canonicalize_concept_rows(generation, records)
    )
    generation._validation_error_context = validation_error_context
    generation._final_checkpoint_refresh_reasons = checkpoint_refresh_reasons
    generation._CONCEPT_TOPOLOGY_CONTRACT_VERSION = _CONTRACT_VERSION
