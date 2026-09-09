"""Picture-bank stitching pinned (owner ruling, 2026-08-21: "Build it").

A non-Objective question whose body carries two or more canonical image
tags ships ONE stitched, labelled grid figure — hosted from the app's own
signed ``/source-assets`` route — instead of a link per picture; Objective
items keep one image per option. A stitch that cannot complete leaves the
text untouched and rides the candidate as a named review flag.
"""
from __future__ import annotations

import copy
import hashlib
import io

import pytest

from app.services import katex_rules as kr
from app.services import question_image_grid as grid
from app.services import source_asset_store

from tests.test_assessment_release_run import (
    OWNER,
    _authorities,
    _chapter_with_concepts,
    _decision_context,
    _make_job,
)

_BASE = "https://aegis-test.example"


def _png_bytes(color: str) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(output, format="PNG")
    return output.getvalue()


def _fake_downloader(url: str) -> bytes:
    return _png_bytes("red" if "one" in url else "blue")


def _two_tag_text() -> str:
    return (
        "Classify each object. "
        + kr.image("https://cdn.example.com/one.png", "Drum")
        + " "
        + kr.image("https://cdn.example.com/two.png", "Carrom board")
    )


# --------------------------------------------------------------------------- #
# The stitcher unit
# --------------------------------------------------------------------------- #

def test_a_single_image_is_left_alone():
    text = "Look at this. " + kr.image(
        "https://cdn.example.com/one.png", "Drum")
    new_text, record = grid.consolidate_images(text, job_id=7)
    assert new_text == text
    assert record is None


def test_two_tags_become_one_stitched_labelled_figure(monkeypatch):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", _BASE)
    cache: dict = {}
    new_text, record = grid.consolidate_images(
        _two_tag_text(), job_id=7, downloader=_fake_downloader, cache=cache,
    )
    tags = grid.image_tags(new_text)
    assert len(tags) == 1, "one combined tag replaces the bank"
    assert tags[0]["src"].startswith(f"{_BASE}/source-assets/7/")
    assert "sig=" in tags[0]["src"]
    assert "Drum" in tags[0]["alt"] and "Carrom board" in tags[0]["alt"]
    assert new_text.startswith("Classify each object.")
    assert record["combined_image_url"] == tags[0]["src"]
    assert [image["src"] for image in record["source_images"]] == [
        "https://cdn.example.com/one.png",
        "https://cdn.example.com/two.png",
    ]
    # The stitched bytes are pinned content-addressed and re-readable.
    filename = tags[0]["src"].split("?")[0].rsplit("/", 1)[-1]
    assert source_asset_store.stored_asset_path(filename).is_file()

    # Deterministic: the same bank stitches to the same asset, cached or not.
    again_text, again = grid.consolidate_images(
        _two_tag_text(), job_id=7, downloader=_fake_downloader,
    )
    assert again["combined_image_url"] == record["combined_image_url"]
    assert again_text == new_text


def test_a_failed_download_changes_nothing_and_is_named(monkeypatch):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", _BASE)

    def broken(url: str) -> bytes:
        raise ValueError("boom")

    text = _two_tag_text()
    new_text, record = grid.consolidate_images(
        text, job_id=7, downloader=broken,
    )
    assert new_text == text, "never half-stitched"
    assert "error" in record and "boom" in record["error"]
    assert "combined_image_url" not in record


def _pinned_jpeg(monkeypatch, tmp_path, color="red"):
    from PIL import Image
    from app.services import canonical_source_phase221_fallback as fallback

    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", _BASE)
    monkeypatch.setattr(source_asset_store.config, "DATA_DIR", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, format="JPEG")
    data = buffer.getvalue()
    filename = hashlib.sha256(data).hexdigest() + ".jpg"
    url = fallback.asset_url(7, filename)
    source_asset_store.pin_asset(data, job_id=7, asset_url=url)
    return url, data, source_asset_store.stored_asset_path(filename)


def test_stitch_reads_verified_local_pixels_without_public_http(monkeypatch, tmp_path):
    first_url, first_bytes, _ = _pinned_jpeg(monkeypatch, tmp_path)
    second_url, _, _ = _pinned_jpeg(monkeypatch, tmp_path, color="blue")

    def refuse(*args, **kwargs):
        pytest.fail("Pinned source bytes must not be downloaded through Fly")

    monkeypatch.setattr(grid.urllib.request, "urlopen", refuse)
    assert grid._download(first_url) == first_bytes
    text = "Compare. " + kr.image(first_url, "First") + " " + kr.image(second_url, "Second")
    result, record = grid.consolidate_images(text, job_id=7)
    assert len(grid.image_tags(result)) == 1
    assert "error" not in record
    assert record["combined_image_url"].startswith(_BASE + "/source-assets/7/")


def test_wrong_local_or_remote_bytes_never_impersonate_source_hash(monkeypatch, tmp_path):
    url, _data, path = _pinned_jpeg(monkeypatch, tmp_path)
    path.write_bytes(b"corrupt local copy")
    calls = []

    def wrong_remote(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(_png_bytes("blue"))

    monkeypatch.setattr(grid.urllib.request, "urlopen", wrong_remote)
    with pytest.raises(ValueError, match="does not match its content hash"):
        grid._download(url)
    assert calls == [url]


def test_missing_local_copy_can_recover_matching_public_bytes(monkeypatch, tmp_path):
    url, data, path = _pinned_jpeg(monkeypatch, tmp_path)
    path.unlink()
    monkeypatch.setattr(grid.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    assert grid._download(url) == data


def test_external_lookalike_url_cannot_resolve_local_pixels(monkeypatch, tmp_path):
    url, _data, _path = _pinned_jpeg(monkeypatch, tmp_path)
    external_url = url.replace(_BASE, "https://external.example.org")
    external_data = _png_bytes("blue")
    calls = []

    def remote(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(external_data)

    monkeypatch.setattr(grid.urllib.request, "urlopen", remote)
    assert grid._download(external_url) == external_data
    assert calls == [external_url]


def test_same_images_with_different_labels_do_not_reuse_cached_pixels(monkeypatch):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", _BASE)
    cache = {}
    first_text = _two_tag_text()
    second_text = first_text.replace('alt="Drum"', 'alt="Cylinder"')
    _, first = grid.consolidate_images(first_text, job_id=7, downloader=_fake_downloader, cache=cache)
    _, second = grid.consolidate_images(second_text, job_id=7, downloader=_fake_downloader, cache=cache)
    assert first["combined_image_url"] != second["combined_image_url"]
    assert len(cache) == 2

    def refuse(_url):
        pytest.fail("The identical labelled bank should reuse the completed stitch")

    _, repeated = grid.consolidate_images(second_text, job_id=7, downloader=refuse, cache=cache)
    assert repeated["combined_image_url"] == second["combined_image_url"]


# --------------------------------------------------------------------------- #
# The release wiring
# --------------------------------------------------------------------------- #

def _authorities_with_image_banks(db, chapter):
    authorities, calls = _authorities(db, chapter)
    plain_materialize = authorities["materialize"][0]

    def materialize_with_images(payload):
        response = copy.deepcopy(plain_materialize(payload))
        bank = (
            " "
            + kr.image("https://cdn.example.com/one.png", "Drum")
            + " "
            + kr.image("https://cdn.example.com/two.png", "Carrom board")
        )
        response["question"] = str(response["question"]) + bank
        return response

    authorities["materialize"] = (
        materialize_with_images, authorities["materialize"][1],
    )
    return authorities, calls


def test_descriptive_banks_stitch_and_objective_options_stay(db, monkeypatch):
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", _BASE)
    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities_with_image_banks(db, chapter)

    released = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        image_downloader=_fake_downloader,
        **_decision_context(),
    )
    by_kind = {
        candidate["sheet_kind"]: candidate
        for candidate in released.payload["candidates"]
    }
    descriptive = by_kind["descriptive"]
    tags = grid.image_tags(descriptive["question"])
    assert len(tags) == 1, "the picture bank became one stitched figure"
    assert tags[0]["src"].startswith(f"{_BASE}/source-assets/")
    audit = descriptive["_aegis_image_consolidation"]
    assert {entry["field"] for entry in audit} == {
        "question", "question_text"}
    assert all("error" not in entry for entry in audit)
    assert "assessment_image_grid_review" not in (
        descriptive.get("flags") or [])

    objective = by_kind["objective"]
    assert len(grid.image_tags(objective["question"])) == 2, (
        "Objective items are exempt — options keep their own images"
    )
    assert "_aegis_image_consolidation" not in objective


def test_a_failed_stitch_flags_the_candidate_and_ships_the_master(db):
    from app.services import assessment_release_run as run

    chapter = _chapter_with_concepts(db)
    job = _make_job(db, chapter)
    authorities, _ = _authorities_with_image_banks(db, chapter)

    def broken(url: str) -> bytes:
        raise ValueError("network down")

    released = run.run_release_for_job(
        db,
        job.id,
        owner_sub=OWNER,
        authorities=authorities,
        image_downloader=broken,
        **_decision_context(),
    )
    by_kind = {
        candidate["sheet_kind"]: candidate
        for candidate in released.payload["candidates"]
    }
    descriptive = by_kind["descriptive"]
    assert len(grid.image_tags(descriptive["question"])) == 2, (
        "the original tags survive untouched"
    )
    assert "assessment_image_grid_review" in descriptive["flags"]
    audit = descriptive["_aegis_image_consolidation"]
    assert all("network down" in entry["error"] for entry in audit)
    assert len(released.payload["candidates"]) == 2, "the Master still ships"
