"""Legacy deterministic chapter-level refinement of concept-mapping output.

No longer invoked during concept deposit — chapter-wide refinement (Types
discipline, culminations, dedup, naming variety) is handled by the API
consolidation pass in ``generation._consolidate_concepts_via_api``. Kept for
unit-test coverage of the old renumbering helpers.
"""
from __future__ import annotations

import re

_SECTION_SEP = " // "
# Matches a Type/Case token, optionally already prefixed with "Miscellaneous "
# (so re-runs never stack the prefix).
_TYPE_CASE_RE = re.compile(r"(?:Miscellaneous\s+)?(Type|Case)\s*0*\d+", re.IGNORECASE)


def is_culmination(title: str) -> bool:
    return (title or "").strip().lower().startswith("culmination")


def split_sections(details: str) -> list[tuple[str, str]]:
    """Split ``Label: content // Label: content`` into ordered (label, content)."""
    out: list[tuple[str, str]] = []
    for part in (details or "").split(_SECTION_SEP):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            label, content = part.split(":", 1)
            out.append((label.strip(), content.strip()))
        else:
            out.append((part, ""))
    return out


def join_sections(sections: list[tuple[str, str]]) -> str:
    return _SECTION_SEP.join(
        f"{label}: {content}".rstrip() if content else f"{label}:"
        for label, content in sections
    )


def _find_types(sections: list[tuple[str, str]]) -> int:
    for i, (label, _) in enumerate(sections):
        if label.strip().lower().startswith("type"):
            return i
    return -1


def _renumber_block(text: str, start_type: int, *, type_label: str = "Type") -> tuple[str, int]:
    """Renumber tokens; Type carries ``type_label``, Case restarts inside each Type."""
    state = {"type": start_type, "case": 0}

    def repl(m: re.Match) -> str:
        kind = m.group(1).lower()
        if kind == "type":
            state["type"] += 1
            state["case"] = 0
            return f"{type_label} {state['type']:02d}"
        state["case"] += 1
        return f"Case {state['case']:02d}"

    return _TYPE_CASE_RE.sub(repl, text), state["type"]


def reduce_type_sections(details: str) -> str:
    """Drop a ``Types:`` block that declares types with NO concrete Case.

    Such blocks are low-value theory placeholders; purely theoretical concepts
    keep only Description (+ Misconception).
    """
    sections = split_sections(details)
    idx = _find_types(sections)
    if idx < 0:
        return details
    content = sections[idx][1]
    if not content.strip() or not re.search(r"\bCase\s*0*\d+", content, re.IGNORECASE):
        sections.pop(idx)
        return join_sections(sections)
    return details


def renumber_types_continuously(records: list[dict]) -> list[dict]:
    """Renumber Types continuously across the chapter.

    Two independent, chapter-wide continuous sequences:
      * regular concepts  -> "Type 01", "Type 02", ...
      * culmination rows  -> "Miscellaneous Type 01", "Miscellaneous Type 02", ...
    Neither advances the other; Case restarts within each Type.
    """
    counter = 0
    misc_counter = 0
    for rec in records:
        details = rec.get("concept_details") or ""
        sections = split_sections(details)
        idx = _find_types(sections)
        if idx < 0:
            continue
        label, content = sections[idx]
        if is_culmination(rec.get("concept_title", "")):
            new_content, misc_counter = _renumber_block(
                content, misc_counter, type_label="Miscellaneous Type")
        else:
            new_content, counter = _renumber_block(content, counter, type_label="Type")
        sections[idx] = (label, new_content)
        rec["concept_details"] = join_sections(sections)
    return records


def refine_chapter(records: list[dict]) -> list[dict]:
    """Full deterministic refinement pass over a chapter's ordered records."""
    for rec in records:
        if rec.get("concept_details"):
            rec["concept_details"] = reduce_type_sections(rec["concept_details"])
    return renumber_types_continuously(records)
