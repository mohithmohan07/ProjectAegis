"""Offline tests for the public diagnostics export boundary."""
import importlib.util
import json
from pathlib import Path

import pytest


_path = Path(__file__).resolve().parents[1] / "scripts/collect_failure_reports.py"
_spec = importlib.util.spec_from_file_location("collect_failure_reports", _path)
collector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collector)


def _page(*reports, cursor=None):
    return {"schema_version": 1, "exported_at": "2026-09-16T12:00:00+00:00",
            "reports": list(reports), "next_cursor": cursor, "total_count": len(reports)}


def test_pagination_preserves_each_incident_and_marks_success_only_after_last_page(tmp_path):
    a = {"report_id": "a" * 32, "error_type": "ValueError"}
    b = {"report_id": "b" * 32, "error_type": "RuntimeError"}
    calls = []
    def fetch(cursor):
        calls.append(cursor)
        return _page(a, cursor="next") if cursor is None else _page(b)
    output = tmp_path / "out"
    summary = collector.collect(output, fetch=fetch, validate=lambda _: None)
    assert calls == [None, "next"]
    assert summary["report_count"] == 2
    assert summary["collection_status"] == "complete"
    assert json.loads((output / "incidents" / ("a" * 32 + ".json")).read_text()) == a
    assert json.loads((output / "incidents" / ("b" * 32 + ".json")).read_text()) == b


def test_rejected_later_page_cannot_publish_partial_success(tmp_path):
    def fetch(cursor):
        return _page({"report_id": "a" * 32}, cursor="next") if cursor is None else {"source_text": "private"}
    def validate(page):
        if "source_text" in page:
            raise ValueError("Rejected schema")
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="Rejected schema"):
        collector.collect(output, fetch=fetch, validate=validate)
    assert not output.exists()
    assert not list(tmp_path.iterdir())


def test_server_failure_is_not_a_zero_failure_collection(tmp_path):
    def fetch(_):
        raise RuntimeError("unavailable")
    with pytest.raises(RuntimeError):
        collector.collect(tmp_path / "out", fetch=fetch, validate=lambda _: None)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("failure", ["cycle", "changed", "path"])
def test_bad_pagination_or_identity_never_reaches_git_directory(tmp_path, failure):
    counter = 0
    def fetch(cursor):
        nonlocal counter
        counter += 1
        if failure == "cycle":
            return _page({"report_id": "a" * 32}, cursor="repeated")
        if failure == "path":
            return _page({"report_id": "../private"})
        return _page({"report_id": "a" * 32, "error_type": str(counter)}, cursor="next" if cursor is None else None)
    with pytest.raises(ValueError):
        collector.collect(tmp_path / "out", fetch=fetch, validate=lambda _: None)
    assert not (tmp_path / "out").exists()


def test_fetch_failure_does_not_echo_host_stderr(monkeypatch):
    class Failed:
        returncode = 1
        stdout = "sensitive source content"
        stderr = "authorization: Bearer private-token"
    monkeypatch.setattr(collector.subprocess, "run", lambda *a, **kw: Failed())
    with pytest.raises(RuntimeError) as caught:
        collector.fetch_page(None)
    assert "private-token" not in str(caught.value)
    assert "sensitive" not in str(caught.value)


def test_untrusted_cursor_never_reaches_shell(monkeypatch):
    monkeypatch.setattr(collector.subprocess, "run", lambda *a, **kw: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="cursor"):
        collector.fetch_page("$(cat /data/secrets)")


def test_production_validator_rejects_private_text_before_any_files_are_written(tmp_path):
    from app.services.failure_reports_public import project_public_report
    report = project_public_report({
        "report_id": "a" * 32,
        "occurred_at": "2026-09-16T12:00:00+00:00",
        "fingerprint": "b" * 64,
        "error": {"type": "ValueError", "message": "Private teacher source text"},
    })
    valid_output = tmp_path / "valid"
    collector.collect(valid_output, fetch=lambda _: _page(report))
    assert "Private teacher source text" not in next((valid_output / "incidents").iterdir()).read_text()
    report["source_text"] = "Private teacher source text"
    with pytest.raises(ValueError):
        collector.collect(tmp_path / "rejected", fetch=lambda _: _page(report))
    assert not (tmp_path / "rejected").exists()
