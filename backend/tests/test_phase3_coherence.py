"""Post coherence uses API meaning and transports exact identities/order."""
from __future__ import annotations

import copy

import pytest

from app.services import generation_quality_policy as quality
from app.services.phase3 import assemble, coherence, kernel


def _fixture():
    names = ["Arithmetic Mean", "Median", "Average Of A Data Set"]
    rows = [
        {"topic": "Statistics", "_semantic_topic_id": "TOPIC-1",
         "concept_title": name, "parent_concept": "Data summaries",
         "concept_details": "Description: " + name + " teaching.\nAchieving Mastery: Use " + name + ".",
         "_source_block_ids": [f"BLK-{i}"], "_reference_block_ids": [f"REF-{i}"],
         "_phase32_origin_concept_id": f"ORIGIN-{i}",
         "review_flags": [f"retained evidence flag {i}"]}
        for i, name in enumerate(names, 1)
    ]
    def dest(i):
        return {"concept_title": names[i], "topic": "Statistics", "topic_id": "TOPIC-1",
                "parent_concept": "Data summaries", "placement_reason": "original API verdict"}
    hosts = {
        "new_concepts": [rows[2]],
        "host_map": {"TYPE-0001::CASE-0001": dest(0), "TYPE-0002::CASE-0002": dest(2),
                     "TYPE-0003::CASE-0003": dest(1)},
        "qid_map": {"QINV-0001": dest(0), "QINV-0002": dest(2), "QINV-0003": dest(1)},
    }
    env = {
        "metadata": {quality.KEY: quality.VERSION}, "envelope_sha256": "source-identity",
        "graph": {"topics": [{"topic_id": "TOPIC-1", "title": "Statistics"}]},
        "canonical": {"blocks": [{"block_id": "BLK-1", "display_text": "Complete source " * 1000}]},
        "inventory": {"items": [{"qid": f"QINV-{i:04d}", "raw_task": f"Source question {i}?"}
                                for i in range(1, 4)]},
        "mined_types": {"types": [
            {"type_id": f"TYPE-{i:04d}", "type_title": f"Method {i}", "type_description": "Teach the method.",
             "case_prompts": [{"case_id": f"CASE-{i:04d}", "case_title": f"Case {i}",
                               "examples": [{"source_question_id": f"QINV-{i:04d}",
                                             "example_prompt": f"Source question {i}?"}]}]}
            for i in range(1, 4)
        ]},
    }
    return env, rows[:2], hosts


def _verdict():
    return {
        "concepts": [
            {"source_row_ids": ["ROW-0001", "ROW-0003"], "representative_row_id": "ROW-0001",
             "concept_title": "Arithmetic Mean", "description": "Divide the sum by the count.",
             "achieving_mastery": "Calculate and interpret a mean.",
             "rationale": "Both rows teach the same capability using different source examples."},
            {"source_row_ids": ["ROW-0002"], "representative_row_id": "ROW-0002",
             "concept_title": "Median", "description": "", "achieving_mastery": "",
             "rationale": "The median is a distinct statistic taught after the mean."},
        ],
        "type_order": ["TYPE-0002", "TYPE-0001", "TYPE-0003"],
        "case_order": ["TYPE-0002::CASE-0002", "TYPE-0001::CASE-0001", "TYPE-0003::CASE-0003"],
        "question_order": ["QINV-0002", "QINV-0001", "QINV-0003"],
    }


def test_api_merges_equivalent_capabilities_preserving_origins_routes_and_questions():
    env, settled, hosts = _fixture()
    original = copy.deepcopy((env, settled, hosts))
    captured, reviews = [], []
    def author(payload):
        captured.append(payload)
        return _verdict()
    def critic(payload):
        reviews.append(payload)
        return {"verdict": "dissent", "confidence": 0.99, "issues": ["ROW-0001 verify the source explanation"]}
    store = kernel.DecisionStore()
    rows, updated = coherence.consolidate(env, settled, hosts, provider=author, critic=critic, store=store)
    assert len(captured) == len(reviews) == 1
    assert captured[0]["canonical_source"] == env["canonical"]
    assert captured[0]["question_inventory"] == env["inventory"]
    assert (env, settled, hosts) == original
    assert [row["concept_title"] for row in rows] == ["Arithmetic Mean", "Median"]
    assert rows[0]["_source_block_ids"] == ["BLK-1", "BLK-3"]
    assert rows[0]["_reference_block_ids"] == ["REF-1", "REF-3"]
    assert rows[0]["_phase32_origin_concept_ids"] == ["ORIGIN-1", "ORIGIN-3"]
    assert rows[0][coherence.AUDIT_FIELD]["original_rows"] == [settled[0], hosts["new_concepts"][0]]
    assert "verify the source explanation" in " ".join(rows[0]["review_flags"])
    assert "retained evidence flag 3" in rows[0]["review_flags"]
    assert rows[1]["concept_details"] == settled[1]["concept_details"]
    assert set(updated["qid_map"]) == set(hosts["qid_map"])
    assert updated["qid_map"]["QINV-0002"]["concept_title"] == "Arithmetic Mean"
    assert updated["coherence_original_routes"]["qid_map"] == hosts["qid_map"]
    assert updated["new_concepts"] == []
    assert updated[coherence.ORDER_FIELD]["question_order"] == _verdict()["question_order"]
    assert coherence.consolidate(env, settled, hosts, provider=author, critic=critic, store=store) == (rows, updated)
    assert len(captured) == len(reviews) == 1


def test_historical_run_neither_calls_nor_reorders():
    env, settled, hosts = _fixture()
    env["metadata"].clear()
    def forbidden(_payload):
        raise AssertionError("historical run attempted a new decision")
    assert coherence.consolidate(env, settled, hosts, provider=forbidden) == (settled, hosts)


@pytest.mark.parametrize("mutation, expected", [
    (lambda v: v["concepts"][0]["source_row_ids"].append("ROW-0002"), "exactly once"),
    (lambda v: v["concepts"].pop(), "exactly once"),
    (lambda v: v["question_order"].pop(), "identity permutation"),
    (lambda v: v["question_order"].__setitem__(0, "QINV-0001"), "identity permutation"),
    (lambda v: v["type_order"].append("TYPE-9999"), "identity permutation"),
    (lambda v: v["concepts"][1].__setitem__("description", "Rewritten singleton"), "singleton"),
    (lambda v: v["concepts"][0].__setitem__("questions", ["invented question"]), "schema"),
])
def test_gate_rejects_lost_duplicated_or_invented_identities(mutation, expected):
    env, settled, hosts = _fixture()
    rows = {f"ROW-{i:04d}": row for i, row in enumerate([*settled, *hosts["new_concepts"]], 1)}
    verdict = _verdict()
    mutation(verdict)
    assert expected in " ".join(coherence._checker(rows, ["TOPIC-1"], coherence._ids(env, hosts))(verdict))


def test_recorded_literary_plan_cannot_be_folded_into_another_concept():
    env, settled, hosts = _fixture()
    settled[0]["_aegis_language_plan_identity"] = {"plan_concept_id": "LENS-1"}
    hosts["new_concepts"][0]["_aegis_language_plan_identity"] = {"plan_concept_id": "LENS-2"}
    rows = {f"ROW-{i:04d}": row for i, row in enumerate([*settled, *hosts["new_concepts"]], 1)}
    assert "language-plan" in " ".join(coherence._checker(rows, ["TOPIC-1"], coherence._ids(env, hosts))(_verdict()))


@pytest.mark.parametrize("repeat_identity", [False, True])
def test_planned_capability_absorbs_its_host_duplicate_without_losing_plan_identity(repeat_identity):
    env, settled, hosts = _fixture()
    identity = {"plan_topic_id": "TOPIC-1", "plan_concept_id": "LENS-1"}
    settled[0]["_aegis_language_plan_identity"] = identity
    if repeat_identity:
        hosts["new_concepts"][0]["_aegis_language_plan_identity"] = copy.deepcopy(identity)
    rows, _ = coherence.consolidate(env, settled, hosts, provider=lambda _: _verdict())
    assert len(rows) == 2
    assert rows[0]["_aegis_language_plan_identity"] == identity
    assert rows[0]["concept_title"] == settled[0]["concept_title"]


def test_unplanned_host_duplicate_cannot_replace_the_recorded_plan_representative():
    env, settled, hosts = _fixture()
    settled[0]["_aegis_language_plan_identity"] = {"plan_concept_id": "LENS-1"}
    rows = {f"ROW-{i:04d}": row for i, row in enumerate([*settled, *hosts["new_concepts"]], 1)}
    verdict = _verdict()
    verdict["concepts"][0]["representative_row_id"] = "ROW-0003"
    assert "planned representative" in " ".join(coherence._checker(rows, ["TOPIC-1"], coherence._ids(env, hosts))(verdict))


def test_renderer_uses_model_order_and_keeps_historical_default():
    env, _, _ = _fixture()
    types, cases = assemble._type_catalog(env)
    hosted = {f"TYPE-{i:04d}": {f"CASE-{i:04d}": [f"Source question {i}?"]} for i in range(1, 4)}
    output = assemble.render_types_section(hosted, types=types, cases=cases,
                                          type_order=_verdict()["type_order"], case_order=_verdict()["case_order"])
    assert output.index("Source question 2?") < output.index("Source question 1?") < output.index("Source question 3?")
    old = assemble.render_types_section(hosted, types=types, cases=cases)
    assert old.index("Source question 1?") < old.index("Source question 2?")


def test_same_case_examples_follow_qid_order_preserving_table_content():
    env, _, _ = _fixture()
    examples = env["mined_types"]["types"][0]["case_prompts"][0]["examples"]
    examples.append({"source_question_id": "QINV-0002", "example_prompt": r"Read [Katex]\begin{array}{cc}0&2\\1&3\end{array}[/Katex]."})
    before = copy.deepcopy(examples)
    _, cases = assemble._type_catalog(env, question_order=["QINV-0002", "QINV-0001"])
    chosen = cases[("TYPE-0001", "CASE-0001")]["examples"]
    assert [entry["qid"] for entry in chosen] == ["QINV-0002", "QINV-0001"]
    assert chosen[0]["prompt"] == examples[1]["example_prompt"]
    assert examples == before


def test_full_assembly_and_deposit_normalization_preserve_decided_case_and_qid_order():
    from app.services.phase3 import envelope
    from tests import test_phase3_case_uniqueness as fixture

    env = fixture._fanout_envelope()
    env["metadata"][quality.KEY] = quality.VERSION
    mined = env["mined_types"]["types"][0]
    first_case = mined["case_prompts"][0]
    second_case = copy.deepcopy(first_case)
    second_case["case_id"] = "CASE-0002"
    second_case["case_title"] = "Use a second setup"
    second_case["examples"] = first_case["examples"][1:]
    first_case["examples"] = first_case["examples"][:1]
    mined["case_prompts"].append(second_case)
    env["envelope_sha256"] = envelope.seal_sha256(env)
    hosts = fixture._fanout_hosts()
    first_home = hosts["qid_map"]["QINV-0101"]
    hosts["qid_map"]["QINV-0102"] = copy.deepcopy(first_home)
    hosts["host_map"]["TYPE-0001::CASE-0002::0001"] = copy.deepcopy(first_home)
    hosts[coherence.ORDER_FIELD] = {
        "type_order": ["TYPE-0001"],
        "case_order": ["TYPE-0001::CASE-0002", "TYPE-0001::CASE-0001"],
        "question_order": ["QINV-0102", "QINV-0101"],
    }
    before = copy.deepcopy(hosts)
    result = assemble.assemble(env, fixture._settled_rows(), hosts)
    rendered = result["rows"][0]
    assert rendered["_aegis_release_qids"] == ["QINV-0102", "QINV-0101"]
    details = rendered["concept_details"]
    assert details.index(fixture.PROMPT_B) < details.index(fixture.PROMPT_A)
    assert details.count(fixture.PROMPT_A) == details.count(fixture.PROMPT_B) == 1
    assert hosts == before
