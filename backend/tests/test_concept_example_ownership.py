"""Unowned rendered Examples become a recorded, reviewer-visible verdict.

The closed-inventory scanner's finding used to be a log line claiming
"closed-world validation remains blocked" while nothing blocked and
nothing was recorded — the rows shipped and the finding evaporated
(observed live: 7 unexpected Examples, three identical warnings, a
clean release). These pin the Q13/R4 repair: the release stage
adjudicates each unowned Example with one chapter-wide recorded
decision and appends the verdicts to the staged release's seal-safe
``issues`` ledger. Nothing is dropped or rewritten; the reviewer acts
through the ordinary revision loop.
"""
from __future__ import annotations

from app.services import concept_example_ownership as ownership
from app.services import generation as g
from app.services.phase3 import kernel


def _finding(text: str, reason: str = "not_in_inventory") -> dict:
    return {"example": text[:40], "example_text": text, "reason": reason}


def _inventory(*prompts: str) -> dict:
    return {
        "items": [
            {
                "qid": f"QINV-{index:04d}",
                "source_kind": "exercise",
                "source_label": f"QINV-{index:04d}",
                "raw_task": prompt,
                "normalized_task": prompt,
            }
            for index, prompt in enumerate(prompts, start=1)
        ],
    }


# ---------------------------------------------------------------------------
# the mechanical checker


def test_the_checker_requires_every_example_ruled_exactly_once():
    check = ownership._ownership_checker(2, {"QINV-0001"})
    defects = check({"verdicts": [
        {"example_index": 0, "verdict": "unowned", "owner_qid": "",
         "reason": "matches nothing"},
    ]})
    assert any("missing example_index" in d for d in defects)

    defects = check({"verdicts": [
        {"example_index": 0, "verdict": "unowned", "owner_qid": "",
         "reason": "a"},
        {"example_index": 0, "verdict": "unowned", "owner_qid": "",
         "reason": "b"},
    ]})
    assert any("ruled twice" in d for d in defects)


def test_the_checker_binds_variant_claims_to_real_qids():
    check = ownership._ownership_checker(1, {"QINV-0001"})
    assert any(
        "must name an inventory qid" in d
        for d in check({"verdicts": [
            {"example_index": 0, "verdict": "source_variant",
             "owner_qid": "QINV-9999", "reason": "re-worded"},
        ]})
    )
    assert any(
        "unowned must not claim a qid" in d
        for d in check({"verdicts": [
            {"example_index": 0, "verdict": "unowned",
             "owner_qid": "QINV-0001", "reason": "nothing matches"},
        ]})
    )
    assert check({"verdicts": [
        {"example_index": 0, "verdict": "source_variant",
         "owner_qid": "QINV-0001", "reason": "trimmed poem quotation"},
    ]}) == []


def test_the_checker_rejects_vocabulary_and_empty_reasons():
    check = ownership._ownership_checker(1, {"QINV-0001"})
    defects = check({"verdicts": [
        {"example_index": 0, "verdict": "maybe", "owner_qid": "",
         "reason": ""},
    ]})
    assert any("is not one of" in d for d in defects)
    assert any("reason is empty" in d for d in defects)


# ---------------------------------------------------------------------------
# the recorded decision


def test_an_empty_scan_spends_nothing():
    def exploding_provider(_payload):  # pragma: no cover - must not run
        raise AssertionError("no findings must mean no model call")

    verdicts, flags = ownership.decide_example_ownership(
        [],
        inventory=_inventory("task"),
        meta={},
        envelope_sha256="",
        provider=exploding_provider,
    )
    assert verdicts == [] and flags == []


def test_verdicts_come_back_in_finding_order_with_their_texts():
    calls = []

    def provider(payload):
        calls.append(payload)
        return {
            "verdicts": [
                {"example_index": 1, "verdict": "unowned", "owner_qid": "",
                 "reason": "matches no task"},
                {"example_index": 0, "verdict": "source_variant",
                 "owner_qid": "QINV-0001",
                 "reason": "the poem quotation was trimmed"},
            ],
            "confidence": 0.8,
            "rationale": "compared each",
        }

    verdicts, flags = ownership.decide_example_ownership(
        [_finding("Recite the opening couplet."), _finding("Invented ask.")],
        inventory=_inventory("Read and recite the opening couplet aloud."),
        meta={"chapter_title": "The School Bell Rings Again..."},
        envelope_sha256="abc",
        provider=provider,
        critic=lambda _p: {"verdict": "concur", "confidence": 0.9,
                           "issues": []},
        store=kernel.DecisionStore(),
    )
    assert [v["verdict"] for v in verdicts] == ["source_variant", "unowned"]
    assert verdicts[0]["owner_qid"] == "QINV-0001"
    assert verdicts[0]["example_text"] == "Recite the opening couplet."
    assert verdicts[1]["owner_qid"] == ""
    # Confidence banding may add its own advisory flag; what matters here
    # is that no dissent text rides a concurring critic.
    assert not any("dissent:" in f for f in flags)
    # The decision saw the full texts and the full inventory, not snippets.
    payload = calls[0]
    assert payload["unowned_examples"][0]["example_text"] == (
        "Recite the opening couplet."
    )
    assert payload["inventory_tasks"][0]["qid"] == "QINV-0001"


def test_a_dissenting_critic_flags_and_never_gates():
    verdicts, flags = ownership.decide_example_ownership(
        [_finding("Something unowned.")],
        inventory=_inventory("A real task."),
        meta={},
        envelope_sha256="abc",
        provider=lambda _p: {"verdicts": [
            {"example_index": 0, "verdict": "unowned", "owner_qid": "",
             "reason": "matches nothing"},
        ], "confidence": 0.7, "rationale": "r"},
        critic=lambda _p: {"verdict": "dissent", "confidence": 0.6,
                           "issues": ["example 0 looks like QINV-0001"]},
        store=kernel.DecisionStore(),
    )
    assert [v["verdict"] for v in verdicts] == ["unowned"]
    assert any("example 0 looks like QINV-0001" in f for f in flags)


# ---------------------------------------------------------------------------
# the release record


def test_the_issue_carries_every_verdict_and_stays_a_warning():
    issue = ownership.build_issue(
        [
            {"example_index": 0, "example_text": "t", "detected_reason": "",
             "verdict": "source_variant", "owner_qid": "QINV-0001",
             "reason": "trimmed"},
            {"example_index": 1, "example_text": "u", "detected_reason": "",
             "verdict": "unowned", "owner_qid": "", "reason": "nothing"},
        ],
        ["critic dissent: …"],
        adjudicated=True,
    )
    assert issue["code"] == ownership.UNOWNED_EXAMPLES_ISSUE_CODE
    assert issue["severity"] == "warning"
    assert issue["qids"] == ["QINV-0001"]
    assert issue["details"]["adjudicated"] is True
    assert len(issue["details"]["verdicts"]) == 2
    assert "1 ruled genuinely unowned" in issue["message"]


def _unowned_records() -> list[dict]:
    return [{
        "topic": "T", "parent_concept": "P", "concept_title": "C",
        "concept_details": (
            "Description: d. // Types: Type 01: T Case 01: c "
            "Example 01: An Example nobody in the inventory owns."
        ),
        "keywords": "",
    }]


def test_the_issue_records_the_finding_even_without_a_live_judge(
    monkeypatch,
):
    # Dry mode: the deterministic finding must still ride the release —
    # an unavailable judge never makes a finding evaporate (R4).
    from app.services import canonical_source_phase3 as phase3_core

    monkeypatch.setattr(phase3_core, "semantic_api_enabled", lambda: False)

    issue = ownership.adjudication_issue(
        _unowned_records(),
        _inventory("A completely different source task."),
        meta={},
        job_id=9,
    )

    assert issue is not None
    assert issue["code"] == ownership.UNOWNED_EXAMPLES_ISSUE_CODE
    assert issue["details"]["adjudicated"] is False
    # The live scanner reads the canonically normalized body, so the
    # carried wording is the normalized (lower-cased) form — complete,
    # which is what the judge and the reviewer need.
    assert issue["details"]["verdicts"][0]["example_text"].startswith(
        "an example nobody"
    )


def test_a_failing_live_judge_still_leaves_the_recorded_finding(
    monkeypatch,
):
    # The live judge erroring (quota, outage, contract exhaustion) is
    # never a reason to lose the finding: the issue ships unadjudicated
    # with the failure named.
    from app.services import canonical_source_phase3 as phase3_core

    monkeypatch.setattr(phase3_core, "semantic_api_enabled", lambda: True)
    monkeypatch.setattr(
        ownership, "decide_example_ownership",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("quota")),
    )

    issue = ownership.adjudication_issue(
        _unowned_records(),
        _inventory("A completely different source task."),
        meta={},
        job_id=12,
    )

    assert issue is not None
    assert issue["details"]["adjudicated"] is False
    assert "live adjudication failed" in (
        issue["details"]["verdicts"][0]["reason"]
    )
    assert "quota" in issue["details"]["verdicts"][0]["reason"]


def test_a_clean_scan_stages_nothing():
    prompt = "The one real source task."
    records = [{
        "topic": "T", "parent_concept": "P", "concept_title": "C",
        "concept_details": (
            "Description: d. // Types: Type 01: T Case 01: c "
            f"Example 01: {prompt}"
        ),
        "keywords": "",
    }]

    assert ownership.adjudication_issue(
        records, _inventory(prompt), meta={}, job_id=10,
    ) is None


def test_the_helper_never_raises(monkeypatch):
    monkeypatch.setattr(
        g, "_unexpected_rendered_type_examples",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert ownership.adjudication_issue(
        [], {}, meta={}, job_id=11,
    ) is None


def test_hub_items_are_never_offered_as_candidate_owners():
    # The scanner excludes Activity/Info-Hub items from the owner keys,
    # so the judge must not be offered one either — otherwise a hub item
    # could bless as source_variant the very Example the contract says
    # it can never own.
    inventory = _inventory("A real exercise task.")
    inventory["items"].append({
        "qid": "QINV-9998",
        "source_kind": "activity",
        "source_label": "QINV-9998",
        "raw_task": "A hub activity whose wording matches the Example.",
        "normalized_task": "A hub activity whose wording matches the Example.",
    })
    seen: list[dict] = []

    def provider(payload):
        seen.append(payload)
        return {
            "verdicts": [
                {"example_index": 0, "verdict": "unowned", "owner_qid": "",
                 "reason": "matches no offered task"},
            ],
            "confidence": 0.9,
            "rationale": "r",
        }

    ownership.decide_example_ownership(
        [_finding("A hub activity whose wording matches the Example.")],
        inventory=inventory,
        meta={},
        envelope_sha256="abc",
        provider=provider,
        critic=lambda _p: {"verdict": "concur", "confidence": 0.9,
                           "issues": []},
        store=kernel.DecisionStore(),
    )

    offered = {task["qid"] for task in seen[0]["inventory_tasks"]}
    assert "QINV-9998" not in offered
    assert "QINV-0001" in offered
    # ...and the checker refuses a hub qid as an owner outright.
    check = ownership._ownership_checker(1, offered)
    assert any(
        "must name an inventory qid" in d
        for d in check({"verdicts": [
            {"example_index": 0, "verdict": "source_variant",
             "owner_qid": "QINV-9998", "reason": "hub wording"},
        ]})
    )


# ---------------------------------------------------------------------------
# the scanner's carried text


def test_scan_findings_carry_the_full_example_text():
    records = [{
        "topic": "T", "parent_concept": "P", "concept_title": "C",
        "concept_details": (
            "Description: d. // Types: Type 01: T Case 01: c "
            "Example 01: A long invented Example whose full wording must "
            "reach the judge, not a truncated diagnostic snippet of it."
        ),
        "keywords": "",
    }]
    findings = g._unexpected_rendered_type_examples(
        records, _inventory("A different real task."),
    )
    assert len(findings) == 1
    assert findings[0]["example_text"].endswith("snippet of it.")


def test_every_declared_purpose_in_the_module_is_a_known_purpose():
    import re

    from aegis_pipeline import openai_policy

    source = open(ownership.__file__, encoding="utf-8").read()
    for purpose in re.findall(r'purpose="([a-z_]+)"', source):
        assert openai_policy.reasoning_effort_for(
            purpose,  # type: ignore[arg-type]
        )
