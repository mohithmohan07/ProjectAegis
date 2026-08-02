"""Regression coverage for the Phase 2.1.1 source-display patch."""
from __future__ import annotations

from pathlib import Path

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase211_contract as phase211
from app.services import katex_rules

DATA = Path(__file__).parents[1] / "data" / "Testing"


def _rne_source() -> str:
    return (DATA / "RNE.mmd").read_text(encoding="utf-8")


def _compile(source: str):
    return phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    )


def _task(canonical: dict, needle: str) -> dict:
    matches = [
        task
        for task in canonical.get("tasks") or []
        if needle.casefold()
        in str(task.get("raw_prompt") or task.get("display_prompt") or "").casefold()
    ]
    assert len(matches) == 1, (needle, len(matches))
    return matches[0]


def test_nested_mathpix_itemize_is_rendered_without_raw_latex():
    compiled = _compile(_rne_source())
    task = _task(compiled.canonical, "Write a note on")
    display = task["display_prompt"]

    assert compiled.canonical["phase21_hardening"]["patch_version"] == "2.1.2"
    assert task["raw_prompt"].startswith("Write a note on:")
    assert "a) Guiseppe Mazzini" in display
    assert "e) The role of women in nationalist struggles" in display
    assert "\\item" not in display
    assert "\\begin{itemize}" not in display
    assert "\\end{itemize}" not in display
    assert katex_rules.rich_text_issues(display) == []


def test_list_normalizer_preserves_labels_and_protected_code():
    source = (
        "\\begin{enumerate}\n"
        "\\item[1.] Explain the pattern.\n"
        "\\begin{itemize}\n"
        "\\item[a)] First case\n"
        "\\item Second case with `\\item[raw]` in code\n"
        "\\end{itemize}\n"
        "\\end{enumerate}"
    )

    rendered = phase211.normalize_task_list_markup(source)

    assert "1. Explain the pattern." in rendered
    assert "a) First case" in rendered
    assert "• Second case" in rendered
    assert "`\\item[raw]`" in rendered
    assert "\\begin{enumerate}" not in rendered
    assert "\\end{itemize}" not in rendered


def test_incomplete_production_rne_has_only_real_source_blocks_not_list_error():
    source = _rne_source()
    section_two = "\\section*{2 The Making of Nationalism in Europe}\n\n"
    club = (
        "\\section*{Discuss}\n\n"
        "What is the caricaturist trying to depict?\n\n"
    )
    assert section_two in source and club in source

    compiled = _compile(source.replace(section_two, "", 1).replace(club, "", 1))
    codes = [issue["code"] for issue in compiled.report["phase2_issues"]]

    assert len(compiled.canonical["tasks"]) == 25
    assert "phase2_task_rich_text_invalid" not in codes
    assert {
        "phase21_missing_numbered_parent_section",
        "phase21_numbered_section_gap",
        "phase21_orphan_task_figure",
    }.issubset(set(codes))


def test_ui_issue_formatter_includes_qid_and_task_snippet():
    issue = {
        "code": "phase2_task_rich_text_invalid",
        "message": "A canonical task display prompt is not valid Aegis rich text.",
        "qid": "QINV-0015",
        "task_snippet": "Write a note on: a) Guiseppe Mazzini",
    }

    rendered = phase211.format_issue_for_ui(issue)

    assert "QINV-0015" in rendered
    assert "Write a note on" in rendered
    assert "phase2_task_rich_text_invalid" in rendered


def test_conversion_log_rewrite_removes_phase1_only_wording():
    message = (
        "Compiling Phase 1 canonical-source shadow; the existing generation "
        "path will continue using the raw MMD unchanged."
    )

    rewritten = phase211.rewrite_conversion_log_message(message)

    assert "Phase 1 canonical-source shadow" not in rewritten
    assert "Phase 2.1 validation" in rewritten
    assert phase211.rewrite_conversion_log_message(
        "Canonical-source shadow passed_with_warnings: 24 task(s)."
    ).startswith("Canonical-source base audit")
