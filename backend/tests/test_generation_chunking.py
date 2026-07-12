"""MMD is chunked (never trimmed) so no content is lost on long chapters."""
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


def test_dry_concepts_include_parent_and_culmination():
    records = g.concepts_from_mmd(
        "## Ratios\nEquivalent ratios\nRatio word problems",
        subject="Mathematics",
        board="CBSE",
        grade="07",
        unit="Numbers",
        chapter_title="Ratios",
    )
    assert all("parent_concept" in r for r in records)
    topics = {r["topic"] for r in records}
    for topic in topics:
        assert sum(
            1 for r in records
            if r["topic"] == topic and r["concept_title"].startswith("Culmination -")
        ) == 1


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


def test_canonicalize_retries_when_model_stays_too_granular(monkeypatch):
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"rows": _to_api_rows(_rows(80))}
        assert "TOO GRANULAR" in user
        return {"rows": _to_api_rows(_rows(12))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    out = g._consolidate_concepts_via_api(_rows(80), subject="Math")
    assert calls["n"] == 2
    assert len(out) == 12


def test_skeleton_retries_sparse_chunks(monkeypatch):
    """A big chunk yielding a couple of concepts triggers a density retry."""
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"rows": _to_api_rows(_rows(1))}
        assert "under-extraction" in user
        return {"rows": _to_api_rows(_rows(8))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 2000)  # ~34k chars
    records = g._extract_skeleton_via_api(
        [{"text": chunk_text, "sections": []}], meta=g._metadata(subject="Math"))
    assert calls["n"] == 2
    assert len(records) == 8


def test_skeleton_retries_overdense_chunks(monkeypatch):
    """A chunk yielding dozens of micro-concepts is compacted before merging."""
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"rows": _to_api_rows(_rows(50))}
        assert "too granular" in user
        return {"rows": _to_api_rows(_rows(12))}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    monkeypatch.setattr(g, "_repair_records_via_api", lambda records, **kw: records)
    chunk_text = "SECTION TEXT:\n" + ("alpha beta gamma " * 1500)
    records = g._extract_skeleton_via_api(
        [{
            "text": chunk_text,
            "sections": [
                {"heading": "Main Topic A", "heading_level": 2},
                {"heading": "Main Topic B", "heading_level": 2},
                {"heading": "Main Topic C", "heading_level": 2},
                {"heading": "Main Topic D", "heading_level": 2},
            ],
        }],
        meta=g._metadata(subject="Math"),
    )
    assert calls["n"] == 2
    assert len(records) == 12


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
    assert len(culms) == 2  # authored one for A, deterministic fallback for B
    assert culms[0]["topic"] == "Topic A" and culms[0]["concept_title"] == "Culmination - Topic A Ideas"
    assert culms[1]["topic"] == "Topic B"


def test_inventory_extraction_retries_sparse_chunks(monkeypatch):
    """A chapter-scale chunk yielding a couple of inventory items is retried."""
    calls = {"n": 0}

    def fake_openai(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"items": [{"raw_task": "Q1"}, {"raw_task": "Q2"}]}
        assert "under-extraction" in user
        return {"items": [{"raw_task": f"Q{i}"} for i in range(1, 13)]}

    monkeypatch.setattr(g, "_openai_json", fake_openai)
    # This test isolates density retry behavior; deterministic source anchors
    # have their own coverage regressions.
    monkeypatch.setattr(g, "_source_task_anchors", lambda sections: [])
    body = "1. Solve for x in the equation shown below. " * 400  # ~18k chars
    sections = g.parse_mmd_sections("# Exercises\n\n" + body)
    inventory = g._extract_question_task_inventory_via_api(
        meta=g._metadata(subject="Math"), sections=sections)
    assert calls["n"] == 2
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
        g, "_refine_descriptions_via_api",
        lambda records, **kw: records)
    monkeypatch.setattr(
        g, "_assign_types_via_api",
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
        return {"questions": [
            {"question": f"Unique question {n}?", "sheet_kind": "objective"},
            {"question": "Shared duplicate question?", "sheet_kind": "objective"},
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
