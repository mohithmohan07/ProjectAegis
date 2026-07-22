from app.services import concept_validator as cv
from app.services import concept_refiner as cr
from app.services import generation as g


def _rec(title, details="Description: clear teachable description here", topic="T", parent="P"):
    return {
        "topic": topic,
        "parent_concept": parent,
        "concept_title": title,
        "concept_details": details,
        "keywords": "k",
    }


def _codes(report):
    return {error["code"] for error in report["errors"]}


def _analysis_sections(details):
    return [
        (label, content)
        for label, content in cr.split_sections(details)
        if cr.is_misconception_label(label)
        or cr.is_error_analysis_label(label)
    ]


def test_duplicate_application_mistake_normalizes_to_error_analysis_and_validates():
    mistake = (
        "Students may make the mistake of dropping the negative sign during "
        "substitution."
    )
    normalized = cr.normalize_analysis_sections(
        "Description: Negative signs are retained during substitution. // "
        f"Misconceptions: {mistake} // Error Analysis: {mistake}"
    )

    assert _analysis_sections(normalized) == [("Error Analysis", mistake)]

    report = cv.validate_concept_rows([
        _rec("Signed Substitution", normalized),
    ])
    assert "missing_misconception_or_error_analysis" not in _codes(report)
    assert "misconception_framing" not in _codes(report)
    assert "error_analysis_framing" not in _codes(report)
    assert "issue_section_overlap" not in _codes(report)


def test_common_error_with_negation_is_valid_error_analysis():
    error_analysis = (
        "A common error is not converting metres to centimetres before "
        "substitution."
    )

    assert cv.is_valid_error_analysis(error_analysis)
    report = cv.validate_concept_rows([_rec(
        "Unit Conversion Before Substitution",
        "Description: Values use compatible units before substitution. // "
        f"Error Analysis: {error_analysis}",
    )])
    assert "error_analysis_framing" not in _codes(report)


def test_actorless_application_mistake_is_invalid_error_analysis():
    actorless = "Omitting the negative sign during substitution."

    assert not cv.is_valid_error_analysis(actorless)
    report = cv.validate_concept_rows([_rec(
        "Actorless Error Analysis",
        "Description: Negative signs are retained during substitution. // "
        f"Error Analysis: {actorless}",
    )])
    assert "error_analysis_framing" in _codes(report)


def test_ensure_valid_learner_analysis_reclassifies_preserves_and_exempts_culmination():
    valid_misconception = (
        "Students may misunderstand multiplication as an operation that "
        "always makes a number larger."
    )
    valid_error = (
        "Students may omit the negative sign while substituting a value."
    )
    records = [
        _rec(
            "Misclassified Analysis",
            "Description: Signed values retain their signs during substitution. // "
            "Misconceptions: Students may omit the negative sign during "
            "substitution. // Error Analysis: Students may believe that every "
            "negative input produces a negative result.",
        ),
        _rec(
            "Multiplication Scale",
            "Description: Multiplication scales a value by a factor. // "
            f"Misconceptions: {valid_misconception}",
        ),
        _rec(
            "Signed Values",
            "Description: Signed values retain their signs in a formula. // "
            f"Error Analysis: {valid_error}",
        ),
        _rec(
            "Culmination - Signed Values",
            "Description: Recap of signed-value concepts.",
            parent="Culmination",
        ),
    ]

    out = cv.ensure_valid_learner_analysis(records)

    repaired = _analysis_sections(out[0]["concept_details"])
    assert [label for label, _ in repaired] == [
        "Misconceptions",
        "Error Analysis",
    ]
    assert cv.is_valid_misconception(repaired[0][1])
    assert cv.is_valid_error_analysis(repaired[1][1])
    assert "believe that every negative input" in repaired[0][1]
    assert "omit the negative sign" in repaired[1][1]

    assert _analysis_sections(out[1]["concept_details"]) == [
        ("Misconceptions", valid_misconception),
    ]
    assert _analysis_sections(out[2]["concept_details"]) == [
        ("Error Analysis", valid_error),
    ]
    assert _analysis_sections(out[3]["concept_details"]) == []


def test_error_analysis_rejects_generic_difficulty_without_a_mistaken_action():
    for text in (
        "Students may struggle with this concept.",
        "Students may encounter difficulties.",
        "Students may use the formula.",
        "Students may apply the concept.",
        "Students may calculate the value.",
        "Students may answer incorrectly.",
        "Students may respond incorrectly.",
        "Students may perform the task incorrectly.",
        "Students may choose the wrong answer.",
        "Students may repeatedly respond incorrectly.",
        "Students may simply choose the wrong answer.",
        "Students may only understand this concept.",
    ):
        assert not cv.is_valid_error_analysis(text)


def test_error_analysis_accepts_subject_specific_actions_with_mistake_cues():
    for text in (
        "Students may paraphrase the passage instead of analysing the "
        "author's inference.",
        "Students may quote evidence without linking it to the claim.",
        "Students may return inside the loop rather than after the loop.",
    ):
        assert cv.is_valid_error_analysis(text)


def test_belief_adverbs_remain_misconceptions_and_are_reclassified():
    beliefs = (
        "Students may incorrectly assume that multiplication always makes a "
        "number larger.",
        "Students mistakenly believe that the denominator is also added.",
    )
    for text in beliefs:
        assert cv.is_valid_misconception(text)
        assert not cv.is_valid_error_analysis(text)

    record = _rec(
        "Adverb-Framed Belief",
        "Description: Multiplication scales quantities by a factor. // "
        f"Error Analysis: {beliefs[0]}",
    )
    sections = _analysis_sections(
        cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
    )
    assert sections == [("Misconceptions", beliefs[0])]


def test_misconceptions_reject_generic_objects_and_mixed_action_statements():
    for text in (
        "Students may believe this.",
        "Students may think something.",
        "Students may misunderstand fractions.",
        "Students may confuse the terms.",
    ):
        assert not cv.is_valid_misconception(text)

    belief = "Students may believe that multiplication always increases a value."
    mistake = "Students may omit the negative sign during substitution."
    mixed = f"{belief} {mistake}"
    assert not cv.is_valid_misconception(mixed)

    record = _rec(
        "Mixed Legacy Analysis",
        "Description: Scaling depends on the factor and signed inputs. // "
        f"Misconceptions: {mixed}",
    )
    assert _analysis_sections(
        cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
    ) == [
        ("Misconceptions", belief),
        ("Error Analysis", mistake),
    ]


def test_analysis_splitter_does_not_split_learner_words_inside_a_belief():
    beliefs = (
        "Students may believe children always learn at the same rate.",
        "Students may believe teachers and students have identical roles.",
    )
    for belief in beliefs:
        assert cv.is_valid_misconception(belief)
        record = _rec(
            "Belief With Learner Object",
            f"Description: Roles and learning rates vary. // Misconceptions: {belief}",
        )
        assert _analysis_sections(
            cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
        ) == [("Misconceptions", belief)]

    assert not cv.is_valid_misconception("Students may believe.")


def test_misconceptions_reject_correction_prose_after_the_false_belief():
    corrected = (
        "Students may believe denominators are added. The correct rule is to "
        "find a common denominator."
    )
    for text in (
        corrected,
        "Students may believe multiplication always increases a value. In "
        "fact, factors below one decrease it.",
        "Students may think zero has no value; instead, zero is a number.",
    ):
        assert not cv.is_valid_misconception(text)

    assert cv.is_valid_misconception(
        "Students may believe the denominator must also be added."
    )
    assert cv.is_valid_misconception(
        "Students may confuse the numerator with the denominator."
    )

    record = _rec(
        "Correction-Tailed Legacy Belief",
        "Description: Fractions use a common denominator before addition. // "
        f"Misconceptions: {corrected}",
    )
    assert _analysis_sections(
        cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
    ) == [("Misconceptions", "Students may believe denominators are added.")]


def test_overlap_removal_keeps_distinct_error_analysis_items():
    misconception = "Students may believe signs can be omitted."
    overlapping_error = "Students may omit signs during substitution."
    distinct_error = (
        "Students may reverse the operation when isolating the variable."
    )
    record = _rec(
        "Multiple Error Items",
        "Description: Signs and inverse operations require careful handling. // "
        f"Misconceptions: {misconception} // Error Analysis: "
        f"{overlapping_error} {distinct_error}",
    )

    sections = _analysis_sections(
        cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
    )

    assert sections == [
        ("Misconceptions", misconception),
        ("Error Analysis", distinct_error),
    ]


def test_validator_accepts_either_issue_section_or_both_when_distinct():
    misconception_only = cv.validate_concept_rows([_rec(
        "Multiplication Scale",
        "Description: Multiplication scales a quantity by a factor. // "
        "Misconceptions: Students may misunderstand multiplication as an "
        "operation that always makes a number larger.",
    )])
    error_only = cv.validate_concept_rows([_rec(
        "Signed Substitution",
        "Description: Substitute signed values without losing their signs. // "
        "Error Analysis: Students may omit the negative sign while "
        "substituting a value into the formula.",
    )])
    both = cv.validate_concept_rows([_rec(
        "Equivalent Fractions",
        "Description: Equivalent fractions name the same quantity. // "
        "Misconceptions: Students may believe that different denominators "
        "always represent different quantities. // "
        "Error Analysis: Students may multiply only the numerator when "
        "generating an equivalent fraction.",
    )])

    for report in (misconception_only, error_only, both):
        codes = _codes(report)
        assert "missing_misconception_or_error_analysis" not in codes
        assert "misconception_framing" not in codes
        assert "error_analysis_framing" not in codes
        assert "issue_section_overlap" not in codes


def test_validator_requires_an_issue_section_for_normal_concepts_only():
    normal = cv.validate_concept_rows([_rec("Unanalysed Concept")])
    culmination = cv.validate_concept_rows([_rec(
        "Culmination - Topic",
        "Description: Recap of the topic concepts.",
        parent="Culmination",
    )])

    assert "missing_misconception_or_error_analysis" in _codes(normal)
    assert "missing_misconception_or_error_analysis" not in _codes(culmination)


def test_validator_flags_duplicate_and_noncanonical_issue_sections():
    report = cv.validate_concept_rows([_rec(
        "Duplicate Issues",
        "Description: A sufficiently clear concept description. // "
        "Misconception: Students may believe that the first claim is true. // "
        "Misconceptions: Students may assume that the second claim is true. // "
        "Error Analysis: Students may omit the first calculation step. // "
        "Error Analysis: Students may reverse the final operation.",
    )])

    assert {
        "duplicate_misconception",
        "duplicate_error_analysis",
        "noncanonical_issue_label",
    } <= _codes(report)


def test_validator_enforces_issue_section_order_and_distinct_content():
    wrong_order = cv.validate_concept_rows([_rec(
        "Adding Fractions",
        "Description: Fractions need a common denominator before addition. // "
        "Error Analysis: Students may incorrectly add the denominators when adding fractions. // "
        "Misconceptions: Students may believe that denominators are added "
        "when adding fractions.",
    )])
    overlapping = cv.validate_concept_rows([_rec(
        "Adding Fractions",
        "Description: Fractions need a common denominator before addition. // "
        "Misconceptions: Students may believe that denominators are added "
        "when adding fractions. // "
        "Error Analysis: Students may incorrectly add the denominators when adding fractions.",
    )])

    assert "issue_section_order" in _codes(wrong_order)
    assert "issue_section_overlap" in _codes(wrong_order)
    assert "issue_section_order" not in _codes(overlapping)
    assert "issue_section_overlap" in _codes(overlapping)


def test_validator_distinguishes_beliefs_from_application_mistakes():
    report = cv.validate_concept_rows([
        _rec(
            "Procedural Text in Misconceptions",
            "Description: The sign must be retained during substitution. // "
            "Misconceptions: Students may omit the negative sign while "
            "substituting a value.",
        ),
        _rec(
            "Belief Text in Error Analysis",
            "Description: Scaling can increase or decrease a value. // "
            "Error Analysis: Students may believe that multiplication always "
            "makes a number larger.",
        ),
        _rec(
            "Generic Error Analysis",
            "Description: The method has an ordered sequence of operations. // "
            "Error Analysis: Students may make calculation errors.",
        ),
        _rec(
            "Correction in Error Analysis",
            "Description: Units are retained throughout the calculation. // "
            "Error Analysis: Students should correctly retain the units.",
        ),
    ])

    assert "misconception_framing" in {
        error["code"] for error in report["errors"] if error["row_index"] == 0
    }
    assert "error_analysis_framing" in {
        error["code"] for error in report["errors"] if error["row_index"] == 1
    }
    assert "generic_error_analysis" in {
        error["code"] for error in report["errors"] if error["row_index"] == 2
    }
    assert "error_analysis_framing" in {
        error["code"] for error in report["errors"] if error["row_index"] == 3
    }


def test_validator_detects_repeated_sibling_openers():
    report = cv.validate_concept_rows([
        _rec("Structure and Function of X"),
        _rec("Structure and Function of Y"),
    ])
    assert any(e["code"] == "repeated_sibling_opener" for e in report["errors"])


def test_repair_loop_merges_repaired_rows(monkeypatch):
    records = [
        _rec("Structure and Function of X"),
        _rec("Structure and Function of Y"),
    ]

    def fake_openai(system, user, **kw):
        return {"rows": [
            {"topic": "T", "parent_concept": "P", "concept": "X Structure",
             "concept_description": "Description: clear teachable description here",
             "keywords": "k"},
            {"topic": "T", "parent_concept": "P", "concept": "Y Function",
             "concept_description": "Description: clear teachable description here",
             "keywords": "k"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    out = g._repair_records_via_api(records, meta=g._metadata(subject="Science"), stage="final")
    assert {r["concept_title"] for r in out} == {"X Structure", "Y Function"}


def test_validator_rejects_source_artifacts_and_bad_names():
    report = cv.validate_concept_rows([
        _rec("Basic Concepts"),
        _rec("Useful Concept", "Description: See Example 19, Fig 2, Ex 1.1 and page 9 for details"),
    ])
    codes = {e["code"] for e in report["errors"]}
    assert "forbidden_name" in codes
    assert "source_artifact" in codes


def test_validator_rejects_empty_sections_and_bad_types():
    report = cv.validate_concept_rows([
        _rec("Empty Types", "Description: useful enough description // Types:"),
        _rec("Empty Misconception", "Description: useful enough description // Misconception:"),
        _rec("Case Without Type", "Description: useful enough description // Types: Case 01: Solve x"),
        _rec("Type Without Case", "Description: useful enough description // Types: Type 01: Solve"),
    ])
    codes = {e["code"] for e in report["errors"]}
    assert {"empty_types", "empty_misconception", "case_without_type", "type_without_case"} <= codes


def test_validator_rejects_culmination_before_culmination_pass():
    report = cv.validate_concept_rows([
        _rec(
            "Culmination - Early",
            "Description: Recap // Types: Type 01: Mix Case 01: Combine the listed concepts to solve a mixed review task.",
            parent="Culmination",
        ),
    ], require_culmination=False, allow_culmination=False)
    assert any(e["code"] == "culmination_too_early" for e in report["errors"])


def test_validator_requires_one_culmination_last_per_topic():
    culmination_details = (
        "Description: Recap // Types: Type 01: Mix Case 01: "
        "Combine the listed concepts to solve a mixed review task."
    )
    report = cv.validate_concept_rows([
        _rec("Skill A"),
        _rec("Culmination - Skill A", culmination_details, parent="Culmination"),
    ], require_culmination=True)
    assert report["ok"]

    bad = cv.validate_concept_rows([
        _rec("Culmination - Skill A", culmination_details, parent="Culmination"),
        _rec("Skill A"),
    ], require_culmination=True)
    assert any(e["code"] == "culmination_order" for e in bad["errors"])
