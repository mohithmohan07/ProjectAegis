"""MES PR 3 — question/answer/rubric authority (spec §6 Stages 4–6, §14, §15).

Author + read-only critic with proposal-hash binding, bounded repair,
mechanical validation, integrity-checked cache, and zero-loss flagging.
"""
from __future__ import annotations

import pytest

from app.services import assessment_materialization as am


@pytest.fixture(autouse=True)
def _fresh_cache():
    am.reset_cache()
    yield
    am.reset_cache()


def _cell(**kw) -> dict:
    cell = {
        "cell_id": "CELL-abc123", "sheet_kind": "objective",
        "question_category": "MCQ", "cognitive_skill": "Remember",
        "difficulty": "Less", "marks": 1.0, "count": 1,
        "appears_in": ["Worksheet"], "concept_id": 7,
        "source_policy": "rewrite",
    }
    cell.update(kw)
    return cell


def _atom(**kw) -> dict:
    atom = {
        "source_qid": "QINV-0003",
        "source_document_hash": "sha256:doc",
        "source_kind": "exercise",
        "raw_text": "Look at the figure and name the solid.",
        "assets": [{"url": "https://x/a.png", "alt": "a cone", "order": 1,
                    "sha256": "s1", "source_page": 3, "bbox": None}],
    }
    atom.update(kw)
    return atom


def _good_proposal() -> dict:
    return {
        "question": "The illustration provided shows a solid. Name it.",
        "answer_restriction": "Specific",
        "restriction_reason": "one exact solid name is required",
        "display_answer": "",
        "answers": [
            {"answer_type": "Phrases", "answer_content": "Cone",
             "correct_answer": "1", "answer_weightage": "1"},
            {"answer_type": "Phrases", "answer_content": "Cube",
             "correct_answer": "0", "answer_weightage": "0"},
        ],
        "sub_questions": [],
        "answer_explanation": "The curved face tapering to an apex is a cone.",
        "requires_visual": True,
    }


def _accepting_critic(system, user):
    import json

    payload = json.loads(user)
    return {"verdict": "accept",
            "proposal_sha256": payload["proposal_sha256"], "feedback": []}


META = {"subject": "Mathematics", "grade": "06"}


# --------------------------------------------------------------------------- #
# Accepted path
# --------------------------------------------------------------------------- #

def test_accepted_candidate_is_immutable_and_hash_bound():
    candidate = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=lambda s, u: _good_proposal(),
        critic_call=_accepting_critic,
        use_cache=False,
    )
    assert candidate["assessment_eligibility"] == "accepted"
    assert candidate["flags"] == []
    assert candidate["blueprint_cell_id"] == "CELL-abc123"
    # Blueprint fields come from the cell, not the model.
    assert candidate["cognitive_skill"] == "Remember"
    assert candidate["marks"] == 1.0
    # question_text is a byte-exact copy of the rich question (spec §3.8).
    assert candidate["question_text"] == candidate["question"]
    # Assets ride along from the atom, ordered.
    assert candidate["assets"][0]["order"] == 1
    authority = candidate["authority"]
    assert authority["proposal_sha256"] == authority["critic_reviewed_sha256"]
    assert authority["attempts"][-1]["outcome"] == "accepted"


def test_critic_is_read_only_wording_never_ships():
    def meddling_critic(system, user):
        import json

        payload = json.loads(user)
        return {
            "verdict": "accept",
            "proposal_sha256": payload["proposal_sha256"],
            "feedback": [],
            "question": "A rewritten question the critic tried to inject.",
        }

    candidate = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=lambda s, u: _good_proposal(),
        critic_call=meddling_critic,
        use_cache=False,
    )
    assert candidate["question"] == _good_proposal()["question"]


# --------------------------------------------------------------------------- #
# Mechanical validation and bounded repair
# --------------------------------------------------------------------------- #

def test_mechanical_defects_feed_the_repair_round():
    bad = _good_proposal()
    bad["answers"][1]["correct_answer"] = "1"  # two correct options
    proposals = iter([bad, _good_proposal()])
    prompts_seen = []

    def author(system, user):
        prompts_seen.append(user)
        return next(proposals)

    candidate = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=author, critic_call=_accepting_critic, use_cache=False,
    )
    assert candidate["assessment_eligibility"] == "accepted"
    assert "exactly one correct option" in prompts_seen[1]


def test_objective_restriction_and_weights_are_enforced():
    open_objective = _good_proposal()
    open_objective["answer_restriction"] = "Open"
    defects = am.proposal_defects(open_objective, _cell())
    assert any("always Specific" in d for d in defects)

    wrong_weight = _good_proposal()
    wrong_weight["answers"][0]["answer_weightage"] = "3"
    defects = am.proposal_defects(wrong_weight, _cell())
    assert any("!= marks" in d for d in defects)

    unknown = _good_proposal()
    unknown["answer_restriction"] = ""
    defects = am.proposal_defects(unknown, _cell())
    assert any("never defaulted" in d for d in defects)


def test_descriptive_mark_arithmetic_is_exact():
    cell = _cell(sheet_kind="descriptive", question_category="Long Answer",
                 marks=5.0)
    proposal = {
        "question": "Explain the water cycle with a labelled diagram.",
        "answer_restriction": "Open",
        "restriction_reason": "several valid explanations earn credit",
        "display_answer": "Evaporation, condensation, precipitation ...",
        "answers": [
            {"answer_type": "Phrases", "answer_weightage": "2",
             "answer_content": "evaporation and condensation explained"},
            {"answer_type": "Phrases", "answer_weightage": "2",
             "answer_content": "precipitation and collection explained"},
        ],
        "sub_questions": [
            {"text": "Name the process of water vapour formation.",
             "marks": "2", "keywords": []},
            {"text": "Explain cloud formation.", "marks": "2",
             "keywords": []},
        ],
        "answer_explanation": "",
        "requires_visual": False,
    }
    defects = am.proposal_defects(proposal, cell)
    assert any("answer weightage sum 4 != marks 5" in d for d in defects)
    assert any("subquestion marks sum 4 != parent marks 5" in d
               for d in defects)


def test_capacities_are_enforced():
    proposal = _good_proposal()
    proposal["answers"] = proposal["answers"] * 4  # 8 options
    defects = am.proposal_defects(proposal, _cell())
    assert any("1..6 options" in d for d in defects)


# --------------------------------------------------------------------------- #
# Critic binding and exhaustion
# --------------------------------------------------------------------------- #

def test_unbound_critic_review_certifies_nothing():
    def unbound_critic(system, user):
        return {"verdict": "accept", "proposal_sha256": "deadbeef",
                "feedback": []}

    candidate = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=lambda s, u: _good_proposal(),
        critic_call=unbound_critic, max_attempts=2, use_cache=False,
    )
    assert candidate["assessment_eligibility"] == "flagged"
    assert "unresolved_author_critic" in candidate["flags"]
    outcomes = [a["outcome"] for a in candidate["authority"]["attempts"]]
    assert outcomes == ["critic_unbound", "critic_unbound"]


def test_exhausted_repair_ships_flagged_best_candidate_not_nothing():
    def rejecting_critic(system, user):
        import json

        payload = json.loads(user)
        return {"verdict": "reject",
                "proposal_sha256": payload["proposal_sha256"],
                "feedback": ["the distractors are not same-family"]}

    author_prompts = []

    def author(system, user):
        author_prompts.append(user)
        return _good_proposal()

    candidate = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=author, critic_call=rejecting_critic,
        max_attempts=3, use_cache=False,
    )
    assert candidate["assessment_eligibility"] == "flagged"
    # The best evidence-bound proposal ships; nothing disappears.
    assert candidate["question"] == _good_proposal()["question"]
    assert candidate["source_evidence"] == _atom()["raw_text"]
    # The critic's evidence-bound feedback reached the next author round.
    assert "not same-family" in author_prompts[1]
    assert len(author_prompts) == 3


def test_provider_failure_is_never_acceptance():
    def broken_author(system, user):
        raise RuntimeError("provider down")

    candidate = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=broken_author, critic_call=_accepting_critic,
        use_cache=False,
    )
    assert candidate["assessment_eligibility"] == "flagged"
    assert candidate["question"] == ""
    assert candidate["source_evidence"] == _atom()["raw_text"]
    assert any("author_error" in flag for flag in candidate["flags"])


# --------------------------------------------------------------------------- #
# Cache (spec §15)
# --------------------------------------------------------------------------- #

def test_cache_reuses_only_integrity_checked_accepted_candidates():
    calls = {"author": 0}

    def author(system, user):
        calls["author"] += 1
        return _good_proposal()

    first = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=author, critic_call=_accepting_critic)
    second = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=author, critic_call=_accepting_critic)
    assert calls["author"] == 1
    assert second == first

    # A different blueprint cell is a different identity: no reuse.
    am.materialize_candidate(
        _atom(), _cell(cell_id="CELL-other", difficulty="High"), meta=META,
        author_call=author, critic_call=_accepting_critic)
    assert calls["author"] == 2


def test_corrupt_cache_record_is_rejected():
    identity = am.cache_identity(_atom(), _cell(), META)
    candidate = am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=lambda s, u: _good_proposal(),
        critic_call=_accepting_critic)
    assert am.load_cached_candidate(identity) is not None
    path = am._cache_path(am._cache_key(identity))
    tampered = path.read_text(encoding="utf-8").replace(
        candidate["question"], "tampered wording")
    path.write_text(tampered, encoding="utf-8")
    am.reset_cache()
    assert am.load_cached_candidate(identity) is None


def test_flagged_candidates_are_never_cached():
    identity = am.cache_identity(_atom(), _cell(), META)
    am.materialize_candidate(
        _atom(), _cell(), meta=META,
        author_call=lambda s, u: (_ for _ in ()).throw(RuntimeError("down")),
        critic_call=_accepting_critic)
    assert am.load_cached_candidate(identity) is None


# --------------------------------------------------------------------------- #
# Zero-loss batch driver
# --------------------------------------------------------------------------- #

def test_every_obligation_yields_a_candidate():
    pairs = [
        (_atom(), _cell()),
        (_atom(source_qid="QINV-0004", raw_text="Define a chord."),
         _cell(cell_id="CELL-def456")),
    ]
    proposals = iter([_good_proposal()])

    def flaky_author(system, user):
        try:
            return next(proposals)
        except StopIteration:
            raise RuntimeError("provider down")

    result = am.materialize_candidates(
        pairs, meta=META,
        author_call=flaky_author, critic_call=_accepting_critic,
        use_cache=False,
    )
    assert len(result["candidates"]) == 2
    assert result["accepted"] == 1
    assert result["flagged"] == 1
    assert result["zero_loss"]["holds"]
