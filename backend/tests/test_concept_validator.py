import pytest

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
    """Expose the two semantic analysis components across storage versions."""
    misconception, error_analysis = cr.analysis_components(details)
    return [
        *([("Misconceptions", misconception)] if misconception else []),
        *([("Error Analysis", error_analysis)] if error_analysis else []),
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


def test_validator_rejects_only_high_confidence_truncated_description_clause():
    broken = cv.validate_concept_rows([_rec(
        "Scientific Inquiry",
        "Description: Students observe carefully, record evidence, and use it "
        "to explain what they.",
    )])
    complete = cv.validate_concept_rows([
        _rec(
            "Scientific Inquiry",
            "Description: Students observe carefully, record evidence, and "
            "use it to explain what they observe.",
        ),
        _rec(
            "Dependence",
            "Description: The result depends on it.",
        ),
    ])

    assert "description_truncated_clause" in _codes(broken)
    assert "description_truncated_clause" not in _codes(complete)


def test_ensure_valid_learner_analysis_preserves_authored_content_and_exempts_culmination():
    """API-authored analysis is authoritative: the final boundary only
    canonicalizes formatting into one combined section — it never drops a
    statement, rewrites one, or backfills a missing sibling with filler.
    Culminations keep no analysis section."""
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

    # Legacy cross-filed statements are re-labelled by normalization without
    # losing a word: the mistake filed under Misconceptions joins the authored
    # Error Analysis text verbatim.
    assert _analysis_sections(out[0]["concept_details"]) == [(
        "Error Analysis",
        "Students may believe that every negative input produces a negative "
        "result. Students may omit the negative sign during substitution.",
    )]

    # A single authored section stays alone: no deterministic filler is
    # inserted for the missing sibling.
    assert _analysis_sections(out[1]["concept_details"]) == [
        ("Misconceptions", valid_misconception),
    ]
    assert _analysis_sections(out[2]["concept_details"]) == [
        ("Error Analysis", valid_error),
    ]
    assert _analysis_sections(out[3]["concept_details"]) == []


@pytest.mark.parametrize(
    ("title", "misconception", "error_analysis"),
    [
        (
            "Scientific Inquiry as an Evolving Investigation Cycle",
            "Students may assume that science only means memorising "
            "established facts or using a sophisticated laboratory",
            "Students may ask an unfocused question, collect observations "
            "without a planned test, or treat one result as a final "
            "explanation.",
        ),
        (
            "Microorganisms, Health, and Infection Control",
            "Students may believe that every microorganism causes disease or "
            "that vaccines cure an infection immediately",
            "Students may confuse preventive vaccination with treatment, or "
            "stop prescribed medicine without considering the infection and "
            "medical guidance.",
        ),
        (
            "Moon Phases and Calendars",
            "Students may believe that Moon phases are caused by Earth's "
            "shadow every night or by the Moon changing shape",
            "Students may draw the Sun, Earth, and Moon in positions that "
            "cannot produce the observed illuminated portion.",
        ),
        (
            "Earth's Habitability and Human-driven Climate Change",
            "Students may assume that Earth's habitability depends only on "
            "its distance from the Sun or that climate change is a single "
            "day's weather",
            "Students may confuse ultraviolet shielding with oxygen supply, "
            "or infer long-term climate trends from one short-term "
            "temperature observation.",
        ),
    ],
)
def test_final_analysis_normalization_preserves_specific_science_errors(
    title,
    misconception,
    error_analysis,
):
    record = _rec(
        title,
        "Description: A source-faithful concept explanation. // "
        "Misconception/ Error Analysis: "
        f"Misconceptions: {misconception}; "
        f"Error Analysis: {error_analysis}",
    )

    out = cv.ensure_valid_learner_analysis([record])[0]

    assert cr.analysis_components(out["concept_details"]) == (
        misconception,
        error_analysis,
    )
    assert cv.is_valid_error_analysis(error_analysis)


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
        "Students may treat political authority as hereditary rather than "
        "civic.",
        "Students may change two variables at once and attribute the outcome "
        "to only one factor.",
        "Students may record only the final observation and omit the trial "
        "conditions.",
        "Students may draw a conclusion after a single trial.",
        "Students may compare unlike observations as though they came from "
        "controlled trials.",
    ):
        assert cv.is_valid_error_analysis(text)


def test_error_analysis_accepts_prompted_actions_only_with_named_consequences():
    for text in (
        "Students may count the endpoint twice, increasing the interval "
        "total by one.",
        "Students may add the numerator and denominator before simplifying, "
        "producing an incorrect fraction.",
        "Students may divide by the scale factor before converting units, "
        "producing the wrong magnitude.",
        "Students may group nonadjacent clauses before checking their link, "
        "resulting in an invalid interpretation.",
    ):
        assert cv.is_valid_error_analysis(text)

    for text in (
        "Students may count the endpoints.",
        "Students may add the numerator and denominator.",
        "Students may divide by the scale factor.",
        "Students may group the clauses.",
    ):
        assert not cv.is_valid_error_analysis(text)


def test_error_analysis_rejects_correct_procedural_action_without_a_defect():
    assert not cv.is_valid_error_analysis(
        "Students may record all observations accurately.")


def test_concise_numeric_yes_no_question_is_not_a_stub():
    assert not cv._example_too_short("Is 9 a cube?")
    assert cv._example_too_short("Is this correct?")


def test_belief_adverbs_classify_as_misconceptions_but_authored_placement_wins():
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
    # The API filed this belief under Error Analysis; authored placement is
    # authoritative, so it stays there verbatim and no Misconceptions sibling
    # is fabricated.
    assert sections == [("Error Analysis", beliefs[0])]


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
    # Authored analysis is preserved verbatim: the mixed statement is not
    # split into belief + mistake and stays under its authored label.
    assert _analysis_sections(
        cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
    ) == [("Misconceptions", mixed)]


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
        sections = _analysis_sections(
            cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
        )
        # The belief survives whole under its authored label; no fallback
        # Error Analysis sibling is inserted.
        assert sections == [("Misconceptions", belief)]

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
    sections = _analysis_sections(
        cv.ensure_valid_learner_analysis([record])[0]["concept_details"]
    )
    # The correction tail is authored content and is kept verbatim (the text
    # fails the misconception predicate, so normalization files the whole
    # statement under Error Analysis) — it is never trimmed down to the
    # bare belief.
    assert sections == [("Error Analysis", corrected)]


def test_overlapping_authored_error_analysis_items_are_all_preserved():
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

    # The overlap filter no longer drops authored statements: every error
    # item the model wrote survives verbatim; overlap quality is the terminal
    # gate's judgment, not a deterministic filter's.
    assert sections == [
        ("Misconceptions", misconception),
        ("Error Analysis", f"{overlapping_error} {distinct_error}"),
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


def test_description_section_references_are_errors_but_decimals_are_allowed():
    report = cv.validate_concept_rows([
        _rec(
            "Section Leak",
            "Description: Section 5.3 introduces the finite-sum rule.",
        ),
        _rec(
            "Section Symbol Leak",
            "Description: The derivation follows \u00a7 4.2 of the source.",
        ),
        _rec(
            "Legitimate Decimals",
            "Description: A 2.5 ohm resistor has cross-section 1.2 square "
            "centimetres, and the scale factor is 1.5.",
        ),
    ])

    section_errors = [
        error for error in report["errors"]
        if error["code"] == "section_number_in_description"
    ]
    assert {
        (error["row_index"], error["severity"])
        for error in section_errors
    } == {(0, "error"), (1, "error")}


def test_validator_rejects_copied_source_prose_only_in_descriptions():
    source = (
        "A nation state is built when people share a sense of collective "
        "identity and decide to live together under common political institutions."
    )
    copied = cv.validate_concept_rows([
        _rec(
            "Nation-state Formation",
            "Description: " + source + " // Error Analysis: Students may "
            "omit the role of shared political institutions when explaining "
            "national identity.",
        ),
    ], source_text=source)
    assert "verbatim_source_description" in _codes(copied)

    question_only = cv.validate_concept_rows([
        _rec(
            "Nation-state Formation",
            "Description: National identity connects collective belonging "
            "with the choice to organise political life together. // "
            "Types: Type 01: Explaining collective political identity "
            "Case 01: Relating shared identity to common institutions "
            "Example 01: " + source + " // Error Analysis: Students may "
            "omit the role of shared political institutions when explaining "
            "national identity.",
        ),
    ], source_text=source)
    assert "verbatim_source_description" not in _codes(question_only)


def test_validator_rejects_empty_sections_and_bad_types():
    report = cv.validate_concept_rows([
        _rec("Empty Types", "Description: useful enough description // Types:"),
        _rec("Empty Misconception", "Description: useful enough description // Misconception:"),
        _rec("Case Without Type", "Description: useful enough description // Types: Case 01: Solve x"),
        _rec("Type Without Case", "Description: useful enough description // Types: Type 01: Solve"),
    ])
    codes = {e["code"] for e in report["errors"]}
    assert {"empty_types", "empty_misconception", "case_without_type", "type_without_case"} <= codes


def test_strict_type_hierarchy_requires_defined_cases_and_numbered_examples():
    invalid = _rec(
        "Equation Practice",
        "Description: A linear equation has one unknown. // "
        "Types: Type 01: Solving Linear Equations "
        "Case 01: Solve 3x + 2 = 14. Example: Solve 3x + 2 = 14.",
    )
    report = cv.validate_concept_rows(
        [invalid], strict_type_hierarchy=True)
    assert {"case_question_not_definition", "example_numbering"} <= _codes(report)

    valid = _rec(
        "Equation Practice",
        "Description: A linear equation has one unknown. // "
        "Types: Type 01: Solving Linear Equations "
        "Case 01: Given a linear equation with one unknown, isolate the "
        "unknown using inverse operations "
        "Example 01: Solve 3x + 2 = 14. "
        "Example 02: Solve 5y - 7 = 18.",
    )
    valid_report = cv.validate_concept_rows(
        [valid], strict_type_hierarchy=True)
    hierarchy_codes = {
        "missing_case_definition", "case_without_example",
        "case_question_not_definition", "example_numbering",
    }
    assert not (_codes(valid_report) & hierarchy_codes)
    assert "source_artifact" not in _codes(valid_report)


def test_strict_type_hierarchy_requires_a_case_for_every_type_and_real_definition():
    orphan_type = _rec(
        "Equation Practice",
        "Description: A linear equation has one unknown and can be solved "
        "through inverse operations. // "
        "Types: Type 01: Direct equations "
        "Case 01: Given one linear equation, isolate the unknown with inverse "
        "operations. Example 01: Solve 3x + 2 = 14. "
        "Type 02: Word-problem equations.",
    )
    orphan_report = cv.validate_concept_rows(
        [orphan_type], strict_type_hierarchy=True)
    assert "type_without_case" in _codes(orphan_report)

    generic_case = _rec(
        "Equation Practice",
        "Description: A linear equation has one unknown and can be solved "
        "through inverse operations. // "
        "Types: Type 01: Direct equations "
        "Case 01: Practice set Example 01: Solve 3x + 2 = 14.",
    )
    generic_report = cv.validate_concept_rows(
        [generic_case], strict_type_hierarchy=True)
    assert "generic_case_definition" in _codes(generic_report)


def test_strict_type_hierarchy_rejects_empty_task_container_case_titles():
    generic_titles = [
        "Use the given information",
        "Answer the question",
        "Checkpoint",
        "Activity",
        "Exercise 5.2",
        "Source inventory task",
        "Task container",
        "Discuss",
    ]
    rows = [
        _rec(
            f"Generic Case {index}",
            "Description: Voltage depends on current and resistance. // "
            "Types: Type 01: Explaining voltage changes "
            f"Case 01: {title} "
            "Example 01: Explain how voltage changes when current doubles "
            "at constant resistance.",
        )
        for index, title in enumerate(generic_titles)
    ]

    report = cv.validate_concept_rows(rows, strict_type_hierarchy=True)
    generic_rows = {
        error["row_index"] for error in report["errors"]
        if error["code"] == "generic_case_definition"
    }
    assert generic_rows == set(range(len(generic_titles)))


def test_strict_type_hierarchy_allows_a_meaningful_imperative_case_title():
    row = _rec(
        "Voltage Relationships",
        "Description: Ohm's law relates voltage, current, and resistance. // "
        "Types: Type 01: Explaining variable relationships "
        "Case 01: Discuss why voltage changes with resistance "
        "Example 01: Explain why the voltage across a resistor increases "
        "when its resistance rises at constant current.",
    )

    report = cv.validate_concept_rows([row], strict_type_hierarchy=True)

    assert not (
        _codes(report)
        & {"generic_case_definition", "case_question_not_definition"}
    )


def test_strict_type_hierarchy_rejects_obvious_case_example_family_mismatch():
    rows = [
        _rec(
            "Resistor Combinations",
            "Description: Resistor topology determines equivalent resistance. // "
            "Types: Type 01: Equivalent-resistance calculations "
            "Case 01: Series resistor combinations "
            "Example 01: Calculate the equivalent resistance of 6 ohm and "
            "3 ohm resistors connected in parallel.",
        ),
        _rec(
            "Arithmetic Mean Tasks",
            "Description: Arithmetic means divide an interval into equal "
            "additive steps. // "
            "Types: Type 01: Working with fixed endpoints "
            "Case 01: Finding arithmetic means between fixed endpoints "
            "Example 01: Construct an arithmetic progression with first "
            "term 4 and common difference 3.",
        ),
        _rec(
            "Aligned Resistor Combination",
            "Description: Resistors in series carry the same current. // "
            "Types: Type 01: Equivalent-resistance calculations "
            "Case 01: Series resistor combinations "
            "Example 01: Calculate the equivalent resistance of 6 ohm and "
            "3 ohm resistors connected in series.",
        ),
    ]

    report = cv.validate_concept_rows(rows, strict_type_hierarchy=True)
    mismatch_rows = {
        error["row_index"] for error in report["errors"]
        if error["code"] == "case_example_semantic_mismatch"
    }

    assert mismatch_rows == {0, 1}


def test_strict_type_hierarchy_rejects_empty_generic_and_duplicate_type_titles():
    case = (
        "Case 01: Given a linear equation, isolate its unknown using inverse "
        "operations. Example 01: Solve 3x + 2 = 14."
    )
    report = cv.validate_concept_rows(
        [
            _rec(
                "Empty Type Title",
                f"Description: Linear equations preserve equality. // "
                f"Types: Type 01: {case}",
            ),
            _rec(
                "Generic Type Title",
                f"Description: Linear equations preserve equality. // "
                f"Types: Type 01: Source inventory task {case}",
            ),
            _rec(
                "Duplicate Type Titles",
                f"Description: Linear equations preserve equality. // "
                f"Types: Type 01: Direct equations {case} "
                f"Type 02: direct   equations. {case}",
            ),
        ],
        strict_type_hierarchy=True,
    )

    assert "missing_type_definition" in {
        error["code"] for error in report["errors"]
        if error["row_index"] == 0
    }
    assert "generic_type_definition" in {
        error["code"] for error in report["errors"]
        if error["row_index"] == 1
    }
    assert "duplicate_type_definition" in {
        error["code"] for error in report["errors"]
        if error["row_index"] == 2
    }

    legacy_report = cv.validate_concept_rows([
        _rec(
            "Legacy Generic Type",
            f"Description: Linear equations preserve equality. // "
            f"Types: Type 01: Assessment pattern {case}",
        ),
    ])
    assert "generic_type_definition" not in _codes(legacy_report)


def test_strict_type_titles_are_unique_across_normal_concepts_in_each_topic():
    def row(title, topic):
        return _rec(
            title,
            "Description: An equation remains balanced when the same inverse "
            "operation is applied to both sides. // "
            "Types: Type 01: Solving by inverse operations "
            "Case 01: Isolating an unknown with one inverse operation "
            "Example 01: Solve 3x + 2 = 14.",
            topic=topic,
        )

    report = cv.validate_concept_rows(
        [
            row("One-step Equations", "Linear Equations"),
            row("Two-step Equations", "  linear   equations  "),
            row("Quadratic Rearrangement", "Quadratic Equations"),
        ],
        strict_type_hierarchy=True,
    )

    duplicate_rows = [
        error["row_index"] for error in report["errors"]
        if error["code"] == "duplicate_type_definition"
    ]
    assert duplicate_rows == [1]


def test_strict_type_hierarchy_rejects_all_named_generic_type_variants():
    generic_titles = [
        "Assessment pattern",
        "Source inventory task",
        "Answering checkpoint question",
        "Practice",
        "Questions",
        "Problems",
        "Examples",
        "Exercises",
    ]
    rows = [
        _rec(
            f"Concept {index}",
            "Description: The method has a reusable task structure. // "
            f"Types: Type 01: {title} "
            "Case 01: Given a new input, apply the method and justify each "
            "step. Example 01: Apply the method to input 12 and explain the "
            "result.",
        )
        for index, title in enumerate(generic_titles, start=1)
    ]

    report = cv.validate_concept_rows(rows, strict_type_hierarchy=True)

    generic_rows = {
        error["row_index"] for error in report["errors"]
        if error["code"] == "generic_type_definition"
    }
    assert generic_rows == set(range(len(generic_titles)))


def test_strict_mastery_requires_one_canonical_terminal_description_line():
    valid_details = (
        "Description: A linear equation preserves equality while inverse "
        "operations isolate the unknown.\n"
        "Achieving Mastery: Solving unfamiliar linear equations and "
        "justifying each inverse operation. // "
        "Error Analysis: Students may reverse an operation without applying "
        "it to both sides."
    )
    valid_report = cv.validate_concept_rows(
        [_rec("Linear Equations", valid_details)],
        strict_mastery_statement=True,
    )
    mastery_codes = {
        "missing_mastery_statement",
        "mastery_statement_format",
        "mastery_statement_not_substantive",
        "duplicate_mastery_statement",
        "mastery_marker_outside_description",
    }
    assert not (_codes(valid_report) & mastery_codes)

    culmination_report = cv.validate_concept_rows(
        [_rec(
            "Culmination - Linear Equations",
            "Description: Recap of Linear Equations.",
            parent="Culmination",
        )],
        strict_mastery_statement=True,
    )
    assert not (_codes(culmination_report) & mastery_codes)


def test_strict_mastery_reports_missing_malformed_duplicate_and_stray_markers():
    report = cv.validate_concept_rows(
        [
            _rec(
                "Missing Mastery",
                "Description: A complete explanation without a mastery line.",
            ),
            _rec(
                "Inline Mastery",
                "Description: A complete explanation. Achieving Mastery: "
                "Applying the method independently.",
            ),
            _rec(
                "Thin Mastery",
                "Description: A complete explanation.\n"
                "Achieving Mastery: Doing it well.",
            ),
            _rec(
                "Duplicate Mastery",
                "Description: A complete explanation.\n"
                "Achieving Mastery: Applying the method independently.\n"
                "Achieving Mastery: Explaining each step independently.",
            ),
            _rec(
                "Stray Mastery",
                "Description: A complete explanation.\n"
                "Achieving Mastery: Applying the method independently. // "
                "Types: Type 01: A meaningful task family Case 01: Given a "
                "new input, apply the method. Example 01: Calculate the "
                "result for input 12. Achieving Mastery: Ignore this marker.",
            ),
        ],
        strict_mastery_statement=True,
    )
    row_codes = [
        {
            error["code"] for error in report["errors"]
            if error["row_index"] == row_index
        }
        for row_index in range(5)
    ]

    assert "missing_mastery_statement" in row_codes[0]
    assert "mastery_statement_format" in row_codes[1]
    assert "mastery_statement_not_substantive" in row_codes[2]
    assert "duplicate_mastery_statement" in row_codes[3]
    assert {
        "duplicate_mastery_statement",
        "mastery_marker_outside_description",
    } <= row_codes[4]

    legacy_report = cv.validate_concept_rows([
        _rec(
            "Legacy Missing Mastery",
            "Description: A complete explanation without a mastery line.",
        ),
    ])
    assert "missing_mastery_statement" not in _codes(legacy_report)


def test_strict_culmination_recap_names_every_normal_topic_concept():
    valid = cv.validate_concept_rows(
        [
            _rec("Linear   Growth", topic="Sequences"),
            _rec("Finite Sum Rule", topic="Sequences"),
            _rec(
                "Culmination - Sequences",
                "Description: Recap of linear growth and FINITE SUM RULE.",
                topic="Sequences",
                parent="Culmination",
            ),
        ],
        strict_culmination_recap=True,
    )
    assert not ({
        "culmination_recap_format",
        "culmination_recap_missing_concepts",
    } & _codes(valid))

    missing = cv.validate_concept_rows(
        [
            _rec("Linear Growth", topic="Sequences"),
            _rec("Finite Sum Rule", topic="Sequences"),
            _rec(
                "Culmination - Sequences",
                "Description: Recap of Linear Growth.",
                topic="Sequences",
                parent="Culmination",
            ),
        ],
        strict_culmination_recap=True,
    )
    assert "culmination_recap_missing_concepts" in _codes(missing)
    missing_error = next(
        error for error in missing["errors"]
        if error["code"] == "culmination_recap_missing_concepts"
    )
    assert "Finite Sum Rule" in missing_error["message"]

    malformed = cv.validate_concept_rows(
        [
            _rec("Linear Growth", topic="Sequences"),
            _rec(
                "Culmination - Sequences",
                "Description: Summary of Linear Growth.",
                topic="Sequences",
                parent="Culmination",
            ),
        ],
        strict_culmination_recap=True,
    )
    assert "culmination_recap_format" in _codes(malformed)

    legacy = cv.validate_concept_rows([
        _rec(
            "Culmination - Sequences",
            "Description: Recap",
            topic="Sequences",
            parent="Culmination",
        ),
    ])
    assert "culmination_recap_format" not in _codes(legacy)


def test_strict_culmination_recap_ignores_canonical_katex_wrappers():
    title = r"Use the Finite-sum Formula S_n = \frac{n}{2}(a+l)"
    report = cv.validate_concept_rows(
        [
            _rec(title, topic="Sequences"),
            _rec(
                f"Culmination - {title}",
                "Description: Recap of Use the Finite-sum Formula "
                r"[Katex] S_n = \frac{n}{2}(a+l) [/Katex].",
                topic="Sequences",
                parent="Culmination",
            ),
        ],
        strict_culmination_recap=True,
    )

    assert "culmination_recap_missing_concepts" not in _codes(report)


def test_strict_culmination_recap_does_not_count_nested_title_as_separate():
    report = cv.validate_concept_rows(
        [
            _rec("Arithmetic Progression", topic="Sequences"),
            _rec("Progression", topic="Sequences"),
            _rec(
                "Culmination - Sequences",
                "Description: Recap of Arithmetic Progression.",
                topic="Sequences",
                parent="Culmination",
            ),
        ],
        strict_culmination_recap=True,
    )

    error = next(
        error for error in report["errors"]
        if error["code"] == "culmination_recap_missing_concepts"
    )
    assert (
        "Recap of Arithmetic Progression and Progression."
        in error["message"]
    )


def test_strict_analysis_requires_one_exact_combined_section():
    valid = _rec(
        "Signed Substitution",
        "Description: Signed values retain their signs during substitution "
        "into a formula. // Misconception/ Error Analysis: "
        "Misconceptions: Students may believe a negative input always makes "
        "the final result negative.; Error Analysis: Students may omit a "
        "negative sign while substituting a value.",
    )
    valid_report = cv.validate_concept_rows(
        [valid], strict_analysis_section=True)
    assert not ({
        "analysis_section_format", "missing_misconception",
        "missing_error_analysis",
    } & _codes(valid_report))

    legacy = _rec(
        "Signed Substitution",
        "Description: Signed values retain their signs during substitution "
        "into a formula. // Misconceptions: Students may believe a negative "
        "input always makes the final result negative. // Error Analysis: "
        "Students may omit a negative sign while substituting a value.",
    )
    legacy_report = cv.validate_concept_rows(
        [legacy], strict_analysis_section=True)
    assert "analysis_section_format" in _codes(legacy_report)

    malformed = _rec(
        "Signed Substitution",
        "Description: Signed values retain their signs during substitution "
        "into a formula. // Misconception/ Error Analysis: Error Analysis: "
        "Students may omit a negative sign while substituting a value.; "
        "Misconceptions: Students may believe a negative input always makes "
        "the final result negative.",
    )
    malformed_report = cv.validate_concept_rows(
        [malformed], strict_analysis_section=True)
    assert "analysis_section_format" in _codes(malformed_report)

    orphan_prefix = _rec(
        "Signed Substitution",
        "Description: Signed values retain their signs during substitution.\n"
        "Misconception/ // Misconception/ Error Analysis: "
        "Misconceptions: Students may believe a negative input always makes "
        "the final result negative.; Error Analysis: Students may omit a "
        "negative sign while substituting a value.",
    )
    orphan_report = cv.validate_concept_rows(
        [orphan_prefix], strict_analysis_section=True)
    assert "analysis_section_format" in _codes(orphan_report)


def test_strict_analysis_accepts_either_section_alone():
    """Reviewer contract: Misconception OR Error Analysis is sufficient;
    both appear only when they carry genuinely different insight."""
    misconception_only = _rec(
        "Signed Substitution",
        "Description: Signed values retain their signs during substitution "
        "into a formula. // Misconception/ Error Analysis: "
        "Misconceptions: Students may believe a negative input always makes "
        "the final result negative.",
    )
    report = cv.validate_concept_rows(
        [misconception_only], strict_analysis_section=True)
    assert not ({
        "analysis_section_format", "missing_learner_analysis",
    } & _codes(report))

    error_only = _rec(
        "Signed Substitution",
        "Description: Signed values retain their signs during substitution "
        "into a formula. // Misconception/ Error Analysis: Error Analysis: "
        "Students may omit a negative sign while substituting a value "
        "instead of carrying it through the calculation.",
    )
    report = cv.validate_concept_rows(
        [error_only], strict_analysis_section=True)
    assert not ({
        "analysis_section_format", "missing_learner_analysis",
    } & _codes(report))


def test_strict_figure_examples_require_matching_canonical_image_tag():
    assert cv._example_figure_ids("Refer to Fig．1（a） and Fig. 11.4.") == [
        "1(a)", "11.4",
    ]
    prefix = (
        "Description: Circuit diagrams make the electrical relationships "
        "visible to a reader. // Types: Type 01: Circuit interpretation "
        "Case 01: Given a labelled circuit diagram, identify the stated "
        "relationship. Example 01: Refer to Fig. 11.4 and state the "
        "observation."
    )
    suffix = (
        " // Misconception/ Error Analysis: Misconceptions: Students may "
        "believe every circuit diagram shows the same current path.; Error "
        "Analysis: Students may overlook the labelled component while "
        "tracing the circuit."
    )
    valid = _rec(
        "Circuit Interpretation",
        prefix + ' [img src="https://example.test/11-4.png" '
        'alt="Fig. 11.4 Circuit observation"]' + suffix,
    )
    valid_report = cv.validate_concept_rows(
        [valid], strict_type_hierarchy=True, strict_analysis_section=True)
    assert not ({
        "figure_reference_without_image", "figure_reference_image_mismatch",
    } & _codes(valid_report))

    missing = _rec("Circuit Interpretation", prefix + suffix)
    missing_report = cv.validate_concept_rows(
        [missing], strict_type_hierarchy=True, strict_analysis_section=True)
    assert "figure_reference_without_image" in _codes(missing_report)

    mismatch = _rec(
        "Circuit Interpretation",
        prefix + ' [img src="https://example.test/11-5.png" '
        'alt="Fig. 11.5 Different circuit"]' + suffix,
    )
    mismatch_report = cv.validate_concept_rows(
        [mismatch], strict_type_hierarchy=True, strict_analysis_section=True)
    assert "figure_reference_image_mismatch" in _codes(mismatch_report)


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
        _rec("Skill B"),
        _rec("Culmination - Skill A", culmination_details, parent="Culmination"),
    ], require_culmination=True)
    assert report["ok"]

    bad = cv.validate_concept_rows([
        _rec("Culmination - Skill A", culmination_details, parent="Culmination"),
        _rec("Skill A"),
        _rec("Skill B"),
    ], require_culmination=True)
    assert any(e["code"] == "culmination_order" for e in bad["errors"])


def test_single_concept_topic_needs_no_culmination():
    # A culmination consolidates several concepts; with one concept its recap
    # would only restate it, so generation omits the row and validation must
    # not demand it back.
    report = cv.validate_concept_rows(
        [_rec("Skill A")], require_culmination=True)
    assert report["ok"]
    assert not any(
        e["code"] == "culmination_count" for e in report["errors"]
    )


def test_multi_concept_topic_still_requires_its_culmination():
    report = cv.validate_concept_rows([
        _rec("Skill A"),
        _rec("Skill B"),
    ], require_culmination=True)
    assert any(e["code"] == "culmination_count" for e in report["errors"])
