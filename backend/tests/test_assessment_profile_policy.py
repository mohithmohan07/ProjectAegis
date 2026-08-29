"""Profile-owned assessment category, mark, and duration contracts."""
from __future__ import annotations

import pytest

from app import bulk_import as bi
from app.services import assessment_profile as profile


MATH_META = {
    "board": "MSBSHSE",
    "grade": "06",
    "subject": "Mathematics",
}

ENGLISH_META = {
    "board": "MSBSHSE",
    "grade": "06",
    "subject": "English",
}

SCIENCE_META = {
    "board": "MSBSHSE",
    "grade": "06",
    "subject": "Science",
}


def test_reference_profile_remains_pinned_and_msbshse_run_widens_it() -> None:
    assert profile.sheet_kinds() == ("objective", "descriptive")
    assert profile.forced_blank_fields() == (
        "chapter_duration", "question_disclaimer",
    )

    run_profile = profile.resolve_for_metadata(None, MATH_META)
    assert profile.sheet_kinds(run_profile) == (
        "objective", "descriptive", "subjective",
    )
    assert profile.forced_blank_fields(run_profile) == (
        "question_disclaimer",
    )


def test_generic_format_fallback_is_the_existing_cms_vocabulary() -> None:
    metadata = {"board": "State Board", "grade": "6", "subject": "Science"}

    assert profile.assessment_format_policy(metadata=metadata)["policy_id"] == (
        "generic-cms"
    )
    assert profile.question_categories(metadata=metadata) == {
        sheet_kind: tuple(categories)
        for sheet_kind, categories in bi.QUESTION_CATEGORIES.items()
    }
    assert profile.question_marks_rule(
        metadata=metadata,
        sheet_kind="descriptive",
        question_category="Long Answer",
    ) == {}
    assert profile.question_duration_minutes(
        metadata=metadata,
        sheet_kind="descriptive",
        question_category="Long Answer",
        difficulty="Moderate",
    ) is None


def test_msbshse_grade_6_science_keeps_the_generic_run_profile() -> None:
    run_profile = profile.resolve_for_metadata(None, SCIENCE_META)

    assert profile.sheet_kinds(run_profile) == (
        "objective", "descriptive",
    )
    assert profile.forced_blank_fields(run_profile) == (
        "chapter_duration", "question_disclaimer",
    )
    assert profile.master_workbook_contract(
        run_profile, learning_phase="Post",
    )["contract_id"] == "reference-master-1"
    assert profile.assessment_format_policy(run_profile)["policy_id"] == (
        "generic-cms"
    )


def test_msbshse_grade_6_mathematics_policy_is_exact_and_narrow() -> None:
    policy = profile.assessment_format_policy(metadata=MATH_META)

    assert policy["policy_id"] == "msbshse-grade-6-mathematics-2026-08-27"
    assert "metadata_match" not in policy
    assert profile.question_categories(metadata=MATH_META) == {
        "objective": (
            "Multiple Choice Question",
            "Match the Following",
            "True or False",
            "Fill in the blanks",
        ),
        "subjective": ("Fill in the blanks",),
        "descriptive": (
            "Very Short Answer Questions",
            "Short Answer Type (2 Marks)",
            "Short Answer Type (3 Marks)",
            "Long Answer Type (4 Marks)",
            "Long Answer Type (5 Marks)",
        ),
    }
    assert "Assertion & Reasons" not in str(policy)
    assert "Case Based Questions" not in str(policy)


def test_msbshse_grade_6_english_policy_is_exact_taxonomy_with_contracts(
) -> None:
    """The Grade-6 taxonomy stays the audit's closed set; since the owner's
    Question Duration Matrix (2026-08-29) every category also carries its
    marks and duration contract instead of an empty rule."""

    policy = profile.assessment_format_policy(metadata=ENGLISH_META)

    assert policy["policy_id"] == "msbshse-grade-6-english-2026-08-29"
    assert "metadata_match" not in policy
    assert profile.question_categories(metadata=ENGLISH_META) == {
        "objective": ("Multiple Choice Question",),
        "subjective": ("Fill in the Blanks",),
        "descriptive": (
            "Very Short Answer Questions",
            "Short Answer Type (2 Marks)",
            "Short Answer Type (3 Marks)",
            "Long Answer Type (4 Marks)",
            "Composition Writing",
        ),
    }
    for sheet_kind, categories in policy["formats_by_sheet"].items():
        for category, rule in categories.items():
            assert set(rule) == {"marks", "duration"}, (sheet_kind, category)
    # The board-wide English additions stay OUT of Grade 6 (scope ruling
    # 2026-08-29: grade-scoped policies layer over the matrix).
    assert "Reading Comprehension" not in str(policy)
    assert "Extract Based Question" not in str(policy)


def test_msbshse_grade_6_english_carries_the_english_matrix_contract() -> None:
    assert profile.question_marks_rule(
        metadata=ENGLISH_META,
        sheet_kind="descriptive",
        question_category="Long Answer Type (4 Marks)",
    ) == {"mode": "fixed", "allowed": (4,)}
    assert profile.question_duration_minutes(
        metadata=ENGLISH_META,
        sheet_kind="descriptive",
        question_category="Long Answer Type (4 Marks)",
        difficulty="High",
    ) == 7.0
    # Marks-tiered categories resolve through the mark tier.
    assert profile.question_duration_minutes(
        metadata=ENGLISH_META,
        sheet_kind="descriptive",
        question_category="Composition Writing",
        difficulty="Moderate",
        marks=10,
    ) == 10.0
    # A Mathematics-only category never leaks into the English taxonomy.
    assert profile.question_format_rule(
        metadata=ENGLISH_META,
        sheet_kind="descriptive",
        question_category="Long Answer Type (5 Marks)",
    ) == {}


@pytest.mark.parametrize(
    ("metadata", "policy_id"),
    [
        pytest.param(
            MATH_META,
            "msbshse-grade-6-mathematics-2026-08-27",
            id="mathematics",
        ),
        pytest.param(
            ENGLISH_META,
            "msbshse-grade-6-english-2026-08-29",
            id="english",
        ),
    ],
)
def test_resolved_profile_carries_format_policy_selectors(
    metadata: dict[str, str], policy_id: str,
) -> None:
    run_profile = profile.resolve_for_metadata(None, metadata)

    assert profile.assessment_format_policy(run_profile)["policy_id"] == (
        policy_id
    )


def test_explicit_format_policy_metadata_is_authoritative() -> None:
    math_profile = profile.resolve_for_metadata(None, MATH_META)

    assert profile.assessment_format_policy(
        math_profile, ENGLISH_META,
    )["policy_id"] == "msbshse-grade-6-english-2026-08-29"
    assert profile.assessment_format_policy(
        math_profile, {},
    )["policy_id"] == "generic-cms"


@pytest.mark.parametrize("metadata", [None, {}], ids=["absent", "empty"])
def test_reresolving_persisted_profile_preserves_carried_metadata(
    metadata: dict[str, str] | None,
) -> None:
    persisted = profile.resolve_for_metadata(None, ENGLISH_META)

    rerun = profile.resolve_for_metadata(persisted, metadata)

    assert rerun["_resolved_metadata"] == ENGLISH_META
    assert profile.assessment_format_policy(rerun)["policy_id"] == (
        "msbshse-grade-6-english-2026-08-29"
    )
    assert profile.master_workbook_contract(
        rerun, learning_phase="Post",
    )["descriptive_answer_slots"] == 30


def test_partial_metadata_cannot_retarget_a_persisted_subject() -> None:
    persisted = profile.resolve_for_metadata(None, MATH_META)

    with pytest.raises(
        ValueError,
        match="cannot retarget resolved assessment profile selector 'subject'",
    ):
        profile.resolve_for_metadata(persisted, {"subject": "English"})


def test_same_selector_partial_reentry_is_idempotent_across_aliases() -> None:
    persisted = profile.resolve_for_metadata(None, MATH_META)

    rerun = profile.resolve_for_metadata(persisted, {
        "board": "Maharashtra (MSBSHSE)",
        "grade": "Class 06",
        "subject": "Maths",
    })

    assert rerun["_resolved_metadata"] == MATH_META
    assert profile.assessment_format_policy(rerun)["policy_id"] == (
        "msbshse-grade-6-mathematics-2026-08-27"
    )


def test_cross_board_metadata_cannot_retarget_a_persisted_profile() -> None:
    persisted = profile.resolve_for_metadata(None, ENGLISH_META)

    with pytest.raises(
        ValueError,
        match="cannot retarget resolved assessment profile selector 'board'",
    ):
        profile.resolve_for_metadata(persisted, {"board": "CBSE"})


@pytest.mark.parametrize("cleared", [None, ""], ids=["null", "blank"])
def test_explicit_clear_cannot_remove_a_carried_selector(cleared) -> None:
    persisted = profile.resolve_for_metadata(None, ENGLISH_META)

    with pytest.raises(
        ValueError,
        match="cannot retarget resolved assessment profile selector 'subject'",
    ):
        profile.resolve_for_metadata(persisted, {"subject": cleared})


def test_blank_first_resolution_leaves_selectors_unknown_then_fillable() -> None:
    unresolved = profile.resolve_for_metadata(None, {
        "board": "   ",
        "grade": None,
        "subject": "",
    })

    assert unresolved["_resolved_metadata"] == {}

    resolved = profile.resolve_for_metadata(unresolved, ENGLISH_META)

    assert resolved["_resolved_metadata"] == ENGLISH_META
    assert profile.assessment_format_policy(resolved)["policy_id"] == (
        "msbshse-grade-6-english-2026-08-29"
    )
    assert profile.master_workbook_contract(
        resolved, learning_phase="Post",
    )["descriptive_answer_slots"] == 30


def test_full_msbshse_display_name_resolves_the_same_policy() -> None:
    metadata = {
        **MATH_META,
        "board": "Maharashtra (MSBSHSE)",
    }

    assert profile.assessment_format_policy(metadata=metadata)["policy_id"] == (
        "msbshse-grade-6-mathematics-2026-08-27"
    )


@pytest.mark.parametrize(
    ("category", "difficulty", "minutes"),
    [
        ("Multiple Choice Question", "Less", 1),
        ("Multiple Choice Question", "Moderate", 1),
        ("Multiple Choice Question", "High", 1),
        ("Very Short Answer Questions", "Less", 1),
        ("Very Short Answer Questions", "Moderate", 1),
        ("Very Short Answer Questions", "High", 2),
        ("Short Answer Type (2 Marks)", "Less", 2),
        ("Short Answer Type (2 Marks)", "Moderate", 2),
        ("Short Answer Type (2 Marks)", "High", 3),
        ("Short Answer Type (3 Marks)", "Less", 4),
        ("Short Answer Type (3 Marks)", "Moderate", 5),
        ("Short Answer Type (3 Marks)", "High", 6),
        ("Long Answer Type (4 Marks)", "Less", 5),
        ("Long Answer Type (4 Marks)", "Moderate", 6),
        ("Long Answer Type (4 Marks)", "High", 7),
        ("Long Answer Type (5 Marks)", "Less", 5),
        ("Long Answer Type (5 Marks)", "Moderate", 7),
        ("Long Answer Type (5 Marks)", "High", 7),
    ],
)
def test_msbshse_grade_6_mathematics_duration_matrix(
    category: str, difficulty: str, minutes: int,
) -> None:
    sheet_kind = (
        "objective" if category == "Multiple Choice Question" else "descriptive"
    )
    assert profile.question_duration_minutes(
        metadata=MATH_META,
        sheet_kind=sheet_kind,
        question_category=category,
        difficulty=difficulty,
    ) == minutes


def test_subpoint_policy_exposes_marks_and_requires_an_authored_basis_count() -> None:
    assert profile.question_marks_rule(
        metadata=MATH_META,
        sheet_kind="subjective",
        question_category="Fill in the blanks",
    ) == {"mode": "per_subpoint", "marks_per_subpoint": 1}
    assert profile.question_marks_rule(
        metadata=MATH_META,
        sheet_kind="objective",
        question_category="Match the Following",
    ) == {
        "mode": "per_subpoint",
        "marks_per_subpoint": 1,
        "max_subpoints": 1,
    }
    assert profile.question_duration_rule(
        metadata=MATH_META,
        sheet_kind="subjective",
        question_category="Fill in the blanks",
    ) == {"mode": "per_subpoint", "minutes_per_subpoint": 1}
    assert profile.question_duration_minutes(
        metadata=MATH_META,
        sheet_kind="subjective",
        question_category="Fill in the blanks",
        difficulty="Moderate",
    ) is None
    assert profile.question_duration_minutes(
        metadata=MATH_META,
        sheet_kind="subjective",
        question_category="Fill in the blanks",
        difficulty="Moderate",
        basis_count=4,
    ) == 4


def test_format_policy_accessors_return_defensive_copies() -> None:
    first = profile.assessment_format_policy(metadata=MATH_META)
    first["formats_by_sheet"]["objective"].clear()

    assert profile.question_categories(metadata=MATH_META)["objective"] == (
        "Multiple Choice Question",
        "Match the Following",
        "True or False",
        "Fill in the blanks",
    )


def test_get_profile_returns_a_defensive_deep_copy() -> None:
    first = profile.get_profile()
    first["assessment_format"]["formats_by_sheet"]["objective"].clear()
    first["run_profile_overrides"][0]["overrides"]["sheet_kinds"] = (
        "subjective",
    )

    second = profile.get_profile()

    assert "Multiple Choice Question" in (
        second["assessment_format"]["formats_by_sheet"]["objective"]
    )
    assert second["run_profile_overrides"][0]["overrides"][
        "sheet_kinds"
    ] == ("objective", "descriptive", "subjective")


def test_resolve_deep_copies_defaults_and_explicit_mappings() -> None:
    default_copy = profile.resolve(None)
    default_copy["assessment_format"]["formats_by_sheet"]["objective"].clear()
    assert "Multiple Choice Question" in profile.resolve(None)[
        "assessment_format"
    ]["formats_by_sheet"]["objective"]

    source = {"name": "copy-source", "nested": {"values": [1]}}
    resolved = profile.resolve(source)
    resolved["nested"]["values"].append(2)
    assert source == {"name": "copy-source", "nested": {"values": [1]}}


def test_register_copies_its_input_return_and_later_reads(monkeypatch) -> None:
    monkeypatch.setattr(profile, "_PROFILES", dict(profile._PROFILES))
    source = {"name": "deep-copy-regression", "nested": {"values": [1]}}

    returned = profile.register(source)
    source["nested"]["values"].append(2)
    returned["nested"]["values"].append(3)

    first_read = profile.get_profile("deep-copy-regression")
    assert first_read["nested"]["values"] == [1]
    first_read["nested"]["values"].append(4)
    resolved_read = profile.resolve("deep-copy-regression")
    assert resolved_read["nested"]["values"] == [1]


@pytest.mark.parametrize(
    ("board", "grade"),
    [
        pytest.param("MH", "6", id="short-aliases"),
        pytest.param("Maharashtra (MSBSHSE)", "Class 06", id="display"),
        pytest.param(
            "Maharashtra State Board of Secondary and Higher Secondary "
            "Education",
            "Standard 6",
            id="full-board-name",
        ),
        pytest.param("  mh board ", " grade   06 ", id="normalized-space"),
    ],
)
def test_board_grade_aliases_select_matching_run_and_format_overrides(
    board, grade,
) -> None:
    metadata = {"board": board, "grade": grade, "subject": "Maths"}

    run_profile = profile.resolve_for_metadata(None, metadata)

    assert profile.sheet_kinds(run_profile) == (
        "objective", "descriptive", "subjective",
    )
    assert profile.forced_blank_fields(run_profile) == (
        "question_disclaimer",
    )
    assert profile.assessment_format_policy(
        run_profile, metadata,
    )["policy_id"] == "msbshse-grade-6-mathematics-2026-08-27"


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            {"board": "MSBSHSE", "grade": "06"},
            id="canonical-partial",
        ),
        pytest.param(
            {"board": "Maharashtra State Board", "grade": "Class 6"},
            id="alias-partial",
        ),
    ],
)
def test_board_grade_partial_metadata_keeps_the_pinned_run_profile(
    metadata,
) -> None:
    run_profile = profile.resolve_for_metadata(None, metadata)

    assert profile.sheet_kinds(run_profile) == ("objective", "descriptive")
    assert profile.forced_blank_fields(run_profile) == (
        "chapter_duration", "question_disclaimer",
    )
    assert profile.master_workbook_contract(
        run_profile, learning_phase="Post",
    )["contract_id"] == "reference-master-1"
    assert profile.assessment_format_policy(
        run_profile, metadata,
    )["policy_id"] == "generic-cms"


@pytest.mark.parametrize(
    ("metadata", "policy_id"),
    [
        pytest.param(
            {"board": "MSBSHSE", "subject": "Mathematics"},
            # Board + subject are conclusive for the FORMAT policy since the
            # owner's Question Duration Matrix (2026-08-29) binds every MH
            # Board grade per subject; only the grade-scoped run-profile
            # facts (sheet kinds, workbook geometry) stay pinned below.
            "msbshse-mathematics-physics-2026-08-29",
            id="missing-grade",
        ),
        pytest.param(
            {"grade": "06", "subject": "Mathematics"},
            "generic-cms",
            id="missing-board",
        ),
    ],
)
def test_inconclusive_partial_metadata_keeps_the_pinned_profile(
    metadata, policy_id,
) -> None:
    run_profile = profile.resolve_for_metadata(None, metadata)

    assert profile.sheet_kinds(run_profile) == ("objective", "descriptive")
    assert profile.forced_blank_fields(run_profile) == (
        "chapter_duration", "question_disclaimer",
    )
    assert profile.assessment_format_policy(
        run_profile, metadata,
    )["policy_id"] == policy_id
