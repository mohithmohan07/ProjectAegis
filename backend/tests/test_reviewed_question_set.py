from app.services.reviewed_question_set import reconcile_post_review


def _payload():
    return {
        "question_task_inventory": {"items": [
            {"qid": "Q1", "raw_task": "Original one", "source_evidence": {"block": "B1"}},
            {"qid": "Q2", "raw_task": "Original two", "source_evidence": {"block": "B2"}},
        ]},
        "type_case_rows": [
            {"row_kind": "type", "type_id": "T1", "qids": ["Q1", "Q2"]},
            {"row_kind": "case", "type_id": "T1", "case_id": "C1", "qids": ["Q1"]},
            {"row_kind": "example", "type_id": "T1", "case_id": "C1", "example_qid": "Q1", "example_prompt": "Original one"},
            {"row_kind": "case", "type_id": "T1", "case_id": "C2", "qids": ["Q2"]},
            {"row_kind": "example", "type_id": "T1", "case_id": "C2", "example_qid": "Q2", "example_prompt": "Original two"},
        ],
    }


def test_reviewed_types_cases_are_exact_and_retain_source_evidence():
    result = reconcile_post_review(
        _payload(),
        [
            {"row_kind": "type", "type_id": "T1", "qids": []},
            {"row_kind": "case", "type_id": "T1", "case_id": "C2", "qids": []},
            {"row_kind": "example", "type_id": "T1", "case_id": "C2", "example_qid": "Q1", "example_prompt": "Corrected one"},
            {"row_kind": "reviewer_added", "type_id": "T1", "case_id": "C2", "example_prompt": "Added three"},
        ],
        now="2026-09-09T00:00:00+00:00",
    )
    inventory = result["question_task_inventory"]
    assert [item["qid"] for item in inventory["items"]] == [
        "Q1", result["review_question_audit"]["added"][0]
    ]
    assert inventory["items"][0]["raw_task"] == "Corrected one"
    assert inventory["items"][0]["source_evidence"] == {"block": "B1"}
    assert result["review_question_audit"]["omitted"] == ["Q2"]
    assert result["review_question_audit"]["moved"][0]["identity"] == "Q1"
    assert result["type_case_rows"][1]["qids"] == [
        "Q1", result["review_question_audit"]["added"][0]
    ]


def test_missing_sheet_preserves_payload_and_empty_sheet_is_explicit_empty():
    payload = _payload()
    assert reconcile_post_review(payload, None) is None
    result = reconcile_post_review(payload, [], now="2026-09-09T00:00:00+00:00")
    assert result["question_task_inventory"]["items"] == []
    assert result["review_question_audit"]["omitted"] == ["Q1", "Q2"]


# Canonical edited-cell identity and extraction are exercised against explicit
# author/critic verdicts in test_concept_question_review.py, including prose
# containing Example labels, deletions, additions and changed row order.


def test_reviewed_case_moved_across_concepts_has_distinct_host_evidence():
    from app.services import build_concepts_release as release
    from app.services.concept_question_review import ReviewRows
    from app.services.reviewed_question_set import synchronize_reviewed_catalog

    payload = _payload()
    payload["mined_types"] = {"types": [{
        "type_id": "T1", "type_title": "Method", "type_definition": "Shared method",
        "case_prompts": [{"case_id": "C1", "case_definition": "Apply", "examples": [
            {"source_question_id": "Q1", "prompt": "Original one"},
            {"source_question_id": "Q2", "prompt": "Original two"},
        ]}],
    }]}
    payload["records"] = [{
        "topic": "Topic", "concept_title": f"Concept {index}",
        "concept_details": f"Description: Supported material. // Types: Type 01: Method Case 01: Apply Example 01: {prompt}",
    } for index, prompt in enumerate(("Original one", "Original two"))]
    bodies = [row["concept_details"] for row in payload["records"]]
    rows = ReviewRows([{
        "concept_row_index": index, "question_text": prompt,
        "type_id": "T1", "case_id": "C1", "placement_section": "types",
    } for index, prompt in enumerate(("Original one", "Original two"))], receipt={"policy": "accepted-model-review"})
    synchronize_reviewed_catalog(payload, rows)
    targets = [item["_aegis_reviewed_target"] for item in payload["question_task_inventory"]["items"]]
    assert [target["concept_row_index"] for target in targets] == [0, 1]
    assert len({(target["type_id"], target["case_id"]) for target in targets}) == 2
    assert all(target["source_type_id"] == "T1" and target["source_case_id"] == "C1" for target in targets)
    assert [row["concept_details"] for row in payload["records"]] == bodies
    assert release._recomputed_identity_issues(payload, payload["records"]) == []
