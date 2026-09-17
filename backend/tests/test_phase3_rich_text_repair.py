"""The repair that stops a run dying on one stray character (register Q74).

Two of the owner's chapters — BLK-00312 "The Inter-war Economy" and BLK-00348
"Conservation of Energy Resources" — ran the whole Phase 3 hierarchy pass, hit
a block whose text carried markup the rich-text contract refuses, found no
repair route, and stopped. ``_SUSPICIOUS_MARKUP_RE`` names six chemistry and
MathML tags, so a stray ``$`` never reached the repair lane at all; and the
lane's only move is to select a DIFFERENT verified page block, which cannot
help when the defect lives in that block's own transcription.

These tests pin the lane that fixes it: the gate's own reported block ids
choose the targets, the model re-expresses the text against the page evidence,
and eight gates stand between a proposal and the graph. The last of those
matters most — this is the first place in the source lane where a model
produces source BYTES rather than selecting an opaque evidence id, so a
canonical-looking paraphrase must be refused as firmly as a defect.
"""
from __future__ import annotations

from pathlib import Path
import copy
import json

import pytest

from app.services import canonical_source_phase3 as phase3


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _canonical(text: str, block_id: str = "BLK-0001") -> dict:
    return {
        "document": {"source_chars": len(text)},
        "blocks": [{
            "block_id": block_id, "order": 1, "kind": "paragraph",
            "display_text": text, "raw_text": text,
            "raw_sha256": phase3._sha256_text(text),
            "source_start": 0,
        }],
    }


def _graph(block_id: str = "BLK-0001") -> dict:
    return {"blocks": [{"block_id": block_id, "kind": "paragraph"}], "issues": []}


def _bundle() -> dict:
    return {"pages": [{
        "page_id": "PDF-PAGE-0001", "page_number": 1,
        "blocks": [{
            "reading_order": 1, "kind": "paragraph",
            "text": "The inter-war economy shrank by 30 per cent.",
            "block_key": "PDF-PAGE-0001#1",
        }],
    }]}


def _author(**overrides):
    proposal = {
        "decision": "canonicalize",
        "canonical_text": "The inter-war economy shrank by 30 per cent.",
        "suppressed": False, "confidence": 0.99, "reason": "ok",
    }
    proposal.update(overrides)
    return lambda packet: dict(proposal)


def _critic(**overrides):
    verdict = {"verdict": "verified", "confidence": 0.99, "issues": [],
               "reason": "faithful"}
    verdict.update(overrides)
    return lambda packet, proposal: dict(verdict)


def _repair(graph, canonical, author, critic, **kwargs):
    return phase3._repair_rich_text_blocks(
        graph, canonical=canonical, page_bundle=_bundle(),
        source_path=Path("/nonexistent.pdf"),
        allow_automatic_reconciliation=True,
        repair_provider=author, repair_critic=critic, **kwargs,
    )


#: Refused by the contract (``raw_latex``), and the same words once canonical.
#: Note what is NOT used here: ``$30`` reads as currency and is deliberately
#: masked by ``katex_rules._CURRENCY_TOKEN_RE``, so it never trips the gate at
#: all — the reproduced owner case was the spaced form, ``US $ 55 million``.
_DEFECT = "The inter-war economy shrank by \\textbf{30} per cent."
_CLEAN = "The inter-war economy shrank by 30 per cent."


# --------------------------------------------------------------------------- #
# The target selection — the gate names its own blocks
# --------------------------------------------------------------------------- #

def test_the_repair_targets_are_the_blocks_the_gate_itself_refused():
    """Not a keyword vocabulary. ``validate_graph`` publishes the offending
    block ids on its own error through ``_rich_text_issue_block_ids``, and the
    repair reads exactly that. Using the gate's output is strictly LESS
    heuristic than the six-tag regex the converter lane selects with."""
    canonical = _canonical(_DEFECT)
    graph = _graph()
    assert phase3._rich_text_issue_block_ids(graph, canonical=canonical) == [
        "BLK-0001"
    ]


def test_a_clean_block_is_never_touched():
    graph = _graph()
    assert _repair(graph, _canonical(_CLEAN), _author(), _critic()) == []
    assert "source_override" not in graph["blocks"][0]
    assert graph.get("rich_text_repairs") is None, (
        "a graph with nothing to repair must not grow a repair record"
    )


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("author_confidence,critic_confidence", [
    (0.90, 0.90), (0.90, 0.95), (0.95, 0.90), (0.95, 0.95), (0.99, 0.99),
])
def test_a_faithful_repair_is_accepted_and_hash_pinned(
    tmp_path, author_confidence, critic_confidence,
):
    graph = _graph()
    assert _repair(
        graph, _canonical(_DEFECT),
        _author(confidence=author_confidence), _critic(confidence=critic_confidence),
        repair_cache_dir=tmp_path,
    ) == []
    override = graph["blocks"][0]["source_override"]
    assert override["mode"] == phase3._REPAIR_MODE
    assert override["resolved_text"] == _CLEAN
    assert override["resolved_sha256"] == phase3._sha256_text(_CLEAN)
    assert override["suppressed"] is False
    assert override["repair_confidence"] == author_confidence
    assert override["verification_confidence"] == critic_confidence
    assert override["repair_confidence_minimum"] == 0.90
    assert graph["rich_text_repairs"] == [
        {"block_id": "BLK-0001", "suppressed": False}
    ]

    def no_paid_calls(*args):
        pytest.fail("an accepted repair must replay without another paid decision")

    resumed = _graph()
    assert _repair(
        resumed, _canonical(_DEFECT), no_paid_calls, no_paid_calls,
        repair_cache_dir=tmp_path,
    ) == []
    assert resumed["blocks"][0]["source_override"] == override


def test_the_repaired_text_is_what_the_renderer_then_reads():
    """An override nothing reads is not a repair."""
    graph = _graph()
    _repair(graph, _canonical(_DEFECT), _author(), _critic())
    assert phase3._graph_block_text(
        graph["blocks"][0], _canonical(_DEFECT)["blocks"][0],
    ) == _CLEAN


def test_a_tampered_override_is_ignored_rather_than_trusted():
    graph = _graph()
    _repair(graph, _canonical(_DEFECT), _author(), _critic())
    graph["blocks"][0]["source_override"]["resolved_text"] = "something else"
    assert phase3._graph_block_text(
        graph["blocks"][0], _canonical(_DEFECT)["blocks"][0],
    ) == _DEFECT, "an unpinned resolution must fall back to the source"


# --------------------------------------------------------------------------- #
# Suppression — the commonest real defect
# --------------------------------------------------------------------------- #

def test_a_block_of_pure_markup_may_resolve_to_nothing():
    """A stray display-math closer carries no readable text, and its only
    faithful repair is to emit nothing. The truthy resolved_text branch cannot
    express that, which is why suppression is its own hash-pinned branch."""
    graph = _graph()
    assert _repair(
        graph, _canonical("\\]\n"),
        _author(decision="suppress", canonical_text="", suppressed=True),
        _critic(),
    ) == []
    override = graph["blocks"][0]["source_override"]
    assert override["suppressed"] is True
    assert override["resolved_sha256"] == phase3._sha256_text("")
    assert phase3._graph_block_text(
        graph["blocks"][0], _canonical("\\]\n")["blocks"][0],
    ) == ""


def test_a_block_carrying_words_can_never_be_suppressed():
    """Deleting readable source is the one outcome worse than refusing."""
    graph = _graph()
    unresolved = _repair(
        graph, _canonical(_DEFECT),
        _author(decision="suppress", canonical_text="", suppressed=True),
        _critic(),
    )
    assert unresolved == ["BLK-0001"]
    assert "source_override" not in graph["blocks"][0]
    assert any(
        issue["code"] == "semantic_source_rich_text_repair_refused"
        and "suppression" in issue["message"]
        for issue in graph["issues"]
    )


# --------------------------------------------------------------------------- #
# Faithfulness — the gates that make this safe to do at all
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("proposed,expected_gate", [
    # A fluent paraphrase: canonical, plausible, and not what the book says.
    ("The economy contracted sharply between the wars.", "drops"),
    # An addition the source does not carry.
    ("The inter-war economy shrank by 30 per cent in Germany.", "adds"),
    # A changed number.
    ("The inter-war economy shrank by 50 per cent.", "digits"),
    # Still refused by the contract.
    ("The inter-war economy shrank by \\textbf{30} per cent.", "still refused"),
])
def test_an_unfaithful_repair_is_refused(proposed, expected_gate):
    graph = _graph()
    unresolved = _repair(
        graph, _canonical(_DEFECT),
        _author(canonical_text=proposed, confidence=0.90), _critic(confidence=0.90),
    )
    assert unresolved == ["BLK-0001"]
    assert "source_override" not in graph["blocks"][0]
    message = " ".join(issue["message"] for issue in graph["issues"])
    assert expected_gate in message, message


def test_a_changed_url_is_refused():
    before = "See ![](https://cdn.example.com/a.jpg) and \\textbf{x}"
    graph = _graph()
    unresolved = _repair(
        graph, _canonical(before),
        _author(canonical_text="See ![](https://cdn.example.com/b.jpg) and x"),
        _critic(),
    )
    assert unresolved == ["BLK-0001"]


def test_the_independent_critic_can_veto_a_repair_every_other_gate_passed():
    graph = _graph()
    unresolved = _repair(
        graph, _canonical(_DEFECT), _author(confidence=0.90),
        _critic(verdict="rejected", confidence=0.90, reason="not what the page shows"),
    )
    assert unresolved == ["BLK-0001"]
    assert "source_override" not in graph["blocks"][0]


def test_a_critic_that_verifies_but_lists_an_issue_is_still_a_refusal():
    graph = _graph()
    assert _repair(
        graph, _canonical(_DEFECT), _author(confidence=0.90),
        _critic(confidence=0.90, issues=["the second clause is invented"]),
    ) == ["BLK-0001"]


@pytest.mark.parametrize("who", ["author", "critic"])
@pytest.mark.parametrize("confidence", [
    0.899999, 0.5, float("nan"), float("inf"), float("-inf"), 1.001, None,
])
def test_invalid_or_below_floor_repair_confidence_is_refused(who, confidence):
    graph = _graph()
    author = _author(confidence=confidence) if who == "author" else _author(confidence=0.90)
    critic = _critic(confidence=confidence) if who == "critic" else _critic(confidence=0.90)
    assert _repair(graph, _canonical(_DEFECT), author, critic) == ["BLK-0001"]
    assert "source_override" not in graph["blocks"][0]


def test_an_author_asking_for_review_is_recorded_not_guessed():
    graph = _graph()
    assert _repair(
        graph, _canonical(_DEFECT),
        _author(decision="review_required", reason="the page is illegible"),
        _critic(),
    ) == ["BLK-0001"]
    assert any(
        "the page is illegible" in issue["message"] for issue in graph["issues"]
    )


@pytest.mark.parametrize("proposal", [
    {"decision": "suppress", "canonical_text": "text", "suppressed": True},
    {"decision": "canonicalize", "canonical_text": "   ", "suppressed": False},
])
def test_a_self_contradictory_proposal_is_refused(proposal):
    """Non-empty text XOR suppression. Recording either half of a
    contradiction would leave the graph asserting something untrue."""
    graph = _graph()
    payload = {"confidence": 0.99, "reason": "r", **proposal}
    assert _repair(
        graph, _canonical(_DEFECT), lambda packet: dict(payload), _critic(),
    ) == ["BLK-0001"]


# --------------------------------------------------------------------------- #
# Atomicity
# --------------------------------------------------------------------------- #

def test_one_refused_block_writes_no_override_for_any_block():
    """A half-repaired graph is strictly worse than an unrepaired one: the
    render would mix repaired and refused blocks and the gate would refuse
    anyway, having spent for it."""
    canonical = _canonical(_DEFECT)
    canonical["blocks"].append({
        "block_id": "BLK-0002", "order": 2, "kind": "paragraph",
        "display_text": "Coal output fell by \\textbf{12} per cent.",
        "raw_text": "Coal output fell by \\textbf{12} per cent.",
        "raw_sha256": phase3._sha256_text(
            "Coal output fell by \\textbf{12} per cent."),
        "source_start": 40,
    })
    graph = {"blocks": [{"block_id": "BLK-0001", "kind": "paragraph"},
                        {"block_id": "BLK-0002", "kind": "paragraph"}],
             "issues": []}

    def author(packet):
        if "Coal" in str(packet.get("refused_block_text") or ""):
            return {"decision": "canonicalize",
                    "canonical_text": "Coal output collapsed.",  # drops tokens
                    "suppressed": False, "confidence": 0.99, "reason": "r"}
        return {"decision": "canonicalize", "canonical_text": _CLEAN,
                "suppressed": False, "confidence": 0.99, "reason": "r"}

    unresolved = _repair(graph, canonical, author, _critic())
    assert unresolved == ["BLK-0002"]
    assert all("source_override" not in block for block in graph["blocks"]), (
        "the block that passed every gate must not be written either"
    )
    assert graph["rich_text_repairs"] == []


# --------------------------------------------------------------------------- #
# Honest degradation — never a silent pass
# --------------------------------------------------------------------------- #

def test_an_unattended_run_that_may_not_spend_refuses_rather_than_repairs():
    graph = _graph()
    assert phase3._repair_rich_text_blocks(
        graph, canonical=_canonical(_DEFECT), page_bundle=_bundle(),
        source_path=Path("/nonexistent.pdf"),
        allow_automatic_reconciliation=False,
        repair_provider=_author(), repair_critic=_critic(),
    ) == ["BLK-0001"]
    assert "source_override" not in graph["blocks"][0]


def test_a_text_upload_with_no_page_evidence_refuses_rather_than_inventing():
    """A .mmd/.md/.txt upload has no pages to repair AGAINST. Canonicalising
    from the defective text alone is exactly the invention these gates exist
    to prevent, so the honest answer is that it cannot be repaired."""
    graph = _graph()
    assert phase3._repair_rich_text_blocks(
        graph, canonical=_canonical(_DEFECT), page_bundle=None,
        source_path=None, allow_automatic_reconciliation=True,
        repair_provider=_author(), repair_critic=_critic(),
    ) == ["BLK-0001"]


def test_every_refusal_names_its_block_and_its_gate():
    """The recorded reason is the whole value of a refusal: without it the
    owner gets a dead chapter and no way to know why."""
    graph = _graph()
    _repair(graph, _canonical(_DEFECT), _author(confidence=0.4), _critic())
    refusals = [
        issue for issue in graph["issues"]
        if issue["code"] == "semantic_source_rich_text_repair_refused"
    ]
    assert len(refusals) == 1
    assert refusals[0]["block_ids"] == ["BLK-0001"]
    assert refusals[0]["severity"] == "warning"
    assert "author_confidence" in refusals[0]["message"]


# --------------------------------------------------------------------------- #
# The faithfulness helper on its own
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("before,after,ok", [
    # The repair this gate exists to ALLOW: markup removed, content identical.
    ("shrank by \\textbf{30} per cent", "shrank by 30 per cent", True),
    ("a \\textbf{bold} word", "a bold word", True),
    # ...and the ones it exists to refuse.
    ("shrank by \\textbf{30} per cent", "shrank by 30 percent", False),
    ("shrank by \\textbf{30} per cent", "shrank by 30 per cent overall", False),
    ("the \\textbf{alpha} beta gamma", "the alpha beta", False),
])
def test_the_faithfulness_gate_compares_content_not_meaning(before, after, ok):
    """Mechanics, not judgment: it never decides what the source MEANS — the
    model did that — it only declines a repair that lost or gained content.

    Markup words are stripped from BOTH sides first. Comparing raw tokens
    would refuse every legitimate repair, since dropping ``\\textbf{bold}`` in
    favour of ``bold`` "loses" the token ``textbf`` — which is the entire
    point of the repair.
    """
    assert (phase3._repair_is_faithful(before, after) == "") is ok


def test_a_provider_that_raises_is_a_refusal_not_a_crash(monkeypatch):
    """The evidence pages are loaded inside the provider, so an unreadable or
    absent PDF raises there. Before this was contained, the repair took down a
    run that was about to record an honest pause — the one thing Q13 forbids.
    """
    graph = _graph()

    def _explodes(packet):
        raise RuntimeError("no objects found")

    assert _repair(graph, _canonical(_DEFECT), _explodes, _critic()) == [
        "BLK-0001"
    ]
    assert "source_override" not in graph["blocks"][0]
    assert any(
        "could not run" in issue["message"] for issue in graph["issues"]
    )


def test_a_critic_that_raises_is_also_a_refusal():
    graph = _graph()

    def _explodes(packet, proposal):
        raise RuntimeError("provider down")

    assert _repair(graph, _canonical(_DEFECT), _author(), _explodes) == [
        "BLK-0001"
    ]
    assert "source_override" not in graph["blocks"][0]


def test_author_receives_repair_contract_not_selector_prohibition_and_can_correct_feedback():
    requests = []
    reviews = []

    def author(packet):
        requests.append(copy.deepcopy(packet))
        assert "Do not write replacement text" not in packet["instruction"]
        assert "canonical_text" in packet["instruction"]
        assert "[Katex]" in packet["rich_text_contract"]
        if len(requests) == 1:
            return _author(canonical_text=_DEFECT)(packet)
        assert packet["previous_proposal"]["canonical_text"] == _DEFECT
        assert packet["repair_feedback"][0]["gate"] == "faithfulness"
        assert "still refused" in packet["repair_feedback"][0]["detail"]
        return _author()(packet)

    def critic(packet, proposal):
        reviews.append(proposal)
        return _critic()(packet, proposal)

    graph = _graph()
    assert _repair(graph, _canonical(_DEFECT), author, critic) == []
    assert len(requests) == 2
    assert len(reviews) == 1
    assert graph["blocks"][0]["source_override"]["resolved_text"] == _CLEAN


def _two_defective_blocks():
    canonical = _canonical(_DEFECT)
    second = "Coal output fell by \\textbf{12} per cent."
    canonical["blocks"].append({
        "block_id": "BLK-0002", "order": 2, "kind": "paragraph",
        "display_text": second, "raw_text": second,
        "raw_sha256": phase3._sha256_text(second), "source_start": 40,
    })
    graph = _graph()
    graph["blocks"].append({"block_id": "BLK-0002", "kind": "paragraph"})
    return graph, canonical


def test_accepted_sibling_decision_is_durable_and_replayed_after_later_refusal(tmp_path):
    graph, canonical = _two_defective_blocks()
    original = copy.deepcopy(canonical)
    first_calls = []

    def first_author(packet):
        block_id = packet["canonical_block"]["block_id"]
        first_calls.append(block_id)
        return _author(
            canonical_text=_CLEAN if block_id == "BLK-0001" else "Coal output collapsed.",
        )(packet)

    assert _repair(graph, canonical, first_author, _critic(), repair_cache_dir=tmp_path) == ["BLK-0002"]
    assert all("source_override" not in block for block in graph["blocks"])
    assert len(list((tmp_path / "rich-text-repairs").glob("*.json"))) == 1
    assert graph["rich_text_repair_refusals"] == [{
        "block_id": "BLK-0002", "gate": "faithfulness", "attempt": 3,
        "rich_text_issue_codes": ["raw_latex"],
    }]
    resumed = _graph()
    resumed["blocks"].append({"block_id": "BLK-0002", "kind": "paragraph"})
    def no_paid_calls(*args):
        pytest.fail("a restart must not repay accepted or exhausted sibling work")

    assert _repair(resumed, canonical, no_paid_calls, no_paid_calls, repair_cache_dir=tmp_path) == ["BLK-0002"]
    assert resumed["rich_text_repair_refusals"] == graph["rich_text_repair_refusals"]
    assert resumed[phase3._RICH_TEXT_REPAIR_MEMO] == graph[phase3._RICH_TEXT_REPAIR_MEMO]
    assert all("source_override" not in block for block in resumed["blocks"])
    assert first_calls == ["BLK-0001", "BLK-0002", "BLK-0002", "BLK-0002"]
    assert canonical == original


def test_paid_sibling_receipt_survives_cooperative_suspension_before_graph_save(tmp_path):
    from app.services import run_control

    graph, canonical = _two_defective_blocks()
    def author(packet):
        if packet["canonical_block"]["block_id"] == "BLK-0002":
            raise run_control.RunDeferred("deployment", reason="deployment")
        return _author()(packet)

    with pytest.raises(run_control.RunDeferred):
        _repair(graph, canonical, author, _critic(), repair_cache_dir=tmp_path)
    assert len(list((tmp_path / "rich-text-repairs").glob("*.json"))) == 1
    fresh_graph, _ = _two_defective_blocks()
    def resume(packet):
        assert packet["canonical_block"]["block_id"] == "BLK-0002"
        return _author(canonical_text="Coal output fell by 12 per cent.")(packet)
    assert _repair(fresh_graph, canonical, resume, _critic(), repair_cache_dir=tmp_path) == []


@pytest.mark.parametrize("change", ["tamper", "source", "page_evidence"])
def test_repair_receipt_cannot_replay_for_tampered_or_different_evidence(tmp_path, change):
    graph = _graph()
    canonical = _canonical(_DEFECT)
    assert _repair(graph, canonical, _author(), _critic(), repair_cache_dir=tmp_path) == []
    bundle = _bundle()
    if change == "tamper":
        path = next((tmp_path / "rich-text-repairs").glob("*.json"))
        receipt = json.loads(path.read_text())
        receipt["proposal"]["canonical_text"] = "Invented source."
        path.write_text(json.dumps(receipt))
    elif change == "source":
        canonical["blocks"][0]["raw_text"] += " "
    else:
        bundle["pdf_sha256"] = "different_pdf"
    calls = []
    def author(packet):
        calls.append(packet)
        return _author()(packet)
    resumed = _graph()
    assert phase3._repair_rich_text_blocks(
        resumed, canonical=canonical, page_bundle=bundle,
        source_path=Path("/nonexistent.pdf"), allow_automatic_reconciliation=True,
        repair_provider=author, repair_critic=_critic(), repair_cache_dir=tmp_path,
    ) == []
    # A corrupt final receipt can be recovered from the independently verified
    # attempt journal; changed evidence must obtain a new decision.
    assert len(calls) == (0 if change == "tamper" else 1)
    assert resumed["blocks"][0]["source_override"]["resolved_text"] == _CLEAN


@pytest.mark.parametrize("failure", ["deferred", "transport"])
def test_paid_author_draft_is_durable_before_critic_and_resumes_without_reauthor(tmp_path, failure):
    from app.services import run_control

    canonical = _canonical(_DEFECT)
    graph = _graph()

    def interrupted_critic(packet, proposal):
        paths = list((tmp_path / "rich-text-repair-attempts").glob("*.json"))
        assert len(paths) == 1
        journal = json.loads(paths[0].read_text())
        assert journal["attempts"][0]["proposal"] == proposal
        assert "verification" not in journal["attempts"][0]
        if failure == "deferred":
            raise run_control.RunDeferred("deployment", reason="deployment")
        raise RuntimeError("temporary critic outage")

    if failure == "deferred":
        with pytest.raises(run_control.RunDeferred):
            _repair(graph, canonical, _author(), interrupted_critic, repair_cache_dir=tmp_path)
    else:
        assert _repair(graph, canonical, _author(), interrupted_critic, repair_cache_dir=tmp_path) == ["BLK-0001"]

    def no_reauthor(packet):
        pytest.fail("the paid author draft must survive a critic interruption")

    resumed = _graph()
    assert _repair(resumed, canonical, no_reauthor, _critic(), repair_cache_dir=tmp_path) == []
    assert resumed["blocks"][0]["source_override"]["resolved_text"] == _CLEAN


@pytest.mark.parametrize("confidence", [0.90, 0.95])
def test_paid_draft_refused_by_old_floor_gets_only_critic_after_floor_change(
    tmp_path, monkeypatch, confidence,
):
    canonical = _canonical(_DEFECT)
    original = copy.deepcopy(canonical)
    authored = []
    reviewed = []

    def author(packet):
        authored.append(copy.deepcopy(packet))
        return _author(confidence=confidence)(packet)

    def no_paid_calls(*args):
        pytest.fail("saved paid work must be reused")

    monkeypatch.setattr(phase3, "_RICH_TEXT_REPAIR_MIN_CONFIDENCE", 0.96)
    refused = _graph()
    assert _repair(
        refused, canonical, author, no_paid_calls, repair_cache_dir=tmp_path,
    ) == ["BLK-0001"]
    assert refused["rich_text_repair_refusals"][0]["gate"] == "author_confidence"
    assert len(authored) == 1
    journal_path = next((tmp_path / "rich-text-repair-attempts").glob("*.json"))
    saved = json.loads(journal_path.read_text())
    assert len(saved["attempts"]) == 1
    assert "verification" not in saved["attempts"][0]

    monkeypatch.setattr(phase3, "_RICH_TEXT_REPAIR_MIN_CONFIDENCE", 0.90)

    def critic(packet, proposal):
        reviewed.append(copy.deepcopy(proposal))
        assert proposal == saved["attempts"][0]["proposal"]
        return _critic(confidence=0.90)(packet, proposal)

    resumed = _graph()
    assert _repair(
        resumed, canonical, no_paid_calls, critic, repair_cache_dir=tmp_path,
    ) == []
    assert len(reviewed) == 1
    assert len(authored) == 1
    after = json.loads(journal_path.read_text())
    assert len(after["attempts"]) == 1
    assert after["attempts"][0]["request_sha256"] == saved["attempts"][0]["request_sha256"]
    assert after["attempts"][0]["proposal"] == saved["attempts"][0]["proposal"]
    assert after["attempts"][0]["verification"]["confidence"] == 0.90
    override = resumed["blocks"][0]["source_override"]
    assert override["resolved_text"] == _CLEAN
    assert override["repair_confidence_minimum"] == 0.90
    assert canonical == original


def test_critic_refusal_and_feedback_survive_restart_without_reverification(tmp_path):
    from app.services import run_control

    requests = []
    reviews = []

    def first_author(packet):
        requests.append(copy.deepcopy(packet))
        if packet["repair_attempt"] == 2:
            raise run_control.RunDeferred("deployment", reason="deployment")
        return _author()(packet)

    def first_critic(packet, proposal):
        reviews.append(proposal)
        return _critic(verdict="refused", issues=["Check the original typography"])(packet, proposal)

    with pytest.raises(run_control.RunDeferred):
        _repair(_graph(), _canonical(_DEFECT), first_author, first_critic, repair_cache_dir=tmp_path)

    def resumed_author(packet):
        assert packet == requests[1]
        assert packet["repair_attempt"] == 2
        assert packet["repair_feedback"][0]["gate"] == "critic"
        return _author()(packet)

    def resumed_critic(packet, proposal):
        reviews.append(proposal)
        return _critic()(packet, proposal)

    assert _repair(_graph(), _canonical(_DEFECT), resumed_author, resumed_critic, repair_cache_dir=tmp_path) == []
    assert len(reviews) == 2


def test_author_transport_failure_does_not_create_an_attempt_or_block_later_recovery(tmp_path):
    def interrupted_author(packet):
        raise RuntimeError("temporary author outage")

    assert _repair(_graph(), _canonical(_DEFECT), interrupted_author, _critic(), repair_cache_dir=tmp_path) == ["BLK-0001"]
    assert not list((tmp_path / "rich-text-repair-attempts").glob("*.json"))

    def resumed_author(packet):
        assert packet["repair_attempt"] == 1
        return _author()(packet)

    assert _repair(_graph(), _canonical(_DEFECT), resumed_author, _critic(), repair_cache_dir=tmp_path) == []


@pytest.mark.parametrize("corruption", ["invalid_json", "context", "hash", "request"])
def test_corrupt_same_context_attempt_journal_fails_closed_without_new_paid_calls(tmp_path, corruption):
    from app.services import run_control

    def interrupted_critic(packet, proposal):
        raise run_control.RunDeferred("deployment", reason="deployment")

    with pytest.raises(run_control.RunDeferred):
        _repair(_graph(), _canonical(_DEFECT), _author(), interrupted_critic, repair_cache_dir=tmp_path)
    path = next((tmp_path / "rich-text-repair-attempts").glob("*.json"))
    if corruption == "invalid_json":
        path.write_text("{invalid")
    else:
        journal = json.loads(path.read_text())
        if corruption == "context":
            journal["context_sha256"] = "other-source"
        elif corruption == "hash":
            journal["attempts"][0]["proposal"]["canonical_text"] = "Invented source."
        else:
            journal["attempts"][0]["request_sha256"] = "other-request"
            journal["journal_sha256"] = phase3._sha256_json({
                "context_sha256": journal["context_sha256"], "attempts": journal["attempts"],
            })
        path.write_text(json.dumps(journal))

    def no_paid_calls(*args):
        pytest.fail("a corrupt spent-attempt record must not reset the paid budget")

    with pytest.raises(ValueError, match="preserved for recovery"):
        _repair(_graph(), _canonical(_DEFECT), no_paid_calls, no_paid_calls, repair_cache_dir=tmp_path)


@pytest.mark.parametrize("author,critic", [
    (_author(confidence="not a score"), _critic()),
    (_author(), lambda packet, proposal: "not an object"),
    (_author(), _critic(confidence="not a score")),
    (_author(suppressed=True), _critic()),
])
def test_malformed_repair_verdicts_are_refused_without_crashing(author, critic):
    graph = _graph()
    assert _repair(graph, _canonical(_DEFECT), author, critic) == ["BLK-0001"]
    assert "source_override" not in graph["blocks"][0]
