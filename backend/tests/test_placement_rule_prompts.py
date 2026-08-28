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


def test_rules_document_states_the_split_versus_new_concept_test():
    text = _squash(RULES_DOC.read_text(encoding="utf-8"))

    # The evidence test, not a wording test.
    assert "do the two concepts ground in the same source blocks?" in text
    assert "the original is untouched" in text
    # And the reason it matters at all.
    assert "it can stop the run outright" in text


def test_rules_document_keeps_one_type_on_one_owner_concept():
    """Q14: Case evidence is granular; the reusable Type has one owner."""

    text = _squash(RULES_DOC.read_text(encoding="utf-8"))

    assert "a type is a **chapter-level identity**" in text
    assert "one type therefore renders under one concept" in text
    assert "places all three cases there" in text
    assert "different numbering" in text


def test_shared_placement_critic_exempts_every_non_necessary_edge():
    """Phase 3.3 Type-host placement had the same bug as Phase 3.2.

    ``PROVIDER_SYSTEM`` defines three relationship types as necessity=false,
    but ``CRITIC_SYSTEM`` named only two of them in its "edges only" carve-out.
    SUBSTANTIVE_LATER_ILLUSTRATION was left able to move -- or sink -- a claim,
    which is the same shape of contradiction that cost job 8 its run, sitting
    in the prompt that places Types onto concepts.
    """

    critic = _squash(placement_policy.CRITIC_SYSTEM)

    for kind in NON_NECESSARY_TYPES:
        assert kind.casefold() in critic, kind
    assert "never be used to move the claim, and never to reject it" in critic
    # Rule 4, Reading B: the later topic earns a claim of its own instead.
    assert "earns its own separate claim" in critic


def test_provider_is_told_which_topic_evidence_must_come_from():
    """Job 9's stop, and it was a prompt gap rather than a bad check.

    Phase 3.3 failed closed with::

        REL-CAF3CCF722DCA85931BB0AAA cites BLK-00293 from TOPIC-0005
        as evidence for TOPIC-0001

    Three deterministic sites require a relationship's evidence to live in
    that relationship's own topic, and by the enum's semantics they are right:
    a prerequisite, back-reference, illustration or mention is evidenced where
    it actually appears. The provider prompt asked for "exact supplied block
    IDs" without ever saying which topic they had to come from, so the model
    cited the block the task was printed in -- an end-of-chapter exercise under
    the last topic -- as evidence for an early-topic prerequisite.

    The check stays strict. The instruction now says what it requires.
    """

    provider = _squash(placement_policy.PROVIDER_SYSTEM)

    assert "every cited block must belong to that relationship's own topic" in provider
    assert "do not cite the block where the claim or task physically sits" in provider
    # Rule 4a, restated where the model actually needed it.
    assert "physical location is provenance, not evidence" in provider


# --------------------------------------------------------------------------- #
# One canonical rules block, appended everywhere placement is decided
# --------------------------------------------------------------------------- #

def test_placement_rules_state_every_product_rule():
    """The shared block carries Rules 2-5 from the authority document."""

    rules = _squash(placement_policy.PLACEMENT_RULES)

    # Rule 2 -- advanced placement.
    assert "belongs to that later topic" in rules
    assert "prerequisite, not the owner" in rules
    # Rule 3 -- split keeps both sides, and only over shared evidence.
    assert "from the same blocks" in rules
    assert "neither may be dropped" in rules
    # Rule 4 -- retrospective reference makes a new claim, not a split.
    assert "stays in the topic that teaches it" in rules
    assert "never a split of the original" in rules
    assert "same blocks means split, different blocks means" in rules
    # Rule 4a -- print position, for figures and for question order alike.
    assert "print position is never evidence" in rules
    assert "question order is not teaching order" in rules
    assert "physical location is provenance, not evidence" in rules
    # Rule 5 -- tasks follow the latest topic in teaching order.
    assert "latest of them in teaching order" in rules


def test_info_hub_items_enter_the_hub_pool():
    """Step 3 of the manual process pools info hubs exactly like activities."""
    from app.services import generation

    pool = generation._hub_inventory_items({"items": [
        {"qid": "QINV-0001", "source_kind": "exercise", "raw_task": "Q"},
        {"qid": "QINV-0002", "source_kind": "activity", "raw_task": "A"},
        {"qid": "QINV-0003", "source_kind": "info_hub", "raw_task": "H"},
    ]})
    assert [item["qid"] for item in pool] == ["QINV-0002", "QINV-0003"]


def test_type_mining_prompts_carry_the_polished_wording_note():
    """Mining classifies by polished wording and never breaks a question."""
    from app.services import generation, question_polishing

    note = question_polishing.FRAGMENT_MINING_NOTE
    assert "ONE question" in note
    assert "never" in note and "break" in note
    assert "polished_task" in note

    source = Path(generation.__file__).read_text(encoding="utf-8")
    assert source.count("+ question_polishing.FRAGMENT_MINING_NOTE") == 2
