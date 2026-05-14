from app import bulk_import as bi


def test_canonical_field_counts():
    assert len(bi.OBJECTIVE_FIELDS) == 63
    assert len(bi.SUBJECTIVE_FIELDS) == 90
    assert len(bi.DESCRIPTIVE_FIELDS) == 372


def test_section_bands_sum_to_field_counts():
    for kind in ("objective", "subjective", "descriptive"):
        assert sum(span for _, span in bi.SECTION_BANDS[kind]) == len(bi.FIELDS_BY_KIND[kind])


def test_question_label_appears_in_group_and_question_bands():
    # Objective: once in the Group band, once in the Question band.
    assert bi.OBJECTIVE_FIELDS.count("question_label") == 2
    # Descriptive's group band repeats it too.
    assert bi.DESCRIPTIVE_FIELDS.count("question_label") >= 2


def test_subjective_has_math_keyboard_and_placeholders():
    assert "math_keyboard" in bi.SUBJECTIVE_FIELDS
    assert "placeholder_10" in bi.SUBJECTIVE_FIELDS


def test_descriptive_has_display_answer_and_subquestions():
    assert "display_answer" in bi.DESCRIPTIVE_FIELDS
    assert "sub_question_15" in bi.DESCRIPTIVE_FIELDS
    assert "sq15_keyword_6" in bi.DESCRIPTIVE_FIELDS
