"""Deterministic chapter-level refinement of concept-mapping output.

Runs on the full, ordered list of concept records for a chapter right before
they are deposited, so the stored Bulk Import rows carry the exact format the
team requires regardless of which extractor produced them:

1. **Continuous Type numbering.** Extractors restart ``Type 01`` inside every
   concept. We renumber ``Type NN`` continuously across the whole chapter (in
   textbook/topic order); ``Case NN`` restarts within each Type.
2. **Culmination concepts use a separate "Miscellaneous Type NN" sequence**
   that is ALSO continuous across the whole chapter, and never advances (or is
   advanced by) the regular Type counter.
3. **Type reduction for theory.** Purely theoretical concepts should not carry
   a Types section; we drop any ``Types:`` block that has no concrete ``Case``.
4. **Culmination description = "Recap".** Culmination rows keep their Types and
   Misconception, but their Description section is replaced with just "Recap".

``concept_details`` is the canonical ``Description: ... // Types: ... //
Misconception: ...`` string (sections joined by " // ").
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


def set_culmination_recap(records: list[dict]) -> list[dict]:
    """Replace the Description of every culmination row with just "Recap".

    Types and Misconception are left untouched. A culmination with no Description
    section gets one prepended.
    """
    for rec in records:
        if not is_culmination(rec.get("concept_title", "")):
            continue
        sections = split_sections(rec.get("concept_details") or "")
        found = False
        for i, (label, _content) in enumerate(sections):
            if label.strip().lower().startswith("description"):
                sections[i] = ("Description", "Recap")
                found = True
                break
        if not found:
            sections.insert(0, ("Description", "Recap"))
        rec["concept_details"] = join_sections(sections)
    return records


def refine_chapter(records: list[dict]) -> list[dict]:
    """Full deterministic refinement pass over a chapter's ordered records."""
    for rec in records:
        if rec.get("concept_details"):
            rec["concept_details"] = reduce_type_sections(rec["concept_details"])
    records = renumber_types_continuously(records)
    return set_culmination_recap(records)
