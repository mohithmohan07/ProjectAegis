"""Q70: generated Pre questions declare their options (generation-quality v4).

A generated Pre MCQ shipped with its fourth option missing (Bholi) and nothing
on the generated lane could see it: the author response had no ``options``
field, the cell carried six fields plus the tier, and the one cardinality
gate read the source atom, which the Pre lane never has. Under v4 the author
declares the choice set, the cell carries it and the materializer holds the
projected answers[] to that count. Every run stamped v3 or earlier keeps the
v1 schema, prompt, checker, cell and key byte for byte.
"""
from __future__ import annotations

import copy
import inspect
import json

import pytest

from app.services import assessment_materialization as materialization
from app.services import assessment_profile, assessment_release_run as run
from app.services import generation, generation_quality_policy as quality, model_provider
from app.services import response_schemas
from app.services.phase3 import envelope as envelope_mod
from app.services.phase3 import kernel, pre_coverage, prequestions
from tests import test_adaptive_pre_coverage as adaptive
from tests import test_assessment_multiple_selection as msq
from tests import test_assessment_pre_release_lane as pre_lane
from tests import test_phase3_prequestions as golden
from tests.test_pre_coverage_rule import _tiered_questions


@pytest.fixture(scope="module")
def golden_envelope() -> dict:
    return golden.settle_golden.envelope_mod.load(golden.GOLDEN / "rne_envelope.json")


@pytest.fixture
def pre_map(golden_envelope) -> dict:
    return golden.premap_golden._build(golden_envelope)


def _stamped(env: dict, version: str) -> dict:
    stamped = copy.deepcopy(env)
    stamped["metadata"][quality.KEY] = version
    stamped["envelope_sha256"] = envelope_mod.seal_sha256(stamped)
    return stamped


def test_the_v2_author_schema_requires_options_and_the_v1_schema_is_untouched():
    v1 = response_schemas.pre_question_author_schema().json_schema()
    assert v1["name"] == "aegis_pre_question_author_v1"
    assert "options" not in json.dumps(v1)
    v2 = response_schemas.pre_question_author_schema(declared_options=True).json_schema()
    assert v2["name"] == "aegis_pre_question_author_v2"
    assert v2["strict"] is True
    draft = v2["schema"]["$defs"]["PreQuestionDeclaredDraft"]
    assert "options" in draft["properties"]
    assert "options" in draft["required"]          # Q61: declared means required
    assert draft["additionalProperties"] is False
    model = response_schemas.PreQuestionAuthorDeclaredResponse
    base = {"question_id": "PRQ-0001", "question_text": "Which is a solid?",
            "answer": "Cube", "rationale": "prior shapes", "tier": "Basic"}
    with pytest.raises(Exception):
        model.model_validate({"questions": [base]})
    with pytest.raises(Exception):
        model.model_validate({"questions": [{**base, "options": [], "extra": 1}]})
    assert model.model_validate({"questions": [{**base, "options": []}]})


def test_the_author_checker_holds_the_declared_shape_only_when_asked():
    rows = _tiered_questions("PRC-0001", {"Basic": 2})
    rule = pre_coverage.owner_rule()

    def defects(rows, **kwargs):
        return prequestions._author_checker(
            "PRC-0001", 2, rule=rule, split={"Basic": 2}, **kwargs,
        )({"questions": rows})

    assert defects(rows) == []
    strict = lambda rows: defects(rows, declared_options=True)  # noqa: E731
    assert any("PRQ-0001 has no options array" in d for d in strict(rows))
    with_empty = [{**rows[0], "options": [""]}, {**rows[1], "options": ["A", "B"]}]
    assert any("declares an empty option" in d for d in strict(with_empty))
    repeated = [{**rows[0], "options": ["A", "A"]}, {**rows[1], "options": ["A", "B"]}]
    assert any("declares a repeated option" in d for d in strict(repeated))
    good = [{**rows[0], "options": []}, {**rows[1], "options": ["A", "B", "C", "D"]}]
    assert strict(good) == []
    # The shape check never reads the question's wording (Rule 1).
    source = inspect.getsource(prequestions._author_checker)
    branch = source.split("if declared_options:", 1)[1].split("if len(rows) != planned_total", 1)[0]
    assert "question_text" not in branch


def test_a_v4_build_carries_declared_options_and_a_pre_v4_build_is_byte_identical(
    golden_envelope, pre_map,
):
    cids = [row["_pre_concept_id"] for row in pre_map["rows"]]
    splits = {cid: {"Basic": 1} for cid in cids}
    base = adaptive._provider(splits)

    def declaring(payload):
        response = base(payload)
        if payload["stage"] == "prequestions.author":
            for row in response["questions"]:
                row["options"] = ["Cube", "Sphere", "Circle", "Cone"]
        return response

    v4 = _stamped(adaptive._adaptive(golden_envelope), quality.V4)
    store = kernel.DecisionStore()
    result = prequestions.build(v4, pre_map, provider=declaring, store=store)
    assert result["blocked"] == {}
    for cid in cids:
        for question in result["questions"][cid]:
            assert question["options"] == ["Cube", "Sphere", "Circle", "Cone"]
    # The author decisions are keyed under the recorded v4 stamp.
    assert any(
        store.get(key)["policy_version"].endswith(";" + quality.V4)
        for key in store.keys()
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("a second identical v4 build must spend nothing")

    assert prequestions.build(v4, pre_map, provider=forbidden, critic=forbidden, store=store) == result

    v3 = _stamped(adaptive._adaptive(golden_envelope), quality.V3)
    legacy = prequestions.build(v3, pre_map, provider=base, store=kernel.DecisionStore())
    for cid in cids:
        for question in legacy["questions"][cid]:
            assert "options" not in question

    unstamped = {"coverage_rule": adaptive.RULE, "stage": "prequestions.author"}
    assert prequestions._author_system({**unstamped, quality.KEY: quality.V3}) == (
        prequestions._author_system(unstamped)
    )
    v4_system = prequestions._author_system({**unstamped, quality.KEY: quality.V4})
    assert '"options":[],' in v4_system
    assert prequestions._DECLARED_OPTIONS_INSTRUCTION in v4_system
    assert prequestions._DECLARED_OPTIONS_INSTRUCTION not in prequestions._author_system(unstamped)
    assert prequestions._DECLARED_OPTIONS_REVIEW in prequestions._critic_system(
        {**unstamped, quality.KEY: quality.V4}
    )
    assert prequestions._DECLARED_OPTIONS_REVIEW not in prequestions._critic_system(unstamped)
    # The fixed-rule (non-adaptive) systems gain the sentence only under v4 too.
    from app.services.phase3 import prompts

    assert prequestions._author_system({}) == prompts.PREQUESTIONS_AUTHOR_SYSTEM
    assert prequestions._author_system({quality.KEY: quality.V4}) == (
        prompts.PREQUESTIONS_AUTHOR_SYSTEM + prequestions._DECLARED_OPTIONS_INSTRUCTION
    )
    assert prequestions._DECLARED_OPTIONS_INSTRUCTION not in prompts.PREQUESTIONS_AUTHOR_SYSTEM


def test_live_author_sends_the_v2_schema_only_for_a_v4_payload(monkeypatch):
    seen = []

    def record(system, user, **kwargs):
        seen.append(kwargs.get("response_schema"))
        return {"questions": []}

    monkeypatch.setattr(generation, "_openai_json", record)
    payload = {"stage": "prequestions.author", "pre_concept": {"mastery": "Add two small numbers"}}
    with model_provider.bind_profile(model_provider.new_profile()):
        prequestions._live_author({**payload, quality.KEY: quality.V4})
        prequestions._live_author(payload)
    assert seen[0].name == "aegis_pre_question_author_v2"
    assert seen[1].name == "aegis_pre_question_author_v1"


def test_bind_generated_cells_carries_declared_options_only_under_a_v4_profile():
    questions = pre_lane._questions(1)
    questions[0]["options"] = ["Cube", "Sphere", "Circle", "Cone"]
    # The lane fixture's category belongs to a board profile; the generic
    # default profile this test binds under permits "Long Answer".
    cells = [{**cell, "question_category": "Long Answer"} for cell in pre_lane._cells(1, "release:ck")]
    stamped = {**assessment_profile.resolve(None), quality.KEY: quality.V4}
    bound = run._bind_generated_cells(
        copy.deepcopy(questions), copy.deepcopy(cells),
        profile=stamped, concept_keys={"release:ck"},
    )
    assert bound[0]["generated_question"]["options"] == ["Cube", "Sphere", "Circle", "Cone"]
    legacy = run._bind_generated_cells(
        copy.deepcopy(questions), copy.deepcopy(cells),
        profile=assessment_profile.resolve(None), concept_keys={"release:ck"},
    )
    assert "options" not in legacy[0]["generated_question"]
    assert len(legacy[0]["generated_question"]) == 6
    questions[0]["options"] = "Cube, Sphere"
    bound = run._bind_generated_cells(
        copy.deepcopy(questions), copy.deepcopy(cells),
        profile=stamped, concept_keys={"release:ck"},
    )
    assert "options" not in bound[0]["generated_question"]


def _generated_cell(options=None) -> dict:
    cell = {**msq._cell(), "source_policy": "generate", "selection_mode": "single"}
    cell["generated_question"] = {
        "pre_question_id": "PRC-0001-PRQ-0001", "pre_concept_id": "PRC-0001",
        "question_id": "PRQ-0001", "question_text": "Which shape is a solid?",
        "answer": "Cube", "rationale": "prior shapes",
        **({"options": list(options)} if options is not None else {}),
    }
    return cell


def _single_correct_answers(count: int) -> list[dict]:
    texts = ["Cube", "Sphere", "Circle", "Cone"][:count]
    return [
        {"answer_type": "Phrases", "answer_content": text,
         "correct_answer": "1" if index == 0 else "0"}
        for index, text in enumerate(texts)
    ]


def _cardinality(defects: list[str]) -> list[str]:
    return [d for d in defects if "option cardinality" in d]


def test_materialization_holds_the_projected_count_to_the_declared_set_and_keeps_the_historical_skip():
    four = ["Cube", "Sphere", "Circle", "Cone"]
    proposal = {**msq._proposal(), "answers": _single_correct_answers(4)}
    assert _cardinality(materialization._proposal_defects(
        proposal, _generated_cell(four), "CAND-MSQ", atom=None,
    )) == []
    three = {**msq._proposal(), "answers": _single_correct_answers(3)}
    defects = _cardinality(materialization._proposal_defects(
        three, _generated_cell(four), "CAND-MSQ", atom=None,
    ))
    assert defects == [
        "objective option cardinality must preserve the generated question's "
        "declared options (4 supplied, 3 returned)"
    ]
    # A historical generated cell (no declared set) keeps the skip it had.
    assert _cardinality(materialization._proposal_defects(
        three, _generated_cell(None), "CAND-MSQ", atom=None,
    )) == []
    assert _cardinality(materialization._proposal_defects(
        three, _generated_cell([]), "CAND-MSQ", atom=None,
    )) == []
    # The source path still names the source.
    source_cell = {**msq._cell(), "selection_mode": "single"}
    defects = _cardinality(materialization._proposal_defects(
        three, source_cell, "CAND-MSQ", atom={"options": four},
    ))
    assert defects == [
        "objective option cardinality must preserve the source "
        "(4 supplied, 3 returned)"
    ]


def test_the_materializer_is_told_the_declared_set_only_on_a_v4_generated_payload(monkeypatch):
    four = ["Cube", "Sphere", "Circle", "Cone"]
    cell = _generated_cell(four)

    def payload_for(meta, atom=None):
        return materialization._decision_payload(
            atom, cell, candidate_id="C", meta=meta, context="",
            descriptive_answer_capacity=10,
        )

    v4 = payload_for({"subject": "Mathematics", quality.KEY: quality.V4})
    assert materialization.DECLARED_OPTIONS_INSTRUCTION in v4["rules"]
    assert materialization.DECLARED_OPTIONS_INSTRUCTION in v4["critic_rules"]
    assert v4["blueprint_cell"]["generated_question"]["options"] == four
    v3 = payload_for({"subject": "Mathematics", quality.KEY: quality.V3})
    assert materialization.DECLARED_OPTIONS_INSTRUCTION not in v3["rules"]
    sourced = payload_for(
        {"subject": "Mathematics", quality.KEY: quality.V4}, atom={"options": four},
    )
    assert materialization.DECLARED_OPTIONS_INSTRUCTION not in sourced["rules"]
    # A pre-v4 payload's key is exactly what it was before the branch existed.
    before = kernel.decision_key(
        kind="assessment.materialize", unit_id="C", envelope_sha256="e" * 64,
        payload=v3, policy_version="p",
    )
    monkeypatch.setattr(quality, "declared_pre_options", lambda _value: False)
    again = payload_for({"subject": "Mathematics", quality.KEY: quality.V3})
    assert kernel.decision_key(
        kind="assessment.materialize", unit_id="C", envelope_sha256="e" * 64,
        payload=again, policy_version="p",
    ) == before
