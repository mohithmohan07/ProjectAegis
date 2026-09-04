"""GPT chapter-outline & question-boundary pass: validation and compilation.

Structure is a semantic judgment: pedagogy banners look like headings, and
boards disagree about whether "1 a) b)" is one question or three. These tests
cover the deterministic side of the pass — reference validation, verbatim
bounds, outline-driven rendering, and GPT-decided splits becoming separate
inventory leaves.
"""
from __future__ import annotations

import copy

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
    # Its parts are verbatim in no task block, so re-aiming has nothing to
    # aim at and the partition is still dropped.
    assert any("no task block holds the parts" in flag for flag in flags)


def test_outline_without_content_topics_keeps_its_question_boundaries():
    # Sectioning and question boundaries are separate judgments. Losing the
    # topics must not silently re-deterministize every question split too.
    candidate = _candidate()
    for topic in candidate["topics"]:
        topic["kind"] = "assessment"

    outline, flags = fallback._normalize_chapter_outline(_page_acsd(), candidate)

    assert outline is not None
    assert outline["topics"] == []
    assert len(outline["task_partitions"]) == 1
    assert any("no usable content topic" in flag for flag in flags)


def test_outline_with_neither_topics_nor_partitions_is_unusable():
    # Nothing of the model's reading survived; claiming a model-judged outline
    # would suppress the deterministic enumeration with nothing to replace it.
    candidate = _candidate()
    for topic in candidate["topics"]:
        topic["kind"] = "assessment"
    candidate["task_partitions"] = []

    outline, flags = fallback._normalize_chapter_outline(_page_acsd(), candidate)

    assert outline is None
    assert any("neither topics nor partitions" in flag for flag in flags)


def test_topicless_outline_leaves_heading_structure_deterministic():
    # With no content topic the renderer must not also swallow the source's
    # own headings — that is what collapses a chapter into a single topic.
    page_acsd = _page_acsd()
    candidate = _candidate()
    for topic in candidate["topics"]:
        topic["kind"] = "assessment"
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, candidate)
    page_acsd["chapter_outline"] = outline

    rendered = fallback.render_page_acsd_to_mmd(page_acsd)

    assert "# Dimensions" in rendered


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

    assert openai_policy.reasoning_effort_for("chapter_outline") in (
        openai_policy.REASONING_ORDER
    )


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


def test_derive_outline_caches_its_decision(monkeypatch, tmp_path):
    from app.services import canonical_source_phase22 as phase22

    calls = {"n": 0}

    def _respond(**_kwargs):
        calls["n"] += 1
        return _candidate()

    monkeypatch.setattr(phase22, "_openai_multimodal_json", _respond)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    first = fallback.derive_chapter_outline(_page_acsd())
    second = fallback.derive_chapter_outline(_page_acsd())

    assert first is not None
    assert first["chapter_title"] == "Three-Dimensional Shapes"
    assert [t["title"] for t in first["topics"]] == ["Dimensions", "Exercise 1"]
    # A decided outline is content-addressed by source hash: the second
    # conversion of the same book must not re-ask the model.
    assert calls["n"] == 1
    assert second == first


def test_trailing_answer_boxes_leave_the_question_text():
    # Exercise 1(2) shipped "…no vertices and no edges. ▯": the box printed
    # under a complete description is where the learner writes the answer,
    # not part of the question. Blanks INSIDE a sentence are the question.
    strip = fallback._without_trailing_answer_boxes

    assert strip("One face is curved. It has no vertices and no edges. ▯") == (
        "One face is curved. It has no vertices and no edges."
    )
    assert strip("Draw the shape. ▯ ▯") == "Draw the shape."
    assert strip("Name the solid: □") == "Name the solid:"
    # In-sentence blanks survive untouched, punctuation included.
    assert strip("Cuboid has ▯ vertices, ▯ rectangular faces, ▯ edges.") == (
        "Cuboid has ▯ vertices, ▯ rectangular faces, ▯ edges."
    )
    assert strip("It has ▯ vertices, ▯ triangular faces") == (
        "It has ▯ vertices, ▯ triangular faces"
    )
    # A prompt that is nothing but a box is never emptied.
    assert strip("▯") == "▯"


def test_cached_task_replay_drops_trailing_answer_boxes():
    page_acsd = {
        "pages": [{
            "page_id": "PDF-PAGE-0008",
            "blocks": [
                {"reading_order": 1, "kind": "task", "source_label": "(2)",
                 "text": "There are 6 faces, 12 edges and 8 vertices. ▯",
                 "table_rows": [], "latex": "", "caption": ""},
                {"reading_order": 2, "kind": "paragraph",
                 "text": "A trailing box in prose is left alone. ▯",
                 "table_rows": [], "latex": "", "caption": ""},
            ],
        }],
    }

    fallback._scrub_page_acsd_escape_artifacts(page_acsd)

    blocks = page_acsd["pages"][0]["blocks"]
    assert blocks[0]["text"] == "There are 6 faces, 12 edges and 8 vertices."
    assert blocks[1]["text"].endswith("▯")


def test_prompts_state_the_conversation_and_answer_box_rules():
    import re as _re

    def flat(value: str) -> str:
        # The prompts are hard-wrapped; rules must be matched as prose.
        return _re.sub(r"\s+", " ", value)

    extraction = flat(fallback._extraction_system_prompt())
    verification = flat(fallback._verification_system_prompt())

    assert "Never append ▯ to the end of a prompt" in extraction
    assert "A printed conversation" in extraction
    assert "is ONE task" in extraction
    assert "never describe it in words" in extraction
    assert "never demand it back" in verification
    assert (
        "speech-bubble exchange posing one activity is correctly ONE task"
        in verification
    )


def test_advisory_gate_issues_ship_under_review_flags():
    # A Grade 6 chapter transcribed and outlined cleanly was refused whole
    # because one task named a visual the Figure registry could not resolve.
    # Advisory codes are review notes under GPT-authoritative parsing.
    canonical = {"tasks": [{"task_id": "TASK-0001", "qid": "QINV-0001"}]}
    report = {"status": "failed"}
    issues = [
        {"severity": "warning", "code": "unresolved_required_visual",
         "task_id": "TASK-0001"},
        {"severity": "warning", "code": "display_rich_text_requires_review"},
    ]

    fallback._accept_gate_issues_with_flags(canonical, report, issues)

    assert canonical["phase2_inventory_ready"] is True
    assert report["phase2_inventory_ready"] is True
    assert report["status"] == "passed_with_warnings"
    flags = canonical["source_review_flags"]
    assert any("unresolved_required_visual (TASK-0001)" == f for f in flags)
    assert any("display_rich_text_requires_review" == f for f in flags)


def test_real_errors_still_stop_the_pipeline():
    canonical = {"tasks": [{"task_id": "TASK-0001", "qid": "QINV-0001"}]}
    issues = [{"severity": "error", "code": "phase2_qid_order_mismatch"}]

    try:
        fallback._accept_gate_issues_with_flags(canonical, {}, issues)
    except ValueError as exc:
        assert "phase2_qid_order_mismatch" in str(exc)
    else:
        raise AssertionError("a genuine error must still fail closed")


def test_a_source_with_no_tasks_fails_closed():
    try:
        fallback._accept_gate_issues_with_flags({"tasks": []}, {}, [])
    except ValueError as exc:
        assert "phase2_no_tasks" in str(exc)
    else:
        raise AssertionError("a source with no tasks is not usable")


def test_stale_figure_verdicts_are_dropped_after_model_links_apply():
    # Validation runs on the deterministic parse before the verified page
    # relationships are applied, so "draw a circle of radius 6 cm" read as a
    # figure reference. Job 20 flagged TASK-00018 as needing an unresolvable
    # visual while the task carried no visual requirement at all.
    canonical = {
        "tasks": [
            {"task_id": "TASK-00018", "requires_visual": False,
             "figure_refs": [], "image_urls": [],
             "unresolved_figure_reference_ids": [],
             "ambiguous_figure_reference_ids": []},
            {"task_id": "TASK-00021", "requires_visual": True,
             "figure_refs": [], "image_urls": [],
             "unresolved_figure_reference_ids": ["Fig. 1.4"],
             "ambiguous_figure_reference_ids": []},
        ],
    }
    report = {"issues": [
        {"severity": "warning", "code": "unresolved_required_visual",
         "task_id": "TASK-00018"},
        {"severity": "warning", "code": "unresolved_required_visual",
         "task_id": "TASK-00021"},
        {"severity": "warning", "code": "unresolved_explicit_figure_reference",
         "task_id": "TASK-00021"},
        {"severity": "warning", "code": "unresolved_explicit_figure_reference",
         "task_id": "TASK-00018"},
        {"severity": "error", "code": "source_reconstruction_mismatch"},
    ]}

    fallback._refresh_task_figure_issues(canonical, report)

    codes = [(i["code"], i.get("task_id")) for i in report["issues"]]
    # TASK-00018's verdicts are stale and gone; TASK-00021 still genuinely
    # names a Figure that does not resolve, and unrelated issues survive.
    assert ("unresolved_required_visual", "TASK-00018") not in codes
    assert ("unresolved_explicit_figure_reference", "TASK-00018") not in codes
    assert ("unresolved_required_visual", "TASK-00021") in codes
    assert ("unresolved_explicit_figure_reference", "TASK-00021") in codes
    assert ("source_reconstruction_mismatch", None) in codes


def test_text_layer_divergence_is_reviewed_not_rejected():
    # The page image is the authority; the embedded text layer is only
    # corroboration. A garbled or differently-ordered text layer must not
    # reject a page the model read correctly.
    page = fallback.PdfPage(
        page_id="PDF-PAGE-0001",
        page_number=1,
        text=" ".join(f"gibberish{n}" for n in range(60)),
        image_data_url="data:image/jpeg;base64,AA==",
        width=600.0,
        height=800.0,
    )
    candidate = {"pages": [{
        "page_id": "PDF-PAGE-0001",
        "confidence": 0.99,
        "blocks": [{
            "reading_order": 1, "kind": "paragraph",
            "bbox": [10, 10, 900, 200],
            "text": " ".join(f"realword{n}" for n in range(60)),
            "heading_level": 0, "source_label": "", "latex": "",
            "table_rows": [], "linked_visual_orders": [],
            "linked_context_orders": [], "caption": "", "confidence": 0.99,
        }],
    }]}

    normalized, reason = fallback.validate_page_extraction([page], candidate)

    assert reason == ""
    assert normalized is not None
    flags = normalized["pages"][0].get("review_flags") or []
    assert any("diverges from the embedded text layer" in f for f in flags)


def test_model_judged_boundaries_silence_the_enumeration_heuristics():
    # With an outline present the model ruled on every task; a task it left
    # whole must not be split by the deterministic enumerator.
    canonical = {
        "chapter_outline": {"version": fallback.OUTLINE_VERSION},
        "tasks": [{
            "task_id": "TASK-0001",
            "qid": "QINV-0001",
            "identity_key": "identity-1",
            "raw_prompt": "Answer: (a) define X. (b) define Y. (c) define Z.",
            "display_prompt": "Answer: (a) define X. (b) define Y. (c) define Z.",
        }],
    }

    structure.materialize_task_leaf_cases(canonical)

    assert not canonical["tasks"][0].get("leaf_cases")


def test_inventory_records_how_the_source_was_read(monkeypatch):
    # The counts are the diagnosis: a chapter with nine task blocks and four
    # questions read very differently depending on whether a model decided
    # the boundaries or a regular expression did.
    from app.services import canonical_source_phase2 as phase2

    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    canonical = {
        "chapter_outline": outline,
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

    provenance = phase2.inventory_from_canonical(canonical)["extraction_provenance"]

    assert provenance["chapter_outline_applied"] is True
    assert provenance["chapter_outline_version"] == fallback.OUTLINE_VERSION
    assert provenance["chapter_outline_topics"] == 2
    assert provenance["chapter_outline_partitions"] == 1
    assert provenance["source_task_blocks"] == 1
    assert provenance["inventory_items"] == 2
    assert provenance["model_split_items"] == 2


def test_inventory_provenance_admits_a_chapter_no_model_outlined():
    from app.services import canonical_source_phase2 as phase2

    canonical = {
        "tasks": [{
            "task_id": "TASK-0001",
            "qid": "QINV-0001",
            "identity_key": "identity-1",
            "source_kind": "checkpoint_question",
            "order": 1,
            "raw_prompt": "Answer the whole exercise.",
            "display_prompt": "Answer the whole exercise.",
        }],
        "figures": [],
        "images": [],
    }

    provenance = phase2.inventory_from_canonical(canonical)["extraction_provenance"]

    assert provenance["chapter_outline_applied"] is False
    assert provenance["chapter_outline_topics"] == 0
    assert provenance["model_split_items"] == 0
    assert provenance["inventory_items"] == 1


def test_run_in_heading_punctuation_is_trimmed_from_topic_titles():
    # Balbharati prints "Excretion -" run into its own paragraph. The words
    # are the topic; the joiner is typesetting.
    candidate = _candidate()
    candidate["topics"][0]["title"] = "Growth and Development-"
    candidate["topics"][1]["title"] = "Response to Stimuli : "

    outline, _flags = fallback._normalize_chapter_outline(_page_acsd(), candidate)

    assert [t["title"] for t in outline["topics"]] == [
        "Growth and Development", "Response to Stimuli",
    ]


def test_a_title_that_is_only_punctuation_survives_the_trim():
    candidate = _candidate()
    candidate["topics"][0]["title"] = "--"

    outline, _flags = fallback._normalize_chapter_outline(_page_acsd(), candidate)

    assert outline["topics"][0]["title"] == "--"


def test_unlabelled_bullet_parts_partition_like_lettered_ones():
    # "What will happen if…" prints seven independent scenarios with no item
    # markers at all. A missing label must not cost the split.
    page_acsd = {
        "pdf_sha256": "abc123",
        "pages": [{
            "page_id": "PDF-PAGE-0001",
            "page_number": 1,
            "blocks": [
                {"reading_order": 1, "kind": "heading", "heading_level": 1,
                 "text": "Growth"},
                {"reading_order": 2, "kind": "task", "source_label": "",
                 "text": (
                     "What Will Happen, if… "
                     "There is a large beehive on a tree branch. Some children "
                     "light a fire under that tree. "
                     "A hen and her chicks are picking grains. A cat or a snake "
                     "approaches them."
                 )},
            ],
        }],
    }
    candidate = {
        "chapter_title": "Characteristics of Living Organisms",
        "topics": [{"title": "Growth", "kind": "content",
                    "start_page_id": "PDF-PAGE-0001", "start_reading_order": 1}],
        "task_partitions": [{
            "page_id": "PDF-PAGE-0001", "reading_order": 2,
            "independent_parts": [
                {"label": "", "stem": "What Will Happen, if…",
                 "text": (
                     "There is a large beehive on a tree branch. Some children "
                     "light a fire under that tree."
                 )},
                {"label": "", "stem": "What Will Happen, if…",
                 "text": (
                     "A hen and her chicks are picking grains. A cat or a snake "
                     "approaches them."
                 )},
            ],
        }],
        "notes": [],
    }

    outline, flags = fallback._normalize_chapter_outline(page_acsd, candidate)

    assert flags == []
    parts = outline["task_partitions"][0]["independent_parts"]
    assert len(parts) == 2
    assert parts[0]["label"] == ""
    assert parts[0]["stem"] == "What Will Happen, if…"


def test_task_cues_stop_minting_a_topic_under_an_outline():
    # Every task block used to emit a level-1 cue heading, so a 24-task
    # chapter derived 24 sections nobody asked for around the outline's real
    # topics. The cue — now the VERBATIM visible label — drops to a
    # sub-level heading rather than losing its heading entirely.
    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    page_acsd["chapter_outline"] = outline

    rendered = fallback.render_page_acsd_to_mmd(page_acsd)

    assert "\n# (1)" not in rendered
    assert "### (1)" in rendered
    # The retired vocabulary no longer prints a cue the book never carried.
    assert "Discuss" not in rendered
    # The outline's own topic keeps its heading weight.
    assert "# Dimensions" in rendered


def test_the_rendered_mmd_still_yields_its_tasks_when_recompiled():
    """The regression PR 208 shipped: bold cues hid every task.

    Conversion builds the canonical from the page ACSD, so it still reported
    the tasks; only generation recompiles from the MMD. Under verbatim cue
    headings the MMD parser's fixed vocabulary cannot be the safety net any
    more — the verified page ledger is: generation's load path
    (rehydrate_verified_fallback) reapplies it on every recompile and FAILS
    CLOSED when a single ledger task cannot be reconciled. Recompile the
    rendered MMD here the way generation does, ledger reapplication
    included.
    """
    from app.services import canonical_source_phase2 as phase2

    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    page_acsd["chapter_outline"] = outline
    rendered = fallback.render_page_acsd_to_mmd(page_acsd)

    canonical = phase2.compile_phase2_source(
        rendered,
        source_filename="outline.mmd",
        consumer_module="build_concepts",
    ).canonical
    fallback._attach_chapter_outline(canonical, page_acsd)
    mapped = fallback.apply_page_acsd_relationships(canonical, page_acsd)

    task_blocks = [
        block for page in page_acsd["pages"]
        for block in page["blocks"]
        if block.get("kind") == "task"
    ]
    assert task_blocks, "fixture must contain a task to be meaningful"
    # apply_page_acsd_relationships raises unless EVERY ledger task is
    # reconciled — the exact-once guarantee generation relies on.
    assert mapped == len(task_blocks)
    assert len(canonical["tasks"]) == len(task_blocks)
    assert all(
        task["source_location_confidence"] == "gpt_pdf_acsd_verified_block"
        for task in canonical["tasks"]
    )

    # And the topics are still only the outline's, not one per task cue.
    level_one = [
        row for row in canonical["sections"]
        if int(row.get("level") or 0) == 1
    ]
    assert len(level_one) <= 2


def test_task_cues_stay_headings_when_no_outline_decided_the_structure():
    rendered = fallback.render_page_acsd_to_mmd(_page_acsd())

    # The verbatim visible cue keeps a heading; no vocabulary rewrites it.
    assert "# (1)" in rendered
    assert "Discuss" not in rendered


def test_the_bold_task_cue_is_still_stripped_when_matching_tasks():
    assert fallback._task_match_key("**Discuss**\nName two solids.") == (
        fallback._task_match_key("# Discuss\nName two solids.")
    )
    assert fallback._task_match_key("**Discuss**\nName two solids.") == (
        "name two solids."
    )


def test_a_stem_printed_as_the_block_cue_is_verbatim_enough():
    # Balbharati prints "What Will Happen, if…" as the cue over seven
    # scenarios, and the transcription captures it as the block's
    # source_label rather than as part of the block text.
    page_acsd = {
        "pdf_sha256": "abc123",
        "pages": [{
            "page_id": "PDF-PAGE-0001",
            "page_number": 1,
            "blocks": [
                {"reading_order": 1, "kind": "heading", "heading_level": 1,
                 "text": "Growth"},
                {"reading_order": 3, "kind": "task",
                 "source_label": "What Will Happen, if…",
                 "text": (
                     "There is a large beehive on a tree branch. Some children "
                     "light a fire under that tree. "
                     "A small baby in a cradle feels hungry or its clothes get wet."
                 )},
            ],
        }],
    }
    candidate = {
        "chapter_title": "Characteristics of Living Organisms",
        "topics": [{"title": "Growth", "kind": "content",
                    "start_page_id": "PDF-PAGE-0001", "start_reading_order": 1}],
        "task_partitions": [{
            "page_id": "PDF-PAGE-0001", "reading_order": 3,
            "independent_parts": [
                {"label": "", "stem": "What Will Happen, if…",
                 "text": (
                     "There is a large beehive on a tree branch. Some children "
                     "light a fire under that tree."
                 )},
                {"label": "", "stem": "What Will Happen, if…",
                 "text": (
                     "A small baby in a cradle feels hungry or its clothes get wet."
                 )},
            ],
        }],
        "notes": [],
    }

    outline, flags = fallback._normalize_chapter_outline(page_acsd, candidate)

    assert flags == []
    parts = outline["task_partitions"][0]["independent_parts"]
    assert [part["stem"] for part in parts] == [
        "What Will Happen, if…", "What Will Happen, if…",
    ]


def test_a_stem_from_nowhere_in_the_source_is_still_cleared():
    page_acsd = _page_acsd()
    candidate = _candidate()
    candidate["task_partitions"][0]["independent_parts"][0]["stem"] = (
        "Answer in your own words."
    )

    outline, flags = fallback._normalize_chapter_outline(page_acsd, candidate)

    assert any("not verbatim" in flag for flag in flags)
    assert outline["task_partitions"][0]["independent_parts"][0]["stem"] == ""


def _exercises_page() -> dict:
    """The real Balbharati layout: an "Exercises" heading above the task."""
    return {
        "pdf_sha256": "abc123",
        "pages": [{
            "page_id": "PDF-PAGE-0008",
            "page_number": 8,
            "blocks": [
                {"reading_order": 5, "kind": "paragraph",
                 "text": "Growth and reproduction are the main characteristics."},
                {"reading_order": 6, "kind": "heading", "heading_level": 1,
                 "text": "Exercises"},
                {"reading_order": 7, "kind": "task",
                 "source_label": "(1) Suggest the appropriate word for the blanks.",
                 "text": (
                     "(a) Every living organism produces another living organism "
                     "like itself is called as ▯. "
                     "(b) ▯ and ▯ are the main characteristics of living organisms. "
                     "(c) The body of living organism is made up of ▯."
                 )},
            ],
        }],
    }


def _exercises_candidate(reading_order: int) -> dict:
    return {
        "chapter_title": "Characteristics of Living Organisms",
        "topics": [{"title": "Exercises", "kind": "content",
                    "start_page_id": "PDF-PAGE-0008", "start_reading_order": 6}],
        "task_partitions": [{
            "page_id": "PDF-PAGE-0008", "reading_order": reading_order,
            "independent_parts": [
                {"label": "(a)", "stem": "",
                 "text": ("Every living organism produces another living organism "
                          "like itself is called as ▯.")},
                {"label": "(b)", "stem": "",
                 "text": "▯ and ▯ are the main characteristics of living organisms."},
                {"label": "(c)", "stem": "",
                 "text": "The body of living organism is made up of ▯."},
            ],
        }],
        "notes": [],
    }


def test_a_partition_aimed_at_the_heading_is_re_aimed_at_the_task():
    # The observed failure: the outline pointed at "Exercises" (#6) instead of
    # the fill-in block (#7), and three questions were dropped with it.
    outline, flags = fallback._normalize_chapter_outline(
        _exercises_page(), _exercises_candidate(reading_order=6)
    )

    assert len(outline["task_partitions"]) == 1
    partition = outline["task_partitions"][0]
    assert partition["reading_order"] == 7
    assert len(partition["independent_parts"]) == 3
    assert any("re-aimed" in flag for flag in flags)


def test_a_correctly_aimed_partition_is_left_alone():
    outline, flags = fallback._normalize_chapter_outline(
        _exercises_page(), _exercises_candidate(reading_order=7)
    )

    assert outline["task_partitions"][0]["reading_order"] == 7
    assert not [flag for flag in flags if "re-aimed" in flag]


def test_a_partition_whose_parts_are_in_no_block_is_still_dropped():
    candidate = _exercises_candidate(reading_order=6)
    for part in candidate["task_partitions"][0]["independent_parts"]:
        part["text"] = "Wording that appears nowhere in this chapter at all."
    candidate["task_partitions"][0]["independent_parts"][1]["text"] = (
        "Another invented sentence with no source behind it whatsoever."
    )

    outline, flags = fallback._normalize_chapter_outline(
        _exercises_page(), candidate
    )

    assert outline is None or outline["task_partitions"] == []
    assert any("no task block holds the parts" in flag for flag in flags)


def test_two_partitions_cannot_claim_the_same_task():
    page_acsd = _exercises_page()
    candidate = _exercises_candidate(reading_order=6)
    # A second partition, also mis-aimed, resolving onto the same block.
    candidate["task_partitions"].append(
        copy.deepcopy(candidate["task_partitions"][0])
    )
    candidate["task_partitions"][1]["reading_order"] = 5

    outline, flags = fallback._normalize_chapter_outline(page_acsd, candidate)

    assert len(outline["task_partitions"]) == 1
    assert any("already partitioned" in flag for flag in flags)


def test_re_aiming_prefers_the_nearest_qualifying_block():
    page_acsd = _exercises_page()
    # A far-away duplicate of the same task; the near one must win.
    page_acsd["pages"].append({
        "page_id": "PDF-PAGE-0002",
        "page_number": 2,
        "blocks": [dict(page_acsd["pages"][0]["blocks"][2], reading_order=3)],
    })

    outline, _flags = fallback._normalize_chapter_outline(
        page_acsd, _exercises_candidate(reading_order=6)
    )

    partition = outline["task_partitions"][0]
    assert partition["page_id"] == "PDF-PAGE-0008"
    assert partition["reading_order"] == 7


def _two_task_pages() -> dict:
    """Two task blocks: one the model rules on, one it can forget."""
    return {
        "pdf_sha256": "abc123",
        "pages": [{
            "page_id": "PDF-PAGE-0001",
            "page_number": 1,
            "blocks": [
                {"reading_order": 1, "kind": "heading", "heading_level": 1,
                 "text": "Shapes"},
                {"reading_order": 2, "kind": "task", "source_label": "",
                 "text": "Name one solid with a curved surface."},
                {"reading_order": 3, "kind": "task", "source_label": "",
                 "text": (
                     "(a) How many faces does a cube have? "
                     "(b) How many edges does a cube have?"
                 )},
            ],
        }],
    }


def _two_task_candidate(**overrides) -> dict:
    candidate = {
        "chapter_title": "Shapes",
        "topics": [{"title": "Shapes", "kind": "content",
                    "start_page_id": "PDF-PAGE-0001", "start_reading_order": 1}],
        "task_partitions": [],
        "whole_tasks": [],
        "notes": [],
    }
    candidate.update(overrides)
    return candidate


def test_a_task_ruled_in_neither_list_is_named_as_unruled():
    # The silent failure: a task the model never mentions looks exactly like
    # one it judged whole, and its questions vanish without a flag.
    outline, _flags = fallback._normalize_chapter_outline(
        _two_task_pages(),
        _two_task_candidate(whole_tasks=[
            {"page_id": "PDF-PAGE-0001", "reading_order": 2},
        ]),
    )

    assert outline["unruled_task_refs"] == [["PDF-PAGE-0001", 3]]


def test_ruling_on_every_task_leaves_nothing_unruled():
    outline, _flags = fallback._normalize_chapter_outline(
        _two_task_pages(),
        _two_task_candidate(whole_tasks=[
            {"page_id": "PDF-PAGE-0001", "reading_order": 2},
            {"page_id": "PDF-PAGE-0001", "reading_order": 3},
        ]),
    )

    assert outline["unruled_task_refs"] == []


def test_a_partitioned_task_counts_as_ruled():
    outline, _flags = fallback._normalize_chapter_outline(
        _two_task_pages(),
        _two_task_candidate(
            whole_tasks=[{"page_id": "PDF-PAGE-0001", "reading_order": 2}],
            task_partitions=[{
                "page_id": "PDF-PAGE-0001", "reading_order": 3,
                "independent_parts": [
                    {"label": "(a)", "stem": "",
                     "text": "How many faces does a cube have?"},
                    {"label": "(b)", "stem": "",
                     "text": "How many edges does a cube have?"},
                ],
            }],
        ),
    )

    assert outline["unruled_task_refs"] == []
    assert len(outline["task_partitions"]) == 1


def test_a_whole_task_ruling_on_a_non_task_is_ignored_and_flagged():
    outline, flags = fallback._normalize_chapter_outline(
        _two_task_pages(),
        _two_task_candidate(whole_tasks=[
            {"page_id": "PDF-PAGE-0001", "reading_order": 1},
        ]),
    )

    assert any("is not a task block" in flag for flag in flags)
    assert sorted(outline["unruled_task_refs"]) == [
        ["PDF-PAGE-0001", 2], ["PDF-PAGE-0001", 3],
    ]


def test_omitted_tasks_go_back_to_the_model_and_their_splits_are_recovered(
    monkeypatch, tmp_path,
):
    from app.services import canonical_source_phase22 as phase22

    calls: list[str] = []

    def _respond(*, prompt, **_kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            # First reading forgets the multi-part task entirely.
            return _two_task_candidate(whole_tasks=[
                {"page_id": "PDF-PAGE-0001", "reading_order": 2},
            ])
        # The follow-up sees only the forgotten block and splits it.
        return _two_task_candidate(task_partitions=[{
            "page_id": "PDF-PAGE-0001", "reading_order": 3,
            "independent_parts": [
                {"label": "(a)", "stem": "",
                 "text": "How many faces does a cube have?"},
                {"label": "(b)", "stem": "",
                 "text": "How many edges does a cube have?"},
            ],
        }])

    monkeypatch.setattr(phase22, "_openai_multimodal_json", _respond)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    outline = fallback.derive_chapter_outline(_two_task_pages())

    assert len(calls) == 2
    # The follow-up prompt is scoped to the forgotten block only.
    assert "How many faces does a cube have?" in calls[1]
    assert "Name one solid with a curved surface." not in calls[1]
    assert outline["unruled_task_refs"] == []
    assert len(outline["task_partitions"]) == 1
    assert len(outline["task_partitions"][0]["independent_parts"]) == 2


def test_a_complete_first_reading_never_triggers_a_follow_up(
    monkeypatch, tmp_path,
):
    from app.services import canonical_source_phase22 as phase22

    calls = {"n": 0}

    def _respond(**_kwargs):
        calls["n"] += 1
        return _two_task_candidate(whole_tasks=[
            {"page_id": "PDF-PAGE-0001", "reading_order": 2},
            {"page_id": "PDF-PAGE-0001", "reading_order": 3},
        ])

    monkeypatch.setattr(phase22, "_openai_multimodal_json", _respond)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    outline = fallback.derive_chapter_outline(_two_task_pages())

    assert calls["n"] == 1
    assert outline["unruled_task_refs"] == []


def test_a_failed_follow_up_leaves_the_first_reading_standing(
    monkeypatch, tmp_path,
):
    from app.services import canonical_source_phase22 as phase22

    calls = {"n": 0}

    def _respond(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _two_task_candidate(whole_tasks=[
                {"page_id": "PDF-PAGE-0001", "reading_order": 2},
            ])
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(phase22, "_openai_multimodal_json", _respond)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    outline = fallback.derive_chapter_outline(_two_task_pages())

    assert calls["n"] == 2
    assert outline is not None
    assert outline["chapter_title"] == "Shapes"
    # Still reported, so the release can say the chapter was not read whole.
    assert outline["unruled_task_refs"] == [["PDF-PAGE-0001", 3]]


def test_the_prompt_requires_a_ruling_on_every_task_block():
    system = fallback._outline_system_prompt()

    assert "whole_tasks" in system
    assert "exactly once" in system


def test_the_rendered_mmd_stamps_the_reader_that_produced_it():
    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    page_acsd["chapter_outline"] = outline

    rendered = fallback.render_page_acsd_to_mmd(page_acsd)

    assert f"source_reader: {fallback.source_reader_version()}" in rendered
    assert fallback.mmd_reader_version(rendered) == (
        fallback.source_reader_version()
    )


def test_the_reader_version_covers_compiler_and_outline():
    # Both decide the SHAPE of the MMD, so a change to either has to
    # invalidate an already-converted source.
    version = fallback.source_reader_version()

    assert fallback.FALLBACK_COMPILER in version
    assert fallback.OUTLINE_VERSION in version


def test_a_current_mmd_is_not_stale():
    rendered = fallback.render_page_acsd_to_mmd(_page_acsd())

    assert fallback.stale_mmd_reader(rendered) == ""


def test_an_unstamped_gpt_mmd_is_stale():
    # Every MMD converted before this stamp existed reads the old structure.
    legacy = (
        "<!-- source_origin: gpt-pdf-to-acsd -->\n"
        "<!-- compiler_version: gpt-pdf-to-acsd-2 -->\n\n# Dimensions\n"
    )

    assert "pre-dates" in fallback.stale_mmd_reader(legacy)


def test_an_mmd_from_a_superseded_reader_is_stale():
    superseded = (
        "<!-- source_origin: gpt-pdf-to-acsd -->\n"
        "<!-- source_reader: gpt-pdf-to-acsd-2+chapter-outline-1 -->\n"
    )

    assert fallback.stale_mmd_reader(superseded) == (
        "gpt-pdf-to-acsd-2+chapter-outline-1"
    )


def test_a_source_we_did_not_render_is_never_called_stale():
    # It carries no stamp of ours, so our versioning says nothing about it.
    assert fallback.stale_mmd_reader("# Chapter\n\nUploaded MMD.\n") == ""
    assert fallback.stale_mmd_reader("") == ""


def test_generation_refuses_a_source_read_by_a_superseded_reader():
    from app.services import canonical_source_phase2 as phase2

    class _Job:
        id = 1
        mmd_text = (
            "<!-- source_origin: gpt-pdf-to-acsd -->\n"
            "<!-- source_reader: gpt-pdf-to-acsd-2+chapter-outline-1 -->\n"
        )

    try:
        phase2.prepare_job_context(None, _Job())
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("a superseded source must not reach generation")

    assert "superseded chapter reader" in message
    assert "chapter-outline-1" in message
    # The message has to say what to actually do about it.
    assert "new upload" in message


def test_a_current_source_passes_the_reader_gate():
    from app.services import canonical_source_phase2 as phase2

    class _Job:
        id = 1
        mmd_text = (
            "<!-- source_origin: gpt-pdf-to-acsd -->\n"
            f"<!-- source_reader: {fallback.source_reader_version()} -->\n"
        )

    # It gets past the reader gate and fails later, on real artifacts.
    try:
        phase2.prepare_job_context(None, _Job())
    except Exception as exc:
        assert "superseded chapter reader" not in str(exc)


def test_the_reader_stamp_never_reaches_the_semantic_source():
    # It is machine metadata like source_origin and compiler_version; leaking
    # it would put a version string in front of the concept model.
    from app.services import canonical_source_phase3 as phase3

    assert "source_reader" in phase3._MACHINE_METADATA_KEYS


def _converter_style_mmd() -> str:
    """A converter MMD: images as links, no task cues, run-in topic leads."""
    return (
        "# 1 Three-Dimensional Shapes\n\n"
        "![](https://cdn.example.com/cropped/abc-1.jpg?width=379)\n\n"
        "# Understand\n\n"
        "Dimensions A point has no dimension at all.\n\n"
        "![](https://cdn.example.com/cropped/abc-1.jpg?width=500)\n\n"
        "(1) Select the correct option. "
        "(i) What shape will be formed if carrom pieces are "
        "placed one on top of the other? A) Cuboid B) Cylinder "
        "C) Cone D) Cube "
        "(ii) Which of the following shapes is not "
        "three-dimensional? A) square B) Sphere C) Cylinder D) Cone\n"
    )


def test_outline_transfer_never_touches_the_converter_images():
    """The whole point: the converter's cropped URLs stay exactly where they are."""
    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    source = _converter_style_mmd()

    out, _flags2 = fallback.transfer_outline_to_mmd(source, outline, page_acsd)

    assert out.count("cdn.example") == source.count("cdn.example")
    for url in ("abc-1.jpg?width=379", "abc-1.jpg?width=500"):
        assert url in out


def test_outline_transfer_gives_a_converter_source_its_task_cues():
    # Without a cue the deterministic parser finds no task at all; that is
    # how such a chapter arrives with four questions instead of thirty.
    from app.services import canonical_source_phase2 as phase2

    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    source = _converter_style_mmd()

    before = phase2.compile_phase2_source(
        source, source_filename="m.mmd", consumer_module="build_concepts",
    ).canonical
    out, _flags2 = fallback.transfer_outline_to_mmd(source, outline, page_acsd)
    after = phase2.compile_phase2_source(
        out, source_filename="m.mmd", consumer_module="build_concepts",
    ).canonical

    assert len(after["tasks"]) > len(before["tasks"])


def test_outline_transfer_is_a_no_op_without_an_outline():
    source = _converter_style_mmd()

    assert fallback.transfer_outline_to_mmd(source, None) == (source, [])
    assert fallback.transfer_outline_to_mmd(source, {}) == (source, [])


def test_outline_transfer_flags_what_it_could_not_locate():
    page_acsd = _page_acsd()
    candidate = _candidate()
    candidate["topics"][0]["title"] = "A Topic This Source Never Prints"
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, candidate)

    _out, flags = fallback.transfer_outline_to_mmd(
        _converter_style_mmd(), outline, page_acsd)

    assert any("not found verbatim" in flag for flag in flags)


def test_the_gpt_reader_is_the_only_conversion_path():
    """There is no switch left to put another converter in front.

    Only this reader applies the chapter outline, which is what gives a
    chapter its topics and its questions, so a second converter could not be
    reinstated without losing them.
    """
    assert not hasattr(fallback, "_forced")
    assert not hasattr(fallback, "assess_mathpix_quality")
