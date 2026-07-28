"""Phase 2.1.1 task-list rendering, diagnostics, and conversion-log polish.

Phase 2.1 correctly blocks incomplete source structure, but the first production
run exposed one presentation defect: Mathpix ``\\item`` commands could survive
when the visual-ownership pass rebuilt a task display from its audit prompt.
This contract is installed last so it can safely refine the public ACSD boundary
without weakening any Phase 2.1 source, Figure, ordering, or inventory gate.
"""
from __future__ import annotations

import copy
import re
from contextvars import ContextVar
from functools import wraps
from types import ModuleType
from typing import Any

from . import canonical_source
from . import canonical_source_phase2 as phase2
from . import canonical_source_phase21_structure as structure
from . import canonical_source_phase21_visuals as visuals

_CONTRACT_VERSION = 1
PATCH_VERSION = "2.1.1"

_LIST_ENV_RE = re.compile(
    r"\\(?:begin|end)\{(?:itemize|enumerate|description)\}",
    re.IGNORECASE,
)
_LIST_ITEM_RE = re.compile(
    r"\\item(?:\[(?P<label>[^\]]*)\])?\s*",
    re.IGNORECASE,
)
_PROTECTED_RICH_TEXT_RE = re.compile(
    r"```.*?```|`[^`\n]+`|\[Katex\].*?\[/Katex\]|"
    r"\[img\s+src=\"[^\"]+\"\s+alt=\"[^\"]*\"[^\]]*\]",
    re.IGNORECASE | re.DOTALL,
)

_CONVERSION_LOG_REWRITE: ContextVar[bool] = ContextVar(
    "aegis_phase211_conversion_log_rewrite",
    default=False,
)
_CONVERSION_LOG_SEEN: ContextVar[set[str] | None] = ContextVar(
    "aegis_phase211_conversion_log_seen",
    default=None,
)


def _protect_rich_text(value: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"__AEGIS_PHASE211_PROTECTED_{len(protected):04d}__"
        protected[token] = match.group(0)
        return token

    return _PROTECTED_RICH_TEXT_RE.sub(replace, value), protected


def _restore_rich_text(value: str, protected: dict[str, str]) -> str:
    out = value
    for token, original in protected.items():
        out = out.replace(token, original)
    return out


def normalize_task_list_markup(value: object) -> str:
    """Render Mathpix list commands as readable text without changing content.

    The immutable raw MMD remains untouched. Only the canonical task/public
    display is normalized. Explicit labels such as ``1.``, ``a)`` and ``iv)``
    are preserved; an unlabeled item receives a neutral bullet.
    """
    text = str(value or "")
    if "\\item" not in text and not _LIST_ENV_RE.search(text):
        return text

    working, protected = _protect_rich_text(text)
    working = _LIST_ENV_RE.sub("\n", working)

    def replace_item(match: re.Match[str]) -> str:
        label = re.sub(r"\s+", " ", str(match.group("label") or "")).strip()
        marker = label if label else "•"
        return f"\n{marker} "

    working = _LIST_ITEM_RE.sub(replace_item, working)
    working = re.sub(r"[ \t]+\n", "\n", working)
    working = re.sub(r"\n[ \t]+", "\n", working)
    working = re.sub(r"\n{3,}", "\n\n", working)
    return _restore_rich_text(working.strip(), protected)


def compact_snippet(value: object, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def enrich_inventory_issues(
    canonical: dict[str, Any],
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach actionable QID/source context to fail-closed issue objects."""
    tasks_by_qid = {
        str(task.get("qid") or "").strip(): task
        for task in canonical.get("tasks") or []
        if isinstance(task, dict) and str(task.get("qid") or "").strip()
    }
    enriched: list[dict[str, Any]] = []
    for raw_issue in issues:
        issue = copy.deepcopy(raw_issue)
        qid = str(issue.get("qid") or "").strip()
        task = tasks_by_qid.get(qid)
        if task is not None:
            snippet = compact_snippet(
                task.get("display_prompt") or task.get("raw_prompt") or ""
            )
            if snippet:
                issue.setdefault("task_snippet", snippet)
        if issue.get("caption"):
            issue.setdefault("caption_snippet", compact_snippet(issue["caption"]))
        enriched.append(issue)
    return enriched


def format_issue_for_ui(issue: dict[str, Any]) -> str:
    """Return one compact, source-located issue string for the browser error."""
    code = str(issue.get("code") or "source_issue")
    location_parts: list[str] = []
    if issue.get("qid"):
        location_parts.append(str(issue["qid"]))
    if issue.get("section_number") not in (None, ""):
        location_parts.append(f"Section {issue['section_number']}")
    if issue.get("figure_id"):
        location_parts.append(str(issue["figure_id"]))
    if issue.get("block_id"):
        location_parts.append(str(issue["block_id"]))
    location = f" [{', '.join(location_parts)}]" if location_parts else ""
    message = str(issue.get("message") or "Canonical source validation failed.")
    snippet = str(
        issue.get("task_snippet")
        or issue.get("caption_snippet")
        or ""
    ).strip()
    context = f" Source: “{snippet}”" if snippet else ""
    return f"{code}{location}: {message}{context}"


def rewrite_conversion_log_message(message: object) -> str:
    """Replace Phase-1-only wording inside a Phase 2.1 Build Concepts run."""
    text = str(message or "")
    if text.startswith("Compiling Phase 1 canonical-source shadow;"):
        return (
            "Compiling canonical-source base artifacts for Phase 2.1 validation; "
            "the immutable raw MMD remains preserved for semantic concept "
            "extraction."
        )
    if text.startswith("Canonical-source shadow could not be written"):
        return text.replace(
            "Canonical-source shadow could not be written",
            "Canonical-source base artifacts could not be written",
            1,
        )
    if text.startswith("Canonical-source shadow "):
        return text.replace(
            "Canonical-source shadow ",
            "Canonical-source base audit ",
            1,
        )
    return text


def _is_visual_repair_summary(message: str) -> bool:
    return (
        message.startswith("Corrected ")
        and "internally inconsistent source Figure reference(s)" in message
    )


def install(generation: ModuleType | None = None) -> None:
    """Install Phase 2.1.1 exactly once after all earlier source contracts."""
    if getattr(phase2, "_PHASE211_CONTRACT_VERSION", 0) >= _CONTRACT_VERSION:
        return

    from . import progress, uploads

    original_task_display = structure.canonical_task_display
    original_strip_public_layout = visuals.strip_public_layout
    original_issue_reader = phase2.phase2_inventory_issues
    original_compile = phase2.compile_phase2_source
    original_convert = uploads.convert_job
    original_log = progress.log

    @wraps(original_task_display)
    def canonical_task_display(value: object) -> str:
        return original_task_display(normalize_task_list_markup(value))

    @wraps(original_strip_public_layout)
    def strip_public_layout(value: object) -> str:
        return normalize_task_list_markup(original_strip_public_layout(value))

    @wraps(original_issue_reader)
    def phase2_inventory_issues(canonical, report=None):
        return enrich_inventory_issues(
            canonical,
            list(original_issue_reader(canonical, report)),
        )

    @wraps(original_compile)
    def compile_phase2_source(*args, **kwargs):
        compiled = original_compile(*args, **kwargs)
        canonical = copy.deepcopy(compiled.canonical)
        report = copy.deepcopy(compiled.report)
        marker = canonical.setdefault("phase21_hardening", {})
        marker["patch_version"] = PATCH_VERSION
        canonical.setdefault("source_contract", {})["hardening_patch"] = PATCH_VERSION
        report.setdefault("phase21_hardening", {})["patch_version"] = PATCH_VERSION

        issues = phase2_inventory_issues(canonical, report)
        active = str(kwargs.get("consumer_module") or "") == "build_concepts"
        ready = active and not issues
        canonical["phase2_inventory_ready"] = ready
        canonical.setdefault("shadow_validation", {})[
            "phase2_inventory_ready"
        ] = ready
        report["phase2_issues"] = copy.deepcopy(issues)
        report["phase2_inventory_ready"] = ready
        report.setdefault("summary", {})["phase2_blocking_issues"] = len(issues)
        if active and issues:
            report["status"] = "failed"
        return canonical_source.CompiledSource(
            canonical=canonical,
            aegis_mmd=compiled.aegis_mmd,
            report=report,
        )

    def prepare_job_context(db: Any, job: Any) -> dict[str, Any]:
        canonical, report = phase2._load_or_refresh_for_job(job)
        issues = phase2.phase2_inventory_issues(canonical, report)
        if issues:
            sample = "; ".join(
                format_issue_for_ui(item) for item in issues[:5]
            )
            raise ValueError(
                "Phase 2.1.1 canonical source validation blocked concept "
                "generation before paid inventory/Type calls "
                f"({len(issues)} issue(s)): {sample}"
            )
        progress.log(
            "Phase 2.1.1 ACSD source contract active: stable tasks, source "
            "order, Figure ownership, shared context, image URLs, and canonical "
            "KaTeX/list rendering now come from the verified source ledger; "
            "semantic concept extraction still uses the immutable raw MMD.",
            level="success",
        )
        return canonical

    @wraps(original_log)
    def scoped_log(message, *args, **kwargs):
        text = str(message or "")
        if _CONVERSION_LOG_REWRITE.get():
            text = rewrite_conversion_log_message(text)
            if _is_visual_repair_summary(text):
                seen = _CONVERSION_LOG_SEEN.get()
                if seen is not None:
                    if text in seen:
                        return None
                    seen.add(text)
        return original_log(text, *args, **kwargs)

    @wraps(original_convert)
    def convert_job(*args, **kwargs):
        module = str(kwargs.get("module") or "")
        if module != "build_concepts":
            return original_convert(*args, **kwargs)
        rewrite_token = _CONVERSION_LOG_REWRITE.set(True)
        seen_token = _CONVERSION_LOG_SEEN.set(set())
        try:
            return original_convert(*args, **kwargs)
        finally:
            _CONVERSION_LOG_SEEN.reset(seen_token)
            _CONVERSION_LOG_REWRITE.reset(rewrite_token)

    structure.normalize_task_list_markup = normalize_task_list_markup
    structure.canonical_task_display = canonical_task_display
    visuals.strip_public_layout = strip_public_layout
    phase2.phase2_inventory_issues = phase2_inventory_issues
    phase2.compile_phase2_source = compile_phase2_source
    phase2.prepare_job_context = prepare_job_context
    progress.log = scoped_log
    uploads.convert_job = convert_job

    phase2._PHASE211_CONTRACT_VERSION = _CONTRACT_VERSION
    if generation is not None:
        generation._PHASE211_SOURCE_PATCH_VERSION = PATCH_VERSION
