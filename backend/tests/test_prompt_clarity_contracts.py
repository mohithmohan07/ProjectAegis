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
    generation_prompt = assessment_prompts.TYPE_BLOCKS["objective"]
    materialization = assessment_materialization.MATERIALIZE_SYSTEM

    for prompt in (generation_prompt, materialization):
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


def test_table_house_style_is_pinned_on_every_prompt_surface():
    """P10 (owner-corrected reference + decision D1, 2026-08-29): the
    canonical table style — one [Katex] wrapper, \\text prose,
    \\\\[0.12 cm] spacing, pipe columns, \\hline every row, sized
    \\phantom placeholders — must reach the model on all three prompt
    surfaces, and coordinate-labelled prose must be named a transport
    encoding, never a shippable rendering."""

    from app.services import katex_rules

    preamble = katex_rules.PROMPT_PREAMBLE
    materialization = assessment_materialization.MATERIALIZE_SYSTEM
    refiner = assessment_master_refiner.CANDIDATE_SYSTEM

    for prompt in (preamble, materialization, refiner):
        assert "{|c|c|c|}" in prompt
        assert r"\\[0.12 cm]" in prompt
        assert r"\phantom{7}" in prompt
        assert r"\phantom{n}" in prompt  # named only to forbid the literal
        assert r"\hline" in prompt

    assert "Never verbalise a table" in preamble
    assert "transport encoding" in preamble
    for prompt in (materialization, refiner):
        assert "Table row 1, column 2: 8611" in prompt
        assert "re-rendered" in prompt

    # D1: the support list flipped — only \mathrm stays banned; the
    # preamble must say the supported trio and row spacing are allowed.
    assert "Supported and encouraged" in preamble
    assert r"\mathrm" in preamble
    for supported in (r"\hspace", r"\phantom", r"\boxed"):
        assert supported in preamble


def test_the_katex_rules_prompt_key_was_re_minted_for_d1_p10():
    """A stored Admin override of the pre-D1 key must never resurrect the
    superseded rules: the old key is retired (its override text archived)
    and every consumer resolves the new key."""

    from app.services import katex_rules, prompts

    assert "content.katex_rules" in prompts.RETIRED_PROMPT_KEYS
    assert prompts.get_text("content.katex_rules.v2") == (
        katex_rules._PROMPT_PREAMBLE_DEFAULT
    )
    assert katex_rules.PROMPT_PREAMBLE == (
        prompts.get_text("content.katex_rules.v2")
    )
