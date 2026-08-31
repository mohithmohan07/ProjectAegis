"""Phase 3 universal semantic-graph regressions.

These tests deliberately span publisher layouts and subjects.  Phase 3 may use
models to interpret semantics, but every tested decision remains bounded by
stable ACSD/PDF identities and deterministic validation.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase3 as phase3
from app.services import generation
from app.services import katex_rules as kr


DATA = Path(__file__).parents[1] / "data" / "Testing"


def _compile_fixture(filename: str, *, subject: str, chapter_title: str = ""):
    source = (DATA / filename).read_text(encoding="utf-8")
    canonical = phase2.compile_phase2_source(
        source,
        source_filename=filename,
        consumer_module="build_concepts",
    ).canonical
    graph, report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={
            "subject": subject,
            "chapter_title": chapter_title,
            "board": "CBSE",
        },
    )
    semantic_source = phase3.render_semantic_source(graph, canonical)
    return source, canonical, graph, report, semantic_source


def _compile_inline_source(source: str):
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="inline-source.mmd",
        consumer_module="build_concepts",
    ).canonical
    graph, report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={
            "subject": "History",
            "chapter_title": "Source Metadata Regression",
            "board": "CBSE",
        },
    )
    semantic = phase3.render_semantic_source(graph, canonical)
    return canonical, graph, report, semantic


def _phase22_adjudicated_parent_fixture():
    source = """# Adjudicated Heading Regression

## 1 First Topic

First topic body.

Introductory text under the recovered second topic.

### 2.1 Child of the Recovered Topic

Second topic body.

## 3 Third Topic

Third topic body.
"""
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="adjudicated-heading.mmd",
        consumer_module="build_concepts",
    ).canonical
    anchor = next(
        row
        for row in canonical["blocks"]
        if "Introductory text under" in str(row.get("raw_text") or "")
    )
    recovered = "2 Second Topic"
    provenance = {
        "repair_id": "REPAIR-ADJUDICATED-HEADING",
        "page_number": 6,
        "raw_mmd_changed": False,
        "recovered_text": recovered,
    }
    phase3.phase22.apply_missing_heading(
        canonical,
        {"section_number": 2},
        {
            "recovered_text": recovered,
            "insert_before_block_id": anchor["block_id"],
        },
        provenance,
    )
    canonical["source_adjudication"] = {
        "version": phase3.phase22.ADJUDICATION_VERSION,
        "status": "verified",
        "raw_mmd_changed": False,
        "decisions": [{
            "issue_type": "missing_parent_section",
            "status": "verified",
            "provenance": copy.deepcopy(provenance),
        }],
        "verified_repairs": 1,
        "remaining_issues": 0,
    }
    semantic_source = phase3.phase22.semantic_source(canonical, source)
    canonical["source_adjudication"]["semantic_source_sha256"] = (
        phase3._sha256_text(semantic_source)
    )
    metadata = {
        "subject": "History",
        "chapter_title": "Adjudicated Heading Regression",
        "board": "CBSE",
    }
    graph, report = phase3.compile_semantic_graph(
        canonical,
        source_text=semantic_source,
        metadata=metadata,
    )
    rendered_source = phase3.render_semantic_source(graph, canonical)
    return (
        source,
        semantic_source,
        rendered_source,
        canonical,
        metadata,
        graph,
        report,
    )


def test_nonvisible_machine_metadata_is_removed_from_every_block():
    source = """<!-- source_origin: gpt-pdf-to-acsd -->
<!-- compiler_version: gpt-pdf-to-acsd-2 -->
<!-- schema_version: 1_1_0 -->

# Source Metadata Regression

<!-- used_for_generation: source-critical -->

## First Topic

Visible first paragraph.

<!-- compilation_status: complete -->

Visible second paragraph.

<!-- source_sha256: abc_def -->
"""
    canonical, graph, _report, semantic = _compile_inline_source(source)
    comment_blocks = [
        block
        for block in canonical["blocks"]
        if "<!--" in str(block.get("raw_text") or "")
    ]

    assert len(comment_blocks) >= 3
    assert any(block["block_id"] != "BLK-00001" for block in comment_blocks)
    assert "Visible first paragraph." in semantic
    assert "Visible second paragraph." in semantic
    assert "<!--" not in semantic
    assert "source_origin" not in semantic
    assert "compiler_version" not in semantic
    assert graph["status"] == "ready"
    assert phase3._rich_text_issue_block_ids(
        graph, canonical=canonical) == []
    assert not kr.rich_text_issues(semantic)
    assert not phase3.validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic,
    )


def test_mixed_machine_metadata_preserves_visible_source_text():
    source = """# Source Metadata Regression

## First Topic

Visible before <!-- compiler_version: build_id --> visible after.
"""
    canonical, graph, _report, semantic = _compile_inline_source(source)
    mixed = next(
        block
        for block in canonical["blocks"]
        if "Visible before" in str(block.get("raw_text") or "")
    )

    assert "<!-- compiler_version" in mixed["raw_text"]
    assert semantic.count("Visible before") == 1
    assert semantic.count("visible after.") == 1
    assert "compiler_version" not in semantic
    assert graph["status"] == "ready"


def test_machine_metadata_does_not_hide_genuine_raw_latex():
    source = r"""<!-- source_origin: gpt-pdf-to-acsd -->
<!-- compiler_version: gpt-pdf-to-acsd-2 -->

# Source Metadata Regression

## First Topic

Visible source with \\notarealcommand still malformed.
"""
    canonical, graph, _report, semantic = _compile_inline_source(source)
    malformed = next(
        block
        for block in canonical["blocks"]
        if "notarealcommand" in str(block.get("raw_text") or "")
    )
    issue = next(
        row
        for row in graph["issues"]
        if row.get("code") == "semantic_source_rich_text"
    )

    assert "source_origin" not in semantic
    assert "notarealcommand" in semantic
    assert issue["block_ids"] == [malformed["block_id"]]
    assert phase3._rich_text_issue_block_ids(
        graph, canonical=canonical) == [malformed["block_id"]]
    assert "raw_latex" in issue["message"]


def test_literal_and_unknown_html_comments_remain_strictly_preserved():
    literals = [
        "`<!-- compiler_version: literal_code -->`",
        "``code ` <!-- compiler_version: literal_code -->``",
        "```html\n<!-- compiler_version: literal_code -->\n```",
        "~~~html\n<!-- compiler_version: literal_code -->\n~~~",
        "    <!-- compiler_version: literal_code -->\n",
    ]
    unknown = "<!-- user_note: learner-visible source -->"

    for literal in literals:
        assert phase3._strip_machine_metadata_comments(literal) == literal
        assert not kr.rich_text_issues(literal)
    literal_source = (
        "# Source Metadata Regression\n\n## Code Examples\n\n"
        + "\n\n".join(literals)
    )
    _canonical, graph, _report, semantic = _compile_inline_source(
        literal_source)
    for literal in literals:
        assert literal.rstrip("\n") in semantic
    assert graph["status"] == "ready"
    assert not kr.rich_text_issues(semantic)
    assert phase3._strip_machine_metadata_comments(
        "word<!-- compiler_version: build_id -->word"
    ) == "word word"
    assert "raw_latex" in kr.rich_text_issues(
        "Visible paragraph\n    x_1 remains malformed prose."
    )
    assert phase3._strip_machine_metadata_comments(unknown) == unknown
    assert "raw_latex" in kr.rich_text_issues(
        phase3._clean_public_text(unknown))


def test_rne_graph_restores_six_topics_and_qid_ancestry():
    _source, canonical, graph, report, semantic = _compile_fixture(
        "RNE.mmd", subject="History",
        chapter_title="The Rise of Nationalism in Europe",
    )

    assert graph["status"] == "ready"
    assert report["status"] == "ready"
    assert [row["title"] for row in graph["topics"]] == [
        "The French Revolution and the Idea of the Nation",
        "The Making of Nationalism in Europe",
        "The Age of Revolutions: 1830-1848",
        "The Making of Germany and Italy",
        "Visualising the Nation",
        "Nationalism and Imperialism",
    ]
    assert [row["qid"] for row in graph["tasks"]] == [
        f"QINV-{index:04d}" for index in range(1, 27)
    ]
    section_two = next(
        row for row in graph["topics"]
        if row["title"] == "The Making of Nationalism in Europe"
    )
    aristocracy = next(
        row for row in graph["subtopics"]
        if row["title"] == "The Aristocracy and the New Middle Class"
    )
    assert section_two["topic_id"] == "TOPIC-0002"
    assert aristocracy["topic_id"] == section_two["topic_id"]
    assert not kr.rich_text_issues(semantic)
    assert not phase3.validate_graph(
        graph, canonical=canonical, semantic_source=semantic
    )


def test_rne_cached_graph_cannot_absorb_numbered_section_two_into_section_one():
    _source, canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd",
        subject="History",
        chapter_title="The Rise of Nationalism in Europe",
    )
    collapsed = copy.deepcopy(graph)
    removed_topic = next(
        row for row in collapsed["topics"]
        if row["title"] == "The Making of Nationalism in Europe"
    )
    removed_id = removed_topic["topic_id"]
    replacement_id = collapsed["topics"][0]["topic_id"]
    collapsed["topics"] = [
        row for row in collapsed["topics"]
        if row["topic_id"] != removed_id
    ]
    for collection in ("blocks", "tasks", "subtopics", "sections"):
        for row in collapsed.get(collection) or []:
            if row.get("topic_id") == removed_id:
                row["topic_id"] = replacement_id
    semantic = phase3.render_semantic_source(collapsed, canonical)
    collapsed["semantic_source_sha256"] = phase3._sha256_text(semantic)

    codes = {
        row["code"]
        for row in phase3.validate_graph(
            collapsed,
            canonical=canonical,
            semantic_source=semantic,
        )
    }

    assert "numbered_main_topic_coverage" in codes


def test_numbered_topic_validation_rejects_number_and_section_aliasing():
    _source, canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd",
        subject="History",
        chapter_title="The Rise of Nationalism in Europe",
    )
    wrong_number = copy.deepcopy(graph)
    wrong_number["topics"][1]["structural_number"] = "999"
    wrong_number_source = phase3.render_semantic_source(
        wrong_number, canonical)
    wrong_number["semantic_source_sha256"] = phase3._sha256_text(
        wrong_number_source)
    number_errors = phase3.validate_graph(
        wrong_number,
        canonical=canonical,
        semantic_source=wrong_number_source,
    )

    duplicate_section = copy.deepcopy(graph)
    duplicate_section["topics"][1]["section_id"] = (
        duplicate_section["topics"][0]["section_id"]
    )
    duplicate_source = phase3.render_semantic_source(
        duplicate_section, canonical)
    duplicate_section["semantic_source_sha256"] = phase3._sha256_text(
        duplicate_source)
    duplicate_errors = phase3.validate_graph(
        duplicate_section,
        canonical=canonical,
        semantic_source=duplicate_source,
    )

    stale_heading = copy.deepcopy(graph)
    second_binding = stale_heading["numbered_main_bindings"][1]
    first_topic = stale_heading["topics"][0]
    heading_block = next(
        row for row in stale_heading["blocks"]
        if row["block_id"] == second_binding["block_id"]
    )
    heading_block["section_id"] = first_topic["section_id"]
    heading_block["topic_id"] = first_topic["topic_id"]
    stale_source = phase3.render_semantic_source(stale_heading, canonical)
    stale_heading["semantic_source_sha256"] = phase3._sha256_text(
        stale_source)
    stale_errors = phase3.validate_graph(
        stale_heading,
        canonical=canonical,
        semantic_source=stale_source,
    )

    assert any(
        row["code"] == "numbered_main_topic_coverage"
        and "changed_structural_number" in str(row.get("mismatches"))
        for row in number_errors
    )
    assert any(
        row["code"] == "numbered_main_topic_coverage"
        and "missing_or_duplicate_topic_for_section"
        in str(row.get("mismatches"))
        for row in duplicate_errors
    )
    assert "## The Making of Nationalism in Europe" not in stale_source
    assert any(
        row["code"] == "numbered_main_topic_coverage"
        and "heading_block_section_mismatch" in str(row.get("mismatches"))
        for row in stale_errors
    )


def test_rne_stale_numbered_heading_section_binding_is_repaired_api_free():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    ).canonical
    mains, _subsections = phase3.structure.numbered_heading_inventory(
        canonical
    )
    section_one_id = str(mains[1]["section_id"])
    section_two = mains[2]
    section_two_id = str(section_two["section_id"])
    section_two["section_id"] = section_one_id

    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "board": "CBSE",
        },
        hierarchy_provider=lambda payload: {
            "sections": [
                {
                    "section_id": row["section_id"],
                    "role": "other",
                    "parent_section_id": "",
                    "confidence": 0.999,
                    "evidence": ["adversarial hierarchy fixture"],
                }
                for row in payload["sections"]
            ],
        },
        critic_provider=lambda _payload: {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )
    semantic = phase3.render_semantic_source(graph, canonical)
    restored = next(
        row for row in graph["topics"]
        if row["title"] == "The Making of Nationalism in Europe"
    )
    restored_heading = next(
        row for row in graph["blocks"]
        if row["block_id"] == section_two["block_id"]
    )
    binding = next(
        row for row in graph["numbered_main_bindings"]
        if row["number"] == "2"
    )

    assert source == (DATA / "RNE.mmd").read_text(encoding="utf-8")
    assert section_two["section_id"] == section_one_id
    assert restored["section_id"] == section_two_id
    assert restored["topic_id"] == "TOPIC-0002"
    assert restored_heading["section_id"] == section_two_id
    assert restored_heading["topic_id"] == restored["topic_id"]
    assert binding["claimed_section_id"] == section_one_id
    assert binding["resolved_section_id"] == section_two_id
    assert binding["resolution_mode"] == "exact_title_position_rebind"
    assert semantic.count("## The Making of Nationalism in Europe") == 1
    assert semantic.index("## The Making of Nationalism in Europe") < (
        semantic.index("### The Aristocracy and the New Middle Class")
    )
    assert not phase3.validate_graph(
        graph,
        canonical=canonical,
        semantic_source=semantic,
    )


def test_phase22_adjudicated_parent_is_valid_without_a_physical_heading_block():
    (
        _source,
        _working_source,
        rendered,
        canonical,
        _metadata,
        graph,
        report,
    ) = _phase22_adjudicated_parent_fixture()
    restored = next(
        row for row in graph["topics"]
        if row["title"] == "Second Topic"
    )
    binding = next(
        row for row in graph["numbered_main_bindings"]
        if row["number"] == "2"
    )

    assert restored["section_id"] == "SEC-ADJ-0002"
    assert restored["structural_number"] == "2"
    assert binding["block_id"] == "ADJUDICATED-SEC-ADJ-0002"
    assert binding["heading_origin"] == "adjudicated_pdf"
    assert binding["heading_materialized_in_blocks"] is False
    assert not any(
        row.get("block_id") == binding["block_id"]
        for row in canonical["blocks"]
    )
    assert not any(
        row.get("block_id") == binding["block_id"]
        for row in graph["blocks"]
    )
    assert phase3._verified_adjudicated_heading_binding(
        binding,
        canonical=canonical,
    )
    at_source_start = copy.deepcopy(canonical)
    start_section = next(
        row for row in at_source_start["sections"]
        if row["section_id"] == binding["resolved_section_id"]
    )
    start_overlay = next(
        row for row in at_source_start["source_overlays"]
        if row["repair_id"]
        == start_section["adjudicated_heading"]["repair_id"]
    )
    start_section["source_start"] = 0
    start_overlay["offset"] = 0
    assert phase3._verified_adjudicated_heading_binding(
        binding,
        canonical=at_source_start,
    )
    assert graph["status"] == "ready"
    assert report["status"] == "ready"
    assert not phase3.validate_graph(
        graph,
        canonical=canonical,
        semantic_source=rendered,
    )


def test_phase22_adjudicated_parent_requires_its_sealed_repair_provenance():
    (
        _source,
        _working_source,
        rendered,
        canonical,
        _metadata,
        graph,
        _report,
    ) = _phase22_adjudicated_parent_fixture()
    tampered = copy.deepcopy(canonical)
    tampered["source_adjudication"]["decisions"][0]["provenance"][
        "page_number"
    ] = 99

    errors = phase3.validate_graph(
        graph,
        canonical=tampered,
        semantic_source=rendered,
    )
    mismatch = next(
        row for row in errors
        if row.get("code") == "numbered_main_topic_coverage"
    )["mismatches"][0]

    assert "invalid_adjudicated_heading_provenance" in mismatch["reasons"]
    assert "heading_block_section_mismatch" not in mismatch["reasons"]


def test_failed_pseudo_block_validation_migrates_without_hierarchy_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    (
        _source,
        working_source,
        rendered,
        canonical,
        metadata,
        graph,
        report,
    ) = _phase22_adjudicated_parent_fixture()
    stale = copy.deepcopy(graph)
    stale["classification_mode"] = "api_classified_and_verified"
    for binding in stale["numbered_main_bindings"]:
        binding.pop("heading_origin", None)
        binding.pop("heading_materialized_in_blocks", None)
    second_topic = next(
        row for row in stale["topics"] if row["title"] == "Second Topic"
    )
    second_binding = next(
        row for row in graph["numbered_main_bindings"]
        if row["number"] == "2"
    )
    stale_mismatch = {
        **{
            key: copy.deepcopy(second_binding.get(key))
            for key in (
                "number", "block_id", "claimed_section_id",
                "resolved_section_id", "expected_title", "source_start",
                "resolution_mode",
            )
        },
        "actual_topic_id": second_topic["topic_id"],
        "actual_section_id": second_topic["section_id"],
        "actual_title": second_topic["title"],
        "actual_structural_number": second_topic["structural_number"],
        "reasons": [
            "heading_block_section_mismatch",
            "heading_block_topic_mismatch",
        ],
    }
    stale["status"] = "failed"
    stale["issues"] = [{
        "severity": "error",
        "code": "numbered_main_topic_coverage",
        "mismatches": [stale_mismatch],
        "message": "legacy pseudo-block false positive",
    }]
    stale["semantic_source_sha256"] = phase3._sha256_text(rendered)
    malformed = copy.deepcopy(stale)
    malformed["issues"][0]["mismatches"].append("not-a-mismatch-object")
    assert phase3._migrate_adjudicated_heading_validation_false_positive(
        malformed,
        canonical=canonical,
        semantic_source=rendered,
    ) is None
    phase3.write_artifacts(
        tmp_path,
        graph=stale,
        report=phase3._updated_graph_report(stale, report),
        semantic_source=rendered,
    )

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)

    def unexpected_api(_payload: dict) -> dict:
        raise AssertionError("saved hierarchy batches must not be called again")

    monkeypatch.setattr(
        phase3, "_classify_hierarchy_via_openai", unexpected_api)
    monkeypatch.setattr(
        phase3, "_critic_hierarchy_via_openai", unexpected_api)

    migrated = phase3.prepare_generation_graph(
        canonical=canonical,
        source_text=working_source,
        metadata=metadata,
        source_path=None,
        artifact_dir=tmp_path,
        verify_semantics=True,
    )

    assert migrated["status"] == "ready"
    assert migrated["classification_mode"] == "api_classified_and_verified"
    assert migrated[phase3._ADJUDICATED_HEADING_MIGRATION_KEY]["version"] == 1
    assert any(
        row.get("code") == "adjudicated_heading_validation_migrated"
        for row in migrated["issues"]
    )
    assert not phase3.validate_graph(
        migrated,
        canonical=canonical,
        semantic_source=rendered,
    )


def test_collapsed_numbered_topic_builds_and_applies_sealed_working_mmd_patch():
    source, canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd",
        subject="History",
        chapter_title="The Rise of Nationalism in Europe",
    )
    collapsed = copy.deepcopy(graph)
    removed_topic = next(
        row for row in collapsed["topics"]
        if row["title"] == "The Making of Nationalism in Europe"
    )
    removed_id = removed_topic["topic_id"]
    replacement_id = collapsed["topics"][0]["topic_id"]
    collapsed["topics"] = [
        row for row in collapsed["topics"]
        if row["topic_id"] != removed_id
    ]
    for collection in ("blocks", "tasks", "subtopics", "sections"):
        for row in collapsed.get(collection) or []:
            if row.get("topic_id") == removed_id:
                row["topic_id"] = replacement_id
    collapsed_semantic = phase3.render_semantic_source(collapsed, canonical)
    collapsed["semantic_source_sha256"] = phase3._sha256_text(
        collapsed_semantic
    )
    collapsed["issues"] = phase3.validate_graph(
        collapsed,
        canonical=canonical,
        semantic_source=collapsed_semantic,
    )
    collapsed["status"] = "failed"

    with pytest.raises(phase3.semantic_recovery.HumanDecisionRequired) as caught:
        phase3._source_review_graph_or_raise(
            collapsed,
            canonical=canonical,
            page_bundle=None,
            source_path=None,
        )
    pending = caught.value.pending_decision
    patch = pending["source_patch"]

    assert pending["item"]["type_id"] == "numbered_main_topic_coverage"
    assert patch["verified"] is True
    assert patch["target"] == "working_derived_source"
    assert patch["raw_source_mutated"] is False
    assert "The Making of Nationalism in Europe" not in patch["before"]
    assert "The Making of Nationalism in Europe" in patch["after"]
    assert pending["options"][0]["target_id"] == patch["target_id"]

    resolution = {
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
        "choice": "accept_recommended",
        "target_id": patch["target_id"],
        "status": "ready",
    }
    tampered = copy.deepcopy(collapsed)
    tampered[phase3._SOURCE_REVIEW_KEY]["source_patch"]["after"] += (
        "\n## Invented topic"
    )
    with phase3.human_source_resolution_context([resolution]):
        with pytest.raises(
            ValueError,
            match="source patch changed after the decision was shown",
        ):
            phase3._source_review_graph_or_raise(
                tampered,
                canonical=canonical,
                page_bundle=None,
                source_path=None,
            )
    with phase3.human_source_resolution_context([resolution]):
        repaired = phase3._source_review_graph_or_raise(
            collapsed,
            canonical=canonical,
            page_bundle=None,
            source_path=None,
        )
    repaired_semantic = phase3.render_semantic_source(repaired, canonical)

    assert source == (DATA / "RNE.mmd").read_text(encoding="utf-8")
    assert repaired["status"] == "ready"
    assert repaired["numbered_topic_patch_resolution"][
        "human_decision_id"
    ] == pending["decision_id"]
    assert not phase3.validate_graph(
        repaired,
        canonical=canonical,
        semantic_source=repaired_semantic,
    )


def test_numbered_topic_patch_resume_reuses_verified_hierarchy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source, canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd",
        subject="History",
        chapter_title="The Rise of Nationalism in Europe",
    )
    metadata = {
        "subject": "History",
        "chapter_title": "The Rise of Nationalism in Europe",
        "board": "CBSE",
    }
    collapsed = copy.deepcopy(graph)
    removed_topic = next(
        row for row in collapsed["topics"]
        if row["title"] == "The Making of Nationalism in Europe"
    )
    removed_id = removed_topic["topic_id"]
    replacement_id = collapsed["topics"][0]["topic_id"]
    collapsed["topics"] = [
        row for row in collapsed["topics"]
        if row["topic_id"] != removed_id
    ]
    for collection in ("blocks", "tasks", "subtopics", "sections"):
        for row in collapsed.get(collection) or []:
            if row.get("topic_id") == removed_id:
                row["topic_id"] = replacement_id
    collapsed["classification_mode"] = "api_classified_and_verified"
    collapsed_semantic = phase3.render_semantic_source(
        collapsed, canonical)
    collapsed["semantic_source_sha256"] = phase3._sha256_text(
        collapsed_semantic)
    collapsed["issues"] = phase3.validate_graph(
        collapsed,
        canonical=canonical,
        semantic_source=collapsed_semantic,
    )
    collapsed["status"] = "failed"

    def unexpected_provider(*_args, **_kwargs):
        raise AssertionError(
            "a saved verified hierarchy must not be classified or criticized again"
        )

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        phase3, "_classify_hierarchy_via_openai", unexpected_provider)
    monkeypatch.setattr(
        phase3, "_critic_hierarchy_via_openai", unexpected_provider)

    with pytest.raises(phase3.semantic_recovery.HumanDecisionRequired) as caught:
        phase3.prepare_generation_graph(
            canonical=canonical,
            source_text=source,
            metadata=metadata,
            source_path=None,
            artifact_dir=tmp_path,
            verify_semantics=True,
            resume_review_graph=collapsed,
        )
    pending = caught.value.pending_decision
    persisted = phase3.load_graph(tmp_path, canonical)

    assert pending["source_patch"]["raw_source_mutated"] is False
    assert persisted is not None
    tampered = copy.deepcopy(persisted)
    tampered[phase3._SOURCE_REVIEW_KEY]["source_patch"]["after"] += (
        "\n## Invented topic"
    )
    assert phase3._compatible_source_review_graph(
        tampered,
        canonical=canonical,
        metadata=metadata,
        page_bundle=None,
    ) is None
    with phase3.human_source_resolution_context([{
        "decision_id": pending["decision_id"],
        "context_hash": pending["context_hash"],
        "choice": "accept_recommended",
        "target_id": pending["source_patch"]["target_id"],
    }]):
        repaired = phase3.prepare_generation_graph(
            canonical=canonical,
            source_text=source,
            metadata=metadata,
            source_path=None,
            artifact_dir=tmp_path,
            verify_semantics=True,
            resume_review_graph=persisted,
        )

    assert source == (DATA / "RNE.mmd").read_text(encoding="utf-8")
    assert repaired["status"] == "ready"
    assert not phase3.validate_graph(
        repaired,
        canonical=canonical,
        semantic_source=phase3.render_semantic_source(repaired, canonical),
    )


def test_verified_pdf_missing_parent_heading_is_rendered_in_source_order():
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    section_two = "\\section*{2 The Making of Nationalism in Europe}\n\n"
    assert section_two in source
    corrupted = source.replace(section_two, "", 1)
    canonical = phase2.compile_phase2_source(
        corrupted,
        source_filename="RNE.pdf",
        consumer_module="build_concepts",
    ).canonical
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=corrupted,
        metadata={
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "board": "CBSE",
        },
        page_bundle={
            "schema_version": "1.1.0",
            "pages": [{
                "page_id": "PAGE-0006",
                "page_number": 6,
                "blocks": [{
                    "reading_order": 1,
                    "kind": "heading",
                    "text": "2 The Making of Nationalism in Europe",
                    "heading_level": 1,
                    "confidence": 0.999,
                }],
            }],
        },
    )
    semantic = phase3.render_semantic_source(graph, canonical)

    parent = "## The Making of Nationalism in Europe"
    child = "### The Aristocracy and the New Middle Class"
    assert [row["title"] for row in graph["topics"]][1] == (
        "The Making of Nationalism in Europe"
    )
    assert parent in semantic and child in semantic
    assert semantic.index(parent) < semantic.index(child)


def test_dropped_vision_heading_evidence_is_recorded_never_silent():
    """Sub-floor or ambiguous page evidence for a missing numbered parent is
    still not emitted as a virtual heading — but the drop is recorded as an
    advisory graph issue instead of vanishing silently."""
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    section_two = "\\section*{2 The Making of Nationalism in Europe}\n\n"
    corrupted = source.replace(section_two, "", 1)
    canonical = phase2.compile_phase2_source(
        corrupted,
        source_filename="RNE.pdf",
        consumer_module="build_concepts",
    ).canonical
    graph, report = phase3.compile_semantic_graph(
        canonical,
        source_text=corrupted,
        metadata={
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "board": "CBSE",
        },
        page_bundle={
            "schema_version": "1.1.0",
            "pages": [{
                "page_id": "PAGE-0006",
                "page_number": 6,
                "blocks": [{
                    "reading_order": 1,
                    "kind": "heading",
                    "text": "2 The Making of Nationalism in Europe",
                    "heading_level": 1,
                    # Below the source-critical extraction floor.
                    "confidence": 0.5,
                }],
            }],
        },
    )

    assert "The Making of Nationalism in Europe" not in {
        row["title"] for row in graph["topics"]
    }
    dropped = [
        row for row in graph["issues"]
        if row.get("code") == "vision_heading_evidence_unresolved"
    ]
    assert len(dropped) == 1
    assert dropped[0]["severity"] == "warning"
    assert "2" in dropped[0]["message"]
    assert "confidence floor" in dropped[0]["message"]
    assert dropped[0] in report["issues"]


def test_arithmetic_progressions_uses_pedagogical_topics_not_tex_layout():
    _source, canonical, graph, _report, semantic = _compile_fixture(
        "jemh105 (1).mmd", subject="Mathematics",
        chapter_title="Arithmetic Progressions",
    )

    assert graph["status"] == "ready"
    # "Summary" stays: the offline structural fallback no longer carries a
    # deterministic non-topic-heading vocabulary — ruling a section banner
    # out of the topic list is the chapter-outline judge's call, and without
    # that verdict the section ships as a topic rather than being guessed
    # away (§3 purge, item 3).
    assert [row["title"] for row in graph["topics"]] == [
        "Introduction",
        "Arithmetic Progressions",
        "nth Term of an AP",
        "Sum of First n Terms of an AP",
        "Summary",
    ]
    assert len(graph["tasks"]) == 65
    assert all("\\boldsymbol" not in row["title"] for row in graph["topics"])
    assert not kr.rich_text_issues(semantic)
    assert not phase3.validate_graph(
        graph, canonical=canonical, semantic_source=semantic
    )


def _electricity_page_bundle() -> dict:
    return {
        "source_origin": "gpt-pdf-to-acsd",
        "pages": [{
            "page_id": "PAGE-0004",
            "page_number": 4,
            "confidence": 0.999,
            "blocks": [
                {
                    "reading_order": 1,
                    "kind": "table",
                    "bbox": [50, 80, 950, 850],
                    "text": "Table 11.1 Symbols of some commonly used components in circuit diagrams",
                    "heading_level": 0,
                    "source_label": "",
                    "latex": "",
                    "table_rows": [
                        ["S1. No.", "Components", "Symbols"],
                        ["6", "Wires crossing without joining", "[[VISUAL:2]]"],
                    ],
                    "linked_visual_orders": [2],
                    "linked_context_orders": [],
                    "caption": "Table 11.1 Symbols of some commonly used components in circuit diagrams",
                    "confidence": 0.999,
                },
                {
                    "reading_order": 2,
                    "kind": "figure",
                    "bbox": [720, 560, 900, 700],
                    "text": "",
                    "heading_level": 0,
                    "source_label": "",
                    "latex": "",
                    "table_rows": [],
                    "linked_visual_orders": [],
                    "linked_context_orders": [],
                    "caption": "Wires crossing without joining",
                    "confidence": 0.999,
                    "asset_url": "https://aegis.example/source-assets/7/crossing-wires.jpg?sig=verified",
                },
            ],
        }],
    }


def test_electricity_blocks_converter_markup_until_verified_pdf_repair():
    _source, canonical, graph, _report, semantic = _compile_fixture(
        "Class 10 Chapter 5 Electricity.mmd",
        subject="Physics",
        chapter_title="Electricity",
    )

    assert graph["status"] == "review_required"
    assert len(graph["topics"]) == 8
    assert len(graph["subtopics"]) == 3
    assert len(graph["tasks"]) == 60
    assert any(
        issue["code"] == "converter_semantic_markup_requires_pdf_reconciliation"
        for issue in graph["issues"]
    )
    assert "<smiles>" in semantic

    selected: dict[str, str] = {}

    def provider(packet: dict) -> dict:
        table = next(
            row for row in packet["candidate_blocks"]
            if row["kind"] == "table"
        )
        selected["key"] = table["block_key"]
        return {
            "decision": "use_verified_page_block",
            "selected_block_key": table["block_key"],
            "confidence": 0.999,
            "reason": "The verified PDF table contains the same row and a linked circuit-symbol crop.",
        }

    def critic(payload: dict) -> dict:
        return {
            "verdict": "verified",
            "selected_block_key": payload["selection"]["selected_block_key"],
            "confidence": 0.999,
            "issues": [],
        }

    repaired = phase3.reconcile_source_anomalies(
        graph,
        canonical=canonical,
        page_bundle=_electricity_page_bundle(),
        provider=provider,
        critic=critic,
    )
    repaired_source = phase3.render_semantic_source(repaired, canonical)

    assert selected["key"] == "PAGE-0004:0001"
    assert repaired["status"] == "ready"
    assert "<smiles>" not in repaired_source
    assert "[[VISUAL:" not in repaired_source
    assert "Wires crossing without joining" in repaired_source
    assert "[img src=\"https://aegis.example/source-assets/7/crossing-wires.jpg?sig=verified\"" in repaired_source
    assert not kr.rich_text_issues(repaired_source)
    assert not phase3.validate_graph(
        repaired, canonical=canonical, semantic_source=repaired_source
    )

    rejected = phase3.reconcile_source_anomalies(
        graph,
        canonical=canonical,
        page_bundle=_electricity_page_bundle(),
        provider=provider,
        critic=lambda payload: {
            "verdict": "verified",
            "selected_block_key": payload["selection"]["selected_block_key"],
            "confidence": 0.999,
            "issues": ["the visual ownership is still uncertain"],
        },
    )
    # Under the rewrite a rejected adjudication never blocks the chapter:
    # the block keeps its verbatim extracted text and ships flagged, and
    # the critic's textual dissent joins the recorded issue message.
    assert rejected["status"] == "ready"
    assert rejected.get("source_fusion_repairs") == []
    unresolved_issue = next(
        issue for issue in rejected["issues"]
        if issue["code"]
        == "converter_semantic_markup_requires_pdf_reconciliation"
    )
    assert unresolved_issue["severity"] == "warning"
    assert "Recorded dissent" in unresolved_issue["message"]
    assert (
        "the visual ownership is still uncertain"
        in unresolved_issue["message"]
    )


def test_stale_markup_review_checkpoint_re_reconciles_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A pre-reconciliation pause checkpoint must not replay its frozen pause.

    A resumed graph saved with the converter-markup error still pending is
    adjudicated against the verified page bundle exactly as a fresh compile
    would be, instead of re-raising the stale human-decision pause.
    """
    source, canonical, graph, _report, _semantic = _compile_fixture(
        "Class 10 Chapter 5 Electricity.mmd",
        subject="Physics",
        chapter_title="Electricity",
    )
    assert graph["status"] == "review_required"
    graph["classification_mode"] = "api_classified_and_verified"

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)
    pages = _electricity_page_bundle()
    monkeypatch.setattr(
        phase3, "load_page_evidence", lambda *_args, **_kwargs: pages)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("resume must not rebuild the verified hierarchy")

    monkeypatch.setattr(phase3, "_classify_hierarchy_via_openai", unexpected)
    monkeypatch.setattr(phase3, "_critic_hierarchy_via_openai", unexpected)

    def select(packet: dict, **_kwargs) -> dict:
        table = next(
            row for row in packet["candidate_blocks"]
            if row["kind"] == "table"
        )
        return {
            "decision": "use_verified_page_block",
            "selected_block_key": table["block_key"],
            "confidence": 0.999,
            "reason": "The verified PDF table matches the converter block.",
        }

    def critic(_packet: dict, selection: dict, **_kwargs) -> dict:
        return {
            "verdict": "verified",
            "selected_block_key": selection["selected_block_key"],
            "confidence": 0.999,
            "issues": [],
        }

    monkeypatch.setattr(phase3, "_select_source_anomaly_via_openai", select)
    monkeypatch.setattr(phase3, "_critic_source_anomaly_via_openai", critic)

    source_path = tmp_path / "jesc111.pdf"
    source_path.write_bytes(b"%PDF-electricity")
    resumed = phase3.prepare_generation_graph(
        canonical=canonical,
        source_text=source,
        metadata={
            "subject": "Physics",
            "chapter_title": "Electricity",
            "board": "CBSE",
        },
        source_path=source_path,
        artifact_dir=tmp_path,
        verify_semantics=True,
        resume_review_graph=copy.deepcopy(graph),
    )
    assert resumed["status"] == "ready"


@pytest.mark.parametrize(
    ("subject", "adapter"),
    [
        ("Computer Science", "computer_science"),
        ("Artificial Intelligence", "computer_science"),
        ("Social Science", "social_science"),
        ("Life Science", "life_science"),
        ("Environmental Science", "environmental_science"),
        ("Electronics", "electronics"),
        ("General Science", "general_science"),
        ("English Literature", "language_literature"),
        ("Commercial Studies", "commerce"),
    ],
)
def test_subject_adapters_resolve_specialisms_before_generic_science(
    subject: str, adapter: str,
):
    assert phase3.subject_adapter(subject) == adapter


def test_api_can_demote_unnumbered_parser_candidate_activity_heading():
    source = (
        "# Matter\n\n"
        "## Matter Around Us\nStable teaching content.\n\n"
        "## Think About It\nDiscuss the observation with a partner.\n"
    )
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="publisher-variant.mmd",
        consumer_module="build_concepts",
    ).canonical

    def classify(payload: dict) -> dict:
        rows = []
        for row in payload["sections"]:
            title = row["title"]
            if title == "Matter":
                role = "chapter_heading"
            elif title == "Matter Around Us":
                role = "main_topic"
            elif title == "Think About It":
                role = "activity"
            else:
                role = row["baseline_role"]
            rows.append({
                "section_id": row["section_id"],
                "role": role,
                "parent_section_id": "",
                "confidence": 0.999,
                "evidence": ["semantic role verified from heading and content"],
            })
        return {"sections": rows}

    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={"chapter_title": "Matter", "subject": "General Science"},
        hierarchy_provider=classify,
        critic_provider=lambda _payload: {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )

    assert graph["classification_mode"] == "api_classified_and_verified"
    assert [row["title"] for row in graph["topics"]] == ["Matter Around Us"]
    activity = next(
        row for row in graph["sections"] if row["title"] == "Think About It"
    )
    assert activity["role"] == "activity"


def test_headingless_non_latin_source_uses_selected_chapter_identity():
    source = (
        "यह अध्याय ऊर्जा के रूपों का वर्णन करता है।\n\n"
        "गतिज और स्थितिज ऊर्जा पर चर्चा।\n"
    )
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="ऊर्जा.mmd",
        consumer_module="build_concepts",
    ).canonical
    graph, _report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={"chapter_title": "ऊर्जा", "subject": "General Science"},
    )
    semantic = phase3.render_semantic_source(graph, canonical)

    assert graph["status"] == "ready"
    assert graph["topics"] == [{
        "topic_id": "TOPIC-0001",
        "order": 1,
        "title": "ऊर्जा",
        "display_title": "ऊर्जा",
        "structural_number": "",
        "section_id": "PHASE3-SYNTHETIC-TOPIC",
        "source_start": 0,
        "source_end": len(source),
        "source": "acsd",
    }]
    assert "गतिज और स्थितिज ऊर्जा" in semantic
    assert not kr.rich_text_issues(semantic)


def test_source_contract_hash_changes_when_verified_overlay_changes():
    _source, canonical, _graph, _report, _semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )
    changed = copy.deepcopy(canonical)
    changed.setdefault("source_overlays", []).append({
        "overlay_id": "OVERLAY-0001",
        "page_number": 6,
        "recovered_text": "2 The Making of Nationalism in Europe",
    })

    assert phase3.source_contract_hash(changed) != phase3.source_contract_hash(canonical)


def test_source_contract_hash_compatibility_survives_no_overlay_reload():
    _source, canonical, _graph, _report, _semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )
    persisted = copy.deepcopy(canonical)
    marker = persisted.setdefault("source_adjudication", {
        "version": phase3.phase22.ADJUDICATION_VERSION,
        "status": "not_required",
        "raw_mmd_changed": False,
    })
    marker.pop("semantic_source_sha256", None)
    assert not persisted.get("source_overlays")

    in_memory = copy.deepcopy(persisted)
    in_memory["source_adjudication"]["semantic_source_sha256"] = str(
        in_memory["document"]["source_sha256"]
    )

    persisted_hash = phase3.source_contract_hash(persisted)
    in_memory_hash = phase3.source_contract_hash(in_memory)

    # Keep the established hash identity for newly compiled graphs, while
    # recognizing only this historic transient-seal pair across a reload.
    assert persisted_hash != in_memory_hash
    assert phase3._source_contract_hash_matches(in_memory_hash, persisted)
    assert phase3._source_contract_hash_matches(persisted_hash, in_memory)


def test_qid_derived_type_scope_ignores_stale_visible_topic_hint():
    _source, _canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )
    mined = {"types": [{
        "type_id": "TYPE-0003",
        "type_title": "Interpreting the political purpose of economic measures",
        "type_description": "Use source evidence to infer the political end of an economic policy.",
        "topic_match_hint": "1 The Aristocracy and the New Middle Class",
        "source_question_ids": ["QINV-0003"],
        "case_prompts": [],
        "is_activity": False,
    }]}

    annotated = phase3.annotate_mined_types(mined, graph)
    mtype = annotated["types"][0]

    assert mtype["_semantic_topic_ids"] == ["TOPIC-0002"]
    assert mtype["_semantic_subtopic_ids"] == ["TOPIC-0002-SUB-003"]
    assert mtype["topic_match_hint"] == "The Making of Nationalism in Europe"


def test_chapter_wide_inventory_topic_resolution_does_not_mutate_parent_task():
    _source, _canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )
    inventory = {"items": [{
        "qid": "QINV-0017",
        "topic_hint": "The French Revolution and the Idea of the Nation",
        "_topic_scope": "chapter",
        "_chapter_wide_task": True,
    }]}
    graph_before = copy.deepcopy(graph)

    annotated_inventory = phase3.annotate_inventory(inventory, graph)
    item = annotated_inventory["items"][0]
    task = next(row for row in graph["tasks"] if row["qid"] == "QINV-0017")

    assert item["_semantic_topic_id"] == "TOPIC-0001"
    assert item["topic_hint"] == "The French Revolution and the Idea of the Nation"
    assert task["topic_id"] == "TOPIC-0006"
    assert graph == graph_before

    mined = {"types": [{
        "type_id": "TYPE-CHAPTER-WIDE",
        "type_title": "Collective identity measures",
        "type_description": "Explain measures used to construct collective identity.",
        "topic_match_hint": "The French Revolution and the Idea of the Nation",
        "source_question_ids": ["QINV-0017"],
        "case_prompts": [{
            "case_id": "CASE-COLLECTIVE-IDENTITY",
            "topic_match_hint": (
                "The French Revolution and the Idea of the Nation"
            ),
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": "QINV-0017",
                "example_prompt": "Explain measures used to create identity.",
            }],
        }],
        "is_activity": False,
    }]}
    annotated_type = phase3.annotate_mined_types(mined, graph)["types"][0]

    assert annotated_type["_semantic_topic_ids"] == ["TOPIC-0001"]
    assert annotated_type["topic_match_hint"] == (
        "The French Revolution and the Idea of the Nation"
    )


def test_cross_topic_type_keeps_case_level_topic_identities():
    _source, _canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )
    mined = {"types": [{
        "type_id": "TYPE-CROSS",
        "type_title": "Comparing national transformation across periods",
        "type_description": "Use the source focus defined by each Case.",
        "source_question_ids": ["QINV-0001", "QINV-0003"],
        "case_prompts": [
            {
                "case_id": "CASE-OPENING",
                "case_title": "Interpret the opening visual",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": "Interpret the opening print.",
                }],
            },
            {
                "case_id": "CASE-ECONOMIC",
                "case_title": "Infer political ends from economic measures",
                "examples": [{
                    "source_question_id": "QINV-0003",
                    "example_prompt": "Infer the political end.",
                }],
            },
        ],
        "is_activity": False,
    }]}

    annotated = phase3.annotate_mined_types(mined, graph)
    mtype = annotated["types"][0]
    assert mtype["_semantic_scope"] == "multi_topic_reuse"
    assert mtype["placement_scope"] == "normal"
    assert mtype["_semantic_topic_ids"] == ["TOPIC-0001", "TOPIC-0002"]
    assert [case["_semantic_topic_id"] for case in mtype["case_prompts"]] == [
        "TOPIC-0001", "TOPIC-0002",
    ]

    units = generation._expand_mined_types_to_assignment_units(
        annotated["types"]
    )
    units = phase3.annotate_assignment_units(units, graph)
    assert [unit["_semantic_topic_id"] for unit in units] == [
        "TOPIC-0001", "TOPIC-0002",
    ]
    assert [unit["source_question_ids"] for unit in units] == [
        ["QINV-0001"], ["QINV-0003"],
    ]


def test_unknown_generated_concept_topic_uses_bounded_api_and_independent_critic():
    _source, _canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )
    records = [{
        "topic": "A heading phrase not present in the source",
        "parent_concept": "Social Change",
        "concept_title": "Aristocracy and the Emerging Middle Classes",
        "concept_details": (
            "Description: Landed aristocracy remained powerful while industrial "
            "and commercial middle classes expanded."
        ),
        "keywords": "",
    }]

    def provider(payload: dict) -> dict:
        assert payload["concepts"][0]["generated_topic_hint"] == (
            "A heading phrase not present in the source"
        )
        assert {row["topic_id"] for row in payload["topics"]} >= {
            "TOPIC-0001", "TOPIC-0002"
        }
        return {"assignments": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "topic_id": "TOPIC-0002",
            "confidence": 0.999,
            "reason": "The concept is supported by Section 2 social-class evidence.",
        }]}

    def critic(payload: dict) -> dict:
        assert payload["proposed_assignments"][0]["topic_id"] == "TOPIC-0002"
        return {"verdict": "verified", "confidence": 0.999, "issues": []}

    resolved = phase3.canonicalize_record_topics(
        records, graph, provider=provider, critic=critic
    )

    assert resolved[0]["_semantic_topic_id"] == "TOPIC-0002"
    assert resolved[0]["topic"] == "The Making of Nationalism in Europe"
    assert resolved[0]["_semantic_topic_contract"] == "api-verified-topic-id"
    assert not resolved[0].get("review_flags")

    # The critic is an auditor: its dissent lands as review flags on the
    # affected rows and never blocks the verified assignment (Q10).
    dissented = phase3.canonicalize_record_topics(
        records,
        graph,
        provider=provider,
        critic=lambda _payload: {
            "verdict": "verified",
            "confidence": 0.999,
            "issues": ["source evidence is insufficient"],
        },
    )
    assert dissented[0]["_semantic_topic_id"] == "TOPIC-0002"
    assert dissented[0]["_semantic_topic_contract"] == "api-verified-topic-id"
    assert any(
        "source evidence is insufficient" in flag
        for flag in dissented[0]["review_flags"]
    )


def test_sub_floor_concept_topic_confidence_ships_flagged_not_blocked():
    _source, _canonical, graph, _report, _semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )
    records = [{
        "topic": "A heading phrase not present in the source",
        "parent_concept": "Social Change",
        "concept_title": "Aristocracy and the Emerging Middle Classes",
        "concept_details": "Description: Landed aristocracy remained powerful.",
        "keywords": "",
    }]

    def low_confidence_provider(payload: dict) -> dict:
        return {"assignments": [{
            "concept_id": payload["concepts"][0]["concept_id"],
            "topic_id": "TOPIC-0002",
            "confidence": 0.5,
            "reason": "uncertain but this is the only supported topic",
        }]}

    resolved = phase3.canonicalize_record_topics(
        records,
        graph,
        provider=low_confidence_provider,
        critic=lambda _payload: {
            "verdict": "verified", "confidence": 0.999, "issues": [],
        },
    )

    # The assignment stands (never silently lost, never blocked) and the
    # sub-floor confidence is a review flag, not a gate.
    assert resolved[0]["_semantic_topic_id"] == "TOPIC-0002"
    assert resolved[0]["_semantic_topic_confidence"] == 0.5
    assert any(
        "confidence 0.500" in flag and "flagged for review" in flag
        for flag in resolved[0]["review_flags"]
    )

    # The bounded ID contract stays mechanical and fail-closed.
    with pytest.raises(ValueError, match="bounded ID contract"):
        phase3.canonicalize_record_topics(
            records,
            graph,
            provider=lambda payload: {"assignments": [{
                "concept_id": payload["concepts"][0]["concept_id"],
                "topic_id": "TOPIC-DOES-NOT-EXIST",
                "confidence": 0.999,
                "reason": "invented topic",
            }]},
            critic=lambda _payload: {
                "verdict": "verified", "confidence": 0.999, "issues": [],
            },
        )


def test_graph_validation_rejects_unknown_ids_order_drift_and_override_tampering():
    _source, canonical, graph, _report, semantic = _compile_fixture(
        "RNE.mmd", subject="History",
    )

    unknown = copy.deepcopy(graph)
    unknown["blocks"][0]["topic_id"] = "TOPIC-DOES-NOT-EXIST"
    assert "unknown_block_topic" in {
        row["code"] for row in phase3.validate_graph(
            unknown, canonical=canonical, semantic_source=semantic
        )
    }

    reordered = copy.deepcopy(graph)
    reordered["blocks"][0], reordered["blocks"][1] = (
        reordered["blocks"][1], reordered["blocks"][0]
    )
    assert "block_order_drift" in {
        row["code"] for row in phase3.validate_graph(
            reordered, canonical=canonical, semantic_source=semantic
        )
    }

    context_tampered = copy.deepcopy(graph)
    context_tampered["metadata"]["subject"] = "Mathematics"
    assert "invalid_semantic_context_hash" in {
        row["code"] for row in phase3.validate_graph(
            context_tampered, canonical=canonical, semantic_source=semantic
        )
    }

    tampered = copy.deepcopy(graph)
    tampered["blocks"][0]["source_override"] = {
        "resolved_text": "Verified text",
        "resolved_sha256": "not-the-real-hash",
    }
    assert "invalid_source_override_hash" in {
        row["code"] for row in phase3.validate_graph(
            tampered, canonical=canonical, semantic_source=semantic
        )
    }


def test_hierarchy_critic_dissent_flags_and_never_blocks():
    classifications = {
        "SEC-0001": {
            "section_id": "SEC-0001",
            "role": "main_topic",
            "parent_section_id": "",
        }
    }

    # A repair request without a bounded repair is dissent: the hierarchy
    # stands and the dissent is recorded as an advisory issue (Q10).
    repaired, advisory = phase3._apply_critic_repairs(
        classifications,
        {
            "verdict": "repair_required",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )
    assert repaired == classifications
    assert any(
        row["code"] == "hierarchy_critic_dissent"
        and row["severity"] == "warning"
        for row in advisory
    )

    # Sub-floor critic confidence is a review flag, never a gate.
    repaired, advisory = phase3._apply_critic_repairs(
        classifications,
        {
            "verdict": "verified",
            "confidence": 0.5,
            "repairs": [],
            "issues": [],
        },
    )
    assert repaired == classifications
    assert any(
        row["code"] == "hierarchy_critic_low_confidence"
        and "0.500" in row["message"]
        for row in advisory
    )

    # A fully verified review leaves no advisory issues.
    repaired, advisory = phase3._apply_critic_repairs(
        classifications,
        {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        },
    )
    assert repaired == classifications
    assert advisory == []

    # Naming a section that does not exist stays a mechanical failure.
    with pytest.raises(ValueError, match="invented a section ID"):
        phase3._apply_critic_repairs(
            classifications,
            {
                "verdict": "repair_required",
                "confidence": 0.999,
                "repairs": [{
                    "section_id": "SEC-MISSING",
                    "role": "subtopic",
                    "parent_section_id": "",
                    "reason": "bad",
                }],
                "issues": ["misplaced"],
            },
        )


def test_explicit_offline_mode_can_compile_deterministic_graph_without_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    ).canonical

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)

    def unexpected_api(_payload: dict) -> dict:
        raise AssertionError("explicit offline compilation must not call the API")

    monkeypatch.setattr(phase3, "_classify_hierarchy_via_openai", unexpected_api)
    monkeypatch.setattr(phase3, "_critic_hierarchy_via_openai", unexpected_api)

    graph = phase3.prepare_generation_graph(
        canonical=canonical,
        source_text=source,
        metadata={
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "board": "CBSE",
        },
        source_path=None,
        artifact_dir=tmp_path,
        verify_semantics=False,
    )

    assert graph["status"] == "ready"
    assert graph["classification_mode"] == "deterministic"
    assert not phase3.checkpoint_resume_graph_safe(graph, canonical=canonical)
    assert (tmp_path / phase3.GRAPH_FILENAME).exists()
    assert (tmp_path / phase3.SEMANTIC_SOURCE_FILENAME).exists()


def test_stale_page_evidence_compiler_is_rebuilt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF-test")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / phase3.VISION_ACSD_FILENAME).write_text(
        '{"pdf_sha256":"same","compiler_version":"old","schema_version":"1.0.0","pages":[]}',
        encoding="utf-8",
    )
    fresh = {
        "pdf_sha256": "same",
        "compiler_version": phase3.page_acsd.FALLBACK_COMPILER,
        "schema_version": "1.1.0",
        "pages": [],
    }
    calls = []

    monkeypatch.setattr(phase3, "vision_enabled", lambda: True)
    monkeypatch.setattr(phase3.page_acsd, "_pdf_sha256", lambda _path: "same")
    monkeypatch.setattr(
        phase3.page_acsd,
        "extract_pdf_to_page_acsd",
        lambda _path: calls.append("extract") or copy.deepcopy(fresh),
    )

    loaded = phase3.load_page_evidence(source_path, artifact_dir)

    assert calls == ["extract"]
    assert loaded == fresh


def test_matching_api_verified_graph_is_reused_without_new_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    source = (DATA / "RNE.mmd").read_text(encoding="utf-8")
    canonical = phase2.compile_phase2_source(
        source,
        source_filename="RNE.mmd",
        consumer_module="build_concepts",
    ).canonical

    def classify(payload: dict) -> dict:
        return {
            "sections": [
                {
                    "section_id": row["section_id"],
                    "role": row["baseline_role"],
                    "parent_section_id": "",
                    "confidence": 0.999,
                    "evidence": ["verified test hierarchy"],
                }
                for row in payload["sections"]
            ]
        }

    def critic(_payload: dict) -> dict:
        return {
            "verdict": "verified",
            "confidence": 0.999,
            "repairs": [],
            "issues": [],
        }

    graph, report = phase3.compile_semantic_graph(
        canonical,
        source_text=source,
        metadata={
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "board": "CBSE",
        },
        hierarchy_provider=classify,
        critic_provider=critic,
    )
    semantic_source = phase3.render_semantic_source(graph, canonical)
    phase3.write_artifacts(
        tmp_path,
        graph=graph,
        report=report,
        semantic_source=semantic_source,
    )
    assert graph["classification_mode"] == "api_classified_and_verified"
    assert phase3.checkpoint_resume_graph_safe(graph, canonical=canonical)

    monkeypatch.setattr(phase3, "semantic_api_enabled", lambda: True)

    def unexpected_api(_payload: dict) -> dict:
        raise AssertionError("a current API-verified graph must be reused")

    monkeypatch.setattr(phase3, "_classify_hierarchy_via_openai", unexpected_api)
    monkeypatch.setattr(phase3, "_critic_hierarchy_via_openai", unexpected_api)

    reused = phase3.prepare_generation_graph(
        canonical=canonical,
        source_text=source,
        metadata={
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "board": "CBSE",
        },
        source_path=None,
        artifact_dir=tmp_path,
        verify_semantics=True,
    )

    assert reused == graph
    assert phase3.load_verified_generation_graph(
        tmp_path,
        canonical,
        {
            "subject": "Mathematics",
            "chapter_title": "A Different Chapter",
            "board": "CBSE",
        },
    ) is None

    stale = copy.deepcopy(graph)
    stale["compiler_version"] = "phase-3-semantic-graph-old"
    (tmp_path / phase3.GRAPH_FILENAME).write_text(
        phase3._json_text(stale), encoding="utf-8"
    )
    assert phase3.load_verified_generation_graph(
        tmp_path,
        canonical,
        {
            "subject": "History",
            "chapter_title": "The Rise of Nationalism in Europe",
            "board": "CBSE",
        },
    ) is None
