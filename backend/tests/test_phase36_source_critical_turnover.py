"""Regression coverage for automatic full-PDF source-critical turnover."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase221_fallback as fallback
from app.services import canonical_source_phase36_source_critical_turnover_contract as phase36
from app.services import progress, uploads


ELECTRICITY_ISSUES = [
    {
        "severity": "error",
        "code": "unresolved_explicit_figure_reference",
        "message": "An explicit source Figure reference does not resolve.",
    },
    {
        "severity": "error",
        "code": "phase2_task_identity_invalid",
        "message": "Canonical tasks do not have unique non-empty source identities.",
    },
    {
        "severity": "error",
        "code": "phase2_task_rich_text_invalid",
        "qid": "QINV-0001",
        "message": "A canonical task display prompt is not valid Aegis rich text.",
    },
    {
        "severity": "error",
        "code": "phase2_unresolved_figure_reference",
        "qid": "QINV-0003",
        "message": "A canonical task contains an unresolved explicit Figure reference.",
    },
    {
        "severity": "error",
        "code": "phase2_task_rich_text_invalid",
        "qid": "QINV-0015",
        "message": "A canonical task display prompt is not valid Aegis rich text.",
    },
    {
        "severity": "error",
        "code": "phase2_required_visual_missing",
        "qid": "QINV-0003",
        "message": "A visual source task has no source-owned canonical image.",
    },
]


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.refreshes = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def refresh(self, _job) -> None:
        self.refreshes += 1


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        id=77,
        filename="Electricity.pdf",
        mmd_text="defective Mathpix MMD",
        question_inventory={"items": [{"qid": "QINV-0001"}]},
        generation_checkpoint={"stage": "pre_type_assignment"},
        status="converted",
        detail="previous detail",
    )


def test_electricity_source_errors_trigger_full_pdf_fallback_at_conversion(tmp_path: Path):
    pdf = tmp_path / "Electricity.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")

    def original(*_args, **_kwargs):
        return {
            "eligible": True,
            "use_fallback": False,
            "reasons": [],
            "page_count": 20,
        }

    result = phase36._assess_quality_with_source_turnover(
        original,
        "substantial healthy-length MMD",
        pdf,
        report={"phase2_issues": ELECTRICITY_ISSUES},
    )

    assert result["use_fallback"] is True
    assert result["source_critical_turnover"] is True
    assert result["source_critical_issue_codes"] == sorted({
        row["code"] for row in ELECTRICITY_ISSUES
    })
    assert any(
        reason.startswith("phase36_source_critical_turnover:")
        for reason in result["reasons"]
    )


def test_bounded_phase22_only_issues_still_use_targeted_adjudication_first(tmp_path: Path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    eligible = [
        {"severity": "error", "code": code, "message": code}
        for code in sorted(phase22.ELIGIBLE_ISSUE_CODES)
    ]

    def original(*_args, **_kwargs):
        return {"eligible": True, "use_fallback": False, "reasons": []}

    result = phase36._assess_quality_with_source_turnover(
        original,
        "healthy MMD",
        pdf,
        report={"phase2_issues": eligible},
    )

    assert result["use_fallback"] is False
    assert "source_critical_turnover" not in result


def test_resume_rebuilds_source_clears_stale_state_and_continues(
    tmp_path: Path,
    monkeypatch,
):
    pdf = tmp_path / "Electricity.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    artifact_dir = tmp_path / "source-shadow"
    artifact_dir.mkdir()
    stale = [
        "source.semantic-graph.json",
        "source.semantic-graph-report.json",
        "source.semantic.mmd",
        "source.phase31-concept-grounding-cache.json",
        "source.phase32-topology-adjudication-cache.json",
        "source.phase33-type-host-plan-cache.json",
        "source.phase34-hierarchy-batch-cache.json",
    ]
    for name in stale:
        (artifact_dir / name).write_text("stale", encoding="utf-8")
    retained_pdf_evidence = artifact_dir / "source.phase3-page-acsd.json"
    retained_pdf_evidence.write_text("verified-page-evidence", encoding="utf-8")

    job = _job()
    db = _FakeDb()
    prepare_calls = 0

    def original_prepare(_db, current):
        nonlocal prepare_calls
        prepare_calls += 1
        if current.mmd_text == "defective Mathpix MMD":
            raise ValueError(
                "Phase 2.2 canonical source adjudication could not verify every "
                "source-critical issue"
            )
        return {"phase2_inventory_ready": True, "replacement": True}

    monkeypatch.setattr(
        phase36,
        "_load_source_state",
        lambda _job: ({}, {"phase2_issues": ELECTRICITY_ISSUES}, ELECTRICITY_ISSUES),
    )
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(
        uploads,
        "source_artifact_directory",
        lambda _job_id: artifact_dir,
    )
    monkeypatch.setattr(progress, "step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(progress, "log", lambda *_args, **_kwargs: None)

    def reconstruct(path, **kwargs):
        assert path == pdf
        assert kwargs["mathpix_mmd"] == "defective Mathpix MMD"
        assert kwargs["fallback_reason"][0].startswith(
            "phase36_source_critical_turnover:"
        )
        return {
            "mmd_text": "verified GPT page ACSD MMD",
            "canonical": {"phase2_inventory_ready": True},
            "reconstruction": {
                "status": "verified",
                "source_origin": fallback.FALLBACK_ORIGIN,
                "pdf_sha256": "a" * 64,
                "page_count": 22,
                "asset_count": 9,
            },
        }

    monkeypatch.setattr(fallback, "reconstruct_pdf_to_acsd", reconstruct)

    result = phase36._prepare_job_context_with_turnover(
        original_prepare,
        db,
        job,
    )

    assert result["replacement"] is True
    assert prepare_calls == 2
    assert job.mmd_text == "verified GPT page ACSD MMD"
    assert job.question_inventory == {}
    assert job.generation_checkpoint == {}
    assert job.status == "converted"
    assert db.commits == 1
    assert db.refreshes == 1
    for name in stale:
        assert not (artifact_dir / name).exists()
    assert retained_pdf_evidence.exists()

    marker = json.loads(
        (artifact_dir / phase36._TURNOVER_FILENAME).read_text(encoding="utf-8")
    )
    assert marker["status"] == "verified"
    assert marker["issue_count"] == len(ELECTRICITY_ISSUES)
    assert marker["cleared_checkpoint_stage"] == "pre_type_assignment"
    assert marker["cleared_question_inventory_items"] == 1
    assert marker["page_count"] == 22
    assert marker["asset_count"] == 9
    assert set(marker["removed_semantic_cache_files"]) == set(stale)


def test_failed_turnover_preserves_existing_job_state(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "Electricity.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    artifact_dir = tmp_path / "source-shadow"
    artifact_dir.mkdir()
    job = _job()
    db = _FakeDb()

    def original_prepare(_db, _job):
        raise ValueError("Phase 2 source gate failed")

    monkeypatch.setattr(
        phase36,
        "_load_source_state",
        lambda _job: ({}, {"phase2_issues": ELECTRICITY_ISSUES}, ELECTRICITY_ISSUES),
    )
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    monkeypatch.setattr(
        uploads,
        "source_artifact_directory",
        lambda _job_id: artifact_dir,
    )
    monkeypatch.setattr(progress, "step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(progress, "log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        fallback,
        "reconstruct_pdf_to_acsd",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("independent page verifier rejected the replacement")
        ),
    )

    with pytest.raises(ValueError, match="Automatic GPT PDF source turnover failed"):
        phase36._prepare_job_context_with_turnover(original_prepare, db, job)

    assert job.mmd_text == "defective Mathpix MMD"
    assert job.question_inventory == {"items": [{"qid": "QINV-0001"}]}
    assert job.generation_checkpoint == {"stage": "pre_type_assignment"}
    assert job.status == "converted"
    assert job.detail == "previous detail"
    assert db.commits == 0
    assert not (artifact_dir / phase36._TURNOVER_FILENAME).exists()


def test_non_pdf_source_does_not_claim_full_pdf_recovery(tmp_path: Path, monkeypatch):
    source = tmp_path / "Electricity.mmd"
    source.write_text("source", encoding="utf-8")
    job = _job()
    job.filename = source.name
    db = _FakeDb()

    def original_prepare(_db, _job):
        raise ValueError("Phase 2 source gate failed")

    monkeypatch.setattr(
        phase36,
        "_load_source_state",
        lambda _job: ({}, {"phase2_issues": ELECTRICITY_ISSUES}, ELECTRICITY_ISSUES),
    )
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: source)
    monkeypatch.setattr(progress, "step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(progress, "log", lambda *_args, **_kwargs: None)

    with pytest.raises(
        ValueError,
        match="automatic full source turnover requires the original PDF",
    ):
        phase36._prepare_job_context_with_turnover(original_prepare, db, job)


def test_cache_purge_keeps_independent_pdf_page_evidence(tmp_path: Path):
    remove = [
        "source.semantic-graph.json",
        "source.phase31-final-topology-cache.json",
        "source.phase34-hierarchy-batch-cache.json",
    ]
    keep = [
        "source.phase3-page-acsd.json",
        "source.gpt-page-acsd.json",
        "source.raw.mmd",
    ]
    for name in [*remove, *keep]:
        (tmp_path / name).write_text(name, encoding="utf-8")

    removed = phase36._purge_source_dependent_semantic_caches(tmp_path)

    assert set(removed) == set(remove)
    assert all(not (tmp_path / name).exists() for name in remove)
    assert all((tmp_path / name).exists() for name in keep)
