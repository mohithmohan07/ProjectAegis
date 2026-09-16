"""An interrupted verifier resumes the exact paid draft and refusal history."""
from __future__ import annotations

import copy

import pytest

from app.services import run_control
from tests.test_phase3_rich_text_repair import (
    _canonical, _graph, _repair, _author, _critic, _CLEAN, _DEFECT,
)


def test_second_paid_author_draft_survives_a_verifier_pause_after_first_refusal(tmp_path):
    author_attempts = []
    first_feedback = []
    canonical = _canonical(_DEFECT)
    saved_source = copy.deepcopy(canonical)

    def author(packet):
        author_attempts.append(packet["repair_attempt"])
        if packet["repair_attempt"] == 1:
            return _author(canonical_text=_DEFECT)(packet)
        first_feedback.extend(packet["repair_feedback"])
        return _author()(packet)

    def pending_verifier(packet, proposal):
        assert proposal["canonical_text"] == _CLEAN
        raise run_control.RunDeferred("The verifier batch is still running.")

    with pytest.raises(run_control.RunDeferred):
        _repair(_graph(), canonical, author, pending_verifier, repair_cache_dir=tmp_path)
    assert author_attempts == [1, 2]
    assert first_feedback[0]["gate"] == "faithfulness"

    def refuse_author_replay(_packet):
        raise AssertionError("The second author result is already paid and must be durable")

    reviews = []
    def verifier(packet, proposal):
        reviews.append(copy.deepcopy(proposal))
        return _critic()(packet, proposal)

    resumed = _graph()
    assert _repair(resumed, canonical, refuse_author_replay, verifier, repair_cache_dir=tmp_path) == []
    assert len(reviews) == 1
    assert reviews[0]["canonical_text"] == _CLEAN
    assert resumed["blocks"][0]["source_override"]["resolved_text"] == _CLEAN
    assert canonical == saved_source


def test_verified_attempt_journal_survives_final_receipt_write_failure(tmp_path, monkeypatch):
    from app.services import canonical_source_phase3 as phase3

    atomic_write = phase3._atomic_write
    def fail_final_receipt(path, content):
        if path.parent.name == "rich-text-repairs":
            raise OSError("final receipt write interrupted")
        return atomic_write(path, content)

    monkeypatch.setattr(phase3, "_atomic_write", fail_final_receipt)
    with pytest.raises(OSError, match="receipt write interrupted"):
        _repair(_graph(), _canonical(_DEFECT), _author(), _critic(), repair_cache_dir=tmp_path)
    monkeypatch.setattr(phase3, "_atomic_write", atomic_write)

    def refuse_repay(*_args):
        raise AssertionError("Both paid author and verifier results are already journaled")

    resumed = _graph()
    assert _repair(resumed, _canonical(_DEFECT), refuse_repay, refuse_repay,
                   repair_cache_dir=tmp_path) == []
    assert resumed["blocks"][0]["source_override"]["resolved_text"] == _CLEAN
