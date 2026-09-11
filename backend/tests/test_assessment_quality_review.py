"""Current demand/context review reaches live systems without unfreezing items."""
from __future__ import annotations

import copy

import pytest

from app.services import assessment_item_review as item_review
from app.services import assessment_master_refiner as refiner
from app.services import assessment_response_policy as response_policy
from app.services import generation_quality_policy as quality
from app.services import source_task_polishing_policy as source_format
from app.services.phase3 import kernel
from tests.test_assessment_master_refiner import (
    _METADATA, _payload as master_fixture, _proposal, _without_refiner_bookkeeping,
    DESCRIPTIVE_ID, ENVELOPE_SHA256,
)


def _forbidden(_request):
    raise AssertionError("historical/current replay must not call an authority")


@pytest.mark.parametrize("carrier", ["metadata", "candidate", "atom"])
def test_joint_review_carries_demand_and_context_contract_without_mutating_item(carrier):
    metadata = {}
    candidate = {
        "candidate_id": "CAND-ONE", "question": "Find the mean.",
        "question_text": "Find the mean from the supplied frequency table.",
        "sheet_kind": "descriptive",
    }
    atom = {
        "source_qid": "QINV-0001", "raw_text": "Find the mean.",
        "tables": [{"headers": ["Value", "Frequency"], "rows": [[2, 3]]}],
        "shared_context": "Complete source chapter exposition remains evidence.",
        "learner_context": "", "frozen_task_text": candidate["question_text"],
    }
    {"metadata": metadata, "candidate": candidate, "atom": atom}[carrier][quality.KEY] = quality.VERSION
    before = copy.deepcopy((candidate, atom))
    requests = []

    def reviewer(payload):
        requests.append(copy.deepcopy(payload))
        return {
            "candidate_id": payload["candidate_id"], "verdict": "dissent",
            "confidence": 0.9,
            "issues": ["Keep the complete required table with the frozen task."],
        }

    store = kernel.DecisionStore()
    arguments = dict(
        units=[(candidate, {}, atom)], meta=metadata,
        profile={"name": "reference-1", "appears_in": "Practice"},
        envelope_sha256=ENVELOPE_SHA256, store=store,
    )
    first = item_review.review_items(provider=reviewer, **arguments)
    assert (candidate, atom) == before
    assert len(requests) == 1
    request = requests[0]
    assert request[quality.KEY] == quality.VERSION
    assert request["source_atom"] == atom
    assert request["item"] == candidate
    assert response_policy.QUALITY_AUTHOR_RULES in request["rules"]
    assert source_format.CONTEXT_REVIEW_RULES in request["rules"]
    assert first[0]["authority"]["policy_version"].endswith(";" + quality.VERSION)
    assert first[0]["verdict"] == "dissent"
    assert first[0]["review_flags"]
    assert item_review.review_items(provider=_forbidden, **arguments) == first


@pytest.mark.parametrize("carrier", ["metadata", "candidate", "atom"])
def test_master_quality_context_joins_only_the_recorded_source_and_blueprint(carrier):
    master = master_fixture()
    record = next(item for item in master["candidates"] if item["candidate_id"] == DESCRIPTIVE_ID)
    atom = next(item for item in master["source_atoms"] if item["source_qid"] in record["source_atom_ids"])
    metadata = {}
    {"metadata": metadata, "candidate": record, "atom": atom}[carrier][quality.KEY] = quality.VERSION
    atom["tables"] = [{"headers": ["Value", "Frequency"], "rows": [[2, 3]]}]
    context = refiner._quality_unit_context(master, "candidate", record, metadata)
    assert context[quality.KEY] == quality.VERSION
    assert context["source_atoms"] == [atom]
    assert [cell["cell_id"] for cell in context["blueprint_cells"]] == [record["blueprint_cell_id"]]
    request = refiner._unit_payload(
        unit_kind="candidate", unit_id=record["candidate_id"], record=record,
        rendered_rows=[], metadata=metadata, instruction_set=None, context=context,
    )
    assert request[quality.KEY] == quality.VERSION
    assert request["candidate"]["question"] == record["question"]
    assert request["candidate"]["question_text"] == record["question_text"]
    for key in ("rules", "critic_rules"):
        assert response_policy.QUALITY_AUTHOR_RULES in request[key]
        assert source_format.CONTEXT_REVIEW_RULES in request[key]
        assert refiner._QUALITY_FROZEN_BOUNDARY in request[key]


@pytest.mark.parametrize("unit_kind", ["candidate", "group"])
def test_live_master_author_uses_current_payload_rules_and_exact_legacy_system(
    monkeypatch, unit_kind,
):
    from app.services import generation

    systems = []

    def capture(system, user, **kwargs):
        systems.append(system)
        return {}

    monkeypatch.setattr(generation, "_openai_json", capture)
    arguments = dict(
        unit_kind=unit_kind, unit_id="record-1", record={}, rendered_rows=[],
        instruction_set=None, context={},
    )
    old = refiner._unit_payload(metadata={}, **arguments)
    current = refiner._unit_payload(metadata={quality.KEY: quality.VERSION}, **arguments)
    refiner._live_author(old)
    refiner._live_author(current)
    refiner._live_critic(current)
    assert systems[0] == (refiner.CANDIDATE_SYSTEM if unit_kind == "candidate" else refiner.GROUP_SYSTEM)
    assert systems[1] == current["rules"]
    assert systems[2] == current["critic_rules"]
    assert refiner._QUALITY_FROZEN_BOUNDARY not in systems[0]


def test_current_master_dissent_keeps_all_frozen_fields_and_replays():
    original = master_fixture()
    requests = []

    def provider(request):
        requests.append(copy.deepcopy(request))
        return _proposal(request)

    def critic(request):
        return {
            "verdict": "dissent", "confidence": 0.9,
            "issues": ["Classification/context concern is a finding; keep the frozen task."],
        }

    arguments = dict(
        records=[original], metadata={**_METADATA, quality.KEY: quality.VERSION},
        envelope_sha256=ENVELOPE_SHA256, store=kernel.DecisionStore(),
    )
    result, diff, flags = refiner.refine_master(provider=provider, critic=critic, **arguments)
    assert requests
    assert _without_refiner_bookkeeping(result[0]) == original
    assert diff["changes"] == []
    assert flags
    for request in requests:
        assert request[quality.KEY] == quality.VERSION
        assert refiner._QUALITY_FROZEN_BOUNDARY in request["rules"]
        if request["unit_kind"] == "candidate":
            assert request["context"]["source_atoms"]
    for record in result[0]["candidates"]:
        assert record[refiner.AUDIT_FIELD]["policy_version"].endswith(";" + quality.VERSION)
    replay = refiner.refine_master(provider=_forbidden, critic=_forbidden, fixer=_forbidden, **arguments)
    assert replay == (result, diff, flags)
