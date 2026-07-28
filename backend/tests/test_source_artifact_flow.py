"""Converted uploads emit private Phase-1 shadow artifacts without cutover."""
from __future__ import annotations

import io
import json

from tests.conftest import convert_assessment_upload, convert_concept_upload


SOURCE = (
    "# Ordered Chapter\n\n"
    "## First Topic\n\n"
    "A relation is $a^2+b^2=c^2$.\n\n"
    "## Last Topic\n\n"
    "The final topic remains last.\n"
)


def _assert_downloads(client, job_id: int, converted: dict) -> None:
    manifest = client.get(f"/source-artifacts/uploads/{job_id}")
    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["available"] is True
    assert payload["shadow_mode"] is True
    assert payload["used_for_generation"] is False
    assert payload["manifest_url"] == f"/source-artifacts/uploads/{job_id}"
    assert {item["kind"] for item in payload["files"]} == {
        "raw_mmd", "canonical_json", "aegis_mmd", "report",
    }

    raw = client.get(f"/source-artifacts/uploads/{job_id}/raw_mmd")
    assert raw.status_code == 200
    assert raw.headers["x-aegis-shadow-only"] == "true"
    assert raw.text == converted["mmd_text"]

    canonical = client.get(
        f"/source-artifacts/uploads/{job_id}/canonical_json"
    )
    assert canonical.status_code == 200
    canonical_payload = json.loads(canonical.text)
    assert canonical_payload["shadow_mode"] is True
    assert canonical_payload["used_for_generation"] is False
    assert canonical_payload["ordering_contract"]["topic_sequence_locked"] is True

    derived = client.get(f"/source-artifacts/uploads/{job_id}/aegis_mmd")
    assert derived.status_code == 200
    assert "AEGIS CANONICAL SOURCE SHADOW" in derived.text
    assert "[Katex] a^2+b^2=c^2 [/Katex]" in derived.text

    report = client.get(f"/source-artifacts/uploads/{job_id}/report")
    assert report.status_code == 200
    assert json.loads(report.text)["used_for_generation"] is False


def test_concept_conversion_writes_downloadable_shadow_artifacts(client):
    files = {
        "file": (
            "ordered.mmd",
            io.BytesIO(SOURCE.encode("utf-8")),
            "text/plain",
        )
    }
    job = client.post(
        "/build-concepts/post-learning/uploads",
        files=files,
    ).json()

    converted = convert_concept_upload(client, job["id"])

    assert converted["status"] == "converted"
    assert converted["source_artifacts"]["available"] is True
    assert converted["source_artifacts"]["used_for_generation"] is False
    _assert_downloads(client, job["id"], converted)


def test_assessment_conversion_uses_the_same_shadow_contract(client):
    files = {
        "file": (
            "assessment-source.mmd",
            io.BytesIO(SOURCE.encode("utf-8")),
            "text/plain",
        )
    }
    job = client.post(
        "/build-assessments/uploads?upload_type=textbook",
        files=files,
    ).json()

    converted = convert_assessment_upload(client, job["id"])

    assert converted["source_artifacts"]["available"] is True
    _assert_downloads(client, job["id"], converted)


def test_replacing_the_source_removes_stale_shadow_artifacts(client):
    files = {
        "file": (
            "old.mmd",
            io.BytesIO(SOURCE.encode("utf-8")),
            "text/plain",
        )
    }
    job = client.post(
        "/build-concepts/post-learning/uploads",
        files=files,
    ).json()
    convert_concept_upload(client, job["id"])
    assert client.get(
        f"/source-artifacts/uploads/{job['id']}"
    ).json()["available"] is True

    replacement = {
        "file": (
            "new.mmd",
            io.BytesIO(b"# New source\n\nNothing compiled yet.\n"),
            "text/plain",
        )
    }
    response = client.put(
        f"/build-concepts/uploads/{job['id']}/file",
        files=replacement,
    )
    assert response.status_code == 200

    manifest = client.get(f"/source-artifacts/uploads/{job['id']}")
    assert manifest.status_code == 200
    assert manifest.json()["available"] is False
    missing = client.get(
        f"/source-artifacts/uploads/{job['id']}/canonical_json"
    )
    assert missing.status_code == 404
