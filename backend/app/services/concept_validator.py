"""Validation for generated concept-map rows before deposit.

The checks are deliberately structured so the LLM repair pass can receive
precise row/field/code feedback instead of a vague "quality is poor" message.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Collection

from . import concept_cleanup, concept_refiner, katex_rules

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
_IMAGE_URL_RE = re.compile(
    r"!\[[^\]]*\]\(https?://[^)]+\)|"
    r'\[img\s+src="https?://[^"]+"(?:\s+alt="[^"]*")?[^\]]*\]|'
    r"https?://\S+",
    re.IGNORECASE,
)
_EMPTY_IMAGE_ALT_RE = re.compile(
    r"!\[\s*\]\(https?://[^)]+\)|"
    r'\[img\s+src="https?://[^"]+"\s+alt="\s*"[^\]]*\]|'
    r'\[img\s+src="https?://[^"]+"(?![^\]]*\balt=)[^\]]*\]',
    re.IGNORECASE,
)
_DESCRIPTION_SECTION_REF_RE = re.compile(
    r"(?:\bsections?\s+|§\s*)\d+(?:\.\d+)+\b",
    re.IGNORECASE,
)
# With embedded Mathpix images, figure/table references are legitimate content
# ("Refer fig. 11.1" next to its image URL); only textual pointers to unshipped
# source artifacts (Example 5, Exercise 1.2, page 14, MMD) stay forbidden.
_SOURCE_ARTIFACT_NO_FIG_RE = re.compile(
    r"\b(?:MMDs?|Examples?\.?\s*\d+(?:\.\d+)*|"
    r"Exercises?\.?\s*\d+(?:\.\d+)*|Ex\.?\s*\d+(?:\.\d+)*|"
    r"pages?\.?\s*(?:no\.?\s*)?\d+|p\.?\s*\d+)\b",
    re.IGNORECASE,
)

# A misconception is a commonly held incorrect belief or interpretation.  It
# therefore needs explicit learner-belief framing, rather than merely naming a
# step that a learner might perform incorrectly.  The latter belongs in Error
# Analysis.
_MISCONCEPTION_BELIEF_RE = re.compile(
    r"\b(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|often|sometimes|commonly)\s+)?"
    r"(?:(?:incorrectly|wrongly|mistakenly)\s+)?"
    r"(?:believe|think|assume|expect|interpret|misinterpret|misunderstand|"
    r"regard|consider|"
    r"confuse|mistake|treat)\b",
    re.IGNORECASE,
)
_GENERIC_MISCONCEPTION_TEXT_RE = re.compile(
    r"^(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|often|sometimes|commonly)\s+)?"
    r"(?:misunderstand|misinterpret|be confused (?:about|by))\s+"
    r"(?:the|this)\s+(?:concept|topic|idea|material)\.?$",
    re.IGNORECASE,
)
_GENERIC_BELIEF_OBJECT_RE = re.compile(
    r"^(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|often|sometimes|commonly)\s+)?"
    r"(?:(?:incorrectly|wrongly|mistakenly)\s+)?"
    r"(?:believe|think|assume|expect|interpret|regard|consider|treat)\s+"
    r"(?:this|that|it|something|(?:the|this)\s+"
    r"(?:concept|topic|idea|material))\.?$",
    re.IGNORECASE,
)
_INCOMPLETE_BELIEF_RE = re.compile(
    r"^(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|often|sometimes|commonly)\s+)?"
    r"(?:(?:incorrectly|wrongly|mistakenly)\s+)?"
    r"(?:believe|think|assume|expect|interpret|regard|consider|treat)\s*\.?$",
    re.IGNORECASE,
)
_BARE_MISUNDERSTANDING_RE = re.compile(
    r"^(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|often|sometimes|commonly)\s+)?"
    r"(?:(?:incorrectly|wrongly|mistakenly)\s+)?"
    r"(?:misunderstand|misinterpret|confuse|mistake|treat|interpret)\s+"
    r"(?P<object>.+?)\.?$",
    re.IGNORECASE,
)
_MISUNDERSTANDING_SPECIFICITY_RE = re.compile(
    r"\b(?:that|how|why|when|whether|as|for|with|and|versus|vs\.?|"
    r"always|never|only|means?|requires?)\b",
    re.IGNORECASE,
)
_LEARNER_ACTOR_RE = re.compile(
    r"\b(?:students?|learners?|children)\b",
    re.IGNORECASE,
)

# Error Analysis names a plausible mistake made while applying a concept.  It
# may be procedural, computational, representational, or reasoning based, but
# it must not be a belief statement or a disguised correction.
_ERROR_ANALYSIS_BELIEF_RE = re.compile(
    r"\b(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|often|sometimes|commonly)\s+)?"
    r"(?:(?:incorrectly|wrongly|mistakenly)\s+)?"
    r"(?:believe|think|assume|expect|interpret|misinterpret|misunderstand|"
    r"regard|consider|"
    r"confuse|mistake|treat)\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_ACTION_RE = re.compile(
    r"\b(?:omit(?:s|ted|ting)?|skip(?:s|ped|ping)?|"
    r"drop(?:s|ped|ping)?|reverse(?:s|d|ing)?|swap(?:s|ped|ping)?|"
    r"misread(?:s|ing)?|miscop(?:y|ies|ied|ying)|"
    r"miscalculat(?:e|es|ed|ing)|mislabel(?:s|led|ling)?|"
    r"misplac(?:e|es|ed|ing)|misappl(?:y|ies|ied|ying)|"
    r"los(?:e|es|t|ing)|ignor(?:e|es|ed|ing)|"
    r"overlook(?:s|ed|ing)?|fail(?:s|ed|ing)?\s+to|"
    r"forget(?:s|ting)?\s+to|forgot(?:ten)?\s+to)\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_ACTOR_RE = re.compile(
    r"\b(?:students?|learners?|children)\b|\b(?:a\s+)?common\s+"
    r"(?:error|mistake|misstep)\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_SPECIFIC_MISTAKE_RE = re.compile(
    r"\b(?:instead\s+of|rather\s+than|incorrectly|wrong(?:ly)?|"
    r"too\s+(?:early|late|many|few|much|little))\b|"
    r"\bwithout\s+(?:first\s+)?[a-z]+ing\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_ONLY_ACTION_RE = re.compile(
    r"\b(?:add|subtract|multiply|divide|copy|quote|use|apply|compare|"
    r"analy[sz]e|select|choose|read|write|label|plot|draw|count|combine|"
    r"interpret|paraphrase|translate|test|check)(?:s|d|ed|ing|ies|ied)?\s+"
    r"only\s+(?:the\s+|a\s+|an\s+)?[a-z]",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_CORRECTION_RE = re.compile(
    r"\b(?:should|must|correct(?:ly)?|remember\s+that|"
    r"in\s+fact|actually|the\s+correct\s+(?:idea|rule|answer|method))\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_NEGATED_ACTION_RE = re.compile(
    r"^\s*(?:a\s+)?common\s+(?:error|mistake|misstep)\s+"
    r"(?:is|would\s+be)\s+not\s+(?:to\s+)?[a-z]+(?:ing)?\b",
    re.IGNORECASE,
)
_GENERIC_ERROR_ANALYSIS_RE = re.compile(
    r"^(?:(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|can|often|sometimes)\s+)?"
    r"(?:make|commit|have)\s+(?:a\s+)?"
    r"(?:mistake|mistakes|error|errors|calculation errors?|procedural errors?)|"
    r"(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|can|often|sometimes)\s+)?"
    r"(?:apply|use)\s+(?:the|this)\s+(?:concept|method|rule)\s+incorrectly|"
    r"(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|can|often|sometimes)\s+)?get\s+(?:a\s+)?wrong answer|"
    r"(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|can|often|sometimes)\s+)?"
    r"(?:[a-z]+ly\s+)*[a-z]+\s+"
    r"(?:(?:(?:the|this|a)\s+)?(?:concept|method|rule|formula|task|"
    r"problem|question|calculation|value|answer)\s+)?"
    r"(?:incorrectly|wrongly)|"
    r"(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|can|often|sometimes)\s+)?"
    r"(?:[a-z]+ly\s+)*"
    r"(?:choose|select|give|write|calculate|compute|produce|reach)\s+"
    r"(?:a\s+|the\s+)?wrong\s+(?:answer|option|response|result|conclusion)|"
    r"errors?\s+(?:may|might|can)\s+occur|"
    r"(?:students?|learners?|children)\s+"
    r"(?:(?:may|might|can|often|sometimes)\s+)?"
    r"(?:struggle\b.*|encounter\s+difficult(?:y|ies)\b.*|"
    r"have\s+difficult(?:y|ies)\b.*|find\b.*\bdifficult\b.*))\.?$",
    re.IGNORECASE,
)

_ISSUE_COMPARISON_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "believe", "believes",
    "by", "can", "children", "commonly", "consider", "do", "does", "for",
    "from", "in", "interpret", "is", "it", "learner", "learners", "may",
    "might", "mistake", "of", "often", "or", "regard", "student", "students",
    "that", "the", "their", "think", "thinks", "this", "to", "when", "while",
    "will", "with", "wrong",
}


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


def _description_text(details: str) -> str:
    for label, content in concept_refiner.split_sections(details):
        if label.lower().startswith("description"):
            return content.strip()
    return ""

def _issue_sections(
    details: str, label_name: str,
) -> list[tuple[int, str, str]]:
    """Return ordered sections matching a misconception/error-analysis label."""
    matches: list[tuple[int, str, str]] = []
    for index, (label, content) in enumerate(
        concept_refiner.split_sections(details)
    ):
        if label_name == "misconception":
            matched = concept_refiner.is_misconception_label(label)
        else:
            matched = concept_refiner.is_error_analysis_label(label)
        if matched:
            matches.append((index, label.strip(), content.strip()))
    return matches


def _is_generic_misconception(text: str) -> bool:
    value = (text or "").strip()
    bare_misunderstanding = _BARE_MISUNDERSTANDING_RE.match(value)
    return (
        _norm(value) in PLACEHOLDERS
        or bool(_GENERIC_MISCONCEPTION_TEXT_RE.match(value))
        or bool(_GENERIC_BELIEF_OBJECT_RE.match(value))
        or bool(_INCOMPLETE_BELIEF_RE.match(value))
        or bool(
            bare_misunderstanding
            and not _MISUNDERSTANDING_SPECIFICITY_RE.search(
                bare_misunderstanding.group("object")
            )
        )
        or concept_refiner._is_generic_misconception(value)
    )


def _has_mixed_learner_statement(text: str) -> bool:
    """Return True when any learner-led statement is not belief-framed."""
    for statement in _learner_analysis_statements(text):
        if (
            _LEARNER_ACTOR_RE.match(statement)
            and not _MISCONCEPTION_BELIEF_RE.search(statement)
        ):
            return True
    return False


def _is_error_analysis_belief(text: str) -> bool:
    return bool(_ERROR_ANALYSIS_BELIEF_RE.search((text or "").strip()))


def _is_correction_shaped_error_analysis(text: str) -> bool:
    value = (text or "").strip()
    declarative_negation = concept_refiner._DECLARATIVE_NEGATION_RE.search(value)
    return bool(
        value
        and (
            _ERROR_ANALYSIS_CORRECTION_RE.search(value)
            or (
                declarative_negation
                and not _ERROR_ANALYSIS_NEGATED_ACTION_RE.search(value)
            )
        )
    )


def _is_generic_error_analysis(text: str) -> bool:
    value = (text or "").strip()
    return (
        not value
        or _norm(value) in PLACEHOLDERS
        or bool(_GENERIC_ERROR_ANALYSIS_RE.match(value))
    )


def _is_plausible_error_analysis(text: str) -> bool:
    value = (text or "").strip()
    return bool(
        value
        and _ERROR_ANALYSIS_ACTOR_RE.search(value)
        and (
            _ERROR_ANALYSIS_ACTION_RE.search(value)
            or _ERROR_ANALYSIS_SPECIFIC_MISTAKE_RE.search(value)
            or _ERROR_ANALYSIS_ONLY_ACTION_RE.search(value)
            or _ERROR_ANALYSIS_NEGATED_ACTION_RE.search(value)
        )
    )


def is_valid_misconception(text: str) -> bool:
    """Return whether text states a specific learner belief/interpretation."""
    value = (text or "").strip()
    return bool(
        value
        and not _is_generic_misconception(value)
        and _MISCONCEPTION_BELIEF_RE.search(value)
        and not _has_mixed_learner_statement(value)
        and not concept_refiner._is_correction_shaped_misconception(value)
    )


def is_valid_error_analysis(text: str) -> bool:
    """Return whether text states a specific application mistake, not a belief."""
    value = (text or "").strip()
    return bool(
        value
        and not _is_generic_error_analysis(value)
        and not _is_error_analysis_belief(value)
        and not _is_correction_shaped_error_analysis(value)
        and _is_plausible_error_analysis(value)
    )


def _issue_comparison_tokens(text: str) -> set[str]:
    """Reduce issue prose to content tokens for cross-section overlap checks."""
    tokens: set[str] = set()
    for raw in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if raw in _ISSUE_COMPARISON_STOP_WORDS:
            continue
        token = raw
        # Lightweight stemming is deliberately narrow.  It catches common
        # restatements such as add/added/adding and fraction/fractions without
        # making unrelated concept vocabulary look equivalent.
        if len(token) > 5 and token.endswith("ing"):
            token = token[:-3]
            if len(token) > 2 and token[-1:] == token[-2:-1]:
                token = token[:-1]
        elif len(token) > 4 and token.endswith("ed"):
            token = token[:-2]
            if len(token) > 2 and token[-1:] == token[-2:-1]:
                token = token[:-1]
        elif len(token) > 4 and token.endswith("s"):
            token = token[:-1]
        if token and token not in _ISSUE_COMPARISON_STOP_WORDS:
            tokens.add(token)
    return tokens


def issue_sections_overlap(misconception: str, error_analysis: str) -> bool:
    """Return whether both sections restate the same underlying learner issue."""
    if _norm(misconception) == _norm(error_analysis):
        return True
    misconception_tokens = _issue_comparison_tokens(misconception)
    error_tokens = _issue_comparison_tokens(error_analysis)
    if not misconception_tokens or not error_tokens:
        return False
    shared = misconception_tokens & error_tokens
    shorter = min(len(misconception_tokens), len(error_tokens))
    return len(shared) >= 2 and len(shared) / shorter >= 0.8


_ANALYSIS_STATEMENT_BOUNDARY_RE = re.compile(
    r"(?<=[.!?;])\s+(?=(?:students?|learners?|children|"
    r"(?:a\s+)?common\s+(?:error|mistake|misstep))\b)",
    re.IGNORECASE,
)


def _learner_analysis_statements(text: str) -> list[str]:
    """Split only at punctuation-delimited learner/error statement starts."""
    value = (text or "").strip()
    if not value:
        return []
    return [
        statement.strip()
        for statement in _ANALYSIS_STATEMENT_BOUNDARY_RE.split(value)
        if statement.strip()
    ]


def ensure_valid_learner_analysis(records: list[dict]) -> list[dict]:
    """Keep valid learner-analysis sections and apply the deterministic fallback.

    API repair can fail or return unusable category text. Normalization alone
    intentionally preserves legacy non-empty text, so this final-boundary helper
    removes invalid analysis, keeps either or both valid categories in canonical
    order, and then lets the refiner add its Error Analysis fallback when a
    normal concept has neither. Culminations remain optional and receive no
    fallback.
    """
    for rec in records:
        details = rec.get("concept_details") or ""
        if not details.strip():
            continue
        normalized = concept_refiner.normalize_analysis_sections(details)
        sections = concept_refiner.split_sections(normalized)
        misconceptions: list[str] = []
        errors: list[str] = []
        seen_misconceptions: set[str] = set()
        seen_errors: set[str] = set()
        for label, content in sections:
            if (
                not concept_refiner.is_misconception_label(label)
                and not concept_refiner.is_error_analysis_label(label)
            ):
                continue
            for value in _learner_analysis_statements(content):
                belief_value = concept_refiner._strip_misconception_correction_tail(
                    value
                )
                belief_key = _norm(belief_value)
                error_key = _norm(value)
                if (
                    is_valid_misconception(belief_value)
                    and belief_key not in seen_misconceptions
                ):
                    seen_misconceptions.add(belief_key)
                    misconceptions.append(belief_value)
                elif is_valid_error_analysis(value) and error_key not in seen_errors:
                    # Legacy rows often put procedural mistakes under
                    # Misconception, while some model versions did the reverse.
                    # Classify by meaning and preserve every distinct valid item.
                    seen_errors.add(error_key)
                    errors.append(value)
        if misconceptions and errors:
            errors = [
                error
                for error in errors
                if not any(
                    issue_sections_overlap(misconception, error)
                    for misconception in misconceptions
                )
            ]
        misconception = " ".join(misconceptions)
        error_analysis = " ".join(errors)

        kept = [
            (label, content)
            for label, content in sections
            if not concept_refiner.is_misconception_label(label)
            and not concept_refiner.is_error_analysis_label(label)
        ]
        if misconception:
            kept.append(("Misconceptions", misconception))
        if error_analysis:
            kept.append(("Error Analysis", error_analysis))
        rec["concept_details"] = concept_refiner.join_sections(kept)

    return concept_refiner.ensure_analysis_sections(records)


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
        rich_text_defects = katex_rules.rich_text_issues(details)
        if rich_text_defects:
            _add(
                errors,
                i,
                "concept_details",
                "rich_text_format",
                "concept_details violates canonical [Katex]/[img] format: "
                + ", ".join(rich_text_defects),
            )
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
        misconception_sections = _issue_sections(details, "misconception")
        error_analysis_sections = _issue_sections(details, "error analysis")
        if any(not content for _, _, content in misconception_sections):
            _add(errors, i, "concept_details", "empty_misconception",
                 "empty Misconceptions section is not allowed")
        if any(not content for _, _, content in error_analysis_sections):
            _add(errors, i, "concept_details", "empty_error_analysis",
                 "empty Error Analysis section is not allowed")
        if len(misconception_sections) > 1:
            _add(errors, i, "concept_details", "duplicate_misconception",
                 "only one Misconceptions section is allowed")
        if len(error_analysis_sections) > 1:
            _add(errors, i, "concept_details", "duplicate_error_analysis",
                 "only one Error Analysis section is allowed")

        if not is_culm:
            if not misconception_sections and not error_analysis_sections:
                # This remains a warning because validation also runs during
                # intermediate generation stages, before the dedicated issue
                # analysis pass.  Final refinement guarantees one or both.
                _add(
                    errors, i, "concept_details",
                    "missing_misconception_or_error_analysis",
                    "normal concepts require Misconceptions, Error Analysis, or both",
                    "warning",
                )
            if (
                misconception_sections
                and error_analysis_sections
                and misconception_sections[0][0] > error_analysis_sections[0][0]
            ):
                _add(
                    errors, i, "concept_details", "issue_section_order",
                    "when both are present, Misconceptions must precede Error Analysis",
                )

        for _, label, _ in misconception_sections:
            if label.lower() != "misconceptions":
                _add(
                    errors, i, "concept_details", "noncanonical_issue_label",
                    "use the canonical 'Misconceptions:' section label",
                    "warning",
                )
        for _, label, _ in error_analysis_sections:
            if re.sub(r"\s+", " ", label.lower()) != "error analysis":
                _add(
                    errors, i, "concept_details", "noncanonical_issue_label",
                    "use the canonical 'Error Analysis:' section label",
                    "warning",
                )

        misconception = misconception_sections[0][2] if misconception_sections else ""
        error_analysis = error_analysis_sections[0][2] if error_analysis_sections else ""
        misconception_is_generic = bool(
            misconception and _is_generic_misconception(misconception)
        )
        if misconception_is_generic:
            _add(errors, i, "concept_details", "generic_misconception",
                 "Misconceptions must name a concept-specific incorrect belief or interpretation")
        if (
            misconception
            and not misconception_is_generic
            and not is_valid_misconception(misconception)
        ):
            _add(
                errors, i, "concept_details", "misconception_framing",
                "Misconceptions must state a learner's incorrect belief or interpretation, not a correction or application mistake",
            )

        error_analysis_is_generic = bool(
            error_analysis and _is_generic_error_analysis(error_analysis)
        )
        if error_analysis_is_generic:
            _add(
                errors, i, "concept_details", "generic_error_analysis",
                "Error Analysis must name a concept-specific mistake",
            )
        if (
            error_analysis
            and not error_analysis_is_generic
            and not is_valid_error_analysis(error_analysis)
        ):
            _add(
                errors, i, "concept_details", "error_analysis_framing",
                "Error Analysis must state a plausible procedural, computational, representational, or reasoning mistake made while applying the concept, not a belief or correction",
            )
        if (
            misconception
            and error_analysis
            and issue_sections_overlap(misconception, error_analysis)
        ):
            _add(
                errors, i, "concept_details", "issue_section_overlap",
                "Misconceptions and Error Analysis must describe distinct learner issues",
            )
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
            if _DESCRIPTION_SECTION_REF_RE.search(desc):
                _add(
                    errors, i, "concept_details",
                    "section_number_in_description",
                    "Description cites a textbook section number instead of the idea",
                    "warning",
                )
            if _EMPTY_IMAGE_ALT_RE.search(details):
                _add(
                    errors, i, "concept_details", "empty_image_alt",
                    "Shipped images need a source-grounded figure caption/alt",
                    "warning",
                )
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
