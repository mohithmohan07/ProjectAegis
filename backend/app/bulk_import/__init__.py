"""Canonical Bulk Import Excel schema.

The Bulk Import workbook is the single source of truth for the integrated
tool. It has three content sheets — Objective, Descriptive, Subjective — each
with TWO header rows:

  row 1: section bands (Chapter / Topic / Concept / Group / Question)
  row 2: the actual field names

**Every name and every position below is DERIVED from the layout registry**
(``bulk_import.layouts``), which reads the owner-supplied, committed format
workbook ``backend/data/Testing/reference_bulk_import/bulk_import_format.xlsx``
(spec-step8 T3.1/T3.1b, slice S7). Nothing here is transcribed: a transcription
sitting in the trust chain is the same defect shape as the trailing-space
``SHEET_OBJECTIVE = "Objective "`` constant that used to hide the whole
Objective sheet from the reader.

Two consequences of the move that callers must know about:

* the target layout carries **no duplicate field name** on any sheet, so a
  column can finally be addressed by its band-qualified name instead of by a
  position derived from a shared constant;
* the row-1 bands are **not contiguous** — the reference Objective sheet
  leaves column 23 (``concept_source``) unbanded and stops banding after
  column 31 — so ``SECTION_BANDS`` carries ``{label, start, end}`` dicts with
  gaps, never ``(label, span)`` runs. ``tests/test_bulk_import_schema.py`` is
  the anti-drift gate that pins every one of these to the registry.

``LEGACY_CONCEPT_FIELDS`` below stays a frozen literal: it describes a layout
older workbooks carry, and deriving it from the current target is exactly the
mistake ``layouts.py``'s frozen ``canonical-*`` entries exist to prevent.
"""
from __future__ import annotations

from . import layouts as _layouts

# The owner's committed format workbook is the layout authority. If it is not
# on disk the registry records ``layout_source_missing`` and this package has
# no positions to offer — a gate on an artifact (CLAUDE.md "gates that refuse
# to accept a broken artifact"), never a guess.
_REFERENCE = _layouts.LAYOUTS.get(_layouts.REFERENCE_LAYOUT_ID)
if _REFERENCE is None:
    raise ImportError(
        "the committed Bulk Import format workbook "
        f"({_layouts.REFERENCE_WORKBOOK_PATH}) is not readable, so layout "
        f"{_layouts.REFERENCE_LAYOUT_ID!r} is not registered and the Bulk "
        "Import column geometry is unknown; registry defects: "
        f"{_layouts.registry_defects()}"
    )

# Sheet names and the sheet ORDER come from the registry (T12/M1). The order
# of this mapping IS the workbook's sheet order — Objective, Descriptive,
# Subjective — and ``writer._new_workbook`` walks it.
SHEET_BY_KIND: dict[str, str] = {
    kind: sheet.sheet_name for kind, sheet in _REFERENCE.sheets.items()
}
SHEET_OBJECTIVE = SHEET_BY_KIND["objective"]
SHEET_DESCRIPTIVE = SHEET_BY_KIND["descriptive"]
SHEET_SUBJECTIVE = SHEET_BY_KIND["subjective"]
SHEET_ORDER: list[str] = list(SHEET_BY_KIND.values())


def _block(kind: str, block: str) -> list[str]:
    return list(_REFERENCE.sheet(kind).block_fields(block))


# --------------------------------------------------------------------------- #
# Shared front bands: Chapter, Topic
# --------------------------------------------------------------------------- #

CHAPTER_FIELDS = _block("objective", "chapter")
TOPIC_FIELDS = _block("objective", "topic")


# --------------------------------------------------------------------------- #
# The Concept band is PER SHEET (it is not shared any more)
#
# [measured on the authority] Objective and Subjective carry ``concept_source``
# as the trailing, unbanded column of the concept block; Descriptive does not
# carry it at all. A single shared ``CONCEPT_FIELDS`` cannot express that, and
# a caller that assumed it computed a wrong row width and had its tail
# truncated in silence (spec-step8 T3.8).
# --------------------------------------------------------------------------- #

def concept_fields(kind: str) -> list[str]:
    """The Concept band of one sheet of the TARGET layout."""
    return _block(kind, "concept")


CONCEPT_FIELDS_BY_KIND: dict[str, list[str]] = {
    kind: _block(kind, "concept") for kind in SHEET_BY_KIND
}

# Concept band of the OLD layout (pre parent_concept column, with keywords /
# related_concepts, pre concept_source). FROZEN LITERAL: legacy workbooks are
# still read and appended correctly, and deriving this from the target would
# silently redefine what "legacy" means the moment the target moves.
LEGACY_CONCEPT_FIELDS = [
    "concept_title", "concept_display_name", "concept_details",
    "keywords", "digicards", "related_concepts",
    "basic_groups", "intermediate_groups", "advanced_groups",
]
LEGACY_CONCEPT_LEN = len(LEGACY_CONCEPT_FIELDS)


# --------------------------------------------------------------------------- #
# Group bands (per sheet — Descriptive's field ORDER differs from Objective's)
# --------------------------------------------------------------------------- #

OBJECTIVE_GROUP_FIELDS = _block("objective", "group")
SUBJECTIVE_GROUP_FIELDS = _block("subjective", "group")
DESCRIPTIVE_GROUP_FIELDS = _block("descriptive", "group")

GROUP_FIELDS_BY_KIND: dict[str, list[str]] = {
    "objective": OBJECTIVE_GROUP_FIELDS,
    "subjective": SUBJECTIVE_GROUP_FIELDS,
    "descriptive": DESCRIPTIVE_GROUP_FIELDS,
}


# --------------------------------------------------------------------------- #
# Question bands
# --------------------------------------------------------------------------- #

OBJECTIVE_QUESTION_FIELDS = _block("objective", "question")
SUBJECTIVE_QUESTION_FIELDS = _block("subjective", "question")
DESCRIPTIVE_QUESTION_FIELDS = _block("descriptive", "question")

QUESTION_FIELDS_BY_KIND: dict[str, list[str]] = {
    "objective": OBJECTIVE_QUESTION_FIELDS,
    "subjective": SUBJECTIVE_QUESTION_FIELDS,
    "descriptive": DESCRIPTIVE_QUESTION_FIELDS,
}


# --------------------------------------------------------------------------- #
# Whole sheets
# --------------------------------------------------------------------------- #

OBJECTIVE_FIELDS = list(_REFERENCE.sheet("objective").fields)
DESCRIPTIVE_FIELDS = list(_REFERENCE.sheet("descriptive").fields)
SUBJECTIVE_FIELDS = list(_REFERENCE.sheet("subjective").fields)

FIELDS_BY_KIND: dict[str, list[str]] = {
    kind: list(sheet.fields) for kind, sheet in _REFERENCE.sheets.items()
}

# Section bands (row 1) as ``{label, start, end}`` dicts, 1-based inclusive,
# in column order. They DO NOT tile the sheet: the reference Objective bands
# cover 30 of its 67 columns. Anything that sums spans to a column count is
# reading the wrong invariant.
SECTION_BANDS: dict[str, list[dict]] = _REFERENCE.bands_by_kind()

# --------------------------------------------------------------------------- #
# Controlled vocabularies (used by the Blueprint UI and column mapping)
# --------------------------------------------------------------------------- #

BOARDS = ["CBSE", "ICSE", "Maharashtra", "Karnataka"]
GRADES = ["06", "07", "08", "09", "10"]
QUESTION_TYPES = ["objective", "subjective", "descriptive"]
GROUP_TYPES = ["Basic", "Intermediate", "Advanced"]

# Common book sources for multi-source tagging (free text is also allowed).
BOOK_SOURCES = [
    "NCERT", "Balbharati", "RD Sharma", "RS Aggarwal", "S Chand", "Arihant",
    "Selina", "Frank", "Together With", "Oswaal", "Xam Idea",
]


def normalize_question_text(text: str) -> str:
    """Normalization used for duplicate-question detection across books."""
    import re as _re2
    return _re2.sub(r"\s+", " ", (text or "")).strip().lower()


# Tags embedded in the title columns of the concept-mapping output, e.g.
# "Number System (09_Mathematics_CBSE_RS)" or
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

    [Katex] x [/Katex] -> x ; [img src=.. alt="d"] -> (Image: d) ;
    [Text](url) -> Text. Newlines are preserved as content.

    Lower-case legacy ``[katex]`` tags remain readable during migration.
    """
    import re as _re3
    s = text or ""
    s = _re3.sub(
        r"\[katex\]\s*(.*?)\s*\[/katex\]",
        r"\1",
        s,
        flags=_re3.DOTALL | _re3.IGNORECASE,
    )
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
