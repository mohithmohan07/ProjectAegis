"""Chapter-level refinement: continuous Type numbering + type reduction."""
from app.services import concept_refiner as cr


def _rec(title, details, topic="Topic 01"):
    return {"topic": topic, "concept_title": title, "concept_details": details, "keywords": ""}


def test_continuous_type_numbering_across_concepts():
    records = [
        _rec("A", "Description: a // Types: Type 01: X Case 01: q1 Case 02: q2 Type 02: Y Case 01: q3 // Misconception: m"),
        _rec("B", "Description: b // Types: Type 01: Z Case 01: q4 // Misconception: m"),
    ]
    out = cr.renumber_types_continuously(records)
    # A keeps Type 01, Type 02; B's single type continues to Type 03.
    assert "Type 01: X" in out[0]["concept_details"]
    assert "Type 02: Y" in out[0]["concept_details"]
    assert "Type 03: Z" in out[1]["concept_details"]
    # Case numbering restarts within each Type.
    assert "Type 01: X Case 01: q1 Case 02: q2" in out[0]["concept_details"]
    assert "Type 02: Y Case 01: q3" in out[0]["concept_details"]
    assert "Type 03: Z Case 01: q4" in out[1]["concept_details"]


def test_origin_type_identity_reuses_number_and_cases_across_topics():
    records = [
        {
            **_rec(
                "Giuseppe Mazzini and Young Italy",
                "Description: d // Types: Type 01: Write a short note on a "
                "historical person Case 01: Giuseppe Mazzini "
                "Example: Write a short note on Giuseppe Mazzini. // "
                "Misconception: m",
                topic="The Age of Revolutions: 1830-1848",
            ),
            "_origin_type_id": "TYPE-SHORT-NOTE-PERSON",
        },
        {
            **_rec(
                "Count Camillo de Cavour and Italian Unification",
                "Description: d // Types: Type 01: Write a short note on a "
                "historical person Case 01: Count Camillo de Cavour "
                "Example: Write a short note on Count Camillo de Cavour. // "
                "Misconception: m",
                topic="The Making of Germany and Italy",
            ),
            "_origin_type_id": "TYPE-SHORT-NOTE-PERSON",
        },
    ]

    out = cr.renumber_types_continuously(records)

    assert "Type 01: Write a short note" in out[0]["concept_details"]
    assert "Type 01: Write a short note" in out[1]["concept_details"]
    assert "Case 01: Giuseppe Mazzini" in out[0]["concept_details"]
    assert "Case 02: Count Camillo de Cavour" in out[1]["concept_details"]
    assert all("_origin_type_id" not in record for record in out)


def test_rendered_global_type_identity_survives_repeated_renumbering():
    records = [
        {
            **_rec(
                "Giuseppe Mazzini and Young Italy",
                "Description: d // Types: Type 01: Write a short note on a "
                "historical person Case 01: Giuseppe Mazzini // "
                "Misconception: m",
                topic="The Age of Revolutions: 1830-1848",
            ),
            "_origin_type_id": "TYPE-SHORT-NOTE-PERSON",
        },
        {
            **_rec(
                "Count Camillo de Cavour and Italian Unification",
                "Description: d // Types: Type 01: Write a short note on a "
                "historical person Case 01: Count Camillo de Cavour // "
                "Misconception: m",
                topic="The Making of Germany and Italy",
            ),
            "_origin_type_id": "TYPE-SHORT-NOTE-PERSON",
        },
    ]

    once = cr.renumber_types_continuously(records)
    twice = cr.renumber_types_continuously(once)

    assert "Type 01: Write a short note" in twice[0]["concept_details"]
    assert "Type 01: Write a short note" in twice[1]["concept_details"]
    assert "Case 01: Giuseppe Mazzini" in twice[0]["concept_details"]
    assert "Case 02: Count Camillo de Cavour" in twice[1]["concept_details"]
    assert all("_origin_type_id" not in record for record in twice)


def test_ordered_origin_type_ids_keep_multiple_types_on_one_record_distinct():
    records = [
        {
            **_rec(
                "Mazzini",
                "Description: d // Types: "
                "Type 01: Write a short note Case 01: Mazzini "
                "Type 02: Trace a historical process Case 01: Young Italy // "
                "Misconception: m",
                topic="The Age of Revolutions: 1830-1848",
            ),
            "_origin_type_id": ["TYPE-SHORT-NOTE", "TYPE-TRACE-PROCESS"],
        },
        {
            **_rec(
                "Cavour and Italian Unification",
                "Description: d // Types: "
                "Type 01: Trace a historical process Case 01: Italian "
                "unification "
                "Type 02: Write a short note Case 01: Cavour "
                "// Misconception: m",
                topic="The Making of Germany and Italy",
            ),
            "_origin_type_id": ["TYPE-TRACE-PROCESS", "TYPE-SHORT-NOTE"],
        },
    ]

    out = cr.renumber_types_continuously(records)

    assert "Type 01: Write a short note Case 01: Mazzini" in (
        out[0]["concept_details"])
    assert "Type 02: Trace a historical process Case 01: Young Italy" in (
        out[0]["concept_details"])
    assert "Type 02: Trace a historical process Case 02: Italian" in (
        out[1]["concept_details"])
    assert "Type 01: Write a short note Case 02: Cavour" in (
        out[1]["concept_details"])
    assert all("_origin_type_id" not in record for record in out)


def test_cross_topic_rows_without_origin_keep_legacy_distinct_type_numbers():
    records = [
        _rec(
            "Mazzini",
            "Description: d // Types: Type 01: Write a short note "
            "Case 01: Mazzini // Misconception: m",
            topic="The Age of Revolutions: 1830-1848",
        ),
        _rec(
            "Cavour",
            "Description: d // Types: Type 01: Write a short note "
            "Case 01: Cavour // Misconception: m",
            topic="The Making of Germany and Italy",
        ),
    ]

    out = cr.renumber_types_continuously(records)

    assert "Type 01: Write a short note Case 01: Mazzini" in (
        out[0]["concept_details"])
    assert "Type 02: Write a short note Case 01: Cavour" in (
        out[1]["concept_details"])


def test_type_refinement_numbers_examples_within_each_case():
    records = [
        _rec(
            "Linear Equations",
            "Description: d // Types: Type 01: Solve equations "
            "Case 01: Given a linear equation, isolate the unknown "
            "Example: Solve 3x + 2 = 14. "
            "Example 09: Solve 5y - 7 = 18. "
            "Case 02: Given a word problem, form and solve an equation "
            "Examples: A number increased by 5 is 12. Find the number. "
            "// Misconception: m",
        ),
    ]

    out = cr.renumber_types_continuously(records)
    details = out[0]["concept_details"]
    assert "Case 01: Given a linear equation" in details
    assert "Example 01: Solve 3x + 2 = 14." in details
    assert "Example 02: Solve 5y - 7 = 18." in details
    assert "Case 02: Given a word problem" in details
    assert "Example 01: A number increased by 5 is 12." in details
    assert "Example 09:" not in details
    assert "Examples:" not in details


def test_culmination_types_share_one_continuous_sequence():
    records = [
        _rec("A", "Description: a // Types: Type 01: X Case 01: q1 // Misconception: m"),
        _rec("Culmination - Topic 01", "Description: c // Types: Type 01: Mix Case 01: q // Misconception: m"),
        _rec("B", "Description: b // Types: Type 01: Y Case 01: q2 // Misconception: m"),
        _rec("Culmination - Topic 02", "Description: c // Types: Type 01: Mix2 Case 01: q // Misconception: m"),
    ]
    out = cr.renumber_types_continuously(records)
    # ONE chapter-wide sequence in row order: a culmination both takes the
    # next number and advances it, instead of running a parallel
    # "Miscellaneous Type NN" numbering beside the regular one.
    assert "Type 01: X" in out[0]["concept_details"]
    assert "Type 02: Mix" in out[1]["concept_details"]
    assert "Type 03: Y" in out[2]["concept_details"]
    assert "Type 04: Mix2" in out[3]["concept_details"]
    assert not any(
        "Miscellaneous" in rec["concept_details"] for rec in out
    )


def test_culmination_type_numbering_is_idempotent():
    records = [
        _rec("Culmination - T", "Description: c // Types: Type 01: M Case 01: q // Misconception: m"),
    ]
    once = cr.renumber_types_continuously(records)
    twice = cr.renumber_types_continuously(once)
    assert "Type 01: M" in twice[0]["concept_details"]
    # A legacy row carrying the old prefix is rewritten, never stacked.
    assert "Miscellaneous" not in twice[0]["concept_details"]


def test_legacy_miscellaneous_rows_join_the_continuous_sequence():
    records = [
        _rec("A", "Description: a // Types: Type 01: X Case 01: q1 // Misconception: m"),
        _rec(
            "Culmination - T",
            "Description: c // Types: Miscellaneous Type 01: M Case 01: q"
            " // Misconception: m",
        ),
    ]
    out = cr.renumber_types_continuously(records)
    assert "Type 01: X" in out[0]["concept_details"]
    assert "Type 02: M" in out[1]["concept_details"]
    assert "Miscellaneous" not in out[1]["concept_details"]


def test_reduce_types_drops_caseless_theory_block():
    # A theory concept whose Types block has no concrete Case is dropped.
    details = "Description: theory only // Types: Type 01: Definition // Misconception: m"
    out = cr.reduce_type_sections(details)
    assert "Types:" not in out
    assert "Description: theory only" in out
    assert "Misconception: m" in out


def test_reduce_types_keeps_real_types():
    details = "Description: d // Types: Type 01: Solve Case 01: compute // Misconception: m"
    assert cr.reduce_type_sections(details) == details


def test_refine_chapter_reduces_then_numbers_continuously():
    records = [
        _rec("Theory", "Description: t // Types: Type 01: Definition // Misconception: m"),
        _rec("Solve A", "Description: a // Types: Type 01: P Case 01: c1 // Misconception: m"),
        _rec("Solve B", "Description: b // Types: Type 01: Q Case 01: c2 Type 02: R Case 01: c3 // Misconception: m"),
    ]
    out = cr.refine_chapter(records)
    # Theory lost its Types block.
    assert "Types:" not in out[0]["concept_details"]
    # Numbering is continuous across the concepts that DO have types.
    assert "Type 01: P" in out[1]["concept_details"]
    assert "Type 02: Q" in out[2]["concept_details"]
    assert "Type 03: R" in out[2]["concept_details"]


def test_records_without_types_are_normalized_to_one_analysis_section():
    records = [_rec("X", "Description: only // Misconception: none")]
    out = cr.refine_chapter(records)
    sections = cr.split_sections(out[0]["concept_details"])
    assert [label for label, _ in sections if cr.is_learner_analysis_label(label)] == [
        "Misconception/ Error Analysis",
    ]
    misconception, error_analysis = cr.analysis_components(
        out[0]["concept_details"])
    assert misconception == "none"
    # The authored section alone is complete: the missing Error Analysis
    # sibling is never backfilled with deterministic filler.
    assert error_analysis == ""


def test_refine_chapter_leaves_missing_learner_analysis_for_model_repair():
    records = [
        _rec("Basic Proportionality Theorem", "Description: relates side ratios."),
        _rec("Culmination - Topic 01", "Description: Recap"),
    ]
    out = cr.refine_chapter(records)
    # A wholly missing analysis stays missing: authoring it is the Polish
    # pass's model work, and the deterministic filler is exactly what the
    # terminal gate forbids.
    normal_details = out[0]["concept_details"]
    assert "Misconception/ Error Analysis:" not in normal_details
    assert cr.analysis_components(normal_details) == ("", "")
    assert "Misconception/ Error Analysis:" not in out[1]["concept_details"]


def test_refine_chapter_accepts_either_analysis_section_or_both():
    records = [
        _rec(
            "Scale Factors",
            "Description: scale factors compare corresponding lengths. // "
            "Misconceptions: Students may believe that scaling always enlarges a figure.",
        ),
        _rec(
            "Signed Substitution",
            "Description: signed values retain their signs during substitution. // "
            "Error Analysis: Students may omit a negative sign while substituting.",
        ),
        _rec(
            "Equivalent Fractions",
            "Description: equivalent fractions name the same quantity. // "
            "Error Analysis: Students may multiply only the numerator. // "
            "Misconception: Students may think different denominators always mean different values.",
        ),
    ]

    out = cr.refine_chapter(records)

    # Either authored section alone is complete; a missing sibling is never
    # backfilled with deterministic filler, and authored text survives
    # verbatim inside the one canonical combined section.
    expected = [
        ("Students may believe that scaling always enlarges a figure.", ""),
        ("", "Students may omit a negative sign while substituting."),
        (
            "Students may think different denominators always mean "
            "different values.",
            "Students may multiply only the numerator.",
        ),
    ]
    for record, components in zip(out, expected):
        details = record["concept_details"]
        sections = cr.split_sections(details)
        assert [
            label for label, _ in sections
            if cr.is_learner_analysis_label(label)
        ] == ["Misconception/ Error Analysis"]
        assert cr.analysis_components(details) == components


def test_normalization_collapses_duplicate_cross_category_analysis():
    details = (
        "Description: retain signs during substitution. // "
        "Misconceptions: Students may omit the negative sign while substituting. // "
        "Error Analysis: Students may omit the negative sign while substituting."
    )

    out = cr.normalize_analysis_sections(details)

    assert out.count("Students may omit the negative sign") == 1
    assert "Misconception/ Error Analysis:" in out
    assert "Error Analysis:" in out
    assert not cr.analysis_components(out)[0]


def test_normalization_reclassifies_separate_legacy_mistake_without_data_loss():
    belief = "Students may believe that every scale factor enlarges a figure."
    mistake = "Students may omit the negative sign during substitution."
    details = (
        "Description: scale and sign rules depend on context. // "
        f"Misconceptions: {belief} // Misconceptions: {mistake}"
    )

    out = cr.normalize_analysis_sections(details)

    assert "Misconception/ Error Analysis:" in out
    assert f"Misconceptions: {belief}" in out
    assert f"Error Analysis: {mistake}" in out


def test_normalization_extracts_newline_combined_analysis_without_orphan_prefix():
    details = (
        "Description: Signed values retain their signs during substitution.\n"
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "a negative input always makes the result negative.; Error Analysis: "
        "Students may omit the negative sign while substituting a value."
    )

    out = cr.normalize_analysis_sections(details)

    assert out.count("Misconception/ Error Analysis:") == 1
    assert "Misconception/ //" not in out
    assert "Signed values retain their signs during substitution." in out
    misconception, error_analysis = cr.analysis_components(out)
    assert misconception and error_analysis


def test_normalization_drops_a_standalone_orphan_analysis_prefix():
    details = (
        "Description: Signed values retain their signs during substitution.\n"
        "Misconception/ // Activity/Info Hub: Practice the sign check. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "a negative input always makes the result negative.; Error Analysis: "
        "Students may omit the negative sign while substituting a value."
    )

    out = cr.normalize_analysis_sections(details)

    assert "Misconception/ //" not in out
    assert out.count("Misconception/ Error Analysis:") == 1

    before_mastery = (
        "Description: Signed values retain their signs during substitution.\n"
        "Misconception/\n"
        "Achieving Mastery: Applying the sign rule consistently. // "
        "Misconception/ Error Analysis: Misconceptions: Students may believe "
        "a negative input always makes the result negative.; Error Analysis: "
        "Students may omit the negative sign while substituting a value."
    )
    before_mastery_out = cr.normalize_analysis_sections(before_mastery)
    assert "\nMisconception/\n" not in before_mastery_out


def test_culmination_description_becomes_recap():
    records = [
        _rec("Solve A", "Description: a // Types: Type 01: P Case 01: c1 // Misconception: m"),
        _rec("Culmination - Topic 01",
             "Description: long synthesis of everything // "
             "Types: Type 01: Mixed Case 01: combine // Misconception: keep me"),
    ]
    out = cr.refine_chapter(records)
    culm = out[1]["concept_details"]
    # Description collapses to exactly "Recap".
    assert "Description: Recap" in culm
    assert "long synthesis" not in culm
    # Types continue the one chapter sequence, and Misconception survives.
    assert "Type 02: Mixed" in culm
    assert "Misconceptions: keep me" in culm
    # Regular concept keeps its continuous Type numbering.
    assert "Type 01: P" in out[0]["concept_details"]


def test_culmination_recap_when_no_description_section():
    records = [_rec("Culmination - T",
                    "Types: Type 01: Mix Case 01: q // Misconception: m")]
    out = cr.set_culmination_recap([dict(r) for r in records])
    assert out[0]["concept_details"].startswith("Description: Recap")


def test_activity_info_hub_section_order_and_append():
    details = (
        "Description: Ohm's law relates V, I and R.\n"
        "Achieving Mastery: Applying V = IR.\n"
        " // Types: Type 01: Ohm's law Case 01: Direct V/I questions "
        "Example: Find R when V is 220 V and I is 0.5 A. "
        "// Misconceptions: Students confuse R and resistivity."
    )
    with_hub = cr.append_activity_hub(
        details,
        "Activity: Activity 11.1. Set up the circuit and vary cells.",
    )
    sections = cr.split_sections(with_hub)
    labels = [label for label, _ in sections]
    assert labels == [
        "Description", "Activity/Info Hub", "Types", "Misconceptions",
    ]
    normalized = cr.normalize_misconception_sections(with_hub)
    labels2 = [label for label, _ in cr.split_sections(normalized)]
    assert labels2 == [
        "Description", "Activity/Info Hub", "Types",
        "Misconception/ Error Analysis",
    ]
    assert "Activity 11.1" in cr.activity_hub_body(normalized)
