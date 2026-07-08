"""Validation for generated concept-map rows before deposit.

The checks are deliberately structured so the LLM repair pass can receive
precise row/field/code feedback instead of a vague "quality is poor" message.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from . import concept_cleanup, concept_refiner

FORBIDDEN_NAMES = {
    "introduction", "overview", "basics", "misc", "miscellaneous",
    "examples", "practice", "basic concepts",
}
FORBIDDEN_TOPIC_NAMES = {
    "overview", "basics", "basic concepts", "general",
    "summary", "misc", "miscellaneous",
}
PLACEHOLDERS = {"n/a", "na", "none", "not applicable", "placeholder", "tbd", "lorem ipsum"}
_SECTION_NUMBER_RE = re.compile(r"\b(?:exercise|ex)?\s*\d+(?:\.\d+)+\b", re.IGNORECASE)
_SOURCE_ARTIFACT_RE = re.compile(
    r"\b(?:MMD|Example\s+\d+|Fig(?:ure)?\s+\d+|Table\s+\d+|"
    r"Exercise\s+\d+(?:\.\d+)?|Ex\s+\d+(?:\.\d+)?|"
    r"page\s+(?:no\.?\s*)?\d+|p(?:age)?\.?\s*\d+)\b",
    re.IGNORECASE,
)
_TYPE_RE = re.compile(r"\bType\s+\d{2}:", re.IGNORECASE)
_CASE_RE = re.compile(r"\bCase\s+\d{2}:", re.IGNORECASE)
_CASE_ANY_RE = re.compile(r"\bCase\s+\d{1,2}:", re.IGNORECASE)
_TYPE_ANY_RE = re.compile(r"\bType\s+\d{1,2}:", re.IGNORECASE)
_GENERIC_OPENER_RE = re.compile(r"^(applications|properties)\s+of\b", re.IGNORECASE)
_CASE_SEGMENT_RE = re.compile(
    r"\bCase\s+\d{1,2}:\s*(.*?)(?=\b(?:Case|Type)\s+\d{1,2}:|$)",
    re.IGNORECASE | re.DOTALL,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_forbidden_name(title: str) -> bool:
    t = _norm(title)
    return (
        t in FORBIDDEN_NAMES
        or t.startswith("definition of ")
        or t.startswith("types of ")
    )


def _description_words(details: str) -> int:
    desc = ""
    for label, content in concept_refiner.split_sections(details):
        if label.lower().startswith("description"):
            desc = content
            break
    return len(re.findall(r"\w+", desc))


def _has_types(details: str) -> bool:
    return any(
        label.lower().startswith("type")
        for label, _ in concept_refiner.split_sections(details)
    )


def _empty_label(details: str, label_name: str) -> bool:
    for label, content in concept_refiner.split_sections(details):
        if label.lower().startswith(label_name) and not content.strip():
            return True
    return False


def _has_label(details: str, label_name: str) -> bool:
    return any(
        label.lower().startswith(label_name)
        for label, _ in concept_refiner.split_sections(details)
    )


def _description_text(details: str) -> str:
    for label, content in concept_refiner.split_sections(details):
        if label.lower().startswith("description"):
            return content.strip()
    return ""


def _misconception_text(details: str) -> str:
    for label, content in concept_refiner.split_sections(details):
        if label.lower().startswith("misconception"):
            return content.strip()
    return ""


def _add(errors: list[dict], row_index: int, field: str, code: str,
         message: str, severity: str = "error") -> None:
    errors.append({
        "row_index": row_index,
        "field": field,
        "code": code,
        "message": message,
        "severity": severity,
    })


def validate_concept_rows(
    rows: list[dict[str, Any]], *,
    require_parent: bool = True,
    allow_types: bool = True,
    require_culmination: bool = False,
    allow_culmination: bool = True,
) -> dict:
    """Return a structured validation report for concept-map records."""
    errors: list[dict] = []
    topic_title_counts: Counter[tuple[str, str]] = Counter()
    title_counts: Counter[str] = Counter()
    topic_rows: defaultdict[str, list[tuple[int, dict]]] = defaultdict(list)

    for i, row in enumerate(rows):
        topic = (row.get("topic") or "").strip()
        parent = (row.get("parent_concept") or "").strip()
        title = (row.get("concept_title") or row.get("concept") or "").strip()
        details = (row.get("concept_details") or row.get("concept_description") or "").strip()
        is_culm = concept_refiner.is_culmination(title)
        topic_rows[topic].append((i, row))
        if title:
            title_counts[_norm(title)] += 1
            topic_title_counts[(_norm(topic), _norm(title))] += 1

        for field, value in (
            ("topic", topic),
            ("concept_title", title),
            ("concept_details", details),
        ):
            if not value:
                _add(errors, i, field, "required", f"{field} is required")
        if require_parent and not is_culm and not parent:
            _add(errors, i, "parent_concept", "required_parent",
                 "parent_concept is required for normal concepts")
        if title and _is_forbidden_name(title):
            _add(errors, i, "concept_title", "forbidden_name",
                 f"forbidden filler concept name: {title}")
        if topic and _norm(topic) in FORBIDDEN_TOPIC_NAMES:
            _add(errors, i, "topic", "forbidden_topic",
                 f"forbidden filler topic name: {topic}", "warning")
        if title and _GENERIC_OPENER_RE.search(title):
            _add(errors, i, "concept_title", "generic_opener",
                 "generic Applications/Properties opener is too broad", "warning")
        if _SECTION_NUMBER_RE.search(title):
            _add(errors, i, "concept_title", "section_number",
                 "concept title contains section/exercise numbering")
        if _SECTION_NUMBER_RE.search(topic):
            _add(errors, i, "topic", "section_number",
                 "topic contains section/exercise numbering")
        if _SOURCE_ARTIFACT_RE.search(" ".join([topic, parent, title, details])):
            _add(errors, i, "concept_details", "source_artifact",
                 "row contains source artifact references")
        if details and not details.startswith("Description:"):
            _add(errors, i, "concept_details", "description_prefix",
                 "concept_details must start with 'Description:'")
        if _norm(details) in PLACEHOLDERS or any(
            f" {p} " in f" {_norm(details)} " for p in PLACEHOLDERS
        ):
            _add(errors, i, "concept_details", "placeholder",
                 "placeholder description text is not allowed")
        if _empty_label(details, "type"):
            _add(errors, i, "concept_details", "empty_types",
                 "empty Types section is not allowed")
        if _empty_label(details, "misconception"):
            _add(errors, i, "concept_details", "empty_misconception",
                 "empty Misconception section is not allowed")
        if details and not is_culm and not _has_label(details, "misconception"):
            _add(errors, i, "concept_details", "missing_misconception",
                 "Misconceptions section is missing", "warning")
        misconception = _misconception_text(details)
        if misconception and concept_refiner._is_generic_misconception(misconception):
            _add(errors, i, "concept_details", "generic_misconception",
                 "Misconception should be specific to this concept", "warning")
        if details:
            words = _description_words(details)
            if not is_culm and (words < 4 or words > 120):
                _add(errors, i, "concept_details", "description_length",
                     "description length is outside reasonable bounds", "warning")
            desc = _description_text(details)
            if len(desc) > 450 and len(set(re.findall(r"\w+", desc.lower()))) < 35:
                _add(errors, i, "concept_details", "textbook_dump",
                     "description appears broad or dump-like", "warning")
        if not allow_types and _has_types(details):
            _add(errors, i, "concept_details", "types_too_early",
                 "Types are not allowed before the Types pass")
        if is_culm and not allow_culmination:
            _add(errors, i, "concept_title", "culmination_too_early",
                 "Culmination rows are not allowed before the culmination pass")
        if allow_types and _has_types(details):
            type_body = ""
            for label, content in concept_refiner.split_sections(details):
                if label.lower().startswith("type"):
                    type_body = content
                    break
            if type_body and _CASE_ANY_RE.search(type_body) and not _TYPE_ANY_RE.search(type_body):
                _add(errors, i, "concept_details", "case_without_type",
                     "Case labels require a Type label")
            if type_body and _TYPE_ANY_RE.search(type_body) and not _CASE_ANY_RE.search(type_body):
                _add(errors, i, "concept_details", "type_without_case",
                     "Type labels require at least one Case")
            if type_body and (not _TYPE_RE.search(type_body) or not _CASE_RE.search(type_body)):
                _add(errors, i, "concept_details", "types_format",
                     "Types must use zero-padded Type NN and Case NN labels")
            for case_match in _CASE_SEGMENT_RE.finditer(type_body or ""):
                case_text = re.sub(r"\s+", " ", case_match.group(1)).strip()
                if len(re.findall(r"\w+", case_text)) < 5:
                    _add(errors, i, "concept_details", "short_case_example",
                         "Case examples should include the full source question/task")
        if is_culm and details and not details.split(" // ", 1)[0].startswith(
                "Description: Recap"):
            _add(errors, i, "concept_details", "culmination_description",
                 "culmination description must start with 'Description: Recap'")

    for norm_title, count in title_counts.items():
        if norm_title and count > 1:
            for i, row in enumerate(rows):
                title = row.get("concept_title") or row.get("concept") or ""
                if _norm(title) == norm_title:
                    _add(errors, i, "concept_title", "duplicate_title",
                         "duplicate concept title within chapter")
    for key, count in topic_title_counts.items():
        if key[0] and key[1] and count > 1:
            for i, row in enumerate(rows):
                title = row.get("concept_title") or row.get("concept") or ""
                if (_norm(row.get("topic", "")), _norm(title)) == key:
                    _add(errors, i, "concept_title", "duplicate_topic_concept",
                         "duplicate normalized topic + concept pair")

    for topic, indexed in topic_rows.items():
        normal = [
            (i, r) for i, r in indexed
            if not concept_refiner.is_culmination(r.get("concept_title") or r.get("concept") or "")
        ]
        repeated = concept_cleanup.detect_repeated_leading_phrase(
            [r.get("concept_title") or r.get("concept") or "" for _, r in normal]
        )
        if repeated:
            affected = {_norm(n) for n in repeated["names"]}
            for i, row in normal:
                title = row.get("concept_title") or row.get("concept") or ""
                if _norm(title) in affected:
                    _add(errors, i, "concept_title", "repeated_sibling_opener",
                         f"repeated leading phrase: {repeated['phrase']}")
        if require_culmination and topic:
            culms = [
                (i, r) for i, r in indexed
                if concept_refiner.is_culmination(r.get("concept_title") or r.get("concept") or "")
            ]
            if len(culms) != 1:
                row_i = indexed[-1][0] if indexed else -1
                _add(errors, row_i, "concept_title", "culmination_count",
                     "exactly one culmination row is required per topic")
            elif culms[0][0] != indexed[-1][0]:
                _add(errors, culms[0][0], "concept_title", "culmination_order",
                     "culmination row must be last in its topic")

    hard = [e for e in errors if e["severity"] == "error"]
    return {
        "ok": not hard,
        "errors": errors,
        "summary": {
            "rows": len(rows),
            "errors": len(hard),
            "warnings": len(errors) - len(hard),
        },
    }
