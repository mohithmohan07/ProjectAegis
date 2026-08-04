"""Independent certification may fail; the run may not.

Phase 3.3 certifies semantic ownership per QID with a paid provider/critic
pair. A pair can omit a QID, reformat one, spend its contract-correction
budget, or have its relationship set rejected -- and each of those used to
raise ``type_case_owner_uncertified`` and end the job. That is the same
defect as the chapter-wide placement crash, one stage later.

Rule 5 has a deterministic reading, so it is applied instead:

    a task belongs to the latest topic whose source text it requires

These tests pin both halves of that: the run continues, and the fallback
owner comes from teaching order rather than from where the task is printed.
"""
from __future__ import annotations

import pytest

from app.services import (
    canonical_source_phase33_preflight_contract as p33,
)
from app.services import generation, placement_policy as pp


_T52 = (
    "An arithmetic progression is a list of numbers in which each term is "
    "obtained by adding a fixed number, the common difference d, to the "
    "preceding term."
)
_T53 = (
    "The nth term of an AP with first term a and common difference d is "
    "given by an = a + (n-1)d."
)
_T54 = (
    "The sum of the first n terms of an AP is Sn = n/2 [2a + (n-1)d]. "
    "Substituting the nth term gives Sn = n/2 (a + an)."
)


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
            {"block_id": "B53", "topic_id": "T-53", "order": 50,
             "kind": "paragraph"},
            {"block_id": "B54", "topic_id": "T-54", "order": 90,
             "kind": "paragraph"},
        ],
    }


def _canonical():
    return {"blocks": [
        {"block_id": "B52", "display_text": _T52},
        {"block_id": "B53", "display_text": _T53},
        {"block_id": "B54", "display_text": _T54},
    ]}


@pytest.fixture
def order():
    return pp.seal_teaching_order(_graph(), source_contract_hash="sc")


@pytest.fixture
def topic_blocks():
    return p33._topic_block_text(_graph(), _canonical())


def _claim(text, *, qid="QINV-0020", located="T-52"):
    return {
        "qid": qid,
        "claim_id": f"CLAIM-{qid}",
        "normalized_claim": text,
        "source_location_topic_id": located,
    }


# --------------------------------------------------------------------------- #
# Rule 5, computed rather than certified
# --------------------------------------------------------------------------- #

def test_a_combined_task_is_owned_by_the_latest_topic_it_requires(
    order, topic_blocks
):
    """Example M3: a task needing both aâ‚™ and Sâ‚™ belongs to Â§5.4."""
    claim = pp.AtomicClaim(
        claim_id="CLAIM-1",
        normalized_claim=(
            "Find the sum of the first n terms of an AP whose nth term "
            "is given, using the first term a and common difference d."
        ),
        source_location_topic_id="T-53",
    )
    decision, relationships = pp.compute_deterministic_placement(
        claim, order, topic_blocks
    )

    assert decision.owner_topic_id == "T-54"
    # The earlier topics it also needs are prerequisites, not owners.
    assert "T-52" in decision.prerequisite_topic_ids
    assert decision.owner_topic_id not in decision.prerequisite_topic_ids
    assert all(
        relation.evidence_block_ids for relation in relationships
    )


def test_printed_location_never_becomes_the_owner(order, topic_blocks):
    """The task sits in Â§5.2 but needs the sum method; Â§5.4 owns it."""
    claim = pp.AtomicClaim(
        claim_id="CLAIM-2",
        normalized_claim=(
            "Compute the sum of the first twenty terms of this progression."
        ),
        source_location_topic_id="T-52",
    )
    decision, _relationships = pp.compute_deterministic_placement(
        claim, order, topic_blocks
    )

    assert decision.owner_topic_id == "T-54"


def test_a_task_needing_only_the_early_topic_stays_there(order, topic_blocks):
    claim = pp.AtomicClaim(
        claim_id="CLAIM-3",
        normalized_claim=(
            "Identify the common difference of the given arithmetic "
            "progression."
        ),
        source_location_topic_id="T-52",
    )
    decision, _relationships = pp.compute_deterministic_placement(
        claim, order, topic_blocks
    )

    assert decision.owner_topic_id == "T-52"


def test_an_unevidenced_task_keeps_its_location_and_says_so(
    order, topic_blocks
):
    """Last resort only: the source evidences no topic at all."""
    claim = pp.AtomicClaim(
        claim_id="CLAIM-4",
        normalized_claim="Discuss Babylonian astronomical diaries from Uruk.",
        source_location_topic_id="T-53",
    )
    decision, relationships = pp.compute_deterministic_placement(
        claim, order, topic_blocks
    )

    assert decision.owner_topic_id == "T-53"
    assert relationships == []
    assert "not a certified owner" in decision.rationale


def test_the_derivation_is_deterministic(order, topic_blocks):
    claim = pp.AtomicClaim(
        claim_id="CLAIM-5",
        normalized_claim="Find the sum of the first n terms using a and d.",
        source_location_topic_id="T-52",
    )
    first = pp.compute_deterministic_placement(claim, order, topic_blocks)
    second = pp.compute_deterministic_placement(claim, order, topic_blocks)

    assert first == second


def test_a_single_shared_word_does_not_move_a_task(order, topic_blocks):
    """One coincidental term is not a dependency."""
    claim = pp.AtomicClaim(
        claim_id="CLAIM-6",
        normalized_claim=(
            "Identify the common difference between consecutive terms."
        ),
        source_location_topic_id="T-52",
    )
    decision, _relationships = pp.compute_deterministic_placement(
        claim, order, topic_blocks
    )

    assert decision.owner_topic_id == "T-52"


# --------------------------------------------------------------------------- #
# The contract the fallback produces is honest and usable
# --------------------------------------------------------------------------- #

def _fallback_contract(claim_row, order, topic_blocks):
    return p33._deterministic_type_case_contract(
        generation,
        claim_row=claim_row,
        graph=_graph(),
        order=order,
        topic_blocks=topic_blocks,
    )


def test_a_fallback_contract_is_accepted_downstream(order, topic_blocks):
    """Routing must be able to use it, or the fallback achieves nothing."""
    contract = _fallback_contract(
        _claim("Find the sum of the first n terms using a and d."),
        order,
        topic_blocks,
    )

    assert generation._certified_type_case_placement_contract(contract) is not None
    assert contract["owner_topic_id"] == "T-54"


def test_a_fallback_contract_never_claims_to_be_certified(
    order, topic_blocks
):
    """An audit must be able to tell the two bases apart."""
    contract = _fallback_contract(
        _claim("Find the sum of the first n terms using a and d."),
        order,
        topic_blocks,
    )

    assert contract["certified"] is False
    assert contract["basis"] == pp.DETERMINISTIC_BASIS
    assert all(
        row["critic_verdict"] == pp.DETERMINISTIC_VERDICT
        for row in contract["topic_relationships"]
    )


def test_a_forged_certified_flag_is_rejected(order, topic_blocks):
    """Relabelling a deterministic contract as reviewed must not work."""
    contract = _fallback_contract(
        _claim("Find the sum of the first n terms using a and d."),
        order,
        topic_blocks,
    )
    contract["certified"] = True
    contract["basis"] = pp.CERTIFIED_BASIS
    contract["placement_certificate_sha256"] = (
        generation._type_case_placement_digest({
            key: value for key, value in contract.items()
            if key != "placement_certificate_sha256"
        })
    )

    # The verdicts are still "deterministic", which the certified basis does
    # not accept, so no owner relationship survives the check.
    assert generation._certified_type_case_placement_contract(contract) is None


def test_a_tampered_fallback_contract_is_rejected(order, topic_blocks):
    contract = _fallback_contract(
        _claim("Find the sum of the first n terms using a and d."),
        order,
        topic_blocks,
    )
    contract["owner_topic_id"] = "T-52"

    assert generation._certified_type_case_placement_contract(contract) is None


def test_an_unknown_basis_is_rejected(order, topic_blocks):
    contract = _fallback_contract(
        _claim("Find the sum of the first n terms using a and d."),
        order,
        topic_blocks,
    )
    contract["basis"] = "trust_me"
    contract["placement_certificate_sha256"] = (
        generation._type_case_placement_digest({
            key: value for key, value in contract.items()
            if key != "placement_certificate_sha256"
        })
    )

    assert generation._certified_type_case_placement_contract(contract) is None


# --------------------------------------------------------------------------- #
# Gap filling
# --------------------------------------------------------------------------- #

def test_only_the_uncovered_qids_are_filled(order, monkeypatch):
    monkeypatch.setattr(p33.progress, "log", lambda *_a, **_k: None)
    claim_by_qid = {
        "QINV-0001": _claim("Identify the common difference.", qid="QINV-0001"),
        "QINV-0002": _claim(
            "Find the sum of the first n terms using a and d.",
            qid="QINV-0002",
        ),
    }
    certified = {"QINV-0001": {"owner_topic_id": "T-52", "certified": True}}
    contracts = dict(certified)

    filled = p33._fill_uncertified_type_case_contracts(
        generation,
        claim_by_qid=claim_by_qid,
        contracts=contracts,
        graph=_graph(),
        canonical=_canonical(),
        order=order,
        reason="test",
    )

    assert [entry.split("->")[0] for entry in filled] == ["QINV-0002"]
    # The already-certified contract is untouched.
    assert contracts["QINV-0001"] is certified["QINV-0001"]
    assert contracts["QINV-0002"]["owner_topic_id"] == "T-54"
    assert contracts["QINV-0002"]["certified"] is False


# --------------------------------------------------------------------------- #
# The consumer side degrades too
# --------------------------------------------------------------------------- #

def test_a_live_item_without_a_certified_owner_derives_one(monkeypatch):
    """The guard that would have fired on every live run.

    Previously any inventory QID reaching Hub routing without a certified v2
    contract raised ``type_case_owner_uncertified`` and ended the job.
    """
    from app.services import canonical_source_phase3 as phase3

    monkeypatch.setattr(generation.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(generation.progress, "log", lambda *_a, **_k: None)
    item = {
        "qid": "QINV-0001",
        "source_kind": "activity",
        "raw_task": "Find the sum of the first n terms using a and d.",
        # Printed early; must not decide ownership.
        "topic_hint": "Arithmetic Progressions",
    }

    with phase3.activate(_graph()):
        with phase3.activate_session({"canonical": _canonical()}):
            owner_id, owner_title = generation._inventory_item_owner_topic(item)

    assert owner_id == "T-54"
    assert owner_title == "Sum of First n Terms"


def test_derivation_reads_the_item_without_mutating_it(monkeypatch):
    from app.services import canonical_source_phase3 as phase3

    monkeypatch.setattr(generation.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(generation.progress, "log", lambda *_a, **_k: None)
    item = {
        "qid": "QINV-0002",
        "source_kind": "activity",
        "raw_task": "Identify the common difference of this progression.",
        "topic_hint": "Arithmetic Progressions",
    }
    before = dict(item)

    with phase3.activate(_graph()):
        with phase3.activate_session({"canonical": _canonical()}):
            generation._inventory_item_owner_topic(item)

    assert item == before


def test_filling_is_a_no_op_when_certification_covered_everything(order):
    contracts = {"QINV-0001": {"owner_topic_id": "T-52"}}

    assert p33._fill_uncertified_type_case_contracts(
        generation,
        claim_by_qid={"QINV-0001": _claim("x", qid="QINV-0001")},
        contracts=contracts,
        graph=_graph(),
        canonical=_canonical(),
        order=order,
        reason="test",
    ) == []
