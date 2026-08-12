"""Deterministic chapter-level refinement of concept-mapping output.

Runs on the full, ordered list of concept records for a chapter right before
they are deposited, so the stored Bulk Import rows carry the exact format the
team requires regardless of which extractor produced them:

1. **Stable reusable Type numbering.** Extractors restart ``Type 01`` inside
   every concept. We allocate numbers in textbook/topic order, but semantically
   consolidated Types rendered on more than one concept or topic retain the
   SAME ``Type NN`` when their hidden mined-Type identity is supplied. Their
   ``Case NN`` sequence continues across those hosts.
2. **One continuous Type sequence for the whole chapter.** Culmination rows
   share the same ``Type NN`` counter as regular concepts, in row order.
   They previously carried a separate "Miscellaneous Type NN" sequence, which
   reviewers read as a second, parallel numbering.
3. **Type reduction for theory.** Purely theoretical concepts should not carry
   a Types section; we drop any ``Types:`` block that has no concrete ``Case``.
4. **Culmination description = detailed "Recap of ...".** Culmination rows keep
   their Types and any analysis sections, but their Description is replaced
   with "Recap of <A>, <B> and <C>" listing the topic's merged concepts.
5. **"Achieving Mastery" statement on its own line.** A mastery statement at
   the end of a Description is normalized to a line-broken
   ``\\nAchieving Mastery: <statement>`` format.
6. **Learner analysis is always present on normal concepts.** Each normal
   concept ends with exactly one ``Misconception/ Error Analysis`` section.
   That section contains both a commonly held incorrect belief and a plausible
   procedural, computational, representational, or reasoning mistake.

``concept_details`` is the canonical
``Description: ... // Activity/Info Hub: ... // Types: ... //
Misconception/ Error Analysis: Misconceptions: ...; Error Analysis: ...`` string
(sections joined by " // "). Optional Activity/Info Hub and Types sections are
omitted when not applicable.
"""
from __future__ import annotations

import hashlib
import re

_SECTION_SEP = " // "
# Matches a Type/Case token, optionally already prefixed with "Miscellaneous "
# (so re-runs never stack the prefix).
_TYPE_TOKEN_RE = re.compile(
    r"(?:Miscellaneous\s+)?Type\s*0*\d+\s*:", re.IGNORECASE)
_CASE_TOKEN_RE = re.compile(r"Case\s*0*\d+\s*:", re.IGNORECASE)
_EXAMPLE_TOKEN_RE = re.compile(
    r"(?<!Worked )\bExamples?(?:\s+0*\d+)?\s*:", re.IGNORECASE)
_ACTIVITY_HUB_LABEL = "Activity/Info Hub"
_MISCONCEPTIONS_LABEL = "Misconceptions"
_ERROR_ANALYSIS_LABEL = "Error Analysis"
_ANALYSIS_LABEL = "Misconception/ Error Analysis"
_TYPE_ORIGIN_SEALS_KEY = "_type_origin_seals"


def _number_from_token(value: str) -> int | None:
    """Return the rendered numeric identity from a Type/Case token."""
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _seal_origin_identity(value: object) -> str:
    """Return a durable opaque seal without retaining the mined Type ID."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"origin-seal::[0-9a-f]{64}", raw):
        return raw
    digest = hashlib.sha256(
        ("aegis-rendered-type-origin-v1\0" + raw).encode("utf-8")
    ).hexdigest()
    return f"origin-seal::{digest}"


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


def is_combined_analysis_label(label: str) -> bool:
    """Return True for the canonical single learner-analysis section."""
    key = re.sub(r"[^a-z]", "", (label or "").strip().lower())
    return key in {
        "misconceptionerroranalysis",
        "misconceptionserroranalysis",
        "misconceptionanderroranalysis",
        "misconceptionsanderroranalysis",
    }


def is_learner_analysis_label(label: str) -> bool:
    """Accept canonical combined and legacy split learner-analysis labels."""
    return (
        is_combined_analysis_label(label)
        or is_misconception_label(label)
        or is_error_analysis_label(label)
    )


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


def _type_example_signature(segment: str) -> tuple[str, ...]:
    """Return exact public Example identities owned by one Type block."""
    matches = list(_EXAMPLE_TOKEN_RE.finditer(segment or ""))
    examples: list[str] = []
    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(segment)
        )
        value = segment[match.end():end]
        boundary = re.search(
            r"\b(?:Case|(?:Miscellaneous\s+)?Type)\s*0*\d+\s*:",
            value,
            re.IGNORECASE,
        )
        if boundary:
            value = value[:boundary.start()]
        normalized = re.sub(r"\s+", " ", value).strip().casefold()
        if normalized:
            examples.append(normalized)
    return tuple(sorted(examples))


def _origin_type_ids_for_block(
    value: object,
    *,
    text: str,
    matches: list[re.Match],
) -> list[str]:
    """Resolve hidden mined-Type identities for rendered Type occurrences.

    ``_origin_type_id`` is deliberately record metadata, never public Type
    text.  A record that renders one Type may carry the historical scalar
    value.  Records that render several Types carry an ordered list/tuple so
    each occurrence keeps its own mined identity.  A small mapping form is
    also accepted for compatibility with callers that retain the original
    local ``Type NN`` token as their key.

    A scalar on a multi-Type record is ambiguous, so it is ignored rather than
    incorrectly collapsing every Type on that record into one public Type.
    """
    count = len(matches)
    if not count:
        return []
    if isinstance(value, str):
        origin = value.strip()
        return [origin] if origin and count == 1 else [""] * count
    if isinstance(value, (list, tuple)):
        return [
            str(value[index] or "").strip() if index < len(value) else ""
            for index in range(count)
        ]
    if not isinstance(value, dict):
        return [""] * count

    origins: list[str] = []
    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < count
            else len(text)
        )
        segment = text[match.start():end]
        token = re.sub(r"\s*:\s*$", "", match.group(0)).strip()
        signature = _type_signature(segment)
        raw_origin = ""
        # Ordered lists are preferred.  Mapping support is intentionally
        # explicit: zero-based occurrence index, original local Type token,
        # or the normalized rendered header.
        for candidate in (
            index,
            str(index),
            token,
            token.casefold(),
            signature,
        ):
            if candidate in value:
                raw_origin = value[candidate]
                break
        origins.append(str(raw_origin or "").strip())
    return origins


def _type_occurrences(record: dict) -> tuple[str, list[re.Match]]:
    """Return the rendered Types body and its ordered Type tokens."""
    sections = split_sections(record.get("concept_details") or "")
    index = _find_types(sections)
    if index < 0:
        return "", []
    content = sections[index][1]
    return content, list(_TYPE_TOKEN_RE.finditer(content or ""))


def _record_origin_seals(record: dict) -> tuple[str, list[re.Match], list[str]]:
    """Resolve the ordered hidden seals currently attached to one record."""
    content, matches = _type_occurrences(record)
    if not matches:
        return content, matches, []
    persisted = _origin_type_ids_for_block(
        record.get(_TYPE_ORIGIN_SEALS_KEY),
        text=content,
        matches=matches,
    )
    handoff = _origin_type_ids_for_block(
        record.get("_origin_type_id"),
        text=content,
        matches=matches,
    )
    seals = [
        _seal_origin_identity(handoff[index] or persisted[index])
        for index in range(len(matches))
    ]
    return content, matches, seals


def carry_type_origin_metadata(before: dict, after: dict) -> dict:
    """Carry opaque Type identity across a row-local Types rebuild.

    Cleanup helpers rebuild Case numbering within one record.  Without this
    handoff, a chapter-global Type rendered on several topics can temporarily
    restart at ``Case 01`` and become indistinguishable from unrelated local
    Types before the next chapter-wide renumbering pass.  Match stable Type
    headers and exact public Examples first. Rendered numbers are used only
    when the block has no Example identity, preventing a broad API rewrite
    from accidentally joining unrelated Types.
    """
    before_content, before_matches, before_seals = _record_origin_seals(before)
    after_content, after_matches = _type_occurrences(after)
    if not after_matches:
        after.pop(_TYPE_ORIGIN_SEALS_KEY, None)
        after.pop("_origin_type_id", None)
        return after
    if not before_matches or not any(before_seals):
        after.pop("_origin_type_id", None)
        return after

    before_entries: list[tuple[int, str, tuple[str, ...], str]] = []
    for index, match in enumerate(before_matches):
        end = (
            before_matches[index + 1].start()
            if index + 1 < len(before_matches)
            else len(before_content)
        )
        before_entries.append((
            _number_from_token(match.group(0)) or -1,
            _type_signature(before_content[match.start():end]),
            _type_example_signature(before_content[match.start():end]),
            before_seals[index],
        ))

    used: set[int] = set()
    carried: list[str] = []
    same_count = len(before_matches) == len(after_matches)
    for index, match in enumerate(after_matches):
        end = (
            after_matches[index + 1].start()
            if index + 1 < len(after_matches)
            else len(after_content)
        )
        number = _number_from_token(match.group(0)) or -1
        segment = after_content[match.start():end]
        signature = _type_signature(segment)
        examples = _type_example_signature(segment)
        candidates = [
            entry_index
            for entry_index, (old_number, old_signature, _old_examples, seal)
            in enumerate(before_entries)
            if entry_index not in used
            and seal
            and old_number == number
            and old_signature == signature
        ]
        if not candidates:
            candidates = [
                entry_index
                for entry_index, (_old_number, old_signature, _old_examples, seal)
                in enumerate(before_entries)
                if entry_index not in used and seal and old_signature == signature
            ]
        if not candidates:
            candidates = [
                entry_index
                for entry_index, (_old_number, _old_signature, old_examples, seal)
                in enumerate(before_entries)
                if entry_index not in used
                and seal
                and examples
                and old_examples == examples
            ]
        if not candidates:
            candidates = [
                entry_index
                for entry_index, (old_number, _old_signature, old_examples, seal)
                in enumerate(before_entries)
                if entry_index not in used
                and seal
                and old_number == number
                and (not examples or not old_examples)
            ]
        if (
            not candidates
            and same_count
            and index not in used
            and before_entries[index][2] == examples
        ):
            candidates = [index]
        chosen = candidates[0] if len(candidates) == 1 else -1
        if chosen >= 0:
            used.add(chosen)
            carried.append(before_entries[chosen][3])
        else:
            carried.append("")

    if any(carried):
        after[_TYPE_ORIGIN_SEALS_KEY] = carried
    else:
        after.pop(_TYPE_ORIGIN_SEALS_KEY, None)
    # Raw mined Type IDs are a one-shot rendering handoff and never survive in
    # checkpoint/public records; only the opaque seals above are durable.
    after.pop("_origin_type_id", None)
    return after


def _durable_rendered_type_origins(records: list[dict]) -> dict[tuple, str]:
    """Recover a previously rendered cross-topic Type identity.

    Hidden mined-Type IDs are intentionally consumed on the first rendering so
    they cannot leak into stored rows.  Later cleanup passes may renumber the
    same public rows again.  A genuinely shared Type is then still observable:
    the same rendered Type number and definition occur in multiple topics and
    its Case numbers form one non-overlapping continuous sequence.  Legacy
    topic-local Types restart at Case 01, so they do not satisfy this seal.
    """
    occurrences: dict[tuple, list[tuple[str, tuple[int, ...]]]] = {}
    for record in records:
        details = record.get("concept_details") or ""
        sections = split_sections(details)
        index = _find_types(sections)
        if index < 0:
            continue
        _, content = sections[index]
        matches = list(_TYPE_TOKEN_RE.finditer(content or ""))
        topic_key = re.sub(
            r"\W+", " ", str(record.get("topic") or "").lower()
        ).strip()
        sequence = "regular"
        for type_index, match in enumerate(matches):
            end = (
                matches[type_index + 1].start()
                if type_index + 1 < len(matches)
                else len(content)
            )
            segment = content[match.start():end]
            type_number = _number_from_token(match.group(0))
            signature = _type_signature(segment)
            case_numbers = tuple(
                number
                for token in _CASE_TOKEN_RE.findall(segment)
                if (number := _number_from_token(token)) is not None
            )
            if type_number is None or not signature or not case_numbers:
                continue
            key = (sequence, type_number, signature)
            occurrences.setdefault(key, []).append((topic_key, case_numbers))

    durable: dict[tuple, str] = {}
    for key, entries in occurrences.items():
        if len({topic for topic, _numbers in entries if topic}) < 2:
            continue
        case_numbers = [
            number
            for _topic, numbers in entries
            for number in numbers
        ]
        if (
            len(case_numbers) < 2
            or len(set(case_numbers)) != len(case_numbers)
            or sorted(case_numbers) != list(range(1, max(case_numbers) + 1))
        ):
            continue
        sequence, type_number, signature = key
        durable[key] = (
            f"rendered-global::{sequence}::{type_number}::{signature}"
        )
    return durable


def _renumber_reusable_block(
    text: str,
    *,
    topic_key: str,
    type_label: str,
    number_by_signature: dict[tuple[str, ...], int],
    case_count_by_signature: dict[tuple[str, ...], int],
    next_number: int,
    origin_type_ids: list[str] | None = None,
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
        origin_type_id = (
            origin_type_ids[index]
            if origin_type_ids and index < len(origin_type_ids)
            else ""
        )
        # A mined Type ID is an opaque chapter-global identity.  It remains
        # authoritative when its Cases are intentionally hosted on different
        # concepts or source topics.  Legacy rows without that hidden handoff
        # preserve the historical topic-plus-header matching behaviour.
        key = (
            ("origin_type_id", origin_type_id)
            if origin_type_id
            else ("topic_signature", topic_key, signature)
        )
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
        # The renderer is deliberately permissive about legacy ``Example:``
        # markers, but the public format is an explicit hierarchy.  Normalize
        # Examples independently within every Case so each Case reads as a
        # defined variation followed by Example 01, Example 02, and so on.
        case_matches = list(_CASE_TOKEN_RE.finditer(segment))
        if case_matches:
            case_pieces: list[str] = [segment[:case_matches[0].start()]]
            for case_index, case_match in enumerate(case_matches):
                case_end = (
                    case_matches[case_index + 1].start()
                    if case_index + 1 < len(case_matches)
                    else len(segment)
                )
                case_segment = segment[case_match.start():case_end]
                example_number = 0

                def replace_example(_match: re.Match) -> str:
                    nonlocal example_number
                    example_number += 1
                    return f"Example {example_number:02d}:"

                case_pieces.append(
                    _EXAMPLE_TOKEN_RE.sub(replace_example, case_segment)
                )
            segment = "".join(case_pieces)
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

    ONE chapter-wide continuous sequence -> "Type 01", "Type 02", ... shared
    by regular concepts and culmination rows alike, in row order. Culminations
    previously carried a separate "Miscellaneous Type NN" sequence, which read
    as a parallel numbering to reviewers. A supplied hidden ``_origin_type_id`` keeps a
    mined Type chapter-global even when its Cases have different concept/topic
    hosts. The raw ID is immediately replaced by an opaque origin seal, which
    survives row-local cleanup and checkpoint resume without entering public
    Type text. Legacy rows without that handoff retain the prior behaviour:
    within one source topic, repeated canonical Type definitions share one
    number and their Cases continue increasing instead of restarting.
    """
    counter = 0
    regular_numbers: dict[tuple[str, ...], int] = {}
    regular_cases: dict[tuple[str, ...], int] = {}
    durable_origins = _durable_rendered_type_origins(records)
    for rec in records:
        # This handoff exists only long enough to give rendered fragments their
        # stable mined-Type identity. Consume the raw ID here; an opaque hash
        # seal remains as non-public record metadata for later cleanup/resume.
        raw_origin_type_id = rec.pop("_origin_type_id", None)
        details = rec.get("concept_details") or ""
        sections = split_sections(details)
        idx = _find_types(sections)
        if idx < 0:
            rec.pop(_TYPE_ORIGIN_SEALS_KEY, None)
            continue
        label, content = sections[idx]
        type_matches = list(_TYPE_TOKEN_RE.finditer(content or ""))
        persisted_origin_seals = _origin_type_ids_for_block(
            rec.get(_TYPE_ORIGIN_SEALS_KEY),
            text=content,
            matches=type_matches,
        )
        raw_origin_type_ids = _origin_type_ids_for_block(
            raw_origin_type_id,
            text=content,
            matches=type_matches,
        )
        origin_type_ids = [
            _seal_origin_identity(
                raw_origin_type_ids[index] or persisted_origin_seals[index]
            )
            for index in range(len(type_matches))
        ]
        topic_key = re.sub(
            r"\W+", " ", str(rec.get("topic") or "").lower()).strip()
        sequence = "regular"
        for type_index, match in enumerate(type_matches):
            if origin_type_ids[type_index]:
                continue
            end = (
                type_matches[type_index + 1].start()
                if type_index + 1 < len(type_matches)
                else len(content)
            )
            segment = content[match.start():end]
            type_number = _number_from_token(match.group(0))
            if type_number is None:
                continue
            origin_type_ids[type_index] = _seal_origin_identity(
                durable_origins.get(
                    (sequence, type_number, _type_signature(segment)),
                    "",
                )
            )
        new_content, counter = _renumber_reusable_block(
            content,
            topic_key=topic_key,
            type_label="Type",
            number_by_signature=regular_numbers,
            case_count_by_signature=regular_cases,
            next_number=counter,
            origin_type_ids=origin_type_ids,
        )
        sections[idx] = (label, new_content)
        rec["concept_details"] = join_sections(sections)
        if any(origin_type_ids):
            rec[_TYPE_ORIGIN_SEALS_KEY] = origin_type_ids
        else:
            rec.pop(_TYPE_ORIGIN_SEALS_KEY, None)
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
        for i, (label, content) in enumerate(sections):
            if label.strip().lower().startswith("description"):
                # An authored consolidation paragraph after a recap-led
                # description survives the re-stamp: only the recap
                # sentence itself is replaced with the canonical form.
                tail = ""
                match = re.match(
                    r"^Recap of\s.*?\.(?:\s+(?=\S)|$)",
                    str(content or "").strip(),
                    re.DOTALL,
                )
                if match:
                    tail = str(content or "").strip()[match.end():].strip()
                sections[i] = (
                    "Description", recap + (f" {tail}" if tail else "")
                )
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
# Models occasionally put the combined learner-analysis label on a new line
# inside Description instead of starting a new `` // `` section. The generic
# inline matcher sees the nested ``Error Analysis:`` label in that form, but
# not the leading ``Misconception/`` prefix.
_NEWLINE_COMBINED_ANALYSIS_RE = re.compile(
    r"(?:^|\r?\n)\s*Misconceptions?\s*/\s*"
    r"Error\s+Analys(?:is|es)\s*:\s*",
    re.IGNORECASE,
)
# If a prior pass already split the trailing canonical section, the malformed
# newline prefix can remain by itself at the end of Description. It has no
# learner-analysis content and must be discarded rather than rendered.
_ORPHAN_ANALYSIS_PREFIX_RE = re.compile(
    r"(?im)^[ \t]*Misconceptions?[ \t]*/[ \t]*(?:\r?\n|$)",
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
    concept = (title or "this concept").strip().rstrip(".")
    return (
        f"Students may assume {concept} is a rule that always applies without "
        "checking its conditions, context, or representation."
    )


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


def _trim_analysis_marker_separator(text: str) -> str:
    """Drop the semicolon that separates the two canonical components.

    ``Misconceptions: ...; Error Analysis: ...`` is stored in one section.
    When the content is split at the next nested label, that separator belongs
    to neither component.  Removing it here keeps normalization idempotent
    rather than accumulating ``;`` on each refinement pass.
    """
    return re.sub(r"\s*;\s*$", "", (text or "").strip()).strip()


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
    """Normalize learner analysis into one combined canonical section.

    Learner-analysis text can appear inline in Description and in repeated
    sections. Inline copies are removed, repeated sections of the same kind are
    consolidated, while their two different meanings remain explicitly
    labelled inside one public section.

    Misconceptions are commonly held but incorrect beliefs or interpretations.
    Error Analysis captures plausible procedural, computational,
    representational, or reasoning mistakes made while applying the concept.
    Legacy split sections are accepted as input. Output contains at most one
    ``Misconception/ Error Analysis`` section after Types.
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
        text = _trim_analysis_marker_separator(text)
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
        if is_combined_analysis_label(label):
            matches = list(_INLINE_ANALYSIS_RE.finditer(content or ""))
            if matches:
                _collect_inline(content)
            elif _LEARNER_FALSE_BELIEF_RE.search(content or ""):
                _collect("misconception", content)
            else:
                _collect("error_analysis", content)
            continue
        kind = _kind_for_label(label)
        if kind:
            _collect_inline(content, kind)
            continue
        if lower.startswith("description"):
            body = content
            # Treat a newline-prefixed combined label as a separate semantic
            # section so a stray ``Misconception/`` cannot survive in the
            # public Description when the canonical delimiter was omitted.
            newline_combined = _NEWLINE_COMBINED_ANALYSIS_RE.search(body)
            if newline_combined:
                combined_content = body[newline_combined.end():]
                matches = list(_INLINE_ANALYSIS_RE.finditer(combined_content))
                if matches:
                    _collect_inline(combined_content)
                elif _LEARNER_FALSE_BELIEF_RE.search(combined_content):
                    _collect("misconception", combined_content)
                else:
                    _collect("error_analysis", combined_content)
                body = body[:newline_combined.start()].rstrip()
            body = _ORPHAN_ANALYSIS_PREFIX_RE.sub("", body).rstrip()
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
    # one combined learner-analysis section. The hub stays before assessable
    # Cases.
    if hub_block:
        ordered.append(hub_block)
    if types_block:
        ordered.append(types_block)
    combined: list[str] = []
    if chosen_misconceptions:
        combined.append(f"Misconceptions: {chosen_misconceptions}")
    if chosen_errors:
        combined.append(f"Error Analysis: {chosen_errors}")
    if combined:
        ordered.append((_ANALYSIS_LABEL, "; ".join(combined)))
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
            or is_learner_analysis_label(label)
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


def _analysis_components(
    sections: list[tuple[str, str]],
) -> tuple[str, str]:
    """Return misconception and error-analysis text from canonical/legacy input."""
    misconception = ""
    error_analysis = ""
    for label, content in sections:
        if is_misconception_label(label):
            misconception = content.strip() or misconception
            continue
        if is_error_analysis_label(label):
            error_analysis = content.strip() or error_analysis
            continue
        if not is_combined_analysis_label(label):
            continue
        matches = list(_INLINE_ANALYSIS_RE.finditer(content or ""))
        for index, marker in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(content)
            )
            value = _trim_analysis_marker_separator(content[marker.end():end])
            kind = marker.group("label")
            if is_misconception_label(kind):
                misconception = value or misconception
            elif is_error_analysis_label(kind):
                error_analysis = value or error_analysis
    return misconception, error_analysis


def analysis_components(details: str) -> tuple[str, str]:
    """Public accessor for the two meanings inside one analysis section."""
    return _analysis_components(split_sections(details or ""))


def _authored_analysis_is_authoritative() -> bool:
    # Under the rewritten pipeline learner analysis is model-authored and
    # either section alone is complete: deterministic filler is exactly what
    # the terminal gate rejects, and a wholly missing analysis is the Polish
    # pass's to author with a real model call. The legacy path keeps its
    # historical backfill.
    try:
        from .phase3 import runner as _phase3_runner
    except ImportError:  # pragma: no cover - defensive ordering
        return False
    return _phase3_runner.rewrite_enabled()


def ensure_analysis_sections(records: list[dict]) -> list[dict]:
    """Ensure one combined section carrying the learner-analysis content."""
    authored = _authored_analysis_is_authoritative()
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
        misconception, error_analysis = _analysis_components(sections)
        if misconception and error_analysis:
            continue
        if authored:
            # Either authored section alone is complete; neither present is
            # model work, never filler. normalize_analysis_sections already
            # rendered the canonical one-sided combined section.
            continue
        sections = [
            (label, content)
            for label, content in sections
            if not is_learner_analysis_label(label)
        ]
        title = rec.get("concept_title", "")
        misconception = misconception or _fallback_misconception(title)
        error_analysis = error_analysis or _fallback_error_analysis(title)
        sections.append((
            _ANALYSIS_LABEL,
            f"Misconceptions: {misconception}; "
            f"Error Analysis: {error_analysis}",
        ))
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
