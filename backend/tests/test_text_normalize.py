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
    assert normalize_board("", filename="UnitChapter_List__CBSE.xlsx") == "CBSE"
    assert normalize_board("", filename="UnitChapter_List__KSTATE.xlsx") == "Karnataka"
    assert normalize_board("", filename="UnitChapter_List__Maharashtra_Board.xlsx") == "Maharashtra"


def test_subject_spelling_variants_collapse_to_one_name():
    """The Karnataka sheets spell this both ways across grades.

    Both spellings reached the directory, so the same subject appeared
    twice in the dropdown.
    """
    from app.services.text_normalize import normalize_subject

    assert normalize_subject("Rythmic Activities") == "Rhythmic Activities"
    assert normalize_subject("Rhythmic Activities") == "Rhythmic Activities"
    assert normalize_subject("rythmic activities") == "Rhythmic Activities"


def test_ncf_catalogue_metadata_is_removed_without_losing_title_text():
    assert normalize_board("ncf") == "NCF"
    assert normalize_board("", filename="Unit-Chapter List_ NCF(1).xlsx") == "NCF"
    assert normalize_unit("Prose (01_NCF)") == "Prose"
    assert normalize_chapter("Seasons(01_EVS_NCF)") == "Seasons"
    assert normalize_chapter("Isn't It Magical (02_English_NCF)") == "Isn't It Magical"
    assert normalize_chapter("Our Earth (Part 1)").casefold() == "our earth (part 1)"
    assert normalize_chapter("IT Tools") == "IT Tools"
