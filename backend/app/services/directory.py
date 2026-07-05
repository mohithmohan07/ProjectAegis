"""Hierarchy parsing + browsing: Board > Grade > Subject > Unit > Chapter > Topic > Concept > Group.

Board / Grade / Subject are not explicit columns in the Bulk Import workbook;
they are encoded in the ID prefixes used throughout it, e.g.::

    10CBMA_CIRCLES_PL   -> grade 10, board CBSE (CB), subject Mathematics (MA)
    10ICEG_Tenses_PL    -> grade 10, board ICSE (IC), subject English Grammar (EG)

``chapter_display_name`` sometimes carries a human hint too, e.g.
"Tenses (10_Grammar_ICSE)". Unit is an optional grouping; when the workbook
gives no signal we default it to "<Subject> Unit".
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import models

# Trailing context is intentionally loose: codes are followed by "_" which is a
# word char, so a trailing \b would never match (e.g. "10CBMA_Circles").
_CODE_PREFIX = re.compile(r"\b(\d{2})([A-Z]{2})([A-Z]{2})(?=[_A-Z0-9 ]|$)")
# Fuller chapter code incl. the chapter slug, e.g. "10CBMA_Circles".
_CHAPTER_CODE = re.compile(r"\b\d{2}[A-Z]{2}[A-Z]{2}_[A-Za-z0-9]+")
# Human chapter title tag, e.g. "Number System (09_Mathematics_CBSE_RS)".
_CHAPTER_HUMAN_TAG = re.compile(
    r"\((\d{2})_([^_()]+)_([^_()]+)(?:_([^_()]+))?\)")

# NCERT source-file convention, e.g.
#   CBSE_NCERT_G08_CH04_QUADRILATERALS.pdf
#   CBSE_NCERT_G08_UN02_VALUES_AND_DISPOSITIONS.pdf   (UN = unit)
_NCERT_SOURCE = re.compile(
    r"\b(CBSE|ICSE)_NCERT_G(\d{2})_(CH|UN)0?(\d+)_([A-Za-z0-9_]+)", re.IGNORECASE)

# Subject inference from free text (folder names like CBSE_NCERT_G08_SocialScience,
# display names, etc). Longest/most specific keys first so SOCIALSCIENCE wins
# over SCIENCE and ENGLISHGRAMMAR over ENGLISH.
_SUBJECT_WORDS = [
    ("SOCIALSCIENCE", "Social Science"),
    ("ENGLISH LANGUAGE", "English Language"),
    ("ENGLISHGRAMMAR", "English Grammar"),
    ("ENGLISHLITERATURE", "English Literature"),
    ("MATHEMATICS", "Mathematics"),
    ("PHYSICS", "Physics"),
    ("CHEMISTRY", "Chemistry"),
    ("BIOLOGY", "Biology"),
    ("ENGLISH", "English"),
    ("SCIENCE", "Science"),
]


def parse_ncert_source(text: str) -> dict | None:
    """Parse the NCERT source-file convention into chapter metadata."""
    m = _NCERT_SOURCE.search(text or "")
    if not m:
        return None
    board, grade, kind, number, slug = m.groups()
    title = " ".join(w.capitalize() for w in slug.split("_") if w)
    return {
        "board": board.upper(), "grade": grade,
        "unit_kind": kind.upper(), "number": int(number), "title": title,
    }


def infer_subject(*probes: str) -> str:
    """Best-effort subject from any text (folder/display names)."""
    for candidate in probes:
        if not candidate:
            continue
        flat = re.sub(r"[^A-Za-z]", "", candidate).upper()
        for key, subject in _SUBJECT_WORDS:
            if key in flat:
                return subject
    return ""


def parse_code_prefix(text: str) -> tuple[str, str, str] | None:
    """Return (grade, board, subject) parsed from any ID-bearing string."""
    if not text:
        return None
    m = _CODE_PREFIX.search(text.upper())
    if not m:
        return None
    grade, board_code, subject_code = m.groups()
    board = bi.BOARD_CODE.get(board_code, board_code)
    subject = bi.SUBJECT_CODE.get(subject_code, subject_code)
    return grade, board, subject


_BOARD_FROM_TAG = {
    "CBSE": "CBSE",
    "ICSE": "ICSE",
    "KSTATE": "Karnataka",
    "MSBSHSE": "Maharashtra",
}
_BOARD_TO_TAG = {
    "CBSE": "CBSE",
    "ICSE": "ICSE",
    "Karnataka": "KSTATE",
    "KSTATE": "KSTATE",
    "Maharashtra": "MSBSHSE",
    "MSBSHSE": "MSBSHSE",
}
_BOOK_TAG_HINTS = [
    ("rs aggarwal", "RS"),
    ("rd sharma", "RD"),
    ("ncert", "NCERT"),
    ("gateway to social science", "Gateway_to_Social_Science"),
    ("morningstar", "MorningStar"),
    ("morning star", "MorningStar"),
    ("selina", "SELINA"),
    ("oswaal", "OSWAAL"),
    ("kts", "KTS"),
    ("karnataka textbook", "KTS"),
    ("msbt", "MSBT"),
    ("m-state", "MSBT"),
    ("symbiosis", "MSBT"),
    ("s chand", "SCHAND"),
    ("arihant", "ARIHANT"),
    ("frank", "FRANK"),
    ("together with", "TW"),
    ("xam idea", "XAMIDEA"),
]

_CBSE_SOCIAL_SCIENCE_COMPONENTS = {
    "history", "geography", "civics", "economics", "political science",
    "social studies", "social science",
}


def parse_chapter_human_tag(text: str) -> dict | None:
    """Parse ``Chapter Name (09_Mathematics_CBSE_RS)`` style tags."""
    m = _CHAPTER_HUMAN_TAG.search(text or "")
    if not m:
        return None
    grade, subject_slug, board_tag, book_tag = m.groups()
    board = _BOARD_FROM_TAG.get(board_tag.upper(), board_tag)
    subject = infer_subject(subject_slug) or subject_slug.replace("_", " ")
    return {
        "grade": grade,
        "board": board,
        "subject": subject,
        "book": (book_tag or "").upper(),
    }


def board_tag_name(board: str) -> str:
    """Board token for chapter title tags, e.g. CBSE, KSTATE, MSBSHSE."""
    key = (board or "").strip()
    if not key:
        return "XX"
    return _BOARD_TO_TAG.get(key, _BOARD_TO_TAG.get(key.upper(), key.upper()))


def book_tag(source: str) -> str:
    """Book/publisher token for chapter title tags, e.g. RS, RD, NCERT."""
    text = (source or "").strip()
    if not text:
        return ""
    lower = text.lower()
    for hint, tag in _BOOK_TAG_HINTS:
        if hint in lower:
            return tag
    words = re.findall(r"[A-Za-z0-9]+", text)
    return "_".join(words) or ""


def primary_book_source(sources: str) -> str:
    """First book source from a comma/semicolon-separated concept source list."""
    for part in (sources or "").replace(";", ",").split(","):
        p = part.strip()
        if p:
            return p
    return ""


def effective_subject_for_tags(board: str, subject: str) -> str:
    """Subject used in export tags/codes.

    CBSE stores History/Geography/Civics/Economics under Social Science for
    import IDs, while ICSE keeps History/Geography as standalone subjects.
    """
    subj = (subject or "").strip()
    if (board or "").strip().upper() == "CBSE" and subj.lower() in _CBSE_SOCIAL_SCIENCE_COMPONENTS:
        return "Social Science"
    return subj


def derive_chapter_meta(chapter_title: str, chapter_display_name: str, *probes: str) -> dict:
    """Best-effort board/grade/subject/code/unit for a chapter from its text fields."""
    for candidate in (chapter_display_name, chapter_title, *probes):
        human = parse_chapter_human_tag(candidate or "")
        if human:
            subject = human["subject"]
            return {
                "grade": human["grade"],
                "board": human["board"],
                "subject": subject,
                "chapter_code": make_chapter_code(
                    human["board"], human["grade"], subject,
                    bi.strip_title_tag(chapter_title) or chapter_title,
                ),
                "unit": f"{subject} Unit",
            }
    for candidate in (chapter_display_name, chapter_title, *probes):
        parsed = parse_code_prefix(candidate or "")
        if parsed:
            grade, board, subject = parsed
            up = (candidate or "").upper()
            full = _CHAPTER_CODE.search(up)
            prefix = _CODE_PREFIX.search(up)
            code = full.group(0) if full else (prefix.group(0) if prefix else "")
            return {
                "grade": grade, "board": board, "subject": subject,
                "chapter_code": code or (chapter_title or "CHAPTER").upper().replace(" ", "_"),
                "unit": f"{subject} Unit",
            }
    # NCERT source-file convention (e.g. CBSE_NCERT_G08_CH04_QUADRILATERALS).
    candidates = (chapter_display_name, chapter_title, *probes)
    for candidate in candidates:
        ncert = parse_ncert_source(candidate or "")
        if ncert:
            subject = infer_subject(*candidates) or "General"
            return {
                "grade": ncert["grade"], "board": ncert["board"], "subject": subject,
                "chapter_code": make_chapter_code(
                    ncert["board"], ncert["grade"], subject, ncert["title"]),
                "unit": f"{subject} Unit",
            }

    # Fallback when nothing parses.
    subject = infer_subject(*candidates) or "General"
    return {
        "grade": "", "board": "", "subject": subject,
        "chapter_code": (chapter_title or "CHAPTER").upper().replace(" ", "_"),
        "unit": f"{subject} Unit" if subject != "General" else "General Unit",
    }


def make_chapter_code(board: str, grade: str, subject: str, chapter_title: str) -> str:
    """Construct an ID prefix-style chapter code for newly created chapters."""
    subject = effective_subject_for_tags(board, subject)
    b = bi.BOARD_CODE_INV.get(board, (board[:2] or "XX").upper())
    s = bi.SUBJECT_CODE_INV.get(subject, (subject[:2] or "XX").upper())
    slug = re.sub(r"[^A-Za-z0-9]", "", (chapter_title or "CH").title())[:12] or "CH"
    return f"{(grade or '00')}{b}{s}_{slug}"


# --------------------------------------------------------------------------- #
# Tag helpers for the Bulk Import title columns
# --------------------------------------------------------------------------- #
# The concept-mapping output embeds a human-readable tag in each title cell so
# every chapter / topic / concept is uniquely addressable:
#   chapter_title  -> "Number System (09_Mathematics_CBSE_RS)"
#   topic_title    -> "Topic 01: Meaning of Social Science (09CBSS_Understanding_Social_Science_PL)"
#   concept_title  -> "What is Social Science (09CBSS_Understanding_Social_Science_PL_Meaning_of_Social_Science)"
# Internal model fields stay CLEAN (no tags); the writer composes these on
# export and the reader strips them on import, so dedupe/round-trip is stable.

_PL = "PL"  # post/pre-learning marker used in the team's label convention


def _underscore_slug(text: str) -> str:
    """Underscore-joined slug preserving the title's original word casing
    (e.g. 'Meaning of Social Science' -> 'Meaning_of_Social_Science')."""
    return "_".join(re.findall(r"[A-Za-z0-9]+", text or "")) or "X"


def _subject_slug(subject: str) -> str:
    """'Social Science' -> 'Social_Science'."""
    return _underscore_slug(subject or "Subject")


def chapter_tag(board: str, grade: str, subject: str, *, book: str = "") -> str:
    """e.g. ('CBSE','09','Mathematics','RS Aggarwal') -> '09_Mathematics_CBSE_RS'."""
    subject = effective_subject_for_tags(board, subject)
    tag = (
        f"{grade or '00'}_{_subject_slug(subject)}_{board_tag_name(board)}"
    )
    book_token = book_tag(book)
    if book_token:
        tag = f"{tag}_{book_token}"
    return tag


def chapter_titled_cell(
    chapter_title: str, board: str, grade: str, subject: str, *, book: str = "",
) -> str:
    """Full chapter title cell value, e.g. 'Number System (09_Mathematics_CBSE_RS)'."""
    clean = bi.strip_title_tag(chapter_title) or chapter_title
    return f"{clean} ({chapter_tag(board, grade, subject, book=book)})"


def code_prefix(board: str, grade: str, subject: str) -> str:
    """ID prefix like '09CBSS' (grade + board code + subject code)."""
    subject = effective_subject_for_tags(board, subject)
    b = bi.BOARD_CODE_INV.get(board, (board[:2] or "XX").upper())
    s = bi.SUBJECT_CODE_INV.get(subject, (subject[:2] or "XX").upper())
    return f"{grade or '00'}{b}{s}"


def chapter_code_full(board: str, grade: str, subject: str, chapter_title: str) -> str:
    """Full (non-truncated) chapter code, e.g. '09CBSS_Understanding_Social_Science'."""
    return f"{code_prefix(board, grade, subject)}_{_underscore_slug(chapter_title)}"


def topic_tag(board: str, grade: str, subject: str, chapter_title: str) -> str:
    """e.g. '09CBSS_Understanding_Social_Science_PL'."""
    return f"{chapter_code_full(board, grade, subject, chapter_title)}_{_PL}"


def concept_tag(board: str, grade: str, subject: str, chapter_title: str,
                topic_title: str) -> str:
    """e.g. '09CBSS_Understanding_Social_Science_PL_Meaning_of_Social_Science'."""
    return f"{topic_tag(board, grade, subject, chapter_title)}_{_underscore_slug(topic_title)}"


# --------------------------------------------------------------------------- #
# Browsing
# --------------------------------------------------------------------------- #

def tree(db: Session) -> list[dict]:
    """Full Board > Grade > Subject > Unit > Chapter tree (chapters carry counts)."""
    chapters = db.query(models.Chapter).order_by(models.Chapter.id).all()
    root: dict = {}
    for ch in chapters:
        board = root.setdefault(ch.board or "Unknown", {})
        grade = board.setdefault(ch.grade or "—", {})
        subject = grade.setdefault(ch.subject or "General", {})
        unit = subject.setdefault(ch.unit or "General Unit", [])
        unit.append({
            "id": ch.id,
            "chapter_code": ch.chapter_code,
            "chapter_title": ch.chapter_title,
            "chapter_display_name": ch.chapter_display_name,
            "topic_count": len(ch.topics),
            "concept_count": sum(len(t.concepts) for t in ch.topics),
        })
    return [
        {
            "board": b,
            "grades": [
                {
                    "grade": g,
                    "subjects": [
                        {
                            "subject": s,
                            "units": [
                                {"unit": u, "chapters": chs}
                                for u, chs in sorted(units.items())
                            ],
                        }
                        for s, units in sorted(subjects.items())
                    ],
                }
                for g, subjects in sorted(grades.items())
            ],
        }
        for b, grades in sorted(root.items())
    ]


def chapter_detail(db: Session, chapter_id: int) -> dict | None:
    ch = db.get(models.Chapter, chapter_id)
    if not ch:
        return None
    return {
        "id": ch.id,
        "chapter_code": ch.chapter_code,
        "chapter_title": ch.chapter_title,
        "chapter_display_name": ch.chapter_display_name,
        "board": ch.board,
        "grade": ch.grade,
        "subject": ch.subject,
        "unit": ch.unit,
        "topics": [
            {
                "id": t.id,
                "topic_title": t.topic_title,
                "topic_display_name": t.topic_display_name,
                "pre_post_learning": t.pre_post_learning,
                "concepts": [
                    {
                        "id": c.id,
                        "concept_title": c.concept_title,
                        "concept_display_name": c.concept_display_name,
                        "parent_concept": c.parent_concept,
                        "sources": c.sources,
                        "group_count": len(c.groups),
                        "question_count": sum(len(g.questions) for g in c.groups),
                    }
                    for c in t.concepts
                ],
            }
            for t in ch.topics
        ],
    }


def resolve_scope_concepts(db: Session, scope_type: str, scope_ids: list[int]) -> list[models.Concept]:
    """Expand a chapter/topic/concept scope selection down to concrete concepts.

    Question content always lives at the concept level, so chapter- and
    topic-level scopes fan out to all concepts beneath them.
    """
    concepts: list[models.Concept] = []
    if scope_type == "chapter":
        for ch in db.query(models.Chapter).filter(models.Chapter.id.in_(scope_ids)):
            for t in ch.topics:
                concepts.extend(t.concepts)
    elif scope_type == "topic":
        for t in db.query(models.Topic).filter(models.Topic.id.in_(scope_ids)):
            concepts.extend(t.concepts)
    elif scope_type == "concept":
        concepts = db.query(models.Concept).filter(models.Concept.id.in_(scope_ids)).all()
    # De-dup preserving order.
    seen: set[int] = set()
    unique: list[models.Concept] = []
    for c in concepts:
        if c.id not in seen:
            seen.add(c.id)
            unique.append(c)
    return unique
