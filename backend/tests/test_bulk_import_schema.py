"""The anti-drift gate on the Bulk Import column geometry (spec-step8 S7).

Every assertion in this file used to be a COUNT or a hand-written position
("65 columns", "CONCEPT_FIELDS[2] == 'parent_concept'", "question_text is
last", "the bands sum to the field count"). Counts are a second transcription
of the layout, and a second transcription is exactly what the layout registry
exists to abolish: the file went green while the writer emitted a sheet name
the reader could not find, because both agreed with the same wrong constant.

So the assertions are now MANIFEST EQUALITY. ``app.bulk_import`` and
``app.bulk_import.assessment_workbook`` are the two modules that carry the
layout into the two renderers; this file pins that both of them, field for
field and band for band, are the owner's committed format workbook. It fails
the moment either one drifts, and it says which column drifted.

One invariant deliberately does NOT come back: the old
``sum(span for _, span in SECTION_BANDS[kind]) == len(FIELDS_BY_KIND[kind])``
contiguity assertion. [measured] the reference Objective bands cover 30 of its
67 columns — column 23 (``concept_source``) is unbanded and nothing after
column 31 is banded — so the target CANNOT satisfy it. It is replaced by
equality against the same bands the assessment renderer reads.
"""
from app import bulk_import as bi
from app.bulk_import import assessment_workbook as aw
from app.bulk_import import layouts

_KINDS = ("objective", "descriptive", "subjective")


def _sheet_of(kind: str) -> str:
    return bi.SHEET_BY_KIND[kind]


def test_the_field_lists_are_the_manifest_field_lists():
    for kind in _KINDS:
        assert bi.FIELDS_BY_KIND[kind] == aw.FIELDS[_sheet_of(kind)], kind


def test_the_section_bands_are_the_manifest_bands():
    for kind in _KINDS:
        assert bi.SECTION_BANDS[kind] == aw.BANDS[_sheet_of(kind)], kind


def test_the_sheet_names_and_their_order_are_the_manifest_sheet_order():
    assert list(bi.SHEET_BY_KIND.values()) == aw.SHEET_ORDER
    assert bi.SHEET_ORDER == aw.SHEET_ORDER
    assert (bi.SHEET_OBJECTIVE, bi.SHEET_DESCRIPTIVE, bi.SHEET_SUBJECTIVE) == (
        "Objective", "Descriptive", "Subjective")


def test_both_carriers_agree_with_the_committed_format_workbook_itself():
    """The JSON manifest is a derived cache; the workbook is the authority."""
    reference = layouts.layout(layouts.REFERENCE_LAYOUT_ID)
    for kind in _KINDS:
        sheet = reference.sheet(kind)
        assert bi.FIELDS_BY_KIND[kind] == list(sheet.fields), kind
        assert bi.SECTION_BANDS[kind] == [b.as_dict() for b in sheet.bands], kind
        assert bi.SHEET_BY_KIND[kind] == sheet.sheet_name, kind


def test_the_bands_do_not_tile_the_sheet_and_nothing_may_assume_they_do():
    """The invariant this file used to assert, pinned INVERTED.

    A caller that sums the band spans to get a column count reads 30 for a
    67-column sheet. The gap is real geometry, not a defect.
    """
    objective = bi.SECTION_BANDS["objective"]
    covered = sum(band["end"] - band["start"] + 1 for band in objective)
    assert covered == 30
    assert len(bi.FIELDS_BY_KIND["objective"]) == 67
    # Column 23 (1-based) is concept_source and it carries no band of its own.
    assert bi.FIELDS_BY_KIND["objective"][22] == "concept_source"
    assert not any(
        band["start"] <= 23 <= band["end"] for band in objective)


def test_the_concept_band_is_per_sheet_not_one_shared_constant():
    """Descriptive has no concept_source; Objective and Subjective do."""
    assert "concept_source" in bi.concept_fields("objective")
    assert "concept_source" in bi.concept_fields("subjective")
    assert "concept_source" not in bi.concept_fields("descriptive")
    assert bi.concept_fields("descriptive") == bi.CONCEPT_FIELDS_BY_KIND[
        "descriptive"]
    assert not hasattr(bi, "CONCEPT_FIELDS")


def test_the_target_carries_the_columns_q5_restored_and_no_parent_concept():
    for kind in _KINDS:
        fields = bi.concept_fields(kind)
        assert "keywords" in fields, kind
        assert "related_concepts" in fields, kind
        assert "parent_concept" not in fields, kind
        assert "parent_concept" not in bi.FIELDS_BY_KIND[kind], kind
    # The legacy band definition stays a frozen literal for old workbooks.
    assert "keywords" in bi.LEGACY_CONCEPT_FIELDS
    assert "related_concepts" in bi.LEGACY_CONCEPT_FIELDS
    assert "parent_concept" not in bi.LEGACY_CONCEPT_FIELDS


def test_no_sheet_repeats_a_field_name_so_names_can_address_columns():
    for kind in _KINDS:
        fields = bi.FIELDS_BY_KIND[kind]
        assert len(set(fields)) == len(fields), kind


def test_question_text_sits_where_the_authority_puts_it_not_last():
    """The old assertion was 'question_text is the last column everywhere'."""
    for kind in _KINDS:
        fields = bi.FIELDS_BY_KIND[kind]
        assert "question_text" in fields, kind
        assert fields[-1] != "question_text", kind
        assert fields[fields.index("question") + 1] == "question_text", kind


def test_group_and_question_band_membership_follows_the_authority():
    objective = bi.GROUP_FIELDS_BY_KIND["objective"]
    assert objective == list(
        layouts.sheet(layouts.REFERENCE_LAYOUT_ID, "objective")
        .block_fields("group"))
    # concept_question_labels moved OUT of the Group band into the Concept
    # band, and the Group band's own label column stayed.
    assert "concept_question_labels" not in objective
    assert "concept_question_labels" in bi.concept_fields("objective")
    assert "group_question_labels" in objective
    # Descriptive's Group band no longer carries a second question_label.
    assert "question_label" not in bi.GROUP_FIELDS_BY_KIND["descriptive"]
    assert bi.FIELDS_BY_KIND["descriptive"].count("question_label") == 1


def test_the_answer_and_subquestion_blocks_are_the_authority_s():
    reference = layouts.layout(layouts.REFERENCE_LAYOUT_ID)
    assert reference.sheet("objective").answer_block_numbers == tuple(
        range(1, 7))
    assert reference.sheet("subjective").answer_block_numbers == tuple(
        range(1, 21))
    assert reference.sheet("descriptive").answer_block_numbers == tuple(
        range(1, 11))
    assert reference.sheet("descriptive").sub_question_numbers == tuple(
        range(1, 16))
    assert "placeholder_20" in bi.SUBJECTIVE_FIELDS
    assert "math_keyboard" in bi.SUBJECTIVE_FIELDS
    assert "display_answer" in bi.DESCRIPTIVE_FIELDS
    assert "sq15_keyword_6" in bi.DESCRIPTIVE_FIELDS
