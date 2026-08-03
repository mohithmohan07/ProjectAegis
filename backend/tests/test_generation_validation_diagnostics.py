from __future__ import annotations

import pytest

from app.services import generation as g


def _row(
    title: str,
    details: str,
    *,
    topic: str = "General Term",
    parent: str = "Arithmetic Progressions",
    evidence: str = "",
) -> dict:
    return {
        "topic": topic,
        "parent_concept": parent,
        "concept_title": title,
        "concept_details": details,
        "keywords": "sequence, term",
        "source_evidence": evidence,
    }


def _culmination(topic: str = "General Term") -> dict:
    return _row(
        "Culmination - General Term",
        "Description: Recap the topic. // Types: Type 01: Mixed reasoning "
        "Case 01: Connect the ideas Example: Combine the listed concepts "
        "to solve a mixed review task.",
        topic=topic,
        parent="Culmination",
    )


def test_final_validation_logs_every_fatal_with_exact_location(monkeypatch):
    records = [
        _row(
            "Deriving the General Term",
            r"Description: The raw expression \frac{n}{2} is not canonical. "
            "// Error Analysis: Students may omit the common difference "
            "while substituting values.",
        ),
        _culmination(),
    ]
    logs: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **kwargs: logs.append((message, kwargs)),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"final validation failed: .*rich_text_format.*"
            r"row_index=0.*concept='Deriving the General Term'.*"
            r"field='concept_details'"
        ),
    ):
        g._validate_final_or_raise(records)

    fatal_logs = [
        message for message, _ in logs
        if "fatal validation error:" in message
    ]
    assert fatal_logs
    assert any(
        all(
            fragment in message
            for fragment in (
                "row_index=0",
                "concept='Deriving the General Term'",
                "field='concept_details'",
                "code='rich_text_format'",
                "message='concept_details violates canonical",
                r"snippet='Description: The raw expression \\frac{n}{2}",
            )
        )
        for message in fatal_logs
    )


def test_final_validation_rejects_missing_source_inventory(monkeypatch):
    question = (
        "Find the tenth term of the progression 3, 7, 11, 15 and explain "
        "which values were substituted."
    )
    records = [
        _row(
            "Deriving the General Term",
            "Description: The nth-term rule locates any requested term. // "
            "Types: Type 01: Direct substitution Case 01: Locate a term "
            "Example 01: Use the supplied values. // "
            "Misconception/ Error Analysis: Misconceptions: Students may "
            "swap the first term and common difference.; Error Analysis: "
            "Students may use the term number without subtracting one.",
        ),
        _culmination(),
    ]
    inventory = {
        "items": [{
            "qid": "QINV-0001",
            "topic_hint": "General Term",
            "raw_task": question,
        }],
    }
    monkeypatch.setattr(
        g.cv,
        "validate_concept_rows",
        lambda *_args, **_kwargs: {
            "errors": [],
            "summary": {"warnings": 0},
        },
    )

    with pytest.raises(
        RuntimeError,
        match=r"final validation failed: exact_inventory_coverage; 1 missing",
    ):
        g._validate_final_or_raise(records, inventory=inventory)


def _strict_normal_row(
    *, question: str = "", hub: str = "",
) -> dict:
    types = (
        " // Types: Type 01: Applying the current relationship "
        "Case 01: Given current and resistance, determine the requested "
        f"quantity Example 01: {question}"
        if question else ""
    )
    hub_section = f" // Activity/Info Hub: {hub}" if hub else ""
    return _row(
        "Electric Current Relationship",
        "Description: Electric current relates charge flow to time and can "
        "be combined with resistance to reason about a circuit."
        "\nAchieving Mastery: Selecting and applying the current relationship "
        "with the supplied circuit quantities."
        + hub_section
        + types
        + " // Misconception/ Error Analysis: Misconceptions: Students may "
        "believe current is consumed as it moves through a circuit.; Error "
        "Analysis: Students may swap the current and resistance values while "
        "substituting into the relationship.",
        topic="Electric Current",
        parent="Circuit Quantities",
    )


def _strict_culmination(*, question: str = "") -> dict:
    types = (
        " // Types: Miscellaneous Type 01: Integrating circuit quantities "
        "Case 01: Combine several circuit relationships in one task "
        f"Example 01: {question}"
        if question else ""
    )
    return _row(
        "Culmination - Electric Current Relationship",
        "Description: Recap of Electric Current Relationship." + types,
        topic="Electric Current",
        parent="Culmination",
    )


def test_final_validation_requires_mastery_and_detailed_culmination_recap():
    missing_mastery = _strict_normal_row()
    missing_mastery["concept_details"] = missing_mastery[
        "concept_details"
    ].replace(
        "\nAchieving Mastery: Selecting and applying the current relationship "
        "with the supplied circuit quantities.",
        "",
    )
    with pytest.raises(RuntimeError, match="missing_mastery_statement"):
        g._validate_final_or_raise(
            [missing_mastery, _strict_culmination()])

    bare_recap = _strict_culmination()
    bare_recap["concept_details"] = "Description: Recap"
    with pytest.raises(RuntimeError, match="culmination_recap_format"):
        g._validate_final_or_raise(
            [_strict_normal_row(), bare_recap])


def test_final_repair_normalizes_newline_analysis_before_api(monkeypatch):
    row = _strict_normal_row()
    row["concept_details"] = row["concept_details"].replace(
        " // Misconception/ Error Analysis:",
        "\nMisconception/ Error Analysis:",
    )
    assert not g._has_valid_terminal_mastery(row["concept_details"])

    def unexpected_api(*_args, **_kwargs):
        pytest.fail("deterministic terminal normalization should avoid API repair")

    monkeypatch.setattr(g, "_openai_json", unexpected_api)
    repaired = g._repair_records_via_api(
        [row, _strict_culmination()],
        meta={},
        stage="final",
        max_attempts=1,
    )

    assert g._has_valid_terminal_mastery(repaired[0]["concept_details"])
    report = g.cv.validate_concept_rows(
        repaired,
        **g._validation_options("final"),
    )
    assert not [
        error for error in report["errors"]
        if error["severity"] == "error"
    ]


def test_final_repair_renormalizes_api_returned_newline_analysis(monkeypatch):
    question = (
        "Calculate the current when 12 coulombs pass in three seconds."
    )
    bad = _strict_normal_row(question=question)
    bad["concept_details"] = bad["concept_details"].replace(
        "Applying the current relationship",
        "Assessment pattern",
        1,
    )
    api_row = _strict_normal_row(question=question)
    description, typed_tail = api_row["concept_details"].split(
        " // Types:", 1,
    )
    types, analysis = typed_tail.split(
        " // Misconception/ Error Analysis:", 1,
    )
    api_row["concept_details"] = (
        description
        + "\nMisconception/ Error Analysis:"
        + analysis
        + " // Types:"
        + types
    )
    calls = []

    def repair_api(*_args, **_kwargs):
        calls.append(1)
        return {"rows": [{
            "topic": api_row["topic"],
            "parent_concept": api_row["parent_concept"],
            "concept": api_row["concept_title"],
            "concept_description": api_row["concept_details"],
            "keywords": api_row["keywords"],
        }]}

    monkeypatch.setattr(g, "_openai_json", repair_api)
    repaired = g._repair_records_via_api(
        [bad, _strict_culmination()],
        meta={},
        stage="final",
        max_attempts=1,
    )

    assert len(calls) == 1
    assert g._has_valid_terminal_mastery(repaired[0]["concept_details"])
    report = g.cv.validate_concept_rows(
        repaired,
        **g._validation_options("final"),
    )
    assert not [
        error for error in report["errors"]
        if error["severity"] == "error"
    ]


def test_final_validation_rejects_unowned_extra_example():
    source_question = (
        "Calculate the current when 12 coulombs of charge pass a point in "
        "three seconds."
    )
    invented = (
        "Invent a different circuit problem that does not appear in the "
        "uploaded source."
    )
    row = _strict_normal_row(question=source_question)
    row["concept_details"] = row["concept_details"].replace(
        " // Misconception/ Error Analysis:",
        " Example 02: " + invented + " // Misconception/ Error Analysis:",
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": source_question,
    }]}

    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; unexpected_examples=1",
    ):
        g._validate_final_or_raise(
            [row, _strict_culmination()],
            inventory=inventory,
        )


def test_final_validation_rejects_extra_truncated_inventory_substring():
    source_question = (
        "Calculate the current when 12 coulombs of charge pass a point in "
        "three seconds and explain each transformation."
    )
    shortened = (
        "when 12 coulombs of charge pass a point in three seconds and explain "
        "each transformation"
    )
    row = _strict_normal_row(question=source_question)
    row["concept_details"] = row["concept_details"].replace(
        " // Misconception/ Error Analysis:",
        " Example 02: " + shortened + " // Misconception/ Error Analysis:",
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": source_question,
    }]}

    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; unexpected_examples=1",
    ):
        g._validate_final_or_raise(
            [row, _strict_culmination()],
            inventory=inventory,
        )


def test_closed_inventory_allows_exact_fragments_from_inner_example_marker():
    source_question = (
        "Analyze this worked demonstration. Example: Calculate the current "
        "when 12 coulombs pass in three seconds."
    )
    row = _strict_normal_row(question=source_question)
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": source_question,
    }]}

    assert not g._unexpected_rendered_type_examples(
        [row, _strict_culmination()],
        inventory,
    )
    g._validate_final_or_raise(
        [row, _strict_culmination()],
        inventory=inventory,
    )


def test_strict_validator_ignores_inner_example_after_katex_repair():
    raw_question = (
        r"Analyze this worked demonstration. Example: Calculate "
        r"\frac{1}{2}+\frac{1}{3} and explain the result."
    )
    rendered_question = g.kr.repair_unwrapped_math(
        g.kr.canonicalize_rich_text(raw_question))
    report = g.cv.validate_concept_rows(
        [_strict_normal_row(question=rendered_question)],
        allow_types=True,
        allowed_source_examples=[raw_question],
        strict_type_hierarchy=True,
    )

    assert "example_numbering" not in {
        error["code"] for error in report["errors"]
    }


def test_closed_inventory_rejects_extra_duplicate_inner_example_fragment():
    source_question = (
        "Analyze this worked demonstration. Example: Calculate the current "
        "when 12 coulombs pass in three seconds."
    )
    fragment = "Calculate the current when 12 coulombs pass in three seconds."
    row = _strict_normal_row(question=source_question)
    row["concept_details"] = row["concept_details"].replace(
        " // Misconception/ Error Analysis:",
        f" Example 02: {fragment} // Misconception/ Error Analysis:",
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": source_question,
    }]}

    unexpected = g._unexpected_rendered_type_examples(
        [row, _strict_culmination()],
        inventory,
    )

    assert len(unexpected) == 1
    assert unexpected[0]["reason"] == "not_in_inventory"


def test_final_validation_rejects_normal_scope_type_on_culmination():
    question = (
        "Calculate the current when 12 coulombs of charge pass a point in "
        "three seconds."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": question,
    }]}
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Applying the current relationship",
        "topic_match_hint": "Electric Current",
        "concept_match_hint": "Electric Current Relationship",
        "placement_scope": "normal",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": (
                "Given current and resistance, determine the requested "
                "quantity"
            ),
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": "QINV-0001",
                "example_prompt": question,
            }],
        }],
    }]}

    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; .*type_hosts=1",
    ):
        g._validate_final_or_raise(
            [_strict_normal_row(), _strict_culmination(question=question)],
            inventory=inventory,
            mined_types=mined,
        )


def test_final_validation_infers_normal_scope_without_mined_taxonomy():
    question = (
        "Calculate the current when 12 coulombs of charge pass a point in "
        "three seconds."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": question,
    }]}

    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; .*type_hosts=1",
    ):
        g._validate_final_or_raise(
            [_strict_normal_row(), _strict_culmination(question=question)],
            inventory=inventory,
            mined_types={"types": []},
        )


def test_final_validation_infers_same_topic_synthesis_without_mined_taxonomy():
    question = (
        "Compare multiple circuit concepts and explain how they interact."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": question,
    }]}

    g._validate_final_or_raise(
        [_strict_normal_row(), _strict_culmination(question=question)],
        inventory=inventory,
        mined_types={"types": []},
    )


def test_explicit_mixed_scope_requires_actual_synthesis_evidence():
    ordinary = {
        "type_title": "Calculating Current",
        # A destination hint is routing context only and must not manufacture
        # synthesis evidence for this ordinary single-method calculation.
        "concept_match_hint": "Integrating Multiple Circuit Relationships",
        "placement_scope": "mixed_synthesis",
        "case_prompts": [{
            "case_title": "Voltage and resistance are supplied",
            "placement_scope": "mixed_synthesis",
            "examples": [{
                "example_prompt": (
                    "Calculate current when voltage is 12 V and resistance "
                    "is 6 ohms."
                ),
            }],
        }],
    }
    synthesis = {
        "type_title": "Integrating Multiple Circuit Relationships",
        "placement_scope": "mixed_synthesis",
        "case_prompts": [{
            "case_title": "Combine current and heating relationships",
            "placement_scope": "mixed_synthesis",
            "examples": [{
                "example_prompt": (
                    "Combine Ohm's law and Joule heating to compare both "
                    "effects."
                ),
            }],
        }],
    }
    one_concept_comparison = {
        "type_title": "Comparing Both Sections of One Conductor",
        "placement_scope": "mixed_synthesis",
        "case_prompts": [{
            "case_title": "Both conductor sections are supplied",
            "placement_scope": "mixed_synthesis",
            "examples": [{
                "example_prompt": (
                    "Compare the resistance of both sections of the same "
                    "uniform conductor."
                ),
            }],
        }],
    }
    unproven_cross_topic = {
        "type_title": "Calculating Current",
        "placement_scope": "cross_topic_synthesis",
        "case_prompts": [{
            "case_title": "Charge and time are supplied",
            "placement_scope": "cross_topic_synthesis",
            "examples": [{
                "example_prompt": (
                    "Calculate current from the supplied charge and time."
                ),
            }],
        }],
    }
    proven_cross_topic = {
        "type_title": "Synthesising Ideas across Source Topics",
        "placement_scope": "cross_topic_synthesis",
        "case_prompts": [{
            "case_title": "Methods from different source topics are combined",
            "placement_scope": "cross_topic_synthesis",
            "examples": [{
                "example_prompt": (
                    "Combine methods across different source topics."
                ),
            }],
        }],
    }
    natural_cross_topic = {
        "type_title": "Comparing Electrical and Heating Effects",
        "placement_scope": "cross_topic_synthesis",
        "case_prompts": [{
            "case_title": "Electrical and heating effects are compared",
            "placement_scope": "cross_topic_synthesis",
            "examples": [{
                "example_prompt": (
                    "Compare resistance from the voltage-current relationship "
                    "with the heating effect in the same conductor."
                ),
            }],
        }],
    }
    concept_payload = {
        "CONCEPT-0001": {
            "concept_id": "CONCEPT-0001",
            "topic": "Electric Current",
            "concept": "Voltage-current Relationship",
            "is_culmination": False,
        },
        "CONCEPT-0002": {
            "concept_id": "CONCEPT-0002",
            "topic": "Heating Effect",
            "concept": "Joule Heating",
            "is_culmination": False,
        },
        "CONCEPT-0003": {
            "concept_id": "CONCEPT-0003",
            "topic": "Electric Current",
            "concept": "Ohm's Law",
            "is_culmination": False,
        },
    }
    invented_synthesis_title = {
        **synthesis,
        "_source_task_evidence": (
            "Calculate current from the supplied charge and time."
        ),
    }

    assert g._assignment_placement_scope(ordinary) == "normal"
    assert g._assignment_placement_scope(one_concept_comparison) == "normal"
    assert g._assignment_placement_scope(unproven_cross_topic) == "normal"
    assert (
        g._assignment_placement_scope(synthesis, concept_payload)
        == "mixed_synthesis"
    )
    assert (
        g._assignment_placement_scope(proven_cross_topic)
        == "cross_topic_synthesis"
    )
    assert (
        g._assignment_placement_scope(
            natural_cross_topic, concept_payload)
        == "cross_topic_synthesis"
    )
    assert (
        g._assignment_placement_scope(
            invented_synthesis_title, concept_payload)
        == "normal"
    )


def test_final_validation_keeps_single_concept_comparison_off_culmination():
    question = (
        "Compare the resistance between different sections of the same "
        "conductor and explain the result."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electric Current",
        "raw_task": question,
    }]}

    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; .*type_hosts=1",
    ):
        g._validate_final_or_raise(
            [_strict_normal_row(), _strict_culmination(question=question)],
            inventory=inventory,
            mined_types={"types": []},
        )


def test_terminal_host_gate_overrides_a_self_consistent_wrong_mined_hint():
    question = "Find the arithmetic mean between 4 and 10."
    records = [
        _row(
            "Constructing Arithmetic Progressions",
            "Description: Build a progression from its first term and common "
            "difference. // Types: Type 01: Finding an arithmetic mean "
            "Case 01: Two endpoint terms are supplied "
            f"Example 01: {question}",
            topic="Arithmetic Progressions",
            parent="Construction",
        ),
        _row(
            "Identifying the Arithmetic Mean of Two AP Terms",
            "Description: The middle term lies equally far from two adjacent "
            "terms in an arithmetic progression.",
            topic="Arithmetic Progressions",
            parent="Properties of AP Terms",
        ),
    ]
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Arithmetic Progressions",
        "raw_task": question,
    }]}
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Finding an Arithmetic Mean",
        "topic_match_hint": "Arithmetic Progressions",
        # Deliberately self-consistent but wrong: terminal evidence must not
        # allow this model-authored hint to certify itself.
        "concept_match_hint": "Constructing Arithmetic Progressions",
        "placement_scope": "normal",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Two endpoint terms are supplied",
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": "QINV-0001",
                "example_prompt": question,
            }],
        }],
    }]}

    violations = g._rendered_type_placement_violations(
        records, inventory, mined)

    assert violations == [{
        "qid": "QINV-0001",
        "type_id": "TYPE-0001",
        "reason": "high_confidence_wrong_host",
        "expected_concept": "Identifying the Arithmetic Mean of Two AP Terms",
        "actual_concept": "Constructing Arithmetic Progressions",
    }]


def test_reviewed_host_certification_replaces_legacy_raw_task_host_guess():
    question = "Find the arithmetic mean between 4 and 10."
    records = [
        _row(
            "Constructing Arithmetic Progressions",
            "Description: Build a progression from its first term and common "
            "difference. // Types: Type 01: Finding an arithmetic mean "
            "Case 01: Two endpoint terms are supplied "
            f"Example 01: {question}",
            topic="Arithmetic Progressions",
            parent="Construction",
        ),
        _row(
            "Identifying the Arithmetic Mean of Two AP Terms",
            "Description: The middle term lies equally far from two adjacent "
            "terms in an arithmetic progression.",
            topic="Arithmetic Progressions",
            parent="Properties of AP Terms",
        ),
    ]
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Arithmetic Progressions",
        "raw_task": question,
    }]}
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Finding an Arithmetic Mean",
        "topic_match_hint": "Arithmetic Progressions",
        "concept_match_hint": "Constructing Arithmetic Progressions",
        "placement_scope": "normal",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "Two endpoint terms are supplied",
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": "QINV-0001",
                "example_prompt": question,
            }],
        }],
    }]}
    g._reset_placement_certifications(mined)
    g._certify_inventory_host(
        mined,
        "QINV-0001",
        records[0],
        basis="type_host_review",
    )

    assert g._placement_certification_violations(
        records, inventory, mined) == []
    # The pre-review heuristic would prefer the second concept from raw task
    # wording. Once semantic review certifies the first host, terminal and
    # deposit validation must not run that heuristic as a second certifier.
    assert g._rendered_type_placement_violations(
        records, inventory, mined) == []
    assert g._normal_concept_type_coverage_violations(
        records, inventory, mined) == []


def test_certified_ambiguous_host_cannot_drift_during_review_or_final_gate(
    monkeypatch,
):
    question = "Perform the supplied procedure and report the requested value."
    type_body = (
        " // Types: Type 01: Applying a Supplied Procedure "
        "Case 01: A procedure is supplied "
        f"Example 01: {question}"
    )
    original = [
        _row(
            "Method Alpha",
            "Description: Apply the first supported procedure.",
            topic="Methods",
            parent="Approaches",
        ),
        _row(
            "Method Beta",
            "Description: Apply the second supported procedure." + type_body,
            topic="Methods",
            parent="Approaches",
        ),
        _row(
            "Why the Procedures Work",
            "Description: Explain assumptions shared by the procedures.",
            topic="Methods",
            parent="Theory",
        ),
    ]
    candidate = [
        dict(original[0]),
        dict(original[1]),
        dict(original[2]),
    ]
    candidate[0]["concept_details"] += type_body
    candidate[1]["concept_details"] = (
        "Description: Apply the second supported procedure."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Methods",
        "raw_task": question,
    }]}
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Applying a Supplied Procedure",
        "type_description": "Use the supplied procedure.",
        "task_pattern": "Perform the supplied procedure.",
        "topic_match_hint": "Methods",
        "placement_scope": "normal",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "A procedure is supplied",
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": "QINV-0001",
                "example_prompt": question,
            }],
        }],
    }]}
    g._reset_placement_certifications(mined)
    g._certify_inventory_host(
        mined,
        "QINV-0001",
        original[1],
        basis="type_host_review",
    )

    assert g._placement_certification_violations(
        original, inventory, mined) == []
    assert g._placement_certification_violations(
        candidate, inventory, mined
    ) == [{
        "qid": "QINV-0001",
        "reason": "certified_host_drift",
        "expected_topic": "Methods",
        "expected_concept": "Method Beta",
        "actual_topic": "Methods",
        "actual_concept": "Method Alpha",
    }]
    assert g._accept_exact_inventory_type_review(
        original, candidate, inventory, mined) is original
    # The theory-only sibling has no qid and therefore needs no Type or ledger
    # entry; only the moved source item is rejected.
    assert not g._has_meaningful_types(original[2]["concept_details"])

    monkeypatch.setattr(
        g.cv,
        "validate_concept_rows",
        lambda *_args, **_kwargs: {
            "errors": [],
            "summary": {"warnings": 0},
        },
    )
    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; .*certified_hosts=1",
    ):
        g._validate_final_or_raise(
            candidate,
            inventory=inventory,
            mined_types=mined,
        )


def test_final_hub_normalization_rebuilds_a_renamed_certified_host(
    monkeypatch,
):
    original_host = _row(
        "Original Activity Host",
        "Description: Interpret observations from a supported investigation.",
        topic="Methods",
        parent="Investigations",
    )
    renamed_host = {
        **original_host,
        "concept_title": "Refined Activity Host",
    }
    inventory = {"items": [{
        "qid": "QINV-ACT-0001",
        "source_kind": "activity",
        "source_label": "Activity 1",
        "topic_hint": "Methods",
        "raw_task": (
            "Investigate the supplied pattern and record the observations "
            "needed to explain the result."
        ),
    }], "stats": {}}
    mined = {"types": []}
    g._reset_placement_certifications(mined)
    g._certify_inventory_host(
        mined,
        "QINV-ACT-0001",
        original_host,
        basis="activity_host_review",
    )
    rebuilds: list[dict] = []

    def rebuild(records, current_inventory, current_mined, *, meta):
        rebuilds.append(meta)
        assert current_inventory is inventory
        assert current_mined is mined
        g._reset_placement_certifications(current_mined)
        g._certify_inventory_host(
            current_mined,
            "QINV-ACT-0001",
            records[0],
            basis="activity_host_review",
        )
        return records

    monkeypatch.setattr(
        g, "_rebuild_types_after_final_placement_drift", rebuild)

    out = g._normalize_activity_hubs_at_final_boundary(
        [renamed_host],
        inventory,
        mined,
        meta={"subject": "Science"},
    )

    assert rebuilds == [{"subject": "Science"}]
    assert out[0]["_activity_hub_qids"] == ["QINV-ACT-0001"]
    assert "Activity/Info Hub:" in out[0]["concept_details"]
    assert g._placement_certification_violations(
        out, inventory, mined) == []


def _electricity_type_fixture(
    qid: str, question: str, *, concept_hint: str,
) -> dict:
    return {
        "type_id": f"TYPE-{qid[-4:]}",
        "type_title": "Calculating Electric Current from Charge and Time",
        "type_description": (
            "Use charge flow and elapsed time to calculate current."
        ),
        "task_pattern": "Calculate electric current from charge and time.",
        "topic_match_hint": "Electricity",
        "concept_match_hint": concept_hint,
        "placement_scope": "normal",
        "source_question_ids": [qid],
        "case_prompts": [{
            "case_id": f"CASE-{qid[-4:]}",
            "case_title": "Charge and elapsed time are supplied",
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": qid,
                "example_prompt": question,
            }],
        }],
    }


def _electricity_concept_rows(
    *, current_types: str = "", potential_types: str = "",
) -> list[dict]:
    return [
        _row(
            "Electric Current from Charge and Time",
            "Description: Current measures charge flow per unit time."
            + current_types,
            topic="Electricity",
            parent="Circuit Quantities",
        ),
        _row(
            "Potential Difference between Two Points",
            "Description: Potential difference measures work per unit charge."
            + potential_types,
            topic="Electricity",
            parent="Circuit Quantities",
        ),
        _row(
            "Culmination - Electricity",
            "Description: Recap of Electric Current from Charge and Time and "
            "Potential Difference between Two Points.",
            topic="Electricity",
            parent="Culmination",
        ),
    ]


def test_applicable_type_coverage_accepts_source_backed_host_and_theory_sibling():
    question = (
        "Calculate electric current when 24 coulombs pass in 6 seconds."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electricity",
        "raw_task": question,
    }]}
    types = (
        " // Types: Type 01: Calculating current from charge and time "
        "Case 01: Charge and elapsed time are supplied "
        f"Example 01: {question}"
    )
    records = _electricity_concept_rows(current_types=types)
    mined = {"types": [
        _electricity_type_fixture(
            "QINV-0001",
            question,
            concept_hint="Electric Current from Charge and Time",
        ),
    ]}

    assert not g._normal_concept_type_coverage_violations(
        records, inventory, mined)
    # Potential Difference is intentionally theory-only in this fixture. The
    # per-concept gate does not require a Types block without a source task.
    assert not g._has_meaningful_types(records[1]["concept_details"])


def test_applicable_type_coverage_rejects_missing_types_despite_wrong_hint(
    monkeypatch,
):
    question = (
        "Calculate electric current when 24 coulombs pass in 6 seconds."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electricity",
        "raw_task": question,
    }]}
    wrong_host_types = (
        " // Types: Type 01: Calculating current from charge and time "
        "Case 01: Charge and elapsed time are supplied "
        f"Example 01: {question}"
    )
    records = _electricity_concept_rows(
        potential_types=wrong_host_types)
    mined = {"types": [
        _electricity_type_fixture(
            "QINV-0001",
            question,
            # The source/task semantics identify Current; this self-consistent
            # wrong destination hint must not certify Potential Difference.
            concept_hint="Potential Difference between Two Points",
        ),
    ]}

    assert g._normal_concept_type_coverage_violations(
        records, inventory, mined
    ) == [{
        "qid": "QINV-0001",
        "type_id": "TYPE-0001",
        "reason": "missing_meaningful_types",
        "expected_concept": "Electric Current from Charge and Time",
        "actual_concepts": ["Potential Difference between Two Points"],
    }]

    monkeypatch.setattr(
        g.cv,
        "validate_concept_rows",
        lambda *_args, **_kwargs: {
            "errors": [],
            "summary": {"warnings": 0},
        },
    )
    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; .*concept_type_coverage=1",
    ):
        g._validate_final_or_raise(
            records, inventory=inventory, mined_types=mined)


def test_applicable_type_coverage_requires_qid_on_proven_typed_host():
    question = (
        "Calculate electric current when 24 coulombs pass in 6 seconds."
    )
    other_question = (
        "Calculate electric current when 12 volts act across 4 ohms."
    )
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Electricity",
        "raw_task": question,
    }]}
    current_types = (
        " // Types: Type 01: Calculating current from voltage and resistance "
        "Case 01: Voltage and resistance are supplied "
        f"Example 01: {other_question}"
    )
    wrong_host_types = (
        " // Types: Type 01: Calculating current from charge and time "
        "Case 01: Charge and elapsed time are supplied "
        f"Example 01: {question}"
    )
    records = _electricity_concept_rows(
        current_types=current_types,
        potential_types=wrong_host_types,
    )
    mined = {"types": [
        _electricity_type_fixture(
            "QINV-0001",
            question,
            concept_hint="Potential Difference between Two Points",
        ),
    ]}

    assert g._normal_concept_type_coverage_violations(
        records, inventory, mined
    ) == [{
        "qid": "QINV-0001",
        "type_id": "TYPE-0001",
        "reason": "source_qid_not_on_expected_host",
        "expected_concept": "Electric Current from Charge and Time",
        "actual_concepts": ["Potential Difference between Two Points"],
    }]


def test_final_validation_requires_every_pure_activity_in_a_hub():
    activity = (
        "Connect the circuit, vary the resistance, and record the current for "
        "each setting."
    )
    inventory = {"items": [{
        "qid": "QINV-ACT-01",
        "source_kind": "activity",
        "source_label": "Activity 12.1",
        "topic_hint": "Electric Current",
        "raw_task": activity,
    }]}

    with pytest.raises(
        RuntimeError,
        match=r"source_inventory_semantics; hub_contract=1",
    ):
        g._validate_final_or_raise(
            [_strict_normal_row(), _strict_culmination()],
            inventory=inventory,
        )


def test_activity_hub_normalization_removes_answer_and_stale_image():
    correct_image = "https://cdn.example.test/current-circuit.png"
    wrong_image = "https://cdn.example.test/unrelated-voltmeter.png"
    activity = (
        "Connect the circuit, vary the resistance, and record the current for "
        "each setting."
    )
    inventory = {"items": [{
        "qid": "QINV-ACT-01",
        "source_kind": "activity",
        "source_label": "Activity 12.1",
        "topic_hint": "Electric Current",
        "raw_task": activity,
        "image_urls": [correct_image],
        "_image_captions": {
            correct_image: "Fig. 12.1 - variable-resistance circuit",
        },
        "_figure_images_resolved": True,
    }]}
    stale_hub = (
        "Activity - Activity 12.1: The result proves that current decreases. "
        f'[img src="{wrong_image}" alt="Fig. 12.2 - voltmeter"].'
    )
    row = _strict_normal_row(hub=stale_hub)
    row["_activity_hub_qids"] = ["QINV-ACT-01"]

    normalized = g._normalize_activity_hubs_from_inventory(
        [row, _strict_culmination()], inventory)
    hub = g.cr.activity_hub_body(normalized[0]["concept_details"])

    assert "result proves" not in hub
    assert wrong_image not in hub
    assert correct_image in hub
    assert normalized[0]["_activity_hub_qids"] == ["QINV-ACT-01"]
    assert not g._hub_inventory_contract_violations(normalized, inventory)


def test_empty_authoritative_hub_inventory_strips_stale_hub_content():
    row = _strict_normal_row(
        hub="Activity - stale task: Record an observation.")
    row["_activity_hub_qids"] = ["QINV-STALE"]

    normalized = g._normalize_activity_hubs_from_inventory(
        [row, _strict_culmination()],
        {"items": [], "stats": {}},
    )

    assert not g.cr.activity_hub_body(normalized[0]["concept_details"])
    assert "_activity_hub_qids" not in normalized[0]


def test_final_repair_options_include_every_terminal_strict_contract():
    options = g._validation_options("final")

    assert options["strict_type_hierarchy"] is True
    assert options["strict_analysis_section"] is True
    assert options["strict_mastery_statement"] is True
    assert options["strict_culmination_recap"] is True
    assert {
        "section_number_in_description",
        "case_example_semantic_mismatch",
    } <= g._FATAL_CODES


def test_final_repair_loop_receives_type_and_stray_mastery_defects(
    monkeypatch,
):
    question = (
        "Calculate the current when 12 coulombs pass in three seconds."
    )
    bad = _strict_normal_row(question=question)
    bad["concept_details"] = bad["concept_details"].replace(
        "Applying the current relationship",
        "Assessment pattern",
        1,
    ).replace(
        " // Misconception/ Error Analysis:",
        " Achieving Mastery: Ignore this stray marker. // "
        "Misconception/ Error Analysis:",
    )
    repaired = _strict_normal_row(question=question)
    prompts = []

    def repair_api(_system, user, **_kwargs):
        prompts.append(user)
        return {"rows": [{
            "topic": repaired["topic"],
            "parent_concept": repaired["parent_concept"],
            "concept": repaired["concept_title"],
            "concept_description": repaired["concept_details"],
            "keywords": repaired["keywords"],
        }]}

    monkeypatch.setattr(g, "_openai_json", repair_api)

    out = g._repair_records_via_api(
        [bad, _strict_culmination()],
        meta={},
        stage="final",
        max_attempts=1,
    )

    assert len(prompts) == 1
    assert "generic_type_definition" in prompts[0]
    assert "mastery_marker_outside_description" in prompts[0]
    assert out[0]["concept_details"] == repaired["concept_details"]


def test_generic_mined_type_title_is_replaced_from_its_source_example():
    source_question = (
        "Calculate current from the supplied charge and elapsed time."
    )
    mined_type = {
        "type_id": "TYPE-0001",
        "type_title": "Assessment pattern",
        "topic_match_hint": "Electric Current",
        "concept_match_hint": "Electric Current Relationship",
        "placement_scope": "normal",
        "case_prompts": [{
            "case_title": "Substitute charge and time",
            "examples": [{"example_prompt": source_question}],
        }],
    }
    body, next_number = g._mined_type_to_body(mined_type, 0)

    assert next_number == 1
    assert "Assessment pattern" not in body
    assert "Type 01: Calculating Current from the Supplied Charge" in body
    assert source_question in body
    row = _strict_normal_row()
    row["concept_details"] = row["concept_details"].replace(
        " // Misconception/ Error Analysis:",
        f" // Types: {body} // Misconception/ Error Analysis:",
    )
    assert not g._mined_type_topic_violations(
        [row, _strict_culmination()],
        {"types": [mined_type]},
    )


def test_generic_mined_type_title_supports_legacy_string_case_prompt():
    source_question = (
        "Calculate current from the supplied charge and elapsed time."
    )
    body, next_number = g._mined_type_to_body(
        {
            "type_title": "Assessment pattern",
            "case_prompts": [source_question],
        },
        0,
    )

    assert next_number == 1
    assert "Assessment pattern" not in body
    assert "Type 01: Calculating Current from the Supplied Charge" in body
    assert source_question in body


def test_late_canonicalization_normalizes_common_mathpix_wrappers():
    records = [_row(
        "Reading a Source Figure",
        r"""Description: \begin{figure}[h]
\centering
\includegraphics[width=\textwidth]{https://cdn.example.org/figure.png}
\captionof{figure}{A resistance diagram}
\label{fig:resistance}
\end{figure}
\begin{itemize}
\item Compare both branches.
\item Record the current.
\end{itemize} // Error Analysis: Students may read the two branches as a
single branch.""",
    )]

    out = g._canonicalize_concept_rich_text(records)
    details = out[0]["concept_details"]

    assert r"\begin{figure}" not in details
    assert r"\caption" not in details
    assert r"\item" not in details
    assert (
        '[img src="https://cdn.example.org/figure.png" alt="Source visual"]'
        in details
    )
    assert "Caption: A resistance diagram" in details
    assert "• Compare both branches." in details
    assert g.kr.rich_text_issues(details) == []


def test_mathpix_normalization_does_not_nest_existing_katex():
    details = (
        r"Description: [Katex] \begin{aligned}a&=b\\c&=d\end{aligned} "
        r"[/Katex] // Error Analysis: Students may omit the second relation."
    )

    normalized = g._canonicalize_concept_rich_text([
        _row("Aligned Relations", details)
    ])[0]["concept_details"]

    assert normalized.count("[Katex]") == 1
    assert normalized.count("[/Katex]") == 1
    assert g.kr.rich_text_issues(normalized) == []


def test_inventory_coverage_ignores_katex_wrapper_only_repairs():
    raw = r"Calculate \frac{a}{b}+\frac{c}{d} and explain each step."
    canonical = (
        r"Calculate [Katex] \frac{a}{b}+\frac{c}{d} [/Katex] "
        "and explain each step."
    )

    assert g._inventory_coverage_key(raw) == (
        g._inventory_coverage_key(canonical)
    )
    adjacent_raw = r"Use d=\frac{1}{2} and S_n=20."
    adjacent_canonical = (
        r"Use d=[Katex] \frac{1}{2} [/Katex] and "
        r"[Katex] S_n=20 [/Katex]."
    )
    assert g._inventory_coverage_key(adjacent_raw) == (
        g._inventory_coverage_key(adjacent_canonical)
    )
    mathpix_list = (
        r"Answer each part. \begin{itemize}\begin{itemize}"
        r"\item[(i)] Find the first term."
        r"\item[(ii)] Find the common difference."
        r"\end{itemize}\end{itemize}"
    )
    public_list = (
        "Answer each part. \u2022 Find the first term."
        "\u2022 Find the common difference."
    )
    assert g._inventory_coverage_key(mathpix_list) == (
        g._inventory_coverage_key(public_list)
    )


def test_source_figure_reconciliation_keeps_multi_image_alt_identity():
    sections = g.parse_mmd_sections(
        "# Visual task\n"
        "![Fig. 5 - comparison](https://example.test/fig-5-a.png)\n"
        "![Fig. 5 - comparison](https://example.test/fig-5-b.png)\n"
    )
    registry = g._source_figure_registry(sections)

    tags = g._source_figure_tags_for_example(
        "Refer to Fig. 5 and compare both panels.",
        registry,
    )

    assert tags == [
        '[img src="https://example.test/fig-5-a.png" '
        'alt="Fig. 5 - comparison"]',
        '[img src="https://example.test/fig-5-b.png" '
        'alt="Fig. 5 - comparison, visual 2"]',
    ]


def test_inventory_gate_dedupes_after_figure_reconciliation():
    source = (
        "# Visual task\n"
        "![Fig. 5 - comparison](https://example.test/fig-5-a.png)\n"
        "![Fig. 5 - comparison](https://example.test/fig-5-b.png)\n"
    )
    item = {
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Visual task",
        "raw_task": "Refer to Fig. 5 and compare both panels.",
        "image_urls": [
            "https://example.test/fig-5-a.png",
            "https://example.test/fig-5-b.png",
        ],
        "_image_captions": {
            "https://example.test/fig-5-a.png": "Fig. 5 - comparison",
            "https://example.test/fig-5-b.png": "Fig. 5 - comparison",
        },
        "_figure_images_resolved": True,
    }
    inventory = {"items": [item], "stats": {}}
    authoritative = g._inventory_task_text(item)
    stale = authoritative.replace(", visual 2", "")
    records = [_row(
        "Compare a visual pair",
        "Description: Two panels can be compared using the same criteria. // "
        "Types: Type 01: Compare a visual pair "
        "Case 01: Read both panels before comparing "
        f"Example 01: {stale} // "
        "Misconception/ Error Analysis: Misconceptions: Students may inspect "
        "only one panel.; Error Analysis: Students may list features without "
        "making a comparison.",
        topic="Visual task",
        parent="Visual comparison",
    )]

    restored = g._enforce_rendered_inventory_coverage(
        records, inventory, {"types": []})
    reconciled, changed = g._reconcile_explicit_figure_images(
        restored, g.parse_mmd_sections(source))

    assert changed == 1
    assert g._rendered_inventory_coverage_defects(
        reconciled, inventory)["duplicate"] == ["QINV-0001"]

    final = g._enforce_rendered_inventory_coverage(
        reconciled, inventory, {"types": []})
    assert g._rendered_inventory_coverage_defects(final, inventory) == {
        "missing": [],
        "duplicate": [],
    }


def test_resumed_inventory_figure_urls_refresh_from_current_source():
    stale_url = "https://example.test/stale-fig-5.png"
    current_url = "https://example.test/current-fig-5.png"
    inventory = {
        "items": [{
            "qid": "QINV-0001",
            "source_kind": "exercise",
            "raw_task": "Refer to Fig. 5 and compare both panels.",
            "image_urls": [stale_url],
            "_image_captions": {stale_url: "Fig. 5 - old copy"},
            "_figure_images_resolved": True,
        }],
        "stats": {},
    }
    sections = g.parse_mmd_sections(
        "# Visual task\n"
        f"![Fig. 5 - current source]({current_url})\n"
    )

    refreshed = g._refresh_inventory_figure_metadata(inventory, sections)
    refreshed_item = refreshed["items"][0]

    assert refreshed_item["image_urls"] == [current_url]
    assert refreshed_item["_image_captions"] == {
        current_url: "Fig. 5 - current source",
    }
    assert stale_url not in g._inventory_task_text(refreshed_item)
    assert current_url in g._inventory_task_text(refreshed_item)

    removed = g._refresh_inventory_figure_metadata(
        inventory,
        g.parse_mmd_sections("# Visual task\nThe figure is unavailable.\n"),
    )
    removed_item = removed["items"][0]
    assert removed_item["image_urls"] == []
    assert removed_item["_image_captions"] == {}
    assert removed_item["_figure_images_resolved"] is True
    assert removed_item["requires_visual"] is True
    assert stale_url not in g._inventory_task_text(removed_item)


@pytest.mark.parametrize(
    "replacement",
    [
        (
            r"Compare the quan[Katex] t [/Katex]ities using "
            r"[Katex] \frac{a}{b} [/Katex]."
        ),
        (
            r"Compare the [Katex] quantities [/Katex] using "
            r"[Katex] \frac{a}{b} [/Katex]."
        ),
        (
            r"Compare [Katex] the quantities using \frac{a}{b} [/Katex]."
        ),
    ],
)
def test_final_rich_text_repair_rejects_non_formatting_changes(
    monkeypatch, replacement,
):
    details = (
        r"Description: Compare the quantities using \frac{a}{b}."
        "\nAchieving Mastery: Explaining the comparison correctly. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "the denominator is irrelevant.; Error Analysis: Students may omit "
        "the denominator."
    )
    records = [
        _row("Compare fractions", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    calls = 0

    def changed_api(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"rows": [{
            "topic": "T",
            "parent_concept": "P",
            "concept": "Compare fractions",
            "concept_description": (
                details.replace(
                    r"Compare the quantities using \frac{a}{b}.",
                    replacement,
                )
            ),
            "keywords": "sequence, term",
        }]}

    monkeypatch.setattr(g, "_openai_json", changed_api)
    monkeypatch.setattr(g.kr, "repair_unwrapped_math", lambda value: value)

    repaired, changed = g._repair_final_rich_text_via_api(
        records,
        meta=g._metadata(subject="Mathematics"),
        inventory={"items": [], "stats": {}},
        mined_types={"types": []},
    )

    assert calls == 1
    assert changed is False
    assert repaired == records


def test_final_rich_text_repair_wraps_unambiguous_math_without_api(
    monkeypatch,
):
    details = (
        r"Description: Compare AP parameters safely."
        "\nAchieving Mastery: Substituting each parameter correctly. // "
        "Types: Type 01: Apply AP parameters "
        "Case 01: Substitute the supplied values "
        r"Example 01: Use a=10, d=\frac{1}{2}, and S_n=20. // "
        "Misconception/ Error Analysis: Misconceptions: Students may swap "
        "the parameters.; Error Analysis: Students may omit a sign."
    )
    row = _row(
        "Use AP parameters",
        details,
        topic="T",
        parent="P",
        evidence="SRC-LOCKED-01",
    )
    row["review_metadata"] = {"locked": True}
    records = [row, _culmination(topic="T")]
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unambiguous formatting must not call the API")
        ),
    )

    repaired, changed = g._repair_final_rich_text_via_api(
        records,
        meta=g._metadata(subject="Mathematics"),
        inventory={"items": [], "stats": {}},
        mined_types={"types": []},
    )

    assert changed is True
    assert g.kr.rich_text_issues(repaired[0]["concept_details"]) == []
    assert g.kr.unwrap_katex(repaired[0]["concept_details"]) == details
    assert {
        key: value
        for key, value in repaired[0].items()
        if key != "concept_details"
    } == {
        key: value
        for key, value in row.items()
        if key != "concept_details"
    }


def test_final_rich_text_repair_handles_grouped_power_types_without_api(
    monkeypatch,
):
    details = (
        "Description: Compare equivalent grouped powers."
        "\nAchieving Mastery: Rewriting each expression without changing its "
        "value. // Types: Type 01: Rewriting powers as powers of powers — "
        "Students construct equivalent grouped powers. Case 01: Factor the "
        "exponent Example 01: Use (n^a)^b = n^(ab). "
        "Type 02: Combining powers with equal exponents — Students combine "
        "the bases while retaining the exponent. Case 01: Multiply equal "
        "powers Example 01: Use m^a × n^a = (mn)^a and compute "
        "2^5 × 5^5. Case 02: Divide equal powers Example 01: Use "
        "m^a/n^a = (m/n)^a and simplify 10^4/5^4. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "the outer exponent applies only to the last factor.; Error Analysis: "
        "Students may multiply the exponents without first identifying the "
        "entire grouped base."
    )
    row = _row(
        "Rewrite grouped powers",
        details,
        topic="T",
        parent="P",
        evidence="SRC-LOCKED-POWERS",
    )
    records = [row, _culmination(topic="T")]
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("grouped-power formatting must not call the API")
        ),
    )

    repaired, changed = g._repair_final_rich_text_via_api(
        records,
        meta=g._metadata(subject="Mathematics"),
        inventory={"items": [], "stats": {}},
        mined_types={"types": []},
    )

    repaired_details = repaired[0]["concept_details"]
    assert changed is True
    assert g.kr.rich_text_issues(repaired_details) == []
    assert g.kr.unwrap_katex(repaired_details) == details
    assert "[Katex] (n^a)^b = n^(ab) [/Katex]" in repaired_details
    assert "[Katex] m^a × n^a = (mn)^a [/Katex]" in repaired_details
    assert "[Katex] m^a/n^a = (m/n)^a [/Katex]" in repaired_details
    assert {
        key: value
        for key, value in repaired[0].items()
        if key != "concept_details"
    } == {
        key: value
        for key, value in row.items()
        if key != "concept_details"
    }


def test_saved_final_checkpoint_repairs_rich_text_once_and_persists(
    monkeypatch,
):
    source = "# T\nA short source-grounded discussion of a numerical pattern."
    raw_title = r"Use the Finite-sum Formula S_n = \frac{n}{2}(a+l)"
    analysis = (
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "unlike denominators can be added directly.; Error Analysis: Students "
        "may add denominators without first finding a common denominator."
    )
    raw_records = [
        _row(
            raw_title,
            (
                r"Description: Compare two fractional quantities using "
                r"\frac{a}{b} before combining them."
                "\nAchieving Mastery: Explaining why a common denominator is "
                "needed before adding fractions. // Types: Type 01: Add unlike "
                "fractions Case 01: Build equivalent fractional quantities "
                r"Example 01: Calculate \frac{1}{2}+\frac{1}{3} and explain "
                "each transformation. // " + analysis
            ),
            topic="T",
            parent="P",
        ),
        _row(
            f"Culmination - {raw_title}",
            (
                "Description: Recap of Use the Finite-sum Formula "
                r"S_n = \frac{n}{2}(a+l)."
            ),
            topic="T",
            parent="Culmination",
        ),
    ]
    raw_records[0]["source_evidence"] = "SRC-LOCKED-01"
    raw_records[0]["review_metadata"] = {"locked": True}
    source_question = (
        r"Calculate \frac{1}{2}+\frac{1}{3} and explain each transformation."
    )
    checkpoint_inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "T",
        "raw_task": source_question,
    }], "stats": {}}
    checkpoint_mined_types = {"types": []}
    g._reset_placement_certifications(checkpoint_mined_types)
    g._certify_inventory_host(
        checkpoint_mined_types,
        "QINV-0001",
        raw_records[0],
        basis="type_host_review",
    )
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=raw_records,
        question_task_inventory=checkpoint_inventory,
        mined_types=checkpoint_mined_types,
        method_row_snapshot=[],
    )
    repaired_records = [
        _row(
            raw_title,
            (
                "Description: Compare two fractional quantities using "
                r"[Katex] \frac{a}{b} [/Katex] before combining them."
                "\nAchieving Mastery: Explaining why a common denominator is "
                "needed before adding fractions. // Types: Type 01: Add unlike "
                "fractions Case 01: Build equivalent fractional quantities "
                "Example 01: Calculate "
                r"[Katex] \frac{1}{2}+\frac{1}{3} [/Katex] and explain each "
                "transformation. // " + analysis
            ),
            topic="T",
            parent="P",
        ),
        _row(
            f"Culmination - {raw_title}",
            (
                "Description: Recap of Use the Finite-sum Formula "
                r"[Katex] S_n = \frac{n}{2}(a+l) [/Katex]."
            ),
            topic="T",
            parent="Culmination",
        ),
    ]
    calls: list[tuple[str, str]] = []

    def repair_api(system, user, **_kwargs):
        calls.append((system, user))
        return {
            "rows": [{
                "topic": row["topic"],
                "parent_concept": row["parent_concept"],
                "concept": row["concept_title"],
                "concept_description": row["concept_details"],
                "keywords": "model attempted to replace keywords",
                "source_evidence": "MODEL-REWRITE",
            } for row in repaired_records],
        }

    monkeypatch.setattr(g, "_openai_json", repair_api)
    monkeypatch.setattr(g.kr, "repair_unwrapped_math", lambda value: value)
    emitted: list[dict] = []

    repaired = g.concepts_from_mmd(
        source,
        subject="Mathematics",
        live=True,
        resume_checkpoint=checkpoint,
        checkpoint_callback=emitted.append,
    )

    assert len(calls) == 1
    assert "rich_text_format" in calls[0][1]
    assert "Equations MUST be wrapped" in calls[0][0]
    assert all(
        g.kr.rich_text_issues(row["concept_details"]) == []
        for row in repaired
    )
    assert repaired[0]["keywords"] == "sequence, term"
    assert repaired[0]["source_evidence"] == "SRC-LOCKED-01"
    assert repaired[0]["review_metadata"] == {"locked": True}
    assert emitted[-1]["stage"] == "final_content_ready"
    assert emitted[-1]["records"] == repaired

    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the repaired final checkpoint must not call the API")
        ),
    )
    second_emitted: list[dict] = []
    resumed = g.concepts_from_mmd(
        source,
        subject="Mathematics",
        live=True,
        resume_checkpoint=emitted[-1],
        checkpoint_callback=second_emitted.append,
    )

    assert resumed == repaired
    assert second_emitted == []


def test_final_checkpoint_is_emitted_only_after_validation(monkeypatch):
    records = [
        _row(
            "C",
            (
                "Description: A complete concept description."
                "\nAchieving Mastery: Applying the concept correctly. // "
                "Misconception/ Error Analysis: Misconceptions: Students may "
                "believe every condition is optional.; Error Analysis: "
                "Students may omit a required condition."
            ),
            topic="T",
            parent="P",
        ),
        _culmination(topic="T"),
    ]
    checkpoint = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory={"items": [], "stats": {}},
        mined_types={"types": []},
        method_row_snapshot=[],
    )
    monkeypatch.setattr(
        g,
        "_prepare_final_concept_content",
        lambda current, **_kwargs: current,
    )
    monkeypatch.setattr(
        g,
        "_validate_final_or_raise",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("final gate rejected rows")
        ),
    )
    emitted: list[dict] = []

    with pytest.raises(RuntimeError, match="final gate rejected rows"):
        g.concepts_from_mmd(
            "# T\nA short source section.",
            subject="Mathematics",
            live=True,
            resume_checkpoint=checkpoint,
            checkpoint_callback=emitted.append,
        )

    assert not any(
        item.get("stage") == "final_content_ready"
        for item in emitted
    )


def test_invalid_inventory_checkpoint_rewinds_before_question_inventory():
    prior = g._make_concept_checkpoint(
        "description_method_snapshot",
        records=[_row(
            "Prior valid concept",
            "Description: A complete concept description.",
            topic="T",
            parent="P",
        )],
        method_row_snapshot=[],
    )
    invalid = g._make_concept_checkpoint(
        "question_inventory",
        records=prior["records"],
        question_task_inventory={"items": [{
            "qid": "QINV-0001",
            "source_kind": "exercise",
            "raw_task": "",
        }], "stats": {}},
        method_row_snapshot=[],
    )
    history = {
        "checkpoint_format": g._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoints": [prior, invalid],
    }

    restored = g._newest_compatible_concept_checkpoint(history)

    assert restored["stage"] == "description_method_snapshot"


def test_legacy_81_percent_checkpoint_and_v6_without_owner_ledger_are_rejected():
    question = (
        "Apply the supplied procedure and report the requested numerical "
        "value."
    )
    records = [_row(
        "Method Beta",
        "Description: Apply the second supported procedure. // Types: "
        "Type 01: Applying a Supplied Procedure "
        "Case 01: A procedure is supplied "
        f"Example 01: {question}",
        topic="Methods",
        parent="Approaches",
    )]
    inventory = {"items": [{
        "qid": "QINV-0001",
        "source_kind": "exercise",
        "topic_hint": "Methods",
        "raw_task": question,
    }], "stats": {}}
    types = [{
        "type_id": "TYPE-0001",
        "type_title": "Applying a Supplied Procedure",
        "type_description": "Use the supplied procedure.",
        "task_pattern": "Apply the procedure.",
        "topic_match_hint": "Methods",
        "placement_scope": "normal",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": "A procedure is supplied",
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": "QINV-0001",
                "example_prompt": question,
            }],
        }],
    }]
    pre_type = g._make_concept_checkpoint(
        g._CONCEPT_CHECKPOINT_STAGE,
        records=records,
        question_task_inventory=inventory,
        mined_types={"types": types},
        method_row_snapshot=[],
    )
    # Pin the legacy one-host Type contract explicitly. Case-owned routing and
    # leaf-QID inventory cannot safely replay it as the current 81% artifact.
    pre_type["stage_schema_version"] = 1
    schema_v2_pre_type = dict(pre_type)
    schema_v2_pre_type["schema_version"] = g._LEGACY_CONCEPT_CHECKPOINT_SCHEMA
    schema_v2_pre_type.pop("stage_schema_version", None)
    assert not g._compatible_concept_checkpoint_entry(schema_v2_pre_type)
    legacy_post = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory=inventory,
        mined_types={"types": types},
        method_row_snapshot=[],
    )
    legacy_post["stage_schema_version"] = 2
    legacy_final = g._make_concept_checkpoint(
        "final_content_ready",
        records=records,
        question_task_inventory=inventory,
        mined_types={"types": types},
        method_row_snapshot=[],
    )
    legacy_final["stage_schema_version"] = 2
    history = {
        "checkpoint_format": g._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "checkpoints": [pre_type, legacy_post, legacy_final],
    }

    restored = g._newest_compatible_concept_checkpoint(history)

    assert restored is None

    incomplete_v3 = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory=inventory,
        mined_types={"types": types},
        method_row_snapshot=[],
    )
    assert not g._compatible_concept_checkpoint_entry(incomplete_v3)

    certified_mined = {"types": types}
    g._reset_placement_certifications(certified_mined)
    g._certify_inventory_host(
        certified_mined,
        "QINV-0001",
        records[0],
        basis="type_host_review",
    )
    current_v6_without_owner_ledger = g._make_concept_checkpoint(
        "post_type_assignment",
        records=records,
        question_task_inventory=inventory,
        mined_types=certified_mined,
        method_row_snapshot=[],
    )
    history["checkpoints"].append(current_v6_without_owner_ledger)

    assert current_v6_without_owner_ledger["stage_schema_version"] == 6
    assert not g._compatible_concept_checkpoint_entry(
        current_v6_without_owner_ledger
    )
    assert g._newest_compatible_concept_checkpoint(history) is None

    reconciled = g._reconcile_resumed_mined_types(
        certified_mined,
        inventory=inventory,
        meta={},
        use_api=False,
    )
    assert reconciled[g._PLACEMENT_CERTIFICATIONS_KEY] == (
        certified_mined[g._PLACEMENT_CERTIFICATIONS_KEY]
    )


def test_inventory_extraction_retries_empty_or_stub_rows_before_checkpoint(
    monkeypatch,
):
    chunk = {
        "text": "Exercise 1. Calculate current from charge and elapsed time.",
        "source_topic": "Electric Current",
        "chapter_wide_tasks": False,
    }
    monkeypatch.setattr(
        g, "_inventory_chunks_by_topic", lambda _sections: [chunk])
    monkeypatch.setattr(g, "_source_task_anchors", lambda _sections: [])
    monkeypatch.setattr(
        g,
        "_attach_explicit_figure_images",
        lambda items, _sections: items,
    )
    responses = iter([
        {"items": [{
            "source_kind": "exercise",
            "raw_task": "",
        }]},
        {"items": [{
            "source_kind": "exercise",
            "raw_task": (
                "Calculate current from the supplied charge and elapsed time."
            ),
        }]},
    ])
    calls = []

    def extract(*_args, **_kwargs):
        calls.append(True)
        return next(responses)

    monkeypatch.setattr(g, "_openai_json", extract)

    inventory = g._extract_question_task_inventory_via_api(
        meta={}, sections=[{"heading": "Electric Current", "text": "body"}])

    assert len(calls) == 2
    assert g._invalid_inventory_items(inventory) == []
    assert inventory["items"][0]["qid"] == "QINV-0001"


def test_inventory_extraction_rejects_empty_correction_retry(monkeypatch):
    chunk = {
        "text": "Exercise 1. Calculate current from charge and elapsed time.",
        "source_topic": "Electric Current",
        "chapter_wide_tasks": False,
    }
    monkeypatch.setattr(
        g, "_inventory_chunks_by_topic", lambda _sections: [chunk])
    monkeypatch.setattr(g, "_source_task_anchors", lambda _sections: [])
    responses = iter([
        {"items": [{
            "source_kind": "exercise",
            "raw_task": "",
        }]},
        {"items": []},
    ])
    monkeypatch.setattr(
        g, "_openai_json", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(
        RuntimeError,
        match=r"0 substantive row\(s\) after retry",
    ):
        g._extract_question_task_inventory_via_api(
            meta={},
            sections=[{"heading": "Electric Current", "text": "body"}],
        )


def test_inventory_extraction_rejects_correction_retry_that_drops_valid_rows(
    monkeypatch,
):
    chunk = {
        "text": "Exercise list with ten source questions.",
        "source_topic": "Electric Current",
        "chapter_wide_tasks": False,
    }
    monkeypatch.setattr(
        g, "_inventory_chunks_by_topic", lambda _sections: [chunk])
    monkeypatch.setattr(g, "_source_task_anchors", lambda _sections: [])
    first_items = [
        {
            "source_kind": "exercise",
            "raw_task": (
                f"Calculate the requested circuit quantity for source case {i}."
            ),
        }
        for i in range(1, 10)
    ]
    first_items.append({"source_kind": "exercise", "raw_task": ""})
    responses = iter([
        {"items": first_items},
        {"items": [{
            "source_kind": "exercise",
            "raw_task": (
                "Calculate the requested circuit quantity for source case 1."
            ),
        }]},
    ])
    monkeypatch.setattr(
        g, "_openai_json", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(RuntimeError, match=r"below the required 9"):
        g._extract_question_task_inventory_via_api(
            meta={},
            sections=[{"heading": "Electric Current", "text": "body"}],
        )


def test_rejected_saved_final_falls_back_to_preceding_checkpoint(monkeypatch):
    details = (
        "Description: A complete concept description."
        "\nAchieving Mastery: Applying the concept correctly. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "every condition is optional.; Error Analysis: Students may omit a "
        "required condition."
    )
    prior_records = [
        _row("Prior-stage concept", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    stale_final_records = [
        _row("Rejected final concept", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    inventory = {"items": [], "stats": {}}
    mined_types = {"types": []}
    prior = g._make_concept_checkpoint(
        "post_type_assignment",
        records=prior_records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    stale_final = g._make_concept_checkpoint(
        "final_content_ready",
        records=stale_final_records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    history = {
        "checkpoint_format": g._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "stage": "final_content_ready",
        "checkpoints": [prior, stale_final],
    }
    finalized: list[list[str]] = []

    def finalize(current, **_kwargs):
        finalized.append([row["concept_title"] for row in current])
        return current

    validations: list[list[str]] = []

    def validate(current, **_kwargs):
        validations.append([row["concept_title"] for row in current])
        if len(validations) == 1:
            raise RuntimeError("legacy final rejected")
        return {"ok": True, "errors": [], "summary": {}}

    monkeypatch.setattr(g, "_prepare_final_concept_content", finalize)
    monkeypatch.setattr(g, "_validate_final_or_raise", validate)
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback should reuse the preceding checkpoint")
        ),
    )
    emitted: list[dict] = []

    out = g.concepts_from_mmd(
        "# T\nA short source section.",
        subject="Mathematics",
        live=True,
        resume_checkpoint=history,
        checkpoint_callback=emitted.append,
    )

    assert validations[0][0] == "Rejected final concept"
    assert validations[1][0] == "Prior-stage concept"
    assert finalized == [[
        "Prior-stage concept",
        "Culmination - General Term",
    ]]
    assert out[0]["concept_title"] == "Prior-stage concept"
    assert emitted[0] == {
        "checkpoint_action": "discard_stage",
        "stage": "final_content_ready",
        "reason": "strict terminal validation failed",
    }
    assert emitted[1]["stage"] == "final_content_ready"
    assert emitted[1]["records"] == out


def test_saved_final_hub_normalization_failure_discards_only_98_percent(
    monkeypatch,
):
    details = (
        "Description: A complete concept description."
        "\nAchieving Mastery: Applying the concept correctly. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "every condition is optional.; Error Analysis: Students may omit a "
        "required condition."
    )
    prior_records = [
        _row("Prior-stage concept", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    stale_final_records = [
        _row("Stale final concept", details, topic="T", parent="P"),
        _culmination(topic="T"),
    ]
    inventory = {"items": [], "stats": {}}
    mined_types = {"types": []}
    prior = g._make_concept_checkpoint(
        "post_type_assignment",
        records=prior_records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    stale_final = g._make_concept_checkpoint(
        "final_content_ready",
        records=stale_final_records,
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    history = {
        "checkpoint_format": g._CONCEPT_CHECKPOINT_FORMAT,
        "schema_version": g._CONCEPT_CHECKPOINT_SCHEMA,
        "stage": "final_content_ready",
        "checkpoints": [prior, stale_final],
    }

    monkeypatch.setattr(
        g,
        "_normalize_activity_hubs_from_inventory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("saved hub certification no longer resolves")
        ),
    )
    monkeypatch.setattr(
        g,
        "_prepare_final_concept_content",
        lambda current, **_kwargs: current,
    )
    monkeypatch.setattr(
        g,
        "_repair_final_rich_text_via_api",
        lambda current, **_kwargs: (current, False),
    )
    monkeypatch.setattr(
        g,
        "_validate_final_or_raise",
        lambda *_args, **_kwargs: {
            "ok": True,
            "errors": [],
            "summary": {},
        },
    )
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback should reuse the preceding checkpoint")
        ),
    )
    emitted: list[dict] = []

    out = g.concepts_from_mmd(
        "# T\nA short source section.",
        subject="Mathematics",
        live=True,
        resume_checkpoint=history,
        checkpoint_callback=emitted.append,
    )

    assert out[0]["concept_title"] == "Prior-stage concept"
    assert emitted[0] == {
        "checkpoint_action": "discard_stage",
        "stage": "final_content_ready",
        "reason": "strict terminal validation failed",
    }
    assert emitted[1]["stage"] == "final_content_ready"
    assert emitted[1]["records"] == out


def test_final_checkpoint_with_partial_mined_metadata_remains_api_free(
    monkeypatch,
):
    first = (
        "Calculate the current when 12 coulombs of charge pass a point in "
        "three seconds."
    )
    second = (
        "Explain how current changes when the same charge passes in half the "
        "time."
    )
    normal = _strict_normal_row(question=first)
    normal["concept_details"] = normal["concept_details"].replace(
        " // Misconception/ Error Analysis:",
        f" Example 02: {second} // Misconception/ Error Analysis:",
    )
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "source_kind": "exercise",
            "topic_hint": "Electric Current",
            "raw_task": first,
        },
        {
            "qid": "QINV-0002",
            "source_kind": "exercise",
            "topic_hint": "Electric Current",
            "raw_task": second,
        },
    ], "stats": {}}
    mined_types = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Applying the current relationship",
        "topic_match_hint": "Electric Current",
        "concept_match_hint": "Electric Current Relationship",
        "placement_scope": "normal",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "case_title": (
                "Given charge and time, determine the resulting current"
            ),
            "placement_scope": "normal",
            "examples": [{
                "source_question_id": "QINV-0001",
                "example_prompt": first,
            }],
        }],
    }]}
    g._reset_placement_certifications(mined_types)
    g._certify_inventory_host(
        mined_types,
        "QINV-0001",
        normal,
        basis="type_host_review",
    )
    g._certify_inventory_host(
        mined_types,
        "QINV-0002",
        normal,
        basis="type_host_review",
    )
    checkpoint = g._make_concept_checkpoint(
        "final_content_ready",
        records=[normal, _strict_culmination()],
        question_task_inventory=inventory,
        mined_types=mined_types,
        method_row_snapshot=[],
    )
    monkeypatch.setattr(
        g,
        "_reconcile_resumed_mined_types",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "a final checkpoint must not synthesize missing mined Types"
            )
        ),
    )
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a valid final checkpoint must remain API-free")
        ),
    )
    emitted: list[dict] = []

    out = g.concepts_from_mmd(
        "# Electric Current\nCircuit quantities relate charge flow to time.",
        subject="Physics",
        live=True,
        resume_checkpoint=checkpoint,
        checkpoint_callback=emitted.append,
    )

    assert out
    assert first in out[0]["concept_details"]
    assert second in out[0]["concept_details"]
    assert emitted == []


def test_method_recovery_canonicalizes_raw_math_before_strict_validation(
    monkeypatch,
):
    anchor_id = "METHOD-1DCE76C4D2"
    anchor = {
        "anchor_id": anchor_id,
        "topic_hint": "General Term",
        "source_evidence": "derive the general term from repeated addition",
        "required_formulas": [r"a_n=a+(n-1)d"],
    }
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *args, **kwargs: {
            "rows": [{
                "topic": "General Term",
                "parent_concept": "Arithmetic Progressions",
                "concept": "Deriving the General Term",
                "concept_description": (
                    r"Description: Repeated addition gives "
                    r"$a_n=a+(n-1)d$. // Error Analysis: Students may omit "
                    "the common difference while substituting values."
                ),
                "keywords": "sequence, term",
                "source_evidence": anchor_id,
            }],
        },
    )

    recovered = g._recover_method_anchor_rows_via_api(
        [anchor],
        chunk_text="Relevant source text",
        meta=g._metadata(subject="Mathematics"),
        max_attempts=1,
    )

    assert len(recovered) == 1
    assert "[Katex] a_n=a+(n-1)d [/Katex]" in (
        recovered[0]["concept_details"])
    assert g.kr.rich_text_issues(recovered[0]["concept_details"]) == []


def test_method_recovery_logs_precise_row_rejection_reason(monkeypatch):
    anchor_id = "METHOD-1DCE76C4D2"
    anchor = {
        "anchor_id": anchor_id,
        "topic_hint": "Electric Power",
        "source_evidence": "electric power is given by current times voltage",
        "required_formulas": ["P=VI"],
    }
    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *args, **kwargs: {
            "rows": [{
                "topic": "Electric Power",
                "parent_concept": "",
                "concept": "Calculating Electric Power",
                "concept_description": "Description: Power is energy per time.",
                "keywords": "power",
                "source_evidence": anchor_id,
            }],
        },
    )
    logs: list[str] = []
    monkeypatch.setattr(
        g.progress,
        "log",
        lambda message, **kwargs: logs.append(message),
    )

    with pytest.raises(RuntimeError, match=anchor_id):
        g._recover_method_anchor_rows_via_api(
            [anchor],
            chunk_text="Relevant source text",
            meta=g._metadata(subject="Physics"),
            max_attempts=1,
        )

    assert any(
        "attempt=1" in message
        and "row_index=0" in message
        and "anchor='METHOD-1DCE76C4D2'" in message
        and "missing or non-string required field(s): parent_concept" in message
        for message in logs
    )


def test_rejected_types_rewrite_keeps_non_type_repairs():
    question = (
        "Find the tenth term of the progression 3, 7, 11, 15 and explain "
        "which values were substituted."
    )
    inventory = {
        "items": [{
            "qid": "QINV-0001",
            "topic_hint": "General Term",
            "raw_task": question,
        }],
    }
    original = [_row(
        "Applying the General Term",
        "Description: The description still needs repair. // Types: "
        "Type 01: Direct substitution Case 01: Locate a specified term "
        f"Example: {question} // Error Analysis: Students may omit the "
        "common difference while substituting values.",
    )]
    candidate = [dict(original[0])]
    candidate[0]["parent_concept"] = "Term Formula"
    candidate[0]["concept_details"] = (
        "Description: The repaired description explains how the first term, "
        "common difference, and position determine a selected term. // Types: "
        "Type 01: Direct substitution Case 01: Locate a specified term "
        "Example: Use the formula. // Error Analysis: Students may omit the "
        "common difference while substituting values."
    )

    accepted = g._accept_exact_inventory_type_review(
        original, candidate, inventory)

    assert accepted is not original
    assert accepted[0]["parent_concept"] == "Term Formula"
    assert "The repaired description explains" in accepted[0]["concept_details"]
    assert f"Example: {question}" in accepted[0]["concept_details"]
    assert "Example: Use the formula." not in accepted[0]["concept_details"]
    assert not g._rendered_inventory_coverage_defects(
        accepted, inventory)["missing"]
