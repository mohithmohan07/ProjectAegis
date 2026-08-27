"""Focused regressions for the NCERT Arithmetic Progressions chapter."""
from __future__ import annotations

import pytest

from app.services import generation as g


def _pass_completed_phase3(records, **kwargs):
    kwargs["phase3_carry"].update({
        "pre_map": {"rows": [], "topics": []},
        "pre_questions": {
            "plans": {}, "questions": {}, "blocked": {},
        },
        "pre_snapshot_writes": {},
    })
    return records


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


def _semantic_placement_mmd() -> str:
    """Representative source excerpts copied from the audited AP chapter."""
    return r"""
\section*{5.2 Arithmetic Progressions}
An arithmetic progression (AP) is a list of numbers in which each term is
obtained by adding a fixed number to the preceding term.
The general form of an AP is a, a+d, a+2d, a+3d, ...
The common difference of the AP can be positive, negative or zero.
A shared arithmetic progression phrase appears in both source topics.

\section*{Example 2}
The difference of any two consecutive terms is the same.

\section*{5.3 nth Term of an AP}
The nth term is given by a_n=a+(n-1)d.
A shared arithmetic progression phrase appears in both source topics.

\section*{Example 3}
Find the 10th term of the AP: 2, 7, 12, ...
We have a_n=a+(n-1)d.

\section*{Example 4}
Which term of the AP: 21,18,15,... is -81?

\section*{Example 6}
Check whether 301 is a term of the list of numbers.

\section*{Example 7}
How many two-digit numbers are divisible by 3?

\section*{Example 8}
Find the 11th term from the last term.

\section*{Alternative Solution}
If we write the given AP in the reverse order, then a=-62 and d=3.

\section*{Example 9}
A sum of ₹1000 is invested at 8% simple interest per year.

\section*{Example 10}
In a flower bed, there are 23 rose plants in the first row, 21 in the second,
19 in the third, and so on.

\section*{EXERCISE 5.2}
Use the nth-term rule to answer each exercise-derived question.

\section*{5.4 Sum of First n Terms of an AP}
Rewriting the terms in reverse order, we have the same finite sum.
On adding the two orders, the sum of the first n terms is obtained.

\section*{5.5 Summary}
The chapter recap repeats formulas from every section.

\section*{A Note to the Reader}
If a, b, c are in AP, then b = (a+c)/2.
"""


def _mathpix_ocr_edge_mmd() -> str:
    """Small synthetic fixture covering real Mathpix heading/task shapes."""
    return r"""
\section*{5．1 Introduction}
Patterns with a constant change occur in school timetables.

\subsection*{5.2 Progressions}
Example 1 : Decide whether 4, 7, 10, 13 is a progression with constant change.

\section*{Solution :}
Subtract consecutive terms to obtain the worked answer.

\section*{Alternative Solution :}
Compare each term with the preceding term.

\section*{Remarks:}
The comparison must use the same term order.

\subsection*{5.3 General Term}
The nth-position rule gives a term directly.

\section*{EXERCISE 5.3 (Optional)*}
1. Read the pattern shown in Fig. 5.1 and state its next term.
![](https://cdn.mathpix.com/cropped/synthetic-fig-5-1.jpg)
"""


def _fake_authored_culminations(records: list[dict]) -> list[dict]:
    """Test stand-in for the model-authored culmination pass.

    Production never synthesizes culmination rows from code any more; this
    fixture plays the model's role by appending one authored culmination to
    every multi-concept topic.
    """
    out: list[dict] = []
    topics: dict[str, list[dict]] = {}
    order: list[str] = []
    for rec in records:
        topic = rec.get("topic", "")
        if topic not in topics:
            topics[topic] = []
            order.append(topic)
        if not g.cr.is_culmination(rec.get("concept_title", "")):
            topics[topic].append(rec)
    for topic in order:
        out.extend(topics[topic])
        if len(topics[topic]) < 2:
            continue
        out.append({
            "topic": topic,
            "parent_concept": "Culmination",
            "concept_title": f"Culmination - {topic}",
            "concept_details": (
                f"Description: An authored consolidation tying {topic}'s "
                "concepts together into one applied account."
            ),
            "keywords": "culmination, recap, mixed application",
        })
    return out


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
    assert by_label["Example 16"] == "Sum of First n Terms of an AP"


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


def test_source_topic_excerpts_group_structural_sections_under_main_topic():
    groups = g._group_source_topic_excerpts(
        g.parse_mmd_sections(_semantic_placement_mmd()))

    assert [group["topic"] for group in groups] == [
        "Arithmetic Progressions",
        "nth Term of an AP",
        "Sum of First n Terms of an AP",
        # The numbered 5.5 Summary section is structure the model sees;
        # whether its recap contributes concepts is the model's judgment.
        "Summary",
    ]
    nth_excerpt = groups[1]["excerpt"]
    assert "HEADING PATH: Example 3" in nth_excerpt
    assert "HEADING PATH: Alternative Solution" in nth_excerpt
    assert "HEADING PATH: EXERCISE 5.2" in nth_excerpt
    assert "A sum of ₹1000 is invested" in nth_excerpt
    assert "flower bed" in nth_excerpt
    assert "Example 3" not in groups[0]["excerpt"]
    # Editorial postscripts are no longer withheld from the model (§3 purge):
    # the note rides in its structurally owning topic's excerpt.
    assert any(
        "A Note to the Reader" in group["excerpt"] for group in groups)


def test_exact_source_evidence_places_audited_ap_concepts_semantically():
    groups = g._group_source_topic_excerpts(
        g.parse_mmd_sections(_semantic_placement_mmd()))
    records = [
        _row(
            "Arithmetic Progressions",
            "Definition and General Form of an Arithmetic Progression",
            evidence=(
                "An arithmetic progression (AP) is a list of numbers... "
                "The general form of an AP is a, a+d, a+2d, a+3d, ... | "
                "common difference of the AP... can be positive, negative or zero"
            ),
        ),
        _row(
            "Arithmetic Progressions",
            "Finding Specific Terms and Term Positions",
            evidence=(
                "Find the 10th term... we have a_n=a+(n-1)d | "
                "Which term of the AP: 21,18,15,... is -81? | "
                "Check whether 301 is a term of the list of numbers"
            ),
        ),
        _row(
            "Arithmetic Progressions",
            "Counting Terms and Working from the End of a Finite AP",
            evidence=(
                "How many two-digit numbers are divisible by 3? | "
                "Find the 11th term from the last term | "
                "If we write the given AP in the reverse order, then a=-62 and d=3"
            ),
        ),
        _row(
            "Arithmetic Progressions",
            "Modeling Real-life Situations with Arithmetic Progressions",
            evidence=(
                "some daily life problems | "
                "A sum of ₹1000 is invested at 8% simple interest per year | "
                "In a flower bed, there are 23 rose plants in the first row..."
            ),
        ),
        _row(
            "Arithmetic Progressions",
            "Using the Sum Formula in Problems",
            evidence=(
                "Rewriting the terms in reverse order | "
                "On adding the two orders, the sum of the first n terms is obtained"
            ),
        ),
        _row(
            "Model-selected Topic",
            "Ambiguous Shared Evidence",
            evidence=(
                "A shared arithmetic progression phrase appears in both source topics"
            ),
        ),
        _row(
            "Arithmetic Progressions",
            "Arithmetic Mean as the Middle Term of Three Numbers in AP",
            evidence="If a, b, c are in AP, then b = (a+c)/2",
        ),
        _row(
            "Anchor-authoritative Topic",
            "Anchored Method",
            evidence=(
                "METHOD-ABCDEF1234 | "
                "A sum of ₹1000 is invested at 8% simple interest per year"
            ),
        ),
    ]

    out = g._assign_topics_from_source_evidence(records, groups)
    by_title = {record["concept_title"]: record["topic"] for record in out}

    assert by_title == {
        "Definition and General Form of an Arithmetic Progression": (
            "Arithmetic Progressions"
        ),
        "Finding Specific Terms and Term Positions": "nth Term of an AP",
        "Counting Terms and Working from the End of a Finite AP": (
            "nth Term of an AP"
        ),
        "Modeling Real-life Situations with Arithmetic Progressions": (
            "nth Term of an AP"
        ),
        "Using the Sum Formula in Problems": "Sum of First n Terms of an AP",
        "Ambiguous Shared Evidence": "Model-selected Topic",
        # The "A Note to the Reader" postscript physically follows the 5.5
        # Summary section; with the heading vocabulary purged its text lives
        # in the Summary excerpt, so exact evidence grounds the row there.
        "Arithmetic Mean as the Middle Term of Three Numbers in AP": (
            "Summary"
        ),
        "Anchored Method": "Anchor-authoritative Topic",
    }


def test_topic_restructuring_applies_exact_evidence_before_and_after_gpt(
    monkeypatch,
):
    groups = g._group_source_topic_excerpts(
        g.parse_mmd_sections(_semantic_placement_mmd()))
    record = _row(
        "Arithmetic Progressions",
        "Modeling Real-life Situations with Arithmetic Progressions",
        evidence=(
            "A sum of ₹1000 is invested at 8% simple interest per year | "
            "In a flower bed, there are 23 rose plants in the first row..."
        ),
    )
    anchored = _row(
        "nth Term of an AP",
        "Deriving the Nth-term Formula",
        evidence="METHOD-ABCDEF1234 | a_n=a+(n-1)d",
    )

    def wrong_topic_response(system, user, **kwargs):
        # Exact grounding runs before GPT, so its input already has the source
        # topic even though this simulated response tries to move it back.
        assert '"topic": "nth Term of an AP"' in user
        assert "SOURCE TOPIC EXCERPTS" in user
        assert "flower bed" in user
        return {"rows": [{
            **_api_row(record),
            "topic": "Arithmetic Progressions",
        }, {
            **_api_row(anchored),
            "topic": "Arithmetic Progressions",
        }]}

    monkeypatch.setattr(g, "_openai_json", wrong_topic_response)
    out = g._restructure_topics_via_api(
        [record, anchored],
        meta=g._metadata(subject="Mathematics"),
        source_topic_excerpts=groups,
    )

    assert out[0]["topic"] == "nth Term of an AP"
    assert out[1]["topic"] == "nth Term of an AP"


def test_ap_gpt_first_inventory_backfills_missed_examples_and_exercises(
    monkeypatch,
):
    calls = {"count": 0}

    def empty_gpt_inventory(system, user, **kwargs):
        if "inventory-completeness reviewer" in system:
            # This test isolates deterministic anchor backfill; the
            # reviewer's own contract has dedicated coverage.
            return {"verdict": "complete", "reason": ""}
        calls["count"] += 1
        return {"items": []}

    monkeypatch.setattr(g, "_openai_json", empty_gpt_inventory)
    inventory = g._extract_question_task_inventory_via_api(
        meta=g._metadata(subject="Mathematics"),
        sections=g.parse_mmd_sections(_ap_mmd()),
    )

    assert calls["count"] > 0
    assert inventory["stats"]["worked_examples"] == 16
    # Backfilled list items carry the neutral parse-time kind (§3 purge);
    # the outline/page model verdict owns the exercise split, so the
    # deterministic stats count them as subjective items, not exercises.
    assert inventory["stats"]["exercise_questions"] == 0
    assert sum(
        item["source_kind"] == "intext_question"
        for item in inventory["items"]
    ) == 6
    assert inventory["stats"]["total_inventory_items"] == 22
    assert all(
        item["raw_solution_or_answer"] == ""
        and "worked answer" not in item["raw_task"]
        for item in inventory["items"]
    )


def test_ap_inventory_merges_numbered_gpt_rows_with_source_anchors():
    spiral = (
        "A spiral is made up of successive semicircles, with centres alternately "
        "at A and B. What is the total length of thirteen consecutive semicircles?"
    )
    ladder = (
        "A ladder has rungs 25 cm apart. The rungs decrease uniformly in length "
        "from 45 cm at the bottom to 25 cm at the top. What length of wood is required?"
    )
    gpt_items = [
        {
            "source_kind": "exercise",
            "source_label": "EXERCISE 5.3",
            "raw_task": f"18. {spiral}",
            "normalized_task": f"18. {spiral}",
        },
        {
            "source_kind": "exercise",
            "source_label": "EXERCISE 5.4 (Optional)*",
            "raw_task": f"3. {ladder}",
            "normalized_task": f"3. {ladder}",
        },
    ]
    anchors = [
        {
            "source_kind": "exercise",
            "source_label": "EXERCISE 5.3 Q18",
            "parent_source_label": "EXERCISE 5.3",
            "topic_hint": "Sum of First n Terms of an AP",
            "raw_task": f"{spiral} [Hint: pair the semicircle lengths.]",
            "normalized_task": f"{spiral} [Hint: pair the semicircle lengths.]",
            "raw_solution_or_answer": "",
            "image_urls": ["https://cdn.mathpix.com/spiral.jpg"],
            "requires_visual": True,
        },
        {
            "source_kind": "exercise",
            "source_label": "EXERCISE 5.4 (Optional)* Q3",
            "parent_source_label": "EXERCISE 5.4 (Optional)*",
            "topic_hint": "Sum of First n Terms of an AP",
            "raw_task": f"{ladder} [Hint: count the rungs.]",
            "normalized_task": f"{ladder} [Hint: count the rungs.]",
            "raw_solution_or_answer": "",
            "image_urls": ["https://cdn.mathpix.com/ladder.jpg"],
            "requires_visual": True,
        },
    ]

    merged = g._merge_source_task_anchors(gpt_items, anchors)

    assert len(merged) == 2
    assert [item["source_label"] for item in merged] == [
        "EXERCISE 5.3 Q18",
        "EXERCISE 5.4 (Optional)* Q3",
    ]
    assert all(item["requires_visual"] for item in merged)


def test_inventory_does_not_retry_when_reviewer_rules_complete(monkeypatch):
    """The reviewer's verdict, not a marker regex, decides whether to retry."""
    calls = {"extract": 0, "review": 0}

    def gpt(system, user, **kwargs):
        if "inventory-completeness reviewer" in system:
            calls["review"] += 1
            return {
                "verdict": "complete",
                "reason": "the chunk prints no assessable task",
            }
        calls["extract"] += 1
        return {"items": []}

    sections = g.parse_mmd_sections(
        r"\section*{Arithmetic Progressions}" "\n"
        "This chapter introduces arithmetic progressions."
    )
    chunks = g._inventory_chunks_by_topic(sections)
    assert len(chunks) == 1

    monkeypatch.setattr(g, "_openai_json", gpt)
    inventory = g._extract_question_task_inventory_via_api(
        meta=g._metadata(subject="Mathematics"),
        sections=sections,
    )

    assert calls["extract"] == len(chunks)
    assert calls["review"] == len(chunks)
    assert inventory["items"] == []


def test_ap_method_anchors_force_skeleton_retry_and_survive(monkeypatch):
    chunks = g._section_aware_chunks(_ap_mmd(), max_chars=100_000)
    anchors = g._method_coverage_anchors(chunks[0]["sections"])
    assert {anchor["topic_hint"] for anchor in anchors} >= {
        "nth Term of an AP",
        "Sum of First n Terms of an AP",
    }
    base = [
        _row("Arithmetic Progressions", "Common Difference"),
        _row("nth Term of an AP", "General Term"),
        _row("Sum of First n Terms of an AP", "Finite AP Sums"),
        _row("Introduction", "Patterns with Constant Change"),
    ]
    calls = {"count": 0}

    def fake_openai(system, user, **kwargs):
        if "audit a concept-skeleton extraction" in system:
            # The per-chunk audit is a separate advisory verdict with its own
            # regressions; this test isolates the method-anchor retry.
            return {"coverage": "complete", "grain": "sound", "reason": ""}
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


def test_formula_less_prose_method_anchor_is_covered_only_by_same_topic_evidence():
    sections = g.parse_mmd_sections(
        r"\section*{2.1 Comparing Historical Sources}" "\n"
        "The corroboration method compares independent accounts side by side "
        "to identify agreements and contradictions before drawing a conclusion."
    )
    anchors = g._method_coverage_anchors(sections)

    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor["required_formulas"] == []
    assert {"corroboration", "independent", "accounts"} <= set(
        anchor["evidence_terms"])

    covered = _row(
        "Comparing Historical Sources",
        "Corroborating Independent Accounts",
    )
    covered["concept_details"] = (
        "Description: Compare independent accounts to identify agreements and "
        "contradictions before reaching a historical conclusion."
    )
    wrong_topic = dict(covered, topic="Interpreting Timelines")
    unrelated_same_topic = _row(
        "Comparing Historical Sources",
        "Introducing the Source Collection",
    )
    unrelated_same_topic["concept_details"] = (
        "Description: The chapter names the sources and gives their dates."
    )

    assert g._method_anchor_covered([covered], anchor)
    assert not g._method_anchor_covered([wrong_topic], anchor)
    assert not g._method_anchor_covered([unrelated_same_topic], anchor)


def test_canonical_method_tags_prefer_formula_row_and_restore_without_duplicate(
    monkeypatch,
):
    topic = "Sum of First n Terms of an AP"
    anchors = [
        {
            "anchor_id": "METHOD-BBBBBBBBBB",
            "topic_hint": topic,
            "required_formulas": [r"2S=n[2a+(n-1)d]"],
            "source_evidence": "reverse-order addition doubles the finite sum",
            "evidence_terms": ["reverse", "order", "addition", "finite", "sum"],
        },
        {
            "anchor_id": "METHOD-CCCCCCCCCC",
            "topic_hint": topic,
            "required_formulas": [r"S=\frac{n}{2}[2a+(n-1)d]"],
            "source_evidence": "halving the doubled sum gives the sum rule",
            "evidence_terms": ["halving", "doubled", "sum"],
        },
    ]
    prose_row = _row(topic, "Explaining Reverse-Order Addition")
    prose_row["concept_details"] = (
        "Description: Reverse-order addition is introduced for finite sums."
    )
    formula_row = _row(
        topic,
        "Deriving the Finite Sum",
        evidence="textbook equations | METHOD-AAAAAAAAAA",
    )
    formula_row["concept_details"] = (
        r"Description: Adding in reverse gives $2S=n[2a+(n-1)d]$, then "
        r"$S=\frac{n}{2}[2a+(n-1)d]$."
    )

    monkeypatch.setattr(
        g,
        "_recover_method_anchor_rows_via_api",
        lambda *args, **kwargs: pytest.fail("formula coverage must not recover"),
    )
    tagged = g._canonicalize_method_anchor_tags(
        [prose_row, formula_row],
        anchors,
        chunk_text="source",
        meta=g._metadata(subject="Mathematics"),
    )

    assert len(tagged) == 2
    assert g._method_anchor_ids(tagged[0]) == set()
    assert g._method_anchor_ids(tagged[1]) == {
        "METHOD-AAAAAAAAAA",
        "METHOD-BBBBBBBBBB",
        "METHOD-CCCCCCCCCC",
    }
    assert tagged[1]["source_evidence"].startswith(
        "textbook equations | METHOD-AAAAAAAAAA")
    snapshot = g._snapshot_method_anchor_rows(tagged, anchors)
    assert set(snapshot) == {
        (anchor["anchor_id"], g._topic_comparison_key(topic))
        for anchor in anchors
    }
    survivor = dict(tagged[1])
    survivor["topic"] = "Model-selected topic"
    survivor["source_evidence"] = "surviving refined evidence"

    restored = g._restore_method_anchor_rows([tagged[0], survivor], snapshot)

    assert len(restored) == 2
    restored_formula = next(
        row for row in restored
        if row["concept_title"] == "Deriving the Finite Sum"
    )
    assert restored_formula["topic"] == topic
    assert {anchor["anchor_id"] for anchor in anchors} <= (
        g._method_anchor_ids(restored_formula)
    )
    assert sum(
        row["concept_title"] == "Deriving the Finite Sum" for row in restored
    ) == 1


def test_canonical_method_tags_recover_instead_of_tagging_unrelated_row(
    monkeypatch,
):
    anchor = {
        "anchor_id": "METHOD-DDDDDDDDDD",
        "topic_hint": "nth Term of an AP",
        "required_formulas": [r"a_n=a+(n-1)d"],
        "source_evidence": "derive the nth term by repeated addition",
        "evidence_terms": ["repeated", "addition", "term"],
    }
    unrelated = _row(
        anchor["topic_hint"],
        "Recognising an Arithmetic Progression",
        evidence="constant differences identify an AP",
    )
    recovered = _row(
        anchor["topic_hint"],
        "Deriving the General Term",
        evidence=anchor["anchor_id"],
    )
    recovered["concept_details"] = (
        r"Description: Repeated addition gives $a_n=a+(n-1)d$."
    )
    calls: list[list[dict]] = []

    def fake_recovery(missing, **kwargs):
        calls.append(missing)
        return [recovered]

    monkeypatch.setattr(
        g, "_recover_method_anchor_rows_via_api", fake_recovery)
    tagged = g._canonicalize_method_anchor_tags(
        [unrelated],
        [anchor],
        chunk_text="source",
        meta=g._metadata(subject="Mathematics"),
    )

    assert calls == [[anchor]]
    assert len(tagged) == 2
    assert tagged[0] == unrelated
    assert g._method_anchor_ids(tagged[0]) == set()
    assert tagged[1]["topic"] == anchor["topic_hint"]
    assert g._method_anchor_ids(tagged[1]) == {anchor["anchor_id"]}
    assert len({
        g.bi.normalize_question_text(row["concept_title"]) for row in tagged
    }) == len(tagged)


def test_anchor_retry_prefers_tagged_duplicate_concept_row(monkeypatch):
    chunks = g._section_aware_chunks(
        r"\section*{2.1 Building a General Rule}" "\n"
        r"Use a derivation method to build the rule $$u_k=u_1+(k-1)c$$.",
        max_chars=100_000,
    )
    anchor = g._method_coverage_anchors(chunks[0]["sections"])[0]
    first = _row("Building a General Rule", "Deriving the General Rule")
    retry = _row(
        "Building a General Rule",
        "Deriving the General Rule",
        evidence=anchor["anchor_id"],
    )
    calls = {"count": 0}

    def fake_openai(system, user, **kwargs):
        if "audit a concept-skeleton extraction" in system:
            return {"coverage": "complete", "grain": "sound", "reason": ""}
        calls["count"] += 1
        return {"rows": [_api_row(first if calls["count"] == 1 else retry)]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kwargs: records)

    out = g._extract_skeleton_via_api(
        chunks, meta=g._metadata(subject="Mathematics"))

    assert calls["count"] == 2
    assert len(out) == 1
    assert anchor["anchor_id"] in out[0]["source_evidence"]


def test_focused_method_recovery_restores_anchor_after_broad_retry_omits_it(
    monkeypatch,
):
    chunks = g._section_aware_chunks(
        r"\section*{2.1 Building a General Rule}" "\n"
        r"Use a derivation method to build the rule $$u_k=u_1+(k-1)c$$.",
        max_chars=100_000,
    )
    anchor = g._method_coverage_anchors(chunks[0]["sections"])[0]
    untagged = _row(
        anchor["topic_hint"],
        "Deriving the General Rule",
    )
    focused = _row(
        "Wrong Model Topic",
        "Deriving the General Rule",
        evidence=f"{anchor['anchor_id']} | model-selected evidence",
    )
    focused["concept_details"] = (
        "Description: Derive the term at position k by adding k minus one "
        "copies of the common change to the first term."
    )
    calls: list[tuple[str, str]] = []

    def fake_openai(system, user, **kwargs):
        if "audit a concept-skeleton extraction" in system:
            return {"coverage": "complete", "grain": "sound", "reason": ""}
        calls.append((system, user))
        if len(calls) <= 2:
            return {"rows": [_api_row(untagged)]}
        assert "focused recovery" in system.lower()
        assert anchor["anchor_id"] in user
        assert anchor["source_evidence"] in user
        assert all(formula in user for formula in anchor["required_formulas"])
        assert chunks[0]["text"] in user
        return {"rows": [_api_row(focused)]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kwargs: records)

    out = g._extract_skeleton_via_api(
        chunks, meta=g._metadata(subject="Mathematics"))

    assert len(calls) == 3
    assert "Return the COMPLETE corrected skeleton" in calls[1][1]
    assert "Return the COMPLETE corrected skeleton" not in calls[2][1]
    assert len(out) == 1
    assert out[0]["topic"] == anchor["topic_hint"]
    assert out[0]["concept_details"] == focused["concept_details"]
    assert anchor["anchor_id"] in out[0]["source_evidence"]
    assert anchor["source_evidence"] in out[0]["source_evidence"]
    assert "model-selected evidence" not in out[0]["source_evidence"]
    assert not g._missing_method_anchors(out, [anchor])


def test_focused_method_recovery_fails_clearly_after_malformed_responses(
    monkeypatch,
):
    chunks = g._section_aware_chunks(
        r"\section*{2.1 Building a General Rule}" "\n"
        r"Use a derivation method to build the rule $$u_k=u_1+(k-1)c$$.",
        max_chars=100_000,
    )
    anchor = g._method_coverage_anchors(chunks[0]["sections"])[0]
    untagged = _row(
        anchor["topic_hint"],
        "Deriving the General Rule",
    )
    wrong_tag = _row(
        anchor["topic_hint"],
        "Deriving the General Rule",
        evidence="METHOD-ABCDEF1234",
    )
    calls = {"count": 0}

    def fake_openai(system, user, **kwargs):
        if "audit a concept-skeleton extraction" in system:
            return {"coverage": "complete", "grain": "sound", "reason": ""}
        calls["count"] += 1
        if calls["count"] <= 2:
            return {"rows": [_api_row(untagged)]}
        if calls["count"] == 3:
            return {"rows": "not-a-list"}
        if calls["count"] == 4:
            return {"rows": [{"topic": anchor["topic_hint"], "concept": ""}]}
        return {"rows": [_api_row(wrong_tag)]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kwargs: records)

    with pytest.raises(
        RuntimeError,
        match=(
            r"focused method-anchor recovery failed after 3 attempt\(s\).*"
            + anchor["anchor_id"]
        ),
    ):
        g._extract_skeleton_via_api(
            chunks, meta=g._metadata(subject="Mathematics"))

    assert calls["count"] == 5


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


def test_pipeline_restores_skeleton_method_rows_before_content_passes(
    monkeypatch,
):
    """Method rows dropped by canonicalization are restored pre-boundary.

    The dedicated pre-81% Description pass is retired (Settle authors content
    inside the rewritten Phase 3), so the earliest surviving content pass —
    the method worked-example pass — must already see every restored
    mandatory method row, and the rows must still reach the 81% boundary.
    """
    # This test stubs the WHOLE extraction function, which the Q19 early
    # inventory track legitimately bypasses — pin the sequential path.
    monkeypatch.setenv("AEGIS_SOURCE_CHUNK_WORKERS", "1")
    anchors = [
        {
            "anchor_id": "METHOD-1111111111",
            "topic_hint": "Arithmetic Progressions",
            "required_formulas": [],
            "source_evidence": "common difference derivation",
            "evidence_terms": ["common", "difference", "derivation"],
        },
        {
            "anchor_id": "METHOD-2222222222",
            "topic_hint": "nth Term of an AP",
            "required_formulas": [r"a_n=a+(n-1)d"],
            "source_evidence": "general term derivation",
            "evidence_terms": ["general", "term", "derivation"],
        },
        {
            "anchor_id": "METHOD-3333333333",
            "topic_hint": "Sum of First n Terms of an AP",
            "required_formulas": [],
            "source_evidence": "finite sum derivation",
            "evidence_terms": ["finite", "sum", "derivation"],
        },
    ]
    skeleton = [
        _row(
            anchor["topic_hint"],
            title,
            evidence=f"{anchor['anchor_id']} | {anchor['source_evidence']}",
        )
        for anchor, title in zip(
            anchors,
            [
                "Deriving the Common Difference",
                "Deriving the General Term",
                "Deriving the Finite Sum",
            ],
        )
    ]
    skeleton[1]["source_evidence"] = "general-term textbook equation"
    skeleton[1]["concept_details"] = (
        r"Description: Repeated addition gives $a_n=a+(n-1)d$."
    )
    skeleton.append(_row("Introduction", "Patterns with Constant Change"))
    dropped_titles = {
        "Deriving the General Term",
        "Deriving the Finite Sum",
    }
    content_pass_input_ids: set[str] = set()

    monkeypatch.setattr(
        g, "_method_coverage_anchors", lambda sections: anchors)
    monkeypatch.setattr(
        g, "_extract_skeleton_via_api",
        lambda chunks, **kwargs: [dict(record) for record in skeleton])

    def drop_two_rows_during_canonicalization(records, **kwargs):
        assert {
            anchor_id
            for record in records
            for anchor_id in g._method_anchor_ids(record)
        } == {anchor["anchor_id"] for anchor in anchors}
        return [
            dict(record) for record in records
            if record["concept_title"] not in dropped_titles
        ]

    monkeypatch.setattr(
        g, "_consolidate_concepts_via_api",
        drop_two_rows_during_canonicalization)
    monkeypatch.setattr(
        g, "_topic_segregation_verdict_via_api",
        lambda records, **kwargs: {"restructure": True, "reason": "test"})
    monkeypatch.setattr(
        g, "_restructure_topics_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_missing_source_topic_excerpts",
        lambda records, source_topic_excerpts: [])
    monkeypatch.setattr(
        g, "_consolidate_task_grounded_fragments_via_api",
        lambda records, **kwargs: records)

    def capture_and_refine_content(records, **kwargs):
        nonlocal content_pass_input_ids
        content_pass_input_ids = {
            anchor_id
            for record in records
            for anchor_id in g._method_anchor_ids(record)
        }
        assert content_pass_input_ids == {
            anchor["anchor_id"] for anchor in anchors
        }
        refined = []
        for index, record in enumerate(records, start=1):
            record = dict(record)
            record["concept_details"] = (
                f"Description: Refined before the boundary: "
                f"{record['concept_title']}.\n"
                f"Achieving Mastery: Explain {record['concept_title']} "
                "independently. // "
                "Misconception/ Error Analysis: Misconceptions: Students may "
                "believe any sequence is an arithmetic progression even when "
                "consecutive differences change.; Error Analysis: Students may "
                "omit checking consecutive differences before applying the "
                "relevant AP derivation."
            )
            # Q1: a row carrying the analysis section models an allotted
            # row — the assemble-stamped marker rides with it.
            record["_aegis_analysis_allotments"] = [f"LA-{index:04d}"]
            refined.append(record)
        return refined

    monkeypatch.setattr(
        g, "_ensure_method_worked_examples_via_api",
        capture_and_refine_content)
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
        lambda records, **kwargs: _fake_authored_culminations(records))
    monkeypatch.setattr(
        g, "_repair_records_via_api",
        lambda records, **kwargs: records)
    # The post-81% step is the rewritten Phase 3; this test covers only the
    # pre-boundary restoration, so hand the boundary rows straight through.
    monkeypatch.setattr(
        g, "_prepare_final_concept_content", _pass_completed_phase3)

    out = g.concepts_from_mmd(
        _ap_mmd(),
        subject="Mathematics",
        chapter_title="Arithmetic Progressions",
        live=True,
    )

    expected_ids = {anchor["anchor_id"] for anchor in anchors}
    assert content_pass_input_ids == expected_ids
    assert {
        anchor_id
        for record in out
        for anchor_id in g._method_anchor_ids(record)
    } == expected_ids
    assert all(
        "Refined before the boundary:" in record["concept_details"]
        for record in out
        if g._method_anchor_ids(record)
    )
    title_keys = [
        g.bi.normalize_question_text(record["concept_title"])
        for record in out
    ]
    assert len(title_keys) == len(set(title_keys))
    for topic in {record["topic"] for record in out}:
        topic_rows = [record for record in out if record["topic"] == topic]
        taught = [
            record for record in topic_rows
            if not g.cr.is_culmination(record["concept_title"])
        ]
        # A culmination closes a topic that teaches several concepts; a
        # single-concept topic has nothing to consolidate and carries none.
        if len(taught) > 1:
            assert g.cr.is_culmination(topic_rows[-1]["concept_title"])
        else:
            assert topic_rows == taught
    assert g._missing_method_anchors(out, anchors) == []


def test_method_anchor_id_is_stable_across_chunk_topic_context():
    sections = g.parse_mmd_sections(
        r"""
\section*{5.1 Introduction}
Patterns begin the chapter.
\subsection*{5.2 Arithmetic Progressions}
An AP has a common difference.
\section*{5.3 nth Term of an AP}
An AP term has a position.
\subsection*{5.4 Sum of First $n$ Terms of an AP}
Finite AP terms can be added.
\section*{So, the sum of the first $n$ terms of an AP is given by}
$$S=\frac{n}{2}[2a+(n-1)d]$$
\section*{So, the sum of first $n$ positive integers is given by}
$$S_n=\frac{n(n+1)}{2}$$
"""
    )
    local_sections = sections[-3:]
    formula = g._normalize_math_evidence(r"S_n=\frac{n(n+1)}{2}")
    global_anchor = next(
        anchor for anchor in g._method_coverage_anchors(sections)
        if formula in {
            g._normalize_math_evidence(value)
            for value in anchor["required_formulas"]
        }
    )
    local_anchor = next(
        anchor for anchor in g._method_coverage_anchors(local_sections)
        if formula in {
            g._normalize_math_evidence(value)
            for value in anchor["required_formulas"]
        }
    )

    assert global_anchor["topic_hint"] == "Sum of First n Terms of an AP"
    assert local_anchor["topic_hint"] == (
        "So, the sum of first n positive integers is given by"
    )
    assert local_anchor["anchor_id"] == global_anchor["anchor_id"]

    row = _row(
        local_anchor["topic_hint"],
        "Derive the Sum of the First n Positive Integers",
        evidence=local_anchor["anchor_id"],
    )
    out = g._enforce_method_anchor_topics([row], [global_anchor])

    assert out[0]["topic"] == global_anchor["topic_hint"]
    assert g._method_anchor_covered(out, global_anchor)


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
        "placement_scope": "cross_topic_synthesis",
        "source_question_ids": ["QINV-0001", "QINV-0002"],
        "case_prompts": [{
            "case_title": "Use the appropriate AP formula",
            "placement_scope": "cross_topic_synthesis",
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
    assert all(item["placement_scope"] == "mixed_synthesis" for item in out)
    assert all(
        case["placement_scope"] == "mixed_synthesis"
        for item in out
        for case in item["case_prompts"]
    )


def test_cross_topic_split_sends_unattributed_qids_to_the_later_topic():
    """Advanced placement decides an unattributed qid's topic.

    A task that draws on more than one topic can only be attempted once the
    later topic is reached, so the later topic owns it and the earlier one is
    a prerequisite. This previously followed the most populous topic, which
    could file combined nth-term/sum work under the earlier section.
    """
    inventory = {"items": [
        {"qid": "QINV-0001", "topic_hint": "nth Term", "raw_task": "Find a term."},
        {"qid": "QINV-0002", "topic_hint": "", "raw_task": "Interpret the result."},
        {"qid": "QINV-0003", "topic_hint": "Sum", "raw_task": "Find a sum."},
    ]}
    types = [{
        "type_id": "TYPE-0001",
        "type_title": "Applying a Progression Rule",
        "source_question_ids": ["QINV-0001", "QINV-0002", "QINV-0003"],
        "case_prompts": [{
            "case_title": "Use the relevant rule",
            "examples": [
                {"source_question_id": item["qid"],
                 "example_prompt": item["raw_task"]}
                for item in inventory["items"]
            ],
        }],
    }]

    out = g._split_mined_types_by_source_topic(types, inventory)

    assert len(out) == 2
    qids_by_topic = {
        item["topic_match_hint"]: item["source_question_ids"] for item in out
    }
    assert qids_by_topic == {
        "nth Term": ["QINV-0001"],
        "Sum": ["QINV-0002", "QINV-0003"],
    }
    assert sorted(qid for item in out for qid in item["source_question_ids"]) == [
        "QINV-0001", "QINV-0002", "QINV-0003",
    ]


def test_mathpix_latex_topic_wrappers_share_one_source_topic_key():
    variants = [
        "Sum of First $ n $ Terms",
        r"Sum of First \boldsymbol{n} Terms",
        "Sum of First n Terms",
    ]
    assert len({g._topic_comparison_key(value) for value in variants}) == 1
    assert g._clean_heading_text(
        r"Sum of First $ \boldsymbol{n} $ Terms"
    ) == "Sum of First n Terms"

    sections = [
        {"heading": variants[0], "body": "", "heading_level": 2},
        {"heading": "Solution", "body": "", "heading_level": 2},
    ]
    # Without numbered structure, the fallback keeps "Solution" as its own
    # structural heading (§3 purge) — no vocabulary folds it into the main
    # topic; the semantic outline owns that call in production.
    assert [topic for topic, _ in g._sections_with_source_topics(sections)] == [
        variants[2], "Solution",
    ]

    prose_anchor = {
        "anchor_id": "METHOD-ABCDEF1234",
        "topic_hint": variants[0],
        "required_formulas": [],
        "source_evidence": "Reverse-order addition builds the finite sum rule.",
        "evidence_terms": ["reverse", "order", "addition", "finite", "sum", "rule"],
    }
    assert g._method_anchor_covered([{
        **_row(variants[2], "Reverse-Order Addition"),
        "concept_details": (
            "Description: Reverse the order before addition to build the finite "
            "sum rule."
        ),
    }], prose_anchor)


def test_method_anchor_topic_is_restored_after_topic_restructuring():
    anchor = {
        "anchor_id": "METHOD-ABCDEF1234",
        "topic_hint": "nth Term of an AP",
    }
    row = _row(
        "Introduction",
        "Deriving the General Term",
        evidence=anchor["anchor_id"],
    )
    assert not g._method_anchor_covered([row], anchor)

    out = g._enforce_method_anchor_topics([row], [anchor])

    assert out[0]["topic"] == "nth Term of an AP"
    assert g._method_anchor_covered(out, anchor)


def test_numbered_main_section_chapter_title_exception_is_explicit_in_prompts():
    skeleton = g.prompts.get_text("concepts.skeleton.system")
    restructuring = g.prompts.get_text("concepts.topic_structure.system")

    for prompt in (skeleton, restructuring):
        assert "numbered MAIN section" in prompt
        assert "same title as the chapter" in prompt
        assert "valid topic" in prompt


def test_math_prompts_separate_formula_building_from_problem_inventory():
    skeleton = g.prompts.get_text("concepts.skeleton.system")
    inventory = g.prompts.get_text("concepts.question_task_inventory.system")
    mining = g.prompts.get_text("concepts.type_mining.system")
    embedding = g.prompts.get_text("concepts.type_embedding.system")

    assert "derivations and formula-building sequences" in skeleton.lower()
    assert "independent of the subject label" in skeleton
    assert "worked, numerical, contextual, or real-life problems" in skeleton
    assert "every worked, numerical, contextual, interpretive" in inventory
    assert "but never solutions" in inventory
    assert "Classify every worked, numerical, contextual, interpretive" in mining
    assert "never copy solutions" in mining
    assert "concept the problem actually assesses" in embedding


def test_task_grounded_concept_fragments_consolidate_before_types(monkeypatch):
    topic = "Finite Sequence Totals"
    records = [
        _row(topic, "Deriving the Finite-sum Rule", evidence="reverse and add"),
        _row(topic, "Applying the Rule to Totals", evidence="Example 11"),
        _row(topic, "Finding Unknown Values from Totals", evidence="Example 12"),
        _row(topic, "Advanced Total Applications", evidence="Exercise 5.4"),
    ]
    assert g._question_grounded_fragmentation_topics(records) == {
        g._topic_comparison_key(topic)
    }

    def fake_api(system, user, **_kwargs):
        assert "task varieties belong later as Types/Cases/Examples" in (
            " ".join(system.split()))
        assert "return AT MOST 3 rows" in user
        return {"rows": [
            _api_row(_row(
                topic,
                "Deriving and Interpreting the Finite-sum Rule",
                evidence="reverse and add",
            )),
            _api_row(_row(
                topic,
                "Applying Finite Sums to Direct and Contextual Problems",
                evidence="Examples 11-16; Exercise 5.4",
            )),
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_api)
    out = g._consolidate_task_grounded_fragments_via_api(
        records,
        meta=g._metadata(subject="Any"),
        source_topic_excerpts=[{
            "topic": topic,
            "excerpt": "A source excerpt teaching the finite-sum rule.",
        }],
    )
    assert [record["concept_title"] for record in out] == [
        "Deriving and Interpreting the Finite-sum Rule",
        "Applying Finite Sums to Direct and Contextual Problems",
    ]


def test_overlapping_method_formulae_reduce_public_concept_bound():
    rows = [
        _row(
            "Finite Sums",
            "Reverse-and-add Derivation",
            evidence="METHOD-AAAAAAAAAA",
        ),
        _row(
            "Finite Sums",
            "Equivalent Sum Formula",
            evidence="METHOD-BBBBBBBBBB",
        ),
        _row(
            "Finite Sums",
            "Recovering Terms from Partial Sums",
            evidence="METHOD-CCCCCCCCCC",
        ),
    ]
    anchors = [
        {
            "anchor_id": "METHOD-AAAAAAAAAA",
            "required_formulas": [r"S=\frac{n}{2}[2a+(n-1)d]"],
        },
        {
            "anchor_id": "METHOD-BBBBBBBBBB",
            "required_formulas": [
                r"\begin{aligned}S=\frac{n}{2}[2a+(n-1)d]=12600\end{aligned}",
            ],
        },
        {
            "anchor_id": "METHOD-CCCCCCCCCC",
            "required_formulas": [r"a_n=S_n-S_{n-1}"],
        },
    ]
    assert g._method_formula_family_reduction(rows, anchors) == 1
    groups = g._method_formula_family_groups(rows, anchors)
    assert groups == [["METHOD-AAAAAAAAAA", "METHOD-BBBBBBBBBB"]]
    merged = g._coalesce_method_family_rows(rows, groups)
    assert len(merged) == 2
    assert g._method_anchor_ids(merged[0]) == {
        "METHOD-AAAAAAAAAA", "METHOD-BBBBBBBBBB",
    }
    assert "METHOD-CCCCCCCCCC" in merged[1]["source_evidence"]


def test_representative_mathpix_ocr_edges_keep_topics_and_visual_questions():
    sections = g.parse_mmd_sections(_mathpix_ocr_edge_mmd())

    assert [section["heading"] for section in sections][0] == "Introduction"
    assert g._topic_headings(sections) == [
        "Introduction", "Progressions", "General Term",
    ]
    paired = g._sections_with_source_topics(sections)
    topic_by_heading = {
        section["heading"]: topic for topic, section in paired
    }
    assert topic_by_heading["Solution :"] == "Progressions"
    assert topic_by_heading["Alternative Solution :"] == "Progressions"
    assert topic_by_heading["Remarks:"] == "Progressions"
    assert topic_by_heading["EXERCISE 5.3 (Optional)*"] == "General Term"

    anchors = g._source_task_anchors(sections)
    assert len(anchors) == 2
    assert all(anchor["raw_solution_or_answer"] == "" for anchor in anchors)
    # List items carry the neutral parse-time kind (§3 purge).
    optional = next(
        anchor for anchor in anchors
        if anchor["source_kind"] == "intext_question")
    assert optional["topic_hint"] == "General Term"
    assert optional["requires_visual"] is True
    assert optional["image_urls"] == [
        "https://cdn.mathpix.com/cropped/synthetic-fig-5-1.jpg",
    ]


def test_concept_pipeline_reports_progress_after_skeleton(monkeypatch):
    # This test stubs the WHOLE extraction function, which the Q19 early
    # inventory track legitimately bypasses — pin the sequential path.
    monkeypatch.setenv("AEGIS_SOURCE_CHUNK_WORKERS", "1")
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
        "Error Analysis: Students may subtract consecutive terms in the wrong order."
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
        lambda records, **kwargs: _fake_authored_culminations(records))
    monkeypatch.setattr(
        g, "_repair_records_via_api",
        lambda records, **kwargs: records)
    monkeypatch.setattr(
        g, "_prepare_final_concept_content", _pass_completed_phase3)
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
    # The pre-Phase-3 tail tops out at 0.815 so the rewritten Phase 3's
    # 0.815 opening continues forward instead of jumping the bar back
    # (the 97%-for-hours rebalance); 0.93 is the terminal validation
    # stage and 1.0 the non-release completion default.
    assert {0.58, 0.72, 0.81, 0.812, 0.815, 0.93, 1.0} <= set(progress_values)
    labels = " ".join(label for _, label in values)
    assert "inventorying questions" in labels
    # Type allocation is deferred to the rewritten Phase 3: the pre-final
    # stage narrates topology preparation, never an 85-91% Type assignment.
    assert "preparing final topology before Type allocation" in labels
    assert "topology ready for final Type allocation" in labels
    assert "assigning Types within source topics" not in labels


def test_canonicalization_does_not_compact_to_hit_a_row_count(monkeypatch):
    """The model's judgement stands; arithmetic does not override it.

    Reproduces the live History run: a 44-row skeleton whose canonicalization
    kept 43 rows was compacted to 30 to satisfy topics x 6. Merging on a row
    count rather than on meaning loses teaching content, and no later stage can
    recover a distinction that was already collapsed
    (docs/concept-placement-rules.md, Rule 3).
    """

    topics = [
        "The French Revolution and the Idea of the Nation",
        "The Making of Nationalism in Europe",
        "The Age of Revolutions: 1830-1848",
        "The Making of Germany and Italy",
        "Visualising the Nation",
    ]
    records = [
        _row(topics[index % len(topics)], f"Concept {index:02d}")
        for index in range(44)
    ]
    # The model judges 43 of the 44 distinct — one genuine duplicate merged.
    response = records[:43]

    calls: list[str] = []

    def provider(*_args, **kwargs):
        calls.append(str(kwargs.get("purpose") or ""))
        return {"rows": [_api_row(row) for row in response]}

    monkeypatch.setattr(g, "_openai_json", provider)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda rows, **kwargs: rows)

    out = g._consolidate_concepts_via_api(records, subject="History")

    # One pass only: no second call demanding a smaller row count.
    assert len(calls) == 1, f"a compaction retry ran: {calls}"
    assert len(out) == 43, f"rows were compacted to {len(out)}"
    # There is no advisory guide left to exceed: the volume arithmetic behind
    # it (topics x 6) went with the floor rather than sit in the tree shaping
    # nothing.
    assert not hasattr(g, "_canonicalize_target_bounds")


def test_canonicalization_no_longer_reverts_on_a_row_count(monkeypatch):
    """A collapse that loses no named family is the model's call to make.

    This asserted the opposite until the volume-derived floor was purged: a
    44-row skeleton compacted to 3 used to trip ``max(4, topics x 2, families)``
    and be thrown away for the raw skeleton. These rows carry no parent family
    distinct from their topic, so canonicalization has no source-backed family
    identity to lose here — and the topics themselves are not this pass's job
    to defend: ``_recover_missing_topics_after_human_direction`` runs
    immediately afterwards.

    That successor is narrower than it sounds and the limit is stated here
    rather than implied. It defends only topics carried in
    ``source_topic_excerpts``, only at TOPIC granularity, and it does not
    recover anything by itself — it PAUSES for a human
    (``source_topic_decision.resolve_or_pause``), one of the allowed pre-spend
    pauses. A distinction collapsed *inside* a topic that still has one
    concept is not recovered by it or by anything else, and that is a trade
    made knowingly: the only guard that could fire on it would be a count of
    concepts per topic, which is the arithmetic this purge exists to delete.
    """

    topics = [
        "The French Revolution and the Idea of the Nation",
        "The Making of Nationalism in Europe",
        "The Age of Revolutions: 1830-1848",
        "The Making of Germany and Italy",
        "Visualising the Nation",
    ]
    records = [
        _row(topics[index % len(topics)], f"Concept {index:02d}")
        for index in range(44)
    ]
    collapsed = records[:3]

    calls: list[str] = []

    def provider(*_args, **kwargs):
        calls.append(str(kwargs.get("purpose") or ""))
        return {"rows": [_api_row(row) for row in collapsed]}

    monkeypatch.setattr(g, "_openai_json", provider)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda rows, **kwargs: rows)

    assert g._skeleton_family_labels(records) == []

    out = g._consolidate_concepts_via_api(records, subject="History")

    assert len(calls) == 1, f"a row-count retry ran: {calls}"
    assert len(out) == 3
    assert len(out) != len(records), "the raw skeleton was reinstated"
