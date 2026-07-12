"""Validation for generated concept-map rows before deposit.

The checks are deliberately structured so the LLM repair pass can receive
precise row/field/code feedback instead of a vague "quality is poor" message.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Collection

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
# Optional whitespace after Fig/Example/Ex covers OCR forms like "fig.11.1".
# page14 / p14 (no space) are included — keep in sync with concept_cleanup
# neutralize/scrub patterns.
_SOURCE_ARTIFACT_RE = re.compile(
    r"\b(?:MMDs?|Examples?\.?\s*\d+(?:\.\d+)*|Fig(?:ure)?s?\.?\s*\d+(?:\.\d+)*|"
    r"Tables?\.?\s*\d+(?:\.\d+)*|"
    r"Exercises?\.?\s*\d+(?:\.\d+)*|Ex\.?\s*\d+(?:\.\d+)*|"
    r"pages?\.?\s*(?:no\.?\s*)?\d+|p\.?\s*\d+)\b",
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
_CASE_TASK_VERB_RE = re.compile(
    r"\b(?:solve|simplify|find|write|identify|expand|compare|calculate|"
    r"rationalise|express|evaluate|convert|draw|label|explain|prove|"
    r"describe|discuss|analyse|analyze|examine|interpret|outline|assess|"
    r"state|list|mention|account|justify|trace|distinguish|define|"
    r"what|why|how|who|when|where)\b",
    re.IGNORECASE,
)
# Concrete detail: math tokens, proper-name phrases, or quoted source wording.
_CASE_SPECIFIC_DETAIL_RE = re.compile(
    r"(?:\d|[+\-*/÷×=^]|[A-Za-z]\s*\^\s*\d|"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|"
    r"['\"][^'\"]{3,}['\"])"
)
_EXAMPLE_SPLIT_RE = re.compile(r"\bExamples?\s*:\s*", re.IGNORECASE)
_EXAMPLE_SEGMENT_RE = re.compile(
    r"(\bExamples?\s*:\s*)(.*?)"
    r"(?=\bExamples?\s*:|\b(?:Case|Type)\s+\d{1,2}:|\s+//\s+|$)",
    re.IGNORECASE | re.DOTALL,
)
_DESCRIPTION_LABEL_RE = re.compile(r"\bDescription\s*:", re.IGNORECASE)
_IMAGE_URL_RE = re.compile(r"!\[[^\]]*\]\(https?://[^)]+\)|https?://\S+", re.IGNORECASE)
# With embedded Mathpix images, figure/table references are legitimate content
# ("Refer fig. 11.1" next to its image URL); only textual pointers to unshipped
# source artifacts (Example 5, Exercise 1.2, page 14, MMD) stay forbidden.
_SOURCE_ARTIFACT_NO_FIG_RE = re.compile(
    r"\b(?:MMDs?|Examples?\.?\s*\d+(?:\.\d+)*|"
    r"Exercises?\.?\s*\d+(?:\.\d+)*|Ex\.?\s*\d+(?:\.\d+)*|"
    r"pages?\.?\s*(?:no\.?\s*)?\d+|p\.?\s*\d+)\b",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _mask_allowed_source_examples(
    details: str, allowed_source_examples: Collection[str],
) -> str:
    """Mask only exact, inventory-owned Example prompts for artifact checks."""
    allowed = {_norm(text) for text in allowed_source_examples if _norm(text)}
    if not details or not allowed:
        return details

    def replace(match: re.Match) -> str:
        if _norm(match.group(2)) not in allowed:
            return match.group(0)
        return match.group(1) + "[inventory-owned source question]"

    return _EXAMPLE_SEGMENT_RE.sub(replace, details)


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


def _example_too_short(example_text: str) -> bool:
    words = re.findall(r"\w+", example_text or "")
    text = example_text or ""
    if len(words) >= 5:
        return False
    # Descriptive/history prompts are often 4+ words with a clear ask and no
    # digits (e.g. "Explain German unification under Prussia").
    if len(words) >= 4 and _CASE_TASK_VERB_RE.search(text):
        return False
    # Concise math tasks: action + concrete expression/value detail.
    return not (
        len(words) >= 3
        and _CASE_TASK_VERB_RE.search(text)
        and _CASE_SPECIFIC_DETAIL_RE.search(text)
    )


def _case_example_too_short(case_text: str) -> bool:
    """A Case is 'Case NN: <sub-type definition> Example: <full question> ...'.

    When Example lines exist, each must carry a substantive untruncated
    question. Legacy cases carry the question directly in the Case text.
    """
    parts = _EXAMPLE_SPLIT_RE.split(case_text or "")
    examples = [p.strip() for p in parts[1:] if p.strip()]
    if examples:
        return any(_example_too_short(ex) for ex in examples)
    return _example_too_short(parts[0].strip() if parts else "")


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
    allowed_source_examples: Collection[str] = (),
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
        artifact_details = _mask_allowed_source_examples(
            details, allowed_source_examples)
        row_text = " ".join([topic, parent, title, artifact_details])
        # Figure/table references are allowed once the actual image URL is
        # embedded (reviewers want "(Refer fig. 11.1)" + the Mathpix image).
        artifact_re = (
            _SOURCE_ARTIFACT_NO_FIG_RE if _IMAGE_URL_RE.search(details)
            else _SOURCE_ARTIFACT_RE
        )
        if artifact_re.search(row_text):
            _add(errors, i, "concept_details", "source_artifact",
                 "row contains source artifact references")
        if details and not details.startswith("Description:"):
            _add(errors, i, "concept_details", "description_prefix",
                 "concept_details must start with 'Description:'")
        if len(_DESCRIPTION_LABEL_RE.findall(details)) > 1:
            _add(errors, i, "concept_details", "merged_description",
                 "cell contains multiple concepts' Description blocks")
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
            if (
                not is_culm
                and _has_types(details)
                and words < 20
            ):
                _add(errors, i, "concept_details", "thin_description",
                     "Description is too thin for a concept that carries Types",
                     "warning")
            if _IMAGE_URL_RE.search(desc):
                _add(errors, i, "concept_details", "description_image_url",
                     "Mathpix/image URLs belong in Types Examples, not Description",
                     "warning")
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
                if _case_example_too_short(case_text):
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
