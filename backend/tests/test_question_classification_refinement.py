"""Classification evidence, shared guidance and sealed replay boundaries.

Authorities are scripted here: these tests verify transport and decision
ownership, without claiming that an offline mock measures model accuracy.
"""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_cells as cells
from app.services import assessment_output_vocabulary as vocabulary
from app.services import assessment_prompts
from app.services import assessment_response_policy as response_policy
from app.services import generation_quality_policy as quality
from app.services.phase3 import kernel


PROFILE = {
    "name": "reference-1",
    "appears_in": "Pre/Post-Worksheet/Test",
    "sheet_kinds": ("objective", "subjective", "descriptive"),
}
META = {"subject": "Mathematics", "grade": "6", "source_book": "NCERT"}
STAMPED_META = {**META, quality.KEY: quality.VERSION}
TABLE = {
    "headers": ["Value", "Frequency"],
    "rows": [[2, 3], [4, 2]],
    "katex": r"$\begin{array}{|c|c|}\hline \text{Value}&\text{Frequency}\\\hline 2&3\\\hline 4&2\\\hline\end{array}$",
}
ENVELOPE = "8" * 64


def _verdict(payload):
    source = payload.get("source_atom") or payload["generated_question"]
    identity = "source_qid" if "source_atom" in payload else "pre_question_id"
    return {
        identity: source[identity],
        "sheet_kind": "descriptive",
        "question_category": "Numerical/application based",
        "cognitive_skill": "Apply",
        "difficulty": "Moderate",
        "marks": 2,
        "selection_mode": "",
        "rationale": (
            "The learner calculates from values and frequencies; 2.8 is the "
            "final result, not a factual lookup or an offered answer option."
        ),
    }


def _source():
    return {
        "source_qid": "QINV-0001",
        "raw_text": "Find the mean from the frequency table.",
        "normalized_public_text": "Find the mean from the frequency table.",
        "source_answer": "2.8",
        "options": [],
        "tables": [copy.deepcopy(TABLE)],
        "source_context": {"shared_context": "The table records observations."},
    }


def _generated():
    return {
        "pre_question_id": "PREQ-0001",
        "pre_concept_id": "PRC-0001",
        "question_id": "generated-1",
        "question_text": "Find the mean from the frequency table.",
        "answer": "2.8",
        "rationale": "Practise the previously learned calculation.",
        "tables": [copy.deepcopy(TABLE)],
        "options": [],
        "children": [{"question_text": "Show how you use the frequencies."}],
        "source_context": {"tables": [copy.deepcopy(TABLE)]},
    }


CONCEPTS = {
    "release:one:0001": {
        "concept_key": "release:one:0001",
        "pre_concept_id": "PRC-0001",
        "concept_title": "Mean from previously learned frequency tables",
    }
}


def _forbidden(payload):
    raise AssertionError("sealed replay must not call an authority")


def test_stamped_source_table_and_full_credit_contract_reach_fixer_and_critic():
    original = _source()
    before = copy.deepcopy(original)
    requests = {"author": [], "fixer": [], "critic": []}

    def author(payload):
        requests["author"].append(copy.deepcopy(payload))
        return {**_verdict(payload), "marks": True}

    def fixer(payload):
        requests["fixer"].append(copy.deepcopy(payload))
        return _verdict(payload["original_payload"])

    def critic(payload):
        requests["critic"].append(copy.deepcopy(payload))
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    result = cells.decide_cells(
        [original], meta=STAMPED_META, profile=PROFILE,
        envelope_sha256=ENVELOPE, provider=author, critic=critic, fixer=fixer,
        store=kernel.DecisionStore(),
    )[0]

    assert original == before
    assert len(requests["fixer"]) == len(requests["critic"]) == 1
    payloads = requests["author"] + [
        requests["fixer"][0]["original_payload"], requests["critic"][0],
    ]
    for payload in payloads:
        assert payload[quality.KEY] == quality.VERSION
        assert payload["source_atom"] == before
        assert response_policy.QUALITY_AUTHOR_RULES in payload["rules"]
    assert result["sheet_kind"] == "descriptive"
    assert result["authority"]["fixer"] is True
    assert result["authority"]["policy_version"].endswith(";" + quality.VERSION)


def test_generated_evidence_is_complete_only_on_stamped_decisions_and_replays():
    question = _generated()
    before = copy.deepcopy(question)
    store = kernel.DecisionStore()
    requests = []

    def author(payload):
        requests.append(copy.deepcopy(payload))
        return _verdict(payload)

    arguments = dict(
        questions=[question], profile=PROFILE, envelope_sha256=ENVELOPE,
        concept_records_by_key=CONCEPTS, store=store,
    )
    legacy = cells.decide_generated_cells(meta=META, provider=author, **arguments)
    current = cells.decide_generated_cells(
        meta=STAMPED_META, provider=author, **arguments,
    )
    assert len(requests) == 2
    historical_payload, current_payload = requests
    assert question == before
    assert quality.KEY not in historical_payload
    assert set(historical_payload["generated_question"]) == {
        "pre_question_id", "pre_concept_id", "question_id", "question_text",
        "answer", "rationale",
    }
    assert response_policy.QUALITY_AUTHOR_RULES not in historical_payload["rules"]
    assert current_payload["generated_question"] == before
    assert response_policy.QUALITY_AUTHOR_RULES in current_payload["rules"]
    assert legacy[0]["authority"]["policy_version"] == (
        cells.GENERATED_CELL_POLICY_VERSION + ";" + vocabulary.VERSION
    )
    assert current[0]["authority"]["policy_version"].endswith(";" + quality.VERSION)
    assert legacy[0]["authority"]["decision_key"] != current[0]["authority"]["decision_key"]
    for meta, expected in [(META, legacy), (STAMPED_META, current)]:
        assert cells.decide_generated_cells(
            meta=meta, provider=_forbidden, critic=_forbidden, fixer=_forbidden,
            **arguments,
        ) == expected


@pytest.mark.parametrize("carrier", ["profile", "resolved_metadata"])
def test_explicit_profile_stamp_reaches_cell_when_caller_metadata_is_empty(carrier):
    profile = copy.deepcopy(PROFILE)
    if carrier == "profile":
        profile[quality.KEY] = quality.VERSION
    else:
        profile["_resolved_metadata"] = {quality.KEY: quality.VERSION}
        # This fixture represents a sealed profile carrying its vocabulary,
        # not an unstamped historical profile silently adopting today's list.
        profile[vocabulary.POLICY_KEY] = vocabulary.snapshot()
    requests = []

    def author(payload):
        requests.append(copy.deepcopy(payload))
        return _verdict(payload)

    result = cells.decide_cells(
        [_source()], meta={}, profile=profile, envelope_sha256=ENVELOPE,
        provider=author, store=kernel.DecisionStore(),
    )[0]
    assert requests[0][quality.KEY] == quality.VERSION
    assert response_policy.QUALITY_AUTHOR_RULES in requests[0]["rules"]
    assert result["authority"]["policy_version"].endswith(";" + quality.VERSION)


@pytest.mark.parametrize("generated", [False, True])
def test_live_cell_review_uses_same_refinement_without_changing_old_prompt(
    monkeypatch, generated,
):
    from app.services import generation

    prompts = []

    def capture(system, user, **kwargs):
        prompts.append((system, user, kwargs))
        return {"verdict": "verified", "confidence": 1.0, "issues": []}

    monkeypatch.setattr(generation, "_openai_json", capture)
    method = cells._live_generated_cell_critic if generated else cells._live_cell_critic
    old_payload = {"profile": {}}
    method(old_payload)
    method({**old_payload, quality.KEY: quality.VERSION})
    old_system = (
        cells.GENERATED_CELL_CRITIC_SYSTEM if generated else cells.CELL_CRITIC_SYSTEM
    )
    assert prompts[0][0] == old_system
    assert prompts[1][0] == old_system + response_policy.quality_review_instruction(
        {quality.KEY: quality.VERSION}
    )
    assert all(entry[2]["purpose"] == "advisory_critic" for entry in prompts)


def test_question_prompt_adds_lane_preservation_only_with_explicit_stamp():
    arguments = dict(
        question_type="descriptive", difficulty="Moderate", skill="Apply",
        category="Numerical/application based", marks=2,
    )
    previous = assessment_prompts.build_prompt(metadata=META, **arguments)
    current = assessment_prompts.build_prompt(metadata=STAMPED_META, **arguments)
    assert current.startswith(previous + "\n\n")
    assert response_policy.QUALITY_AUTHOR_RULES not in previous
    assert response_policy.QUALITY_AUTHOR_RULES in current
    assert "instead of inventing options or placeholders" in current
    assert response_policy.quality_instruction({quality.KEY: "future-policy"}) == ""
    assert response_policy.quality_review_instruction({}) == ""
