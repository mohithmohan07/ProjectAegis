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
    "concept", "related_topics", "topic_description",
]
CONCEPT_FIELDS = [
    "concept_title", "concept_display_name", "concept_details",
    "keywords", "digicards", "related_concepts",
    "basic_groups", "intermediate_groups", "advanced_groups",
]

# --------------------------------------------------------------------------- #
# Objective sheet
# --------------------------------------------------------------------------- #

OBJECTIVE_GROUP_FIELDS = [
    "question_label",  # group-band label: links group -> question
    "group_name", "group_display_name", "group_description",
    "group_status", "group_type", "related_digicards",
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
)
SUBJECTIVE_FIELDS = (
    CHAPTER_FIELDS + TOPIC_FIELDS + CONCEPT_FIELDS
    + SUBJECTIVE_GROUP_FIELDS + SUBJECTIVE_QUESTION_FIELDS
)

# --------------------------------------------------------------------------- #
# Descriptive sheet
# --------------------------------------------------------------------------- #

DESCRIPTIVE_GROUP_FIELDS = [
    "question_label", "group_display_name", "group_description",
    "group_name", "group_status", "group_type",
    "question_label", "related_digicards",
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
        ("Question", len(DESCRIPTIVE_QUESTION_FIELDS) + len(DESCRIPTIVE_SUBQUESTION_FIELDS)),
    ],
}

# --------------------------------------------------------------------------- #
# Controlled vocabularies (used by the Blueprint UI and column mapping)
# --------------------------------------------------------------------------- #

BOARDS = ["CBSE", "ICSE"]
GRADES = ["08", "09", "10"]
QUESTION_TYPES = ["objective", "subjective", "descriptive"]
GROUP_TYPES = ["Basic", "Intermediate", "Advanced"]
GROUP_TYPE_CODE = {"Basic": "BG", "Intermediate": "IG", "Advanced": "AG"}

COGNITIVE_SKILLS = [
    "Remembering", "Understanding", "Applying",
    "Analysing", "Evaluating", "Creating",
]
DIFFICULTY_LEVELS = ["Less", "Moderate", "High"]

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
BOARD_CODE = {"CB": "CBSE", "IC": "ICSE"}
BOARD_CODE_INV = {v: k for k, v in BOARD_CODE.items()}
SUBJECT_CODE = {
    "MA": "Mathematics", "PH": "Physics", "BI": "Biology",
    "CH": "Chemistry", "EG": "English Grammar", "EL": "English Literature",
    # Combined middle-school subjects (e.g. Class 08 NCERT sources).
    "SC": "Science", "SS": "Social Science", "EN": "English",
}
SUBJECT_CODE_INV = {v: k for k, v in SUBJECT_CODE.items()}
