"""A final resolution call must be able to name an action.

Job 23 stopped at 81% with ``UnattendedDecisionUnavailable``. The saved
review recorded ``status=escalated`` and ``"The agent selected an action that
was not offered."`` after inspecting all eight candidates.

The final schema pins ``disposition`` to ``apply`` but still offered ``""`` in
the ``choice`` enum, so the model could legally return an apply that names no
action -- which the validator then rejects, ending an unattended run.  The
decision itself was resolvable: its recommended option was ``keep``.
"""
from __future__ import annotations

from app.services import autonomous_resolution as ar


def _pending() -> dict:
    return {
        "kind": "phase32_concept_blueprint_semantic_conflict",
        "phase": "3.2",
        "options": [
            {"choice": "accept_recommended", "recommended": True,
             "target_id": "3.2:keep:7485"},
            {"choice": "select_candidate", "recommended": False},
            {"choice": "replace_source", "recommended": False},
            {"choice": "custom_instruction", "recommended": False},
        ],
        "candidates": [
            {"target_id": "3.2:keep:7485", "title": "Keep the current source claim"},
            {"target_id": "3.2:refine:c7d1", "title": "Refine this concept"},
        ],
    }


def test_a_final_call_cannot_answer_with_no_choice():
    schema = ar._response_schema(_pending(), set(), final=True)
    choice = schema["schema"]["properties"]["choice"]
    target = schema["schema"]["properties"]["target_id"]

    assert schema["schema"]["properties"]["disposition"]["enum"] == ["apply"]
    assert "" not in choice["enum"]
    assert choice["enum"] == ["accept_recommended", "select_candidate"]
    assert target["enum"] == ["3.2:keep:7485", "3.2:refine:c7d1"]
    assert "" not in target["enum"]


def test_an_earlier_pass_may_still_answer_with_no_choice():
    # request_evidence and ask_human legitimately name no action.
    schema = ar._response_schema(_pending(), set(), final=False)
    choice = schema["schema"]["properties"]["choice"]

    assert "request_evidence" in schema["schema"]["properties"]["disposition"]["enum"]
    assert choice["enum"][0] == ""
    assert schema["schema"]["properties"]["target_id"]["enum"][0] == ""


def test_a_final_targetless_action_keeps_the_empty_target_shape():
    pending = _pending()
    pending["options"].append({"choice": "keep_distinct_types"})

    schema = ar._response_schema(pending, set(), final=True)

    # The flat compatibility contract must still represent a genuinely
    # targetless action. The local validator enforces that select_candidate
    # itself cannot use this empty value.
    assert schema["schema"]["properties"]["target_id"]["enum"][0] == ""


def test_candidate_selection_is_not_emitted_without_an_untried_target():
    pending = _pending()
    pending["excluded_prior_pathways"] = [
        {
            "choice": "select_candidate",
            "target_id": candidate["target_id"],
            "target_concept_id": "",
        }
        for candidate in pending["candidates"]
    ]

    schema = ar._response_schema(pending, set(), final=True)
    properties = schema["schema"]["properties"]

    assert properties["choice"]["enum"] == [""]
    assert properties["target_id"]["enum"] == [""]


def test_a_packet_with_no_automatable_choice_keeps_an_empty_option():
    # Never emit an empty enum, which is not valid JSON schema.
    pending = _pending()
    pending["options"] = [{"choice": "replace_source"}]

    schema = ar._response_schema(pending, set(), final=True)

    assert schema["schema"]["properties"]["choice"]["enum"] == [""]


def test_an_empty_applied_choice_is_reported_as_such():
    result = ar._validate_response(
        {
            "disposition": "apply",
            "choice": "",
            "target_id": "",
            "confidence": 0.9,
            "reason": "",
        },
        pending=_pending(),
        evidence_refs=set(),
    )

    assert result.status == "escalated"
    assert "applied no choice" in result.reason


def test_an_unoffered_choice_still_reports_as_unoffered():
    result = ar._validate_response(
        {
            "disposition": "apply",
            "choice": "consolidate_types",
            "target_id": "",
            "confidence": 0.9,
            "reason": "",
        },
        pending=_pending(),
        evidence_refs=set(),
    )

    assert result.status == "escalated"
    assert "was not offered" in result.reason
