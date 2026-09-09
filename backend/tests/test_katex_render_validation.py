"""Target-engine receipts cover exported formulas without altering artifacts."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import shutil
import subprocess

import openpyxl
import pytest

from app.services import katex_render_validation as validation


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "frontend/scripts/validate-katex.mjs"
ENGINE = REPO / "frontend/node_modules/katex/package.json"
real_engine = pytest.mark.skipif(
    not shutil.which("node") or not ENGINE.is_file(),
    reason="Run npm ci in frontend to install the exact target KaTeX engine",
)


@real_engine
@pytest.mark.parametrize("latex", [
    r"\frac{a_{n}+b}{2}",
    r"\begin{array}{c|c}x&y\\1&2\end{array}",
    r"9.8\,\text{m}\,\text{s}^{-2}",
    r"\angle ABC=60^{\circ}",
    r"\color{#cc0000}{x}",
])
def test_actual_target_engine_renders_supported_math(latex):
    report = validation.inspect_render({"concept_details": f"[Katex]{latex}[/Katex]"})
    assert validation.report_defects(report) == []
    assert report["engine_version"] == "0.18.7"
    assert len(report["expressions"][0]["rendered_sha256"]) == 64


@real_engine
@pytest.mark.parametrize("latex,code", [
    (r"\frac{1}{", "katex_render_invalid"),
    (r"\notAnActualCommand{x}", "katex_render_invalid"),
    (r"\href{https://example.com}{x}", "katex_untrusted_command"),
    (r"\htmlClass{hidden}{x}", "katex_untrusted_command"),
])
def test_engine_failures_are_named_and_input_is_preserved(latex, code):
    payload = {"answer_type": "Equation", "answer_content": latex}
    before = copy.deepcopy(payload)
    report = validation.inspect_render(payload)
    assert report["status"] == "blocked"
    assert code in {v["code"] for v in report["findings"]}
    assert validation.report_defects(report)
    assert payload == before


@real_engine
def test_raw_equations_wrapped_math_and_all_occurrences_share_one_engine_call(monkeypatch):
    calls = []
    actual_run = validation.subprocess.run

    def record_run(*args, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return actual_run(*args, **kwargs)

    monkeypatch.setattr(validation.subprocess, "run", record_run)
    report = validation.inspect_render({
        "question": r"[Katex]\frac{x}{2}[/Katex] and [Katex]\frac{x}{2}[/Katex]",
        "answer_type_1": "Equation", "answer_content_1": r"\frac{x}{2}",
        "sq1_answer_type_1": "Equation", "sq1_keyword_1": r"x^{2}",
        "answer_type_2": "Phrases", "answer_content_2": r"\notAnEquation",
    })
    assert validation.report_defects(report) == []
    assert len(calls) == 1
    assert len(calls[0]["expressions"]) == report["expression_count"] == 2
    assert report["occurrence_count"] == 4


@real_engine
def test_typed_equation_wrappers_fail_and_subjective_placeholders_are_not_math():
    report = validation.inspect_render({
        "question": "The answer is $$a$$.",
        "answer_type_1": "Equation", "answer_1": "[Katex]x[/Katex]",
    })
    assert report["expression_count"] == 1
    assert {v["code"] for v in report["findings"]} == {"katex_equation_wrapped"}


def test_missing_engine_is_visible_and_non_math_does_not_invoke_node(monkeypatch):
    monkeypatch.setenv("AEGIS_KATEX_NODE", "/missing/aegis-node")
    report = validation.inspect_render({"concept_details": "[Katex]x[/Katex]"})
    assert report["status"] == "unavailable"
    assert report["findings"][0]["code"] == "katex_renderer_unavailable"
    report = validation.inspect_render({"concept_details": "Read the poem."})
    assert report["expression_count"] == 0
    assert validation.report_defects(report) == []


def test_unbalanced_wrappers_and_resource_limits_cannot_get_partial_pass(monkeypatch):
    report = validation.inspect_render({"question": "[Katex]x"})
    assert report["status"] == "blocked"
    assert report["findings"][0]["code"] == "katex_wrapper_syntax"
    monkeypatch.setattr(validation, "MAX_EXPRESSIONS", 1)
    report = validation.inspect_render({"question": "[Katex]x[/Katex] [Katex]y[/Katex]"})
    assert report["checked_expression_count"] == 0
    assert report["findings"][0]["code"] == "katex_render_resource_limit"


@real_engine
def test_staged_projection_ignores_audit_templates_but_binds_formula_edits():
    value = {"records": [{
        "topic": "Ratios", "concept": "Comparing ratios",
        "concept_details": r"[Katex]\frac{a}{b}[/Katex]",
        "concept_description": "An unused fallback with [Katex]",
        "_aegis_critic": {"prompt": "Use [Katex] wrappers"},
    }], "chapter_meta": {"chapter_description": "Original teaching."}}
    report = validation.inspect_render(value)
    payload = {**copy.deepcopy(value), validation.REPORT_FIELD: report}
    payload["records"][0]["_aegis_release_status"] = "ready"
    payload["records"][0]["_aegis_release_errors"] = []
    assert validation.readiness_defects(payload) == []
    payload["records"][0]["concept_details"] = "[Katex]x[/Katex]"
    assert any("katex_render_stale" in v for v in validation.readiness_defects(payload))


def _workbook(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Subjective"
    ws.append(["Question", "Answer"])
    fields = list(rows[0])
    ws.append(fields)
    for row in rows:
        ws.append([row.get(field, "") for field in fields])
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


@real_engine
def test_exact_xlsx_readback_includes_raw_subjective_answer_and_every_workbook():
    good = _workbook([{
        "question": "[Katex]x[/Katex] is $$a$$.",
        "answer_type_1": "Equation", "answer_1": "x",
    }])
    bad = _workbook([{
        "question": "Write $$a$$.",
        "answer_type_1": "Equation", "answer_1": r"\frac{2}{",
    }])
    inputs = {"01_concepts.xlsx": good, "02_master.xlsx": bad}
    before = copy.deepcopy(inputs)
    report = validation.validate_workbooks(inputs)
    assert report["scope"] == "serialized_workbook_cells"
    assert report["status"] == "blocked"
    assert report["expression_count"] == 2
    assert report["occurrence_count"] == 3
    assert set(report["workbook_sha256s"]) == set(inputs)
    invalid = next(v for v in report["findings"] if v["code"] == "katex_render_invalid")
    assert invalid["occurrences"][0]["path"].endswith("02_master.xlsx.Subjective.3.answer_1")
    assert inputs == before


def test_failed_readback_and_forged_incomplete_report_are_not_ready():
    report = validation.validate_workbooks({"broken.xlsx": b"not a workbook"})
    assert report["findings"][0]["code"] == "katex_workbook_readback_failed"
    assert validation.report_defects({"version": validation.VERSION, "status": "pass"})


def test_timeout_returns_a_named_unavailable_receipt(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(validation.subprocess, "run", timeout)
    monkeypatch.setattr(validation, "_runtime", lambda: ("node", SCRIPT))
    report = validation.inspect_render({"question": "[Katex]x[/Katex]"})
    assert report["status"] == "unavailable"
    assert report["checked_expression_count"] == 0
    assert "katex_renderer_unavailable" in validation.report_defects(report)[0]


@real_engine
def test_wrong_engine_version_is_not_accepted(monkeypatch):
    monkeypatch.setattr(validation, "KATEX_VERSION", "0.0.0")
    report = validation.inspect_render({"question": "[Katex]x[/Katex]"})
    assert report["status"] == "unavailable"
    assert report["checked_expression_count"] == 0
    assert "found 0.18.7" in report["findings"][0]["message"]


@real_engine
def test_node_input_decodes_utf8_after_chunk_reassembly():
    # The stream may divide the multibyte Greek character between chunks.
    # Sending exactly that boundary catches per-chunk Buffer.toString use.
    latex = r"\text{α}"
    request = json.dumps({"expected_version": validation.KATEX_VERSION,
                          "expressions": [{"id": "unicode", "latex": latex}]},
                         ensure_ascii=False).encode("utf-8")
    cut = request.index("α".encode("utf-8")) + 1
    process = subprocess.Popen([shutil.which("node"), str(SCRIPT)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    assert process.stdin is not None
    process.stdin.write(request[:cut])
    process.stdin.flush()
    process.stdin.write(request[cut:])
    process.stdin.close()
    process.stdin = None
    stdout, _stderr = process.communicate(timeout=20)
    assert process.returncode == 0
    actual = json.loads(stdout)["results"][0]
    expected = validation.inspect_render({"question": f"[Katex]{latex}[/Katex]"})
    assert actual["rendered_sha256"] == expected["expressions"][0]["rendered_sha256"]
