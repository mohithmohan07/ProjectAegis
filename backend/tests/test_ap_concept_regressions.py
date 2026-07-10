"""Focused regressions for the NCERT Arithmetic Progressions chapter."""
from __future__ import annotations

from app.services import generation as g


def _examples(first: int, last: int, skill: str) -> str:
    return "\n".join(
        f"Example {number} : {skill} in numerical task {number}.\n"
        f"Solution : This worked answer for task {number} must not be inventoried."
        for number in range(first, last + 1)
    )


def _ap_mmd() -> str:
    return rf"""
\section*{{5.1 Introduction}}
Daily-life patterns motivate arithmetic progressions.

\subsection*{{5.2 Arithmetic Progressions}}
An arithmetic progression has a fixed common difference.
The general form is
$$a, a+d, a+2d, a+3d, \ldots$$
In general, the common difference is given by
$$d=a_{{k+1}}-a_k$$
{_examples(1, 2, "Identify the first term and common difference")}

\section*{{EXERCISE 5.1}}
1. Decide whether the taxi-fare sequence is an AP and justify the answer.
2. Write four terms when the first term and common difference are given.

\section*{{5.3 nth Term of an AP}}
Let the first term be $a$ and common difference be $d$.
Looking at the pattern, we can say that the nth term is
$$a_n=a+(n-1)d$$
This general-term method avoids listing every preceding term.
{_examples(3, 8, "Apply the nth-term formula")}

\section*{{Alternative Solution :}}
{_examples(9, 10, "Use the nth-term method in context")}

\section*{{EXERCISE 5.2}}
1. Complete a table using $a_n=a+(n-1)d$.
2. Find which term of an AP has the stated value.

\subsection*{{5.4 Sum of First $n$ Terms of an AP}}
Use the same technique as Gauss to derive the sum.
$$S=a+(a+d)+(a+2d)+\ldots+[a+(n-1)d]$$
Rewriting the terms in reverse order and on adding, we get
$$2S=n[2a+(n-1)d]$$
So the sum of the first $n$ terms is given by
$$S=\frac{{n}}{{2}}[2a+(n-1)d]$$
{_examples(11, 13, "Apply the sum formula")}

\section*{{Remarks:}}
{_examples(14, 14, "Derive the sum of positive integers")}

\section*{{So, the sum of first $n$ positive integers is given by}}
$$S_n=\frac{{n(n+1)}}{{2}}$$
{_examples(15, 15, "Sum a sequence from its nth term")}

\section*{{Solution :}}
Continuation of the preceding worked answer.
{_examples(16, 16, "Model uniform production as an AP")}

\section*{{EXERCISE 5.3}}
1. Find the sum of an AP to ten terms.
2. Solve a daily-life penalty problem using an AP sum.
"""


def _row(topic: str, title: str, *, evidence: str = "") -> dict:
    return {
        "topic": topic,
        "parent_concept": topic,
        "concept_title": title,
        "concept_details": "Description: A source-grounded AP concept.",
        "keywords": "arithmetic progression",
        "source_evidence": evidence,
    }


def _api_row(record: dict) -> dict:
    return {
        "topic": record["topic"],
        "parent_concept": record["parent_concept"],
        "concept": record["concept_title"],
        "concept_description": record["concept_details"],
        "keywords": record["keywords"],
        "source_evidence": record.get("source_evidence", ""),
    }


def test_ap_deterministic_audit_finds_every_worked_example_without_solutions():
    sections = g.parse_mmd_sections(_ap_mmd())
    anchors = g._source_task_anchors(sections)
    worked = [item for item in anchors if item["source_kind"] == "worked_example"]

    assert [item["source_label"] for item in worked] == [
        f"Example {number}" for number in range(1, 17)
    ]
    assert all(item["raw_solution_or_answer"] == "" for item in worked)
    assert all("worked answer" not in item["raw_task"] for item in worked)
    by_label = {item["source_label"]: item["topic_hint"] for item in worked}
    assert by_label["Example 2"] == "Arithmetic Progressions"
    assert by_label["Example 10"] == "nth Term of an AP"
    assert by_label["Example 16"] == "Sum of First $n$ Terms of an AP"


def test_ap_inventory_chunks_preserve_heading_context_and_topic_boundaries():
    sections = g.parse_mmd_sections(_ap_mmd())
    topic_by_section = {
        id(section): topic
        for topic, section in g._sections_with_source_topics(sections)
    }
    chunks = g._inventory_chunks_by_topic(sections, max_chars=100_000)

    assert chunks
    assert all("HEADING PATH:" in chunk["text"] for chunk in chunks)
    assert all(
        {topic_by_section[id(section)] for section in chunk["sections"]}
        == {chunk["source_topic"]}
        for chunk in chunks
    )


def test_ap_gpt_first_inventory_backfills_missed_examples_and_exercises(
    monkeypatch,
):
    calls = {"count": 0}

    def empty_gpt_inventory(system, user, **kwargs):
        calls["count"] += 1
        return {"items": []}

    monkeypatch.setattr(g, "_openai_json", empty_gpt_inventory)
    inventory = g._extract_question_task_inventory_via_api(
        meta=g._metadata(subject="Mathematics"),
        sections=g.parse_mmd_sections(_ap_mmd()),
    )

    assert calls["count"] > 0
    assert inventory["stats"]["worked_examples"] == 16
    assert inventory["stats"]["exercise_questions"] == 6
    assert inventory["stats"]["total_inventory_items"] == 22
    assert all(
        item["raw_solution_or_answer"] == ""
        and "worked answer" not in item["raw_task"]
        for item in inventory["items"]
    )


def test_ap_method_anchors_force_skeleton_retry_and_survive(monkeypatch):
    chunks = g._section_aware_chunks(_ap_mmd(), max_chars=100_000)
    anchors = g._method_coverage_anchors(chunks[0]["sections"])
    assert {anchor["topic_hint"] for anchor in anchors} >= {
        "nth Term of an AP",
        "Sum of First $n$ Terms of an AP",
    }
    base = [
        _row("Arithmetic Progressions", "Common Difference"),
        _row("nth Term of an AP", "General Term"),
        _row("Sum of First $n$ Terms of an AP", "Finite AP Sums"),
        _row("Introduction", "Patterns with Constant Change"),
    ]
    calls = {"count": 0}

    def fake_openai(system, user, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"rows": [_api_row(record) for record in base]}
        assert "OMITTED THESE MANDATORY" in user
        method_rows = [
            _row(
                anchor["topic_hint"],
                f"Required Derivation {index}",
                evidence=anchor["anchor_id"],
            )
            for index, anchor in enumerate(anchors, start=1)
        ]
        return {"rows": [
            _api_row(record) for record in base + method_rows
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kwargs: records)
    out = g._extract_skeleton_via_api(
        chunks, meta=g._metadata(subject="Mathematics"))

    assert calls["count"] == 2
    assert not g._missing_method_anchors(out, anchors)
    assert {anchor["anchor_id"] for anchor in anchors} <= {
        anchor_id for record in out for anchor_id in g._method_anchor_ids(record)
    }


def test_canonicalization_cannot_drop_an_ap_derivation_row(monkeypatch):
    method = _row(
        "nth Term of an AP",
        "Deriving the General Term",
        evidence="METHOD-ABCDEF1234",
    )
    records = [
        _row("Arithmetic Progressions", "Common Difference"),
        method,
        _row("nth Term of an AP", "Finding a Specified Term"),
        _row("Sum of First n Terms of an AP", "Applying the Sum Formula"),
    ]
    response = [
        _row("Arithmetic Progressions", "Common Difference"),
        _row("nth Term of an AP", "Finding a Specified Term"),
        _row("Sum of First n Terms of an AP", "Applying the Sum Formula"),
        _row("Sum of First n Terms of an AP", "Using the Last-Term Form"),
    ]
    monkeypatch.setattr(
        g, "_openai_json",
        lambda *args, **kwargs: {"rows": [_api_row(row) for row in response]},
    )
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda rows, **kwargs: rows)

    out = g._consolidate_concepts_via_api(
        records, subject="Mathematics")

    assert any(
        "METHOD-ABCDEF1234" in record.get("source_evidence", "")
        for record in out
    )
    assert any(
        record["concept_title"] == "Deriving the General Term"
        for record in out
    )


def test_ap_section_is_not_discarded_when_it_matches_chapter_title():
    sections = g.parse_mmd_sections(_ap_mmd())
    assert g._chapter_title_is_main_topic(
        sections, "Arithmetic Progressions")
    records = [
        _row("Arithmetic Progressions", "Common Difference"),
        _row("nth Term of an AP", "General Term"),
        _row("Sum of First n Terms of an AP", "Finite AP Sums"),
    ]

    out = g._snap_topics_to_headings(
        records,
        [
            "Introduction",
            "Arithmetic Progressions",
            "nth Term of an AP",
            "Sum of First n Terms of an AP",
        ],
        chapter_title="Arithmetic Progressions",
        allow_chapter_title_topic=True,
    )

    assert out[0]["topic"] == "Arithmetic Progressions"


def test_cross_topic_mined_type_is_split_at_source_topic_boundary():
    inventory = {"items": [
        {
            "qid": "QINV-0001",
            "topic_hint": "nth Term of an AP",
            "raw_task": "Find the 20th term.",
        },
        {
            "qid": "QINV-0002",
            "topic_hint": "Sum of First n Terms of an AP",
            "raw_task": "Find the sum of the first 20 terms.",
        },
    ]}
    types = [{
        "type_id": "TYPE-0001",
        "type_title": "Applying an AP Formula",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [{
            "case_title": "Use the appropriate AP formula",
            "examples": [
                {
                    "source_question_id": "QINV-0001",
                    "example_prompt": "Find the 20th term.",
                },
                {
                    "source_question_id": "QINV-0002",
                    "example_prompt": "Find the sum of the first 20 terms.",
                },
            ],
        }],
    }]

    out = g._split_mined_types_by_source_topic(types, inventory)

    assert len(out) == 2
    assert {item["topic_match_hint"] for item in out} == {
        "nth Term of an AP",
        "Sum of First n Terms of an AP",
    }
    assert all(len(item["source_question_ids"]) == 1 for item in out)


def test_type_assignment_rejects_wrong_ap_source_topic(monkeypatch):
    records = [
        _row("Arithmetic Progressions", "Common Difference"),
        _row("Sum of First n Terms of an AP", "Applying the Sum Formula"),
    ]
    mined = {"types": [{
        "type_id": "TYPE-0001",
        "type_title": "Finding a Finite AP Sum",
        "topic_match_hint": "Sum of First n Terms of an AP",
        "source_question_ids": ["QINV-0001"],
        "case_prompts": [{
            "case_title": "First term, difference, and number of terms given",
            "examples": [{
                "source_question_id": "QINV-0001",
                "example_prompt": "Find the sum of 2, 7, 12, ... to 10 terms.",
            }],
        }],
    }]}
    calls = {"count": 0}

    def fake_assignment(system, user, **kwargs):
        calls["count"] += 1
        assert '"allowed_concept_ids": ["CONCEPT-0002"]' in user
        if calls["count"] == 1:
            return {"assignments": [{
                "concept_id": "CONCEPT-0001",
                "type_ids": ["TYPE-0001"],
            }]}
        return {"assignments": [{
            "concept_id": "CONCEPT-0002",
            "type_ids": ["TYPE-0001"],
        }]}

    monkeypatch.setattr(g, "_openai_json", fake_assignment)
    out = g._assign_mined_types_via_api(
        records, meta=g._metadata(subject="Mathematics"),
        mined_types=mined, max_attempts=2)

    assert calls["count"] == 2
    assert "Finding a Finite AP Sum" not in out[0]["concept_details"]
    assert "Finding a Finite AP Sum" in out[1]["concept_details"]
    assert not g._mined_type_topic_violations(out, mined)


def test_concept_pipeline_reports_progress_after_skeleton(monkeypatch):
    values: list[tuple[float, str]] = []

    def capture_step(label: str, *, value: float | None = None):
        if value is not None:
            values.append((value, label))

    def capture_progress(value: float, *, label: str = ""):
        values.append((value, label))

    normal = _row("Arithmetic Progressions", "Common Difference")
    normal["concept_details"] = (
        "Description: Consecutive terms have a fixed difference.\n"
        "Achieving Mastery: Identifying the common difference correctly. // "
        "Misconceptions: Subtracting consecutive terms in the wrong order."
    )
    monkeypatch.setattr(g.progress, "step", capture_step)
    monkeypatch.setattr(g.progress, "set_progress", capture_progress)
    monkeypatch.setattr(
        g, "_extract_skeleton_via_api",
        lambda chunks, **kwargs: [dict(normal)])
    monkeypatch.setattr(
        g, "_consolidate_concepts_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_refine_descriptions_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_ensure_mastery_lines_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_extract_question_task_inventory_via_api",
        lambda **kwargs: g._empty_inventory())
    monkeypatch.setattr(
        g, "_mine_types_from_inventory_via_api",
        lambda **kwargs: {"types": []})
    monkeypatch.setattr(
        g, "_build_culminations_via_api",
        lambda records, **kwargs: g._ensure_culmination_rows(records))
    monkeypatch.setattr(
        g, "_assign_types_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_merge_similar_concepts_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_ensure_misconceptions_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_repair_records_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_neutralize_unrepaired_rows", lambda records: records)
    monkeypatch.setattr(
        g, "_salvage_short_case_examples",
        lambda records, **kwargs: records)
    monkeypatch.setattr(g.cr, "refine_chapter", lambda records: records)
    monkeypatch.setattr(
        g, "_validate_final_or_raise",
        lambda records, **kwargs: {"ok": True})

    g.concepts_from_mmd(
        r"\section*{5.2 Arithmetic Progressions}"
        "\nAn AP has a fixed common difference.",
        subject="Mathematics",
        chapter_title="Arithmetic Progressions",
        live=True,
    )

    progress_values = [value for value, _ in values]
    assert progress_values == sorted(progress_values)
    assert {0.58, 0.72, 0.81, 0.85, 0.93, 1.0} <= set(progress_values)
    labels = " ".join(label for _, label in values)
    assert "inventorying questions" in labels
    assert "assigning Types within source topics" in labels
    assert "validating and repairing final map" in labels
