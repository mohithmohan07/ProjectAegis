"""Explicit corrected multipart grouping reaches the frozen Master source."""
import copy

import pytest

from app.services import assessment_materialization as materialization
from app.services import assessment_source_inventory as inventory
from app.services import concept_question_review as review
from app.services import generation
from app.services import generation_quality_policy as quality
from app.services import generation_repair_policy as repair
from app.services import release_workbook_edits as edits
from app.services.reviewed_question_set import ReviewedQuestionSetError
from tests.test_concept_question_review import question


TABLE = {"headers": ["Sample", "Count"], "rows": [["A", 0], ["B", 4]]}
IMAGE = "https://example.test/dna.png"
PROMPT = "(a) What is DNA? (b) Explain its uses using the supplied sample table and figure."
EXCERPT = "Historical chapter exposition. " * 300


def source_payload():
    return {
        repair.KEY: repair.VERSION,
        quality.KEY: quality.VERSION,
        "records": [{"topic": "DNA", "concept_title": "DNA", "concept_details": "Types: " + PROMPT}],
        "question_task_inventory": {"items": [
            {"qid": "Q1", "raw_task": "What is DNA?", "raw_solution_or_answer": "DNA definition.",
             "shared_context": EXCERPT, "requires_context": True,
             "source_context": {"shared_context": EXCERPT}},
            {"qid": "Q2", "raw_task": "Explain its uses.", "raw_solution_or_answer": "Uses of DNA.",
             "shared_context": EXCERPT, "options": ["source choice A", "source choice B"],
             "tables": [TABLE], "image_urls": [IMAGE],
             "sub_questions": [{"question_text": "Use sample B.", "answer": "4"}],
             "source_context": {"shared_context": EXCERPT, "image_urls": [IMAGE]}},
            {"qid": "Q3", "raw_task": "Describe RNA."},
        ]},
        "type_case_rows": [
            {"row_kind": "example", "type_id": "T1", "case_id": "C1", "example_qid": "Q1"},
            {"row_kind": "example", "type_id": "T2", "case_id": "C2", "example_qid": "Q2"},
        ],
    }


def grouped_question(qid="Q1", members=None, text=PROMPT):
    proposed = question(qid, 0, text)
    proposed.update({
        "source_qids": members if members is not None else ["Q1", "Q2"],
        "context_review": {"action": "remove", "sources": [],
                           "rationale": "The reviewer has made the complete multipart question standalone."},
    })
    proposed["source_dependency_reviews"] = [
        {"source_qid": member, "removed_dependencies": [],
         "rationale": "This member's original structured dependencies remain applicable."}
        for member in proposed["source_qids"]
    ]
    return proposed


def decision(questions, *, omitted=("Q3",)):
    return {
        "questions": questions,
        "original_dispositions": [
            {"source_qid": qid, "disposition": "omitted" if qid in omitted else "edited",
             "rationale": "Explicit reviewed membership."} for qid in ("Q1", "Q2", "Q3")
        ],
        "row_dispositions": [{"concept_row": 0, "rationale": "Complete edited row reviewed."}],
    }


def accept(monkeypatch, payload, proposed, text=PROMPT):
    calls = []

    def call(system, evidence, *, critic=False):
        calls.append((system, copy.deepcopy(evidence), critic))
        if critic:
            return {"verdict": "verified", "issues": []}
        return copy.deepcopy(proposed)

    monkeypatch.setattr(review, "_call", call)
    rows = [{"topic": "DNA", "concept_title": "DNA", "concept_details": "Types: " + text}]
    accepted = review.review_canonical_questions(payload, rows)
    return accepted, calls


def test_corrected_group_preserves_secondary_dependencies_and_removes_exposition(monkeypatch):
    payload = source_payload()
    original = copy.deepcopy(payload)
    accepted, calls = accept(monkeypatch, payload, decision([grouped_question()]))
    assert payload == original
    assert all(review.GROUP_REPAIR in system for system, _, _ in calls)
    assert accepted.receipt["policy"].startswith(review.GROUP_POLICY)
    assert accepted.receipt["original_questions"] == original["question_task_inventory"]["items"]
    edits._apply_question_review(payload, accepted, lane="post")

    items = payload["question_task_inventory"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["qid"] == "Q1"
    assert item["reviewed_source_qids"] == ["Q1", "Q2"]
    assert item["raw_task"] == item["frozen_task_text"] == PROMPT
    assert item["shared_context"] == item["learner_context"] == ""
    assert item["source_context"]["shared_context"] == ""
    assert item["tables"] == [TABLE]
    assert item["image_urls"] == [IMAGE]
    assert item["sub_questions"] == original["question_task_inventory"]["items"][1]["sub_questions"]
    supports = item["source_context"]["reviewed_source_dependencies"]
    assert [support["source_qid"] for support in supports] == ["Q1", "Q2"]
    assert supports[0]["raw_solution_or_answer"] == "DNA definition."
    assert supports[1]["raw_solution_or_answer"] == "Uses of DNA."
    assert supports[1]["options"] == ["source choice A", "source choice B"]
    assert item.get("options", []) == []  # No invented whole-question choice set.
    assert item.get("raw_solution_or_answer", "") == ""
    assert all("shared_context" not in support for support in supports)
    assert payload["review_question_audit"]["omitted"] == ["Q3"]
    assert payload["review_question_audit"]["grouped"][0]["source_evidence"] == original["question_task_inventory"]["items"][:2]
    examples = [row for row in payload["type_case_rows"] if row["row_kind"] == "example"]
    assert len(examples) == 1 and examples[0]["qids"] == ["Q1"]
    assert examples[0]["type_id"] == "T1"  # No forced union with secondary T2.

    display = generation._inventory_task_text(item)
    assert display.startswith(PROMPT) and display.count(IMAGE) == 1
    assert EXCERPT not in display
    atom = inventory.source_atom_from_item(item, source_document_hash="corrected-source")
    authority = materialization._source_wording_authority(atom)
    assert authority["frozen_task_text"] == PROMPT
    assert authority["learner_context"] == ""
    assert authority["supporting_source"]["source_context"]["reviewed_source_dependencies"] == supports
    assert authority["supporting_source"]["tables"] == [TABLE]
    assert IMAGE in authority["supporting_source"]["image_urls"]
    assert EXCERPT not in str(authority["supporting_source"])


def test_explicit_member_removal_does_not_clear_other_dependencies(monkeypatch):
    payload = source_payload()
    proposal = grouped_question()
    proposal["source_dependency_reviews"][1].update({
        "removed_dependencies": ["source_answer", "subquestions"],
        "rationale": "The corrected grouped task replaces this obsolete answer and child projection.",
    })
    rows, _ = accept(monkeypatch, payload, decision([proposal]))
    edits._apply_question_review(payload, rows, lane="post")
    item = payload["question_task_inventory"]["items"][0]
    primary, secondary = item["source_context"]["reviewed_source_dependencies"]
    assert primary["raw_solution_or_answer"] == "DNA definition."
    assert "raw_solution_or_answer" not in secondary
    assert "sub_questions" not in secondary and "sub_questions" not in item
    assert item["tables"] == [TABLE] and item["image_urls"] == [IMAGE]
    assert secondary["options"] == ["source choice A", "source choice B"]


def test_independent_questions_stay_separate_when_api_selects_singletons(monkeypatch):
    payload = source_payload()
    q1 = grouped_question(members=["Q1"], text="What is DNA?")
    q2 = grouped_question(qid="Q2", members=["Q2"], text="Explain its uses.")
    rows, _ = accept(monkeypatch, payload, decision([q1, q2]), "What is DNA? Explain its uses.")
    edits._apply_question_review(payload, rows, lane="post")
    assert [item["qid"] for item in payload["question_task_inventory"]["items"]] == ["Q1", "Q2"]
    assert payload["review_question_audit"]["omitted"] == ["Q3"]
    assert "grouped" not in payload["review_question_audit"]


@pytest.mark.parametrize("defect", ["overlap", "unaccounted", "bad_primary", "unknown", "missing_dependency_decision"])
def test_grouping_requires_closed_world_exact_source_accounting(monkeypatch, defect):
    payload = source_payload()
    q = grouped_question()
    proposed = decision([q])
    if defect == "overlap":
        proposed["questions"].append(grouped_question(qid="Q2", members=["Q2"]))
    elif defect == "unaccounted":
        proposed["original_dispositions"][1]["disposition"] = "omitted"
    elif defect == "bad_primary":
        q["source_qids"] = ["Q2", "Q1"]
    elif defect == "unknown":
        q["source_qids"].append("Q404")
    else:
        q["source_dependency_reviews"].pop()
    with pytest.raises(ReviewedQuestionSetError):
        accept(monkeypatch, payload, proposed)


def test_group_context_requires_explicit_assembly_when_sources_differ(monkeypatch):
    payload = source_payload()
    payload["question_task_inventory"]["items"][1]["shared_context"] = "Required experimental setup."
    q = grouped_question()
    q["context_review"]["action"] = "inherit"
    with pytest.raises(ReviewedQuestionSetError, match="identical contexts"):
        accept(monkeypatch, payload, decision([q]))


def test_v5_schema_requires_explicit_group_and_per_source_decisions():
    schema = review.GroupedReviewVerdict.model_json_schema()
    fields = schema["$defs"]["GroupedReviewedQuestion"]
    assert set(fields["required"]) == set(fields["properties"])
    assert {"source_qids", "source_dependency_reviews"} <= set(fields["required"])
    assert "source_qids" not in review.ReviewedQuestion.model_json_schema()["properties"]


def test_group_dependencies_survive_later_regrouping_without_restoring_removed_answers(monkeypatch):
    payload = source_payload()
    # Historical inventories can hold nonempty dependencies behind direct
    # empty placeholders; preserving them cannot depend on which alias won.
    payload["question_task_inventory"]["items"][1]["source_context"]["options"] = ["nested option"]
    payload["question_task_inventory"]["items"][1]["options"] = []
    rows, _ = accept(monkeypatch, payload, decision([grouped_question()]))
    edits._apply_question_review(payload, rows, lane="post")
    first = payload["question_task_inventory"]["items"][0]
    assert first["source_context"]["reviewed_source_dependencies"][1]["options"] == ["nested option"]

    from app.services.concept_review_context import apply_source_group
    next_group = grouped_question(members=["Q1", "Q3"])
    originals = {"Q1": copy.deepcopy(first), "Q3": {"qid": "Q3", "tables": [{"rows": [[9]]}]}}
    current = copy.deepcopy(first)
    apply_source_group(current, next_group, originals)
    carried = current["source_context"]["reviewed_source_dependencies"][0]["reviewed_source_dependencies"]
    assert carried[0]["raw_solution_or_answer"] == "DNA definition."
    assert carried[1]["tables"] == [TABLE]
    assert carried[1]["options"] == ["nested option"]

    singleton = grouped_question(members=["Q1"])
    singleton["source_dependency_reviews"][0]["removed_dependencies"] = ["source_answer"]
    apply_source_group(current, singleton, {"Q1": current})
    carried = current["source_context"]["reviewed_source_dependencies"][0]["reviewed_source_dependencies"]
    assert "raw_solution_or_answer" not in carried[0]
    assert "raw_solution_or_answer" not in carried[1]
    assert carried[1]["tables"] == [TABLE]
