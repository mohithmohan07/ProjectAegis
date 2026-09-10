import copy

import pytest

from app.services import concept_question_review as review
from app.services import release_workbook_edits as edits
from app.services.reviewed_question_set import ReviewedQuestionSetError


def payload():
    return {
        "records": [{"topic": "Old", "concept_title": "First", "concept_details": "Original details"}],
        "question_task_inventory": {"items": [
            {"qid": "Q1", "raw_task": "Old first question", "options": ["yes", "no"]},
            {"qid": "Q2", "raw_task": "Retained question", "shared_context": "Original necessary context"},
        ]},
        "type_case_rows": [{"row_kind": "example", "type_id": "T1", "case_id": "C1", "example_qid": "Q1"}],
    }


def question(qid, row, text):
    return {
        "source_qid": qid, "concept_row": row, "question_text": text,
        "question_text_spans": [], "shared_context": "", "source_answer": "",
        "context_review": {
            "action": "inherit" if qid else "remove",
            "sources": [],
            "rationale": (
                "The existing supporting context remains applicable."
                if qid else
                "The added question has no separate supporting context."
            ),
        },
        "options": [], "removed_dependencies": [], "type_id": "T1",
        "placement_section": "types",
        "type_title": "", "type_definition": "", "case_id": "C1",
        "case_definition": "", "rationale": "The complete edited row supports this identity and placement.",
    }


def verdict(questions, *, rows=2, omitted=("Q1",)):
    return {
        "questions": questions,
        "original_dispositions": [
            {"source_qid": qid, "disposition": "omitted" if qid in omitted else "moved", "rationale": "Reviewed source decision"}
            for qid in ("Q1", "Q2")
        ],
        "row_dispositions": [{"concept_row": i, "rationale": "All question examples in this row accounted for"} for i in range(rows)],
    }


def test_deletion_and_addition_do_not_reuse_positional_source_identity(monkeypatch):
    original = payload()
    saved = copy.deepcopy(original)
    edited = [
        {"topic": "First", "concept_title": "New target", "concept_details": "Description: Example of a misconception. Types: Example: Human added question"},
        {"topic": "Second", "concept_title": "Moved target", "concept_details": "Types: Case 2. Example: Retained question"},
    ]
    calls = []
    def call(system, evidence, *, critic=False):
        calls.append((system, evidence, critic))
        if critic:
            return {"verdict": "dissent", "issues": ["Reviewer should inspect the new question."]}
        return verdict([question("", 0, "Human added question"), question("Q2", 1, "Retained question")])
    monkeypatch.setattr(review, "_call", call)
    result = review.review_canonical_questions(original, edited)
    assert [row["kind"] for row in result] == ["reviewer_added", "source"]
    assert [row["source_qid"] for row in result] == ["", "Q2"]
    assert result[1]["topic_hint"] == "Second"
    assert result[1]["concept_title"] == "Moved target"
    assert result[1]["preserve_source_dependencies"] is True
    assert result.receipt["critic"]["verdict"] == "dissent"
    assert calls[0][1]["edited_concepts"][0]["concept_details"] == edited[0]["concept_details"]
    assert calls[0][1]["original_questions"] == original["question_task_inventory"]["items"]
    assert len(calls) == 2
    assert original == saved


def test_unquoted_generated_question_is_rejected_before_any_revision(monkeypatch):
    original = payload()
    edited = [{"concept_details": "Types: Example: Supplied task"}]
    calls = []
    def call(system, evidence, *, critic=False):
        calls.append(critic)
        return verdict([question("Q2", 0, "Invented task")], rows=1)
    monkeypatch.setattr(review, "_call", call)
    with pytest.raises(ReviewedQuestionSetError, match="exact nonempty edited-text quote"):
        review.review_canonical_questions(original, edited)
    assert calls == [False, False]
    assert original == payload()


def test_all_omitted_is_an_explicit_empty_review_with_durable_receipt(monkeypatch):
    def call(system, evidence, *, critic=False):
        if critic:
            return {"verdict": "verified", "issues": []}
        return verdict([], rows=1, omitted=("Q1", "Q2"))
    monkeypatch.setattr(review, "_call", call)
    result = review.review_canonical_questions(payload(), [{"concept_details": "Types: No question examples retained."}])
    assert result == []
    assert result.receipt["author"]["original_dispositions"][1]["disposition"] == "omitted"


def test_rich_question_quotes_remain_byte_exact(monkeypatch):
    prompt = 'Read the table [Katex]\\begin{array}{|c|}\\hline 3 \\\\ \\hline\\end{array}[/Katex]<br>[Image: https://example.test/a.png alt="a [shape]"]<br>(a) Explain. (b) Calculate.'
    def call(system, evidence, *, critic=False):
        if critic:
            return {"verdict": "verified", "issues": []}
        return verdict([question("Q2", 0, prompt)], rows=1)
    monkeypatch.setattr(review, "_call", call)
    result = review.review_canonical_questions(payload(), [{"concept_details": "Types: Example: " + prompt}])
    assert result[0]["question_text"] == prompt
    assert result[0]["normalized_public_text"] == prompt


def test_original_and_row_accounting_are_closed_world():
    current = verdict([question("Q2", 0, "Text")], rows=1)
    current["original_dispositions"] = current["original_dispositions"][:1]
    current["row_dispositions"] = []
    defects = review._validate(current, [{"concept_details": "Text"}], payload()["question_task_inventory"]["items"])
    assert any("every original QID" in error for error in defects)
    assert any("every edited Concept row" in error for error in defects)


def test_exact_unchanged_workbook_and_pre_do_not_spend_on_post_reconciliation(monkeypatch):
    monkeypatch.setattr(review, "_call", lambda *args, **kwargs: pytest.fail("no review call for unchanged cells or Pre"))
    current = payload()
    assert edits._canonical_detail_question_rows(current, copy.deepcopy(current["records"])) is None
    assert edits._canonical_detail_question_rows(current, [{"concept_details": "changed Pre"}], lane="pre") is None


def test_unknown_route_identity_cannot_orphan_an_accepted_question():
    current = verdict([question("Q2", 0, "Text")], rows=1)
    current["questions"][0]["case_id"] = "UNKNOWN"
    defects = review._validate(
        current, [{"concept_details": "Text"}],
        payload()["question_task_inventory"]["items"], payload()["type_case_rows"],
    )
    assert any("unknown original case_id" in error for error in defects)
    # Empty IDs explicitly describe a new reviewer-authored Type/Case. Their
    # stable identities are minted by the accepted revision, not invented by
    # the model or copied from an unrelated source question.
    current["questions"][0]["case_id"] = ""
    assert review._validate(
        current, [{"concept_details": "Text"}],
        payload()["question_task_inventory"]["items"], payload()["type_case_rows"],
    ) == []


def test_author_review_emits_closed_v4_schema_with_required_question_fields(monkeypatch):
    """The provider receives the same strict schema used by local validation."""

    captured = {}

    def fake_openai(system, user, **kwargs):
        captured.update(kwargs)
        return {"questions": [], "original_dispositions": [], "row_dispositions": []}

    from app.services import generation

    monkeypatch.setattr(generation, "_openai_json", fake_openai)
    wires = {}
    for critic, expected_name in (
        (False, "concept_question_review_author_v4"),
        (True, "concept_question_review_critic_v4"),
    ):
        review._call("review", {"evidence": "fixture"}, critic=critic)
        wire = captured["response_schema"].json_schema()
        wires[critic] = wire
        assert wire["name"] == expected_name
        assert wire["strict"] is True

        def assert_closed_and_required(node):
            if not isinstance(node, dict):
                return
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["properties"]) <= set(node["required"])
            for value in node.values():
                if isinstance(value, dict):
                    assert_closed_and_required(value)
                elif isinstance(value, list):
                    for child in value:
                        assert_closed_and_required(child)

        assert_closed_and_required(wire["schema"])

    question_schema = wires[False]["schema"]["$defs"]["ReviewedQuestion"]
    assert {
        "question_text_spans", "context_review", "removed_dependencies",
    } <= set(question_schema["required"])
    assert "preserve_source_dependencies" not in question_schema["properties"]
