"""Statistics datasets stay complete, ordered and owned through real exports."""
from __future__ import annotations

import copy

from app.bulk_import import assessment_workbook as aw
from app.bulk_import import from_workbook_rich_text
from app.services import canonical_source_phase21_structure as source_display
from app.services import canonical_source_phase221_fallback as fallback
from app.services import katex_render_validation as validation
from app.services import katex_rules as kr


FREQUENCIES = [
    ["Height (cm)", "Frequency"],
    [r"[Katex]0 \leq h < 10[/Katex]", "0"],
    [r"[Katex]10 \leq h < 20[/Katex]", "7"],
    [r"[Katex]20 \leq h \leq 30[/Katex]", "3"],
    ["Total", "10"],
]
RELATIVE = [
    ["Class", "Relative frequency (%)", "Cumulative frequency"],
    ["First", r"[Katex]0\%[/Katex]", "0"],
    ["Second", r"[Katex]\frac{7}{10}\times100\%[/Katex]", "7"],
    ["Third", "", "10"],
]


def _assert_frequency_table(value):
    assert r"\begin{array}{|l|l|}" in value
    assert r"\text{Height (cm)} & \text{Frequency}" in value
    assert r"0 \leq h < 10 & \text{0}" in value
    assert r"10 \leq h < 20 & \text{7}" in value
    assert r"20 \leq h \leq 30 & \text{3}" in value
    assert r"\text{Total} & \text{10}" in value
    assert value.index("0 \\leq h < 10") < value.index("10 \\leq h < 20")
    assert value.index("10 \\leq h < 20") < value.index("20 \\leq h \\leq 30")


def test_marked_math_zero_frequency_and_units_survive_page_table_transport():
    rows = copy.deepcopy(FREQUENCIES)
    rows[1][1] = 0  # Legacy structured data may contain a JSON number.
    original = copy.deepcopy(rows)
    rendered = source_display.canonical_task_display(fallback._render_table(rows))
    _assert_frequency_table(rendered)
    assert rendered.count(r"\hline") == len(rows) + 1
    assert rows == original
    assert kr.rich_text_issues(rendered) == []


def test_page_validation_keeps_numeric_zero_distinct_from_an_empty_table_cell():
    page = fallback.PdfPage(
        page_id="PDF-PAGE-0001", page_number=1, text="Height Frequency",
        image_data_url="data:image/jpeg;base64,AA==", width=600.0, height=800.0,
    )
    rows = [["Height (cm)", "Frequency"], ["0–10", 0], ["10–20", ""]]
    candidate = {"pages": [{"page_id": page.page_id, "confidence": 0.999,
                            "blocks": [{"kind": "table", "reading_order": 1,
                                        "bbox": [100, 100, 900, 500], "confidence": 0.999,
                                        "table_rows": rows}]}]}
    original = copy.deepcopy(candidate)
    normalized, reason = fallback.validate_page_extraction([page], candidate)
    assert reason == ""
    assert normalized["pages"][0]["blocks"][0]["table_rows"] == [
        ["Height (cm)", "Frequency"], ["0–10", "0"], ["10–20", ""],
    ]
    assert candidate == original


def test_verified_page_projection_keeps_zero_frequency_and_intentional_blank():
    from app.services import canonical_source_phase3 as phase3

    block = {"kind": "table", "table_rows": [
        ["Height (cm)", "Frequency"], ["0–10", 0], ["10–20", ""],
    ]}
    original = copy.deepcopy(block)
    rendered = phase3._render_verified_page_block({}, block)
    assert r"\text{0–10} & \text{0}" in rendered
    assert r"\text{10–20} & \phantom{\text{Frequency}}" in rendered
    assert rendered.count(r"\begin{array}") == 1
    assert rendered.count(r"\hline") == 4
    assert kr.rich_text_issues(rendered) == []
    assert block == original


def test_adjacent_markdown_statistics_tables_do_not_merge_their_datasets():
    source = (
        "| Interval | Frequency |\n|---|---|\n| 0–10 | 0 |\n| 10–20 | 7 |\n"
        "| Class | Relative frequency (%) | Total |\n|---|---|---|\n| Second | 70 | 10 |"
    )
    rendered = kr.canonicalize_rich_text(source)
    assert rendered.count(r"\begin{array}") == 2
    assert rendered.count(r"\end{array}") == 2
    assert rendered.count(r"\hline") == 7
    assert rendered.index(r"\text{10–20}") < rendered.index(r"\text{Relative frequency (\%)}")
    assert kr.rich_text_issues(rendered) == []


def test_ragged_structured_table_is_preserved_for_repair_without_invented_cells():
    rows = [["Class", "Frequency", "Total"], ["First", "0"]]
    rendered = fallback._render_table(rows)
    assert "First & 0 \\\\" in rendered
    assert "First & 0 &" not in rendered
    assert kr.canonicalize_rich_text(rendered) == rendered
    assert "unsupported_table" in kr.rich_text_issues(rendered)


def test_visual_table_repair_keeps_exact_image_url_and_cell_position():
    image = '[img src="https://example.test/class_one.png" alt="First class"]'
    rows = [["Class", "Frequency"], [image, "0"]]
    rendered = fallback._page_context_text({"kind": "table", "table_rows": rows})
    assert image + " & 0" in rendered
    assert "unsupported_table" in kr.rich_text_issues(rendered)
    assert r"class\_one" not in rendered


def _source_with_two_tables():
    def block(kind, order, **values):
        return {"kind": kind, "reading_order": order, "text": "", "source_label": "",
                "latex": "", "table_rows": [], "linked_visual_orders": [],
                "linked_context_orders": [], "caption": "", **values}

    bundle = {"pages": [{"page_id": "PDF-PAGE-0001", "page_number": 1, "blocks": [
        block("heading", 1, text="Grouped observations", heading_level=1),
        block("task", 2, text="Use both tables to compare the grouped observations.",
              source_label="Exercise", linked_context_orders=[3, 4]),
        block("table", 3, table_rows=copy.deepcopy(FREQUENCIES)),
        block("table", 4, table_rows=copy.deepcopy(RELATIVE)),
    ]}]}
    mmd = fallback.render_page_acsd_to_mmd(bundle)
    canonical = fallback.phase2.compile_phase2_source(
        mmd, source_filename="statistics.mmd", consumer_module="build_concepts",
    ).canonical
    fallback.apply_page_acsd_relationships(canonical, bundle)
    return bundle, canonical


def test_two_statistics_tables_reach_the_owning_inventory_task_in_source_order():
    bundle, canonical = _source_with_two_tables()
    task = canonical["tasks"][0]
    context = task["shared_context"]
    _assert_frequency_table(context)
    assert context.count(r"\begin{array}") == 2
    assert context.index(r"\text{Height (cm)}") < context.index(r"\text{Relative frequency (\%)}")
    assert r"\frac{7}{10}\times100\%" in context
    assert r"\phantom{\text{Relative frequency (\%)}}" in context
    owned = task["content_objects"]["shared_context_blocks"]
    assert [row["reading_order"] for row in owned] == [3, 4]
    assert [row["table_rows"] for row in owned] == [FREQUENCIES, RELATIVE]
    item = fallback.phase2.inventory_from_canonical(canonical)["items"][0]
    assert item["shared_context"] == context
    assert item["content_objects"]["shared_context_blocks"] == owned
    # Relationship replay must not duplicate either dataset.
    fallback.apply_page_acsd_relationships(canonical, bundle)
    assert canonical["tasks"][0]["shared_context"] == context


def test_statistics_tables_survive_both_workbooks_and_actual_katex_rendering():
    from app.services import assessment_release_service as release_service
    from tests import test_assessment_master_refiner as fixtures

    _, canonical = _source_with_two_tables()
    context = canonical["tasks"][0]["shared_context"]
    question = "Use both tables to compare the grouped observations.\n" + context
    payload = fixtures._payload()
    concept = payload["concept_snapshot"]["topics"][0]["concepts"][0]
    concept["concept_details"] = (
        "Description: Summarise grouped observations.\n\nTypes\n"
        "Type 01: Compare datasets\nCase 01: Grouped observations\nExample: " + question
    )
    candidate = next(row for row in payload["candidates"]
                     if row["candidate_id"] == fixtures.DESCRIPTIVE_ID)
    candidate["question"] = candidate["question_text"] = question
    snapshot = release_service.snapshot_from_staged_release(payload)
    outputs = aw.build_dual_output(snapshot, fixtures._LEGACY_PROFILE)
    assert outputs["valid"], outputs["manifest"]["read_back"]

    for key in ("concepts_xlsx", "master_xlsx"):
        parsed = aw.parse_workbook(outputs[key])["sheets"]
        concept_rows = [row for sheet in parsed.values() for row in sheet["rows"]
                        if row.get("concept_details")]
        assert concept_rows
        for row in concept_rows:
            restored = from_workbook_rich_text(row["concept_details"])
            assert restored == concept["concept_details"]
            assert restored.count(r"\begin{array}") == 2
            assert restored.index("Example:") < restored.index(r"\begin{array}")

    exported = next(row for row in aw.parse_workbook(outputs["master_xlsx"])["sheets"]["Descriptive"]["rows"]
                    if row["question_label"] == candidate["question_label"])
    assert from_workbook_rich_text(exported["question"]) == question
    assert from_workbook_rich_text(exported["question_text"]) == "\n".join(
        [question, *(part["text"] for part in candidate["sub_questions"])]
    )
    for field in ("question", "question_text"):
        assert exported[field].count(r"\begin{array}") == 2
        _assert_frequency_table(exported[field])

    report = validation.validate_workbooks({
        "post_concepts.xlsx": outputs["concepts_xlsx"],
        "post_master.xlsx": outputs["master_xlsx"],
    })
    assert validation.report_defects(report) == []
    assert report["engine_version"] == "0.18.7"
    assert report["scope"] == "serialized_workbook_cells"
    assert report["checked_expression_count"] == report["expression_count"]
