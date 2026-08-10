"""Unmatchable explicit Figure citations ship for review under the rewrite."""
from __future__ import annotations

from app.services import canonical_source as cs

_MMD = (
    "\\section*{CHAPTER 9 Waves}\n\n"
    "Sound travels as a wave through a medium.\n\n"
    "\\section*{Questions}\n\n"
    "1. In Fig. 9.9, name the labelled parts of the wave.\n"
)


def _citation_issues(mmd: str) -> dict[str, str]:
    compiled = cs.compile_source(mmd, source_filename="test.pdf")
    report = getattr(compiled, "report", None) or {}
    issues = (
        getattr(compiled, "issues", None)
        or report.get("issues")
        or []
    )
    return {
        str(row.get("code")): str(row.get("severity"))
        for row in issues
        if "figure_reference" in str(row.get("code", ""))
    }


def test_citation_blocks_on_the_legacy_path(monkeypatch):
    monkeypatch.delenv("AEGIS_PHASE3_REWRITE", raising=False)
    codes = _citation_issues(_MMD)
    assert codes.get("unresolved_explicit_figure_reference") == "error"


def test_citation_ships_for_review_under_the_rewrite(monkeypatch):
    monkeypatch.setenv("AEGIS_PHASE3_REWRITE", "1")
    codes = _citation_issues(_MMD)
    assert codes.get("unresolved_explicit_figure_reference") == "warning"
