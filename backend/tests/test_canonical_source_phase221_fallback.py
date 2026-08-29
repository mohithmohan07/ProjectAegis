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
        fallback_reason=["pdf_source"],
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
    # One verified batch entry plus the sealed complete bundle for the source
    # hash, which lets an unchanged source skip lane re-entry entirely.
    assert len(list(cache_dir.glob("*.json"))) == 2
    sealed_key = fallback._bundle_cache_key(fallback._pdf_sha256(pdf))
    assert fallback._batch_cache_path(sealed_key).exists()


def test_page_batches_extract_in_parallel_and_assemble_in_order(
    tmp_path: Path, monkeypatch,
):
    """Independent page batches overlap; the bundle stays deterministic."""
    import threading

    pdf = tmp_path / "source.pdf"
    doc = fitz.open()
    for number in range(1, 7):
        page = doc.new_page(width=600, height=800)
        page.insert_text((50, 70), f"Page {number} heading", fontsize=18)
    doc.save(pdf)
    doc.close()

    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setenv("AEGIS_GPT_PDF_ACSD_BATCH_PAGES", "2")
    monkeypatch.setenv("AEGIS_GPT_PDF_ACSD_PARALLEL_BATCHES", "3")
    import time as time_mod

    lock = threading.Lock()
    in_flight = {"now": 0, "max": 0}

    def provider(pages):
        with lock:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time_mod.sleep(0.3)
        with lock:
            in_flight["now"] -= 1
        return {
            "status": "verified",
            "pages": [
                {"page_number": page.page_number, "blocks": []}
                for page in pages
            ],
        }

    bundle = fallback.extract_pdf_to_page_acsd(pdf, provider=provider)

    # At least two of the three batches were in flight together.
    assert in_flight["max"] >= 2
    assert [row["page_number"] for row in bundle["pages"]] == [1, 2, 3, 4, 5, 6]
    assert [entry["batch"] for entry in bundle["batches"]] == [1, 2, 3]
    assert all(entry["status"] == "verified" for entry in bundle["batches"])


def test_build_concepts_pdf_converts_through_the_gpt_reader(
    client,
    tmp_path: Path,
    monkeypatch,
):
    """A PDF upload reaches canonical source through the reader, end to end."""
    from app import config
    from tests.conftest import convert_concept_upload

    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "integration-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "use_live_generation", lambda: True)

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



def test_failed_gpt_reader_persists_billable_openai_usage(
    client,
    tmp_path: Path,
    monkeypatch,
):
    """A rejected reading still cost tokens; the job must keep that record."""
    from app import config
    from app.services import openai_usage
    from tests.conftest import stream_error_message

    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)

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

    assert "synthetic verified-source rejection" in (
        stream_error_message(response) or ""
    )
    reloaded = client.get(f"/build-concepts/uploads/{job['id']}").json()
    assert reloaded["openai_usage"]["request_count"] == 1
    assert reloaded["openai_usage"]["total_tokens"] == 150


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
        fallback_reason=["pdf_source"],
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
    import hashlib

    from app import config
    from app.api import source_assets
    from app.services import uploads

    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "unit-test-secret")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        uploads,
        "source_artifact_directory",
        lambda _job_id: tmp_path / "source-shadow",
    )
    data = b"jpeg"
    filename = f"{hashlib.sha256(data).hexdigest()}.jpg"
    path = fallback.source_asset_path(19, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
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
        fallback_reason=["pdf_source"],
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
            fallback_reason=["pdf_source"],
            provider=lambda _pages: {
                "status": "review_required",
                "reason": "uncertain page",
            },
        )

    assert existing.read_text(encoding="utf-8") == "existing-source"
    assert not list(artifact_dir.glob(".phase221-staging-*"))


def test_publishing_sweeps_a_retired_artifact_an_older_run_left_behind(
    tmp_path: Path, monkeypatch,
):
    """An artifact directory written before the converter was removed still
    holds ``source.mathpix.raw.mmd``. Publishing must retire it rather than
    serve a file no current run produces."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "source-shadow"
    artifact_dir.mkdir()
    legacy_name, = fallback.LEGACY_ARTIFACT_NAMES
    stale = artifact_dir / legacy_name
    stale.write_text("mmd from a retired converter", encoding="utf-8")
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=46,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
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
        fallback_reason=["pdf_source"],
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
        fallback_reason=["pdf_source"],
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
        fallback_reason=["pdf_source"],
        provider=provider,
    )

    assert len(result["canonical"]["tasks"]) == 1
    task = result["canonical"]["tasks"][0]
    assert task["source_label"] == "Explore"
    assert task["raw_prompt"] == "Describe the pattern shown in Fig. 1."
    assert task["source_location_confidence"] == "gpt_pdf_acsd_verified_block"
    # §3 purge, item 4D: the visible cue renders VERBATIM — the retired
    # vocabulary used to collapse every unknown cue to "Discuss".
    assert "# Explore" in result["mmd_text"]
    assert "# Discuss" not in result["mmd_text"]
    # A cue outside every legacy vocabulary ships with the neutral kind and
    # a not-model-ruled scope marker — never a keyword-derived semantic.
    assert task["source_kind"] == "checkpoint_question"
    assert task["chapter_wide"] is False
    assert task["scope_ruling"] == "not_model_ruled_flagged"
    # The verified visual link still ships with the flagged task.
    assert len(task["figure_refs"]) == 1


def test_exercise_style_label_no_longer_makes_a_ledger_task_chapter_wide(
    tmp_path: Path, monkeypatch,
):
    """§3 purge, item 4D (~:2739): the three-word label list used to set
    chapter_wide=True. Scope is the outline's assessment-span verdict; with
    no outline the task ships topic-scoped with a not-model-ruled marker."""
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
            task["source_label"] = "Questions"
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=52,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=provider,
    )

    task = result["canonical"]["tasks"][0]
    assert task["source_label"] == "Questions"
    assert task["chapter_wide"] is False
    assert task["scope_ruling"] == "not_model_ruled_flagged"
    # The verbatim label is also the rendered heading.
    assert "# Questions" in result["mmd_text"]


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
        fallback_reason=["pdf_source"],
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


def test_worked_example_task_block_classifies_as_worked_example(
    tmp_path: Path, monkeypatch,
):
    """A verified 'Example N' task block enters the ledger as a
    worked_example item — the AP review found all 16 of the textbook's
    worked example problems missing because they never became tasks."""
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
            task["source_label"] = "Example 3"
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=51,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=provider,
    )

    task = result["canonical"]["tasks"][0]
    assert task["source_label"] == "Example 3"
    assert task["source_kind"] == "worked_example"


def _ruled_outline(task_kind: str) -> dict:
    """A chapter outline with the model's per-task kind ruling attached."""
    return {
        "version": fallback.OUTLINE_VERSION,
        "chapter_title": "1 Number Patterns",
        "topics": [
            {"title": "1 Number Patterns", "kind": "content",
             "start_page_id": "PDF-PAGE-0001", "start_reading_order": 1},
        ],
        "task_partitions": [],
        "ruled_task_kinds": [["PDF-PAGE-0002", 1, task_kind]],
        "unruled_task_refs": [],
        "notes": [],
        "review_flags": [],
    }


def test_model_ruled_info_hub_ships_source_kind_info_hub(
    tmp_path: Path, monkeypatch,
):
    """§4 Phase 1.2 / Rule 1: the ACSD lane can now capture info_hub —
    the outline judge's task_kind ruling, never a label vocabulary,
    assigns the kind."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        fallback, "derive_chapter_outline",
        lambda _bundle: _ruled_outline("info_hub"),
    )

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        for page in result["pages"]:
            if page["page_number"] != 2:
                continue
            task = page["blocks"][0]
            task["source_label"] = "Do you know?"
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=53,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=provider,
    )

    task = result["canonical"]["tasks"][0]
    assert task["source_kind"] == "info_hub"
    assert task["activity_origin"] is False
    assert task["kind_ruling"] == "chapter_outline_task_kind"


def test_model_ruled_activity_ships_source_kind_activity(
    tmp_path: Path, monkeypatch,
):
    """A ruled activity keeps its dual identity: source_kind=activity plus
    activity_origin, with no label list involved (the cue here is a
    publisher word no vocabulary knows)."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        fallback, "derive_chapter_outline",
        lambda _bundle: _ruled_outline("activity"),
    )

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        for page in result["pages"]:
            if page["page_number"] != 2:
                continue
            page["blocks"][0]["source_label"] = "Karke Dekho"
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=54,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=provider,
    )

    task = result["canonical"]["tasks"][0]
    assert task["source_kind"] == "activity"
    assert task["activity_origin"] is True
    assert task["kind_ruling"] == "chapter_outline_task_kind"


def test_unruled_task_keeps_the_neutral_kind_and_a_flagged_marker(
    tmp_path: Path, monkeypatch,
):
    """No outline ruling -> the neutral default ships with a reviewable
    kind_ruling marker; an 'Activity' cue word alone assigns NOTHING."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=55,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=_verified_provider,
    )

    task = result["canonical"]["tasks"][0]
    # The printed cue IS "Activity" — under the retired vocabulary that
    # word alone minted source_kind=activity. Unruled now stays neutral.
    assert task["source_label"] == "Activity"
    assert task["source_kind"] == "checkpoint_question"
    assert task["activity_origin"] is False
    assert task["kind_ruling"] == "not_model_ruled_flagged"


def test_outline_normalization_records_the_task_kind_rulings():
    page_acsd = {
        "pdf_sha256": "abc",
        "pages": [{
            "page_id": "PDF-PAGE-0001",
            "page_number": 1,
            "blocks": [
                {"reading_order": 1, "kind": "heading", "heading_level": 1,
                 "text": "Light"},
                {"reading_order": 2, "kind": "task", "source_label": "",
                 "text": "Do you know? Light once defined the metre."},
            ],
        }],
    }
    candidate = {
        "chapter_title": "Light",
        "topics": [{"title": "Light", "kind": "content",
                    "start_page_id": "PDF-PAGE-0001",
                    "start_reading_order": 1}],
        "task_partitions": [],
        "whole_tasks": [{"page_id": "PDF-PAGE-0001", "reading_order": 2,
                         "task_kind": "info_hub"}],
        "notes": [],
    }
    outline, flags = fallback._normalize_chapter_outline(page_acsd, candidate)
    assert outline is not None
    assert flags == []
    assert outline["ruled_task_kinds"] == [["PDF-PAGE-0001", 2, "info_hub"]]
    assert outline["unruled_task_refs"] == []


def test_acsd_dropped_furniture_reaches_the_canonical_verbatim(
    tmp_path: Path, monkeypatch,
):
    """R4 for the ACSD lane: omitted running headers/footers/page numbers
    ride the canonical with what they said, not merely as an omission."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    artifact_dir = tmp_path / "canonical-source"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def provider(pages: list[fallback.PdfPage]) -> dict:
        result = _verified_provider(pages)
        result["pages"][0]["dropped_furniture"] = ["MATHS STD 4", "14"]
        result["pages"][1]["dropped_furniture"] = ["MATHS STD 4"]
        return result

    result = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=56,
        artifact_dir=artifact_dir,
        fallback_reason=["pdf_source"],
        provider=provider,
    )

    assert result["canonical"]["dropped_furniture"] == [
        "MATHS STD 4", "14", "MATHS STD 4",
    ]
    # The extraction schema itself requires the record on every page.
    schema = fallback.extraction_schema([])
    page_schema = schema["schema"]["properties"]["pages"]["items"]
    assert "dropped_furniture" in page_schema["properties"]
    assert "dropped_furniture" in page_schema["required"]


def test_pdf_lane_rendered_mmd_compiles_exempt_from_qx(monkeypatch, tmp_path):
    """The PDF lane never pays a QX membership pass: the rendered MMD's
    recorded reader stamp (source_contract.source_reader, carried from the
    ``<!-- source_reader: gpt-pdf-to-acsd-... -->`` header) identifies the
    GPT page ledger as the membership authority BEFORE the Phase 2.1.2
    contract checks exemption — so QX's provider is never called and no QX
    ledger stamps claim membership on this lane."""
    from app.services import canonical_source_phase2 as phase2
    from app.services import canonical_source_phase212 as qx

    monkeypatch.setattr(qx, "_CACHE_DIR", tmp_path / "qx-cache")

    def explode(**kwargs):  # pragma: no cover - exemption must pre-empt QX
        raise AssertionError(
            "the PDF lane must not pay a QX author/critic pass"
        )

    monkeypatch.setattr(qx, "_call_provider", explode)

    page_acsd = {
        "pdf_sha256": "abc",
        "pages": [{
            "page_id": "PDF-PAGE-0001",
            "page_number": 1,
            "blocks": [
                {"reading_order": 1, "kind": "heading", "heading_level": 1,
                 "text": "1 Number Patterns"},
                {"reading_order": 2, "kind": "paragraph",
                 "text": "A sequence follows a visible rule."},
                {"reading_order": 3, "kind": "task", "source_label":
                 "Activity", "text": "Describe the pattern."},
            ],
        }],
    }
    mmd_text = fallback.render_page_acsd_to_mmd(page_acsd)
    assert f"<!-- source_reader: {fallback.source_reader_version()} -->" in (
        mmd_text
    )

    compiled = phase2.compile_phase2_source(
        mmd_text,
        source_filename="source.pdf",
        consumer_module="build_concepts",
    )
    canonical = compiled.canonical
    assert canonical["task_membership"] == {
        "status": "exempt",
        "authority": "gpt_page_ledger",
    }
    assert qx.LEDGER_KEY not in canonical
    codes = {
        str(issue.get("code") or "")
        for issue in phase2.phase2_inventory_issues(canonical)
    }
    assert "task_membership_unadjudicated" not in codes


def test_ingestion_normalizes_plain_mathrm_atoms_to_supported_text():
    """The CMS KaTeX subset bans ``\\mathrm``; GPT (like Mathpix before it)
    transcribes physics units with it. [measured] job 'Electricity'
    (Class 10 Ch 5, 2026-08-29): a ``\\mathrm`` minted at PDF-read time
    paused the run at source review and the semantic graph was refused
    downstream. Plain unit atoms are now canonicalized AT INGESTION with
    the same deterministic rewrite legacy export uses; non-plain bodies
    stay byte-for-byte for source review to judge.
    """

    page = fallback.PdfPage(
        page_id="P-1", page_number=1, text="Electric power",
        image_data_url="data:image/png;base64,AAAA", width=1000, height=1000,
    )
    candidate = {
        "pages": [{
            "page_id": "P-1",
            "confidence": 0.99,
            "blocks": [
                {
                    "kind": "math", "reading_order": 1,
                    "bbox": [10, 10, 400, 60], "confidence": 0.99,
                    "latex": r"P = V \times I\ \mathrm{~W}",
                },
                {
                    "kind": "paragraph", "reading_order": 2,
                    "bbox": [10, 70, 400, 120], "confidence": 0.99,
                    "text": (
                        "One volt is [Katex]1\\ \\mathrm{V}[/Katex] of "
                        "potential."
                    ),
                },
                {
                    "kind": "math", "reading_order": 3,
                    "bbox": [10, 130, 400, 180], "confidence": 0.99,
                    # A body with a nested command has mathematical
                    # semantics: it must remain untouched for review.
                    "latex": r"R = 5\ \mathrm{\Omega}",
                },
            ],
        }],
    }

    normalized, reason = fallback.validate_page_extraction([page], candidate)

    assert reason == ""
    blocks = {
        int(block["reading_order"]): block
        for block in normalized["pages"][0]["blocks"]
    }
    assert blocks[1]["latex"] == r"P = V \times I\ \text{ W}"
    assert "\\mathrm" not in blocks[2]["text"]
    assert "\\text{V}" in blocks[2]["text"]
    assert blocks[3]["latex"] == r"R = 5\ \mathrm{\Omega}"
    flags = normalized["pages"][0].get("review_flags") or []
    assert any("normalized \\mathrm unit atom" in flag for flag in flags)
