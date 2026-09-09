"""Owner-attached response-mechanism policy is present on every cell pass."""
from __future__ import annotations

from app.services import assessment_cells
from app.services import assessment_item_review
from app.services import assessment_response_policy as policy


def test_sop_policy_is_versioned_and_has_no_local_classifier() -> None:
    assert policy.POLICY_VERSION == "assessment-response-mechanism-1-sop-2026-09-09"
    assert policy.SELECTION_MODES == ("single", "multiple")
    assert "two or more" in policy.AUTHOR_RULES
    assert "short" in policy.AUTHOR_RULES
    assert "fixed-format factual" in policy.AUTHOR_RULES
    assert "TRUE/FALSE IS ALWAYS SUBJECTIVE" in policy.AUTHOR_RULES
    assert "Calculate the value of x" in policy.AUTHOR_RULES
    assert "marks" in policy.AUTHOR_RULES
    assert "verbs" in policy.AUTHOR_RULES
    assert "answer length" in policy.AUTHOR_RULES
    assert 'selection_mode as "single"' in policy.AUTHOR_RULES
    assert "regex" not in policy.AUTHOR_RULES.lower()
    assert "keyword list" not in policy.AUTHOR_RULES.lower()


def test_cell_author_and_critic_prompts_carry_the_same_sop() -> None:
    assert policy.POLICY_VERSION in assessment_cells.CELL_SYSTEM
    assert policy.POLICY_VERSION in assessment_cells.GENERATED_CELL_SYSTEM
    assert "TRUE/FALSE IS ALWAYS SUBJECTIVE" in assessment_cells.CELL_SYSTEM
    assert "TRUE/FALSE IS ALWAYS SUBJECTIVE" in assessment_cells.GENERATED_CELL_SYSTEM
    assert '"selection_mode":"single|multiple|"' in assessment_cells.CELL_SYSTEM
    assert "two or more explicit" in assessment_cells.CELL_CRITIC_SYSTEM
    assert "two or more explicit" in assessment_cells.GENERATED_CELL_CRITIC_SYSTEM
    assert "advisory" in assessment_cells.CELL_CRITIC_SYSTEM.lower()
    assert "advisory" in assessment_cells.GENERATED_CELL_CRITIC_SYSTEM.lower()
    assert assessment_cells.CELL_POLICY_VERSION.endswith(
        "response-mechanism-sop-2026-09-09"
    )
    assert assessment_cells.GENERATED_CELL_POLICY_VERSION.endswith(
        "response-mechanism-sop-2026-09-09"
    )


def test_joint_item_review_audits_the_sop_without_owning_the_verdict() -> None:
    assert policy.CRITIC_RULES in assessment_item_review.ITEM_REVIEW_SYSTEM
    assert "do not revise" in assessment_item_review.ITEM_REVIEW_SYSTEM
    review_prompt = assessment_item_review.ITEM_REVIEW_SYSTEM.replace("\n", " ")
    assert "calculating x remains Descriptive" in review_prompt
