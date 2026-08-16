"""Phase 2 ACSD source-critical cutover for Build Concepts.

Phase 1 compiled an immutable raw MMD, structured ACSD JSON, a readable Aegis
MMD, and a validation report beside every converted upload. Phase 2 makes the
ACSD authoritative for the source-critical portion of Build Concepts:

* ordered Question / Task Inventory rows;
* stable QINV identifiers and source identities;
* task-to-Figure ownership and canonical image captions;
* canonical teacher-facing task text, including KaTeX and image wrappers;
* duplicate/coverage keys used by Type mining and terminal validation.

Semantic concept extraction, description writing, learner analysis, and Type
classification remain on the existing generation path in this phase. The raw
MMD stays immutable and remains the semantic source supplied to those model
calls. Build Assessments remains a Phase 1 shadow consumer.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from . import canonical_source

PHASE = "phase-2-source-critical"
SOURCE_CONTRACT_MODE = "acsd-phase2-source-critical"
SCHEMA_VERSION = "1.1.0"
COMPILER_VERSION = "phase-2-source-critical-2"

_ACTIVE_CANONICAL: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_phase2_active_canonical_source",
    default=None,
)

# Fields copied from the deterministic production task-anchor parser. They are
# retained in ACSD so the production inventory never needs a second semantic
# extraction merely to recover source framing or visual ownership.
_ANCHOR_FIELDS = (
    "raw_solution_or_answer",
    "shared_context",
    "requires_context",
    "subpart_label",
    "page_hint",
    "content_objects",
    "_topic_scope",
    "_source_task_boundary",
    "_image_captions",
    "_figure_images_resolved",
)

_PHASE2_BLOCKING_WARNING_CODES = {
    "task_anchor_extraction_failed",
    "unresolved_required_visual",
    "display_rich_text_requires_review",
}


# Warning-severity codes that must never refuse a source on their own. Kept
# separate from ``_PHASE2_BLOCKING_WARNING_CODES`` (which compat layers mutate
# at install time) so the policy is stable wherever it is consulted.
_PHASE2_ADVISORY_GATE_CODES = frozenset({
    "task_anchor_extraction_failed",
    "unresolved_required_visual",
    "display_rich_text_requires_review",
})


def partition_gate_issues(
    issues: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split source-gate issues into fatal defects and advisory review notes.

    The compiler raises the advisory codes as WARNINGS — a task naming a
    Figure the registry could not resolve, a residual rich-text nit — and the
    gate then promoted them to blocking, which refused entire books that had
    otherwise parsed cleanly. They are review notes; only an error-severity
    defect genuinely invalidates a source.
    """
    fatal: list[dict[str, Any]] = []
    advisory: list[dict[str, Any]] = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity") or "").lower()
        code = str(issue.get("code") or "")
        if severity != "error" and code in _PHASE2_ADVISORY_GATE_CODES:
            advisory.append(issue)
        else:
            fatal.append(issue)
    return fatal, advisory


def _reader_stamp(source: str) -> str:
    from . import canonical_source_phase221_fallback as fallback

    return fallback.mmd_reader_version(source)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _normal_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _anchor_payloads(source: str) -> list[dict[str, Any]]:
    """Return deterministic production anchors with source-display metadata."""
    from . import generation

    sections = generation.parse_mmd_sections(source)
    anchors = generation._source_task_anchors(sections)
    anchors = generation._attach_explicit_figure_images(
        [copy.deepcopy(item) for item in anchors],
        sections,
    )
    enable_repairs = getattr(
        generation,
        "_enable_source_visual_display_repairs",
        None,
    )
    if callable(enable_repairs):
        wrapped = enable_repairs({"items": anchors})
        anchors = list(wrapped.get("items") or [])
    return [item for item in anchors if isinstance(item, dict)]


def _match_anchor(
    task: dict[str, Any],
    anchors: list[dict[str, Any]],
    used: set[int],
) -> tuple[int, dict[str, Any]] | None:
    """Match one canonical task to its source parser row without reordering."""
    raw_key = _normal_text(task.get("raw_prompt") or "")
    label_key = _normal_text(task.get("source_label") or "")
    task_section = int(task.get("source_section_index") or 0)
    task_position = int(task.get("source_position") or 0)

    ranked: list[tuple[tuple[int, int, int, int], int, dict[str, Any]]] = []
    for index, anchor in enumerate(anchors):
        if index in used:
            continue
        anchor_raw = str(
            anchor.get("raw_task") or anchor.get("normalized_task") or ""
        )
        anchor_key = _normal_text(anchor_raw)
        if not anchor_key:
            continue
        anchor_label = _normal_text(anchor.get("source_label") or "")
        try:
            anchor_section = int(anchor.get("_source_section_index") or 0)
        except (TypeError, ValueError):
            anchor_section = 0
        try:
            anchor_position = int(anchor.get("_source_position") or 0)
        except (TypeError, ValueError):
            anchor_position = 0

        exact = int(anchor_key == raw_key and bool(raw_key))
        contained = int(
            bool(raw_key)
            and min(len(raw_key), len(anchor_key)) >= 30
            and (raw_key in anchor_key or anchor_key in raw_key)
        )
        same_label = int(bool(label_key) and label_key == anchor_label)
        same_section = int(anchor_section == task_section)
        if not (exact or contained or same_label):
            continue
        score = (
            exact,
            contained,
            same_label + same_section,
            -abs(anchor_position - task_position),
        )
        ranked.append((score, index, anchor))

    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    _score, index, anchor = ranked[0]
    return index, anchor


def _augment_canonical_tasks(
    canonical: dict[str, Any],
    source: str,
) -> list[dict[str, Any]]:
    """Attach stable qids and complete deterministic source-anchor metadata."""
    tasks = [
        copy.deepcopy(task)
        for task in canonical.get("tasks") or []
        if isinstance(task, dict)
    ]
    tasks.sort(
        key=lambda task: (
            int(task.get("source_start") or 0),
            int(task.get("source_position") or 0),
            str(task.get("identity_key") or ""),
        )
    )
    try:
        anchors = _anchor_payloads(source)
    except Exception:
        anchors = []
    used: set[int] = set()

    for order, task in enumerate(tasks, start=1):
        task["task_id"] = f"TASK-{order:05d}"
        task["qid"] = f"QINV-{order:04d}"
        task["order"] = order
        task["order_index"] = order
        task["canonical_source_mode"] = SOURCE_CONTRACT_MODE

        matched = _match_anchor(task, anchors, used)
        if matched is not None:
            anchor_index, anchor = matched
            used.add(anchor_index)
            task["anchor_match"] = "deterministic_source_anchor"
            for field in _ANCHOR_FIELDS:
                if anchor.get(field) not in (None, "", [], {}):
                    task[field] = copy.deepcopy(anchor[field])
            # Source semantics already present in ACSD remain authoritative. The
            # extra anchor fields only complete context that Phase 1 did not
            # serialize explicitly.
            if not task.get("display_prompt"):
                try:
                    from . import generation

                    task["display_prompt"] = generation._inventory_task_text(
                        anchor
                    )
                except Exception:
                    task["display_prompt"] = str(
                        anchor.get("normalized_task")
                        or anchor.get("raw_task")
                        or task.get("raw_prompt")
                        or ""
                    )
        else:
            task["anchor_match"] = "canonical_task_only"

        if not task.get("display_prompt"):
            task["display_prompt"] = str(task.get("raw_prompt") or "")
        if not task.get("identity_key"):
            material = "\u241f".join([
                str(task.get("section_id") or ""),
                str(task.get("source_start") or 0),
                str(task.get("raw_prompt") or ""),
            ])
            task["identity_key"] = _sha256_text(material)
    canonical["tasks"] = tasks
    if isinstance(canonical.get("statistics"), dict):
        canonical["statistics"]["tasks"] = len(tasks)
    return tasks


def _figure_maps(
    canonical: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    figures = {
        str(item.get("figure_id") or ""): item
        for item in canonical.get("figures") or []
        if isinstance(item, dict) and item.get("figure_id")
    }
    images = {
        str(item.get("image_id") or ""): item
        for item in canonical.get("images") or []
        if isinstance(item, dict) and item.get("image_id")
    }
    return figures, images


def _task_visual_payload(
    task: dict[str, Any],
    figures: dict[str, dict[str, Any]],
    images: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, str]]:
    urls: list[str] = []
    captions: dict[str, str] = {}

    for raw_url in task.get("image_urls") or []:
        url = str(raw_url or "").strip()
        if url and url not in urls:
            urls.append(url)

    task_captions = task.get("_image_captions")
    if isinstance(task_captions, dict):
        captions.update({
            str(url): str(caption or "")
            for url, caption in task_captions.items()
            if str(url or "").strip()
        })

    for figure_id in task.get("figure_refs") or []:
        figure = figures.get(str(figure_id or ""), {})
        caption = str(figure.get("caption_raw") or "")
        for image_id in figure.get("image_ids") or []:
            image = images.get(str(image_id or ""), {})
            url = str(image.get("url") or "").strip()
            if not url:
                continue
            if url not in urls:
                urls.append(url)
            captions.setdefault(
                url,
                caption or str(image.get("alt_raw") or ""),
            )
    return urls, captions


def phase2_inventory_issues(
    canonical: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return source-critical defects that must block Build Concepts early."""
    from . import katex_rules as kr

    issues: list[dict[str, Any]] = []
    report = report or {}
    for item in report.get("issues") or []:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").lower()
        code = str(item.get("code") or "")
        if severity == "error" or code in _PHASE2_BLOCKING_WARNING_CODES:
            issues.append(copy.deepcopy(item))

    tasks = [item for item in canonical.get("tasks") or [] if isinstance(item, dict)]
    qids = [str(item.get("qid") or "").strip() for item in tasks]
    expected_qids = [f"QINV-{index:04d}" for index in range(1, len(tasks) + 1)]
    if qids != expected_qids:
        issues.append({
            "severity": "error",
            "code": "phase2_qid_order_mismatch",
            "message": "Canonical task qids are not a complete source-ordered sequence.",
        })
    identities = [str(item.get("identity_key") or "").strip() for item in tasks]
    if any(not value for value in identities) or len(identities) != len(set(identities)):
        issues.append({
            "severity": "error",
            "code": "phase2_task_identity_invalid",
            "message": "Canonical tasks do not have unique non-empty source identities.",
        })
    starts = [int(item.get("source_start") or 0) for item in tasks]
    if starts != sorted(starts):
        issues.append({
            "severity": "error",
            "code": "phase2_task_order_violation",
            "message": "Canonical tasks are not ordered by immutable source position.",
        })

    figures, images = _figure_maps(canonical)
    for task in tasks:
        qid = str(task.get("qid") or "")
        raw = str(task.get("raw_prompt") or "").strip()
        display = str(task.get("display_prompt") or "").strip()
        if not raw or not display:
            issues.append({
                "severity": "error",
                "code": "phase2_empty_task",
                "message": "A canonical source task has no complete prompt.",
                "qid": qid,
            })
            continue
        rich_issues = kr.rich_text_issues(display)
        if rich_issues:
            issues.append({
                "severity": "error",
                "code": "phase2_task_rich_text_invalid",
                "message": "A canonical task display prompt is not valid Aegis rich text.",
                "qid": qid,
                "rich_text_issues": rich_issues,
            })
        citation_severity = (
            "warning"
            if canonical_source.figure_citations_ship_for_review()
            else "error"
        )
        if task.get("unresolved_figure_reference_ids"):
            issues.append({
                "severity": citation_severity,
                "code": "phase2_unresolved_figure_reference",
                "message": "A canonical task contains an unresolved explicit Figure reference.",
                "qid": qid,
                "reference_ids": list(task.get("unresolved_figure_reference_ids") or []),
            })
        if task.get("ambiguous_figure_reference_ids"):
            issues.append({
                "severity": citation_severity,
                "code": "phase2_ambiguous_figure_reference",
                "message": "A canonical task has an ambiguous explicit Figure reference.",
                "qid": qid,
                "reference_ids": list(task.get("ambiguous_figure_reference_ids") or []),
            })
        urls, _captions = _task_visual_payload(task, figures, images)
        if task.get("requires_visual") and not urls:
            issues.append({
                "severity": "error",
                "code": "phase2_required_visual_missing",
                "message": "A visual source task has no source-owned canonical image.",
                "qid": qid,
            })
    # Stable de-duplication keeps the report readable when Phase 1 and Phase 2
    # independently identify the same source defect.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in issues:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _generation_usage(module: str) -> dict[str, Any]:
    active = str(module or "") == "build_concepts"
    return {
        "mode": "source-critical" if active else "shadow",
        "components": (
            [
                "question_task_inventory",
                "stable_qids",
                "task_order",
                "task_identity",
                "figure_ownership",
                "image_rendering",
                "katex_rendering",
                "inventory_coverage_keys",
            ]
            if active
            else []
        ),
        "raw_mmd_components": (
            [
                "semantic_concept_extraction",
                "description_generation",
                "learner_analysis",
                "type_classification",
            ]
            if active
            else ["all_generation_components"]
        ),
    }


def compile_phase2_source(
    mmd_text: str,
    *,
    source_filename: str,
    consumer_module: str,
) -> canonical_source.CompiledSource:
    """Compile and annotate an ACSD bundle for its actual consumer module."""
    compiled = canonical_source.compile_source(
        mmd_text,
        source_filename=source_filename,
    )
    canonical = copy.deepcopy(compiled.canonical)
    report = copy.deepcopy(compiled.report)
    source = str(mmd_text or "")
    tasks = _augment_canonical_tasks(canonical, source)

    active = str(consumer_module or "") == "build_concepts"
    phase2_issues = phase2_inventory_issues(canonical, report)
    phase2_ready = not phase2_issues
    usage = _generation_usage(consumer_module)

    canonical.update({
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "phase": PHASE,
        "consumer_module": str(consumer_module or ""),
        "shadow_mode": not active,
        "used_for_generation": active,
        "generation_usage": usage,
        "phase2_inventory_ready": phase2_ready if active else False,
    })
    canonical["source_contract"] = {
        "mode": SOURCE_CONTRACT_MODE,
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        # Carried from the MMD header so the reader survives into artifacts
        # that no longer hold the raw source text.
        "source_reader": _reader_stamp(source),
        "source_sha256": canonical.get("document", {}).get("source_sha256") or _sha256_text(source),
        "section_sequence": list(canonical.get("section_sequence") or []),
        "task_count": len(tasks),
        "consumer_module": str(consumer_module or ""),
    }
    canonical["shadow_validation"] = {
        **(canonical.get("shadow_validation") or {}),
        "phase2_inventory_ready": phase2_ready if active else False,
        "phase2_blocking_issues": len(phase2_issues),
    }

    report.update({
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "phase": PHASE,
        "consumer_module": str(consumer_module or ""),
        "shadow_mode": not active,
        "used_for_generation": active,
        "generation_usage": usage,
        "phase2_inventory_ready": phase2_ready if active else False,
        "phase2_issues": phase2_issues,
    })
    report_summary = report.setdefault("summary", {})
    report_summary["tasks"] = len(tasks)
    report_summary["phase2_blocking_issues"] = len(phase2_issues)

    aegis_mmd = canonical_source._render_aegis_mmd(canonical)
    if active:
        aegis_mmd = aegis_mmd.replace(
            "AEGIS CANONICAL SOURCE SHADOW",
            "AEGIS CANONICAL SOURCE PHASE 2",
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


def write_phase2_artifacts(
    directory: Path,
    *,
    mmd_text: str,
    source_filename: str,
    consumer_module: str,
) -> dict[str, Any]:
    """Atomically replace the four Phase 1 files with their Phase 2 form."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    compiled = compile_phase2_source(
        mmd_text,
        source_filename=source_filename,
        consumer_module=consumer_module,
    )
    payloads = {
        "raw_mmd": str(mmd_text or ""),
        "canonical_json": canonical_source._json_text(compiled.canonical),
        "aegis_mmd": compiled.aegis_mmd,
        "report": canonical_source._json_text(compiled.report),
    }
    for key, content in payloads.items():
        canonical_source._atomic_write(
            directory / canonical_source.ARTIFACT_SPECS[key]["filename"],
            content,
        )
    return artifact_manifest(directory)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"canonical source artifact is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("canonical source artifact must contain a JSON object")
    return value


def artifact_manifest(directory: Path) -> dict[str, Any]:
    """Return a Phase-aware manifest while preserving the Phase 1 API shape."""
    manifest = canonical_source.artifact_manifest(Path(directory))
    report_path = Path(directory) / canonical_source.ARTIFACT_SPECS["report"]["filename"]
    report = _read_json(report_path) if report_path.exists() else {}
    manifest.update({
        "shadow_mode": bool(report.get("shadow_mode", True)),
        "used_for_generation": bool(report.get("used_for_generation", False)),
        "phase": str(report.get("phase") or "phase-1-shadow"),
        "consumer_module": str(report.get("consumer_module") or ""),
        "generation_usage": report.get("generation_usage")
        if isinstance(report.get("generation_usage"), dict)
        else _generation_usage(""),
        "phase2_inventory_ready": bool(report.get("phase2_inventory_ready")),
    })
    return manifest


def _load_or_refresh_for_job(job: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a source-matching Phase 2 ACSD, rebuilding stale Phase 1 files."""
    from . import uploads

    # Every route into the canonical source funnels through here, including
    # the compat and turnover layers that replace prepare_job_context at
    # install time. The reader check belongs at this chokepoint so no variant
    # can skip it.
    stale_reader = _stale_source_reader(job)
    if stale_reader:
        raise ValueError(
            "this upload's source was read by a superseded chapter reader "
            f"({stale_reader}; current is {_current_source_reader()}). The MMD "
            "is written once at conversion and every later phase reads it, so "
            "regenerating would rebuild concepts from the old topic and "
            "question structure. Convert this PDF again as a new upload to "
            "read it with the current reader."
        )

    directory = uploads.source_artifact_directory(int(job.id))
    canonical_path = directory / canonical_source.ARTIFACT_SPECS["canonical_json"]["filename"]
    report_path = directory / canonical_source.ARTIFACT_SPECS["report"]["filename"]
    expected_hash = _sha256_text(str(job.mmd_text or ""))
    canonical: dict[str, Any] = {}
    report: dict[str, Any] = {}
    if canonical_path.exists() and report_path.exists():
        try:
            canonical = _read_json(canonical_path)
            report = _read_json(report_path)
        except ValueError:
            canonical = {}
            report = {}
    contract = canonical.get("source_contract") if isinstance(canonical, dict) else {}
    valid = bool(
        isinstance(contract, dict)
        and contract.get("mode") == SOURCE_CONTRACT_MODE
        and contract.get("source_sha256") == expected_hash
        and canonical.get("consumer_module") == "build_concepts"
        and canonical.get("schema_version") == SCHEMA_VERSION
        and canonical.get("compiler_version") == COMPILER_VERSION
    )
    if not valid:
        write_phase2_artifacts(
            directory,
            mmd_text=str(job.mmd_text or ""),
            source_filename=str(job.filename or "source.mmd"),
            consumer_module="build_concepts",
        )
        canonical = _read_json(canonical_path)
        report = _read_json(report_path)
    return canonical, report


@contextmanager
def activate(canonical: dict[str, Any]) -> Iterator[None]:
    token = _ACTIVE_CANONICAL.set(canonical)
    try:
        yield
    finally:
        _ACTIVE_CANONICAL.reset(token)


def active_canonical() -> dict[str, Any] | None:
    value = _ACTIVE_CANONICAL.get()
    return value if isinstance(value, dict) else None


def _never_split_questions() -> bool:
    # Reviewer rule under the rewritten pipeline: a question with
    # sub-questions stays ONE question, placed whole by the routing rules.
    # The ledger's leaf cases remain available to the legacy path.
    return True


def _mmd_source_reader(canonical: dict[str, Any]) -> str:
    contract = canonical.get("source_contract")
    if isinstance(contract, Mapping):
        return str(contract.get("source_reader") or "")
    return ""


def extraction_provenance(
    canonical: dict[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Record how this source was read, so a thin release can be diagnosed.

    A chapter that lands with far fewer questions than the book contains has
    two very different causes — the model never read it (the outline pass
    failed and the chapter fell back to deterministic sectioning), or it read
    it and split nothing. The release workbook has to be able to tell a
    reviewer which one happened without a rerun.
    """
    outline = canonical.get("chapter_outline")
    outline = outline if isinstance(outline, dict) else {}
    tasks = [task for task in canonical.get("tasks") or [] if isinstance(task, dict)]
    model_split_items = sum(
        1 for item in items
        if str(item.get("_acsd_decomposition") or "") == "gpt_semantic_boundary"
    )
    return {
        "chapter_outline_applied": bool(outline.get("version")),
        "chapter_outline_version": str(outline.get("version") or ""),
        "chapter_outline_topics": len(outline.get("topics") or []),
        "chapter_outline_partitions": len(outline.get("task_partitions") or []),
        "chapter_outline_review_flags": [
            str(flag) for flag in outline.get("review_flags") or []
        ],
        # A task the outline never ruled on is not a task judged whole: it is
        # a block nobody read, and any independent questions inside it were
        # lost without a flag. The count belongs next to the others.
        "chapter_outline_unruled_tasks": len(
            outline.get("unruled_task_refs") or []
        ),
        # Which reader produced the MMD every later phase read. A release
        # that looks thin can be checked against the current reader without
        # re-running anything.
        "source_reader": _mmd_source_reader(canonical),
        "source_task_blocks": len(tasks),
        "inventory_items": len(items),
        "model_split_items": model_split_items,
    }


def inventory_from_canonical(canonical: dict[str, Any]) -> dict[str, Any]:
    """Render the production Question / Task Inventory from ACSD task objects."""
    from . import generation
    from . import katex_rules as kr

    figures, images = _figure_maps(canonical)
    items: list[dict[str, Any]] = []
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        leaf_cases = [
            leaf for leaf in task.get("leaf_cases") or []
            if isinstance(leaf, dict)
        ]
        rows = leaf_cases or [task]
        gpt_decided_split = any(
            str(leaf.get("decomposition") or "") == "gpt_semantic_boundary"
            for leaf in leaf_cases
        )
        if leaf_cases and _never_split_questions() and not gpt_decided_split:
            # The whole question is one inventory item. The parent task's
            # display prompt carries every part printed inside the task's
            # own source span; a part recovered from a separate source
            # block (the phase-2.1 follow-up recovery) is appended in leaf
            # order so the one item really carries the question's entirety
            # — a part only the retired leaf split used to materialize
            # must never silently vanish from the closed-world inventory
            # (R4: exact-once, nothing lost). The span comparison is pure
            # provenance bookkeeping: the leaves were identified upstream;
            # this only assembles the recorded wording and its visuals.
            # The exception stays: a split the chapter-outline judge
            # decided keeps its independent subparts as separate questions
            # (board conventions differ, and the model judged the content
            # itself).
            merged = copy.deepcopy(task)
            parent_start = int(task.get("source_start") or 0)
            parent_end = int(task.get("source_end") or 0)
            display = str(merged.get("display_prompt") or "").strip()
            raw = str(merged.get("raw_prompt") or display).strip()
            figure_refs = [
                str(value) for value in merged.get("figure_refs") or []
                if str(value or "").strip()
            ]
            image_urls = [
                str(value) for value in merged.get("image_urls") or []
                if str(value or "").strip()
            ]
            appended_visuals = False
            for leaf in leaf_cases:
                leaf_start = int(leaf.get("source_start") or 0)
                if parent_start <= leaf_start < max(
                    parent_end, parent_start + 1
                ):
                    # Wording carved from the parent's own block is already
                    # inside the parent's complete prompt.
                    continue
                leaf_display = str(leaf.get("display_prompt") or "").strip()
                leaf_raw = str(
                    leaf.get("raw_prompt") or leaf_display
                ).strip()
                if leaf_display and leaf_display not in display:
                    display = f"{display}\n{leaf_display}".strip()
                if leaf_raw and leaf_raw not in raw:
                    raw = f"{raw}\n{leaf_raw}".strip()
                for figure_id in leaf.get("figure_refs") or []:
                    value = str(figure_id or "").strip()
                    if value and value not in figure_refs:
                        figure_refs.append(value)
                leaf_urls, leaf_captions = _task_visual_payload(
                    leaf, figures, images
                )
                for url in leaf_urls:
                    if url in image_urls:
                        continue
                    image_urls.append(url)
                    appended_visuals = True
                    if url not in display:
                        # The whole item's display prompt is the canonical
                        # public wording (`_acsd_display_prompt`); embed the
                        # follow-up part's visual exactly as the parent's own
                        # visuals were embedded at compile time.
                        caption = str(leaf_captions.get(url) or "").strip()
                        alt = caption or f"Source visual {len(image_urls)}"
                        display = f"{display} {kr.image(url, alt)}".strip()
            merged["display_prompt"] = display
            merged["raw_prompt"] = raw
            merged["figure_refs"] = figure_refs
            merged["image_urls"] = image_urls
            if appended_visuals:
                merged["requires_visual"] = True
            rows = [merged]
        for leaf in rows:
            row = copy.deepcopy(task)
            row.update(copy.deepcopy(leaf))
            qid = str(row.get("qid") or "").strip()
            display_prompt = str(row.get("display_prompt") or "").strip()
            raw_prompt = str(row.get("raw_prompt") or display_prompt).strip()
            image_urls, image_captions = _task_visual_payload(
                row,
                figures,
                images,
            )
            canonical_display = display_prompt
            if row.get("parent_qid"):
                for image_index, url in enumerate(image_urls):
                    if url in canonical_display:
                        continue
                    caption = str(image_captions.get(url) or "").strip()
                    alt = caption or f"Source visual {image_index + 1}"
                    canonical_display = (
                        f"{canonical_display} {kr.image(url, alt)}"
                    ).strip()
            parent_qid = str(row.get("parent_qid") or "").strip()
            parent_task_id = str(
                row.get("parent_task_id") or task.get("task_id") or ""
            )
            parent_identity = str(
                row.get("parent_identity_key")
                or task.get("identity_key")
                or ""
            )
            item: dict[str, Any] = {
                "qid": qid,
                "case_id": str(row.get("case_id") or ""),
                "parent_qid": parent_qid,
                "order_index": len(items) + 1,
                "parent_order_index": int(task.get("order") or len(items) + 1),
                "source_kind": str(row.get("source_kind") or "source_task"),
                "source_label": str(row.get("source_label") or ""),
                "parent_source_label": str(row.get("parent_source_label") or ""),
                "topic_hint": str(row.get("topic_hint") or ""),
                "page_hint": str(row.get("page_hint") or ""),
                "subpart_label": str(row.get("subpart_label") or ""),
                "requires_visual": bool(row.get("requires_visual")),
                "requires_context": bool(row.get("requires_context")),
                "normalized_task": canonical_display,
                "raw_task": raw_prompt,
                "raw_solution_or_answer": str(
                    row.get("raw_solution_or_answer") or ""
                ),
                "shared_context": str(row.get("shared_context") or ""),
                "image_urls": image_urls,
                "content_objects": copy.deepcopy(row.get("content_objects") or {}),
                "_activity_origin": bool(row.get("activity_origin")),
                "_chapter_wide_task": bool(row.get("chapter_wide")),
                "_topic_scope": (
                    "chapter" if row.get("chapter_wide") else str(
                        row.get("_topic_scope") or "topic"
                    )
                ),
                "_source_section_index": int(
                    row.get("source_section_index") or 0
                ),
                "_source_position": int(row.get("source_position") or 0),
                "_source_task_boundary": (
                    "acsd_leaf_case" if parent_qid else str(
                        row.get("_source_task_boundary") or "acsd_task"
                    )
                ),
                "_image_captions": image_captions,
                "_figure_images_resolved": bool(
                    not row.get("unresolved_figure_reference_ids")
                    and not row.get("ambiguous_figure_reference_ids")
                    and (not row.get("requires_visual") or image_urls)
                ),
                "_source_visual_reference_repairs": copy.deepcopy(
                    row.get("display_overrides") or []
                ),
                "_source_visual_display_repair_enabled": bool(
                    row.get("display_overrides")
                ),
                "_acsd_task_id": parent_task_id,
                "_acsd_case_id": str(row.get("case_id") or ""),
                "_acsd_parent_qid": parent_qid,
                "_acsd_parent_identity_key": parent_identity,
                "_acsd_identity_key": str(row.get("identity_key") or ""),
                "_acsd_display_prompt": canonical_display,
                "_acsd_raw_prompt": raw_prompt,
                "_acsd_source_contract": SOURCE_CONTRACT_MODE,
                "_acsd_decomposition": str(row.get("decomposition") or ""),
            }
            items.append(item)

    inventory = {
        "items": items,
        "stats": generation._inventory_stats(items),
        "source_contract": copy.deepcopy(canonical.get("source_contract") or {}),
        "extraction_provenance": extraction_provenance(canonical, items),
    }
    return inventory


def phase2_inventory_ready(canonical: dict[str, Any], report: dict[str, Any]) -> bool:
    return not phase2_inventory_issues(canonical, report)


def _checkpoint_uses_phase2(entry: dict[str, Any]) -> bool:
    inventory = entry.get("question_task_inventory")
    return bool(
        isinstance(inventory, dict)
        and isinstance(inventory.get("source_contract"), dict)
        and inventory["source_contract"].get("mode") == SOURCE_CONTRACT_MODE
    )


def prune_legacy_inventory_checkpoints(db: Any, job: Any) -> bool:
    """Rewind only legacy inventory-or-later stages, retaining earlier work."""
    from . import generation, progress

    stored = copy.deepcopy(job.generation_checkpoint or {})
    entries = [
        copy.deepcopy(entry)
        for entry in generation._concept_checkpoint_entries(stored)
        if isinstance(entry, dict) and entry.get("stage")
    ]
    cutoff = generation._checkpoint_order("question_inventory")
    stale = [
        entry for entry in entries
        if generation._checkpoint_order(str(entry.get("stage") or "")) >= cutoff
        and not _checkpoint_uses_phase2(entry)
    ]
    if not stale:
        return False
    retained = [
        entry for entry in entries
        if generation._checkpoint_order(str(entry.get("stage") or "")) < cutoff
        or _checkpoint_uses_phase2(entry)
    ]
    if retained:
        newest = max(
            enumerate(retained),
            key=lambda indexed: (
                generation._checkpoint_order(
                    str(indexed[1].get("stage") or "")
                ),
                indexed[0],
            ),
        )[1]
        durable = {
            key: copy.deepcopy(value)
            for key, value in stored.items()
            if key != "checkpoints"
        }
        durable["checkpoints"] = retained
        for field in (
            "stage",
            "stage_order",
            "stage_schema_version",
            "stage_label",
            "saved_at",
            "progress",
        ):
            durable[field] = copy.deepcopy(newest.get(field))
    else:
        durable = {}
    job.generation_checkpoint = durable
    job.detail = (
        "Phase 2 retained the newest pre-inventory checkpoint and discarded "
        "legacy Question / Task Inventory stages; the next run rebuilds the "
        "inventory deterministically from ACSD."
    )
    db.commit()
    progress.log(
        "Rewound "
        f"{len(stale)} legacy checkpoint stage(s) to the pre-inventory "
        "boundary for the Phase 2 ACSD source contract.",
        level="warning",
    )
    return True


def _current_source_reader() -> str:
    from . import canonical_source_phase221_fallback as fallback

    return fallback.source_reader_version()


def _stale_source_reader(job: Any) -> str:
    """The superseded reader behind this job's MMD, or "" when it is current.

    ``job.mmd_text`` is produced once, by conversion, and every later phase
    reads it instead of the PDF. Nothing else invalidates it, so an improved
    chapter reader would otherwise never reach an already-converted upload.
    """
    from . import canonical_source_phase221_fallback as fallback

    return fallback.stale_mmd_reader(getattr(job, "mmd_text", ""))


def prepare_job_context(db: Any, job: Any) -> dict[str, Any]:
    """Refresh artifacts, enforce the source-critical gate, and prune drift."""
    from . import progress

    canonical, report = _load_or_refresh_for_job(job)
    issues = phase2_inventory_issues(canonical, report)
    if issues:
        sample = "; ".join(
            f"{item.get('code')}: {item.get('message')}"
            for item in issues[:5]
        )
        raise ValueError(
            "Phase 2 canonical source validation blocked concept generation "
            f"before paid inventory/Type calls ({len(issues)} issue(s)): "
            + sample
        )
    prune_legacy_inventory_checkpoints(db, job)
    progress.log(
        "Phase 2 ACSD source contract active: Question / Task Inventory, "
        "stable QIDs, task order, Figure ownership, images, and KaTeX now come "
        "from the canonical source; semantic concept extraction still uses the "
        "immutable raw MMD.",
        level="success",
    )
    return canonical
