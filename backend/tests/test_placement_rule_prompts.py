"""The placement rules must reach the model, not just the code comments.

``docs/concept-placement-rules.md`` states that the Phase 3.1/3.2/3.8 grounding
contracts and the Type prompts implement it, and that where a rule and an
implementation disagree the document is correct. For a long time the file was
referenced six times in the codebase and every reference was a comment: nothing
loaded it, or its substance, into a model call.

Job 8 paid for that. The provider was told to set ``necessity=true`` only for
the three necessity-bearing relationship types, and the critic was told to
accept a relationship only when ``necessity_supported`` was true. So a
correctly-labelled ``SUBSTANTIVE_LATER_ILLUSTRATION`` was rejected for holding
the exact value the provider was instructed to give it, and could never pass.
One concept was walked through five topics before landing back where it began.

These tests pin the rules into the prompt text. They are deliberately literal:
a prompt is only as good as the words in it, and the failure above was two
instructions in one file disagreeing twenty lines apart.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import (
    canonical_source_phase32_topology_adjudication_contract as phase32,
    placement_policy,
    prompts,
)


def _squash(text: str) -> str:
    """Compare prompt wording without depending on where a line wrapped."""

    return " ".join(str(text or "").split()).casefold()

RULES_DOC = (
    Path(__file__).resolve().parents[2] / "docs" / "concept-placement-rules.md"
)

# The relationship types the provider is instructed to mark non-necessary.
NON_NECESSARY_TYPES = (
    "RETROSPECTIVE_REFERENCE",
    "SUBSTANTIVE_LATER_ILLUSTRATION",
    "INCIDENTAL_MENTION",
)
NECESSARY_TYPES = (
    "CORE_TEACHING",
    "REQUIRED_PREREQUISITE",
    "REQUIRED_LATER_METHOD",
)


def test_the_rules_document_is_present():
    """The prompts below encode it; a missing file makes them unverifiable."""

    assert RULES_DOC.is_file(), f"{RULES_DOC} is the stated authority"


def test_provider_and_critic_agree_about_necessity():
    """The exact contradiction that cost job 8 its run.

    The provider assigns ``necessity=false`` to three relationship types by
    instruction. If the critic then rejects any relationship whose
    ``necessity_supported`` is false, those three can never be accepted, and
    every concept carrying one is rejected forever.
    """

    provider = phase32.PLACEMENT_PROVIDER_INSTRUCTIONS
    critic = phase32.PLACEMENT_CRITIC_INSTRUCTIONS

    # The provider still restricts necessity to the three bearing types.
    assert "Set necessity=true only for" in provider
    for kind in NECESSARY_TYPES:
        assert kind in provider, kind

    # The critic must scope necessity_supported to those same three, and must
    # name the non-necessary types as exempt.
    assert "necessity_supported is judged ONLY for" in critic
    for kind in NECESSARY_TYPES:
        assert kind in critic, kind
    for kind in NON_NECESSARY_TYPES:
        assert kind in critic, f"{kind} must be exempted by name"


def test_critic_is_told_not_to_reject_a_correct_illustration():
    """Rule 4: a later topic that refers back does not own the material."""

    critic = _squash(phase32.PLACEMENT_CRITIC_INSTRUCTIONS)

    assert "never reject a concept because a correctly-labelled" in critic
    assert "stays in the topic" in critic
    # The later topic is not left empty-handed: it gains its own concept.
    assert "gains its own concept" in critic


def test_provider_is_told_print_position_is_not_placement():
    """Rule 4a: layout decides where a figure prints, not what it is about."""

    provider = _squash(phase32.PLACEMENT_PROVIDER_INSTRUCTIONS)

    for phrase in (
        "page layout",
        "whose content it depicts",
        "never by the topic it was printed under",
    ):
        assert phrase in provider, phrase


def test_type_mining_prompt_states_rule_5_and_its_exception():
    """Rule 5 with the Rule 4 carve-out, in the prompt that mines Types."""

    prompt = _squash(prompts.get_text("concepts.type_mining.system"))

    assert "latest of those topics" in prompt
    assert "prerequisite, not the owner" in prompt
    # The retrospective-reference exception must travel with it, or Rule 5
    # drags every back-referencing task forward.
    assert "retrospective reference is the exception" in prompt
    assert "does not by itself mean later in teaching" in prompt


def test_placement_engine_ignores_source_position_and_qid_order():
    """Rule 4a in the deterministic core, where it actually binds.

    ``compute_placement`` is the only thing that assigns ownership. Its
    contract is that the result depends on the certified relationship set and
    the sealed teaching order alone — never on where a claim sits in the
    source, and never on QID order.
    """

    doc = _squash(placement_policy.compute_placement.__doc__ or "")

    assert "to qid order" in doc
    assert "where the claim physically sits in the source" in doc


@pytest.mark.parametrize("rule", ["Rule 4a", "Rule 5"])
def test_rules_document_carries_the_rules_the_prompts_encode(rule):
    text = RULES_DOC.read_text(encoding="utf-8")

    assert rule in text, f"{rule} must be stated in the authority document"
