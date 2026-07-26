"""Validation for generated concept-map rows before deposit.

The checks are deliberately structured so the LLM repair pass can receive
precise row/field/code feedback instead of a vague "quality is poor" message.
"""
from __future__ import annotations

import re
import unicodedata
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
    r"\b(?:MMDs?|Examples?\.?\s*\d+(?:\.\d+)*(?![\d.]|\s*:)|"
    r"Fig(?:ure)?s?\.?\s*\d+(?:\.\d+)*|"
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
_EXAMPLE_SPLIT_RE = re.compile(
    r"\bExamples?(?:\s+0*\d+)?\s*:\s*", re.IGNORECASE)
_EXAMPLE_MARKER_RE = re.compile(
    r"\b(?P<label>Examples?)(?:\s+(?P<number>0*\d+))?\s*:",
    re.IGNORECASE,
)
_CASE_RAW_QUESTION_RE = re.compile(
    r"^(?:solve|simplify|find|write|identify|expand|compare|calculate|"
    r"rationalise|express|evaluate|convert|draw|label|explain|prove|"
    r"describe|discuss|analyse|analyze|examine|interpret|outline|assess|"
    r"state|list|mention|account|justify|trace|distinguish|define|"
    r"what|why|how|who|when|where|which)\b",
    re.IGNORECASE,
)
_EXAMPLE_SEGMENT_RE = re.compile(
    r"(\bExamples?(?:\s+0*\d+)?\s*:\s*)(.*?)"
    r"(?=\bExamples?(?:\s+0*\d+)?\s*:|"
    r"\b(?:Case|Type)\s+\d{1,2}:|\s+//\s+|$)",
    re.IGNORECASE | re.DOTALL,
)
_TYPE_SEGMENT_RE = re.compile(
    r"\b(?:Miscellaneous\s+)?Type\s+\d{1,2}:\s*(?P<body>.*?)"
    r"(?=\b(?:Miscellaneous\s+)?Type\s+\d{1,2}:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
# A figure-dependent source question must carry the corresponding image in the
# same rendered Example.  Keep suffixes such as ``Fig. 14(a)`` and decimal
# identifiers such as ``Fig. 11.10`` intact.
_EXPLICIT_FIGURE_REFERENCE_RE = re.compile(
    r"\b(?:fig(?:ure)?s?\.?\s*)(?P<number>\d+(?:\.\d+)*"
    r"(?:\s*\([a-z]\))?)(?!\w)",
    re.IGNORECASE,
)
_CANONICAL_IMAGE_TAG_RE = re.compile(
    r'\[img\s+src="(?P<src>https?://[^"]+)"\s+'
    r'alt="(?P<alt>[^"]+)"[^\]]*\]',
    re.IGNORECASE,
)
_CANONICAL_ANALYSIS_LABEL = "Misconception/ Error Analysis"
_CANONICAL_ANALYSIS_CONTENT_RE = re.compile(
    r"^\s*Misconceptions:\s*(?P<misconception>.+?)\s*;\s*"
    r"Error Analysis:\s*(?P<error_analysis>.+?)\s*$",
    re.DOTALL,
)
# A malformed newline-boundary combined label can leave ``Misconception/`` at
# the end of Description even while a later canonical section exists. Treat
# that residual prefix as a strict-format error.
_ORPHAN_ANALYSIS_PREFIX_RE = re.compile(
    r"(?im)^[ \t]*Misconceptions?[ \t]*/[ \t]*(?=\r?$|\r?\n|//)",
)
_GENERIC_CASE_DEFINITION_RE = re.compile(
    r"^(?:"
    r"(?:practice(?:\s+(?:sets?|tasks?|questions?|problems?|examples?|"
    r"exercises?))?|questions?|problems?|examples?|applications?|"
    r"case\s+stud(?:y|ies))|"
    r"(?:(?:source|inventory|textbook|chapter|classroom|assessment|review|"
    r"practice|given|following)\s+)*(?:checkpoints?|activit(?:y|ies)|exercises?|tasks?|"
    r"prompts?|questions?|problems?)(?:\s+(?:labels?|containers?|sets?|"
    r"tasks?|prompts?|questions?|problems?))?(?:\s+\d+(?:\.\d+)*)?|"
    r"(?:use|apply)\s+(?:only\s+)?(?:the\s+)?"
    r"(?:given|provided|following|above)\s+"
    r"(?:information|data|details|facts|material|passage|source)|"
    r"(?:answer|respond\s+to)\s+(?:the\s+)?"
    r"(?:(?:given|provided|following|above)\s+)?(?:questions?|prompts?)|"
    r"(?:complete|attempt|do|work\s+through)\s+(?:the\s+)?"
    r"(?:(?:given|provided|following|above)\s+)?"
    r"(?:activit(?:y|ies)|exercises?|tasks?|questions?|prompts?)|"
    r"(?:discuss|think\s+and\s+discuss|let\s+us\s+discuss)"
    r")$",
    re.IGNORECASE,
)
_CASE_TITLE_INTERROGATIVE_RE = re.compile(
    r"^(?:what|why|how|who|when|where|which)\b",
    re.IGNORECASE,
)
_CASE_TITLE_SOURCE_DETAIL_RE = re.compile(
    r"(?:\d|[=+*/^÷×]|[A-Za-z]\s*-\s*\d|"
    r"['\"][^'\"]{3,}['\"]|\[(?:Katex|img)\b)",
    re.IGNORECASE,
)
_GENERIC_TYPE_DEFINITION_RE = re.compile(
    r"^(?:assessment\s+patterns?|source\s+inventory\s+tasks?|"
    r"answering\s+(?:a\s+)?checkpoint\s+questions?|"
    r"practice(?:\s+(?:sets?|questions?|problems?|examples?|exercises?))?|"
    r"questions?|problems?|examples?|exercises?)$",
    re.IGNORECASE,
)
_MASTERY_MARKER_RE = re.compile(
    r"\b(?:achieving\s+mastery|mastery(?:\s+indicators?)?)\s*[:\-]",
    re.IGNORECASE,
)
_CANONICAL_MASTERY_LINE_RE = re.compile(
    r"\nAchieving Mastery: (?P<statement>[^\r\n]+)$",
)
_GENERIC_MASTERY_STATEMENT_RE = re.compile(
    r"^(?:applying|using|understanding|mastering|doing)\s+"
    r"(?:the|this)\s+(?:concept|topic|idea|material)"
    r"(?:\s+correctly|\s+well|\s+independently)?\.?$",
    re.IGNORECASE,
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
    r"(?:(?<![-\w])(?<!cross\s)(?:chapter\s+)?"
    r"sections?\s+(?:no\.?\s*)?|"
    r"(?<!\w)\u00a7\s*)\d+(?:\.\d+)*(?![\d.])",
    re.IGNORECASE,
)
_DESCRIPTION_TRUNCATED_CLAUSE_RE = re.compile(
    r"\b(?:what|why|how|whether|when|where|because|although|while|if|"
    r"unless|that|which|who|whom|whose)\s+"
    r"(?:students?|learners?|children|they|we|you|he|she|it|one)"
    r"\s*[.!?](?=\s|$)",
    re.IGNORECASE,
)

# Strict Case titles and their numbered Examples should describe the same
# reusable variation. These dimensions intentionally cover only strongly
# mutually exclusive families. A dimension is ignored whenever either side
# names both families (for example, a comparison of series and parallel
# circuits), which keeps the check conservative and extensible.
_CASE_EXAMPLE_SEMANTIC_DIMENSIONS = (
    (
        "connection topology",
        (
            (
                "series",
                re.compile(
                    r"\b(?:series\s+(?:(?:and|or|versus|vs\.?|/)\s+parallel|"
                    r"connections?|combinations?|circuits?|"
                    r"resistors?|arrangements?)|(?:resistors?|components?|"
                    r"loads?|cells?|bulbs?|devices?)\s+(?:connected\s+)?"
                    r"in\s+series|(?:connected|combined|arranged)\s+in\s+"
                    r"series)\b",
                    re.IGNORECASE,
                ),
            ),
            (
                "parallel",
                re.compile(
                    r"\b(?:parallel\s+(?:(?:and|or|versus|vs\.?|/)\s+series|"
                    r"connections?|combinations?|circuits?|"
                    r"resistors?|arrangements?)|(?:resistors?|components?|"
                    r"loads?|cells?|bulbs?|devices?)\s+(?:connected\s+)?"
                    r"in\s+parallel|(?:connected|combined|arranged)\s+in\s+"
                    r"parallel)\b",
                    re.IGNORECASE,
                ),
            ),
        ),
    ),
    (
        "arithmetic-progression task family",
        (
            (
                "arithmetic means",
                re.compile(r"\barithmetic\s+means?\b", re.IGNORECASE),
            ),
            (
                "progression construction",
                re.compile(
                    r"(?:\b(?:construct(?:ing|ion)?|form(?:ing|ation)?|"
                    r"build(?:ing)?|generat(?:e|ing|ion)|creat(?:e|ing|ion)|"
                    r"write)\b.{0,80}\b(?:arithmetic\s+progressions?|"
                    r"a\.?p\.?)(?!\w)|\b(?:arithmetic\s+progressions?|"
                    r"a\.?p\.?)\s+construction\b)",
                    re.IGNORECASE,
                ),
            ),
        ),
    ),
)
# With embedded Mathpix images, figure/table references are legitimate content
# ("Refer fig. 11.1" next to its image URL); only textual pointers to unshipped
# source artifacts (Example 5, Exercise 1.2, page 14, MMD) stay forbidden.
_SOURCE_ARTIFACT_NO_FIG_RE = re.compile(
    r"\b(?:MMDs?|Examples?\.?\s*\d+(?:\.\d+)*(?![\d.]|\s*:)|"
    r"Exercises?\.?\s*\d+(?:\.\d+)*|Ex\.?\s*\d+(?:\.\d+)*|"
    r"pages?\.?\s*(?:no\.?\s*)?\d+|p\.?\s*\d+)\b",
    re.IGNORECASE,
)
# A Figure reference in a rendered Example has its own strict contract below:
# it must carry the matching canonical image tag.  Do not classify it as a
# generic source artifact first, otherwise cleanup can hide the missing-image
# defect by rewriting ``Fig. N`` to "the figure".  Table references retain the
# ordinary source-artifact behaviour until an image is present.
_SOURCE_ARTIFACT_NO_FIGURE_RE = re.compile(
    r"\b(?:MMDs?|Examples?\.?\s*\d+(?:\.\d+)*(?![\d.]|\s*:)|"
    r"Tables?\.?\s*\d+(?:\.\d+)*|"
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
    r"\bwithout\s+(?:first\s+)?[a-z]+ing\b|"
    r"\bas\s+(?:an?\s+)?(?:final|complete|conclusive)\s+"
    r"(?:answer|explanation|proof|conclusion)\b|"
    r"\bthat\s+(?:cannot|does\s+not|do\s+not|fails?\s+to)\b|"
    r"\bfrom\s+(?:only\s+)?(?:one|a\s+single)\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_PROCEDURAL_MISTAKE_RE = re.compile(
    r"\b(?:change|vary|alter|control)"
    r"(?:s|d|ed|ing|ies|ied)?\s+"
    r"(?:more\s+than\s+one|multiple|two\s+(?:variables?|factors?|"
    r"conditions?)\s+(?:at\s+once|simultaneously))\b|"
    r"\b(?:record|measure|label|plot|classify)"
    r"(?:s|d|ed|ing|ies|ied)?\s+"
    r"(?:an?\s+|the\s+)?(?:incomplete|inaccurate|wrong)\b|"
    r"\b(?:compare|combine|group|classify)"
    r"(?:s|d|ed|ing|ies|ied)?\s+"
    r"(?:unlike|incompatible|unrelated)\b|"
    r"\b(?:draw|reach|make)(?:s|d|es|ed|ing)?\s+"
    r"(?:an?\s+|the\s+)?conclusion\s+"
    r"(?:after|from|using)\s+(?:only\s+)?(?:one|a\s+single)\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_CONTEXTUAL_PROCEDURAL_RE = re.compile(
    r"\b(?:instead\s+of|rather\s+than)\b|"
    r"\bwithout\s+(?:first\s+)?[a-z]+ing\b|"
    r"\bas\s+(?:an?\s+)?(?:final|complete|conclusive)\s+"
    r"(?:answer|explanation|proof|conclusion)\b|"
    r"\bthat\s+(?:cannot|does\s+not|do\s+not|fails?\s+to)\b|"
    r"\bfrom\s+(?:only\s+)?(?:one|a\s+single)\b",
    re.IGNORECASE,
)
_ERROR_ANALYSIS_ONLY_ACTION_RE = re.compile(
    r"\b(?:add|subtract|multiply|divide|copy|quote|use|apply|compare|"
    r"analy[sz]e|select|choose|read|write|label|plot|draw|count|combine|"
    r"interpret|paraphrase|translate|test|check|record|measure|infer|"
    r"conclude|explain|classify|identify|observe)"
    r"(?:s|d|ed|ing|ies|ied)?\s+"
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
_TERMINAL_GENERIC_ANALYSIS_FILLER_RE = re.compile(
    r"^Students\s+may\s+(?:"
    r"assume\s+.+?\s+is\s+a\s+rule\s+that\s+always\s+applies\s+without\s+"
    r"checking\s+its\s+conditions,\s*context,\s*or\s+representation"
    r"|apply\s+.+?\s+as\s+a\s+memorized\s+rule\s+without\s+checking\s+"
    r"the\s+conditions,\s*context,\s*or\s+representation\s+given\s+in\s+"
    r"the\s+problem"
    r")\.?$",
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


def _normalized_case_definition(value: str) -> str:
    """Normalize presentation-only Case-title differences for strict checks."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return _norm(normalized).strip(" .,:;!?-")


def _is_generic_case_definition(value: str) -> bool:
    """Return whether a Case title is an empty task/container label."""
    return bool(
        _GENERIC_CASE_DEFINITION_RE.fullmatch(
            _normalized_case_definition(value)
        )
    )


def _case_title_is_raw_question(value: str) -> bool:
    """Distinguish source questions from meaningful imperative task families."""
    title = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", value or "")
    ).strip()
    if not title:
        return False
    if title.endswith("?") or _CASE_TITLE_INTERROGATIVE_RE.match(title):
        return True
    # An imperative with concrete source values is a one-off question
    # ("Solve 3x + 2 = 14"). An assessable but reusable imperative such as
    # "Discuss why voltage changes with resistance" remains a valid Case title.
    return bool(
        _CASE_RAW_QUESTION_RE.match(title)
        and _CASE_TITLE_SOURCE_DETAIL_RE.search(title)
    )


def _case_example_semantic_mismatches(
    case_title: str, example: str,
) -> list[tuple[str, str, str]]:
    """Return only unambiguous mutually-exclusive Case/Example family pairs."""
    title = unicodedata.normalize("NFKC", case_title or "")
    example_text = unicodedata.normalize("NFKC", example or "")
    mismatches: list[tuple[str, str, str]] = []
    for dimension, families in _CASE_EXAMPLE_SEMANTIC_DIMENSIONS:
        title_families = [
            label for label, pattern in families if pattern.search(title)
        ]
        example_families = [
            label for label, pattern in families if pattern.search(example_text)
        ]
        if (
            len(title_families) == 1
            and len(example_families) == 1
            and title_families[0] != example_families[0]
        ):
            mismatches.append(
                (dimension, title_families[0], example_families[0])
            )
    return mismatches


def _normalized_figure_id(value: str) -> str:
    """Normalize a Figure identifier while retaining decimal/suffix meaning."""
    return re.sub(
        r"\s+", "", (value or "").translate(str.maketrans("．（）", ".()"))
    ).lower()


def _example_figure_ids(text: str) -> list[str]:
    """Return source-order explicit Figure IDs used by one rendered Example."""
    ids: list[str] = []
    source = unicodedata.normalize("NFKC", text or "")
    for match in _EXPLICIT_FIGURE_REFERENCE_RE.finditer(source):
        figure_id = _normalized_figure_id(match.group("number"))
        if figure_id and figure_id not in ids:
            ids.append(figure_id)
    return ids


def _example_image_figure_ids(text: str) -> set[str]:
    """Return Figure IDs named in canonical image-tag captions in one Example."""
    ids: set[str] = set()
    for image in _CANONICAL_IMAGE_TAG_RE.finditer(text or ""):
        ids.update(_example_figure_ids(image.group("alt")))
    return ids


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


def _type_definition(type_body: str) -> str:
    """Return the reusable Type title before its first Case."""
    case_match = _CASE_ANY_RE.search(type_body or "")
    value = (
        type_body[:case_match.start()]
        if case_match else type_body
    )
    return re.sub(r"\s+", " ", value).strip()


def _normalized_type_definition(value: str) -> str:
    """Normalize superficial Type-title differences for duplicate checks."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return _norm(normalized).rstrip(" .,:;!?")


def _is_substantive_mastery_statement(value: str) -> bool:
    """Reject empty, placeholder, or generic final mastery claims."""
    statement = re.sub(r"\s+", " ", value or "").strip()
    words = re.findall(r"\w+", statement, re.UNICODE)
    return bool(
        len(words) >= 4
        and len(statement) >= 12
        and _norm(statement).rstrip(".") not in PLACEHOLDERS
        and not _GENERIC_MASTERY_STATEMENT_RE.fullmatch(statement)
    )


def _normalized_recap_text(value: str) -> str:
    """Normalize only presentation wrappers for exact recap comparison."""
    value = re.sub(r"\[/?Katex\]", " ", value or "", flags=re.IGNORECASE)
    value = re.sub(r"\[img\b[^\]]*\]", " ", value, flags=re.IGNORECASE)
    value = unicodedata.normalize("NFKC", value)
    value = _norm(value)
    return re.sub(r"\s+([,;:.!?])", r"\1", value)


def _source_word_windows(source_text: str, *, width: int = 18) -> set[str]:
    """Return normalized contiguous source spans used to catch copied prose."""
    words = re.findall(r"\w+", (source_text or "").casefold())
    if len(words) < width:
        return set()
    return {
        " ".join(words[index:index + width])
        for index in range(len(words) - width + 1)
    }


def _verbatim_source_description_snippet(
    description: str, source_windows: Collection[str], *, width: int = 18,
) -> str:
    """Return a copied Description span, excluding Types/Examples by design."""
    if not source_windows:
        return ""
    words = re.findall(r"\w+", (description or "").casefold())
    for index in range(max(0, len(words) - width + 1)):
        candidate = " ".join(words[index:index + width])
        if candidate in source_windows:
            return candidate
    return ""


def _issue_sections(
    details: str, label_name: str,
) -> list[tuple[int, str, str]]:
    """Return ordered learner-analysis components from canonical or legacy input.

    The public contract stores both meanings under one top-level
    ``Misconception/ Error Analysis`` section.  Intermediate and older records
    may still use separate sections, so validation deliberately accepts both
    shapes unless its strict final-boundary option is requested.
    """
    matches: list[tuple[int, str, str]] = []
    for index, (label, content) in enumerate(
        concept_refiner.split_sections(details)
    ):
        if concept_refiner.is_combined_analysis_label(label):
            misconception, error_analysis = concept_refiner.analysis_components(
                concept_refiner.join_sections([(label, content)])
            )
            if label_name == "misconception" and misconception:
                matches.append((index, "Misconceptions", misconception))
            elif label_name != "misconception" and error_analysis:
                matches.append((index, "Error Analysis", error_analysis))
            continue
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


def is_terminal_generic_analysis_filler(text: str) -> bool:
    """Whether text is the title-substitution fallback forbidden at final."""
    return bool(_TERMINAL_GENERIC_ANALYSIS_FILLER_RE.fullmatch(
        (text or "").strip()))


def _is_plausible_error_analysis(text: str) -> bool:
    value = (text or "").strip()
    return bool(
        value
        and _ERROR_ANALYSIS_ACTOR_RE.search(value)
        and (
            _ERROR_ANALYSIS_ACTION_RE.search(value)
            or _ERROR_ANALYSIS_SPECIFIC_MISTAKE_RE.search(value)
            or _ERROR_ANALYSIS_PROCEDURAL_MISTAKE_RE.search(value)
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
    specific_mistake = bool(
        _ERROR_ANALYSIS_SPECIFIC_MISTAKE_RE.search(value)
    )
    contextual_procedural_mistake = bool(
        _ERROR_ANALYSIS_CONTEXTUAL_PROCEDURAL_RE.search(value)
    )
    return bool(
        value
        and not _is_generic_error_analysis(value)
        and (
            not _is_error_analysis_belief(value)
            # Some source-specific procedural errors contain an ambiguous
            # verb such as "confuse" or "treat". Preserve them as Error
            # Analysis when the same sentence also names the concrete faulty
            # condition or inference, rather than collapsing them into a
            # belief and replacing their original meaning with boilerplate.
            or contextual_procedural_mistake
        )
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
        misconception_body, error_analysis_body = (
            concept_refiner.analysis_components(normalized)
        )
        misconceptions: list[str] = []
        errors: list[str] = []
        seen_misconceptions: set[str] = set()
        seen_errors: set[str] = set()
        for content, preferred_kind in (
            (misconception_body, "misconception"),
            (error_analysis_body, "error_analysis"),
        ):
            for value in _learner_analysis_statements(content):
                belief_value = concept_refiner._strip_misconception_correction_tail(
                    value
                )
                belief_key = _norm(belief_value)
                error_key = _norm(value)
                valid_misconception = is_valid_misconception(belief_value)
                valid_error = is_valid_error_analysis(value)
                if (
                    preferred_kind == "misconception"
                    and valid_misconception
                    and belief_key not in seen_misconceptions
                ):
                    seen_misconceptions.add(belief_key)
                    misconceptions.append(belief_value)
                elif (
                    preferred_kind == "error_analysis"
                    and valid_error
                    and error_key not in seen_errors
                ):
                    seen_errors.add(error_key)
                    errors.append(value)
                elif (
                    valid_misconception
                    and belief_key not in seen_misconceptions
                ):
                    seen_misconceptions.add(belief_key)
                    misconceptions.append(belief_value)
                elif valid_error and error_key not in seen_errors:
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
            if not concept_refiner.is_learner_analysis_label(label)
        ]
        if misconception or error_analysis:
            combined: list[str] = []
            if misconception:
                combined.append(f"Misconceptions: {misconception}")
            if error_analysis:
                combined.append(f"Error Analysis: {error_analysis}")
            kept.append((
                "Misconception/ Error Analysis",
                "; ".join(combined),
            ))
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
    # A complete yes/no mathematics prompt can be both substantive and very
    # short even though its interrogative auxiliary is not an action verb
    # (for example, "Is 9 a cube?"). Require a question mark and concrete
    # numeric/expression detail so generic fragments such as "Is this correct?"
    # remain invalid.
    if (
        len(words) >= 3
        and re.match(r"^\s*(?:is|are|does|do|can)\b", text, re.IGNORECASE)
        and "?" in text
        and _CASE_SPECIFIC_DETAIL_RE.search(text)
    ):
        return False
    # Concise math tasks: action + concrete expression/value detail.
    return not (
        len(words) >= 3
        and _CASE_TASK_VERB_RE.search(text)
        and _CASE_SPECIFIC_DETAIL_RE.search(text)
    )


def _allowed_source_example_spans(
    case_text: str, allowed_source_examples: Collection[str],
) -> list[tuple[int, int]]:
    """Locate exact source prompts so their internal labels stay content."""
    spans: list[tuple[int, int]] = []
    for source in allowed_source_examples:
        raw = str(source or "")
        canonical = katex_rules.canonicalize_rich_text(raw)
        candidates = {
            re.sub(r"\s+", " ", value).strip()
            for value in (
                raw,
                canonical,
                katex_rules.repair_unwrapped_math(canonical),
            )
            if value
        }
        for candidate in candidates:
            if not candidate:
                continue
            for match in re.finditer(
                re.escape(candidate), case_text, re.IGNORECASE
            ):
                spans.append(match.span())
    return spans


def _structural_example_markers(
    case_text: str, allowed_source_examples: Collection[str],
) -> list[re.Match]:
    """Return Case-level markers, excluding labels quoted inside source text."""
    source_spans = _allowed_source_example_spans(
        case_text, allowed_source_examples)
    return [
        marker
        for marker in _EXAMPLE_MARKER_RE.finditer(case_text)
        if not any(
            start <= marker.start() < end
            for start, end in source_spans
        )
    ]


def _case_examples(
    case_text: str, markers: Collection[re.Match],
) -> list[str]:
    """Split a Case on already-classified structural Example markers."""
    marker_list = list(markers)
    return [
        case_text[
            marker.end():
            marker_list[index + 1].start()
            if index + 1 < len(marker_list)
            else len(case_text)
        ].strip()
        for index, marker in enumerate(marker_list)
        if case_text[
            marker.end():
            marker_list[index + 1].start()
            if index + 1 < len(marker_list)
            else len(case_text)
        ].strip()
    ]


def _case_example_too_short(
    case_text: str, allowed_source_examples: Collection[str] = (),
) -> bool:
    """A Case is 'Case NN: <sub-type definition> Example: <full question> ...'.

    When Example lines exist, each must carry a substantive untruncated
    question. Legacy cases carry the question directly in the Case text.
    """
    case_text = case_text or ""
    markers = _structural_example_markers(
        case_text, allowed_source_examples)
    examples = _case_examples(case_text, markers)
    if examples:
        return any(_example_too_short(ex) for ex in examples)
    return _example_too_short(case_text.strip())


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
    strict_type_hierarchy: bool = False,
    strict_analysis_section: bool = False,
    strict_mastery_statement: bool = False,
    strict_culmination_recap: bool = False,
    source_text: str = "",
) -> dict:
    """Return a structured validation report for concept-map records."""
    errors: list[dict] = []
    topic_title_counts: Counter[tuple[str, str]] = Counter()
    title_counts: Counter[str] = Counter()
    topic_rows: defaultdict[str, list[tuple[int, dict]]] = defaultdict(list)
    topic_type_definition_rows: dict[
        tuple[str, str, tuple[str, ...]], int
    ] = {}
    source_windows = _source_word_windows(source_text)

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
        # Explicit figure references remain visible until strict Type
        # validation verifies their same-Example image tag.  Other source
        # artifacts (and unshipped table references) remain invalid.
        artifact_re = (
            _SOURCE_ARTIFACT_NO_FIG_RE if _IMAGE_URL_RE.search(details)
            else _SOURCE_ARTIFACT_NO_FIGURE_RE
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
        if strict_mastery_statement and not is_culm:
            description = _description_text(details)
            description_markers = list(
                _MASTERY_MARKER_RE.finditer(description)
            )
            all_markers = list(_MASTERY_MARKER_RE.finditer(details))
            canonical_mastery = _CANONICAL_MASTERY_LINE_RE.search(
                description
            )
            if not description_markers:
                _add(
                    errors, i, "concept_details",
                    "missing_mastery_statement",
                    "normal concept Description requires one terminal "
                    "'Achieving Mastery: <substantive text>' line",
                )
            elif canonical_mastery is None:
                _add(
                    errors, i, "concept_details",
                    "mastery_statement_format",
                    "Achieving Mastery must be the final Description line in "
                    "canonical '\\nAchieving Mastery: <text>' format",
                )
            elif not _is_substantive_mastery_statement(
                    canonical_mastery.group("statement")):
                _add(
                    errors, i, "concept_details",
                    "mastery_statement_not_substantive",
                    "Achieving Mastery must state a substantive, "
                    "concept-specific learner capability",
                )
            if len(all_markers) > 1:
                _add(
                    errors, i, "concept_details",
                    "duplicate_mastery_statement",
                    "normal concepts must contain exactly one mastery marker",
                )
            if len(all_markers) > len(description_markers):
                _add(
                    errors, i, "concept_details",
                    "mastery_marker_outside_description",
                    "mastery markers are allowed only at the end of "
                    "Description",
                )
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

        analysis_sections = [
            (label, content)
            for label, content in concept_refiner.split_sections(details)
            if concept_refiner.is_learner_analysis_label(label)
        ]
        if strict_analysis_section and not is_culm:
            canonical_match = None
            orphan_analysis_prefix = bool(
                _ORPHAN_ANALYSIS_PREFIX_RE.search(details))
            if (
                len(analysis_sections) == 1
                and analysis_sections[0][0] == _CANONICAL_ANALYSIS_LABEL
            ):
                canonical_match = _CANONICAL_ANALYSIS_CONTENT_RE.fullmatch(
                    analysis_sections[0][1] or "")
            if canonical_match is None or orphan_analysis_prefix:
                _add(
                    errors, i, "concept_details", "analysis_section_format",
                    "normal concepts require exactly one canonical "
                    "'Misconception/ Error Analysis: Misconceptions: ...; "
                    "Error Analysis: ...' section",
                )
            combined_misconception = (
                canonical_match.group("misconception").strip()
                if canonical_match else ""
            )
            combined_error_analysis = (
                canonical_match.group("error_analysis").strip()
                if canonical_match else ""
            )
            if not combined_misconception:
                _add(
                    errors, i, "concept_details", "missing_misconception",
                    "combined learner analysis must include 'Misconceptions:'",
                )
            if not combined_error_analysis:
                _add(
                    errors, i, "concept_details", "missing_error_analysis",
                    "combined learner analysis must include 'Error Analysis:'",
                )

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
            misconception
            and (
                _is_generic_misconception(misconception)
                or (
                    strict_analysis_section
                    and is_terminal_generic_analysis_filler(misconception)
                )
            )
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
            error_analysis
            and (
                _is_generic_error_analysis(error_analysis)
                or (
                    strict_analysis_section
                    and is_terminal_generic_analysis_filler(error_analysis)
                )
            )
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
                )
            if not is_culm and _DESCRIPTION_TRUNCATED_CLAUSE_RE.search(desc):
                _add(
                    errors,
                    i,
                    "concept_details",
                    "description_truncated_clause",
                    "Description ends a subordinate clause after a bare "
                    "subject; complete or rewrite the broken sentence",
                )
            copied_source = _verbatim_source_description_snippet(
                desc, source_windows)
            if not is_culm and copied_source:
                _add(
                    errors, i, "concept_details",
                    "verbatim_source_description",
                    "Description repeats a long contiguous source passage; "
                    "rewrite it in original teaching language",
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
            if type_body and _TYPE_ANY_RE.search(type_body):
                seen_type_definitions: set[str] = set()
                for type_match in _TYPE_SEGMENT_RE.finditer(type_body):
                    matched_type_body = type_match.group("body") or ""
                    if not _CASE_ANY_RE.search(matched_type_body):
                        _add(
                            errors, i, "concept_details", "type_without_case",
                            "Every Type must contain at least one Case",
                        )
                    if strict_type_hierarchy:
                        type_definition = _type_definition(
                            matched_type_body
                        )
                        normalized_definition = _normalized_type_definition(
                            type_definition
                        )
                        if not type_definition:
                            _add(
                                errors, i, "concept_details",
                                "missing_type_definition",
                                "Each Type must have a meaningful title before "
                                "its first Case",
                            )
                        elif _GENERIC_TYPE_DEFINITION_RE.fullmatch(
                                normalized_definition):
                            _add(
                                errors, i, "concept_details",
                                "generic_type_definition",
                                "Type titles must name a meaningful reusable "
                                "task family, not a generic assessment label",
                            )
                        if normalized_definition:
                            if normalized_definition in seen_type_definitions:
                                _add(
                                    errors, i, "concept_details",
                                    "duplicate_type_definition",
                                    "Type definitions must be unique within "
                                    "each concept row",
                                )
                            seen_type_definitions.add(normalized_definition)
                            if topic and not is_culm:
                                case_definition_keys: list[str] = []
                                for case_match in _CASE_SEGMENT_RE.finditer(
                                    matched_type_body
                                ):
                                    case_text = re.sub(
                                        r"\s+", " ",
                                        case_match.group(1) or "",
                                    ).strip()
                                    markers = _structural_example_markers(
                                        case_text,
                                        allowed_source_examples,
                                    )
                                    case_title = (
                                        case_text[:markers[0].start()].strip()
                                        if markers
                                        else case_text
                                    )
                                    normalized_case = (
                                        _normalized_case_definition(case_title)
                                    )
                                    if normalized_case:
                                        case_definition_keys.append(
                                            normalized_case
                                        )
                                topic_definition_key = (
                                    _norm(topic),
                                    normalized_definition,
                                    tuple(case_definition_keys),
                                )
                                first_row = topic_type_definition_rows.get(
                                    topic_definition_key
                                )
                                if first_row is not None and first_row != i:
                                    _add(
                                        errors, i, "concept_details",
                                        "duplicate_type_definition",
                                        "The same Type and Case definition "
                                        "must not be duplicated across normal "
                                        "concepts within a topic",
                                    )
                                else:
                                    topic_type_definition_rows[
                                        topic_definition_key
                                    ] = i
            if type_body and (not _TYPE_RE.search(type_body) or not _CASE_RE.search(type_body)):
                _add(errors, i, "concept_details", "types_format",
                     "Types must use zero-padded Type NN and Case NN labels")
            for case_match in _CASE_SEGMENT_RE.finditer(type_body or ""):
                case_text = re.sub(r"\s+", " ", case_match.group(1)).strip()
                if _case_example_too_short(
                    case_text, allowed_source_examples
                ):
                    _add(errors, i, "concept_details", "short_case_example",
                         "Case examples should include the full source question/task")
                if strict_type_hierarchy:
                    markers = _structural_example_markers(
                        case_text, allowed_source_examples)
                    case_title = (
                        case_text[:markers[0].start()].strip()
                        if markers else case_text.strip()
                    )
                    examples = _case_examples(case_text, markers)
                    if not case_title:
                        _add(
                            errors, i, "concept_details",
                            "missing_case_definition",
                            "Each Case must define the sub-type before its Examples",
                        )
                    if not markers or not examples:
                        _add(
                            errors, i, "concept_details",
                            "case_without_example",
                            "Each Case must contain at least one numbered Example",
                        )
                    for example_index, example in enumerate(examples, start=1):
                        for (
                            dimension,
                            case_family,
                            example_family,
                        ) in _case_example_semantic_mismatches(
                            case_title, example
                        ):
                            _add(
                                errors,
                                i,
                                "concept_details",
                                "case_example_semantic_mismatch",
                                "Case and Example name mutually exclusive "
                                f"{dimension} families (Case: {case_family}; "
                                f"Example {example_index}: {example_family})",
                            )
                        figure_ids = _example_figure_ids(example)
                        if not figure_ids:
                            continue
                        image_figure_ids = _example_image_figure_ids(example)
                        missing_figure_ids = [
                            figure_id for figure_id in figure_ids
                            if figure_id not in image_figure_ids
                        ]
                        if not missing_figure_ids:
                            continue
                        if not image_figure_ids:
                            _add(
                                errors, i, "concept_details",
                                "figure_reference_without_image",
                                "Every Example that refers to a figure must embed "
                                "a canonical [img] tag with that figure's caption "
                                f"in the same Example (Case Example {example_index}; "
                                f"missing Fig. {', Fig. '.join(missing_figure_ids)})",
                            )
                        else:
                            _add(
                                errors, i, "concept_details",
                                "figure_reference_image_mismatch",
                                "Every Figure reference must match a canonical [img] "
                                "tag caption in the same Example "
                                f"(Case Example {example_index}; missing Fig. "
                                f"{', Fig. '.join(missing_figure_ids)})",
                            )
                    title_key = _norm(case_title)
                    if case_title and (
                        _case_title_is_raw_question(case_title)
                        or any(title_key == _norm(example) for example in examples)
                    ):
                        _add(
                            errors, i, "concept_details",
                            "case_question_not_definition",
                            "Case text must define a reusable variation; the "
                            "complete question belongs in a numbered Example",
                        )
                    elif case_title and _is_generic_case_definition(case_title):
                        _add(
                            errors, i, "concept_details",
                            "generic_case_definition",
                            "Case text must define a meaningful reusable "
                            "variation, not a generic practice label",
                        )
                    numbers = [
                        marker.group("number") or ""
                        for marker in markers
                    ]
                    if (
                        any(
                            marker.group("label").lower() != "example"
                            for marker in markers
                        )
                        or numbers != [
                            f"{number:02d}"
                            for number in range(1, len(markers) + 1)
                        ]
                    ):
                        _add(
                            errors, i, "concept_details",
                            "example_numbering",
                            "Examples must use contiguous zero-padded labels "
                            "starting at Example 01 within every Case",
                        )
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
        culms = [
            (i, r) for i, r in indexed
            if concept_refiner.is_culmination(
                r.get("concept_title") or r.get("concept") or ""
            )
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
        if strict_culmination_recap:
            normal_titles = [
                (row.get("concept_title") or row.get("concept") or "").strip()
                for _, row in normal
            ]
            normal_titles = [title for title in normal_titles if title]
            expected_recap = concept_refiner.recap_text(normal_titles)
            for culmination_index, culmination in culms:
                recap = _description_text(
                    culmination.get("concept_details")
                    or culmination.get("concept_description")
                    or ""
                )
                if not re.match(r"^Recap of\s+\S", recap):
                    _add(
                        errors, culmination_index, "concept_details",
                        "culmination_recap_format",
                        "culmination Description must begin with the detailed "
                        "canonical form 'Recap of ...'",
                    )
                if (
                    _normalized_recap_text(recap)
                    != _normalized_recap_text(expected_recap)
                ):
                    _add(
                        errors, culmination_index, "concept_details",
                        "culmination_recap_missing_concepts",
                        "culmination recap must exactly match the canonical "
                        f"topic recap: {expected_recap}",
                    )
        if require_culmination and topic:
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
