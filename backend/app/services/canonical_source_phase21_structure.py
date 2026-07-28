"""Phase 2.1 chapter-structure and deterministic task-boundary guards."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from . import katex_rules as kr

_CUE_RE = re.compile(
    r"(?is)^\s*(?P<cue>Discuss|Activity|Project|Write\s+in\s+brief|"
    r"Think\s+about\s+it|Let's\s+discuss)\s*(?:[:\-]\s*)?\n+"
    r"(?P<body>.+?)\s*$"
)
_IMPERATIVE_RE = re.compile(
    r"(?i)^(?:with\s+the\s+help\s+of|summari[sz]e|describe|discuss|compare|"
    r"explain|imagine|write|plot|look|examine|identify|find\s+out|trace|"
    r"choose|state|list|calculate|solve|draw|analyse|analyze|interpret|"
    r"comment|who|what|why|how|where|when|which)\b"
)
_GLOSSARY_RE = re.compile(
    r"(?i)^\s*(?:new\s+words?|glossary|source\s+[A-Z0-9]+|"
    r"some\s+important\s+dates)\b"
)
_MAIN_HEADING_RE = re.compile(
    r"\\section\*?\{\s*(?P<number>\d+)(?:\s+(?P<title>[^}]+))?\s*\}",
    re.IGNORECASE,
)
_SUB_HEADING_RE = re.compile(
    r"\\(?:section|subsection)\*?\{\s*(?P<major>\d+)[.．]"
    r"(?P<minor>\d+)\s+(?P<title>[^}]+)\}",
    re.IGNORECASE,
)
_MD_MAIN_HEADING_RE = re.compile(
    r"(?m)^#{1,2}\s+(?P<number>\d+)(?:\s+(?P<title>.+?))?\s*$"
)
_MD_SUB_HEADING_RE = re.compile(
    r"(?m)^#{1,4}\s+(?P<major>\d+)[.．](?P<minor>\d+)\s+"
    r"(?P<title>.+?)\s*$"
)
_LAYOUT_RE = re.compile(
    r"\\(?:captionsetup|caption|includegraphics|begin\{(?:figure|table|tabular)\}"
    r"|end\{(?:figure|table|tabular)\})",
    re.IGNORECASE,
)
_TASK_CONTAMINATION_RE = re.compile(
    r"(?i)(?:\bnew\s+words?\b|\\begin\{figure\}|\\captionsetup\b|"
    r"\\caption\b|\\includegraphics\b)"
)
_TABLE_HINT_RE = re.compile(
    r"\\hline\b|\\begin\{tabular\}|"
    r"\{\s*\|?(?:[lcrpmbX]\|?){2,}\s*\}",
    re.IGNORECASE,
)
_TABLE_COLUMN_SPEC_RE = re.compile(
    r"\{\s*\|?(?:[lcrpmbX]\|?){2,}\s*\}",
    re.IGNORECASE,
)


def normal_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def is_task_like(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text and ("?" in text or _IMPERATIVE_RE.match(text)))


def strip_leading_task_cue(value: object) -> str:
    text = str(value or "").strip()
    match = _CUE_RE.match(text)
    return (match.group("body") if match else text).strip()


def normalize_task_table_markup(value: object) -> str:
    """Convert Mathpix tabular layout to readable, non-LaTeX task text."""
    text = str(value or "")
    if not _TABLE_HINT_RE.search(text):
        return text
    text = re.sub(
        r"\\begin\{tabular\}\{[^{}]*\}",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\\end\{tabular\}", "\n", text, flags=re.IGNORECASE)
    text = _TABLE_COLUMN_SPEC_RE.sub("\n", text)
    text = re.sub(r"\\hline\b", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\\\\(?=\s|$)", "\n", text)
    text = re.sub(r"\s*&\s*", " | ", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip(" |	") for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def canonical_task_display(value: object) -> str:
    """Return task text with safe table layout and canonical rich text only."""
    rendered = kr.canonicalize_rich_text(
        normalize_task_table_markup(value)
    ).strip()
    issues = set(kr.rich_text_issues(rendered))
    if issues and issues.issubset({"raw_latex", "raw_math_expression"}):
        repaired = kr.repair_unwrapped_math(rendered)
        if (
            not kr.rich_text_issues(repaired)
            and kr.unwrap_katex(repaired) == kr.unwrap_katex(rendered)
        ):
            rendered = repaired
    return kr.canonicalize_rich_text(rendered).strip()


def prompt_from_block(value: object) -> str:
    """Extract only the contiguous task prompt from one source block."""
    text = strip_leading_task_cue(value)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    start = next(
        (index for index, line in enumerate(lines) if is_task_like(line)),
        None,
    )
    if start is None:
        return ""
    chosen: list[str] = []
    for line in lines[start:]:
        if chosen and (_GLOSSARY_RE.match(line) or _LAYOUT_RE.search(line)):
            break
        if chosen and not is_task_like(line):
            break
        chosen.append(line)
    return " ".join(chosen).strip()


def numbered_heading_inventory(
    canonical: dict[str, Any],
) -> tuple[dict[int, dict], dict[int, list[dict]]]:
    mains: dict[int, dict] = {}
    subsections: dict[int, list[dict]] = {}
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict) or block.get("kind") != "heading":
            continue
        raw = str(block.get("raw_text") or "")
        main = _MAIN_HEADING_RE.search(raw) or _MD_MAIN_HEADING_RE.search(raw)
        if main:
            mains[int(main.group("number"))] = block
        sub = _SUB_HEADING_RE.search(raw) or _MD_SUB_HEADING_RE.search(raw)
        if sub:
            subsections.setdefault(int(sub.group("major")), []).append(block)
    return mains, subsections


def section_integrity_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    mains, subsections = numbered_heading_inventory(canonical)
    issues: list[dict[str, Any]] = []
    # Some textbooks number only subsections using the external chapter number
    # (for example 11.1–11.8 under an unnumbered "Electricity" heading). A
    # missing parent is provable only after the source establishes at least one
    # numbered main-section sequence of its own.
    if not mains:
        return issues
    for major, blocks in sorted(subsections.items()):
        if major in mains:
            continue
        issues.append({
            "severity": "error",
            "code": "phase21_missing_numbered_parent_section",
            "message": (
                f"Subsections {major}.x exist, but the numbered Section {major} "
                "heading is missing from the Mathpix MMD."
            ),
            "section_number": major,
            "subsection_block_ids": [block.get("block_id") for block in blocks],
            "source_start": min(
                int(block.get("source_start") or 0) for block in blocks
            ),
        })
    numbers = sorted(mains)
    if numbers[0] == 1:
        for number in range(1, numbers[-1] + 1):
            if number not in mains:
                issues.append({
                    "severity": "error",
                    "code": "phase21_numbered_section_gap",
                    "message": (
                        f"The numbered chapter sequence jumps over Section {number}."
                    ),
                    "section_number": number,
                })
    return issues


def topic_for_position(canonical: dict[str, Any], position: int) -> str:
    mains, _subsections = numbered_heading_inventory(canonical)
    ordered = sorted(
        (
            int(block.get("source_start") or 0),
            str((block.get("heading") or {}).get("title") or "").strip(),
        )
        for block in mains.values()
    )
    if not ordered:
        return ""
    prior = [title for start, title in ordered if start <= position and title]
    return prior[-1] if prior else ordered[0][1]


def task_matches_prompt(task: dict[str, Any], prompt: str) -> bool:
    prompt_key = normal_text(prompt)
    if not prompt_key:
        return False
    for field in ("raw_prompt", "display_prompt"):
        task_key = normal_text(task.get(field) or "")
        if not task_key:
            continue
        if task_key == prompt_key:
            return True
        if min(len(task_key), len(prompt_key)) >= 30 and (
            task_key in prompt_key or prompt_key in task_key
        ):
            return True
    return False


def recover_plain_task_cues(canonical: dict[str, Any]) -> int:
    """Recover cues emitted by Mathpix as plain text inside a source block."""
    tasks = [
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ]
    sections = {
        str(section.get("section_id") or ""): section
        for section in canonical.get("sections") or []
        if isinstance(section, dict)
    }
    recovered = 0
    for block in canonical.get("blocks") or []:
        if (
            not isinstance(block, dict)
            or block.get("kind") not in {"paragraph", "list"}
        ):
            continue
        match = _CUE_RE.match(str(block.get("raw_text") or ""))
        if not match:
            continue
        prompt = prompt_from_block(block.get("raw_text") or "")
        if not is_task_like(prompt) or any(
            task_matches_prompt(task, prompt) for task in tasks
        ):
            continue
        section_id = str(block.get("section_id") or "")
        section = sections.get(section_id, {})
        cue = str(match.group("cue") or "").strip()
        start_in_block = str(block.get("raw_text") or "").find(match.group("body"))
        source_start = int(block.get("source_start") or 0) + max(0, start_in_block)
        activity = cue.casefold() in {"activity", "project"}
        tasks.append({
            "task_id": "",
            "qid": "",
            "order": 0,
            "order_index": 0,
            "source_kind": "activity" if activity else "checkpoint_question",
            "source_label": cue,
            "parent_source_label": cue,
            "topic_hint": topic_for_position(canonical, source_start),
            "raw_prompt": prompt,
            "display_prompt": canonical_task_display(prompt),
            "identity_key": "",
            "section_id": section_id,
            "source_section_index": max(0, int(section.get("order") or 1) - 1),
            "source_position": max(
                0, source_start - int(section.get("source_start") or 0)
            ),
            "source_start": source_start,
            "source_end": int(block.get("source_end") or source_start + len(prompt)),
            "source_location_confidence": "phase21_plain_task_cue",
            "chapter_wide": cue.casefold() in {"project", "write in brief"},
            "activity_origin": activity,
            "requires_visual": False,
            "requires_context": False,
            "image_urls": [],
            "figure_refs": [],
            "explicit_figure_reference_ids": [],
            "unresolved_figure_reference_ids": [],
            "ambiguous_figure_reference_ids": [],
            "display_overrides": [],
            "phase21_recovery": {
                "reason": "plain_text_task_cue",
                "block_id": block.get("block_id"),
            },
        })
        recovered += 1
    canonical["tasks"] = tasks
    return recovered


def related_task_blocks(
    canonical: dict[str, Any], task: dict[str, Any]
) -> list[dict[str, Any]]:
    task_id = str(task.get("task_id") or "")
    start = int(task.get("source_start") or 0)
    end = int(task.get("source_end") or start)
    related: list[dict[str, Any]] = []
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if task_id and task_id in (block.get("task_ids") or []):
            related.append(block)
            continue
        block_start = int(block.get("source_start") or 0)
        block_end = int(block.get("source_end") or block_start)
        if start < block_end and end > block_start:
            related.append(block)
    return related


def trim_task_boundaries(canonical: dict[str, Any]) -> int:
    """Remove glossary, narrative and layout tails swallowed by a task."""
    repaired = 0
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        candidates: list[tuple[int, int, str, dict[str, Any]]] = []
        for block in related_task_blocks(canonical, task):
            if block.get("kind") not in {"paragraph", "list"}:
                continue
            prompt = prompt_from_block(block.get("raw_text") or "")
            if is_task_like(prompt):
                candidates.append((
                    int(block.get("source_start") or 0),
                    len(prompt),
                    prompt,
                    block,
                ))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (item[0], item[1]))
        _start, _length, candidate, block = candidates[0]
        current = strip_leading_task_cue(task.get("raw_prompt") or "")
        current_key = normal_text(current)
        candidate_key = normal_text(candidate)
        polluted = bool(_TASK_CONTAMINATION_RE.search(current))
        contained = bool(
            candidate_key and candidate_key != current_key and candidate_key in current_key
        )
        overly_long = len(current) > max(
            len(candidate) + 160, int(len(candidate) * 1.8)
        )
        source_kind = str(task.get("source_kind") or "").strip().lower()
        if source_kind == "exercise" and not polluted:
            continue
        if not (polluted or contained or overly_long or current.startswith("## ")):
            continue
        task.setdefault("phase21_boundary_repairs", []).append({
            "reason": "removed_non_task_source_tail",
            "block_id": block.get("block_id"),
            "raw_prompt_sha256": sha256_text(task.get("raw_prompt") or ""),
        })
        task.setdefault("raw_prompt_original", task.get("raw_prompt") or "")
        task["raw_prompt"] = candidate
        task["display_prompt"] = canonical_task_display(candidate)
        task["source_start"] = int(
            block.get("source_start") or task.get("source_start") or 0
        )
        task["source_end"] = int(
            block.get("source_end") or task.get("source_end") or 0
        )
        task["source_location_confidence"] = "phase21_prompt_block"
        repaired += 1
    return repaired


def renumber_tasks(canonical: dict[str, Any]) -> None:
    tasks = [
        task for task in canonical.get("tasks") or [] if isinstance(task, dict)
    ]
    tasks.sort(key=lambda task: (
        int(task.get("source_start") or 0),
        int(task.get("source_position") or 0),
        normal_text(task.get("raw_prompt") or ""),
    ))
    old_to_new: dict[str, str] = {}
    for order, task in enumerate(tasks, start=1):
        old_id = str(task.get("task_id") or "")
        new_id = f"TASK-{order:05d}"
        if old_id:
            old_to_new[old_id] = new_id
        task["task_id"] = new_id
        task["qid"] = f"QINV-{order:04d}"
        task["order"] = order
        task["order_index"] = order
        material = "\u241f".join([
            str(task.get("section_id") or ""),
            str(task.get("source_start") or 0),
            str(task.get("raw_prompt") or ""),
        ])
        task["identity_key"] = sha256_text(material)
    for block in canonical.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        previous = [str(value) for value in block.get("task_ids") or [] if value]
        if previous:
            block.setdefault("source_task_ids", previous)
        block["task_ids"] = [
            old_to_new[value] for value in previous if value in old_to_new
        ]
    for task in tasks:
        start = int(task.get("source_start") or 0)
        end = int(task.get("source_end") or start)
        for block in canonical.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            block_start = int(block.get("source_start") or 0)
            block_end = int(block.get("source_end") or block_start)
            if start < block_end and end > block_start:
                ids = block.setdefault("task_ids", [])
                if task["task_id"] not in ids:
                    ids.append(task["task_id"])
    canonical["tasks"] = tasks
    if isinstance(canonical.get("statistics"), dict):
        canonical["statistics"]["tasks"] = len(tasks)


def task_boundary_issues(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for task in canonical.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if _TASK_CONTAMINATION_RE.search(str(task.get("raw_prompt") or "")):
            issues.append({
                "severity": "error",
                "code": "phase21_task_boundary_contamination",
                "message": (
                    "A canonical task still contains glossary or Mathpix layout content."
                ),
                "qid": task.get("qid") or "",
            })
    for block in canonical.get("blocks") or []:
        if (
            not isinstance(block, dict)
            or block.get("kind") not in {"paragraph", "list"}
        ):
            continue
        if not _CUE_RE.match(str(block.get("raw_text") or "")):
            continue
        prompt = prompt_from_block(block.get("raw_text") or "")
        owners = [
            task for task in canonical.get("tasks") or []
            if isinstance(task, dict) and task_matches_prompt(task, prompt)
        ]
        if len(owners) != 1:
            issues.append({
                "severity": "error",
                "code": "phase21_task_cue_coverage_mismatch",
                "message": (
                    "A source task cue does not map to exactly one canonical task."
                ),
                "block_id": block.get("block_id"),
                "owner_count": len(owners),
            })
    return issues
