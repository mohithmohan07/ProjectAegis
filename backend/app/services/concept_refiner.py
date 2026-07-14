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
4. **Culmination description = detailed "Recap of ...".** Culmination rows keep
   their Types and Misconception, but their Description section is replaced
   with "Recap of <A>, <B> and <C>" listing the topic's merged concepts.
5. **"Achieving Mastery" statement on its own line.** A mastery statement at
   the end of a Description is normalized to a line-broken
   ``\\nAchieving Mastery: <statement>`` format.
6. **Misconceptions are always present on normal concepts.** If the model omits
   the section, a deterministic concept-specific fallback is appended.

``concept_details`` is the canonical
``Description: ... // Activity/Info Hub: ... // Types: ... // Misconception: ...``
string (sections joined by " // "). Activity/Info Hub is optional and holds
textbook activities, experiments, discussion cases, and other excess source
material that must not overload Culmination or become vague Cases.
"""
from __future__ import annotations

import re

_SECTION_SEP = " // "
# Matches a Type/Case token, optionally already prefixed with "Miscellaneous "
# (so re-runs never stack the prefix).
_TYPE_CASE_RE = re.compile(r"(?:Miscellaneous\s+)?(Type|Case)\s*0*\d+", re.IGNORECASE)
_ACTIVITY_HUB_LABEL = "Activity/Info Hub"


def is_culmination(title: str) -> bool:
    return (title or "").strip().lower().startswith("culmination")


def is_activity_hub_label(label: str) -> bool:
    key = re.sub(r"[\s/]+", "", (label or "").strip().lower())
    return key.startswith("activityinfohub") or key.startswith("activityhub")


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


def recap_text(concept_titles: list[str]) -> str:
    """Detailed culmination Description listing the merged concepts."""
    names = [n.strip() for n in concept_titles if n and n.strip()]
    if not names:
        return "Recap"
    if len(names) == 1:
        joined = names[0]
    else:
        joined = ", ".join(names[:-1]) + " and " + names[-1]
    return f"Recap of {joined}."


def set_culmination_recap(records: list[dict]) -> list[dict]:
    """Give every culmination row a detailed "Recap of <A>, <B> and <C>."
    Description naming the topic's merged (non-culmination) concepts.

    Types and Misconception are left untouched. A culmination with no
    Description section gets one prepended.
    """
    titles_by_topic: dict[str, list[str]] = {}
    for rec in records:
        title = rec.get("concept_title", "")
        if not is_culmination(title):
            titles_by_topic.setdefault(rec.get("topic", ""), []).append(title)
    for rec in records:
        if not is_culmination(rec.get("concept_title", "")):
            continue
        recap = recap_text(titles_by_topic.get(rec.get("topic", ""), []))
        sections = split_sections(rec.get("concept_details") or "")
        found = False
        for i, (label, _content) in enumerate(sections):
            if label.strip().lower().startswith("description"):
                sections[i] = ("Description", recap)
                found = True
                break
        if not found:
            sections.insert(0, ("Description", recap))
        rec["concept_details"] = join_sections(sections)
    return records


# A mastery statement at the tail of a Description. Accepts the label variants
# models produce ("Achieving Mastery:", "Mastery:", "Mastery indicator:") and
# normalizes all of them to a line-broken "Achieving Mastery: ..." format.
_MASTERY_LABEL_RE = re.compile(
    r"\s*(?:achieving\s+mastery|mastery(?:\s+indicators?)?)\s*[:\-]\s*",
    re.IGNORECASE,
)
# Inline misconception markers inside the Description body (before a separate section).
_INLINE_MISCONCEPTION_RE = re.compile(
    r"\s*(?://\s*)?(?:Misconception(?:s)?|Common (?:error|misconception))\s*[:\-]\s*",
    re.IGNORECASE,
)
# Generic fallback text from ``_fallback_misconception`` — reviewers asked to drop
# the second, post-mastery copy when the model duplicates it.
_GENERIC_MISCONCEPTION_RE = re.compile(
    r"^Students may apply .+ as a memorized rule without checking "
    r"the conditions, context, or representation given in the problem\.?$",
    re.IGNORECASE,
)


def format_mastery_statement(details: str) -> str:
    """Put the Description's mastery statement on its own line.

    ``... Achieving Mastery: <statement>`` (any label variant, any spacing)
    becomes ``...\\nAchieving Mastery: <statement>``. Only the Description
    section is touched; nothing is invented when no mastery label exists.

    When the model wrote TWO mastery statements (review feedback: one before
    Misconceptions and one after), the SECOND is kept — the first tends to be
    a formulaic "applying X to problems" line, while the second is written
    with the concept's actual content in view.
    """
    sections = split_sections(details)
    for i, (label, content) in enumerate(sections):
        if not label.strip().lower().startswith("description"):
            continue
        matches = list(_MASTERY_LABEL_RE.finditer(content))
        matches = [m for m in matches if content[m.end():].strip()]
        if not matches:
            break
        first, last = matches[0], matches[-1]
        body = content[:first.start()].rstrip()
        statement = content[last.end():].strip()
        if not statement:
            statement = content[first.end():last.start()].strip()
        sections[i] = (label, f"{body}\nAchieving Mastery: {statement}")
        return join_sections(sections)
    return details


def _misconception_index(sections: list[tuple[str, str]]) -> int:
    for i, (label, _) in enumerate(sections):
        if label.strip().lower().startswith("misconception"):
            return i
    return -1


def _fallback_misconception(title: str) -> str:
    concept = (title or "this concept").strip().rstrip(".")
    return (
        f"Students may apply {concept} as a memorized rule without checking "
        "the conditions, context, or representation given in the problem."
    )


def _is_generic_misconception(text: str) -> bool:
    return bool(_GENERIC_MISCONCEPTION_RE.match((text or "").strip()))


def normalize_misconception_sections(details: str) -> str:
    """Keep exactly one Misconceptions SECTION in canonical order.

    Review feedback: misconception text appeared twice — inline in Description
    (sometimes after Achieving Mastery) AND as a separate section. We strip
    inline copies from Description, merge every section into ONE Misconceptions
    section, and enforce Description // Types // Misconceptions order.

    A concept may legitimately carry MORE THAN ONE real misconception, so all
    distinct specific misconception texts are kept (joined in one section);
    only generic filler duplicates of a specific one are dropped.
    """
    sections = split_sections(details)
    if not sections:
        return details

    misconception_texts: list[str] = []
    stray_mastery = ""

    def _collect(text: str) -> None:
        nonlocal stray_mastery
        text = (text or "").strip()
        if not text:
            return
        # A mastery statement drifted into the misconception text (review:
        # mastery appearing again after Misconceptions) — pull it back out.
        m = _MASTERY_LABEL_RE.search(text)
        if m:
            tail = text[m.end():].strip()
            if tail:
                stray_mastery = tail
            text = text[:m.start()].strip()
        if text:
            misconception_texts.append(text)

    cleaned: list[tuple[str, str]] = []
    for label, content in sections:
        lower = label.strip().lower()
        if lower.startswith("misconception"):
            _collect(content)
            continue
        if lower.startswith("description"):
            body = content
            # Remove inline misconception before or after the mastery line.
            m_mastery = _MASTERY_LABEL_RE.search(body)
            if m_mastery:
                post_mastery = body[m_mastery.end():]
                inline = _INLINE_MISCONCEPTION_RE.search(post_mastery)
                if inline:
                    _collect(post_mastery[inline.end():])
                    post_mastery = post_mastery[:inline.start()].rstrip()
                body = body[:m_mastery.start()] + body[m_mastery.start():m_mastery.end()] + post_mastery
            inline_pre = _INLINE_MISCONCEPTION_RE.search(body)
            if inline_pre:
                _collect(body[inline_pre.end():])
                body = body[:inline_pre.start()].rstrip()
            cleaned.append((label, body))
            continue
        cleaned.append((label, content))

    # Keep every distinct specific misconception; generic filler survives only
    # when no specific one exists.
    specific: list[str] = []
    seen: set[str] = set()
    for text in misconception_texts:
        key = re.sub(r"\W+", " ", text.lower()).strip()
        if key and key not in seen and not _is_generic_misconception(text):
            seen.add(key)
            specific.append(text)
    if not specific and misconception_texts:
        specific = [misconception_texts[0]]
    chosen = " ".join(specific)

    ordered: list[tuple[str, str]] = []
    hub_block: tuple[str, str] | None = None
    types_block: tuple[str, str] | None = None
    for label, content in cleaned:
        lower = label.strip().lower()
        if lower.startswith("type"):
            types_block = (label, content)
        elif is_activity_hub_label(label):
            if content.strip():
                hub_block = (_ACTIVITY_HUB_LABEL, content.strip())
        else:
            ordered.append((label, content))
    # A mastery statement extracted from the misconception text replaces the
    # Description's existing one (the reviewers prefer the later statement).
    if stray_mastery:
        for i, (label, content) in enumerate(ordered):
            if not label.strip().lower().startswith("description"):
                continue
            m = _MASTERY_LABEL_RE.search(content)
            body = content[:m.start()].rstrip() if m else content.rstrip()
            ordered[i] = (label, f"{body}\nAchieving Mastery: {stray_mastery}")
            break
    # Canonical order: Description (+ mastery) → Activity/Info Hub → Types →
    # Misconceptions. Hub sits before Types so assessable Cases stay conceptual.
    if hub_block:
        ordered.append(hub_block)
    if types_block:
        ordered.append(types_block)
    if chosen:
        ordered.append(("Misconceptions", chosen))
    return join_sections(ordered)


def append_activity_hub(details: str, hub_text: str) -> str:
    """Append or extend the Activity/Info Hub section before Types."""
    text = (hub_text or "").strip()
    if not text:
        return details
    sections = split_sections(details or "")
    for i, (label, content) in enumerate(sections):
        if is_activity_hub_label(label):
            existing = (content or "").strip()
            if text in existing:
                return details
            merged = f"{existing} {text}".strip() if existing else text
            sections[i] = (_ACTIVITY_HUB_LABEL, merged)
            return join_sections(sections)

    out: list[tuple[str, str]] = []
    inserted = False
    for label, content in sections:
        lower = label.strip().lower()
        if not inserted and (
            lower.startswith("type") or lower.startswith("misconception")
        ):
            out.append((_ACTIVITY_HUB_LABEL, text))
            inserted = True
        out.append((label, content))
    if not inserted:
        out.append((_ACTIVITY_HUB_LABEL, text))
    return join_sections(out)


def activity_hub_body(details: str) -> str:
    for label, content in split_sections(details or ""):
        if is_activity_hub_label(label):
            return (content or "").strip()
    return ""


def split_merged_description_blocks(details: str) -> str:
    """When a cell accidentally concatenates multiple concepts, keep the first."""
    raw = (details or "").strip()
    if not raw:
        return raw
    parts = re.split(r"(?<=[.!?])\s*(?=Description\s*:)", raw, flags=re.IGNORECASE)
    if len(parts) <= 1:
        return raw
    first = parts[0].strip()
    return first if first.lower().startswith("description:") else raw


def ensure_misconceptions(records: list[dict]) -> list[dict]:
    """Append a Misconceptions section to every normal concept when missing."""
    for rec in records:
        if is_culmination(rec.get("concept_title", "")):
            continue
        details = rec.get("concept_details") or ""
        if not details.strip():
            continue
        sections = split_sections(details)
        idx = _misconception_index(sections)
        fallback = _fallback_misconception(rec.get("concept_title", ""))
        if idx < 0:
            sections.append(("Misconceptions", fallback))
        elif not sections[idx][1].strip():
            sections[idx] = (sections[idx][0] or "Misconceptions", fallback)
        else:
            continue
        rec["concept_details"] = join_sections(sections)
    return records


def refine_chapter(records: list[dict]) -> list[dict]:
    """Full deterministic refinement pass over a chapter's ordered records."""
    for rec in records:
        if rec.get("concept_details"):
            details = split_merged_description_blocks(rec["concept_details"])
            details = reduce_type_sections(details)
            if not is_culmination(rec.get("concept_title", "")):
                details = format_mastery_statement(details)
            details = normalize_misconception_sections(details)
            rec["concept_details"] = details
    records = ensure_misconceptions(records)
    records = renumber_types_continuously(records)
    return set_culmination_recap(records)
