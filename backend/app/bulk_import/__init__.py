"""Canonical Bulk Import Excel schema.

The Bulk Import workbook is the single source of truth for the integrated
tool. It has three content sheets — Objective, Subjective, Descriptive — each
with TWO header rows:

  row 1: section bands (Chapter / Topic / Concept / Group / Question)
  row 2: the actual field names

Field orders below are reproduced exactly from LATEST_BULK_IMPORT_EXCEL.xlsx.
The answer / sub-question blocks are regular, so they are generated rather
than transcribed. ``question_label`` deliberately appears more than once in a
row — once in the Group band (links a group to a question) and once in the
Question band — so columns are addressed positionally, never by name alone.
"""
from __future__ import annotations

# Sheet names exactly as they appear in the source workbook (note trailing spaces).
SHEET_OBJECTIVE = "Objective "
SHEET_SUBJECTIVE = "Subjective"
SHEET_DESCRIPTIVE = "Descriptive"
SHEET_DOC_LINK = "Doc Link <> Each fields "

SHEET_BY_KIND = {
    "objective": SHEET_OBJECTIVE,
    "subjective": SHEET_SUBJECTIVE,
    "descriptive": SHEET_DESCRIPTIVE,
}

# --------------------------------------------------------------------------- #
# Shared front bands: Chapter, Topic, Concept
# --------------------------------------------------------------------------- #

CHAPTER_FIELDS = [
    "chapter_title", "chapter_display_name", "chapter_duration",
    "pre_topics", "post_topics", "chapter_description",
]
TOPIC_FIELDS = [
    "topic_title", "topic_display_name", "pre_post_learning",
    # Comma-separated list of the concept titles taught under this topic.
    "topic_concept_labels", "related_topics", "topic_description",
]
CONCEPT_FIELDS = [
    "concept_title", "concept_display_name", "concept_details",
    "keywords", "digicards", "related_concepts",
    "basic_groups", "intermediate_groups", "advanced_groups",
    # Multi-source tag (e.g. "NCERT; RD Sharma") — concepts overlap between the
    # books different schools prefer; one concept carries every book it appears
    # in. Appended at the END of the Concept band so all earlier columns keep
    # their positions; the reader auto-detects workbooks without this column.
    "concept_source",
]
# Concept band length of the legacy layout (pre concept_source).
LEGACY_CONCEPT_LEN = len(CONCEPT_FIELDS) - 1

# --------------------------------------------------------------------------- #
# Objective sheet
# --------------------------------------------------------------------------- #

OBJECTIVE_GROUP_FIELDS = [
    # Trailing label of the Concept band: the question labels tagged to the
    # concept (comma-separated). Renamed from the old positional "question_label".
    "concept_question_labels",
    "group_name", "group_display_name", "group_description",
    "group_status", "group_type",
    # The question labels tagged to THIS group (comma-separated).
    "group_question_labels", "related_digicards",
]
OBJECTIVE_QUESTION_FIELDS = (
    [
        "question_label", "question_category", "cognitive_skills",
        "question_source", "question_disclaimer", "question_duration",
        "question_appears_in", "level_of_difficulty", "question", "marks",
    ]
    + [
        f"{prefix}_{n}"
        for n in range(1, 7)
        for prefix in ("answer_type", "answer_content", "correct_answer", "answer_weightage")
    ]
    + ["answer_explanation"]
    # question_text: plain-text question + any shared context (passage,
    # conversation, diagram description...) passed to the AI evaluator.
    # Appended as the LAST column of the sheet so no existing column shifts;
    # the reader auto-detects templates without it (non-breaking).
    + ["question_text"]
)
OBJECTIVE_FIELDS = (
    CHAPTER_FIELDS + TOPIC_FIELDS + CONCEPT_FIELDS
    + OBJECTIVE_GROUP_FIELDS + OBJECTIVE_QUESTION_FIELDS
)

# --------------------------------------------------------------------------- #
# Subjective sheet
# --------------------------------------------------------------------------- #

SUBJECTIVE_GROUP_FIELDS = OBJECTIVE_GROUP_FIELDS
SUBJECTIVE_QUESTION_FIELDS = (
    [
        "question_label", "question_category", "cognitive_skills",
        "question_source", "question_disclaimer", "question_duration",
        "math_keyboard", "question_appears_in", "level_of_difficulty",
        "question", "marks",
    ]
    + [
        f"{prefix}_{n}"
        for n in range(1, 11)
        for prefix in ("answer_type", "answer", "answer_display", "weightage", "placeholder")
    ]
    + ["answer_explanation"]
    + ["question_text"]  # last column; see Objective note
)
SUBJECTIVE_FIELDS = (
    CHAPTER_FIELDS + TOPIC_FIELDS + CONCEPT_FIELDS
    + SUBJECTIVE_GROUP_FIELDS + SUBJECTIVE_QUESTION_FIELDS
)

# --------------------------------------------------------------------------- #
# Descriptive sheet
# --------------------------------------------------------------------------- #

DESCRIPTIVE_GROUP_FIELDS = [
    "concept_question_labels", "group_display_name", "group_description",
    "group_name", "group_status", "group_type",
    "question_label", "group_question_labels", "related_digicards",
]
DESCRIPTIVE_QUESTION_FIELDS = (
    [
        "question_label", "question_category", "cognitive_skills",
        "question_source", "question_disclaimer", "question_duration",
        "math_keyboard", "question_appears_in", "level_of_difficulty",
        "question", "marks", "display_answer",
    ]
    + [
        f"{prefix}_{n}"
        for n in range(1, 11)
        for prefix in ("answer_type", "answer_weightage", "answer_content")
    ]
    + ["answer_explanation"]
)
DESCRIPTIVE_SUBQUESTION_FIELDS = [
    field
    for n in range(1, 16)
    for field in (
        [f"sub_question_{n}", f"sub_question_marks_{n}"]
        + [
            f"sq{n}_{prefix}_{m}"
            for m in range(1, 7)
            for prefix in ("answer_type", "weightage", "keyword")
        ]
    )
]
DESCRIPTIVE_FIELDS = (
    CHAPTER_FIELDS + TOPIC_FIELDS + CONCEPT_FIELDS
    + DESCRIPTIVE_GROUP_FIELDS + DESCRIPTIVE_QUESTION_FIELDS
    + DESCRIPTIVE_SUBQUESTION_FIELDS
    + ["question_text"]  # last column; see Objective note
)

FIELDS_BY_KIND = {
    "objective": OBJECTIVE_FIELDS,
    "subjective": SUBJECTIVE_FIELDS,
    "descriptive": DESCRIPTIVE_FIELDS,
}

# Section bands (row 1) as (label, span) in column order.
SECTION_BANDS = {
    "objective": [
        ("Chapter", len(CHAPTER_FIELDS)),
        ("Topic", len(TOPIC_FIELDS)),
        ("Concept", len(CONCEPT_FIELDS) + 1),   # +1: trailing question_label
        ("Group", len(OBJECTIVE_GROUP_FIELDS) - 1),
        ("Question", len(OBJECTIVE_QUESTION_FIELDS)),
    ],
    "subjective": [
        ("Chapter", len(CHAPTER_FIELDS)),
        ("Topic", len(TOPIC_FIELDS)),
        ("Concept", len(CONCEPT_FIELDS) + 1),
        ("Group", len(SUBJECTIVE_GROUP_FIELDS) - 1),
        ("Question", len(SUBJECTIVE_QUESTION_FIELDS)),
    ],
    "descriptive": [
        ("Chapter", len(CHAPTER_FIELDS)),
        ("Topic", len(TOPIC_FIELDS)),
        ("Concept", len(CONCEPT_FIELDS) + 1),
        ("Group", len(DESCRIPTIVE_GROUP_FIELDS) - 1),
        # +1: trailing question_text column.
        ("Question", len(DESCRIPTIVE_QUESTION_FIELDS) + len(DESCRIPTIVE_SUBQUESTION_FIELDS) + 1),
    ],
}

# --------------------------------------------------------------------------- #
# Controlled vocabularies (used by the Blueprint UI and column mapping)
# --------------------------------------------------------------------------- #

BOARDS = ["CBSE", "ICSE", "Maharashtra", "Karnataka"]
GRADES = ["06", "07", "08", "09", "10"]
QUESTION_TYPES = ["objective", "subjective", "descriptive"]
GROUP_TYPES = ["Basic", "Intermediate", "Advanced"]
GROUP_TYPE_CODE = {"Basic": "BG", "Intermediate": "IG", "Advanced": "AG"}

# Common book sources for multi-source tagging (free text is also allowed).
BOOK_SOURCES = [
    "NCERT", "RD Sharma", "RS Aggarwal", "S Chand", "Arihant",
    "Selina", "Frank", "Together With", "Oswaal", "Xam Idea",
]


def normalize_question_text(text: str) -> str:
    """Normalization used for duplicate-question detection across books."""
    import re as _re2
    return _re2.sub(r"\s+", " ", (text or "")).strip().lower()


# Tags embedded in the title columns of the concept-mapping output, e.g.
# "Understanding Social Science (09_SocialScience_CBSE)" or
# "What is Social Science (09CBSS_..._PL_Meaning_of_Social_Science)". A tag is a
# trailing "(...)" whose body has at least one underscore (so real parentheticals
# like "(C3)" or "(i)" are never stripped). topic_title also carries a leading
# "Topic NN: " number. The model keeps CLEAN titles; these strip on import.
import re as _re_tags

_TITLE_TAG_RE = _re_tags.compile(r"\s*\([A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\)\s*$")
_TOPIC_NUM_RE = _re_tags.compile(r"^\s*Topic\s+\d+\s*:\s*", _re_tags.IGNORECASE)


def strip_title_tag(text: str) -> str:
    """Remove a trailing ``(tag_with_underscores)`` from a title cell."""
    return _TITLE_TAG_RE.sub("", text or "").strip()


def strip_topic_title(text: str) -> str:
    """Remove a leading ``Topic NN:`` and a trailing tag from a topic title."""
    return strip_title_tag(_TOPIC_NUM_RE.sub("", text or "")).strip()


def merge_sources(existing: str, new: str) -> str:
    """Merge multi-value source lists (comma-separated, order-preserving,
    case-insensitive dedupe). Legacy '; '-separated data is normalized to
    commas on the way through — comma is the only supported separator."""
    out: list[str] = []
    seen: set[str] = set()
    for blob in (existing, new):
        for part in (blob or "").replace(";", ",").split(","):
            p = part.strip()
            if p and p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
    return ", ".join(out)


# Standard action-verb form (the gerund forms are legacy and are normalized).
COGNITIVE_SKILLS = [
    "Remember", "Understand", "Apply",
    "Analyse", "Evaluate", "Create",
]
_COGNITIVE_LEGACY = {
    "remembering": "Remember", "understanding": "Understand",
    "applying": "Apply", "analysing": "Analyse", "analyzing": "Analyse",
    "evaluating": "Evaluate", "creating": "Create",
    # canonical values map to themselves (case-insensitive)
    "remember": "Remember", "understand": "Understand", "apply": "Apply",
    "analyse": "Analyse", "analyze": "Analyse",
    "evaluate": "Evaluate", "create": "Create",
}
DIFFICULTY_LEVELS = ["Less", "Moderate", "High"]
# Real assessment sheets contain Easy/Medium/Hard variants — normalize them.
_DIFFICULTY_LEGACY = {
    "easy": "Less", "low": "Less", "less": "Less",
    "medium": "Moderate", "moderate": "Moderate", "average": "Moderate",
    "hard": "High", "difficult": "High", "high": "High",
}


def normalize_difficulty(value: str) -> str:
    v = (value or "").strip()
    return _DIFFICULTY_LEGACY.get(v.lower(), v) if v else v

APPEARS_IN = ["Pre-test", "Post-test", "Worksheet", "Test"]
APPEARS_IN_ALL = ", ".join(APPEARS_IN)
# Legacy composite value used across earlier imports/generations.
_APPEARS_IN_LEGACY = {"pre/post-worksheet/test": APPEARS_IN_ALL}

ANSWER_TYPES = ["Phrases", "Equation", "Image"]
_ANSWER_TYPE_LEGACY = {"words": "Phrases", "phrases": "Phrases",
                       "equation": "Equation", "image": "Image"}

QUESTION_SOURCE_DEFAULT = "UpSchool DB"


def split_multi(value: str) -> list[str]:
    """Split a multi-value field. COMMA is the only supported separator —
    newline, semicolon and pipe are content, never separators."""
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def join_multi(values: list[str]) -> str:
    return ", ".join(v.strip() for v in values if v and v.strip())


def normalize_cognitive_skills(value: str) -> str:
    """Normalize one or more (comma-separated) skills to the standard form."""
    out = []
    for part in split_multi(value):
        out.append(_COGNITIVE_LEGACY.get(part.lower(), part))
    return join_multi(out)


def normalize_appears_in(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return v
    legacy = _APPEARS_IN_LEGACY.get(v.lower())
    if legacy:
        return legacy
    canon = {a.lower(): a for a in APPEARS_IN}
    return join_multi([canon.get(p.lower(), p) for p in split_multi(v)])


def normalize_answer_type(value: str) -> str:
    v = (value or "").strip()
    return _ANSWER_TYPE_LEGACY.get(v.lower(), v) if v else v


def to_plain_text(text: str) -> str:
    """Rich-text bracket formats -> plain text (for question_text).

    [katex] x [/katex] -> x ; [img src=.. alt="d"] -> (Image: d) ;
    [Text](url) -> Text. Newlines are preserved as content.
    """
    import re as _re3
    s = text or ""
    s = _re3.sub(r"\[katex\]\s*(.*?)\s*\[/katex\]", r"\1", s, flags=_re3.DOTALL)
    s = _re3.sub(r'\[img[^\]]*alt="([^"]*)"[^\]]*\]', r"(Image: \1)", s)
    s = _re3.sub(r"\[img[^\]]*\]", "(Image)", s)
    s = _re3.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", s)
    s = _re3.sub(r"[ \t]{2,}", " ", s)
    return s.strip()

QUESTION_CATEGORIES = {
    "objective": [
        "Multiple Choice Question", "Assertion & Reasons",
        "True/False", "Fill in the Blanks",
    ],
    "subjective": [
        "Fill in the Blanks", "Very Short Answer",
        "Short Answer", "Sentence Transformation", "Error Correction",
    ],
    "descriptive": [
        "Long Answer", "Case Based Questions", "Passage Based Questions",
        "Extract Based Questions", "Composition Writing",
    ],
}

# Board / subject codes embedded in chapter & label IDs, e.g. 10CBMA_... .
BOARD_CODE = {"CB": "CBSE", "IC": "ICSE", "MS": "Maharashtra", "KS": "Karnataka"}
BOARD_CODE_INV = {v: k for k, v in BOARD_CODE.items()}
SUBJECT_CODE = {
    "MA": "Mathematics", "PH": "Physics", "BI": "Biology",
    "CH": "Chemistry", "EG": "English Grammar", "EL": "English Literature",
    "LG": "English Language",
    # Combined middle-school subjects (e.g. Class 08 NCERT sources).
    "SC": "Science", "SS": "Social Science", "EN": "English",
    "HI": "Hindi", "SA": "Sanskrit", "GE": "Geography", "HS": "History",
    "CV": "Civics", "EC": "Economics", "CS": "Computer Science",
    "EV": "Environmental Studies",
}
SUBJECT_CODE_INV = {v: k for k, v in SUBJECT_CODE.items()}
