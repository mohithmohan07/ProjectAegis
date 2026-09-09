"""Table transport preserves a stimulus or keeps its repair defect visible."""
from __future__ import annotations

import pytest

from app.bulk_import import from_workbook_rich_text, to_workbook_rich_text
from app.services import canonical_source_phase21_structure as source_display
from app.services import katex_rules as kr


@pytest.mark.parametrize(
    "source",
    [
        "| Material | Quantity | Notes |\n|---|---|---|\n"
        r"| Rock \| Roll | \(\frac{1}{2}\) | |",
        r"\begin{tabular}{|l|l|l|}Material & Quantity & Notes \\ "
        r"Rock \& Roll & \(\frac{1}{2}\) & \\ \end{tabular}",
    ],
)
def test_source_to_workbook_readback_preserves_columns_math_and_blank(source):
    display = source_display.canonical_task_display(source)
    wire = to_workbook_rich_text(display)
    restored = from_workbook_rich_text(wire)
    assert restored == display
    assert restored.count(r"\begin{array}") == 1
    assert restored.count(" & ") == 4
    assert r"\frac{1}{2} & \phantom{\text{Notes}} \\ \hline" in restored
    assert "(blank)" not in restored
    assert "Table row" not in restored
    assert kr.rich_text_issues(restored) == []
    assert kr.answer_cell_issues("Equation", kr.raw_answer_cell("Equation", display)) == []


@pytest.mark.parametrize(
    "source",
    [
        "| Name | Image |\n|---|---|\n"
        '| Shape | [img src="https://example.test/shape.png" alt="Shape"] |',
        r'\begin{tabular}{ll}Name & Image\\Shape & '
        r'[img src="https://example.test/shape.png" alt="Shape"]\end{tabular}',
        r"\begin{tabular}{ll}A & B\\1 & 2 & 3\end{tabular}",
        r"\begin{tabular}{ll}\multicolumn{2}{c}{Heading}\\A&B\end{tabular}",
        r"\begin{tabular}{p{3cm}l}A&B\end{tabular}",
        r"\begin{tabular}{ll}A&B",
        r"\begin{tabular}{ll}A & B\\$$a$$ & 2\end{tabular}",
        "| Name | Link |\n|---|---|\n| A | [Source](https://example.test/source) |",
        "| A | B |\n|---|---|\n| 1 | 2 | 3 |",
        '<table><tr><td>A</td><td><img src="https://example.test/a.png"></td></tr></table>',
    ],
)
def test_visual_or_incomplete_tables_remain_complete_for_api_repair(source):
    rendered = kr.replace_unsupported_tables(source)
    assert rendered == source
    assert kr.canonicalize_rich_text(source) == source
    assert "unsupported_table" in kr.rich_text_issues(rendered)
    assert "unsupported_table" in kr.answer_cell_issues("Phrases", rendered)
    assert "Table row" not in rendered


def test_escaped_separators_are_cell_content_and_header_order_survives():
    source = r"\begin{tabular}{|l|l|}Research \& development & Share\\"
    source += r"Rock \& Roll & \(50\%\)\end{tabular}"
    rendered = source_display.normalize_task_table_markup(source)
    assert r"\text{Research \& development} & \text{Share}" in rendered
    assert r"\text{Rock \& Roll} & 50\%" in rendered
    assert rendered.count(" & ") == 2
    assert kr.rich_text_issues(rendered) == []


@pytest.mark.parametrize("wrapper", ["[Katex] {} [/Katex]", r"\[{}\]", "$${}$$"])
def test_wrapped_tabular_produces_exactly_one_renderable_array(wrapper):
    source = wrapper.format(r"\begin{tabular}{ll}A&B\\1&2\end{tabular}")
    rendered = kr.canonicalize_rich_text(source)
    assert rendered.count("[Katex]") == 1
    assert rendered.count("[/Katex]") == 1
    assert r"\begin{array}{|l|l|}" in rendered
    assert kr.rich_text_issues(rendered) == []


def test_existing_coordinate_prose_is_not_guessed_back_into_a_table():
    source = "Table row 1, column 1: A; Table row 1, column 2: B"
    assert kr.replace_unsupported_tables(source) == source


def test_converted_source_table_renders_with_the_actual_target_katex_engine():
    from pathlib import Path
    import shutil
    from app.services import katex_render_validation as validation

    engine = Path(__file__).resolve().parents[2] / "frontend/node_modules/katex/package.json"
    if not shutil.which("node") or not engine.is_file():
        pytest.skip("Install the exact target KaTeX engine with frontend npm ci")
    source = (
        r"\begin{tabular}{|l|l|l|}Material & Ratio & Notes \\ "
        r"Rock \& Roll & \(\frac{1}{2}\) & \\ \end{tabular}"
    )
    rendered = source_display.canonical_task_display(source)
    report = validation.inspect_render({"question": rendered})
    assert validation.report_defects(report) == []
    assert report["expression_count"] == 1
    assert len(report["expressions"][0]["rendered_sha256"]) == 64


@pytest.mark.parametrize("wrapper", ["[Katex] {} [/Katex]", r"\[{}\]", "$${}$$"])
def test_mixed_math_table_wrappers_remain_whole_for_api_repair(wrapper):
    source = wrapper.format(r"x + \begin{tabular}{ll}A&B\\1&2\end{tabular} + y")
    rendered = kr.canonicalize_rich_text(source)
    assert rendered == source
    assert "unsupported_table" in kr.rich_text_issues(rendered)
    assert "nested_katex" not in kr.rich_text_issues(rendered)


def test_tex_row_spacing_is_not_rewritten_as_first_cell_data():
    source = r"\begin{tabular}{ll}A&B\\[2pt]1&2\end{tabular}"
    assert kr.canonicalize_rich_text(source) == source
    assert "unsupported_table" in kr.rich_text_issues(source)


@pytest.mark.parametrize("cell", [r"[Katex]x^2[/Katex]", r"\(\frac{1}{2}\)"])
def test_phase3_public_cleaner_keeps_math_inside_its_table(cell):
    from app.services import canonical_source_phase3 as phase3
    source = r"\begin{tabular}{ll}Value & Meaning\\" + cell + r" & Area\end{tabular}"
    rendered = phase3._clean_public_text(source)
    assert r"\begin{array}{|l|l|}" in rendered
    assert rendered.count(r"\hline") == 3
    assert "AEGIS" not in rendered
    assert ("x^2" if "x^2" in cell else r"\frac{1}{2}") in rendered
    assert kr.rich_text_issues(rendered) == []


@pytest.mark.parametrize(
    "source",
    [
        r'\begin{tabular}{ll}Shape & Meaning\\[img src="https://example.test/shape.png" alt="Shape"] & Label\end{tabular}',
        r"\begin{tabular}{ll}\multicolumn{2}{c}{Heading}\\1&2\end{tabular}",
        "| Name | Image |\n|---|---|\n| Shape | ![Shape](https://example.test/shape.png) |",
    ],
)
def test_phase3_public_cleaner_keeps_visual_and_spanning_tables_for_repair(source):
    from app.services import canonical_source_phase3 as phase3
    assert phase3._clean_public_text(source) == source
    assert "unsupported_table" in kr.rich_text_issues(source)


def test_phase3_public_cleaner_preserves_canonical_table_rules_and_spacing():
    from app.services import canonical_source_phase3 as phase3
    source = kr.katex(r"\begin{array}{|c|c|}\hline x\hspace{1em} & y\\\hline 1&2\\\hline\end{array}")
    assert phase3._clean_public_text(source) == source



def test_multiline_tex_cell_uses_whitespace_not_workbook_html_as_its_label():
    from app.bulk_import.presentation import to_display_rich_text
    source = "\\begin{tabular}{ll}Mean\nscore & Frequency\\\\1&2\\end{tabular}"
    rendered = source_display.canonical_task_display(source)
    display = to_display_rich_text(rendered)
    assert r"\text{Mean score}" in display
    assert "<br>" not in display
    assert "\n" not in display


def test_unsupported_visual_table_does_not_stop_surrounding_math_normalization():
    from app.services import canonical_source_phase3 as phase3
    table = r'\begin{tabular}{ll}A & [img src="https://example.test/a.png" alt="A"]\end{tabular}'
    source = r"Before \(x^2\)." + "\n" + table + "\n" + r"After \(y^2\)."
    rendered = phase3._clean_public_text(source)
    assert table in rendered
    assert "[Katex] x^2 [/Katex]" in rendered
    assert "[Katex] y^2 [/Katex]" in rendered
    assert "unsupported_table" in kr.rich_text_issues(rendered)
    assert "raw_math_delimiter" not in kr.rich_text_issues(rendered)



def test_verified_full_table_crop_replaces_complete_table_as_one_owned_image():
    from app.services import canonical_source_phase3 as phase3
    block = {
        "kind": "table", "asset_scope": "full_table",
        "asset_url": "https://example.test/full-table.png",
        "caption": "Circuit symbols", "table_rows": [["Component", "Symbol"], ["Cell", "[[VISUAL:2]]"]],
    }
    canonical = {"kind": "table", "raw_text": r"\begin{tabular}{ll}Component&Symbol\\Cell&<smiles>X</smiles>\end{tabular}"}
    rendered = phase3._resolve_verified_page_candidate(canonical, selected_page={}, selected_block=block)
    assert rendered == '[img src="https://example.test/full-table.png" alt="Circuit symbols"]'
    assert kr.rich_text_issues(rendered) == []


def test_verified_table_projection_requires_whole_visual_not_detached_cell():
    from app.services import canonical_source_phase3 as phase3
    page = {"blocks": [{"kind": "figure", "reading_order": 2, "asset_url": "https://example.test/cell.png"}]}
    visual_table = {"kind": "table", "table_rows": [["Component", "Symbol"], ["Cell", "[[VISUAL:2]]"]]}
    with pytest.raises(ValueError, match="complete table crop"):
        phase3._render_verified_page_block(page, visual_table)
    text_table = {"kind": "table", "table_rows": [["Component", "Count"], ["Cell", "2"]]}
    rendered = phase3._render_verified_page_block({}, text_table)
    assert r"\begin{array}{|c|c|}" in rendered
    assert "Table row" not in rendered



def test_raw_equation_array_comments_are_not_reclassified_as_plain_prose():
    source = "\\begin{array}{|c|c|}x&y\\\\% English comment on the header\n1&2\\end{array}"
    assert kr.raw_equation_cell(source) == source
    assert kr.raw_answer_cell("Equation", source) == source
    assert kr._mask_tex_comments(r"50\% + x") == r"50\% + x"
    assert "comment" not in kr._mask_tex_comments("x\\\\% comment\ny")



def test_markdown_literal_braces_do_not_hide_a_column_separator():
    source = "| Opening { | Closing } |\n|---|---|\n| A | B |"
    rendered = kr.canonicalize_rich_text(source)
    assert r"\text{Opening \{} & \text{Closing \}}" in rendered
    assert rendered.count(" & ") == 2
    assert kr.rich_text_issues(rendered) == []



@pytest.mark.parametrize(
    "source",
    [
        "| | Value |\n|---|---|\n| | 3 |",
        r"\begin{tabular}{cc}[Katex]n[/Katex]&2\\ &3\end{tabular}",
    ],
)
def test_blank_column_without_permitted_source_size_stays_for_api_repair(source):
    assert kr.canonicalize_rich_text(source) == source
    assert "unsupported_table" in kr.rich_text_issues(source)


def test_blank_cell_uses_an_existing_same_column_entry_without_inventing_an_answer():
    source = r"\begin{tabular}{cc}[Katex]n[/Katex]&A\\ &B\\[Katex]7[/Katex]&C\end{tabular}"
    rendered = kr.canonicalize_rich_text(source)
    assert r"\phantom{7} & \text{B}" in rendered
    assert r"\phantom{n}" not in rendered
    assert r"n & \text{A}" in rendered
    assert r"7 & \text{C}" in rendered
    assert kr.rich_text_issues(rendered) == []
