"""Tests for curriculum label normalization."""
from app.services.text_normalize import (
    normalize_board,
    normalize_chapter,
    normalize_grade,
    normalize_subject,
    normalize_unit,
)


def test_normalize_grade():
    assert normalize_grade("6") == "06"
    assert normalize_grade("Grade 10") == "10"
    assert normalize_grade("Class 09") == "09"
    assert normalize_grade("") == ""


def test_normalize_subject_canonical():
    assert normalize_subject("maths") == "Mathematics"
    assert normalize_subject("ENGLISH LANGUAGE") == "English Language"
    assert normalize_subject("social science") == "Social Science"


def test_normalize_chapter_strips_enumeration():
    assert normalize_chapter("1. Real Numbers") == "Real Numbers"
    assert normalize_chapter("Chapter 3: Light - Reflection") == "Light - Reflection"
    assert normalize_chapter("  Circles  ") == "Circles"


def test_normalize_unit_and_board():
    assert normalize_unit("  number  systems  ") == "Number Systems"
    assert normalize_board("", filename="Unit-Chapter List_ CBSE.xlsx") == "CBSE"
    assert normalize_board("", filename="Kstate Syllabus Grade 6-10.xlsx") == "Karnataka"
    assert normalize_board("", filename="Maharashtra Board Chapter List.xlsx") == "Maharashtra"
