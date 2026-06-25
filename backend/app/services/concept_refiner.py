"""Deterministic chapter-level refinement of concept-mapping output.

Runs on the full, ordered list of concept records for a chapter (dry OR live,
every subject) right before they are deposited, so the stored Bulk Import rows
are consistent regardless of which extractor produced them. It addresses three
reviewed defects that are safe to fix without an LLM:

1. **Continuous Type numbering.** The extractor restarts ``Type 01`` inside
   every concept. We renumber ``Type NN`` continuously across the whole
   chapter (in textbook/topic order); ``Case NN`` restarts within each Type.
2. **Culmination concepts are excluded** from the continuous sequence — their
   Types are numbered independently so they never advance the chapter counter.
3. **Type reduction for theory.** Purely theoretical concepts should not carry
   a Types section at all; we drop any ``Types:`` block that has no concrete
   ``Case`` (the prompt already omits them — this is the safety net).

``concept_details`` is the canonical ``Description: ... // Types: ... //
Misconception: ...`` string (sections joined by " // ").
"""
from __future__ import annotations

import re

_SECTION_SEP = " // "
_TYPE_CASE_RE = re.compile(r"\b(Type|Case)\s*0*\d+", re.IGNORECASE)


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


def _renumber_block(text: str, start_type: int) -> tuple[str, int]:
    """Renumber ``Type``/``Case`` tokens; Case restarts at 01 inside each Type."""
    state = {"type": start_type, "case": 0}

    def repl(m: re.Match) -> str:
        kind = m.group(1).lower()
        if kind == "type":
            state["type"] += 1
            state["case"] = 0
            return f"Type {state['type']:02d}"
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
    """Renumber Types continuously across the chapter, skipping culminations."""
    counter = 0
    for rec in records:
        details = rec.get("concept_details") or ""
        sections = split_sections(details)
        idx = _find_types(sections)
        if idx < 0:
            continue
        label, content = sections[idx]
        if is_culmination(rec.get("concept_title", "")):
            # Independent numbering — never advances the chapter counter.
            new_content, _ = _renumber_block(content, 0)
        else:
            new_content, counter = _renumber_block(content, counter)
        sections[idx] = (label, new_content)
        rec["concept_details"] = join_sections(sections)
    return records


def refine_chapter(records: list[dict]) -> list[dict]:
    """Full deterministic refinement pass over a chapter's ordered records."""
    for rec in records:
        if rec.get("concept_details"):
            rec["concept_details"] = reduce_type_sections(rec["concept_details"])
    return renumber_types_continuously(records)
