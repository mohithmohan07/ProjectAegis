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


def derive_chapter_meta(chapter_title: str, chapter_display_name: str, *probes: str) -> dict:
    """Best-effort board/grade/subject/code/unit for a chapter from its text fields."""
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
    b = bi.BOARD_CODE_INV.get(board, (board[:2] or "XX").upper())
    s = bi.SUBJECT_CODE_INV.get(subject, (subject[:2] or "XX").upper())
    slug = re.sub(r"[^A-Za-z0-9]", "", (chapter_title or "CH").title())[:12] or "CH"
    return f"{(grade or '00')}{b}{s}_{slug}"


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
