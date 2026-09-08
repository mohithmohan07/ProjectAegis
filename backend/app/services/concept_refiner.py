"""Deterministic chapter-level refinement of concept-mapping output.

Name note: this is the deterministic pre-deposit FORMATTER (numbering,
section shape) — distinct from ``app/services/release_refiner.py``, The
Refiner of docs/aegis-restructure.md §8.3, which polishes the RENDERED
release output through the model before staging.

Runs on the full, ordered list of concept records for a chapter right before
they are deposited, so the stored Bulk Import rows carry the exact format the
team requires regardless of which extractor produced them:

1. **Stable reusable Type numbering.** Extractors restart ``Type 01`` inside
   every concept. We allocate numbers in textbook/topic order while preserving
   hidden reusable-Type identity. Ownership is decided upstream; audit-only
   lanes may retain a split long enough to report it without renumbering it as
   several apparently different Types.
2. **One continuous Type sequence for the whole chapter.** Culmination rows
   share the same ``Type NN`` counter as regular concepts, in row order.
   They previously carried a separate "Miscellaneous Type NN" sequence, which
   reviewers read as a second, parallel numbering.
3. **Preserve malformed sections.** Types without Cases and repeated
   Description markers remain intact with named API-repair findings.
4. **Preserve authored culmination prose.** Formatting does not replace
   consolidation teaching with a generated list of member titles.
5. **"Achieving Mastery" statement on its own line.** A mastery statement at
   the end of a Description is normalized to a line-broken
   ``\\nAchieving Mastery: <statement>`` format.
6. **Learner analysis keeps its authored content and kind.** Existing
   labelled sections are formatted as one ``Misconception/ Error Analysis``
   section. The chapter inventory and API critics own whether insights exist,
   their classification and quality; formatting never invents or drops them.

``concept_details`` is the canonical
``Description: ... // Activity/Info Hub: ... // Types: ... //
Misconception/ Error Analysis: Misconceptions: ...; Error Analysis: ...`` string
(sections joined by " // "). Optional Activity/Info Hub and Types sections are
omitted when not applicable.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

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
_RELEASE_TYPE_CASE_ROUTES_KEY = "_aegis_release_type_case_routes"


class SplitTypeHostError(RuntimeError):
    """Raised when one reusable Type is rendered under several concepts."""


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


_ANALYSIS_LABEL_ECHO_RE = re.compile(
    r"^(?:misconceptions?|error\s+analys(?:is|es))\s*:\s*",
    re.IGNORECASE,
)


def strip_analysis_label_echo(text: str) -> str:
    """Drop a leading kind-label echo from ONE analysis item's text.

    The composers add the canonical ``Misconceptions:`` / ``Error
    Analysis:`` prefix themselves; an item whose text already begins with
    the label would otherwise render it twice ("Error Analysis: Error
    Analysis: …"). Mechanics only: the item's KIND was decided upstream
    and is not re-judged here — a single exact leading label token is
    removed, nothing else, and the pass is idempotent.
    """

    return _ANALYSIS_LABEL_ECHO_RE.sub("", str(text or ""), count=1).strip()


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


def _type_host_key(record: dict, row_index: int) -> tuple[str, str]:
    """Return one stable concept-host identity for a rendered record."""
    topic = str(
        record.get("_semantic_topic_id") or record.get("topic") or ""
    )
    title = str(
        record.get("concept_title") or record.get("concept") or ""
    )
    topic_key = re.sub(r"\W+", " ", topic.casefold()).strip()
    title_key = re.sub(r"\W+", " ", title.casefold()).strip()
    # Malformed anonymous rows must not accidentally become one concept merely
    # because both public identity fields are blank.
    return topic_key, title_key or f"__row_{row_index}"


def split_type_host_violations(records: list[dict]) -> list[dict]:
    """Return every reusable Type identity rendered on multiple concepts.

    The sealed mined-Type identity is authoritative when present.  Exact
    public Type definitions are the deterministic fallback for older rows,
    because a reusable Type is chapter-level and duplicate definitions must
    not be assigned fresh numbers merely to conceal a second host.  Rewritten
    Phase 3 rows also carry explicit ``TYPE::CASE`` release routes; those catch
    activity or damaged-text rows whose visible Type block is unavailable.
    """
    occurrences: dict[tuple[str, str], dict] = {}

    def add(
        identity: tuple[str, str],
        *,
        row_index: int,
        host: tuple[str, str],
        display: str,
        kind: str,
    ) -> None:
        entry = occurrences.setdefault(identity, {
            "display": display,
            "kind": kind,
            "hosts": {},
        })
        entry["hosts"].setdefault(host, set()).add(row_index)

    pending_routes: list[tuple[int, tuple[str, str], str]] = []
    for row_index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        host = _type_host_key(record, row_index)
        content, matches, seals = _record_origin_seals(record)
        for type_index, match in enumerate(matches):
            end = (
                matches[type_index + 1].start()
                if type_index + 1 < len(matches)
                else len(content)
            )
            segment = content[match.start():end]
            signature = _type_signature(segment)
            if not signature:
                continue
            seal = seals[type_index] if type_index < len(seals) else ""
            if seal:
                add(
                    ("origin", seal),
                    row_index=row_index,
                    host=host,
                    display=signature,
                    kind="origin",
                )
            else:
                add(
                    ("definition", signature),
                    row_index=row_index,
                    host=host,
                    display=signature,
                    kind="definition",
                )

        for raw_route in record.get(_RELEASE_TYPE_CASE_ROUTES_KEY) or []:
            type_id = (
                str(raw_route.get("type_id") or "").strip()
                if isinstance(raw_route, Mapping)
                else str(raw_route or "").split("::", 1)[0].strip()
            )
            if type_id:
                pending_routes.append((row_index, host, type_id))

    # Route IDs and hidden origin seals are two representations of the same
    # mined Type identity. Process routes only after all visible blocks so a
    # route encountered on an earlier row can link to a seal encountered later.
    for row_index, host, type_id in pending_routes:
        origin_identity = ("origin", _seal_origin_identity(type_id))
        identity = (
            origin_identity
            if origin_identity in occurrences
            else ("release_route", type_id)
        )
        add(
            identity,
            row_index=row_index,
            host=host,
            display=(
                occurrences[identity]["display"]
                if identity in occurrences
                else type_id
            ),
            kind=(
                occurrences[identity]["kind"]
                if identity in occurrences
                else "release_route"
            ),
        )

    raw_violations: list[dict] = []
    for entry in occurrences.values():
        hosts = entry["hosts"]
        if len(hosts) <= 1:
            continue
        row_indexes = tuple(sorted({
            row_index
            for indexes in hosts.values()
            for row_index in indexes
        }))
        raw_violations.append({
            "type": entry["display"],
            "kind": entry["kind"],
            "row_indexes": list(row_indexes),
            "concept_titles": [
                str(
                    records[index].get("concept_title")
                    or records[index].get("concept")
                    or ""
                ).strip()
                for index in row_indexes
            ],
        })

    # Older rows can lack origin seals while carrying both a public definition
    # and a release route. For one host-set these are parallel evidence, not
    # automatically two Types. Pair the two evidence lists by cardinality:
    # this removes the one-definition/one-route double count while retaining
    # two distinguishable definitions or two distinct route IDs as two splits.
    by_rows: dict[tuple[int, ...], list[dict]] = {}
    for violation in raw_violations:
        by_rows.setdefault(
            tuple(violation["row_indexes"]), []
        ).append(violation)
    violations: list[dict] = []
    for grouped in by_rows.values():
        semantic = [
            violation for violation in grouped
            if violation["kind"] != "release_route"
        ]
        routes = [
            violation for violation in grouped
            if violation["kind"] == "release_route"
        ]
        if semantic and routes:
            representatives = (
                semantic if len(semantic) >= len(routes) else routes
            )
        else:
            representatives = grouped
        for representative in representatives:
            violations.append({
                key: value
                for key, value in representative.items()
                if key != "kind"
            })
    return violations


def assert_single_concept_type_hosts(records: list[dict]) -> None:
    """Enforce the Q14 owner ruling before deterministic formatting."""
    violations = split_type_host_violations(records)
    if not violations:
        return
    first = violations[0]
    titles = ", ".join(
        repr(title or f"row {index + 1}")
        for index, title in zip(
            first["row_indexes"], first["concept_titles"]
        )
    )
    raise SplitTypeHostError(
        "one concept must own every reusable Type; "
        f"{first['type']!r} spans {titles}"
        + (
            f" ({len(violations)} split Types total)"
            if len(violations) > 1
            else ""
        )
    )


def carry_type_origin_metadata(before: dict, after: dict) -> dict:
    """Carry opaque Type identity across a row-local Types rebuild.

    Cleanup helpers rebuild Case numbering within one record.  Keep the sealed
    mined identity attached to its single owning concept so later formatting
    cannot silently mint a second identity. Match stable Type headers and exact
    public Examples first. Rendered numbers are used only when the block has no
    Example identity, preventing a broad API rewrite from accidentally joining
    unrelated Types.
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
        # A mined Type ID is an opaque chapter-global identity. The formatter
        # preserves it so the release audit can see a split instead of hiding
        # it behind fresh numbers. Legacy rows without that hidden handoff keep
        # their historical topic-plus-header matching behaviour.
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
    """Compatibility entry point: preserve authored Types, including defects.

    A missing Case is a named schema defect for the existing API repair;
    it is not evidence that the Type contains no meaningful task.
    """
    return details


def renumber_types_continuously(records: list[dict]) -> list[dict]:
    """Renumber Types continuously without making an ownership verdict.

    ONE chapter-wide continuous sequence -> "Type 01", "Type 02", ... shared
    by regular concepts and culmination rows alike, in row order. Culminations
    previously carried a separate "Miscellaneous Type NN" sequence, which read
    as a parallel numbering to reviewers. A supplied hidden ``_origin_type_id``
    keeps the mined identity stable on its owning concept. The raw ID is
    immediately replaced by an opaque origin seal, which survives row-local
    cleanup and checkpoint resume without entering public Type text. The
    formatter deliberately does not choose a Type owner: Phase 3 does that from
    Case/QID evidence, while direct audit lanes preserve and report a split.
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
def format_mastery_statement(details: str) -> str:
    """Canonicalize each authored mastery label without choosing its content.

    Duplicate or empty mastery statements remain visible to the mechanical
    validator and the API author/critic. A formatter cannot infer that the
    later statement is better and discard the earlier one.
    """
    sections = split_sections(details)
    for i, (label, content) in enumerate(sections):
        if not label.strip().lower().startswith("description"):
            continue
        if not _MASTERY_LABEL_RE.search(content):
            continue
        sections[i] = (
            label, _MASTERY_LABEL_RE.sub("\nAchieving Mastery: ", content)
        )
    return join_sections(sections)


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


def _trim_analysis_marker_separator(text: str) -> str:
    """Drop the semicolon that separates the two canonical components.

    ``Misconceptions: ...; Error Analysis: ...`` is stored in one section.
    When the content is split at the next nested label, that separator belongs
    to neither component.  Removing it here keeps normalization idempotent
    rather than accumulating ``;`` on each refinement pass.
    """
    return re.sub(r"\s*;\s*$", "", (text or "").strip()).strip()


def normalize_analysis_sections(details: str) -> str:
    """Format existing learner-analysis labels without judging their meaning.

    Keep every authored statement under its supplied kind, including repeats,
    correction tails and overlapping wording. Bare combined-section content
    stays unlabelled; an API author/critic owns classification and quality.
    Empty labels remain available to the mechanical shape validator.
    """
    sections = split_sections(details)
    if not sections:
        return details

    misconception_texts: list[str] = []
    error_analysis_texts: list[str] = []
    unclassified_texts: list[str] = []
    stray_masteries: list[str] = []
    seen_kinds: set[str] = set()
    saw_analysis = False

    def _kind_for_label(label: str) -> str | None:
        if is_misconception_label(label):
            return "misconception"
        if is_error_analysis_label(label):
            return "error_analysis"
        return None

    def _collect(kind: str | None, text: str) -> None:
        if kind:
            seen_kinds.add(kind)
        text = _trim_analysis_marker_separator(text)
        # A labelled mastery span belongs in Description. Move its complete
        # authored tail without selecting one statement over another.
        marker = _MASTERY_LABEL_RE.search(text)
        if marker:
            stray_masteries.append(_MASTERY_LABEL_RE.sub(
                "\nAchieving Mastery: ", text[marker.start():]
            ).strip())
            text = text[:marker.start()].strip()
        if not text:
            return
        if kind == "misconception":
            misconception_texts.append(text)
        elif kind == "error_analysis":
            error_analysis_texts.append(text)
        else:
            unclassified_texts.append(text)

    def _collect_inline(text: str, default_kind: str | None = None) -> None:
        value = (text or "").strip()
        matches = list(_INLINE_ANALYSIS_RE.finditer(value))
        if not matches:
            _collect(default_kind, value)
            return
        prefix = value[:matches[0].start()].strip()
        if prefix:
            _collect(default_kind, prefix)
        for index, marker in enumerate(matches):
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(value)
            )
            _collect(_kind_for_label(marker.group("label")), value[marker.end():end])

    cleaned: list[tuple[str, str]] = []
    for label, content in sections:
        lower = label.strip().lower()
        if is_combined_analysis_label(label):
            saw_analysis = True
            _collect_inline(content)
            continue
        kind = _kind_for_label(label)
        if kind:
            saw_analysis = True
            _collect_inline(content, kind)
            continue
        if lower.startswith("description"):
            body = content
            newline_combined = _NEWLINE_COMBINED_ANALYSIS_RE.search(body)
            if newline_combined:
                saw_analysis = True
                _collect_inline(body[newline_combined.end():])
                body = body[:newline_combined.start()].rstrip()
            body = _ORPHAN_ANALYSIS_PREFIX_RE.sub("", body).rstrip()
            inline = _INLINE_ANALYSIS_RE.search(body)
            if inline:
                saw_analysis = True
                _collect_inline(body[inline.start():])
                body = body[:inline.start()].rstrip()
            cleaned.append((label, body))
            continue
        cleaned.append((label, content))

    # The upstream inventory decided each item's identity and kind. Repeated
    # text is not proof of duplicate identity or a reason to move/drop it.
    chosen_misconceptions = " ".join(misconception_texts)
    chosen_errors = " ".join(error_analysis_texts)

    ordered: list[tuple[str, str]] = []
    hub_blocks: list[tuple[str, str]] = []
    types_blocks: list[tuple[str, str]] = []
    for label, content in cleaned:
        lower = label.strip().lower()
        if lower.startswith("type"):
            types_blocks.append((label, content))
        elif is_activity_hub_label(label):
            if content.strip():
                hub_blocks.append((_ACTIVITY_HUB_LABEL, content.strip()))
        else:
            ordered.append((label, content))
    if stray_masteries:
        for i, (label, content) in enumerate(ordered):
            if not label.strip().lower().startswith("description"):
                continue
            ordered[i] = (label, content.rstrip() + "\n" + "\n".join(stray_masteries))
            break
        else:
            # No Description exists: retain the exact labelled content so
            # the required-field check/API repair sees what was supplied.
            for authored in stray_masteries:
                label, _separator, content = authored.partition(":")
                ordered.append((label, content.strip()))
    ordered.extend(hub_blocks)
    ordered.extend(types_blocks)
    combined: list[str] = list(unclassified_texts)
    if "misconception" in seen_kinds:
        combined.append(
            "Misconceptions: " + strip_analysis_label_echo(chosen_misconceptions)
        )
    if "error_analysis" in seen_kinds:
        combined.append("Error Analysis: " + strip_analysis_label_echo(chosen_errors))
    if saw_analysis:
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
    """Preserve concatenated Description blocks for an API-owned repair."""
    return details


def structure_findings(details: str) -> list[tuple[str, str]]:
    """Exact public marker defects, never a judgment about prose meaning."""
    findings: list[tuple[str, str]] = []
    if len(re.findall(r"\bDescription\s*:", str(details or ""), re.IGNORECASE)) > 1:
        findings.append((
            "repeated_description_marker",
            "More than one Description marker; preserve every teaching span "
            "while repairing the section structure through the API.",
        ))
    for label, content in split_sections(details):
        if label.strip().casefold() == "types" and not _CASE_TOKEN_RE.search(content):
            findings.append((
                "type_without_case",
                "Types has no numbered Case; retain the authored Type/Example "
                "and repair its hierarchy through the API.",
            ))
    return findings


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


def ensure_analysis_sections(records: list[dict]) -> list[dict]:
    """Normalize the one combined learner-analysis section.

    Learner analysis is model-authored: either authored section alone is
    complete, and a wholly missing analysis is the Polish pass's to author
    with a real model call — never deterministic filler.
    """
    for rec in records:
        if is_culmination(rec.get("concept_title", "")):
            continue
        details = rec.get("concept_details") or ""
        # Preserve the historical behavior for wholly empty rows; upstream
        # validation is responsible for missing Description content.
        if not details.strip():
            continue
        rec["concept_details"] = normalize_analysis_sections(details)
    return records


def ensure_misconceptions(records: list[dict]) -> list[dict]:
    """Backward-compatible alias for ensuring learner-analysis coverage."""
    return ensure_analysis_sections(records)


def refine_chapter(records: list[dict]) -> list[dict]:
    """Full deterministic refinement pass over a chapter's ordered records."""
    for rec in records:
        if rec.get("concept_details"):
            details = split_merged_description_blocks(rec["concept_details"])
            findings = structure_findings(details)
            if findings:
                rec.setdefault("_aegis_structure_original", details)
                flags = rec.setdefault("review_flags", [])
                for code, message in findings:
                    flag = f"structure preserved [{code}]: {message}"
                    if flag not in flags:
                        flags.append(flag)
            details = reduce_type_sections(details)
            if not is_culmination(rec.get("concept_title", "")):
                details = format_mastery_statement(details)
            details = normalize_analysis_sections(details)
            rec["concept_details"] = details
    records = ensure_analysis_sections(records)
    return renumber_types_continuously(records)
