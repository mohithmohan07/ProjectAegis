"""Cross-pipeline prompt contracts pinned by the 2026-08-23 clarity audit.

These tests do not judge educational meaning.  They keep the owner-authored
format and stage-boundary instructions present in the exact prompts sent to
the provider, where an apparently harmless wording cleanup could otherwise
reintroduce the defects seen in released workbooks.
"""

from app.services import assessment_master_refiner
from app.services import assessment_materialization
from app.services import assessment_prompts
from app.services import generation
from app.services.phase3 import prompts as phase3_prompts


def test_assessment_author_prompt_has_an_explicit_evidence_boundary():
    prompt = assessment_prompts.BASE_BLOCK

    assert "EVIDENCE AND DECISION BOUNDARY" in prompt
    assert "least-distorting, evidence-bound" in prompt
    assert '"needs review"' in prompt


def test_objective_prompts_require_lowercase_paper_labels_without_duplication():
    generation = assessment_prompts.TYPE_BLOCKS["objective"]
    materialization = assessment_materialization.MATERIALIZE_SYSTEM

    for prompt in (generation, materialization):
        assert "lowercase" in prompt
        assert "a), b), c), d)" in prompt
        assert "answer_content" in prompt
    assert "never uppercase" in materialization
    assert "lowercase paper labels a), b), c), d)" in (
        generation.prompts.get_text("identify.type_hint.objective")
    )


def test_assessment_output_contract_is_valid_json_not_comment_annotated_json():
    prompt = assessment_prompts.OUTPUT_BLOCK

    assert "one valid JSON object" in prompt
    assert "// student-facing" not in prompt
    assert "// objective" not in prompt
    assert "lowercase a), b), c), d)" in prompt
    assert "otherwise is []" in prompt


def test_phase3_shared_prompt_pins_evidence_schema_and_exact_coverage():
    for prompt in (
        phase3_prompts.TOPOLOGY_SYSTEM,
        phase3_prompts.PREMAP_SYSTEM,
        phase3_prompts.PREQUESTIONS_AUTHOR_SYSTEM,
        phase3_prompts.FIXER_SYSTEM,
    ):
        assert "complete decision boundary" in prompt
        assert "required input ID" in prompt
        assert "matches the stated schema" in prompt


def test_description_author_does_not_duplicate_later_type_and_question_passes():
    prompt = phase3_prompts.ANALYSIS_SYSTEM

    assert "intentionally returns only" in prompt
    assert "Type/Case/Example" in prompt
    assert "Do not anticipate or duplicate" in prompt


def test_master_refiner_critic_example_is_strict_json():
    prompt = assessment_master_refiner.CRITIC_SYSTEM

    assert "strict JSON" in prompt
    assert '{"verdict":"verified|dissent"' in prompt
    assert "{verdict:'" not in prompt
