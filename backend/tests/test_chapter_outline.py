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


def test_task_cues_stop_minting_a_section_each_under_an_outline():
    # Every task block used to emit "# Discuss", so a 24-task chapter derived
    # 24 sections nobody asked for around the outline's real topics.
    page_acsd = _page_acsd()
    outline, _flags = fallback._normalize_chapter_outline(page_acsd, _candidate())
    page_acsd["chapter_outline"] = outline

    rendered = fallback.render_page_acsd_to_mmd(page_acsd)

    assert "# Discuss" not in rendered
    assert "**Discuss**" in rendered
    # The outline's own topic keeps its heading weight.
    assert "# Dimensions" in rendered


def test_task_cues_stay_headings_when_no_outline_decided_the_structure():
    rendered = fallback.render_page_acsd_to_mmd(_page_acsd())

    assert "# Discuss" in rendered


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
