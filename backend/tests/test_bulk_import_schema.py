from app import bulk_import as bi


def test_canonical_field_counts():
    # +2 vs the legacy layout: concept_source (end of Concept band) and
    # question_text (last column of every sheet).
    assert len(bi.OBJECTIVE_FIELDS) == 65
    assert len(bi.SUBJECTIVE_FIELDS) == 92
    assert len(bi.DESCRIPTIVE_FIELDS) == 374


def test_question_text_is_last_column_everywhere():
    for fields in (bi.OBJECTIVE_FIELDS, bi.SUBJECTIVE_FIELDS, bi.DESCRIPTIVE_FIELDS):
        assert fields[-1] == "question_text"


def test_concept_source_position():
    # concept_source closes the Concept band, right before the Group band.
    idx = bi.OBJECTIVE_FIELDS.index("concept_source")
    assert bi.OBJECTIVE_FIELDS[idx - 1] == "advanced_groups"
    assert bi.OBJECTIVE_FIELDS[idx + 1] == "question_label"


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
