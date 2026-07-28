"""Regression coverage for Phase 2.2.1 and GPT PDF-to-ACSD fallback."""
from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from app.services import canonical_source_phase221_fallback as fallback


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 70), "1 Number Patterns", fontsize=18)
    page.insert_text((50, 130), "A sequence follows a visible rule.", fontsize=12)
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 70), "Activity", fontsize=15)
    page.insert_text((50, 120), "Describe the pattern shown in Fig. 1.", fontsize=12)
    page.draw_rect(fitz.Rect(60, 190, 420, 450), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    page.insert_text((90, 260), "1, 3, 5, 7", fontsize=24)
    page.insert_text((50, 500), "Fig. 1 - Odd-number pattern.", fontsize=11)
    doc.save(path)
    doc.close()


def _verified_provider(pages: list[fallback.PdfPage]) -> dict:
    rows = []
    for page in pages:
        if page.page_number == 1:
            blocks = [
                {
                    "reading_order": 1,
                    "kind": "heading",
                    "bbox": [70, 50, 720, 120],
                    "text": "1 Number Patterns",
                    "heading_level": 1,
                    "source_label": "",
                    "latex": "",
                    "table_rows": [],
                    "linked_visual_orders": [],
                    "linked_context_orders": [],
                    "caption": "",
                    "confidence": 0.999,
                },
                {
                    "reading_order": 2,
                    "kind": "paragraph",
                    "bbox": [70, 130, 800, 210],
                    "text": "A sequence follows a visible rule.",
                    "heading_level": 0,
                    "source_label": "",
                    "latex": "",
                    "table_rows": [],
                    "linked_visual_orders": [],
                    "linked_context_orders": [],
                    "caption": "",
                    "confidence": 0.999,
                },
            ]
        else:
            blocks = [
                {
                    "reading_order": 1,
                    "kind": "task",
                    "bbox": [70, 60, 850, 180],
                    "text": "Describe the pattern shown in Fig. 1.",
                    "heading_level": 0,
                    "source_label": "Activity",
                    "latex": "",
                    "table_rows": [],
                    "linked_visual_orders": [2],
                    "linked_context_orders": [],
                    "caption": "",
                    "confidence": 0.999,
                },
                {
                    "reading_order": 2,
                    "kind": "figure",
                    "bbox": [90, 220, 700, 590],
                    "text": "",
                    "heading_level": 0,
                    "source_label": "",
                    "latex": "",
                    "table_rows": [],
                    "linked_visual_orders": [],
                    "linked_context_orders": [],
                    "caption": "Fig. 1 - Odd-number pattern.",
                    "confidence": 0.999,
                },
            ]
        rows.append({
            "page_id": page.page_id,
            "page_number": page.page_number,
            "confidence": 0.999,
            "blocks": blocks,
        })
    return {
        "status": "verified",
        "pages": rows,
        "verification": {
            "verdict": "verified",
            "approved_page_ids": [page.page_id for page in pages],
            "rejected_page_ids": [],
            "confidence": 0.999,
            "issues": [],
        },
    }


def test_reconstruct_pdf_to_acsd_materializes_verified_source(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=42,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        provider=_verified_provider,
    )

    reconstruction = result["reconstruction"]
    assert reconstruction["status"] == "verified"
    assert reconstruction["page_count"] == 2
    assert reconstruction["asset_count"] == 1
    assert reconstruction["verified_task_visual_relationships"] == 1
    assert result["canonical"]["source_contract"]["source_origin"] == (
        fallback.FALLBACK_ORIGIN
    )
    assert "# Activity" in result["mmd_text"]
    assert "https://aegis.example/source-assets/42/" in result["mmd_text"]
    assert len(result["canonical"]["tasks"]) == 1
    task = result["canonical"]["tasks"][0]
    assert task["qid"] == "QINV-0001"
    assert len(task["figure_refs"]) == 1
    assert task["display_prompt"].count("[img ") == 1
    assert (artifact_dir / "source.raw.mmd").read_text(encoding="utf-8") == result[
        "mmd_text"
    ]
    assert (artifact_dir / fallback.GPT_PAGE_ACSD_FILENAME).exists()
    assets = list((artifact_dir / fallback.ASSET_DIRNAME).glob("*.jpg"))
    assert len(assets) == 1
    report = json.loads(
        (artifact_dir / "source.source-report.json").read_text(encoding="utf-8")
    )
    assert report["source_reconstruction"]["source_origin"] == fallback.FALLBACK_ORIGIN


def test_quality_gate_keeps_bounded_phase22_source_for_adjudication(tmp_path: Path):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    mmd = "A substantial source paragraph. " * 500
    report = {
        "phase2_issues": [
            {"code": "phase21_missing_numbered_parent_section"},
            {"code": "phase21_numbered_section_gap"},
            {"code": "phase21_orphan_task_figure"},
        ]
    }

    quality = fallback.assess_mathpix_quality(mmd, pdf, report=report)

    assert quality["use_fallback"] is False
    assert quality["reasons"] == []


def test_quality_gate_falls_back_for_empty_or_hard_failed_pdf(tmp_path: Path):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)

    empty = fallback.assess_mathpix_quality("", pdf)
    failed = fallback.assess_mathpix_quality(
        "", pdf, hard_failure="Mathpix timeout"
    )

    assert empty["use_fallback"] is True
    assert "mmd_too_short_for_pdf" in empty["reasons"]
    assert failed["use_fallback"] is True
    assert any(reason.startswith("mathpix_hard_failure:") for reason in failed["reasons"])


def test_quality_gate_falls_back_when_mathpix_misses_most_pdf_text(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(
        fallback, "_pdf_text_coverage", lambda _path, _mmd: (1200, 0.18)
    )
    substantial_but_wrong = "Unrelated conversion content. " * 500

    quality = fallback.assess_mathpix_quality(substantial_but_wrong, pdf)

    assert quality["use_fallback"] is True
    assert "pdf_text_coverage_too_low:0.180" in quality["reasons"]
    assert quality["pdf_text_tokens"] == 1200


def test_source_asset_signature_is_unforgeable(monkeypatch):
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "unit-test-secret")
    filename = "a" * 64 + ".jpg"
    signature = fallback.asset_signature(17, filename)

    assert fallback.validate_asset_signature(17, filename, signature) is True
    assert fallback.validate_asset_signature(18, filename, signature) is False
    assert fallback.validate_asset_signature(17, "../asset.jpg", signature) is False


def test_review_required_batch_is_not_cached(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(fallback, "_CACHE_DIR", cache_dir)
    calls = 0

    def provider(_pages):
        nonlocal calls
        calls += 1
        return {"status": "review_required", "reason": "unreadable"}

    for _ in range(2):
        with pytest.raises(ValueError, match="requires review"):
            fallback.extract_pdf_to_page_acsd(pdf, provider=provider)

    assert calls == 2
    assert list(cache_dir.glob("*.json")) == []


def test_verified_page_batches_are_cached(tmp_path: Path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(fallback, "_CACHE_DIR", cache_dir)
    calls = 0

    def provider(pages):
        nonlocal calls
        calls += 1
        return _verified_provider(pages)

    first = fallback.extract_pdf_to_page_acsd(pdf, provider=provider)
    second = fallback.extract_pdf_to_page_acsd(pdf, provider=provider)

    assert first["pages"] == second["pages"]
    assert calls == 1
    assert len(list(cache_dir.glob("*.json"))) == 1


def test_build_concepts_conversion_uses_fallback_after_mathpix_hard_failure(
    client,
    tmp_path: Path,
    monkeypatch,
):
    from app import config
    from app.services import mmd
    from tests.conftest import convert_concept_upload

    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "integration-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "use_live_generation", lambda: True)

    def failed_mathpix(_path):
        raise mmd.ConversionError("synthetic Mathpix outage")

    monkeypatch.setattr(mmd, "to_mmd", failed_mathpix)
    real_reconstruct = fallback.reconstruct_pdf_to_acsd

    def verified_reconstruct(path, **kwargs):
        return real_reconstruct(path, provider=_verified_provider, **kwargs)

    monkeypatch.setattr(fallback, "reconstruct_pdf_to_acsd", verified_reconstruct)

    job = client.post(
        "/build-concepts/post-learning/uploads",
        files={"file": ("source.pdf", pdf.read_bytes(), "application/pdf")},
    ).json()
    converted = convert_concept_upload(client, job["id"])

    assert converted["conversion_source"] == fallback.FALLBACK_ORIGIN
    reconstruction = converted["source_artifacts"]["source_reconstruction"]
    assert reconstruction["status"] == "verified"
    assert reconstruction["page_count"] == 2
    kinds = {item["kind"] for item in converted["source_artifacts"]["files"]}
    assert "gpt_page_acsd" in kinds
    page_acsd = client.get(
        f"/source-artifacts/uploads/{job['id']}/gpt_page_acsd"
    )
    assert page_acsd.status_code == 200
    assert page_acsd.json()["source_origin"] == fallback.FALLBACK_ORIGIN
    canonical = client.get(
        f"/source-artifacts/uploads/{job['id']}/canonical_json"
    ).json()
    image_url = canonical["images"][0]["url"]
    # Replace the configured external origin with the local TestClient path.
    local_path = image_url.removeprefix("https://aegis.example")
    asset = client.get(local_path)
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("image/jpeg")
    assert asset.headers["content-disposition"].startswith("inline;")



def test_failed_gpt_fallback_persists_billable_openai_usage(
    client,
    tmp_path: Path,
    monkeypatch,
):
    from app import config
    from app.services import mmd, openai_usage
    from tests.conftest import stream_error_message

    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        mmd,
        "to_mmd",
        lambda _path: (_ for _ in ()).throw(
            mmd.ConversionError("synthetic Mathpix outage")
        ),
    )

    def billed_failure(_path, **_kwargs):
        openai_usage.record_response({
            "model": "gpt-5.6-luna",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            },
        }, requested_model="gpt-5.6-luna")
        raise ValueError("synthetic verified-source rejection")

    monkeypatch.setattr(fallback, "reconstruct_pdf_to_acsd", billed_failure)
    job = client.post(
        "/build-concepts/post-learning/uploads",
        files={"file": ("source.pdf", pdf.read_bytes(), "application/pdf")},
    ).json()

    response = client.post(f"/build-concepts/uploads/{job['id']}/convert")

    assert "GPT PDF-to-ACSD fallback also failed" in (stream_error_message(response) or "")
    reloaded = client.get(f"/build-concepts/uploads/{job['id']}").json()
    assert reloaded["openai_usage"]["request_count"] == 1
    assert reloaded["openai_usage"]["total_tokens"] == 150


def test_unusable_mathpix_and_failed_fallback_persist_a_blocking_source_issue(
    client,
    tmp_path: Path,
    monkeypatch,
):
    from app import config
    from app.services import mmd
    from tests.conftest import stream_error_message

    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    monkeypatch.setattr(
        mmd,
        "to_mmd",
        lambda _path: "# Number Patterns\n\nA substantial but corrupted source.\n" * 80,
    )
    monkeypatch.setattr(
        fallback,
        "assess_mathpix_quality",
        lambda *_args, **_kwargs: {
            "eligible": True,
            "use_fallback": True,
            "reasons": ["pdf_text_coverage_too_low:0.120"],
        },
    )
    monkeypatch.setattr(
        fallback,
        "reconstruct_pdf_to_acsd",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("verification remained ambiguous")
        ),
    )
    job = client.post(
        "/build-concepts/post-learning/uploads",
        files={"file": ("source.pdf", pdf.read_bytes(), "application/pdf")},
    ).json()

    response = client.post(f"/build-concepts/uploads/{job['id']}/convert")

    assert "source-quality gate" in (stream_error_message(response) or "")
    report = client.get(
        f"/source-artifacts/uploads/{job['id']}/report"
    ).json()
    assert report["phase2_inventory_ready"] is False
    assert report["source_reconstruction"]["status"] == "review_required"
    assert any(
        issue.get("code") == "phase221_gpt_pdf_acsd_fallback_failed"
        for issue in report["issues"]
    )


def test_renderer_preserves_figure_before_task_order_and_restores_ownership(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        for page in result["pages"]:
            if page["page_number"] != 2:
                continue
            figure, task = page["blocks"][1], page["blocks"][0]
            figure["reading_order"] = 1
            task["reading_order"] = 2
            task["linked_visual_orders"] = [1]
            page["blocks"] = [figure, task]
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=43,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        provider=provider,
    )

    mmd = result["mmd_text"]
    assert mmd.index("![Fig. 1 - Odd-number pattern.") < mmd.index(
        "Describe the pattern shown in Fig. 1."
    )
    task = result["canonical"]["tasks"][0]
    assert len(task["figure_refs"]) == 1
    assert task["display_prompt"].count("[img ") == 1


def test_signed_source_asset_response_is_inline_not_attachment(
    tmp_path: Path, monkeypatch,
):
    from app.api import source_assets
    from app.services import uploads

    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "unit-test-secret")
    monkeypatch.setattr(
        uploads,
        "source_artifact_directory",
        lambda _job_id: tmp_path / "source-shadow",
    )
    filename = "b" * 64 + ".jpg"
    path = fallback.source_asset_path(19, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpeg")
    sig = fallback.asset_signature(19, filename)

    response = source_assets.source_asset(19, filename, sig)

    assert response.media_type == "image/jpeg"
    assert "attachment" not in response.headers.get("content-disposition", "")
    assert response.headers["cache-control"].endswith("immutable")


def test_unlinked_decorative_figure_is_not_inherited_by_nearby_task(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        for page in result["pages"]:
            if page["page_number"] != 2:
                continue
            task = page["blocks"][0]
            task["text"] = "Explain the visible odd-number rule."
            task["linked_visual_orders"] = []
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=44,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        provider=provider,
    )

    task = result["canonical"]["tasks"][0]
    assert task["figure_refs"] == []
    assert task["image_urls"] == []
    assert "[img " not in task["display_prompt"]


def test_failed_fallback_does_not_replace_existing_source_bundle(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "source-shadow"
    artifact_dir.mkdir()
    existing = artifact_dir / "source.raw.mmd"
    existing.write_text("existing-source", encoding="utf-8")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    with pytest.raises(ValueError, match="requires review"):
        fallback.reconstruct_pdf_to_acsd(
            pdf,
            job_id=45,
            artifact_dir=artifact_dir,
            fallback_reason=["mathpix_hard_failure:test"],
            provider=lambda _pages: {
                "status": "review_required",
                "reason": "uncertain page",
            },
        )

    assert existing.read_text(encoding="utf-8") == "existing-source"
    assert not list(artifact_dir.glob(".phase221-staging-*"))


def test_successful_hard_failure_fallback_removes_stale_partial_mathpix_artifact(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "source-shadow"
    artifact_dir.mkdir()
    stale = artifact_dir / fallback.MATHPIX_RAW_FILENAME
    stale.write_text("stale partial mmd", encoding="utf-8")
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=46,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        mathpix_mmd="",
        provider=_verified_provider,
    )

    assert not stale.exists()


def test_required_table_context_is_linked_without_moving_source_blocks(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        for page in result["pages"]:
            if page["page_number"] != 2:
                continue
            task, figure = page["blocks"]
            table = {
                "reading_order": 1,
                "kind": "table",
                "bbox": [80, 170, 760, 300],
                "text": "",
                "heading_level": 0,
                "source_label": "",
                "latex": "",
                "table_rows": [["Term", "Value"], ["First", "1"]],
                "linked_visual_orders": [],
                "linked_context_orders": [],
                "caption": "",
                "confidence": 0.999,
            }
            task["reading_order"] = 2
            task["linked_visual_orders"] = [3]
            task["linked_context_orders"] = [1]
            figure["reading_order"] = 3
            page["blocks"] = [table, task, figure]
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=47,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        provider=provider,
    )

    task = result["canonical"]["tasks"][0]
    assert task["requires_context"] is True
    assert "Term | Value" in task["shared_context"]
    assert "First | 1" in task["shared_context"]
    assert task["content_objects"]["shared_context_blocks"][0]["kind"] == "table"
    assert task["content_objects"]["shared_context_blocks"][0]["block_id"]
    assert task["source_start"] > 0
    assert task["section_id"] != "SEC-0000"
    mmd = result["mmd_text"]
    assert mmd.index("Term") < mmd.index("Describe the pattern")



def test_explicit_cross_page_figure_reference_survives_page_local_relationships(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        first, second = result["pages"]
        figure = second["blocks"][1]
        figure["reading_order"] = 3
        first["blocks"].append(figure)
        task = second["blocks"][0]
        task["reading_order"] = 1
        task["linked_visual_orders"] = []
        second["blocks"] = [task]
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=48,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        provider=provider,
    )

    task = result["canonical"]["tasks"][0]
    assert task["explicit_figure_reference_ids"] == ["1"]
    assert len(task["figure_refs"]) == 1
    assert len(task["image_urls"]) == 1
    assert task["display_prompt"].count("[img ") == 1
    assert result["mmd_text"].index("Odd-number pattern") < result["mmd_text"].index(
        "Describe the pattern shown in Fig. 1."
    )


def test_verified_nonstandard_task_cue_bypasses_legacy_regex_vocabulary(
    tmp_path: Path, monkeypatch,
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        for page in result["pages"]:
            if page["page_number"] != 2:
                continue
            task = page["blocks"][0]
            task["source_label"] = "Explore"
            task["linked_visual_orders"] = [2]
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=49,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        provider=provider,
    )

    assert len(result["canonical"]["tasks"]) == 1
    task = result["canonical"]["tasks"][0]
    assert task["source_label"] == "Explore"
    assert task["raw_prompt"] == "Describe the pattern shown in Fig. 1."
    assert task["source_location_confidence"] == "gpt_pdf_acsd_verified_block"
    assert "# Discuss" in result["mmd_text"]
    assert "# Explore" not in result["mmd_text"]


def test_verified_page_acsd_rehydrates_after_future_core_recompile(
    tmp_path: Path, monkeypatch,
):
    from types import SimpleNamespace
    from app.services import canonical_source_phase2 as phase2
    from app.services import uploads

    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    initial = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=50,
        artifact_dir=artifact_dir,
        fallback_reason=["mathpix_hard_failure:test"],
        provider=_verified_provider,
    )
    fresh = phase2.compile_phase2_source(
        initial["mmd_text"],
        source_filename="source.pdf",
        consumer_module="build_concepts",
    )
    assert "source_reconstruction" not in fresh.canonical
    monkeypatch.setattr(uploads, "upload_file_path", lambda _job: pdf)
    job = SimpleNamespace(
        id=50,
        filename="source.pdf",
        mmd_text=initial["mmd_text"],
    )

    canonical, report = fallback.rehydrate_verified_fallback(
        job,
        fresh.canonical,
        fresh.report,
        artifact_dir=artifact_dir,
    )

    assert canonical["source_reconstruction"]["status"] == "verified"
    assert canonical["source_contract"]["source_origin"] == fallback.FALLBACK_ORIGIN
    assert canonical["tasks"][0]["gpt_pdf_acsd_relationship"]["page_id"] == (
        "PDF-PAGE-0002"
    )
    assert canonical["tasks"][0]["display_prompt"].count("[img ") == 1
    assert report["source_reconstruction"]["page_count"] == 2
