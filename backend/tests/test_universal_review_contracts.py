"""Universal quality contracts distilled from the three reviewed chapters."""
from __future__ import annotations

import copy

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            r"Example 01: Use a=10 and d=\frac{1}{2}.",
            r"Example 01: Use [Katex] a=10 [/Katex] and "
            r"[Katex] d=\frac{1}{2} [/Katex].",
        ),
        (
            r"Example 01: Sum \frac{1}{15}, \frac{1}{12}, \ldots.",
            r"Example 01: Sum "
            r"[Katex] \frac{1}{15}, \frac{1}{12}, \ldots [/Katex].",
        ),
        (
            r"Hint: S_{x-1}=S_{49}-S_x.",
            r"Hint: [Katex] S_{x-1}=S_{49}-S_x [/Katex].",
        ),
        (
            r"Example 01: Invest at 8\% simple interest.",
            r"Example 01: Invest at [Katex] 8\% [/Katex] simple interest.",
        ),
        (
            r"Hint: Number of rungs = 250/25+1.",
            r"Hint: Number of rungs = [Katex] 250/25+1 [/Katex].",
        ),
        (
            r"Apply S_n=\frac{n}{2}[2a+(n-1)d].",
            r"Apply [Katex] S_n=\frac{n}{2}[2a+(n-1)d] [/Katex].",
        ),
        (
            r"Let \alpha_i identify the indexed value.",
            r"Let [Katex] \alpha_i [/Katex] identify the indexed value.",
        ),
        (
            r"Compute \frac{1}{2}^2.",
            r"Compute [Katex] \frac{1}{2}^2 [/Katex].",
        ),
        (
            r"Use x^\alpha in the rule.",
            r"Use [Katex] x^\alpha [/Katex] in the rule.",
        ),
        (
            r"Calculate a \times b.",
            r"Calculate [Katex] a \times b [/Katex].",
        ),
        (
            "The answer has the form (a^m)^n.",
            "The answer has the form [Katex] (a^m)^n [/Katex].",
        ),
        (
            "Use (n^a)^b = n^(ab).",
            "Use [Katex] (n^a)^b = n^(ab) [/Katex].",
        ),
        (
            "Use m^a × n^a = (mn)^a.",
            "Use [Katex] m^a × n^a = (mn)^a [/Katex].",
        ),
        (
            "Use m^a/n^a = (m/n)^a.",
            "Use [Katex] m^a/n^a = (m/n)^a [/Katex].",
        ),
    ],
)
def test_unwrapped_math_repair_preserves_hosted_source_fragments(
    raw, expected,
):
    assert kr.rich_text_issues(raw)

    repaired = kr.repair_unwrapped_math(raw)

    assert repaired == expected
    assert kr.rich_text_issues(repaired) == []
    assert kr.unwrap_katex(repaired) == raw
    assert kr.repair_unwrapped_math(repaired) == repaired


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            r"Use a \frac{1}{2} scale.",
            r"Use a [Katex] \frac{1}{2} [/Katex] scale.",
        ),
        (
            r"Use a \theta angle.",
            r"Use a [Katex] \theta [/Katex] angle.",
        ),
        (
            r"Call \theta a variable.",
            r"Call [Katex] \theta [/Katex] a variable.",
        ),
    ],
)
def test_unwrapped_math_repair_keeps_whitespace_separated_prose_atoms_out(
    raw, expected,
):
    repaired = kr.repair_unwrapped_math(raw)

    assert repaired == expected
    assert kr.rich_text_issues(repaired) == []
    assert kr.unwrap_katex(repaired) == raw


@pytest.mark.parametrize(
    "expression",
    [
        r"\alpha_i",
        r"\frac{1}{2}^2",
        r"x^\alpha",
        r"a \times b",
        r"a + \frac{1}{2}",
        "a=10",
        "250/25+1",
        "(a^m)^n",
        "(n^a)^b = n^(ab)",
        "m^a × n^a = (mn)^a",
        "m^a/n^a = (m/n)^a",
        r"  \theta  ",
    ],
)
def test_unambiguous_math_expression_accepts_one_complete_range(expression):
    assert kr.is_unambiguous_math_expression(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "quantities",
        "a phrase",
        r"a \frac{1}{2}",
        r"\frac{1}{2} scale",
        r"\frac{1}",
        "(students)^2",
        "(for example)^2",
    ],
)
def test_unambiguous_math_expression_rejects_prose_and_partial_math(
    expression,
):
    assert not kr.is_unambiguous_math_expression(expression)


def test_unwrapped_math_repair_protects_existing_markup_and_ambiguous_tex():
    existing = (
        r"Description: [Katex] \begin{aligned}a&=b\\c&=d\end{aligned} "
        r"[/Katex] and `mode=active`."
    )
    assert kr.repair_unwrapped_math(existing) == existing

    malformed = r"Description: Use \frac{1}{2 without a closing brace."
    assert kr.repair_unwrapped_math(malformed) == malformed
    assert "raw_latex" in kr.rich_text_issues(malformed)

    for incomplete in (
        r"Description: Use \frac{1}.",
        r"Description: Use \left( x.",
        r"Description: Use \begin{aligned} content.",
    ):
        assert kr.repair_unwrapped_math(incomplete) == incomplete
        assert "raw_latex" in kr.rich_text_issues(incomplete)

    with_url = (
        r"Visit https://host.example/?x=10/2 and use \frac{1}{2}.")
    url_repaired = kr.repair_unwrapped_math(with_url)
    assert "https://host.example/?x=10/2" in url_repaired
    assert (
        r"use [Katex] \frac{1}{2} [/Katex]." in url_repaired
    )


def test_partial_grouped_power_wrapper_is_still_rejected():
    partial = "[Katex] (a^m) [/Katex]^n"

    assert "raw_latex" in kr.rich_text_issues(partial)
    assert kr.repair_unwrapped_math(partial) == partial


def test_unwrapped_math_repair_handles_long_relations_iteratively():
    raw = "Use " + "=".join([r"\alpha"] * 250) + "."

    repaired = kr.repair_unwrapped_math(raw)

    assert repaired.startswith(r"Use [Katex] \alpha=\alpha=")
    assert repaired.endswith(r"\alpha [/Katex].")
    assert kr.rich_text_issues(repaired) == []


def test_key_value_and_inline_code_are_not_misclassified_as_equations():
    assert "raw_math_expression" not in kr.rich_text_issues(
        "Description: Set mode=active before continuing.")
    assert not kr.rich_text_issues(
        "Description: Assign `x = 5` in the program.")
    assert "raw_math_expression" in kr.rich_text_issues(
        "Description: Solve x = 5.")


def test_rich_text_registry_uses_student_facing_display_answer():
    assert "display_answer" in kr.RICH_TEXT_FIELDS
    assert "answer_display" not in kr.RICH_TEXT_FIELDS


def test_literal_trailing_newline_escape_is_normalized_outside_katex():
    rendered = kr.canonicalize_rich_text(
        r"Description: Explain the pattern.\n")
    assert rendered == "Description: Explain the pattern.\n"
    assert not kr.rich_text_issues(rendered)
    instructional = (
        r"Description: The escape sequence `\n` starts a new line.")
    assert kr.canonicalize_rich_text(instructional) == instructional
    assert not kr.rich_text_issues(instructional)


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


def test_semantic_type_consolidation_preserves_case_routes_across_topics(
    monkeypatch,
):
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
    assert len(result["types"]) == 1
    contracts = g._type_qid_contracts(result["types"])
    assert contracts[first["qid"]][0] == g._topic_comparison_key("Topic A")
    assert contracts[second["qid"]][0] == g._topic_comparison_key("Topic B")


def test_one_reusable_type_keeps_normal_and_activity_cases_independent():
    normal = _item(
        "QINV-0011.1",
        "Interpret the regional identities shown in Fig. 14(a).",
        topic="Italy Unified",
    )
    activity = _item(
        "QINV-0004",
        "Plot the territorial changes made by the Congress of Vienna.",
        topic="A New Conservatism after 1815",
        source_kind="activity",
        _activity_origin=True,
    )
    inventory = _inventory(normal, activity)
    reusable = {
        "type_id": "TYPE-MAP",
        "type_title": "Historical map interpretation",
        "source_question_ids": [normal["qid"], activity["qid"]],
        "case_prompts": [
            {
                "case_id": "CASE-ITALY",
                "case_title": "Infer regional identity",
                "examples": [{
                    "source_question_id": normal["qid"],
                    "example_prompt": normal["raw_task"],
                }],
            },
            {
                "case_id": "CASE-VIENNA",
                "case_title": "Plot territorial change",
                "examples": [{
                    "source_question_id": activity["qid"],
                    "example_prompt": activity["raw_task"],
                }],
            },
        ],
    }

    annotated = g._annotate_mined_type_case_routes([reusable], inventory)
    assert len(annotated) == 1
    cases = annotated[0]["case_prompts"]
    assert [case["topic_match_hint"] for case in cases] == [
        "Italy Unified",
        "A New Conservatism after 1815",
    ]
    assert [case["is_activity"] for case in cases] == [False, True]

    units = g._expand_mined_types_to_assignment_units(annotated)
    assert {unit["_origin_type_id"] for unit in units} == {"TYPE-MAP"}
    by_qid = {unit["source_question_ids"][0]: unit for unit in units}
    assert by_qid[normal["qid"]]["is_activity"] is False
    assert by_qid[activity["qid"]]["is_activity"] is True
    assert by_qid[normal["qid"]]["topic_match_hint"] == "Italy Unified"
    assert by_qid[activity["qid"]]["topic_match_hint"] == (
        "A New Conservatism after 1815"
    )

    for ordered_units in (
        [by_qid[normal["qid"]], by_qid[activity["qid"]]],
        [by_qid[activity["qid"]], by_qid[normal["qid"]]],
    ):
        collapsed = g._collapse_assignment_units_for_render(ordered_units)
        assert len(collapsed) == 2
        by_role = {bool(unit.get("is_activity")): unit for unit in collapsed}
        assert by_role[False]["source_question_ids"] == [normal["qid"]]
        assert by_role[True]["source_question_ids"] == [activity["qid"]]
        assert [
            case["case_id"] for case in by_role[False]["case_prompts"]
        ] == ["CASE-ITALY"]
        assert [
            case["case_id"] for case in by_role[True]["case_prompts"]
        ] == ["CASE-VIENNA"]


def test_dedupe_rebuild_keeps_cross_topic_type_identity_on_resume():
    mazzini = (
        "Write a short note on Giuseppe Mazzini and his role in Young Italy."
    )
    cavour = (
        "Write a short note on Count Camillo de Cavour and Italian "
        "unification."
    )
    duplicate = (
        "Explain how nationalism influenced political change in "
        "nineteenth-century Europe."
    )
    records = [
        {
            "topic": "The Revolutionaries",
            "parent_concept": "Revolutionary nationalism",
            "concept_title": "Giuseppe Mazzini and Young Italy",
            "concept_details": (
                "Description: Mazzini organised revolutionary nationalism. "
                "// Types: Type 01: Writing a concise historical note "
                "Case 01: Revolutionary organiser Example 01: " + mazzini
                + " Type 02: Explaining political development Case 01: "
                "Nationalist change Example 01: " + duplicate
            ),
            "keywords": "Mazzini",
            "_origin_type_id": ["TYPE-SHARED-NOTE", "TYPE-LOCAL-A"],
        },
        {
            "topic": "Italy Unified",
            "parent_concept": "Italian unification",
            "concept_title": "Cavour and Piedmont-Sardinia",
            "concept_details": (
                "Description: Cavour used diplomacy for unification. "
                "// Types: Type 01: Writing a concise historical note "
                "Case 01: Diplomatic architect Example 01: " + cavour
                + " Type 02: Explaining political development Case 01: "
                "Nationalist change Example 01: " + duplicate
            ),
            "keywords": "Cavour",
            "_origin_type_id": ["TYPE-SHARED-NOTE", "TYPE-LOCAL-B"],
        },
    ]
    inventory = _inventory(
        _item("QINV-0001", mazzini, topic="The Revolutionaries"),
        _item("QINV-0002", cavour, topic="Italy Unified"),
        _item("QINV-0003", duplicate, topic="The Revolutionaries"),
    )

    rendered = cr.renumber_types_continuously(records)
    deduped, removed = g._dedupe_rendered_inventory_examples(
        rendered, inventory)
    resumed = cr.renumber_types_continuously(copy.deepcopy(deduped))
    resumed = cr.renumber_types_continuously(copy.deepcopy(resumed))

    assert removed == 1
    assert "Type 01: Writing a concise historical note" in resumed[0][
        "concept_details"
    ]
    assert "Case 01: Revolutionary organiser" in resumed[0][
        "concept_details"
    ]
    assert "Type 01: Writing a concise historical note" in resumed[1][
        "concept_details"
    ]
    assert "Case 02: Diplomatic architect" in resumed[1][
        "concept_details"
    ]
    assert all("_origin_type_id" not in record for record in resumed)
    assert all(
        "TYPE-SHARED-NOTE" not in record["concept_details"]
        for record in resumed
    )
    assert all(
        cr._TYPE_ORIGIN_SEALS_KEY not in row
        for row in g._records_to_api_rows(resumed)
    )


def test_salvage_rebuild_keeps_cross_topic_type_identity_on_resume():
    mazzini = (
        "Write a short note on Giuseppe Mazzini and his role in Young Italy."
    )
    cavour = (
        "Write a short note on Count Camillo de Cavour and Italian "
        "unification."
    )
    records = [
        {
            "topic": "The Revolutionaries",
            "parent_concept": "Revolutionary nationalism",
            "concept_title": "Giuseppe Mazzini and Young Italy",
            "concept_details": (
                "Description: Mazzini organised revolutionary nationalism. "
                "// Types: Type 01: Writing a concise historical note "
                "Case 01: Revolutionary organiser Example 01: " + mazzini
            ),
            "keywords": "Mazzini",
            "_origin_type_id": ["TYPE-SHARED-NOTE"],
        },
        {
            "topic": "Italy Unified",
            "parent_concept": "Italian unification",
            "concept_title": "Cavour and Piedmont-Sardinia",
            "concept_details": (
                "Description: Cavour used diplomacy for unification. "
                "// Types: Type 01: Writing a concise historical note "
                "Case 01: Diplomatic architect Example 01: " + cavour
                + " Type 02: Interpreting a source Case 01: Stub "
                "Example 01: x"
            ),
            "keywords": "Cavour",
            "_origin_type_id": ["TYPE-SHARED-NOTE", "TYPE-STUB"],
        },
    ]
    inventory = _inventory(
        _item("QINV-0001", mazzini, topic="The Revolutionaries"),
        _item("QINV-0002", cavour, topic="Italy Unified"),
    )

    rendered = cr.renumber_types_continuously(records)
    salvaged = g._salvage_short_case_examples(
        rendered, inventory=inventory)
    resumed = cr.renumber_types_continuously(copy.deepcopy(salvaged))
    resumed = cr.renumber_types_continuously(copy.deepcopy(resumed))

    assert "Type 01: Writing a concise historical note" in resumed[0][
        "concept_details"
    ]
    assert "Case 01: Revolutionary organiser" in resumed[0][
        "concept_details"
    ]
    assert "Type 01: Writing a concise historical note" in resumed[1][
        "concept_details"
    ]
    assert "Case 02: Diplomatic architect" in resumed[1][
        "concept_details"
    ]
    assert "Interpreting a source" not in resumed[1]["concept_details"]
    assert all("_origin_type_id" not in record for record in resumed)


def test_visual_drift_audit_keeps_each_qid_on_its_own_case_route():
    first = _item(
        "QINV-0001",
        "Interpret the first visual source.",
        topic="Topic A",
        requires_visual=True,
    )
    second = _item(
        "QINV-0002",
        "Interpret the second visual source.",
        topic="Topic B",
        requires_visual=True,
    )
    reusable = {
        "type_id": "TYPE-VISUAL",
        "type_title": "Visual-source interpretation",
        "source_question_ids": [first["qid"], second["qid"]],
        "case_prompts": [
            {
                "case_id": "CASE-A",
                "case_title": "Interpret allegorical symbols",
                "concept_match_hint": "Concept A",
                "topic_match_hint": "Topic A",
                "examples": [{
                    "source_question_id": first["qid"],
                    "example_prompt": first["raw_task"],
                    "requires_visual": True,
                }],
            },
            {
                "case_id": "CASE-B",
                "case_title": "Interpret a political caricature",
                "concept_match_hint": "Concept B",
                "topic_match_hint": "Topic B",
                "examples": [{
                    "source_question_id": second["qid"],
                    "example_prompt": second["raw_task"],
                    "requires_visual": True,
                }],
            },
        ],
    }

    contracts = g._type_qid_semantic_contracts(
        [reusable],
        _inventory(first, second),
        honor_emitted_requires_visual=True,
        include_case_identity=True,
    )

    assert contracts[first["qid"]]["concept_match_hint"] == "Concept A"
    assert contracts[first["qid"]]["case_id"] == "CASE-A"
    assert contracts[second["qid"]]["concept_match_hint"] == "Concept B"
    assert contracts[second["qid"]]["case_id"] == "CASE-B"


def test_chapter_wide_leaf_cases_are_topic_assigned_independently(monkeypatch):
    destinations = {
        "QINV-0016.1": "The Revolutionaries",
        "QINV-0016.2": "Italy Unified",
        "QINV-0016.3": "The Age of Revolutions",
        "QINV-0016.4": "The Revolution of the Liberals",
        "QINV-0016.5": "Women in Nationalist Struggles",
    }
    tasks = {
        qid: _item(
            qid,
            label,
            topic="",
            parent_qid="QINV-0016",
            _topic_scope="chapter",
        )
        for qid, label in zip(destinations, (
            "Write a note on Giuseppe Mazzini.",
            "Write a note on Count Camillo de Cavour.",
            "Write a note on the Greek War of Independence.",
            "Write a note on the Frankfurt Parliament.",
            "Write a note on women in nationalist struggles.",
        ))
    }
    records = [
        {
            "topic": topic,
            "parent_concept": topic,
            "concept_title": f"Teach {topic}",
            "concept_details": f"Description: {topic}",
            "keywords": "",
        }
        for topic in destinations.values()
    ]

    def fake_openai(_system, user, **_kwargs):
        assert all(qid in user for qid in destinations)
        return {"assignments": [
            {"qid": qid, "topic": topic}
            for qid, topic in destinations.items()
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    inventory = _inventory(*tasks.values())
    result = g._assign_chapter_wide_inventory_topics_via_api(
        meta={},
        inventory=inventory,
        records=records,
        source_topic_excerpts=[],
    )

    assert {
        item["qid"]: item["topic_hint"] for item in result["items"]
    } == destinations
    assert {item["parent_qid"] for item in result["items"]} == {
        "QINV-0016"
    }


def test_independent_parent_leaves_cannot_be_recombined_into_one_case():
    topics = {
        "QINV-0016.1": "The Making of Nationalism in Europe",
        "QINV-0016.2": "The Making of Nationalism in Europe",
        "QINV-0016.3": "The Age of Revolutions",
        "QINV-0016.4": "The Age of Revolutions",
        "QINV-0016.5": "The Age of Revolutions",
    }
    items = [
        _item(
            qid,
            f"Write a note on target {index}.",
            topic=topic,
            parent_qid="QINV-0016",
        )
        for index, (qid, topic) in enumerate(topics.items(), start=1)
    ]
    bundled = {
        "type_id": "TYPE-SHORT-NOTE",
        "type_title": "Writing a concise historical note",
        "source_question_ids": list(topics),
        "case_prompts": [{
            "case_id": "CASE-BUNDLED",
            "case_title": "Specified person, event, institution, or role",
            "concept_match_hint": "One unsafe shared concept",
            "parent_concept_match_hint": "One unsafe shared parent",
            "placement_scope": "mixed_synthesis",
            "examples": [
                {
                    "source_question_id": item["qid"],
                    "example_prompt": item["raw_task"],
                }
                for item in items
            ],
        }],
    }

    annotated = g._annotate_mined_type_case_routes(
        [bundled], _inventory(*items)
    )
    cases = annotated[0]["case_prompts"]
    assert len(cases) == 5
    assert [
        g._assignment_case_qids(case) for case in cases
    ] == [[qid] for qid in topics]
    assert [case["topic_match_hint"] for case in cases] == list(
        topics.values()
    )
    assert all(case["placement_scope"] == "normal" for case in cases)
    assert all(not case.get("concept_match_hint") for case in cases)
    assert all(not case.get("parent_concept_match_hint") for case in cases)

    units = g._expand_mined_types_to_assignment_units(annotated)
    assert len(units) == 5
    assert {unit["_origin_type_id"] for unit in units} == {
        "TYPE-SHORT-NOTE"
    }


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


def test_host_entailment_review_fails_closed_without_a_complete_verdict(
    monkeypatch,
):
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
            "concept": "Method A",
            "concept_description": "Apply the first supported procedure.",
            "is_culmination": False,
        },
        {
            "concept_id": "CONCEPT-0002",
            "topic": "Methods",
            "concept": "Method B",
            "concept_description": "Apply the second supported procedure.",
            "is_culmination": False,
        },
    ]
    monkeypatch.setattr(
        g, "_openai_json", lambda *_a, **_k: {"assignments": []})

    with pytest.raises(
        RuntimeError, match="did not certify every assignment unit"
    ):
        g._review_case_unit_hosts_via_api(
            assignment_units=[unit],
            per_concept={"CONCEPT-0001": [unit]},
            concept_payload=concepts,
            allowed_cids_by_tid={
                "TYPE-0001": {"CONCEPT-0001", "CONCEPT-0002"},
            },
            meta={},
        )


def test_host_entailment_review_accepts_unique_parent_type_alias(monkeypatch):
    unit = _type(
        "TYPE-0010::CASE-0022::0001",
        "QINV-0019",
        "Find the 36th positive odd number.",
        title="Finding the nth Odd Number",
    )
    unit["_origin_type_id"] = "TYPE-0010"
    concepts = [
        {
            "concept_id": "CONCEPT-0001",
            "topic": "Methods",
            "concept": "Procedure Alpha",
            "concept_description": "Apply the first candidate procedure.",
            "is_culmination": False,
        },
        {
            "concept_id": "CONCEPT-0002",
            "topic": "Methods",
            "concept": "Procedure Beta",
            "concept_description": "Use a term position to calculate a value.",
            "is_culmination": False,
        },
    ]
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {
        "assignments": [{
            # Provider shortened the opaque unit ID to its parent Type ID.
            "type_id": "TYPE-0010",
            "concept_id": "CONCEPT-0002",
        }],
    })

    result = g._review_case_unit_hosts_via_api(
        assignment_units=[unit],
        per_concept={"CONCEPT-0001": [unit]},
        concept_payload=concepts,
        allowed_cids_by_tid={
            unit["type_id"]: {"CONCEPT-0001", "CONCEPT-0002"},
        },
        meta={},
    )

    assert not result.get("CONCEPT-0001")
    assert result["CONCEPT-0002"] == [unit]


def test_host_entailment_review_retries_ambiguous_parent_alias(monkeypatch):
    first = _type(
        "TYPE-0010::CASE-A::0001",
        "QINV-0001",
        "Apply source case A.",
        title="Applying a Method",
    )
    second = _type(
        "TYPE-0010::CASE-B::0002",
        "QINV-0002",
        "Apply source case B.",
        title="Applying a Method",
    )
    for unit in (first, second):
        unit["_origin_type_id"] = "TYPE-0010"
    concepts = [
        {
            "concept_id": "CONCEPT-0001",
            "topic": "Methods",
            "concept": "Method Alpha",
            "concept_description": "Apply procedure alpha.",
            "is_culmination": False,
        },
        {
            "concept_id": "CONCEPT-0002",
            "topic": "Methods",
            "concept": "Method Beta",
            "concept_description": "Apply procedure beta.",
            "is_culmination": False,
        },
    ]
    calls = []

    def review(_system, user, **_kwargs):
        calls.append(user)
        if len(calls) == 1:
            return {"assignments": [{
                "type_id": "TYPE-0010",
                "concept_id": "CONCEPT-0001",
            }]}
        return {"assignments": [
            {
                "type_id": first["type_id"],
                "concept_id": "CONCEPT-0001",
            },
            {
                "type_id": second["type_id"],
                "concept_id": "CONCEPT-0002",
            },
        ]}

    monkeypatch.setattr(g, "_openai_json", review)
    result = g._review_case_unit_hosts_via_api(
        assignment_units=[first, second],
        per_concept={"CONCEPT-0001": [first, second]},
        concept_payload=concepts,
        allowed_cids_by_tid={
            first["type_id"]: {"CONCEPT-0001", "CONCEPT-0002"},
            second["type_id"]: {"CONCEPT-0001", "CONCEPT-0002"},
        },
        meta={},
    )

    assert len(calls) == 2
    assert "RETRY CONTRACT" in calls[1]
    assert result["CONCEPT-0001"] == [first]
    assert result["CONCEPT-0002"] == [second]


def test_host_entailment_review_fails_closed_on_provider_error(monkeypatch):
    unit = _type(
        "TYPE-0001",
        "QINV-0001",
        "Recover the input by reversing the supplied rule.",
        title="Recovering an Input by Reversing a Rule",
    )
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")),
    )

    with pytest.raises(
        RuntimeError, match="failed before a complete verdict"
    ):
        g._review_case_unit_hosts_via_api(
            assignment_units=[unit],
            per_concept={"CONCEPT-0001": [unit]},
            concept_payload=[
                {
                    "concept_id": "CONCEPT-0001",
                    "topic": "Methods",
                    "concept": "Method A",
                    "concept_description": "Apply the first procedure.",
                    "is_culmination": False,
                },
                {
                    "concept_id": "CONCEPT-0002",
                    "topic": "Methods",
                    "concept": "Method B",
                    "concept_description": "Apply the second procedure.",
                    "is_culmination": False,
                },
            ],
            allowed_cids_by_tid={
                "TYPE-0001": {"CONCEPT-0001", "CONCEPT-0002"},
            },
            meta={},
        )


def test_host_entailment_review_preserves_unreviewed_activity_units(
    monkeypatch,
):
    reviewed = _type(
        "TYPE-0001",
        "QINV-0001",
        "Recover the input by reversing the supplied rule.",
        title="Recovering an Input by Reversing a Rule",
    )
    activity = _type(
        "TYPE-ACTIVITY",
        "QINV-ACTIVITY",
        "Record how the output changes as the input is varied.",
        title="Observing a Rule",
    )
    activity["is_activity"] = True
    concepts = [{
        "concept_id": "CONCEPT-0001",
        "topic": "Methods",
        "concept": "Reversing and Observing Rules",
        "concept_description": "Undo a rule and observe its input-output pairs.",
        "is_culmination": False,
    }]
    monkeypatch.setattr(g, "_openai_json", lambda *_a, **_k: {
        "assignments": [{
            "type_id": "TYPE-0001",
            "concept_id": "CONCEPT-0001",
            "reason": "The concept explicitly teaches reversal.",
        }],
    })

    result = g._review_case_unit_hosts_via_api(
        assignment_units=[reviewed, activity],
        per_concept={"CONCEPT-0001": [reviewed, activity]},
        concept_payload=concepts,
        allowed_cids_by_tid={
            "TYPE-0001": {"CONCEPT-0001"},
            "TYPE-ACTIVITY": {"CONCEPT-0001"},
        },
        meta={},
    )

    assert [
        unit["type_id"] for unit in result["CONCEPT-0001"]
    ] == ["TYPE-0001", "TYPE-ACTIVITY"]


def test_case_scoped_host_review_preserves_reusable_type_across_hosts(
    monkeypatch,
):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    first_task = "Perform the requested method for source case one."
    second_task = "Perform the requested method for source case two."
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Applying a Supplied Method",
        "type_description": "Apply the method requested in each source case.",
        "task_pattern": "Apply the requested method.",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [
            {
                "case_id": "CASE-A",
                "case_title": "Source case one",
                "placement_scope": "normal",
                "examples": [{
                    "source_question_id": "QINV-0001",
                    "example_prompt": first_task,
                }],
            },
            {
                "case_id": "CASE-B",
                "case_title": "Source case two",
                "placement_scope": "normal",
                "examples": [{
                    "source_question_id": "QINV-0002",
                    "example_prompt": second_task,
                }],
            },
        ],
        "topic_match_hint": "Methods",
        "placement_scope": "normal",
        "is_activity": False,
    }]}
    records = [
        {
            "topic": "Methods",
            "parent_concept": "Approaches",
            "concept_title": "Method Alpha",
            "concept_details": "Description: Apply the first method.",
            "keywords": "",
        },
        {
            "topic": "Methods",
            "parent_concept": "Approaches",
            "concept_title": "Method Beta",
            "concept_details": "Description: Apply the second method.",
            "keywords": "",
        },
        {
            "topic": "Methods",
            "parent_concept": "Theory",
            "concept_title": "Why the Methods Work",
            "concept_details": (
                "Description: Explain the assumptions shared by both methods."
            ),
            "keywords": "",
        },
    ]

    def review(system, user, **kwargs):
        if kwargs["purpose"] == "concept_mapping":
            return {"assignments": [
                {
                    "concept_id": "CONCEPT-0001",
                    "type_ids": ["TYPE-0001::CASE-A::0001"],
                },
                {
                    "concept_id": "CONCEPT-0002",
                    "type_ids": ["TYPE-0001::CASE-B::0002"],
                },
            ]}
        assert kwargs["purpose"] == "concept_validation"
        if "REUSABLE TYPES SPLIT ACROSS HOSTS" in user:
            return {"assignments": [{
                "type_id": "TYPE-0001",
                "concept_id": "CONCEPT-0001",
            }]}
        return {"assignments": [
            {
                "type_id": "TYPE-0001::CASE-A::0001",
                "concept_id": "CONCEPT-0001",
            },
            {
                "type_id": "TYPE-0001::CASE-B::0002",
                "concept_id": "CONCEPT-0002",
            },
        ]}

    monkeypatch.setattr(g, "_openai_json", review)
    out = g._assign_mined_types_via_api(
        records,
        meta=g._metadata(subject="General"),
        mined_types=mined,
        max_attempts=1,
    )

    hosts = mined[g._PLACEMENT_CERTIFICATIONS_KEY]["hosts"]
    assert hosts["QINV-0001"]["concept"] == "Method Alpha"
    assert hosts["QINV-0002"]["concept"] == "Method Beta"
    assert sum(
        "Applying a Supplied Method" in row["concept_details"]
        for row in out
    ) == 2
    alpha = next(
        row for row in out if row["concept_title"] == "Method Alpha")
    beta = next(
        row for row in out if row["concept_title"] == "Method Beta")
    assert first_task in alpha["concept_details"]
    assert second_task not in alpha["concept_details"]
    assert second_task in beta["concept_details"]
    assert first_task in g._types_body(out[0]["concept_details"])
    assert second_task in g._types_body(out[1]["concept_details"])
    # No blanket Type is created for a theory-only sibling with no source qid.
    assert not g._has_meaningful_types(out[2]["concept_details"])
    assert not g._placement_certification_violations(
        out,
        _inventory(
            _item("QINV-0001", first_task),
            _item("QINV-0002", second_task),
        ),
        mined,
    )
    strict_report = cv.validate_concept_rows(
        out,
        allow_types=True,
        require_culmination=False,
        allow_culmination=True,
        allowed_source_examples=(first_task, second_task),
        strict_type_hierarchy=True,
    )
    assert "duplicate_type_definition" not in {
        error["code"] for error in strict_report["errors"]
    }


def test_reusable_type_hosts_remain_distinct_without_convergence_review(
    monkeypatch,
):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("review provider unavailable")
        ),
    )
    first = {
        "type_id": "TYPE-0001::CASE-A::0001",
        "_origin_type_id": "TYPE-0001",
        "type_title": "Applying a shared method",
        "is_activity": False,
    }
    second = {
        "type_id": "TYPE-0001::CASE-B::0002",
        "_origin_type_id": "TYPE-0001",
        "type_title": "Applying a shared method",
        "is_activity": False,
    }

    result = g._consolidate_reusable_type_hosts(
        per_concept={
            "CONCEPT-0001": [first],
            "CONCEPT-0002": [second],
        },
        original_types_by_id={"TYPE-0001": first},
        allowed_cids_by_tid={
            first["type_id"]: {"CONCEPT-0001", "CONCEPT-0002"},
            second["type_id"]: {"CONCEPT-0001", "CONCEPT-0002"},
        },
        concept_payload=[
            {"concept_id": "CONCEPT-0001", "concept": "Method Alpha"},
            {"concept_id": "CONCEPT-0002", "concept": "Method Beta"},
        ],
        meta={},
    )

    assert set(result) == {"CONCEPT-0001", "CONCEPT-0002"}
    assert result["CONCEPT-0001"][0]["_origin_type_id"] == "TYPE-0001"
    assert result["CONCEPT-0002"][0]["_origin_type_id"] == "TYPE-0001"
    assert result["CONCEPT-0001"][0]["type_title"] == (
        "Applying a shared method"
    )
    assert result["CONCEPT-0002"][0]["type_title"] == (
        "Applying a shared method"
    )


def test_mined_type_normalization_prunes_examples_without_inventory_qids():
    source_task = (
        "Explain how the supplied evidence supports the stated conclusion."
    )
    inventory = _inventory(_item("QINV-0001", source_task))
    raw_types = [{
        "type_id": "TYPE-0001",
        "type_title": "Explaining a Conclusion from Evidence",
        "type_description": (
            "Connect supplied evidence to the conclusion it supports."
        ),
        "task_pattern": "Explain a conclusion using supplied evidence.",
        "source_question_ids": ["QINV-0001", "QINV-INVENTED"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Evidence and a conclusion are supplied",
            "examples": [
                {
                    "source_question_id": "QINV-0001",
                    "example_prompt": "A shortened paraphrase.",
                },
                {
                    "source_question_id": "QINV-INVENTED",
                    "example_prompt": "Invented model-authored question.",
                },
                {
                    "source_question_id": "",
                    "example_prompt": "Question with no inventory owner.",
                },
            ],
        }],
        "topic_match_hint": "Methods",
        "placement_scope": "normal",
        "is_activity": False,
    }]

    normalized = g._normalize_mined_type_candidate(raw_types, inventory)

    assert len(normalized) == 1
    assert g._type_source_qids(normalized[0]) == ["QINV-0001"]
    examples = [
        example
        for case in normalized[0]["case_prompts"]
        for example in g._case_examples(case)
    ]
    assert examples == [{
        "source_question_id": "QINV-0001",
        "example_prompt": source_task,
    }]


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
    assert culmination["concept_title"] == "Culmination - Opening Patterns"
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


def test_activity_hub_note_preserves_complete_task_and_removes_heading():
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
    assert note.count(
        "Repeat the full procedure carefully with every supplied material."
    ) == 30
    assert "…" not in note


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
