"""Lossless task display recovery does not change verified source evidence."""
from __future__ import annotations

import copy

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase221_fallback as fallback
from app.services import katex_rules as kr


@pytest.mark.parametrize("prompt", [
    "Calculate R = 2 + 3.",
    r"Write the formula C_{2}H_{6}.",
    r"Simplify \frac{2}{3}.",
])
def test_verified_raw_task_restoration_keeps_lossless_math_projection(prompt):
    page_evidence = {"text": prompt, "kind": "task", "reading_order": 1}
    before = copy.deepcopy(page_evidence)
    url = "https://source.example/figure.png"
    display = fallback._compose_task_display_prompt(
        page_evidence["text"], set(), [url], {url: "Printed source diagram"},
    )
    assert kr.rich_text_issues(kr.canonicalize_rich_text(prompt))
    assert kr.rich_text_issues(display) == []
    assert kr.unwrap_katex(display) == prompt + " " + kr.image(url, "Printed source diagram")
    assert page_evidence == before
    canonical = {"tasks": [{
        "qid": "QINV-0001", "identity_key": "original-task", "source_start": 0,
        "raw_prompt": prompt, "display_prompt": display,
    }]}
    assert not any(issue["code"] == "phase2_task_rich_text_invalid"
                   for issue in phase2.phase2_inventory_issues(canonical))
    assert fallback._compose_task_display_prompt(display, set(), [], {}) == display


def test_malformed_source_is_still_refused_with_exact_task_and_defect():
    prompt = r"Simplify \frac{2}{3."
    display = fallback._compose_task_display_prompt(prompt, set(), [], {})
    assert display == prompt
    canonical = {"tasks": [{
        "qid": "QINV-0001", "identity_key": "original-task", "source_start": 0,
        "raw_prompt": prompt, "display_prompt": display,
    }]}
    issues = phase2.phase2_inventory_issues(canonical)
    assert any(issue["code"] == "phase2_task_rich_text_invalid" for issue in issues)
    with pytest.raises(ValueError, match=r"QINV-0001.*raw_latex"):
        fallback._accept_gate_issues_with_flags(canonical, {}, issues)


def test_source_gate_error_carries_all_diagnostics_not_only_the_eight_in_its_message():
    issues = [{
        "severity": "error", "code": "phase2_task_rich_text_invalid",
        "qid": f"QINV-{index:04d}", "rich_text_issues": ["raw_latex"],
    } for index in range(1, 11)]
    with pytest.raises(fallback.CanonicalSourceGateError) as caught:
        fallback._accept_gate_issues_with_flags({"tasks": [{}]}, {}, issues)
    assert isinstance(caught.value, ValueError)
    assert "QINV-0008" in str(caught.value)
    assert "QINV-0009" not in str(caught.value)
    assert caught.value.validation_diagnostics == {
        "code": "source_canonical_gate", "issues": issues,
    }
    issues[0]["qid"] = "mutated"
    assert caught.value.validation_diagnostics["issues"][0]["qid"] == "QINV-0001"
