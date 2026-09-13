"""The reviewed-file reader must track content, not declared extent (Q57).

A reviewed workbook declares every formatted row Excel saved, empty or not,
and openpyxl's default loader instantiates a Cell object for each — then
``read_document`` loaded the same file a SECOND time for cached formula
values. Measured on the owner's job 139 Post file: 0.48 MB on disk, 201,580
declared cells, 403,160 Cell objects, 306 MB of RSS, to read 1,416 non-empty
cells worth 0.43 MB. Two lanes doing that at once on a 2 GB machine killed
the process mid-run and returned a bare 502 to the console.
"""
import tracemalloc

from openpyxl import Workbook
from openpyxl.styles import Font

from app.services import reviewed_file_input as rfi


# The owner's job 139 Post file, in shape: three sheets whose declared extent
# is 201,580 cells and whose real content is 1,416.
JOB_139_SHAPE = (("Objective", 1000, 72), ("Descriptive", 220, 440),
                 ("Subjective", 220, 149))


def test_a_sparse_workbook_costs_its_content_not_its_declared_grid(tmp_path):
    """The regression that mattered: cost must not scale with empty cells.

    Measured on this fixture: the two-full-load reader peaked at 43.6 MB;
    streaming peaks at 0.8 MB. The 10 MB bar sits an order of magnitude below
    the old cost and an order above the new one, so it fails loudly if full
    materialisation returns without being brittle about allocator noise.
    """
    wb = Workbook()
    first = True
    for title, rows, columns in JOB_139_SHAPE:
        ws = wb.active if first else wb.create_sheet(title)
        ws.title = title
        first = False
        for index in range(30):
            ws.cell(row=index + 1, column=1, value=f"real content {index}")
        for row in range(1, rows + 1):
            ws.cell(row=row, column=columns).font = Font(name="Calibri")
    path = tmp_path / "sparse.xlsx"
    wb.save(path)
    wb.close()

    # The fixture must actually reproduce the condition, or the assertion
    # below proves nothing.
    import openpyxl
    probe = openpyxl.load_workbook(path)
    declared = sum(ws.max_row * ws.max_column for ws in probe.worksheets)
    probe.close()
    assert declared == 201_580, f"fixture declares {declared} cells, not job 139's shape"

    tracemalloc.start()
    document = rfi.read_document(path, path.name)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    blocks = document["blocks"]
    assert len(blocks) == 90, "every real row of every sheet is still read"
    assert blocks[0]["text"] == "real content 0"
    assert peak < 10_000_000, (
        f"peak heap {peak/1e6:.1f} MB — the reader is materialising the "
        "declared grid again"
    )


def test_formula_cells_still_resolve_to_their_cached_value(tmp_path):
    """The deferred second load must preserve the old formula behaviour."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Objective"
    ws["A1"] = "plain"
    ws["A2"] = "=1+1"
    wb.save(tmp_path / "formula.xlsx")
    wb.close()

    document = rfi.read_document(tmp_path / "formula.xlsx", "formula.xlsx")

    texts = [block["text"] for block in document["blocks"]]
    assert "plain" in texts
    # openpyxl writes no cached result, so the formula text itself is kept —
    # exactly what the two-workbook reader did via its ``is not None`` fallback.
    assert any("=1+1" in text for text in texts)


def test_hidden_sheets_are_still_skipped(tmp_path):
    """Streaming must not change which sheets count as reviewed content."""
    wb = Workbook()
    visible = wb.active
    visible.title = "Objective"
    visible["A1"] = "reviewed row"
    hidden = wb.create_sheet("Receipt")
    hidden["A1"] = "export receipt"
    hidden.sheet_state = "hidden"
    wb.save(tmp_path / "hidden.xlsx")
    wb.close()

    document = rfi.read_document(tmp_path / "hidden.xlsx", "hidden.xlsx")

    texts = [block["text"] for block in document["blocks"]]
    assert "reviewed row" in texts
    assert "export receipt" not in texts
