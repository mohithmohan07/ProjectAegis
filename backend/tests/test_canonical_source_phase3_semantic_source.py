"""Regression coverage for graph-derived semantic MMD."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import canonical_source_phase3_contract as graph_contract
from app.services import canonical_source_phase3_semantic_source as semantic_source
from app.services import generation


@pytest.fixture(scope="module")
def rne_graph() -> dict:
    source = Path("data/Testing/RNE.mmd").read_text(encoding="utf-8")
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="RNE.pdf",
        consumer_module="build_concepts",
    ).canonical
    return phase3.build_semantic_graph(
        canonical,
        source,
        metadata={
            "subject": "History",
            "grade": "10",
            "chapter_title": "The Rise of Nationalism in Europe",
        },
    )


def test_semantic_source_contract_is_installed_after_topology_contract():
    assert generation._PHASE3_TOPOLOGY_CONTRACT_VERSION == 1
    assert generation._PHASE3_SEMANTIC_SOURCE_VERSION == 1
    assert "semantic_source" in graph_contract.OPTIONAL_ARTIFACT_SPECS
    assert "semantic_source_report" in graph_contract.OPTIONAL_ARTIFACT_SPECS


def test_rne_semantic_source_preserves_order_images_and_rich_text(rne_graph: dict):
    source = semantic_source.render_semantic_source(rne_graph)
    report = semantic_source.validation_report(rne_graph, source)

    assert report["status"] == "passed"
    assert report["issues"] == []
    assert report["missing_image_urls"] == []
    assert report["topic_order_valid"] is True
    assert report["summary"]["topics"] == 6
    assert report["summary"]["images"] == len({
        url
        for figure in rne_graph["figures"]
        for url in figure.get("image_urls") or []
    })
    assert source.count("[img ") == report["summary"]["images"]
    assert "\\begin{figure}" not in source
    assert "\\captionsetup" not in source
    assert "\\includegraphics" not in source
    assert "\\begin{itemize}" not in source
    assert "\\item" not in source
    assert "\\section" not in source
    assert "\\subsection" not in source
    assert "# 2 The Making of Nationalism in Europe" in source
    assert "## 2.1 The Aristocracy and the New Middle Class" in source
    assert "**Activity**" in source
    assert "# Activity" not in source


def test_chapter_opening_stays_before_first_numbered_topic(rne_graph: dict):
    source = semantic_source.render_semantic_source(rne_graph)

    assert source.index("Frédéric Sorrieu") < source.index(
        "# 1 The French Revolution and the Idea of the Nation"
    )
    assert source.index("# 1 The French Revolution") < source.index(
        "# 2 The Making of Nationalism in Europe"
    ) < source.index("# 3 The Age of Revolutions")


def test_every_graph_figure_url_is_preserved_exactly_once(rne_graph: dict):
    source = semantic_source.render_semantic_source(rne_graph)
    urls = {
        str(url)
        for figure in rne_graph["figures"]
        for url in figure.get("image_urls") or []
        if str(url)
    }

    assert urls
    assert all(source.count(url) == 1 for url in urls)


def test_installed_section_chunker_reads_semantic_source_not_raw_argument(
    rne_graph: dict,
):
    with phase3.activate(rne_graph):
        chunks = generation._section_aware_chunks(
            "THIS RAW ARGUMENT CONTAINS NO TEXTBOOK HEADINGS"
        )
    sections = [section for chunk in chunks for section in chunk["sections"]]
    headings = [
        str(section.get("heading") or "")
        for section in sections
        if section.get("heading")
    ]

    assert "The Making of Nationalism in Europe" in headings
    assert "The Aristocracy and the New Middle Class" in headings
    assert "Nationalism and Imperialism" in headings
    assert all("THIS RAW ARGUMENT" not in section.get("body", "") for section in sections)


def test_semantic_source_artifacts_are_deterministic(tmp_path, rne_graph: dict):
    first = semantic_source.write_artifacts(tmp_path, rne_graph)
    first_source = (
        tmp_path / semantic_source.ARTIFACT_SPECS["semantic_source"]["filename"]
    ).read_text(encoding="utf-8")
    second = semantic_source.write_artifacts(tmp_path, rne_graph)
    report_path = (
        tmp_path
        / semantic_source.ARTIFACT_SPECS["semantic_source_report"]["filename"]
    )
    persisted = json.loads(report_path.read_text(encoding="utf-8"))

    assert first == second == persisted
    assert first["semantic_source_sha256"] == second["semantic_source_sha256"]
    assert first_source == semantic_source.render_semantic_source(rne_graph)
