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


def strip_topic_number(text: str) -> str:
    """Remove only a leading ``Topic NN:`` prefix from a topic cell.

    Keeping this separate from :func:`strip_topic_title` lets the shared
    topic-cell composer remove an old display ordinal without assuming that
    every underscore-bearing parenthetical in a readable title is an
    identity tag.
    """
    return _TOPIC_NUM_RE.sub("", text or "").strip()


def strip_topic_title(text: str) -> str:
    """Remove a leading ``Topic NN:`` and a trailing tag from a topic title."""
    return strip_title_tag(strip_topic_number(text)).strip()


# --------------------------------------------------------------------------- #
# Multi-value cells (Master Governing Contract v2.0 §16, Appendix B.1)
#
# Every exported multi-value cell uses the exact separator ``" | "`` — one
# space, a pipe, one space. A comma is ordinary content: a topic or concept
# name may contain one, so the parser splits ONLY on the pipe. Workbooks
# written before v2.0 carry comma lists; a value with no pipe at all is read
# as such a legacy list so the deployed database keeps loading. A value that
# carries a pipe is never comma-split.
# --------------------------------------------------------------------------- #

LIST_DELIMITER = " | "


def _legacy_or_pipe_parts(value: str, *, legacy_commas: bool = True) -> list[str]:
    text = str(value or "")
    if LIST_DELIMITER.strip() in text:
        # The exact delimiter is " | ". A bare pipe glued to its neighbours
        # ("a|b") is content (contract §16 forbids one inside a value and
        # the read-back records it — ``list_token_defects``), so it is never
        # a split point.
        parts = text.split(LIST_DELIMITER)
        if len(parts) == 1:
            parts = [text]
    elif legacy_commas:
        # Pre-v2.0 export or hand-filled sheet: comma (or the older
        # semicolon) separated. Never applied when a pipe is present, and
        # only where a legacy cell is being READ (import, migration) — an
        # export treats a pipe-free value as exactly one token, because a
        # comma is content.
        parts = text.replace(";", ",").split(",")
    else:
        parts = [text]
    return [part.strip() for part in parts if part.strip()]


def list_token_defects(value: str) -> list[str]:
    """Contract §16 (DEL-001): a literal pipe inside one list token blocks."""

    return [
        f"list token {token!r} contains a literal pipe"
        for token in split_multi(value, legacy_commas=False)
        if "|" in token
    ]


def merge_sources(existing: str, new: str) -> str:
    """Merge multi-value source lists (pipe-delimited, order-preserving,
    case-insensitive dedupe). Legacy comma/semicolon data is normalized to
    the v2.0 pipe delimiter on the way through."""
    out: list[str] = []
    seen: set[str] = set()
    for blob in (existing, new):
        for p in _legacy_or_pipe_parts(blob):
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
    return LIST_DELIMITER.join(out)


# --------------------------------------------------------------------------- #
# Rich cells (contract §17): the workbook projection of a line break is the
# canonical HTML ``<br>``; a paragraph break is ``<br><br>``. The internal
# model keeps real newlines; ONLY the exporter projects, and the reader
# inverts it, so a round trip is byte-stable on the model side.
# --------------------------------------------------------------------------- #

LINE_BREAK = "<br>"
_BR_RE = _re_tags.compile(r"<br\s*/?>", _re_tags.IGNORECASE)


def to_workbook_rich_text(text) -> str:
    """Project internal newlines to ``<br>`` for one workbook cell."""
    value = str(text if text is not None else "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.replace("\n", LINE_BREAK)


def from_workbook_rich_text(text) -> str:
    """Invert :func:`to_workbook_rich_text` on import (``<br/>`` included)."""
    return _BR_RE.sub("\n", str(text if text is not None else ""))


_DURATION_MINUTES_RE = _re_tags.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:min(?:ute)?s?)?\s*$", _re_tags.IGNORECASE)


def duration_minutes_cell(value):
    """The numeric workbook cell for a stored chapter duration (§32).

    The internal model stores ``"200 minutes"``; the contract stores a real
    number. A value that is not a plain minute count (blank, prose, a unit
    other than minutes) projects to blank — never to a guessed number.
    """
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return value if value > 0 else ""
    match = _DURATION_MINUTES_RE.match(str(value or ""))
    if not match:
        return ""
    number = float(match.group(1))
    if number <= 0:
        return ""
    return int(number) if number == int(number) else number


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
APPEARS_IN_ALL = LIST_DELIMITER.join(APPEARS_IN)
# The exact wire literal every question row carries in the four outputs
# (contract §18): the composite spelling, never an expanded list.
APPEARS_IN_WIRE = "Pre/Post-Worksheet/Test"
# The composite wire value expands to the internal purpose list on import.
_APPEARS_IN_LEGACY = {APPEARS_IN_WIRE.lower(): APPEARS_IN_ALL}


def appears_in_wire(value: str) -> str:
    """The workbook cell for an internal ``question_appears_in`` value.

    The complete purpose set — which is what every generated row carries —
    projects to the exact contract literal. A deliberately narrower set (a
    hand-tagged row) is exported as a pipe list of its purposes.
    """
    parts = split_multi(normalize_appears_in(value))
    if not parts:
        return ""
    canon = {a.lower(): a for a in APPEARS_IN}
    normalized = {canon.get(p.lower(), p) for p in parts}
    if normalized == set(APPEARS_IN):
        return APPEARS_IN_WIRE
    return join_multi(parts)

# Contract v2.0 §22–§24: the WORKBOOK carries a lane-exact textual medium —
# ``Words`` on Objective option cells and Subjective answer cells, ``Phrases``
# on Descriptive rubric criteria and keyword cells; ``Equation`` and
# ``Image`` read the same on every sheet. Inside the pipeline the one
# canonical textual medium stays ``Phrases`` (every checker reads one enum),
# the reader accepts both spellings, and the writers project the lane
# literal at the cell (``wire_answer_type``). ``ANSWER_TYPES`` is the
# canonical enum; ``WIRE_ANSWER_TYPES`` is the closed set a cell may carry.
ANSWER_TYPES = ["Phrases", "Equation", "Image"]
WIRE_ANSWER_TYPES = ["Words", "Phrases", "Equation", "Image"]
_ANSWER_TYPE_LEGACY = {"words": "Phrases", "phrases": "Phrases",
                       "equation": "Equation", "image": "Image"}
_WIRE_TEXT_ANSWER_TYPE_BY_SHEET = {
    "objective": "Words",
    "subjective": "Words",
    "descriptive": "Phrases",
}


def wire_answer_type(answer_type: str, sheet_kind: str) -> str:
    """The contract literal a workbook ``answer_type`` cell carries.

    A textual medium projects to the lane's literal (``Words`` on Objective
    and Subjective, ``Phrases`` on Descriptive); ``Equation``/``Image`` pass
    through; an unknown value is returned as given so the enum gates keep
    refusing it.
    """
    canonical = normalize_answer_type(answer_type)
    if canonical == "Phrases":
        return _WIRE_TEXT_ANSWER_TYPE_BY_SHEET.get(
            str(sheet_kind or "").strip().lower(), "Phrases",
        )
    return canonical

# Contract v2.0 §18: ``question_source`` is the run's publication, a frozen
# run variable. The former origin-system default ("UpSchool DB") is retired;
# no writer, renderer or post-generation step borrows a value for it.


def split_multi(value: str, *, legacy_commas: bool = True) -> list[str]:
    """Split a multi-value field on the v2.0 pipe delimiter.

    Contract §16: the parser splits only the list delimiter, never commas —
    a comma is content. With ``legacy_commas`` (the default, for values
    being READ from a legacy workbook, an API input or a pre-migration row)
    a value with no pipe at all is a pre-v2.0 comma list and is read as
    such; the writers pass ``legacy_commas=False`` so a pipe-free value
    exports as exactly one token.
    """
    return _legacy_or_pipe_parts(value, legacy_commas=legacy_commas)


def split_roster(value: str, *, legacy_commas: bool = True) -> list[str]:
    """Split a roster of identified titles (``Title (id)`` items, §15).

    A pipe-delimited value splits exactly like :func:`split_multi`. A
    pre-v2.0 comma list is the one legacy shape a plain comma split reads
    wrongly: every roster item closes with its identity tag, so a comma
    INSIDE a title ("Story Setting, Events and Sequence (06MSEN_..._PL)")
    used to split one topic into two. Here a fragment that does not close
    its tag is glued back onto the fragment that follows it. This parses
    the identity grammar this codebase itself writes; it never judges
    content, and a roster whose items carry no tag at all is read as the
    plain list it is.
    """
    text = str(value or "")
    if LIST_DELIMITER.strip() in text or not legacy_commas:
        return _legacy_or_pipe_parts(text, legacy_commas=False)
    fragments = _legacy_or_pipe_parts(text, legacy_commas=True)
    if not any(fragment.endswith(")") for fragment in fragments):
        return fragments
    items: list[str] = []
    for fragment in fragments:
        if items and not items[-1].endswith(")"):
            items[-1] = f"{items[-1]}, {fragment}"
        else:
            items.append(fragment)
    return items


def join_multi(values: list[str]) -> str:
    """Join list tokens with the exact ``" | "`` delimiter (contract §16)."""
    return LIST_DELIMITER.join(v.strip() for v in values if v and v.strip())


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

# Contract §21 (True/False override): every True or False item is routed to
# Subjective with one placeholder-bound accepted answer, never Objective.
QUESTION_CATEGORIES = {
    "objective": [
        "Multiple Choice Question", "Assertion & Reasons",
        "Fill in the Blanks",
    ],
    "subjective": [
        "Fill in the Blanks", "True/False", "Very Short Answer",
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
