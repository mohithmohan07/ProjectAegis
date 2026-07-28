"""Phase-1 canonical-source shadow: deterministic, lossless, and non-cutover."""
from __future__ import annotations

import json
from pathlib import Path

from app.services import canonical_source


DATA = Path(__file__).parents[1] / "data" / "Testing"


def _synthetic_source() -> str:
    return r"""# Ordered Chapter

Opening text with inline maths $a^2+b^2=c^2$.

## First Topic

The first topic stays first.

\begin{figure}
\includegraphics[max width=\textwidth]{https://cdn.example.org/figure-10.png}
\captionsetup{labelformat=empty}
\caption{Fig. 10 - A source-owned visual}
\end{figure}

## Last Topic

Look once more at Fig. 10 and explain the relationship.
"""


def test_shadow_compiler_is_byte_deterministic_and_lossless():
    source = _synthetic_source()

    first = canonical_source.compile_source(
        source,
        source_filename="ordered-chapter.mmd",
    )
    second = canonical_source.compile_source(
        source,
        source_filename="ordered-chapter.mmd",
    )

    assert first.canonical == second.canonical
    assert first.aegis_mmd == second.aegis_mmd
    assert first.report == second.report
    assert "".join(
        block["raw_text"] for block in first.canonical["blocks"]
    ) == source

    sections = first.canonical["sections"]
    assert [section["title"] for section in sections] == [
        "Ordered Chapter",
        "First Topic",
        "Last Topic",
    ]
    assert [section["order"] for section in sections] == [1, 2, 3]
    assert sections[1]["parent_section_id"] == sections[0]["section_id"]
    assert sections[2]["parent_section_id"] == sections[0]["section_id"]

    assert [image["url"] for image in first.canonical["images"]] == [
        "https://cdn.example.org/figure-10.png"
    ]
    assert first.canonical["math"][0]["canonical_latex"] == "a^2+b^2=c^2"
    assert "[Katex] a^2+b^2=c^2 [/Katex]" in first.aegis_mmd
    assert first.canonical["shadow_mode"] is True
    assert first.canonical["used_for_generation"] is False
    assert first.report["used_for_generation"] is False


def test_rne_shadow_preserves_review_source_topic_and_task_order():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")

    compiled = canonical_source.compile_source(
        source,
        source_filename="RNE.mmd",
    )

    assert "".join(
        block["raw_text"] for block in compiled.canonical["blocks"]
    ) == source
    titles = [
        section["title"] for section in compiled.canonical["sections"]
    ]
    expected_topics = [
        "The French Revolution and the Idea of the Nation",
        "The Making of Nationalism in Europe",
        "The Age of Revolutions: 1830-1848",
        "The Making of Germany and Italy",
        "Visualising the Nation",
        "Nationalism and Imperialism",
    ]
    positions = [titles.index(title) for title in expected_topics]
    assert positions == sorted(positions)

    tasks = compiled.canonical["tasks"]
    assert len(tasks) == 26
    assert [task["order"] for task in tasks] == list(range(1, 27))
    assert [task["task_id"] for task in tasks] == [
        f"TASK-{index:05d}" for index in range(1, 27)
    ]
    assert [task["source_start"] for task in tasks] == sorted(
        task["source_start"] for task in tasks
    )
    section_by_id = {
        section["section_id"]: section
        for section in compiled.canonical["sections"]
    }
    assert all(
        section_by_id[task["section_id"]]["source_start"]
        <= task["source_start"]
        < section_by_id[task["section_id"]]["source_end"]
        for task in tasks
    )

    assert compiled.report["summary"]["source_chars"] == len(source)
    assert compiled.report["summary"]["errors"] == len([
        issue for issue in compiled.report["issues"]
        if issue["severity"] == "error"
    ])


def test_shadow_artifacts_are_written_together_and_raw_mmd_is_exact(tmp_path):
    source = _synthetic_source()

    manifest = canonical_source.write_shadow_artifacts(
        tmp_path,
        mmd_text=source,
        source_filename="ordered-chapter.mmd",
    )

    assert manifest["available"] is True
    assert manifest["shadow_mode"] is True
    assert manifest["used_for_generation"] is False
    assert {item["kind"] for item in manifest["files"]} == {
        "raw_mmd",
        "canonical_json",
        "aegis_mmd",
        "report",
    }
    assert (tmp_path / "source.raw.mmd").read_text(encoding="utf-8") == source
    canonical = json.loads(
        (tmp_path / "source.aegis-source.json").read_text(encoding="utf-8")
    )
    assert canonical["document"]["source_sha256"] == manifest["source_sha256"]
    assert canonical["ordering_contract"]["source_order_locked"] is True


def test_shadow_compiler_failure_still_writes_audit_bundle(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic compiler failure")

    monkeypatch.setattr(canonical_source, "compile_source", fail)
    manifest = canonical_source.write_shadow_artifacts(
        tmp_path,
        mmd_text="# Raw source\n",
        source_filename="source.mmd",
    )

    assert manifest["available"] is True
    assert manifest["status"] == "failed"
    assert (tmp_path / "source.raw.mmd").read_text(encoding="utf-8") == "# Raw source\n"
    report = json.loads(
        (tmp_path / "source.source-report.json").read_text(encoding="utf-8")
    )
    assert report["issues"][0]["code"] == "shadow_compiler_exception"
