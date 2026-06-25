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


def test_concepts_live_processes_every_chunk(monkeypatch):
    """Live concept extraction must call GPT for each chunk and merge results."""
    monkeypatch.setattr(g.config, "use_live_generation", lambda: True)
    monkeypatch.setattr(g, "_MMD_CHUNK_CHARS", 4000)

    calls = {"n": 0}

    def fake_openai_json(system, user, **kw):
        calls["n"] += 1
        n = calls["n"]
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
    # Consolidation is a separate API pass — pass records through in tests.
    monkeypatch.setattr(
        g, "_consolidate_concepts_via_api",
        lambda records, **kw: records)
    doc = _big_doc(20)  # forces several chunks at 4000 chars
    records = g.concepts_from_mmd(doc, subject="Mathematics")
    assert calls["n"] >= 3, "expected multiple chunks to be processed"
    # Every chunk's concepts survive the merge (2 per chunk, all unique).
    titles = {r["concept_title"] for r in records}
    assert len(titles) >= calls["n"] * 2


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
