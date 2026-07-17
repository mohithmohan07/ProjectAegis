"""Universal quality contracts distilled from the three reviewed chapters."""
from __future__ import annotations

import pytest

from app.services import concept_refiner as cr
from app.services import concept_validator as cv
from app.services import generation as g
from app.services import katex_rules as kr


def _inventory(*items: dict) -> dict:
    return {"items": list(items), "stats": {"total_inventory_items": len(items)}}


def _item(qid: str, task: str, *, topic: str = "Methods", **extra) -> dict:
    return {
        "qid": qid,
        "source_kind": "exercise",
        "source_label": f"Question {qid[-1]}",
        "parent_source_label": "Exercises",
        "topic_hint": topic,
        "raw_task": task,
        "normalized_task": task,
        "image_urls": [],
        **extra,
    }


def _type(
    type_id: str,
    qid: str,
    task: str,
    *,
    title: str,
    topic: str = "Methods",
) -> dict:
    return {
        "type_id": type_id,
        "type_title": title,
        "type_description": f"Learners use the {title.lower()} method.",
        "task_pattern": title,
        "source_question_ids": [qid],
        "case_prompts": [{
            "case_id": f"CASE-{qid[-1]}",
            "case_title": title,
            "examples": [{
                "source_question_id": qid,
                "example_prompt": task,
            }],
            "placement_scope": "normal",
        }],
        "concept_match_hint": "Target Method",
        "parent_concept_match_hint": "Methods",
        "topic_match_hint": topic,
        "is_activity": False,
        "placement_scope": "normal",
    }


def test_rich_text_emits_uppercase_katex_and_canonical_images():
    raw = (
        "Use $\\frac{a}{b}=\\alpha$ and "
        "![ratio diagram](https://images.example/ratio.png). "
        "Legacy [katex] x^2 [/katex]."
    )
    rendered = kr.canonicalize_rich_text(raw)
    assert "[Katex] \\frac{a}{b}=\\alpha [/Katex]" in rendered
    assert "[Katex] x^2 [/Katex]" in rendered
    assert (
        '[img src="https://images.example/ratio.png" alt="ratio diagram"]'
        in rendered
    )
    assert not kr.rich_text_issues(rendered)


def test_rich_text_rejects_malformed_tags_without_treating_currency_as_math():
    currency = "The price rose from $5 to $10 in one week."
    assert kr.canonicalize_rich_text(currency) == currency
    assert "raw_math_delimiter" not in kr.rich_text_issues(currency)
    assert kr.canonicalize_rich_text("$5 x 10$") == (
        "[Katex] 5 x 10 [/Katex]")

    defects = kr.rich_text_issues(
        '[KATEX] x [/KATEX] '
        '[Katex] outer [Katex] inner [/Katex] [/Katex] '
        '[img src="https://images.example/x.png" onerror="alert(1)" alt="x"] '
        '[img src="https://images.example/y.png" alt="y" '
        r"\alpha + x^2 + $unclosed"
    )
    assert "noncanonical_katex_case" in defects
    assert "nested_katex" in defects
    assert "noncanonical_image" in defects
    assert "unbalanced_image" in defects
    assert "raw_latex" in defects
    assert "raw_math_delimiter" in defects
    with pytest.raises(ValueError):
        kr.image('https://images.example/x.png" onerror="alert(1)', "x")
    with pytest.raises(ValueError):
        kr.image("http://images.example/x.png", "x")
    with pytest.raises(ValueError):
        kr.image("https://images.example/x.png", "x", width="200")


def test_markdown_images_handle_titles_parentheses_and_reject_relative_urls():
    rendered = kr.canonicalize_rich_text(
        '![graph](https://images.example/x_(1).png "source graph")')
    assert rendered == (
        '[img src="https://images.example/x_(1).png" alt="graph"]')
    relative = "![graph](../images/x.png)"
    assert kr.canonicalize_rich_text(relative) == relative
    assert "markdown_image" in kr.rich_text_issues(relative)
    assert "invalid_image_src" in kr.rich_text_issues(
        '[img src="http://images.example/x.png" alt="graph"]')


@pytest.mark.parametrize("expression", ["F = ma", "2 + 3 = 5", "a/b = c/d"])
def test_plain_ascii_equations_require_katex(expression):
    assert "raw_math_expression" in kr.rich_text_issues(
        f"Description: Apply {expression} to solve the problem.")
    assert not kr.rich_text_issues(
        f"Description: Apply [Katex] {expression} [/Katex] to solve the problem.")


def test_rich_text_registry_uses_student_facing_display_answer():
    assert "display_answer" in kr.RICH_TEXT_FIELDS
    assert "answer_display" not in kr.RICH_TEXT_FIELDS


def test_literal_trailing_newline_escape_is_normalized_outside_katex():
    rendered = kr.canonicalize_rich_text(
        r"Description: Explain the pattern.\n")
    assert rendered == "Description: Explain the pattern.\n"
    assert not kr.rich_text_issues(rendered)


def test_inventory_examples_strip_headings_and_emit_canonical_media():
    item = _item(
        "QINV-0001",
        "## Activity\nFind $x$ from the diagram. "
        "![triangle](https://images.example/triangle.png)",
        requires_visual=True,
        image_urls=["https://images.example/triangle.png"],
    )
    rendered = g._inventory_task_text(item)
    assert "## Activity" not in rendered
    assert "[Katex] x [/Katex]" in rendered
    assert '[img src="https://images.example/triangle.png"' in rendered
    assert "![" not in rendered


def test_semantic_type_consolidation_merges_paraphrases_exactly_once(monkeypatch):
    first = _item("QINV-0001", "Determine the output using the stated rule.")
    second = _item("QINV-0002", "Find the output by applying the given rule.")
    inventory = _inventory(first, second)
    original = {
        "types": [
            _type(
                "TYPE-0001", first["qid"], first["raw_task"],
                title="Determining an Output from a Rule",
            ),
            _type(
                "TYPE-0002", second["qid"], second["raw_task"],
                title="Finding an Output by Applying a Rule",
            ),
        ],
    }
    merged = _type(
        "TYPE-0001",
        first["qid"],
        first["raw_task"],
        title="Determining an Output by Applying a Rule",
    )
    merged["source_question_ids"].append(second["qid"])
    merged["case_prompts"].append({
        "case_id": "CASE-0002",
        "case_title": "Rule supplied in symbolic form",
        "examples": [{
            "source_question_id": second["qid"],
            "example_prompt": second["raw_task"],
        }],
        "placement_scope": "normal",
    })
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {"types": [merged]})

    result = g._consolidate_semantic_types_via_api(
        original, inventory=inventory, meta={})
    assert len(result["types"]) == 1
    assert not g._uncovered_inventory_items(inventory, result["types"])
    assert not g._duplicate_inventory_assignments(inventory, result["types"])


def test_semantic_type_consolidation_rejects_topic_drift(monkeypatch):
    first = _item("QINV-0001", "Determine the first output.", topic="Topic A")
    second = _item("QINV-0002", "Determine the second output.", topic="Topic B")
    inventory = _inventory(first, second)
    original = {
        "types": [
            _type(
                "TYPE-0001", first["qid"], first["raw_task"],
                title="Determining an Output", topic="Topic A",
            ),
            _type(
                "TYPE-0002", second["qid"], second["raw_task"],
                title="Determining an Output", topic="Topic B",
            ),
        ],
    }
    invalid = _type(
        "TYPE-0001", first["qid"], first["raw_task"],
        title="Determining an Output", topic="Topic A",
    )
    invalid["source_question_ids"].append(second["qid"])
    invalid["case_prompts"].append(original["types"][1]["case_prompts"][0])
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {"types": [invalid]})

    result = g._consolidate_semantic_types_via_api(
        original, inventory=inventory, meta={})
    assert len(result["types"]) == 2


def test_semantic_consolidation_restores_qid_bearing_example_text(monkeypatch):
    first = _item("QINV-0001", "Determine the first output from the full rule.")
    second = _item(
        "QINV-0002",
        "Determine the second output using every condition in the stated rule.",
    )
    inventory = _inventory(first, second)
    original = {
        "types": [
            _type(
                "TYPE-0001", first["qid"], first["raw_task"],
                title="Determining an Output",
            ),
            _type(
                "TYPE-0002", second["qid"], second["raw_task"],
                title="Finding an Output",
            ),
        ],
    }
    candidate = _type(
        "TYPE-0001", first["qid"], first["raw_task"],
        title="Determining an Output",
    )
    candidate["case_prompts"].append({
        "case_id": "CASE-0002",
        "case_title": "Conditional rule",
        "examples": [{
            "source_question_id": second["qid"],
            "example_prompt": "Find the second output.",
        }],
        "placement_scope": "normal",
    })
    monkeypatch.setattr(
        g, "_openai_json", lambda *_a, **_k: {"types": [candidate]})

    result = g._consolidate_semantic_types_via_api(
        original, inventory=inventory, meta={})
    merged = result["types"][0]
    restored = merged["case_prompts"][1]["examples"][0]
    assert restored["example_prompt"] == g._inventory_task_text(second)
    assert second["qid"] in merged["source_question_ids"]


def test_concept_sufficiency_adds_only_a_supported_same_topic_method(monkeypatch):
    records = [{
        "topic": "Methods",
        "parent_concept": "Core",
        "concept_title": "Applying a Direct Rule",
        "concept_details": (
            "Description: Substitute a supplied input directly into the rule "
            "to calculate its output."
        ),
        "keywords": "rule",
    }]
    mined = {
        "types": [_type(
            "TYPE-0001",
            "QINV-0001",
            "Recover an input by reversing every operation in the rule.",
            title="Recovering an Input by Reversing a Rule",
        )],
    }
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {
        "additions": [{
            "after_concept_id": "CONCEPT-0001",
            "topic": "Methods",
            "parent_concept": "Core",
            "concept": "Reversing a Rule to Recover Its Input",
            "concept_description": (
                "Description: Start from the known output and undo each "
                "operation in reverse order while preserving equality."
            ),
            "keywords": "inverse operations",
            "supporting_type_ids": ["TYPE-0001"],
        }],
    })
    result = g._add_missing_type_method_concepts_via_api(
        records, mined_types=mined, meta={})
    assert [row["concept_title"] for row in result] == [
        "Applying a Direct Rule",
        "Reversing a Rule to Recover Its Input",
    ]


def test_concept_sufficiency_does_not_cap_distinct_methods_by_row_count(
    monkeypatch,
):
    records = [{
        "topic": "Methods",
        "parent_concept": "Core",
        "concept_title": "Applying a Direct Rule",
        "concept_details": "Description: Substitute the supplied input.",
        "keywords": "rule",
    }]
    first = _type(
        "TYPE-0001", "QINV-0001", "Reverse the rule.",
        title="Reversing a Rule",
    )
    second = _type(
        "TYPE-0002", "QINV-0002", "Compare two rules.",
        title="Comparing Two Rules",
    )
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {
        "additions": [
            {
                "after_concept_id": "CONCEPT-0001",
                "topic": "Methods",
                "parent_concept": "Core",
                "concept": "Reversing a Rule",
                "concept_description": (
                    "Description: Undo each operation in reverse order."
                ),
                "keywords": "inverse",
                "supporting_type_ids": ["TYPE-0001"],
            },
            {
                "after_concept_id": "CONCEPT-0001",
                "topic": "Methods",
                "parent_concept": "Core",
                "concept": "Comparing Two Rules",
                "concept_description": (
                    "Description: Apply both rules to a shared input and "
                    "compare their outputs."
                ),
                "keywords": "comparison",
                "supporting_type_ids": ["TYPE-0002"],
            },
        ],
    })
    result = g._add_missing_type_method_concepts_via_api(
        records, mined_types={"types": [first, second]}, meta={})
    assert {row["concept_title"] for row in result} == {
        "Applying a Direct Rule",
        "Reversing a Rule",
        "Comparing Two Rules",
    }


def test_new_derivation_concept_receives_a_mined_type_worked_cue(monkeypatch):
    records = [{
        "topic": "Formula Building",
        "parent_concept": "Core",
        "concept_title": "Recognising a Pattern",
        "concept_details": "Description: Identify the repeated change.",
        "keywords": "pattern",
    }]
    derivation_type = _type(
        "TYPE-0001",
        "QINV-0001",
        "Derive the general term by writing successive terms.",
        title="Deriving the General-Term Rule",
        topic="Formula Building",
    )
    derivation_type["type_description"] = (
        "Write successive terms, isolate the repeated change, and generalise."
    )
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {
        "additions": [{
            "after_concept_id": "CONCEPT-0001",
            "topic": "Formula Building",
            "parent_concept": "Derivations",
            "concept": "Deriving the General-Term Rule",
            "concept_description": (
                "Description: Express each term through its repeated change "
                "and generalise the position."
            ),
            "keywords": "derivation",
            "supporting_type_ids": ["TYPE-0001"],
        }],
    })
    result = g._add_missing_type_method_concepts_via_api(
        records, mined_types={"types": [derivation_type]}, meta={})
    added = next(
        row for row in result
        if row["concept_title"] == "Deriving the General-Term Rule")
    assert "Worked Example:" in added["concept_details"]
    assert "writing successive terms" in added["concept_details"]


def test_host_entailment_review_moves_case_to_supported_sibling(monkeypatch):
    unit = _type(
        "TYPE-0001",
        "QINV-0001",
        "Recover the input by reversing the supplied rule.",
        title="Recovering an Input by Reversing a Rule",
    )
    concepts = [
        {
            "concept_id": "CONCEPT-0001",
            "topic": "Methods",
            "concept": "Applying a Direct Rule",
            "concept_description": "Substitute an input to find an output.",
            "is_culmination": False,
        },
        {
            "concept_id": "CONCEPT-0002",
            "topic": "Methods",
            "concept": "Reversing a Rule",
            "concept_description": "Undo operations to recover an input.",
            "is_culmination": False,
        },
    ]
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {
        "assignments": [{
            "type_id": "TYPE-0001",
            "concept_id": "CONCEPT-0002",
            "reason": "The second Description teaches reversal.",
        }],
    })
    result = g._review_case_unit_hosts_via_api(
        assignment_units=[unit],
        per_concept={"CONCEPT-0001": [unit]},
        concept_payload=concepts,
        allowed_cids_by_tid={
            "TYPE-0001": {"CONCEPT-0001", "CONCEPT-0002"},
        },
        meta={},
    )
    assert not result.get("CONCEPT-0001")
    assert result["CONCEPT-0002"][0]["type_id"] == "TYPE-0001"


def test_derivation_concept_receives_a_relevant_worked_example(monkeypatch):
    anchor_id = "METHOD-A1B2C3D4E5"
    record = {
        "topic": "Formula Building",
        "parent_concept": "Derivations",
        "concept_title": "Deriving a General Rule",
        "concept_details": (
            "Description: Repeated changes reveal the general symbolic rule.\n"
            "Achieving Mastery: Deriving the rule from its repeated structure."
        ),
        "keywords": "derivation",
        "source_evidence": anchor_id,
    }
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {
        "rows": [{
            "topic": record["topic"],
            "parent_concept": record["parent_concept"],
            "concept": record["concept_title"],
            "concept_description": (
                "Description: Repeated changes reveal the general rule. "
                "Worked Example: Write successive terms and factor their "
                "shared change to obtain [Katex] u_n=u_1+(n-1)d [/Katex].\n"
                "Achieving Mastery: Deriving the rule from repeated structure."
            ),
            "keywords": "derivation",
        }],
    })
    result = g._ensure_method_worked_examples_via_api(
        [record],
        anchors=[{
            "anchor_id": anchor_id,
            "topic_hint": "Formula Building",
            "source_evidence": "Write successive terms and generalise the change.",
            "required_formulas": [r"u_n=u_1+(n-1)d"],
        }],
        meta={},
    )
    description = g._concept_description_only(result[0]["concept_details"])
    assert "Worked Example:" in description
    assert "[Katex]" in description
    assert description.index("Worked Example:") < description.index(
        "Achieving Mastery:")


def test_incidental_historical_proof_language_is_not_a_method_anchor():
    sections = g.parse_mmd_sections(
        "## National Claims\n"
        "The communities used history to prove that they had once been "
        "independent. A critic asked whether any further proof was required."
    )
    assert not g._method_coverage_anchors(sections)


@pytest.mark.parametrize(
    "source",
    [
        "## Proof of the Angle-Bisector Theorem\n"
        "Construct the bisector and compare the resulting triangles.",
        "## Angle-Bisector Theorem\n"
        "Prove that the theorem follows by comparing the two triangles.",
    ],
)
def test_formula_free_formal_proofs_remain_method_anchors(source):
    assert len(g._method_coverage_anchors(g.parse_mmd_sections(source))) == 1


def test_reusable_type_keeps_one_number_and_continues_cases_across_concepts():
    records = [
        {
            "topic": "Methods",
            "concept_title": "Direct Context",
            "concept_details": (
                "Description: d // Types: Type 01: Interpreting a Supplied Rule "
                "Case 01: Symbolic input Example: Find the output. // "
                "Misconceptions: Students may confuse input and output."
            ),
        },
        {
            "topic": "Methods",
            "concept_title": "Contextual Use",
            "concept_details": (
                "Description: d // Types: Type 01: Interpreting a Supplied Rule "
                "Case 01: Verbal input Example: Explain the output. "
                "Type 02: Reversing a Supplied Rule Case 01: Known output // "
                "Misconceptions: Students may reverse operations incorrectly."
            ),
        },
    ]
    result = cr.renumber_types_continuously(records)
    assert "Type 01: Interpreting a Supplied Rule Case 01:" in (
        result[0]["concept_details"])
    assert "Type 01: Interpreting a Supplied Rule Case 02:" in (
        result[1]["concept_details"])
    assert "Type 02: Reversing a Supplied Rule Case 01:" in (
        result[1]["concept_details"])


def test_reusable_type_identity_preserves_mathematical_operators():
    records = [
        {
            "topic": "Methods",
            "concept_title": "Division",
            "concept_details": (
                "Description: d // Types: Type 01: Evaluate "
                "[Katex] a/b [/Katex] Case 01: Divide. // "
                "Misconceptions: Students may invert the quotient."
            ),
        },
        {
            "topic": "Methods",
            "concept_title": "Subtraction",
            "concept_details": (
                "Description: d // Types: Type 01: Evaluate "
                "[Katex] a-b [/Katex] Case 01: Subtract. // "
                "Misconceptions: Students may reverse the terms."
            ),
        },
    ]
    result = cr.renumber_types_continuously(records)
    assert "Type 01: Evaluate [Katex] a/b [/Katex]" in (
        result[0]["concept_details"])
    assert "Type 02: Evaluate [Katex] a-b [/Katex]" in (
        result[1]["concept_details"])


def test_culmination_title_uses_only_its_topic_and_has_no_synthetic_type():
    normal = {
        "topic": "Opening Patterns",
        "parent_concept": "Patterns",
        "concept_title": "Recognising Fixed Changes",
        "concept_details": "Description: Identify a constant change.",
        "keywords": "",
    }
    authored = {
        "topic": "Opening Patterns",
        "parent_concept": "Culmination",
        "concept_title": "Culmination - Later Definition and Testing",
        "concept_details": (
            "Description: Recap // Types: Type 01: Synthetic Mixed Task "
            "Case 01: Invent a task."
        ),
        "keywords": "",
    }
    result = g._merge_culmination_rows([normal], [authored])
    culmination = result[-1]
    assert culmination["concept_title"] == (
        "Culmination - Recognising Fixed Changes")
    assert "Types:" not in culmination["concept_details"]


def test_parent_question_with_independent_looking_subparts_remains_atomic():
    sections = g.parse_mmd_sections(
        "## 1 Main Method\nTeaching prose.\n\n"
        "## EXERCISES\n"
        "1. Write a short note on each:\n"
        "(a) The first application\n"
        "(b) The second application\n"
    )
    anchors = g._source_task_anchors(sections)
    exercise = [
        item for item in anchors if item.get("source_kind") == "exercise"
    ]
    assert len(exercise) == 1
    assert "(a)" in exercise[0]["raw_task"]
    assert "(b)" in exercise[0]["raw_task"]


def test_parent_anchor_removes_model_children_without_subpart_metadata():
    anchor = _item(
        "QINV-0001",
        "Write a note on each: (a) first application; (b) second application.",
        source_label="Question 1",
    )
    children = [
        _item(
            "MODEL-0001", "Write a note on the first application.",
            source_label="Q1(a)",
        ),
        _item(
            "MODEL-0002", "Write a note on the second application.",
            source_label="Q1(b)",
        ),
    ]
    merged = g._merge_source_task_anchors(children, [anchor])
    assert len(merged) == 1
    assert merged[0]["source_label"] == "Question 1"
    assert "(a)" in merged[0]["raw_task"]
    assert "(b)" in merged[0]["raw_task"]


def test_activity_hub_note_is_concise_and_heading_free():
    long_task = (
        "## Activity\nObserve the setup and record what changes. "
        + "Repeat the full procedure carefully with every supplied material. " * 30
    )
    item = _item(
        "QINV-0001",
        long_task,
        source_kind="activity",
        source_label="Activity 1",
    )
    note = g._compact_activity_hub_note(item)
    assert "## Activity" not in note
    assert len(note.split()) <= g._ACTIVITY_PUBLIC_WORD_LIMIT + 6
    assert len(note) <= g._ACTIVITY_PUBLIC_CHAR_LIMIT + 80


def test_activity_hub_matching_avoids_label_prefix_and_generic_collisions():
    records = [{
        "topic": "Methods",
        "concept_title": "Hub",
        "concept_details": (
            "Description: d // Activity/Info Hub: Activity — Activity 10: "
            "Observe the second setup."
        ),
    }]
    activity_one = _item(
        "QINV-0001", "Observe the first setup.",
        source_kind="activity", source_label="Activity 1",
    )
    assert not g._activity_hub_locations(records, activity_one)

    first_discussion = _item(
        "QINV-0002", "Compare the two supplied processes.",
        source_kind="activity", source_label="Discuss",
    )
    second_discussion = _item(
        "QINV-0003", "Explain why the observed result changes.",
        source_kind="activity", source_label="Discuss",
    )
    assert g._activity_hub_marker(first_discussion) != (
        g._activity_hub_marker(second_discussion))

    common = "Compare the two supplied processes and record"
    same_prefix_first = _item(
        "QINV-0004", f"{common} the first outcome.",
        source_kind="activity", source_label="Discuss",
    )
    same_prefix_second = _item(
        "QINV-0005", f"{common} the second outcome.",
        source_kind="activity", source_label="Discuss",
    )
    assert g._activity_hub_marker(same_prefix_first) == (
        g._activity_hub_marker(same_prefix_second))
    tracked = [
        {
            "concept_details": (
                "Description: d // Activity/Info Hub: "
                + g._compact_activity_hub_note(same_prefix_first)
            ),
            "_activity_hub_qids": ["QINV-0004"],
        },
        {
            "concept_details": (
                "Description: d // Activity/Info Hub: "
                + g._compact_activity_hub_note(same_prefix_second)
            ),
            "_activity_hub_qids": ["QINV-0005"],
        },
    ]
    assert g._activity_hub_locations(tracked, same_prefix_first) == [0]
    assert g._activity_hub_locations(tracked, same_prefix_second) == [1]


def test_activity_only_topic_does_not_require_an_artificial_type():
    records = [{
        "topic": "Classroom Investigation",
        "concept_title": "Observing Change",
        "concept_details": (
            "Description: Observe the change. // Activity/Info Hub: "
            "Activity — Activity 1: Record the observation."
        ),
    }]
    activity = _item(
        "QINV-0001", "Record the observation.",
        topic="Classroom Investigation",
        source_kind="activity", source_label="Activity 1",
    )
    assert not g._inventory_topic_type_coverage_violations(
        records, _inventory(activity))

    assessable = _item(
        "QINV-0002", "Explain the observation.",
        topic="Classroom Investigation",
        source_kind="checkpoint_question",
        _activity_origin=True,
    )
    assert g._inventory_topic_type_coverage_violations(
        records, _inventory(assessable))


def test_checkpoint_fallback_uses_task_semantics_not_source_category():
    item = _item(
        "QINV-0001",
        "Compare the two supplied processes and justify the difference.",
        source_kind="checkpoint_question",
    )
    fallback = g._deterministic_fallback_type(item)
    assert fallback is not None
    assert "Checkpoint" not in fallback["type_title"]
    assert fallback["type_title"].startswith("Comparing")


def test_validator_rejects_raw_rich_text_and_accepts_canonical_form():
    base = {
        "topic": "Methods",
        "parent_concept": "Core",
        "concept_title": "Applying a Ratio",
        "keywords": "",
    }
    raw = {
        **base,
        "concept_details": (
            "Description: Use $a/b$ for the ratio. // "
            "Misconceptions: Students may invert the ratio."
        ),
    }
    report = cv.validate_concept_rows(
        [raw], require_culmination=False, allow_culmination=False)
    assert any(
        error["code"] == "rich_text_format" for error in report["errors"])

    canonical = dict(raw)
    canonical["concept_details"] = kr.canonicalize_rich_text(
        raw["concept_details"])
    report = cv.validate_concept_rows(
        [canonical], require_culmination=False, allow_culmination=False)
    assert not any(
        error["code"] == "rich_text_format" for error in report["errors"])
