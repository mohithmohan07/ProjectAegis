from __future__ import annotations

import hashlib
import io
import copy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openpyxl import Workbook
from PIL import Image

from app import config
from app.services import source_asset_publication as publication
from app.services import source_asset_store

ORIGIN = "https://aegis.example.org"


def jpeg() -> bytes:
    target = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(target, format="JPEG")
    return target.getvalue()


def url_for(content: bytes, *, job: int = 7) -> str:
    return f"{ORIGIN}/source-assets/{job}/{hashlib.sha256(content).hexdigest()}.jpg"


def wire(url: str) -> str:
    return f'[img src="{url}" alt="Source diagram"]'


@pytest.fixture(autouse=True)
def isolated_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", ORIGIN)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)


def test_local_pin_is_not_public_delivery():
    data = jpeg()
    url = url_for(data)
    source_asset_store.pin_asset(data, job_id=7, asset_url=url)
    report = publication.inspect_assets(wire(url), allow_probe=False)
    assert report["state"] == "unverified"
    assert report["assets"][0]["local_state"] == "pinned"
    assert report["assets"][0]["reason"] == "public_probe_not_run"
    assert publication.report_defects(report)


def test_public_get_verifies_jpeg_bytes_and_dedupes_hash_without_forwarding_query():
    data = jpeg()
    seen = []

    def serve(request):
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=data)

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        report = publication.inspect_assets(
            [wire(url_for(data) + "?sig=old&redirect=https://other.invalid"), wire(url_for(data, job=9))],
            allow_probe=True, client=client,
        )
    assert report["state"] == "verified"
    assert seen == [url_for(data)]
    assert report["probe_count"] == 1
    assert report["source_url_count"] == 2
    assert report["assets"][0]["local_state"] == "not_pinned"


@pytest.mark.parametrize("bad_url", [
    "https://other.example.org/source-assets/7/" + "a" * 64 + ".jpg",
    ORIGIN + "/admin/secret.jpg",
    "https://aegis.example.org@other.example.org/source-assets/7/" + "a" * 64 + ".jpg",
    ORIGIN + "/source-assets/7/not-a-hash.jpg",
])
def test_untrusted_targets_are_reported_without_network(bad_url):
    def refuse(request):
        pytest.fail(f"Untrusted URL was fetched: {request.url}")

    with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
        report = publication.inspect_assets(wire(bad_url), allow_probe=True, client=client)
    assert report["state"] == "unverified"
    assert report["probe_count"] == 0


@pytest.mark.parametrize("origin", ["", "https://aegis.local", "https://localhost", "https://127.0.0.1", "https://aegis.example.org/path", "https://user:secret@aegis.example.org"])
def test_missing_or_nonpublic_origin_never_claims_success(monkeypatch, origin):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", origin)
    report = publication.inspect_assets(wire(url_for(jpeg())), allow_probe=True)
    assert report["state"] == "unverified"
    assert report["probe_count"] == 0


@pytest.mark.parametrize("kind, reason", [
    ("redirect", "http_status"), ("missing", "http_status"),
    ("html", "content_type"), ("wrong_bytes", "content_hash_mismatch"),
    ("too_large", "response_too_large"),
])
def test_failed_delivery_remains_named_report(kind, reason):
    data = jpeg()
    responses = {
        "redirect": httpx.Response(302, headers={"location": "https://other.invalid/private"}),
        "missing": httpx.Response(404),
        "html": httpx.Response(200, headers={"content-type": "text/html"}, content=data),
        "wrong_bytes": httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"wrong"),
        "too_large": httpx.Response(200, headers={"content-type": "image/jpeg", "content-length": str(publication.MAX_ASSET_BYTES + 1)}),
    }
    seen = []

    def serve(request):
        seen.append(request.url)
        return responses[kind]

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        report = publication.inspect_assets(wire(url_for(data)), allow_probe=True, client=client)
    assert report["state"] == "failed"
    assert report["assets"][0]["reason"] == reason
    assert len(seen) == 1


def test_matching_hash_and_mime_do_not_certify_invalid_image_bytes():
    data = b"not a jpeg even when the hash matches"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=data))) as client:
        report = publication.inspect_assets(wire(url_for(data)), allow_probe=True, client=client)
    assert report["assets"][0]["reason"] == "invalid_image_bytes"
    assert report["state"] == "failed"


def test_bad_url_does_not_prevent_remaining_assets_being_checked():
    data = jpeg()
    seen = []

    def serve(request):
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=data)

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        report = publication.inspect_assets(
            [wire("https://[invalid:port/image.jpg"), wire(ORIGIN + ":bad/source-assets/7/" + "a" * 64 + ".jpg"), wire(url_for(data))],
            allow_probe=True, client=client,
        )
    assert report["state"] == "unverified"
    assert seen == [url_for(data)]
    assert report["verified_count"] == 1
    assert any(row["reason"] == "malformed_image_url" for row in report["assets"])


def test_timeout_and_probe_budget_are_unverified_not_exceptions(monkeypatch):
    data = jpeg()

    def timeout(request):
        raise httpx.ReadTimeout("secret query must not appear in the report")

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        report = publication.inspect_assets(wire(url_for(data)), allow_probe=True, client=client)
    assert report["assets"][0]["error_type"] == "ReadTimeout"
    assert "secret query" not in str(report)
    monkeypatch.setattr(publication, "MAX_PROBES", 0)
    report = publication.inspect_assets(wire(url_for(data)), allow_probe=True)
    assert report["assets"][0]["reason"] == "probe_budget_exhausted"


def test_total_deadline_is_checked_on_small_transport_chunks():
    yielded = []

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(100):
                yielded.append(index)
                yield b"x"

    def serve(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, stream=Trickle())

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        result = publication._probe(client, url_for(jpeg()), "a" * 64, deadline=0)
    assert result["reason"] == "probe_budget_exhausted"
    assert yielded == [0]


def test_readiness_binds_current_exported_images_and_ignores_audit_quotes():
    payload = {"records": [{"concept_details": "No image", "_source_quote": wire(url_for(jpeg()))}], "chapter_meta": {}}
    report = publication.inspect_assets(publication.release_asset_input(payload), allow_probe=False)
    payload[publication.REPORT_FIELD] = report
    assert report["state"] == "no_assets"
    assert publication.readiness_defects(payload) == []
    payload["records"][0]["concept_details"] = wire(url_for(jpeg()))
    assert publication.readiness_defects(payload)[0].startswith("source_asset_publication_stale:")


def test_actual_workbook_image_wires_are_read_back_without_network():
    wb = Workbook()
    wb.active["A1"] = wire(url_for(jpeg()))
    output = io.BytesIO()
    wb.save(output)
    report = publication.inspect_workbooks({"master_xlsx": output.getvalue()}, allow_probe=False)
    assert report["source_url_count"] == 1
    assert report["state"] == "unverified"
    assert report["workbook_sha256s"]["master_xlsx"] == hashlib.sha256(output.getvalue()).hexdigest()


def test_bare_external_image_cells_remain_unverified_including_subquestion_keywords():
    wb = Workbook()
    wb.active.append(["Question", "Answer", "Sub-question", "Keyword"])
    wb.active.append(["answer_type_1", "answer_content_1", "sq1_answer_type_1", "sq1_keyword_1"])
    wb.active.append(["Image", "https://external.example.org/answer.jpg", "Image", "https://external.example.org/rubric.jpg"])
    output = io.BytesIO()
    wb.save(output)
    report = publication.inspect_workbooks({"master_xlsx": output.getvalue()}, allow_probe=False)
    assert report["source_url_count"] == 2
    assert report["state"] == "unverified"
    assert report["probe_count"] == 0


def test_new_master_manifests_require_current_receipts_but_legacy_does_not():
    from app.services import assessment_release_service as service
    from app.services import katex_render_validation as math_render

    release = SimpleNamespace(payload={}, diagnostics={})
    legacy = {"read_back": {}, "workbook_sha256s": {"master_xlsx": "old"}}
    assert service._readiness(release, legacy) == service.READY
    current = {**legacy, "output_validation_version": 1}
    assert service._readiness(release, current) == service.BLOCKED
    for checker, report in (
        (publication, publication.inspect_assets([], allow_probe=False)),
        (math_render, math_render.inspect_render({})),
    ):
        current[checker.REPORT_FIELD] = {**report, "workbook_sha256s": current["workbook_sha256s"]}
    assert service._readiness(release, current) == service.READY
    current[publication.REPORT_FIELD]["workbook_sha256s"] = {"master_xlsx": "another-file"}
    assert service._readiness(release, current) == service.BLOCKED


def test_concept_staging_records_checks_for_both_lanes_and_missing_new_report_blocks(db):
    from app.services import build_concepts_release as release
    from tests.test_assessment_release_run import _chapter_with_concepts
    from tests.test_pre_release_lane_wiring import _both_lanes_job

    job = _both_lanes_job(db, _chapter_with_concepts(db))
    for lane in (release.LANE_POST, release.LANE_PRE):
        payload = release.release_payload(job, lane=lane)
        assert payload["output_validation_version"] == 1
        assert payload[publication.REPORT_FIELD]["state"] == "no_assets"
        assert not any("source_asset_publication" in item or "katex_render" in item for item in release.structural_defects(payload))
        missing = copy.deepcopy(payload)
        missing.pop(publication.REPORT_FIELD)
        assert any("source_asset_publication_missing" in item for item in release.structural_defects(missing))
        legacy = copy.deepcopy(missing)
        legacy.pop("output_validation_version")
        legacy.pop("katex_render_validation")
        assert not any("source_asset_publication" in item or "katex_render" in item for item in release.structural_defects(legacy))


def test_unverified_final_image_blocks_database_but_both_workbooks_are_saved(db):
    from app.services import assessment_release_service as service
    from tests.test_mes_release_lifecycle import OWNER, _fresh_release

    def with_image(payload):
        item = payload["candidates"][0]
        item["question"] += " " + wire(url_for(jpeg()))
        item["question_text"] = item["question"]

    release, _, _ = _fresh_release(db, mutate=with_image)
    published = service.publish_release(db, release)
    assert published.diagnostics["readiness"] == service.BLOCKED
    report = published.diagnostics[publication.REPORT_FIELD]
    assert report["state"] == "unverified"
    assert report["source_url_count"] >= 1
    assert published.diagnostics["issues"]["output_validation"]
    assert any("source_asset_publication" in error for error in published.diagnostics["read_back"]["master_errors"])
    directory = Path(published.publication["directory"])
    assert (directory / service.CONCEPTS_FILENAME).is_file()
    assert (directory / service.MASTER_FILENAME).is_file()
    with pytest.raises(service.UploadRefused, match="blocked for database"):
        service.upload_master_to_database(db, published, owner_sub=OWNER)


def test_invalid_katex_in_serialized_workbook_blocks_database_but_preserves_files(db):
    from app.services import assessment_release_service as service
    from app.services import katex_render_validation as math_render
    from tests.test_mes_release_lifecycle import OWNER, _fresh_release

    def with_invalid_math(payload):
        item = payload["candidates"][0]
        item["question"] += r" [Katex]\notAnAegisKaTeXCommand{x}[/Katex]"
        item["question_text"] = item["question"]

    release, _, _ = _fresh_release(db, mutate=with_invalid_math)
    published = service.publish_release(db, release)
    report = published.diagnostics[math_render.REPORT_FIELD]
    assert report["scope"] == "serialized_workbook_cells"
    assert report["status"] == "blocked"
    assert report["engine_version"] == math_render.KATEX_VERSION
    assert published.diagnostics["readiness"] == service.BLOCKED
    assert any("katex" in str(finding.get("code")) for finding in published.diagnostics["issues"]["output_validation"])
    directory = Path(published.publication["directory"])
    assert (directory / service.CONCEPTS_FILENAME).is_file()
    assert (directory / service.MASTER_FILENAME).is_file()
    with pytest.raises(service.UploadRefused, match="blocked for database"):
        service.upload_master_to_database(db, published, owner_sub=OWNER)
