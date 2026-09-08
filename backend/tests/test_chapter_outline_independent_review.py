"""Complete evidence, advisory review, and honest outline cache provenance."""
from __future__ import annotations
import copy
import json
import pytest
from app.services import canonical_source_phase221_fallback as fallback
from tests.test_chapter_outline import _candidate, _page_acsd


def _review(dissent=False):
    return {"verdict": "dissent" if dissent else "verified", "issues": [{
        "code": "dependent_subparts", "field": "task_partitions[0]",
        "message": "The two proposed parts may share a source context.",
        "evidence_refs": [{"page_id": "PDF-PAGE-0002", "reading_order": 2}],
    }] if dissent else []}


def _provider(monkeypatch, tmp_path, review=None):
    calls = []
    def respond(**kwargs):
        calls.append(kwargs)
        if kwargs["response_schema"]["name"] == "aegis_chapter_outline_review":
            if isinstance(review, Exception):
                raise review
            return copy.deepcopy(_review() if review is None else review)
        return _candidate()
    monkeypatch.setattr(fallback.phase22, "_openai_multimodal_json", respond)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    return calls


def test_review_receives_full_evidence_and_applied_decision(monkeypatch, tmp_path):
    calls = _provider(monkeypatch, tmp_path)
    source = _page_acsd()
    source["pages"][0]["blocks"].append({
        "reading_order": 5, "kind": "paragraph", "text": "full passage " * 9000,
    })
    before = copy.deepcopy(source)
    result = fallback.derive_chapter_outline(source)
    assert len(calls) == 2
    assert calls[0]["system"] != calls[1]["system"]
    payload = json.loads(calls[1]["prompt"])
    assert payload["source_pages"] == source["pages"]
    assert payload["outline_to_apply"]["task_partitions"] == result["task_partitions"]
    assert payload["author_attempts"][0]["response"] == _candidate()
    assert result["review_provenance"]["status"] == "verified"
    assert fallback._outline_review_is_current(source, result)
    assert source == before


def test_digest_keeps_late_prose_questions_tables_math_and_figure_context():
    source = _page_acsd()
    source["pages"][0]["blocks"].extend([
        {"reading_order": 5, "kind": "paragraph", "text": "prose " * 20000 + "PROSE_END"},
        {"reading_order": 6, "kind": "task", "text": "question " * 2000 + "TASK_END"},
        {"reading_order": 7, "kind": "table", "table_rows": [[str(i)] for i in range(100)] + [["TABLE_END"]]},
        {"reading_order": 8, "kind": "math", "latex": r"E = mc^2"},
        {"reading_order": 9, "kind": "figure", "caption": "caption " * 100 + "FIGURE_END", "related_task_refs": [6]},
    ])
    digest = fallback._outline_digest(source)
    for sentinel in ("PROSE_END", "TASK_END", "TABLE_END", "E = mc^2", "FIGURE_END", "related_task_refs", "Exercise 1"):
        assert sentinel in digest


def test_dissent_is_visible_and_cached_without_changing_decision(monkeypatch, tmp_path):
    calls = _provider(monkeypatch, tmp_path, _review(True))
    source = _page_acsd()
    expected, _ = fallback._normalize_chapter_outline(source, _candidate())
    first = fallback.derive_chapter_outline(source)
    second = fallback.derive_chapter_outline(source)
    assert len(calls) == 2
    assert first == second
    assert first["task_partitions"] == expected["task_partitions"]
    assert first["topics"] == expected["topics"]
    assert first["review_provenance"]["status"] == "dissent"
    assert "PDF-PAGE-0002:2" in first["review_flags"][-1]


@pytest.mark.parametrize("review", [
    RuntimeError("provider unavailable"),
    {"verdict": "verified", "issues": [{"message": "wrong schema"}]},
    {"verdict": "dissent", "issues": [{
        "code": "bad_ref", "field": "topics", "message": "Invalid pointer",
        "evidence_refs": [{"page_id": "PDF-PAGE-9999", "reading_order": 1}],
    }]},
])
def test_failed_review_keeps_content_flagged_and_unverified(monkeypatch, tmp_path, review):
    _provider(monkeypatch, tmp_path, review)
    source = _page_acsd()
    result = fallback.derive_chapter_outline(source)
    assert result["topics"] and result["task_partitions"] and result["review_flags"]
    assert result["review_provenance"]["status"] in {"unavailable", "invalid_response"}
    assert not fallback._outline_review_is_current(source, result)
    assert fallback._read_verified_batch_cache(fallback._outline_cache_key(source["pdf_sha256"])) is None


def test_same_pdf_changed_evidence_cannot_reuse_review(monkeypatch, tmp_path):
    calls = _provider(monkeypatch, tmp_path)
    source = _page_acsd()
    first = fallback.derive_chapter_outline(source)
    source["pages"][0]["blocks"][3]["text"] += " Additional teaching."
    second = fallback.derive_chapter_outline(source)
    assert len(calls) == 4
    assert first["review_provenance"]["evidence_sha256"] != second["review_provenance"]["evidence_sha256"]


def test_author_only_cache_is_not_promoted_to_reviewed(monkeypatch, tmp_path):
    calls = _provider(monkeypatch, tmp_path)
    source = _page_acsd()
    old, _ = fallback._normalize_chapter_outline(source, _candidate())
    fallback._write_verified_batch_cache(fallback._outline_cache_key(source["pdf_sha256"]), {
        "status": "verified", "model": fallback.config.OPENAI_MODEL,
        "pdf_sha256": source["pdf_sha256"], "result": old,
    })
    result = fallback.derive_chapter_outline(source)
    assert len(calls) == 2
    assert result["review_provenance"]["status"] == "verified"


def test_changed_decision_or_prompt_invalidates_review(monkeypatch, tmp_path):
    _provider(monkeypatch, tmp_path)
    source = _page_acsd()
    result = fallback.derive_chapter_outline(source)
    changed = copy.deepcopy(result)
    changed["topics"][0]["title"] = "Different meaning"
    assert not fallback._outline_review_is_current(source, changed)
    prior_key = fallback._outline_cache_key(source["pdf_sha256"])
    original = fallback._outline_review_system_prompt
    monkeypatch.setattr(fallback, "_outline_review_system_prompt", lambda: original() + " New review policy.")
    assert not fallback._outline_review_is_current(source, result)
    assert prior_key != fallback._outline_cache_key(source["pdf_sha256"])
