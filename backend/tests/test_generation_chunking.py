"""MMD is chunked (never trimmed) so no content is lost on long chapters."""
import pytest

from app.services import generation as g


def _big_doc(sections: int, body_words: int = 60) -> str:
    out = []
    for i in range(sections):
        out.append(f"# Section {i:02d} Heading\n")
        out.append(("alpha beta gamma delta " * body_words) + "\n\n")
    return "".join(out)


def test_split_preserves_all_content():
    doc = _big_doc(30)
    chunks = g._split_mmd_into_chunks(doc, max_chars=8000)
    assert len(chunks) > 1
    # Concatenation preserves every non-whitespace character (nothing trimmed).
    joined = "".join(chunks)
    assert joined.replace("\n", "").replace(" ", "") == \
        doc.replace("\n", "").replace(" ", "")


def test_split_small_doc_single_chunk():
    assert g._split_mmd_into_chunks("# Only\n\nshort body", max_chars=9000) == \
        ["# Only\n\nshort body"]
    assert g._split_mmd_into_chunks("   ", max_chars=10) == []


def test_dry_concepts_include_parent_and_never_synthesize_culminations():
    records = g.concepts_from_mmd(
        "## Ratios\nEquivalent ratios\nRatio word problems",
        subject="Mathematics",
        board="CBSE",
        grade="07",
        unit="Numbers",
        chapter_title="Ratios",
    )
    assert records
    assert all("parent_concept" in r for r in records)
    # A culmination is model-authored content; the dry path never invents
    # one from code.
    assert not any(
        r["concept_title"].startswith("Culmination -") for r in records
    )


def test_skeleton_pass_strips_types_and_culminations(monkeypatch):
    def fake_openai(system, user, **kw):
        return {"rows": [
            {"topic": "T", "parent_concept": "P", "concept": "C",
             "concept_description": "Description: d // Types: Type 01: X Case 01: y",
             "keywords": "k"},
            {"topic": "T", "parent_concept": "P", "concept": "Culmination - T",
             "concept_description": "Description: Recap", "keywords": "k"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    records = g._extract_skeleton_via_api(
        [{"text": "HEADING PATH: T\nSECTION TEXT:\nBody", "sections": []}],
        meta=g._metadata(subject="Math"),
    )
    assert [r["concept_title"] for r in records] == ["C"]
    assert "Types:" not in records[0]["concept_details"]


def _rows(n: int, topic: str = "T") -> list[dict]:
    return [
        {"topic": topic, "parent_concept": "P", "concept_title": f"Concept {i:02d}",
         "concept_details": f"Description: about concept {i}", "keywords": ""}
        for i in range(1, n + 1)
    ]


def _rows_with_families(
    n: int, family_count: int, topic: str = "T",
) -> list[dict]:
    return [
        {
            "topic": topic,
            "parent_concept": f"Source Family {((i - 1) % family_count) + 1:02d}",
            "concept_title": f"Concept {i:02d}",
            "concept_details": f"Description: about concept {i}",
            "keywords": "",
        }
        for i in range(1, n + 1)
    ]


def _to_api_rows(records: list[dict]) -> list[dict]:
    return [
        {"topic": r["topic"], "parent_concept": r["parent_concept"],
         "concept": r["concept_title"], "concept_description": r["concept_details"],
         "keywords": r["keywords"]}
        for r in records
    ]


def test_canonicalize_falls_back_when_model_over_merges(monkeypatch):
    """A canonicalize response that collapses the chapter must not be accepted."""
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        # Model keeps over-merging on both the first pass and the retry.
        return {"rows": _to_api_rows(_rows(1))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    records = _rows(20)
    out = g._consolidate_concepts_via_api(records, subject="Math")
    assert calls["n"] == 2  # first pass + over-merge retry
    # The full de-duplicated skeleton is kept instead of the collapsed map.
    assert len(out) == 20


def test_canonicalize_retry_recovers_row_count(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"rows": _to_api_rows(_rows(1))}
        assert "over-merging" in user
        return {"rows": _to_api_rows(_rows(6))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    out = g._consolidate_concepts_via_api(_rows(20), subject="Math")
    assert calls["n"] == 2
    assert len(out) == 6


def test_canonicalize_accepts_reasonable_compaction(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        return {"rows": _to_api_rows(_rows(10))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    out = g._consolidate_concepts_via_api(_rows(20), subject="Math")
    assert calls["n"] == 1  # no retry needed
    assert len(out) == 10


def test_canonicalize_bounds_preserve_source_backed_parent_families():
    records = _rows_with_families(18, family_count=9)

    min_keep, max_keep = g._canonicalize_target_bounds(records)

    assert min_keep == 9
    assert max_keep >= min_keep


def test_canonicalize_rejects_compaction_below_parent_family_floor(
    monkeypatch,
):
    calls = {"n": 0}
    records = _rows_with_families(18, family_count=9)

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        assert "MUST-PRESERVE SOURCE-BACKED PARENT FAMILIES" in user
        if calls["n"] == 1:
            return {"rows": _to_api_rows(_rows(6))}
        assert "over-merging" in user
        return {"rows": _to_api_rows(_rows(8))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(
        g, "_repair_records_via_api", lambda records, **kw: records)

    out = g._consolidate_concepts_via_api(records, subject="Science")

    assert calls["n"] == 2
    assert len(out) == 18


def test_canonicalize_accepts_a_granular_map_without_compacting(monkeypatch):
    """A high row count is no longer retried down to a target.

    This asserted the opposite until a live run showed the cost: a 44-row
    skeleton whose canonicalization kept 43 rows was compacted to 30 purely to
    satisfy topics x _CANONICALIZE_MAX_PER_TOPIC. The system prompt already
    asks for a compact teacher-facing map and carries no numeric target, so the
    retry merged on arithmetic rather than on meaning -- and a distinction
    collapsed here cannot be recovered by any later stage.
    """

    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        assert "TOO GRANULAR" not in user
        return {"rows": _to_api_rows(_rows(80))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    out = g._consolidate_concepts_via_api(_rows(80), subject="Math")
    assert calls["n"] == 1
    assert len(out) == 80


def _audit_aware_openai(script):
    """Stub ``_openai_json`` splitting audit calls from extraction calls.

    ``script`` maps call kinds to a list of responses consumed in order:
    ``{"extract": [...], "audit": [...]}`` — an Exception instance in the
    audit list is raised.
    """
    calls = {"extract": [], "audit": []}

    def fake_openai(system, user, **kw):
        if "audit a concept-skeleton extraction" in system:
            calls["audit"].append(user)
            step = script["audit"].pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        calls["extract"].append(user)
        return script["extract"].pop(0)

    return fake_openai, calls


def test_sound_audit_verdict_accepts_the_extraction_without_retry(monkeypatch):
    """(a) A complete/sound audit: exactly one extraction call plus one audit
    call — the model's extraction is the answer; no count-driven retry."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [{"rows": _to_api_rows(_rows(2))}],
        "audit": [{"coverage": "complete", "grain": "sound", "reason": ""}],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 2000)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 1
    assert len(calls["audit"]) == 1
    # However small the extraction, the sound verdict stands (no char floor).
    assert len(records) == 2


def test_under_extracted_audit_verdict_retries_with_named_objectives(
    monkeypatch,
):
    """(b) An under-extracted verdict triggers ONE retry whose user text names
    the missed objectives, and the retry is adopted."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [
            {"rows": _to_api_rows(_rows(1))},
            {"rows": _to_api_rows(_rows(8))},
        ],
        "audit": [{
            "coverage": "under_extracted",
            "missing_objectives": [
                "how gradation reshapes landforms",
                "the chapter-opening framing of slow change",
            ],
            "grain": "sound",
            "reason": "whole objectives are missing",
        }],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 2000)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 2
    assert len(calls["audit"]) == 1
    retry_user = calls["extract"][1]
    assert "how gradation reshapes landforms" in retry_user
    assert "chapter-opening framing of slow change" in retry_user
    assert "COVER every main teaching objective" in retry_user
    assert len(records) == 8


def test_micro_split_audit_verdict_retries_without_a_numeric_target(
    monkeypatch,
):
    """(c) A micro-split verdict triggers ONE consolidation retry that is
    adopted — and the retry prompt names NO numeric concept target."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [
            {"rows": _to_api_rows(_rows_with_families(27, family_count=9))},
            {"rows": _to_api_rows(_rows(9))},
        ],
        "audit": [{
            "coverage": "complete",
            "grain": "micro_split",
            "reason": "terms and cases stand alone as rows",
        }],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 1500)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 2
    assert len(calls["audit"]) == 1
    retry_user = calls["extract"][1]
    assert "micro-concepts" in retry_user
    assert "no target count" in retry_user
    # The must-preserve families are named, but never a number of concepts.
    assert "MUST-PRESERVE SOURCE-BACKED PARENT FAMILIES" in retry_user
    import re as _re
    assert not _re.search(
        r"(?i)\breturn\s+(?:between\s+)?\d+", retry_user)
    assert len(records) == 9


def test_unavailable_audit_keeps_the_extraction_unchanged(monkeypatch):
    """(d) An audit exception is advisory-only: the extraction is kept exactly
    as returned and no retry is spent."""
    fake_openai, calls = _audit_aware_openai({
        "extract": [{"rows": _to_api_rows(_rows(3))}],
        "audit": [RuntimeError("audit transport failure")],
    })
    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 1500)
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert len(calls["extract"]) == 1
    assert len(calls["audit"]) == 1
    assert len(records) == 3


def test_culmination_pass_cannot_drop_normal_rows(monkeypatch):
    """The culmination model authors ONLY culmination rows; normal rows are
    merged back programmatically, so a bad response can't lose chapter content."""

    def fake_openai(system, user, **kw):
        # Model misbehaves: returns one culmination for topic A only, plus a
        # rewritten fragment of the map (which must be ignored).
        return {"rows": [
            {"topic": "Topic A", "parent_concept": "Culmination",
             "concept": "Culmination - Topic A Ideas",
             "concept_description": "Description: Recap", "keywords": ""},
            {"topic": "Topic A", "parent_concept": "P", "concept": "Concept 01",
             "concept_description": "Description: rewritten fragment", "keywords": ""},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    records = _rows(4, topic="Topic A") + _rows(3, topic="Topic B")
    out = g._build_culminations_via_api(records, meta=g._metadata(subject="Math"))
    normal = [r for r in out if not r["concept_title"].startswith("Culmination -")]
    culms = [r for r in out if r["concept_title"].startswith("Culmination -")]
    assert len(normal) == 7  # every normal row survives
    assert {r["concept_details"] for r in normal if r["topic"] == "Topic A"} == {
        f"Description: about concept {i}" for i in range(1, 5)
    }  # the rewritten fragment was ignored
    # Only the authored culmination ships; Topic B (which the model left
    # without one) ships without a culmination — nothing is synthesized —
    # and the validator report flags it for review.
    assert len(culms) == 1
    assert culms[0]["topic"] == "Topic A"
    # The authored title survives; it is never rebuilt from code.
    assert culms[0]["concept_title"] == "Culmination - Topic A Ideas"


def test_inventory_extraction_retries_underextracted_chunks(monkeypatch):
    """The completeness reviewer, not a count formula, triggers the retry."""
    calls = {"extract": 0, "review": 0}

    def fake_openai(system, user, **kw):
        if "inventory-completeness reviewer" in system:
            calls["review"] += 1
            if calls["review"] == 1:
                return {
                    "verdict": "under_extracted",
                    "reason": "the numbered exercise list was summarized",
                    "missed": ["numbered exercise items"],
                }
            return {"verdict": "complete", "reason": ""}
        calls["extract"] += 1
        if calls["extract"] == 1:
            return {"items": [
                {
                    "raw_task": (
                        f"Solve equation problem number {i} with every "
                        "supplied condition."
                    ),
                }
                for i in range(1, 3)
            ]}
        assert "under-extraction" in user
        assert "numbered exercise items" in user
        return {"items": [
            {
                "raw_task": (
                    f"Solve equation problem number {i} with every supplied "
                    "condition."
                ),
            }
            for i in range(1, 13)
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    # This test isolates the retry behavior; deterministic source anchors
    # have their own coverage regressions.
    monkeypatch.setattr(g, "_source_task_anchors", lambda sections: [])
    body = "1. Solve for x in the equation shown below. " * 400  # ~18k chars
    sections = g.parse_mmd_sections("# Exercises\n\n" + body)
    inventory = g._extract_question_task_inventory_via_api(
        meta=g._metadata(subject="Math"), sections=sections)
    assert calls["extract"] == 2
    assert calls["review"] == 2
    assert len(inventory["items"]) == 12
    assert inventory["items"][0]["qid"] == "QINV-0001"


def test_latex_headings_are_normalized_to_markdown():
    """Real Mathpix PDF output uses \\section*{...}, not Markdown '#'."""
    mmd = (
        "\\section*{NUMBER SYSTEMS}\n\nintro text\n\n"
        "\\section*{1．1 Introduction}\n\nbody one\n\n"
        "\\subsection*{1.2 Irrational Numbers}\n\nbody two\n\n"
        "\\section*{EXERCISE 1.1}\n\n1. Is zero rational?\n"
    )
    out = g.normalize_mmd_headings(mmd)
    assert "## NUMBER SYSTEMS" in out
    assert "## 1．1 Introduction" in out
    assert "### 1.2 Irrational Numbers" in out
    assert "\\section" not in out
    # Idempotent for already-Markdown documents.
    md = "# Title\n\n## Section\n\nbody\n"
    assert g.normalize_mmd_headings(md) == md


def test_parse_mmd_sections_handles_latex_headings():
    mmd = (
        "\\section*{NUMBER SYSTEMS}\n\n"
        "\\section*{1．1 Introduction}\n\nbody one\n\n"
        "\\subsection*{1.2 Irrational Numbers}\n\nbody two\n"
    )
    sections = g.parse_mmd_sections(mmd)
    headings = [s["heading"] for s in sections]
    # Fullwidth "1．1" section number is stripped like a normal one.
    assert "Introduction" in headings
    assert "Irrational Numbers" in headings
    assert len(sections) >= 3


def test_section_aware_chunks_split_headingless_documents():
    """A document with no (parseable) headings must still be chunked, not sent
    as one giant chunk that causes chapter-scale under-extraction."""
    body = ("A paragraph about numbers and operations. " * 40 + "\n\n") * 60
    chunks = g._section_aware_chunks(body, max_chars=20_000)
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 26_000 for c in chunks)
    # No content is lost across the split.
    joined = "".join(s["body"] for c in chunks for s in c["sections"])
    assert joined.replace("\n", "").replace(" ", "") == \
        body.replace("\n", "").replace(" ", "")


def test_description_prefix_is_normalized_deterministically():
    f = g._normalize_description_prefix
    assert f("Description: fine as is") == "Description: fine as is"
    assert f("description: lowercase") == "Description: lowercase"
    assert f("Description： fullwidth colon") == "Description: fullwidth colon"
    assert f("  Description :  spaced colon") == "Description: spaced colon"
    assert f("Plain text with no prefix") == "Description: Plain text with no prefix"
    assert f("") == ""
    rows = g._concept_rows_to_records({"rows": [{
        "topic": "T", "concept": "C", "concept_description": "no prefix here",
    }]})
    assert rows[0]["concept_details"] == "Description: no prefix here"


def test_duplicate_concepts_are_merged_by_topic_and_title():
    records = [
        {"topic": "T", "parent_concept": "P", "concept_title": "Same",
         "concept_details": "Description: first", "keywords": ""},
        {"topic": "T", "parent_concept": "P", "concept_title": "Same",
         "concept_details": "Description: second", "keywords": ""},
    ]
    assert len(g._merge_concept_records(records)) == 1


def test_concepts_live_processes_every_chunk(monkeypatch):
    """Live concept extraction must call GPT for each chunk and merge results."""
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(g, "_MMD_CHUNK_CHARS", 4000)

    calls = {"n": 0, "skeleton": 0}

    def fake_openai_json(system, user, **kw):
        if "inventory-completeness reviewer" in system:
            return {"verdict": "complete", "reason": ""}
        if "audit a concept-skeleton extraction" in system:
            return {"coverage": "complete", "grain": "sound", "reason": ""}
        if "mined assessment-Type taxonomy" in system:
            return {"verdict": "healthy", "rationale": "reusable methods"}
        if "another auditor's verdict" in system:
            return {"verdict": "confirmed", "rationale": ""}
        calls["n"] += 1
        if "clean teachable concept skeleton" in system:
            calls["skeleton"] += 1
        n = calls["skeleton"] or calls["n"]
        # Each chunk yields two unique concepts.
        return {"rows": [
            {"topic": "Topic A", "concept": f"Concept {n}a",
             "concept_description": "Description: x // Types: // Misconception:",
             "keywords": "k"},
            {"topic": "Topic A", "concept": f"Concept {n}b",
             "concept_description": "Description: y // Types: // Misconception:",
             "keywords": "k"},
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai_json)
    monkeypatch.setattr(
        g, "_consolidate_concepts_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_topic_segregation_verdict_via_api",
        lambda records, **kw: {"restructure": False, "reason": ""})

    # Everything after the 81% boundary runs through the rewritten Phase 3;
    # this test is about chunked extraction, so pass its output through.
    monkeypatch.setattr(
        g, "_prepare_final_concept_content",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_build_culminations_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_repair_records_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_validate_final_or_raise",
        lambda records, **kw: {"ok": True, "errors": [], "summary": {}})
    # This test's fake model deliberately emits one invented topic ("Topic A")
    # for every chunk. Source-topic coverage is validated in dedicated tests.
    monkeypatch.setattr(
        g, "_recover_missing_topic_concepts_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_missing_source_topic_excerpts",
        lambda records, source_topic_excerpts: [])
    doc = _big_doc(20)  # forces several chunks at 4000 chars
    records = g.concepts_from_mmd(doc, subject="Mathematics")
    assert calls["skeleton"] >= 3, "expected multiple chunks to be processed"
    # Every chunk's concepts survive the merge (2 per chunk, all unique).
    titles = {r["concept_title"] for r in records}
    assert len(titles) >= calls["skeleton"] * 2


def test_identify_questions_live_merges_and_dedupes(monkeypatch):
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(g, "_MMD_CHUNK_CHARS", 4000)

    calls = {"n": 0}

    def fake_openai_json(system, user, **kw):
        calls["n"] += 1
        n = calls["n"]
        def row(question):
            return {
                "question": question,
                "sheet_kind": "objective",
                "question_category": "Multiple Choice Question",
                "cognitive_skills": "Understand",
                "level_of_difficulty": "Moderate",
                "marks": 1,
                "question_duration": 2,
                "math_keyboard": "",
            }

        return {"questions": [
            row(f"Unique question {n}?"),
            row("Shared duplicate question?"),
        ]}

    monkeypatch.setattr(g, "_openai_json", fake_openai_json)
    doc = _big_doc(20)
    records = g._live_identify_questions_from_mmd(
        doc, upload_type="questions", question_type="auto")
    assert calls["n"] >= 3
    questions = [r["question"] for r in records]
    # The duplicate that appears in every chunk is kept exactly once.
    assert questions.count("Shared duplicate question?") == 1
    # Every chunk's unique question is present (no trimming/cap loss).
    assert sum(1 for q in questions if q.startswith("Unique question")) == calls["n"]


def test_identify_questions_safety_cap_fails_closed(monkeypatch):
    monkeypatch.setattr(g, "_IDENTIFY_SAFETY_CAP", 2)
    monkeypatch.setattr(g, "_split_mmd_into_chunks", lambda _text: ["section"])

    def row(question):
        return {
            "question": question,
            "sheet_kind": "objective",
            "question_category": "Multiple Choice Question",
            "cognitive_skills": "Understand",
            "level_of_difficulty": "Moderate",
            "marks": 1,
            "question_duration": 2,
            "math_keyboard": "",
        }

    monkeypatch.setattr(
        g,
        "_openai_json",
        lambda *_args, **_kwargs: {
            "questions": [row("Question one?"), row("Question two?")]
        },
    )
    with pytest.raises(RuntimeError, match="refusing a truncated success"):
        g._live_identify_questions_from_mmd(
            "source", upload_type="questions", question_type="auto")
