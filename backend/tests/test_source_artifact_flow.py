"""Converted uploads expose Phase-aware canonical-source artifacts."""
from __future__ import annotations

import io
import json

from tests.conftest import convert_assessment_upload, convert_concept_upload


SOURCE = (
    "# Ordered Chapter\n\n"
    "## First Topic\n\n"
    "A relation is $a^2+b^2=c^2$.\n\n"
    "Questions\n\n"
    "1. Calculate the missing value.\n\n"
    "## Last Topic\n\n"
    "The final topic remains last.\n"
)


def _assert_downloads(
    client,
    job_id: int,
    converted: dict,
    *,
    phase2: bool,
) -> None:
    manifest = client.get(f"/source-artifacts/uploads/{job_id}")
    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["available"] is True
    assert payload["shadow_mode"] is (not phase2)
    assert payload["used_for_generation"] is phase2
    assert payload["manifest_url"] == f"/source-artifacts/uploads/{job_id}"
    assert {item["kind"] for item in payload["files"]} == {
        "raw_mmd", "canonical_json", "aegis_mmd", "report",
    }
    if phase2:
        assert payload["phase"] == "phase-2-source-critical"
        assert payload["generation_usage"]["mode"] == "source-critical"
        assert payload["phase2_inventory_ready"] is True
    else:
        assert payload["generation_usage"]["mode"] == "shadow"

    raw = client.get(f"/source-artifacts/uploads/{job_id}/raw_mmd")
    assert raw.status_code == 200
    assert raw.headers["x-aegis-shadow-only"] == (
        "false" if phase2 else "true"
    )
    assert raw.headers["x-aegis-generation-usage"] == (
        "source-critical" if phase2 else "shadow"
    )
    assert raw.text == converted["mmd_text"]

    canonical = client.get(
        f"/source-artifacts/uploads/{job_id}/canonical_json"
    )
    assert canonical.status_code == 200
    canonical_payload = json.loads(canonical.text)
    assert canonical_payload["shadow_mode"] is (not phase2)
    assert canonical_payload["used_for_generation"] is phase2
    assert canonical_payload["ordering_contract"]["topic_sequence_locked"] is True
    if phase2:
        assert canonical_payload["source_contract"]["mode"] == (
            "acsd-phase2-source-critical"
        )
        assert [
            task["qid"] for task in canonical_payload["tasks"]
        ] == [
            f"QINV-{index:04d}"
            for index in range(1, len(canonical_payload["tasks"]) + 1)
        ]

    derived = client.get(f"/source-artifacts/uploads/{job_id}/aegis_mmd")
    assert derived.status_code == 200
    assert (
        "AEGIS CANONICAL SOURCE PHASE 2" in derived.text
        if phase2
        else "AEGIS CANONICAL SOURCE SHADOW" in derived.text
    )
    assert "[Katex] a^2+b^2=c^2 [/Katex]" in derived.text

    report = client.get(f"/source-artifacts/uploads/{job_id}/report")
    assert report.status_code == 200
    assert json.loads(report.text)["used_for_generation"] is phase2


def test_concept_conversion_writes_phase2_source_critical_artifacts(client):
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
    assert converted["source_artifacts"]["used_for_generation"] is True
    assert converted["source_artifacts"]["shadow_mode"] is False
    reloaded = client.get(f"/build-concepts/uploads/{job['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["source_artifacts"]["available"] is True
    assert reloaded.json()["source_artifacts"]["used_for_generation"] is True
    _assert_downloads(client, job["id"], converted, phase2=True)


def test_assessment_conversion_remains_on_phase1_shadow_contract(client):
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
    assert converted["source_artifacts"]["used_for_generation"] is False
    reloaded = client.get(f"/build-assessments/uploads/{job['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["source_artifacts"]["available"] is True
    _assert_downloads(client, job["id"], converted, phase2=False)


def test_replacing_the_source_removes_stale_canonical_artifacts(client):
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
    assert response.json()["source_artifacts"]["available"] is False

    manifest = client.get(f"/source-artifacts/uploads/{job['id']}")
    assert manifest.status_code == 200
    assert manifest.json()["available"] is False
    missing = client.get(
        f"/source-artifacts/uploads/{job['id']}/canonical_json"
    )
    assert missing.status_code == 404
