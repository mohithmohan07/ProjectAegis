"""GPT chapter-outline & question-boundary pass: validation and compilation.

Structure is a semantic judgment: pedagogy banners look like headings, and
boards disagree about whether "1 a) b)" is one question or three. These tests
cover the deterministic side of the pass — reference validation, verbatim
bounds, outline-driven rendering, and GPT-decided splits becoming separate
inventory leaves.
"""
from __future__ import annotations

from app.services import canonical_source_phase21_structure as structure
from app.services import canonical_source_phase221_fallback as fallback


def _page_acsd() -> dict:
    return {
        "pdf_sha256": "abc123",
        "pages": [
            {
                "page_id": "PDF-PAGE-0001",
                "page_number": 1,
                "blocks": [
                    {"reading_order": 1, "kind": "heading", "heading_level": 1,
                     "text": "Three-Dimensional Shapes"},
                    {"reading_order": 2, "kind": "heading", "heading_level": 2,
                     "text": "Understand"},
                    {"reading_order": 3, "kind": "heading", "heading_level": 1,
                     "text": "Dimensions"},
                    {"reading_order": 4, "kind": "paragraph",
                     "text": "Two-dimensional shapes occupy space on a flat surface."},
                ],
            },
            {
                "page_id": "PDF-PAGE-0002",
                "page_number": 2,
                "blocks": [
                    {"reading_order": 1, "kind": "heading", "heading_level": 1,
                     "text": "Exercise 1"},
                    {"reading_order": 2, "kind": "task", "source_label": "(1)",
                     "text": (
                         "(1) Select the correct option. "
                         "(i) What shape will be formed if carrom pieces are "
                         "placed one on top of the other? A) Cuboid B) Cylinder "
                         "C) Cone D) Cube "
                         "(ii) Which of the following shapes is not "
                         "three-dimensional? A) square B) Sphere C) Cylinder D) Cone"
                     )},
                ],
            },
        ],
    }


def _candidate() -> dict:
    return {
        "chapter_title": "Three-Dimensional Shapes",
        "topics": [
            {"title": "Dimensions", "kind": "content",
             "start_page_id": "PDF-PAGE-0001", "start_reading_order": 3},
            {"title": "Exercise 1", "kind": "assessment",
             "start_page_id": "PDF-PAGE-0002", "start_reading_order": 1},
        ],
        "task_partitions": [
            {"page_id": "PDF-PAGE-0002", "reading_order": 2,
             "independent_parts": [
                 {"label": "(i)", "stem": "Select the correct option.",
                  "text": (
                      "(i) What shape will be formed if carrom pieces are "
                      "placed one on top of the other? A) Cuboid B) Cylinder "
                      "C) Cone D) Cube"
                  )},
                 {"label": "(ii)", "stem": "Select the correct option.",
                  "text": (
                      "(ii) Which of the following shapes is not "
                      "three-dimensional? A) square B) Sphere C) Cylinder D) Cone"
                  )},
             ]},
        ],
        "notes": [],
    }


def test_outline_normalization_accepts_a_grounded_response():
    outline, flags = fallback._normalize_chapter_outline(_page_acsd(), _candidate())

    assert outline is not None
    assert flags == []
    assert outline["chapter_title"] == "Three-Dimensional Shapes"
    assert [t["title"] for t in outline["topics"]] == ["Dimensions", "Exercise 1"]
    assert outline["topics"][1]["kind"] == "assessment"
    parts = outline["task_partitions"][0]["independent_parts"]
    assert len(parts) == 2
    assert parts[0]["stem"] == "Select the correct option."


def test_outline_normalization_drops_ungrounded_references():
    candidate = _candidate()
    candidate["topics"].append({
        "title": "Ghost Topic", "kind": "content",
        "start_page_id": "PDF-PAGE-0009", "start_reading_order": 1,
    })
    candidate["task_partitions"].append({
        # References a heading, not a task.
        "page_id": "PDF-PAGE-0002", "reading_order": 1,
        "independent_parts": [
            {"label": "a", "stem": "", "text": "Exercise 1"},
            {"label": "b", "stem": "", "text": "Exercise 1"},
        ],
    })
    candidate["task_partitions"][0]["independent_parts"][1]["text"] = (
        "A rewritten question that never appeared in the source block."
    )

    outline, flags = fallback._normalize_chapter_outline(_page_acsd(), candidate)

    assert outline is not None
    assert [t["title"] for t in outline["topics"]] == ["Dimensions", "Exercise 1"]
    # The rewritten part fails verbatim; with only 1 conforming part the
    # partition is dropped entirely — the task stays whole.
    assert outline["task_partitions"] == []
    assert any("does not exist" in flag for flag in flags)
    assert any("not a task block" in flag for flag in flags)


def test_outline_without_content_topics_is_unusable():
    candidate = _candidate()
    for topic in candidate["topics"]:
        topic["kind"] = "assessment"

    outline, flags = fallback._normalize_chapter_outline(_page_acsd(), candidate)

    assert outline is None
    assert any("no usable content topic" in flag for flag in flags)


def test_renderer_promotes_topics_and_demotes_banners():
    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    page_acsd["chapter_outline"] = outline

    rendered = fallback.render_page_acsd_to_mmd(page_acsd)

    assert "# Dimensions" in rendered
    # Banners and non-topic headings keep their words without heading weight.
    assert "# Understand" not in rendered
    assert "**Understand**" in rendered
    assert "# Three-Dimensional Shapes" not in rendered
    assert "**Three-Dimensional Shapes**" in rendered
    # Assessment collections are not content topics — no heading for them.
    assert "# Exercise 1" not in rendered


def test_renderer_without_outline_keeps_headings():
    rendered = fallback.render_page_acsd_to_mmd(_page_acsd())

    assert "# Three-Dimensional Shapes" in rendered
    assert "## Understand" in rendered


def test_gpt_boundary_parts_become_leaf_cases():
    canonical = {
        "tasks": [{
            "task_id": "TASK-0001",
            "qid": "QINV-0022",
            "identity_key": "identity-1",
            "source_kind": "checkpoint_question",
            "source_label": "(1)",
            "raw_prompt": (
                "(1) Select the correct option. (i) What shape? A) Cuboid "
                "(ii) Which is not 3D? A) square"
            ),
            "display_prompt": "(1) Select the correct option. …",
            "gpt_boundary_parts": [
                {"label": "(i)", "stem": "Select the correct option.",
                 "text": "(i) What shape? A) Cuboid"},
                {"label": "(ii)", "stem": "Select the correct option.",
                 "text": "(ii) Which is not 3D? A) square"},
            ],
        }],
    }

    structure.materialize_task_leaf_cases(canonical)

    leaves = canonical["tasks"][0]["leaf_cases"]
    assert len(leaves) == 2
    assert all(l["decomposition"] == "gpt_semantic_boundary" for l in leaves)
    assert leaves[0]["qid"] == "QINV-0022.1"
    assert leaves[0]["subpart_label"] == "(i)"
    assert leaves[0]["raw_prompt"] == "(i) What shape? A) Cuboid"
    assert "Select the correct option." in leaves[0]["shared_context"]
    assert leaves[1]["qid"] == "QINV-0022.2"


def test_gpt_split_survives_never_split_inventory(monkeypatch):
    from app.services import canonical_source_phase2 as phase2

    canonical = {
        "tasks": [{
            "task_id": "TASK-0001",
            "qid": "QINV-0022",
            "identity_key": "identity-1",
            "source_kind": "checkpoint_question",
            "source_label": "(1)",
            "order": 1,
            "raw_prompt": (
                "(1) Select the correct option. (i) What shape? A) Cuboid "
                "(ii) Which is not 3D? A) square"
            ),
            "display_prompt": "(1) Select the correct option. …",
            "gpt_boundary_parts": [
                {"label": "(i)", "stem": "Select the correct option.",
                 "text": "(i) What shape? A) Cuboid"},
                {"label": "(ii)", "stem": "Select the correct option.",
                 "text": "(ii) Which is not 3D? A) square"},
            ],
        }],
        "figures": [],
        "images": [],
    }
    structure.materialize_task_leaf_cases(canonical)
    monkeypatch.setattr(phase2, "_never_split_questions", lambda: True)

    inventory = phase2.inventory_from_canonical(canonical)

    qids = [item["qid"] for item in inventory["items"]]
    assert qids == ["QINV-0022.1", "QINV-0022.2"]
    labels = [item["subpart_label"] for item in inventory["items"]]
    assert labels == ["(i)", "(ii)"]


def test_deterministic_leaves_still_never_split(monkeypatch):
    from app.services import canonical_source_phase2 as phase2

    canonical = {
        "tasks": [{
            "task_id": "TASK-0002",
            "qid": "QINV-0031",
            "identity_key": "identity-2",
            "source_kind": "checkpoint_question",
            "order": 1,
            "raw_prompt": "Answer both: (a) define X. (b) define Y.",
            "display_prompt": "Answer both: (a) define X. (b) define Y.",
            "leaf_cases": [
                {"qid": "QINV-0031.1", "case_id": "CASE-QINV-0031.1",
                 "raw_prompt": "(a) define X.",
                 "display_prompt": "(a) define X.",
                 "decomposition": ""},
                {"qid": "QINV-0031.2", "case_id": "CASE-QINV-0031.2",
                 "raw_prompt": "(b) define Y.",
                 "display_prompt": "(b) define Y.",
                 "decomposition": ""},
            ],
        }],
        "figures": [],
        "images": [],
    }
    monkeypatch.setattr(phase2, "_never_split_questions", lambda: True)

    inventory = phase2.inventory_from_canonical(canonical)

    assert [item["qid"] for item in inventory["items"]] == ["QINV-0031"]


def test_outline_request_purpose_is_registered():
    # The policy module rejects unknown purposes with ValueError, and the
    # outline pass degrades on exception — so an unregistered purpose would
    # silently disable the pass for every book. Pin the registration.
    from aegis_pipeline import openai_policy

    assert openai_policy.reasoning_effort_for("chapter_outline") == "max"


def test_misconfigured_outline_pass_fails_loudly(monkeypatch):
    from app.services import canonical_source_phase22 as phase22

    def _boom(**_kwargs):
        raise ValueError("Unknown OpenAI request purpose 'chapter_outline'")

    monkeypatch.setattr(phase22, "_openai_multimodal_json", _boom)
    monkeypatch.setattr(fallback, "_read_verified_batch_cache", lambda _key: None)

    try:
        fallback.derive_chapter_outline(_page_acsd())
    except RuntimeError as exc:
        assert "misconfigured" in str(exc)
    else:
        raise AssertionError("a wiring error must not degrade silently")


def test_model_failure_still_degrades_to_deterministic_structure(monkeypatch):
    from app.services import canonical_source_phase22 as phase22

    def _boom(**_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(phase22, "_openai_multimodal_json", _boom)
    monkeypatch.setattr(fallback, "_read_verified_batch_cache", lambda _key: None)

    assert fallback.derive_chapter_outline(_page_acsd()) is None
