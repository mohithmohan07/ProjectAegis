from app import bulk_import as bi


def test_canonical_field_counts():
    # vs the legacy layout: + concept_source (end of Concept band),
    # + parent_concept, - keywords, - related_concepts (Concept band),
    # + question_text (last column), + group_question_labels (Group band).
    assert len(bi.OBJECTIVE_FIELDS) == 65
    assert len(bi.SUBJECTIVE_FIELDS) == 92
    assert len(bi.DESCRIPTIVE_FIELDS) == 374


def test_dropped_and_added_concept_columns():
    # Team request: keywords / related_concepts columns are gone from the
    # canonical layout; parent_concept is a first-class column instead.
    assert "keywords" not in bi.CONCEPT_FIELDS
    assert "related_concepts" not in bi.CONCEPT_FIELDS
    assert bi.CONCEPT_FIELDS[2] == "parent_concept"
    # Legacy band definition is kept for auto-detected old workbooks.
    assert "keywords" in bi.LEGACY_CONCEPT_FIELDS
    assert "related_concepts" in bi.LEGACY_CONCEPT_FIELDS


def test_question_text_is_last_column_everywhere():
    for fields in (bi.OBJECTIVE_FIELDS, bi.SUBJECTIVE_FIELDS, bi.DESCRIPTIVE_FIELDS):
        assert fields[-1] == "question_text"


def test_concept_source_position():
    # concept_source closes the Concept band, right before the Group band.
    idx = bi.OBJECTIVE_FIELDS.index("concept_source")
    assert bi.OBJECTIVE_FIELDS[idx - 1] == "advanced_groups"
    # The Group band now opens with the renamed concept_question_labels column.
    assert bi.OBJECTIVE_FIELDS[idx + 1] == "concept_question_labels"


def test_section_bands_sum_to_field_counts():
    for kind in ("objective", "subjective", "descriptive"):
        assert sum(span for _, span in bi.SECTION_BANDS[kind]) == len(bi.FIELDS_BY_KIND[kind])


def test_group_band_label_columns():
    # The Concept band's trailing label was renamed; the Group band gained a
    # group_question_labels column before related_digicards.
    assert "concept_question_labels" in bi.OBJECTIVE_FIELDS
    assert "group_question_labels" in bi.OBJECTIVE_FIELDS
    gi = bi.OBJECTIVE_FIELDS.index("group_question_labels")
    assert bi.OBJECTIVE_FIELDS[gi + 1] == "related_digicards"
    # The Question band still carries its own question_label.
    assert bi.OBJECTIVE_FIELDS.count("question_label") == 1
    assert "topic_concept_labels" in bi.OBJECTIVE_FIELDS


def test_subjective_has_math_keyboard_and_placeholders():
    assert "math_keyboard" in bi.SUBJECTIVE_FIELDS
    assert "placeholder_10" in bi.SUBJECTIVE_FIELDS


def test_descriptive_has_display_answer_and_subquestions():
    assert "display_answer" in bi.DESCRIPTIVE_FIELDS
    assert "sub_question_15" in bi.DESCRIPTIVE_FIELDS
    assert "sq15_keyword_6" in bi.DESCRIPTIVE_FIELDS
