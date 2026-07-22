"""Deterministic chapter-level refinement of concept-mapping output.

Runs on the full, ordered list of concept records for a chapter right before
they are deposited, so the stored Bulk Import rows carry the exact format the
team requires regardless of which extractor produced them:

1. **Stable reusable Type numbering.** Extractors restart ``Type 01`` inside
   every concept. We allocate numbers in textbook/topic order, but semantically
   consolidated Types rendered on more than one concept retain the SAME
   ``Type NN``. Their ``Case NN`` sequence continues across those concepts.
2. **Culmination concepts use a separate "Miscellaneous Type NN" sequence**
   that is ALSO continuous across the whole chapter, and never advances (or is
   advanced by) the regular Type counter.
3. **Type reduction for theory.** Purely theoretical concepts should not carry
   a Types section; we drop any ``Types:`` block that has no concrete ``Case``.
4. **Culmination description = detailed "Recap of ...".** Culmination rows keep
   their Types and any analysis sections, but their Description is replaced
   with "Recap of <A>, <B> and <C>" listing the topic's merged concepts.
5. **"Achieving Mastery" statement on its own line.** A mastery statement at
   the end of a Description is normalized to a line-broken
   ``\\nAchieving Mastery: <statement>`` format.
6. **Learner analysis is always present on normal concepts.** Each normal
   concept ends with at least one of ``Misconceptions`` (a commonly held but
   incorrect belief or interpretation) and ``Error Analysis`` (a plausible
   procedural, computational, representational, or reasoning mistake). Either
   section may appear alone, or both may appear when they add distinct value.

``concept_details`` is the canonical
``Description: ... // Activity/Info Hub: ... // Types: ... // Misconceptions:
... // Error Analysis: ...`` string (sections joined by " // "). Optional
sections are omitted. Activity/Info Hub holds textbook activities, experiments,
discussion cases, and other excess source material that must not overload
Culmination or become vague Cases.
"""
from __future__ import annotations

import re

_SECTION_SEP = " // "
# Matches a Type/Case token, optionally already prefixed with "Miscellaneous "
# (so re-runs never stack the prefix).
_TYPE_TOKEN_RE = re.compile(
    r"(?:Miscellaneous\s+)?Type\s*0*\d+\s*:", re.IGNORECASE)
_CASE_TOKEN_RE = re.compile(r"Case\s*0*\d+\s*:", re.IGNORECASE)
_ACTIVITY_HUB_LABEL = "Activity/Info Hub"
_MISCONCEPTIONS_LABEL = "Misconceptions"
_ERROR_ANALYSIS_LABEL = "Error Analysis"


def is_culmination(title: str) -> bool:
    return (title or "").strip().lower().startswith("culmination")


def is_activity_hub_label(label: str) -> bool:
    key = re.sub(r"[\s/]+", "", (label or "").strip().lower())
    return key.startswith("activityinfohub") or key.startswith("activityhub")


def is_misconception_label(label: str) -> bool:
    """Return True for canonical and legacy singular misconception labels."""
    key = re.sub(r"[^a-z]", "", (label or "").strip().lower())
    return key in {
        "misconception",
        "misconceptions",
        "commonmisconception",
        "commonmisconceptions",
    }


def is_error_analysis_label(label: str) -> bool:
    """Return True for Error Analysis and common model-produced aliases."""
    key = re.sub(r"[^a-z]", "", (label or "").strip().lower())
    return key in {
        "erroranalysis",
        "erroranalyses",
        "commonerror",
        "commonerrors",
        "possibleerror",
        "possibleerrors",
        "commonmistake",
        "commonmistakes",
        "possiblemistake",
        "possiblemistakes",
    }


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


def _type_signature(segment: str) -> str:
    """Normalized Type definition before its first Case token."""
    match = _TYPE_TOKEN_RE.match(segment)
    body = segment[match.end():] if match else segment
    case = _CASE_TOKEN_RE.search(body)
    header = body[:case.start()] if case else body
    # Repeated renderings of one consolidated Type carry the same header, so
    # whitespace normalization is sufficient. Preserve operators and LaTeX:
    # collapsing punctuation would conflate distinct methods such as a/b and
    # a-b and incorrectly share their Type number and Case sequence.
    return re.sub(r"\s+", " ", header.lower()).strip()


def _renumber_reusable_block(
    text: str,
    *,
    topic_key: str,
    type_label: str,
    number_by_signature: dict[tuple[str, str], int],
    case_count_by_signature: dict[tuple[str, str], int],
    next_number: int,
) -> tuple[str, int]:
    """Allocate stable Type numbers and continuous Cases by semantic header."""
    matches = list(_TYPE_TOKEN_RE.finditer(text or ""))
    if not matches:
        return text, next_number
    pieces: list[str] = [text[:matches[0].start()]]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.start():end]
        signature = _type_signature(segment)
        # A malformed/empty title must not accidentally share identity.
        if not signature:
            signature = f"__anonymous_{next_number + 1}_{index}"
        key = (topic_key, signature)
        number = number_by_signature.get(key)
        if number is None:
            next_number += 1
            number = next_number
            number_by_signature[key] = number
        segment = _TYPE_TOKEN_RE.sub(
            f"{type_label} {number:02d}:", segment, count=1)

        def replace_case(_match: re.Match) -> str:
            case_count_by_signature[key] = (
                case_count_by_signature.get(key, 0) + 1)
            return f"Case {case_count_by_signature[key]:02d}:"

        segment = _CASE_TOKEN_RE.sub(replace_case, segment)
        pieces.append(segment)
    return "".join(pieces), next_number


def reduce_type_sections(details: str) -> str:
    """Drop a ``Types:`` block that declares types with NO concrete Case.

    Such blocks are low-value theory placeholders; purely theoretical concepts
    keep Description and their applicable learner-analysis section(s).
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
    """Renumber Types continuously while preserving reusable Type identity.

    Two independent, chapter-wide continuous sequences:
      * regular concepts  -> "Type 01", "Type 02", ...
      * culmination rows  -> "Miscellaneous Type 01", "Miscellaneous Type 02", ...
    Neither advances the other. Within one source topic, repeated canonical
    Type definitions retain one number across concept rows and their Cases
    continue increasing instead of restarting.
    """
    counter = 0
    misc_counter = 0
    regular_numbers: dict[tuple[str, str], int] = {}
    regular_cases: dict[tuple[str, str], int] = {}
    misc_numbers: dict[tuple[str, str], int] = {}
    misc_cases: dict[tuple[str, str], int] = {}
    for rec in records:
        details = rec.get("concept_details") or ""
        sections = split_sections(details)
        idx = _find_types(sections)
        if idx < 0:
            continue
        label, content = sections[idx]
        topic_key = re.sub(
            r"\W+", " ", str(rec.get("topic") or "").lower()).strip()
        if is_culmination(rec.get("concept_title", "")):
            new_content, misc_counter = _renumber_reusable_block(
                content,
                topic_key=topic_key,
                type_label="Miscellaneous Type",
                number_by_signature=misc_numbers,
                case_count_by_signature=misc_cases,
                next_number=misc_counter,
            )
        else:
            new_content, counter = _renumber_reusable_block(
                content,
                topic_key=topic_key,
                type_label="Type",
                number_by_signature=regular_numbers,
                case_count_by_signature=regular_cases,
                next_number=counter,
            )
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

    Types, Misconceptions, and Error Analysis are left untouched. A culmination
    with no Description section gets one prepended.
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
# Inline learner-analysis markers inside Description or another section. The
# named group lets normalization keep beliefs separate from application errors.
_INLINE_ANALYSIS_RE = re.compile(
    r"\s*(?://\s*)?(?P<label>"
    r"(?:Common\s+)?Misconception(?:s)?|"
    r"Error\s+Analys(?:is|es)|"
    r"(?:Common|Possible)\s+(?:Error|Mistake)s?"
    r")\s*[:\-]\s*",
    re.IGNORECASE,
)
# Generic legacy fallback text; normalization drops a duplicate copy when a
# more specific learner misconception exists.
_GENERIC_MISCONCEPTION_RE = re.compile(
    r"^Students may apply .+ as a memorized rule without checking "
    r"the conditions, context, or representation given in the problem\.?$",
    re.IGNORECASE,
)
_LEARNER_FALSE_BELIEF_RE = re.compile(
    r"\b(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|often|sometimes|commonly)\s+)?"
    r"(?:(?:incorrectly|wrongly|mistakenly)\s+)?"
    r"(?:believe|think|assume|expect|confuse|mistake|treat|interpret|"
    r"misunderstand|misinterpret|regard|consider)\b",
    re.IGNORECASE,
)
_DECLARATIVE_NEGATION_RE = re.compile(
    r"^\s*(?:a|an|the|this|that)\b.{0,120}\b(?:is|are|does|do|can)\s+not\b",
    re.IGNORECASE,
)
_CORRECTION_AFTER_BELIEF_RE = re.compile(
    r"(?:[.!?;,]\s*)(?:but\s+)?(?:"
    r"in\s+fact\b|actually\b|instead\s*,|remember\s+that\b|"
    r"the\s+correct\s+(?:idea|rule|answer|method)\b|"
    r"(?:students?|learners?|children)\s+(?:should|must)\b|"
    r"(?:a|an|the|this|that)\b.{0,120}\b"
    r"(?:is|are|does|do|can)\s+not\b)",
    re.IGNORECASE,
)
_EXPLICIT_BELIEF_CUE_RE = re.compile(
    r"\b(?:believ\w*|think\w*|assum\w*|expect\w*|confus\w*|"
    r"mistak\w*|interpret\w*|misunderstand\w*|misinterpret\w*|"
    r"treat\w*|always|never|all|only)\b",
    re.IGNORECASE,
)
_APPLICATION_ERROR_CUE_RE = re.compile(
    r"\b(?:appl\w*|calculat\w*|comput\w*|substitut\w*|omit\w*|"
    r"skip\w*|revers\w*|swap\w*|round\w*|invert\w*|misread\w*|"
    r"cop\w*|draw\w*|label\w*|plot\w*|convert\w*|simplif\w*|"
    r"solv\w*|add\w*|subtract\w*|multip\w*|divid\w*|use\w*)\b",
    re.IGNORECASE,
)
_ANALYSIS_ERROR_ACTOR_RE = re.compile(
    r"\b(?:students?|learners?|children)\b|"
    r"\b(?:a\s+)?common\s+(?:error|mistake|misstep)\b",
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
        if is_misconception_label(label):
            return i
    return -1


def _error_analysis_index(sections: list[tuple[str, str]]) -> int:
    for i, (label, _) in enumerate(sections):
        if is_error_analysis_label(label):
            return i
    return -1


def _fallback_misconception(title: str) -> str:
    """Legacy name retained for callers; the text describes an application error."""
    return _fallback_error_analysis(title)


def _fallback_error_analysis(title: str) -> str:
    concept = (title or "this concept").strip().rstrip(".")
    return (
        f"Students may apply {concept} as a memorized rule without checking "
        "the conditions, context, or representation given in the problem."
    )


def _is_generic_misconception(text: str) -> bool:
    return bool(_GENERIC_MISCONCEPTION_RE.match((text or "").strip()))


def _is_correction_shaped_misconception(text: str) -> bool:
    """True when text teaches the correction instead of naming a false belief."""
    value = (text or "").strip()
    if not value:
        return False
    # Modal words such as "must" or "should" can be part of the false belief
    # itself ("Students may believe the denominator must also be added"). Only
    # correction language introduced after a clause/sentence boundary is
    # treated as teacher-facing repair prose.
    if not _LEARNER_FALSE_BELIEF_RE.search(value):
        return True
    return bool(_CORRECTION_AFTER_BELIEF_RE.search(value))


def _strip_misconception_correction_tail(text: str) -> str:
    """Keep the false belief while removing a following teacher correction."""
    value = (text or "").strip()
    if not _LEARNER_FALSE_BELIEF_RE.search(value):
        return value
    correction = _CORRECTION_AFTER_BELIEF_RE.search(value)
    if not correction:
        return value
    belief = value[:correction.start()].rstrip()
    if belief and not re.search(r"[.!?]\s*$", belief):
        belief += "."
    return belief


def _needs_misconception_rewrite(text: str) -> bool:
    return (
        not (text or "").strip()
        or _is_generic_misconception(text)
        or _is_correction_shaped_misconception(text)
    )


def _analysis_text_key(text: str) -> str:
    """Case- and punctuation-insensitive identity for exact content dedupe."""
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def _duplicate_belongs_to_misconceptions(text: str) -> bool:
    """Choose the more appropriate section for an exact cross-section copy."""
    # Explicit learner-belief syntax is the strongest signal. A broad word
    # such as "mistake" is not enough on its own: "Students may make the
    # mistake of dropping the sign" names an action and belongs in Error
    # Analysis, while "Students may mistake the sign for ..." names a belief.
    if _LEARNER_FALSE_BELIEF_RE.search(text or ""):
        return True
    if _APPLICATION_ERROR_CUE_RE.search(text or ""):
        return False
    if _EXPLICIT_BELIEF_CUE_RE.search(text or ""):
        return True
    return not _needs_misconception_rewrite(text)


def normalize_analysis_sections(details: str) -> str:
    """Normalize distinct Misconceptions and Error Analysis sections.

    Learner-analysis text can appear inline in Description and in repeated
    sections. Inline copies are removed, repeated sections of the same kind are
    consolidated, and the two different meanings are never merged together.

    Misconceptions are commonly held but incorrect beliefs or interpretations.
    Error Analysis captures plausible procedural, computational,
    representational, or reasoning mistakes made while applying the concept.
    The meanings remain separate; either section may appear alone, or both may
    appear in canonical order after Types.
    """
    sections = split_sections(details)
    if not sections:
        return details

    misconception_texts: list[str] = []
    error_analysis_texts: list[str] = []
    stray_mastery = ""

    def _kind_for_label(label: str) -> str | None:
        if is_misconception_label(label):
            return "misconception"
        if is_error_analysis_label(label):
            return "error_analysis"
        return None

    def _collect(kind: str, text: str) -> None:
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
        if text and kind == "misconception":
            misconception_texts.append(text)
        elif text and kind == "error_analysis":
            error_analysis_texts.append(text)

    def _collect_inline(text: str, default_kind: str | None = None) -> None:
        """Collect multiple inline labels without merging their meanings."""
        value = (text or "").strip()
        if not value:
            return
        matches = list(_INLINE_ANALYSIS_RE.finditer(value))
        if not matches:
            if default_kind:
                _collect(default_kind, value)
            return
        prefix = value[:matches[0].start()].strip()
        if prefix and default_kind:
            _collect(default_kind, prefix)
        for index, marker in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(value)
            )
            kind = _kind_for_label(marker.group("label"))
            if kind:
                _collect(kind, value[marker.end():end])

    cleaned: list[tuple[str, str]] = []
    for label, content in sections:
        lower = label.strip().lower()
        kind = _kind_for_label(label)
        if kind:
            _collect_inline(content, kind)
            continue
        if lower.startswith("description"):
            body = content
            # Remove one or more inline analysis blocks before or after mastery.
            inline = _INLINE_ANALYSIS_RE.search(body)
            if inline:
                _collect_inline(body[inline.start():])
                body = body[:inline.start()].rstrip()
            cleaned.append((label, body))
            continue
        cleaned.append((label, content))

    # Keep every distinct specific misconception; generic filler survives only
    # when no specific one exists.
    specific: list[str] = []
    unclassified_misconceptions: list[str] = []
    seen: set[str] = set()
    for text in misconception_texts:
        key = _analysis_text_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        if (
            not _is_generic_misconception(text)
            and not _is_correction_shaped_misconception(text)
        ):
            specific.append(text)
        elif _ANALYSIS_ERROR_ACTOR_RE.search(text):
            # Preserve legacy procedural mistakes that were stored under the
            # old Misconception-only contract. The final semantic boundary
            # validates or reclassifies the content without losing it.
            error_analysis_texts.append(text)
        else:
            unclassified_misconceptions.append(text)
    if not specific and unclassified_misconceptions:
        specific = [unclassified_misconceptions[0]]

    # Error Analysis follows a different semantic contract, so retain concise
    # mistake descriptions without requiring learner-belief phrasing.
    distinct_errors: list[str] = []
    seen_errors: set[str] = set()
    for text in error_analysis_texts:
        key = _analysis_text_key(text)
        if key and key not in seen_errors:
            seen_errors.add(key)
            distinct_errors.append(text)
    specific_errors = [
        text for text in distinct_errors if not _is_generic_misconception(text)
    ]
    if specific_errors:
        distinct_errors = specific_errors

    # If the model copied the exact same statement into both sections, retain
    # one copy under the category suggested by its wording. Do not deduplicate
    # merely similar texts: a related belief and application error may both be
    # instructionally useful.
    duplicate_keys = (
        {_analysis_text_key(text) for text in specific}
        & {_analysis_text_key(text) for text in distinct_errors}
    )
    for key in duplicate_keys:
        duplicate_text = next(
            text for text in specific if _analysis_text_key(text) == key
        )
        if _duplicate_belongs_to_misconceptions(duplicate_text):
            distinct_errors = [
                text for text in distinct_errors
                if _analysis_text_key(text) != key
            ]
        else:
            specific = [
                text for text in specific if _analysis_text_key(text) != key
            ]

    def _join_items(items: list[str]) -> str:
        if not items:
            return ""
        joined = items[0].strip()
        for item in items[1:]:
            if joined and not re.search(r"[.!?;]\s*$", joined):
                joined += "."
            joined += f" {item.strip()}"
        return joined

    chosen_misconceptions = _join_items(specific)
    chosen_errors = _join_items(distinct_errors)

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
    # A mastery statement extracted from either analysis section replaces the
    # Description's existing one (the reviewers prefer the later statement).
    if stray_mastery:
        for i, (label, content) in enumerate(ordered):
            if not label.strip().lower().startswith("description"):
                continue
            m = _MASTERY_LABEL_RE.search(content)
            body = content[:m.start()].rstrip() if m else content.rstrip()
            ordered[i] = (label, f"{body}\nAchieving Mastery: {stray_mastery}")
            break
    # Canonical order: Description (+ mastery), Activity/Info Hub, Types,
    # Misconceptions, Error Analysis. The hub stays before assessable Cases.
    if hub_block:
        ordered.append(hub_block)
    if types_block:
        ordered.append(types_block)
    if chosen_misconceptions:
        ordered.append((_MISCONCEPTIONS_LABEL, chosen_misconceptions))
    if chosen_errors:
        ordered.append((_ERROR_ANALYSIS_LABEL, chosen_errors))
    return join_sections(ordered)


def normalize_misconception_sections(details: str) -> str:
    """Backward-compatible entry point for learner-analysis normalization."""
    return normalize_analysis_sections(details)


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
        if not inserted and (
            label.strip().lower().startswith("type")
            or is_misconception_label(label)
            or is_error_analysis_label(label)
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


def ensure_analysis_sections(records: list[dict]) -> list[dict]:
    """Ensure every normal concept ends with Misconceptions or Error Analysis.

    Either populated section satisfies the contract. When both are absent or
    empty, a deterministic application mistake is added as Error Analysis;
    culmination rows remain exempt.
    """
    for rec in records:
        if is_culmination(rec.get("concept_title", "")):
            continue
        details = rec.get("concept_details") or ""
        # Preserve the historical behavior for wholly empty rows; upstream
        # validation is responsible for missing Description content.
        if not details.strip():
            continue
        details = normalize_analysis_sections(details)
        rec["concept_details"] = details
        sections = split_sections(details)
        misconception_idx = _misconception_index(sections)
        error_idx = _error_analysis_index(sections)
        has_misconception = (
            misconception_idx >= 0
            and bool(sections[misconception_idx][1].strip())
        )
        has_error_analysis = (
            error_idx >= 0 and bool(sections[error_idx][1].strip())
        )
        if has_misconception or has_error_analysis:
            continue
        sections = [
            (label, content)
            for label, content in sections
            if not is_misconception_label(label)
            and not is_error_analysis_label(label)
        ]
        fallback = _fallback_error_analysis(rec.get("concept_title", ""))
        sections.append((_ERROR_ANALYSIS_LABEL, fallback))
        rec["concept_details"] = join_sections(sections)
    return records


def ensure_misconceptions(records: list[dict]) -> list[dict]:
    """Backward-compatible alias for ensuring learner-analysis coverage."""
    return ensure_analysis_sections(records)


def refine_chapter(records: list[dict]) -> list[dict]:
    """Full deterministic refinement pass over a chapter's ordered records."""
    for rec in records:
        if rec.get("concept_details"):
            details = split_merged_description_blocks(rec["concept_details"])
            details = reduce_type_sections(details)
            if not is_culmination(rec.get("concept_title", "")):
                details = format_mastery_statement(details)
            details = normalize_analysis_sections(details)
            rec["concept_details"] = details
    records = ensure_analysis_sections(records)
    records = renumber_types_continuously(records)
    return set_culmination_recap(records)
