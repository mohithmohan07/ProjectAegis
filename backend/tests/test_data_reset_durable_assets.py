"""Step 10 (Q8): published image URLs survive rotation, resets, and re-runs.

A school's workbook embeds ``/source-assets/{job_id}/{sha256}.jpg?sig=…``
verbatim. These tests pin the durability contract: the signature never gates
delivery, the mint-time pin keeps bytes serveable after the job's artifact
directory dies, a data reset preserves the store, and a serve-time miss is
recorded instead of silently 404ing.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import tarfile
from pathlib import Path

import fitz
import pytest

from app import config
from app.db import Base, engine, init_db
from app.services import canonical_source_phase221_fallback as fallback
from app.services import source_asset_store
from app.services import uploads
from tests.conftest import _load_test_fixtures


CONTENT = b"jpeg-bytes-stand-in-for-a-real-crop"


def _hash_name(data: bytes) -> str:
    return f"{hashlib.sha256(data).hexdigest()}.jpg"


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(60, 190, 420, 450), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    page.insert_text((90, 260), "1, 3, 5, 7", fontsize=24)
    doc.save(path)
    doc.close()


def _isolated_serving(tmp_path: Path, monkeypatch) -> Path:
    """Point both serve paths (job dir and store) at this test's tmp dir."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    job_root = tmp_path / "jobs"
    monkeypatch.setattr(
        uploads,
        "source_artifact_directory",
        lambda job_id: job_root / str(int(job_id)),
        raising=False,
    )
    return job_root


def test_rotated_secret_still_serves_published_asset(client, tmp_path, monkeypatch):
    job_root = _isolated_serving(tmp_path, monkeypatch)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "term-one-secret")
    filename = _hash_name(CONTENT)
    asset_dir = job_root / "7" / fallback.ASSET_DIRNAME
    asset_dir.mkdir(parents=True)
    (asset_dir / filename).write_bytes(CONTENT)
    published = fallback.asset_url(7, filename)

    # The secret a school's workbook URL was signed under rotates away.
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "term-two-secret")

    response = client.get(published.removeprefix("https://aegis.example"))
    assert response.status_code == 200
    assert response.content == CONTENT
    assert response.headers["cache-control"].endswith("immutable")


def test_signature_is_advisory_for_existing_content(client, tmp_path, monkeypatch):
    job_root = _isolated_serving(tmp_path, monkeypatch)
    filename = _hash_name(CONTENT)
    asset_dir = job_root / "7" / fallback.ASSET_DIRNAME
    asset_dir.mkdir(parents=True)
    (asset_dir / filename).write_bytes(CONTENT)

    assert client.get(f"/source-assets/7/{filename}").status_code == 200
    assert (
        client.get(f"/source-assets/7/{filename}?sig=forged-nonsense").status_code
        == 200
    )


def test_store_fallback_survives_job_artifact_deletion(client, tmp_path, monkeypatch):
    _isolated_serving(tmp_path, monkeypatch)
    filename = source_asset_store.pin_asset(
        CONTENT,
        job_id=7,
        asset_url=f"https://aegis.example/source-assets/7/{_hash_name(CONTENT)}",
    )

    # No job artifact directory exists at all — reset, replacement, or a
    # checkpoint import pointing at a job this volume never had.
    response = client.get(f"/source-assets/7/{filename}")
    assert response.status_code == 200
    assert response.content == CONTENT


def test_store_serves_across_jobs_with_any_sig(client, tmp_path, monkeypatch):
    # A checkpoint import re-homes content under a new job id while its URLs
    # keep the old one; the store answers by content hash regardless.
    _isolated_serving(tmp_path, monkeypatch)
    filename = source_asset_store.pin_asset(
        CONTENT,
        job_id=7,
        asset_url=f"https://aegis.example/source-assets/7/{_hash_name(CONTENT)}",
    )

    response = client.get(f"/source-assets/999/{filename}?sig=forged-nonsense")
    assert response.status_code == 200
    assert response.content == CONTENT


def test_boot_sweep_backfills_legacy_job_assets(client, tmp_path, monkeypatch):
    job_root = _isolated_serving(tmp_path, monkeypatch)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "sweep-secret")
    monkeypatch.setattr(config, "UPLOAD_DIR", job_root)
    filename = _hash_name(CONTENT)
    # A crop minted before the store existed: on disk, never pinned.
    asset_dir = job_root / "7" / fallback.ASSET_DIRNAME
    asset_dir.mkdir(parents=True)
    (asset_dir / filename).write_bytes(CONTENT)

    assert fallback.pin_existing_job_assets() == 1
    # Idempotent: a caught-up sweep pins nothing more.
    assert fallback.pin_existing_job_assets() == 0

    entry = json.loads(
        source_asset_store.manifest_path(filename).read_text(encoding="utf-8")
    )
    assert entry["job_id"] == 7
    assert entry["asset_url"] == fallback.asset_url(7, filename)

    import shutil

    shutil.rmtree(job_root / "7")
    response = client.get(f"/source-assets/7/{filename}")
    assert response.status_code == 200
    assert response.content == CONTENT


def test_boot_sweep_records_a_crop_that_lost_its_content_hash(
    tmp_path, monkeypatch, caplog
):
    job_root = _isolated_serving(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "UPLOAD_DIR", job_root)
    asset_dir = job_root / "5" / fallback.ASSET_DIRNAME
    asset_dir.mkdir(parents=True)
    # Valid-shaped name over bytes that no longer hash to it.
    claimed = "c" * 64 + ".jpg"
    (asset_dir / claimed).write_bytes(b"rotted bytes")

    with caplog.at_level(
        logging.WARNING, logger="app.services.canonical_source_phase221_fallback"
    ):
        assert fallback.pin_existing_job_assets() == 0

    assert any("does not match its content" in r.getMessage() for r in caplog.records)
    assert not source_asset_store.stored_asset_path(claimed).exists()


def test_serve_time_pin_self_heals_legacy_assets(client, tmp_path, monkeypatch):
    job_root = _isolated_serving(tmp_path, monkeypatch)
    filename = _hash_name(CONTENT)
    asset_dir = job_root / "7" / fallback.ASSET_DIRNAME
    asset_dir.mkdir(parents=True)
    (asset_dir / filename).write_bytes(CONTENT)

    # First serve hits the job dir and pins as a side effect.
    assert client.get(f"/source-assets/7/{filename}").status_code == 200

    import shutil

    shutil.rmtree(job_root / "7")
    response = client.get(f"/source-assets/7/{filename}")
    assert response.status_code == 200
    assert response.content == CONTENT


def test_data_reset_preserves_the_durable_store(client):
    # Deliberately unpatched paths: this pins the real reset against the real
    # store location. The store must be the one thing a reset never clears.
    filename = source_asset_store.pin_asset(
        CONTENT,
        job_id=99,
        asset_url=f"https://aegis.example/source-assets/99/{_hash_name(CONTENT)}",
    )
    stored = source_asset_store.stored_asset_path(filename)
    assert stored.exists()

    try:
        token = client.post("/admin/login", json={"password": "admin"}).json()[
            "token"
        ]
        assert (
            client.post(
                "/data/reset", headers={"X-Admin-Token": token}
            ).status_code
            == 200
        )
        assert stored.exists(), "reset_all cleared the durable asset store"
        response = client.get(f"/source-assets/99/{filename}")
        assert response.status_code == 200
        assert response.content == CONTENT
    finally:
        stored.unlink(missing_ok=True)
        source_asset_store.manifest_path(filename).unlink(missing_ok=True)
        # Restore the exact session-start state (conftest._prepare's
        # sequence), not reset_all's syllabus-bootstrapped variant, so
        # downstream DB-state suites see what they saw at session start.
        Base.metadata.drop_all(bind=engine)
        init_db()
        _load_test_fixtures()


def test_mint_pins_asset_with_manifest_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "mint-secret")
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    page_acsd = {
        "pages": [
            {
                "page_number": 1,
                "blocks": [{"kind": "figure", "bbox": [100, 200, 700, 600]}],
            }
        ]
    }

    count = fallback.materialize_visual_assets(
        pdf, page_acsd, job_id=11, artifact_dir=tmp_path / "artifacts"
    )

    assert count == 1
    block = page_acsd["pages"][0]["blocks"][0]
    job_copy = tmp_path / "artifacts" / fallback.ASSET_DIRNAME / block["asset_filename"]
    stored = source_asset_store.stored_asset_path(block["asset_filename"])
    assert stored.exists()
    assert stored.read_bytes() == job_copy.read_bytes()
    entry = json.loads(
        source_asset_store.manifest_path(block["asset_filename"]).read_text(
            encoding="utf-8"
        )
    )
    assert entry["sha256"] + ".jpg" == block["asset_filename"]
    assert entry["job_id"] == 11
    assert entry["asset_url"] == block["asset_url"]
    assert entry["media_type"] == "image/jpeg"
    assert entry["bytes"] == stored.stat().st_size
    assert entry["public_base_url"] == "https://aegis.example"


def test_pin_is_idempotent_and_first_mint_keeps_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    first = source_asset_store.pin_asset(
        CONTENT, job_id=1, asset_url="https://aegis.example/source-assets/1/x"
    )
    second = source_asset_store.pin_asset(
        CONTENT, job_id=2, asset_url="https://aegis.example/source-assets/2/x"
    )
    assert first == second
    entry = json.loads(
        source_asset_store.manifest_path(first).read_text(encoding="utf-8")
    )
    assert entry["job_id"] == 1


def test_store_pin_failure_never_halts_materialization(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "mint-secret")

    def _refuse(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(fallback.source_asset_store, "pin_asset", _refuse)
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    page_acsd = {
        "pages": [
            {
                "page_number": 1,
                "blocks": [{"kind": "figure", "bbox": [100, 200, 700, 600]}],
            }
        ]
    }

    with caplog.at_level(
        logging.WARNING, logger="app.services.canonical_source_phase221_fallback"
    ):
        count = fallback.materialize_visual_assets(
            pdf, page_acsd, job_id=11, artifact_dir=tmp_path / "artifacts"
        )

    assert count == 1
    block = page_acsd["pages"][0]["blocks"][0]
    assert block["asset_url"]
    assert (
        tmp_path / "artifacts" / fallback.ASSET_DIRNAME / block["asset_filename"]
    ).exists()
    # Never a halt AND never silent: the degradation left a record (Q13/R4).
    assert any(
        "Durable pin failed" in record.getMessage() for record in caplog.records
    )


def test_missing_asset_404_is_recorded(client, tmp_path, monkeypatch, caplog):
    _isolated_serving(tmp_path, monkeypatch)
    filename = "e" * 64 + ".jpg"

    with caplog.at_level(logging.WARNING, logger="app.api.source_assets"):
        response = client.get(f"/source-assets/9/{filename}?sig=forged")

    assert response.status_code == 404
    records = [
        record
        for record in caplog.records
        if filename in record.getMessage()
    ]
    assert records and records[0].levelno == logging.WARNING
    assert "sig_valid=False" in records[0].getMessage()


def test_malformed_names_are_still_refused(client, tmp_path, monkeypatch):
    _isolated_serving(tmp_path, monkeypatch)
    assert client.get("/source-assets/9/notahash.jpg").status_code == 404
    with pytest.raises(ValueError):
        source_asset_store.stored_asset_path("../escape.jpg")
    with pytest.raises(ValueError):
        source_asset_store.stored_asset_path("notahash.jpg")


def test_admin_export_requires_auth_and_contains_the_store(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    filename = source_asset_store.pin_asset(
        CONTENT, job_id=3, asset_url="https://aegis.example/source-assets/3/x"
    )
    # In-flight atomic-write temp files and stray dirs are not store members.
    (source_asset_store.store_root() / f".{filename}.tmp123").write_bytes(b"junk")
    (source_asset_store.store_root() / "subdir").mkdir()

    assert client.get("/admin/source-asset-store/export").status_code == 401

    token = client.post("/admin/login", json={"password": "admin"}).json()["token"]
    response = client.get(
        "/admin/source-asset-store/export", headers={"X-Admin-Token": token}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/gzip"
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        names = set(archive.getnames())
        assert names == {
            f"source-asset-store/{filename}",
            f"source-asset-store/{filename.removesuffix('.jpg')}.json",
        }
        member = archive.extractfile(f"source-asset-store/{filename}")
        assert member is not None and member.read() == CONTENT
