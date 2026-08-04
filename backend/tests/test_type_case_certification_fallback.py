"""Ownership is decided by the model, and the model gets asked again.

Phase 3.3 certifies semantic ownership per QID with a provider/critic pair.
A batch can come back unusable -- most often because one claim in it was
hard, or because the provider collapsed a sub-part qid like ``QINV-0016.2``
into its parent, which is the exact defect that lost four tasks in
production.

Treating that as final ended the run. It is not final: a batch fails as a
*batch*, and the pair answers correctly when asked about the awkward claim
on its own. So a failure splits and re-asks, down to single claims.

What deterministic code must never do is answer the question in the model's
place. Ownership is "does understanding this task require that topic's
method?", and no amount of word counting can tell that apart from "happens
to share a word". When the pair genuinely will not answer, the task is
recorded as *unplaced* -- it keeps the topic it is printed under, labelled
as provenance -- and the run continues.
"""
from __future__ import annotations

import pytest

from app.services import (
    canonical_source_phase33_preflight_contract as p33,
)
from app.services import generation, placement_policy as pp


def _graph():
    return {
        "source_contract_hash": "sc",
        "topics": [
            {"topic_id": "T-52", "title": "Arithmetic Progressions"},
            {"topic_id": "T-53", "title": "nth Term of an AP"},
            {"topic_id": "T-54", "title": "Sum of First n Terms"},
        ],
        "blocks": [
            {"block_id": "B52", "topic_id": "T-52", "order": 10,
             "kind": "paragraph"},
            {"block_id": "B54", "topic_id": "T-54", "order": 90,
             "kind": "paragraph"},
        ],
    }


def _canonical():
    return {"blocks": [
        {"block_id": "B52", "display_text": "The common difference is d."},
        {"block_id": "B54", "display_text": "The sum is Sn = n/2[2a+(n-1)d]."},
    ]}


@pytest.fixture
def order():
    return pp.seal_teaching_order(_graph(), source_contract_hash="sc")


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(p33.progress, "log", lambda *_a, **_k: None)


def _claim(qid, *, located="T-52"):
    return {
        "qid": qid,
        "claim_id": f"CLAIM-{qid}",
        "normalized_claim": f"Task {qid}.",
        "source_location_topic_id": located,
    }


def _contract(qid, owner):
    """A contract shaped like one the certification pair would produce."""
    return {"qid": qid, "owner_topic_id": owner, "certified": True}


def _driver(monkeypatch, answers):
    """Run the re-ask driver against a scripted certification pair.

    ``answers`` maps a frozenset of claim IDs to the contracts that batch
    returns, or to an exception it raises. Anything unscripted raises, which
    is what a real unusable batch response does.
    """
    seen: list[frozenset[str]] = []

    def fake_batch(_generation, *, claims, **_kwargs):
        key = frozenset(str(row["claim_id"]) for row in claims)
        seen.append(key)
        outcome = answers.get(key)
        if outcome is None:
            raise ValueError("placement provider failed its bounded contract")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(p33, "_certify_type_case_batch", fake_batch)
    return seen


# --------------------------------------------------------------------------- #
# The model is asked again, not replaced
# --------------------------------------------------------------------------- #

def test_one_awkward_claim_does_not_lose_the_whole_batch(monkeypatch, order):
    """The production shape: three qids fine, one sub-part reformatted."""
    claims = [_claim("QINV-0016"), _claim("QINV-0016.2")]
    seen = _driver(monkeypatch, {
        # Asked alone, the pair places the sub-part correctly.
        frozenset({"CLAIM-QINV-0016"}): {
            "QINV-0016": _contract("QINV-0016", "T-52")},
        frozenset({"CLAIM-QINV-0016.2"}): {
            "QINV-0016.2": _contract("QINV-0016.2", "T-54")},
    })
    contracts: dict[str, dict] = {}

    unanswered = p33._reask_type_case_batch(
        generation,
        claims=claims,
        graph=_graph(),
        canonical=_canonical(),
        order=order,
        block_directory={},
        provider=object(),
        critic=object(),
        contracts=contracts,
    )

    assert unanswered == []
    assert contracts["QINV-0016.2"]["owner_topic_id"] == "T-54"
    # The whole batch was tried first, then split -- not split pre-emptively.
    assert seen[0] == frozenset({"CLAIM-QINV-0016", "CLAIM-QINV-0016.2"})


def test_a_partial_response_only_re_asks_what_is_missing(monkeypatch, order):
    claims = [_claim("QINV-0001"), _claim("QINV-0002")]
    both = frozenset({"CLAIM-QINV-0001", "CLAIM-QINV-0002"})
    seen = _driver(monkeypatch, {
        # The pair answers for one qid and silently drops the other.
        both: {"QINV-0001": _contract("QINV-0001", "T-52")},
        frozenset({"CLAIM-QINV-0002"}): {
            "QINV-0002": _contract("QINV-0002", "T-54")},
    })
    contracts: dict[str, dict] = {}

    unanswered = p33._reask_type_case_batch(
        generation,
        claims=claims,
        graph=_graph(),
        canonical=_canonical(),
        order=order,
        block_directory={},
        provider=object(),
        critic=object(),
        contracts=contracts,
    )

    assert unanswered == []
    assert set(contracts) == {"QINV-0001", "QINV-0002"}
    # The already-answered claim is not paid for twice.
    assert seen == [both, frozenset({"CLAIM-QINV-0002"})]


def test_re_asking_is_bounded(monkeypatch, order):
    """A pair that never answers must not be asked forever."""
    monkeypatch.setenv("AEGIS_TYPE_CASE_PLACEMENT_REASK_DEPTH", "1")
    claims = [_claim(f"QINV-000{n}") for n in (1, 2, 3, 4)]
    seen = _driver(monkeypatch, {})
    contracts: dict[str, dict] = {}

    unanswered = p33._reask_type_case_batch(
        generation,
        claims=claims,
        graph=_graph(),
        canonical=_canonical(),
        order=order,
        block_directory={},
        provider=object(),
        critic=object(),
        contracts=contracts,
    )

    assert sorted(unanswered) == [f"CLAIM-QINV-000{n}" for n in (1, 2, 3, 4)]
    assert len(seen) < 20  # bounded, not runaway


def test_a_recoverable_pause_is_not_swallowed_by_the_re_ask(
    monkeypatch, order
):
    """The recovery ladder above can still answer it properly."""
    pause = p33.HumanDecisionRequired({
        "decision_id": "phase33-placement", "context_hash": "a" * 64,
    })
    _driver(monkeypatch, {frozenset({"CLAIM-QINV-0001"}): pause})

    with pytest.raises(p33.HumanDecisionRequired):
        p33._reask_type_case_batch(
            generation,
            claims=[_claim("QINV-0001")],
            graph=_graph(),
            canonical=_canonical(),
            order=order,
            block_directory={},
            provider=object(),
            critic=object(),
            contracts={},
        )


# --------------------------------------------------------------------------- #
# What happens when the model genuinely will not answer
# --------------------------------------------------------------------------- #

def test_an_unplaced_task_survives_and_keeps_its_printed_topic(order):
    contracts: dict[str, dict] = {}

    missing = p33._record_unplaced_type_case_claims(
        generation,
        claim_by_qid={"QINV-0009": _claim("QINV-0009", located="T-53")},
        contracts=contracts,
        graph=_graph(),
        order=order,
    )

    assert missing == ["QINV-0009"]
    contract = contracts["QINV-0009"]
    assert contract["owner_topic_id"] == "T-53"
    assert contract["basis"] == pp.UNPLACED_BASIS
    assert contract["certified"] is False
    assert "provenance" in contract["rationale"]


def test_an_unplaced_contract_asserts_no_ownership(order):
    """It must not read as a certified placement anywhere downstream."""
    contracts: dict[str, dict] = {}
    p33._record_unplaced_type_case_claims(
        generation,
        claim_by_qid={"QINV-0009": _claim("QINV-0009", located="T-53")},
        contracts=contracts,
        graph=_graph(),
        order=order,
    )

    assert contracts["QINV-0009"]["topic_relationships"] == []
    assert generation._certified_type_case_placement_contract(
        contracts["QINV-0009"]
    ) is None


def test_nothing_is_recorded_when_the_pair_answered_for_everything(order):
    contracts = {"QINV-0001": _contract("QINV-0001", "T-52")}

    assert p33._record_unplaced_type_case_claims(
        generation,
        claim_by_qid={"QINV-0001": _claim("QINV-0001")},
        contracts=contracts,
        graph=_graph(),
        order=order,
    ) == []


def test_routing_honours_an_unplaced_disposition_without_stopping(monkeypatch):
    """The guard that used to fire on any live run."""
    from app.services import canonical_source_phase3 as phase3

    monkeypatch.setattr(generation.config, "use_live_generation", lambda: True)
    contracts: dict[str, dict] = {}
    p33._record_unplaced_type_case_claims(
        generation,
        claim_by_qid={"QINV-0009": _claim("QINV-0009", located="T-53")},
        contracts=contracts,
        graph=_graph(),
        order=pp.seal_teaching_order(_graph(), source_contract_hash="sc"),
    )
    item = {
        "qid": "QINV-0009",
        "source_kind": "activity",
        "topic_hint": "Some Printed Heading",
        "_type_case_placement_contract": contracts["QINV-0009"],
    }

    with phase3.activate(_graph()):
        owner_id, owner_title = generation._inventory_item_owner_topic(item)

    # The recorded provenance is used, not the free-text printed heading.
    assert owner_id == "T-53"
    assert owner_title == "nth Term of an AP"


def test_a_live_item_with_no_disposition_at_all_still_fails_closed(
    monkeypatch,
):
    """Skipping Phase 3.3 entirely is an integrity fault, not a placement."""
    from app.services import canonical_source_phase3 as phase3

    monkeypatch.setattr(generation.config, "use_live_generation", lambda: True)
    item = {"qid": "QINV-0010", "source_kind": "activity",
            "topic_hint": "Printed Heading"}

    with phase3.activate(_graph()):
        with pytest.raises(RuntimeError, match="type_case_owner_uncertified"):
            generation._inventory_item_owner_topic(item)


def test_no_placement_basis_other_than_certification_yields_an_owner(order):
    """Ownership has exactly one legitimate source: the certified pair."""
    contracts: dict[str, dict] = {}
    p33._record_unplaced_type_case_claims(
        generation,
        claim_by_qid={"QINV-0009": _claim("QINV-0009")},
        contracts=contracts,
        graph=_graph(),
        order=order,
    )
    forged = dict(contracts["QINV-0009"])
    forged["basis"] = pp.CERTIFIED_BASIS
    forged["certified"] = True
    forged["placement_certificate_sha256"] = (
        generation._type_case_placement_digest({
            key: value for key, value in forged.items()
            if key != "placement_certificate_sha256"
        })
    )

    # Relabelling it does not create the relationships it never had.
    assert generation._certified_type_case_placement_contract(forged) is None
