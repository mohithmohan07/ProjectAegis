"""Step 12 — directed fault injection at the four real seams (spec D5).

No product code is edited; frozen files are exercised, never modified.
Each test injects at a documented seam through the established fixtures
and pins the CURRENT contract. The two formerly pinned residues — the
deterministic asset-retry failure and the sealed-bundle tamper-trust
asymmetry — have been repaired and their pins FLIPPED: a degenerate crop
is now a flagged degradation whose conversion (and retry) completes, and
a tampered seal is rejected by its recorded page digest and re-extracted.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import fitz
import pytest

from app import models
from app.services import canonical_source_phase2 as phase2
from app.services import canonical_source_phase221_fallback as fallback
from app.services import generation
from tests.conftest import convert_concept_upload, stream_events, stream_result


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 70), "1 Number Patterns", fontsize=18)
    page.insert_text((50, 130), "A sequence follows a rule.", fontsize=12)
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 70), "Activity", fontsize=15)
    page.insert_text((50, 120), "Describe the pattern.", fontsize=12)
    doc.save(path)
    doc.close()


def _block(order, kind, text, bbox, **over):
    row = {
        "reading_order": order, "kind": kind, "bbox": bbox, "text": text,
        "heading_level": 1 if kind == "heading" else 0, "source_label": "",
        "latex": "", "table_rows": [], "linked_visual_orders": [],
        "linked_context_orders": [], "caption": "", "confidence": 0.99,
    }
    row.update(over)
    return row


def _pages_payload(pages, *, figure_bbox=None):
    rows = []
    for page in pages:
        if page.page_number == 1:
            blocks = [
                _block(1, "heading", "1 Number Patterns", [70, 50, 720, 120]),
                _block(2, "paragraph", "A sequence follows a rule.",
                       [70, 130, 800, 210]),
            ]
        else:
            blocks = [
                _block(1, "task", "Describe the pattern.",
                       [70, 60, 850, 180], source_label="Activity"),
            ]
            if figure_bbox is not None:
                blocks.append(_block(
                    2, "figure", "", list(figure_bbox),
                    caption="Fig. 1 - degenerate crop.",
                ))
        rows.append({
            "page_id": page.page_id,
            "page_number": page.page_number,
            "confidence": 0.99,
            "blocks": blocks,
        })
    return rows


def _verification(pages):
    return {
        "verdict": "verified",
        "approved_page_ids": [page.page_id for page in pages],
        "rejected_page_ids": [],
        "confidence": 0.99,
        "issues": [],
    }


# ---------------------------------------------------------------------------
# Seam A — API dissent ships flagged, never silently and never over-fatally
# ---------------------------------------------------------------------------

def test_dissent_with_candidate_pages_ships_accepted_with_review_flags(
    tmp_path, monkeypatch
):
    """First-ever pin on fallback's accepted_with_review_flags branch: a
    review_required result WITH candidate pages completes flagged — every
    page kept, the dissent reason on each page, nothing lost (R4/Q10)."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def dissenting(pages):
        return {
            "status": "review_required",
            "reason": "verifier saw a numbering mismatch on page 2",
            "pages": _pages_payload(pages),
            "verification": _verification(pages) | {
                "verdict": "needs_correction",
            },
        }

    result = fallback.extract_pdf_to_page_acsd(pdf, provider=dissenting)
    assert {row["page_number"] for row in result["pages"]} == {1, 2}
    for row in result["pages"]:
        assert any(
            "verification unresolved after bounded correction" in flag
            and "numbering mismatch" in flag
            for flag in row.get("review_flags") or []
        )
    assert all(
        decision["status"] == "accepted_with_review_flags"
        for decision in result["batches"]
    )


def test_dissent_with_no_candidate_at_all_stops_the_source_pipeline(
    tmp_path, monkeypatch
):
    """The intentional fail-closed twin: no candidate pages = nothing to
    ship flagged, so the conversion stops with the named reason."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def unusable(_pages):
        return {"status": "review_required", "reason": "unreadable scan"}

    with pytest.raises(ValueError, match="requires review"):
        fallback.extract_pdf_to_page_acsd(pdf, provider=unusable)


# ---------------------------------------------------------------------------
# Seam B — quota failure
# ---------------------------------------------------------------------------

_QUOTA_MESSAGE = (
    "OpenAI generation failed: quota exhausted (insufficient_quota). "
    "Injected by test_fault_injection."
)


def test_quota_death_inside_generation_becomes_a_failure_release(
    client, db, first_chapter, monkeypatch
):
    """Quota death mid-generation is contained: the run ends as a recorded
    release-shaped failure, never a silent crash, with the quota reason
    preserved as a release issue. (Checkpoint retention is a property of
    runs that reached a checkpointed stage; this drive dies before one, so
    it is deliberately not asserted here — audit F17.)"""
    files = {"file": ("quota.txt", io.BytesIO(
        b"## Quota Topic\nQuota concept one\nQuota concept two"
    ), "text/plain")}
    job = client.post(
        "/build-concepts/post-learning/uploads", files=files
    ).json()
    convert_concept_upload(client, job["id"])

    def die(*args, **kwargs):
        raise RuntimeError(_QUOTA_MESSAGE)

    monkeypatch.setattr(generation, "concepts_from_mmd", die)
    events = stream_events(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]},
    ))
    db.expire_all()
    row = db.get(models.UploadJob, job["id"])
    # The containment contract produced a failure RELEASE: nothing
    # uploaded, the run recorded, and the quota reason preserved verbatim
    # as a release issue — never swallowed.
    assert row.status == "released"
    assert "Released 0 concept row(s)" in str(row.detail or "")
    from app.services import build_concepts_release as release

    payload = release.release_payload(row) or {}
    issue_text = json.dumps(payload.get("issues") or [], ensure_ascii=False)
    assert "insufficient_quota" in issue_text
    assert not any(event.get("type") == "error" for event in events) or (
        "insufficient_quota" in json.dumps(events)
    )


def test_quota_death_pre_spend_is_a_true_halt_with_no_release(
    client, db, first_chapter, monkeypatch
):
    files = {"file": ("quota2.txt", io.BytesIO(
        b"## Quota Topic B\nQuota concept three\nQuota concept four"
    ), "text/plain")}
    job = client.post(
        "/build-concepts/post-learning/uploads", files=files
    ).json()
    convert_concept_upload(client, job["id"])

    def die(*args, **kwargs):
        raise RuntimeError(_QUOTA_MESSAGE)

    monkeypatch.setattr(phase2, "prepare_job_context", die)
    events = stream_events(client.post(
        f"/build-concepts/post-learning/uploads/{job['id']}/generate",
        json={"target_chapter_id": first_chapter["id"]},
    ))
    assert any(event.get("type") == "error" for event in events)
    db.expire_all()
    row = db.get(models.UploadJob, job["id"])
    assert row.status == "converted"
    assert str(row.detail or "").startswith("Generation failed:")
    assert "insufficient_quota" in str(row.detail or "")


def test_verified_batches_replay_free_after_a_quota_shaped_death(
    tmp_path, monkeypatch
):
    """The free-resume property, PARTIAL-death shape (audit F17): with two
    one-page batches, batch 1 verifies and batch 2 dies on quota. The
    retry must replay batch 1 from cache (no provider call for it) and pay
    only for batch 2."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(fallback, "_batch_size", lambda: 1)
    monkeypatch.setattr(fallback, "_parallel_batches", lambda: 1)
    calls = {"n": 0}

    def verified_then_quota(pages):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError(_QUOTA_MESSAGE)
        return {
            "status": "verified",
            "pages": _pages_payload(pages),
            "verification": _verification(pages),
        }

    with pytest.raises(RuntimeError, match="insufficient_quota"):
        fallback.extract_pdf_to_page_acsd(pdf, provider=verified_then_quota)
    assert calls["n"] == 2, "batch 1 verified, batch 2 died on quota"

    retry_calls = {"n": 0}

    def verified(pages):
        retry_calls["n"] += 1
        return {
            "status": "verified",
            "pages": _pages_payload(pages),
            "verification": _verification(pages),
        }

    result = fallback.extract_pdf_to_page_acsd(pdf, provider=verified)
    assert retry_calls["n"] == 1, (
        "the retry pays ONLY for the batch the quota death interrupted"
    )
    assert {row["page_number"] for row in result["pages"]} == {1, 2}


# ---------------------------------------------------------------------------
# Seam C — asset failure (residue repaired: flagged degradation, run completes)
# ---------------------------------------------------------------------------

def test_degenerate_bbox_is_a_flagged_degradation_and_retry_completes(
    tmp_path, monkeypatch
):
    """FLIPPED PIN (map §6 residue repaired, Q13/R4): a figure crop below
    the 8pt floor is one recorded, flagged degradation — the conversion
    COMPLETES. The figure keeps its identity, bbox, caption, and page
    evidence with no invented asset URL; the named flag rides the page
    bundle's review_flags; every other block ships; and the sealed-evidence
    retry completes IDENTICALLY (still deterministic, now successful)."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", tmp_path / "cache")

    def with_degenerate_figure(pages):
        return {
            "status": "verified",
            "pages": _pages_payload(pages, figure_bbox=[100, 100, 104, 103]),
            "verification": _verification(pages),
        }

    def run():
        return fallback.reconstruct_pdf_to_acsd(
            pdf,
            job_id=91,
            artifact_dir=tmp_path / "artifacts",
            fallback_reason=["pdf_source"],
            provider=with_degenerate_figure,
        )

    def page_flags(result):
        return [
            flag
            for page in result["page_acsd"]["pages"]
            for flag in page.get("review_flags") or []
        ]

    first = run()

    flags = page_flags(first)
    assert any(
        "figure asset could not be materialized" in flag
        and "figure bbox is too small" in flag
        for flag in flags
    ), "the degradation must be named on the page's review flags"
    figure = next(
        block
        for page in first["page_acsd"]["pages"]
        for block in page["blocks"]
        if block.get("kind") == "figure"
    )
    # Identity, bbox, caption, and page evidence kept; no URL invented.
    assert figure["bbox"] == [100, 100, 104, 103]
    assert figure["caption"] == "Fig. 1 - degenerate crop."
    assert not figure.get("asset_url")
    assert not figure.get("asset_filename")
    # Everything else in the conversion is intact.
    assert {row["page_number"] for row in first["page_acsd"]["pages"]} == {1, 2}
    assert "Describe the pattern." in first["mmd_text"]
    assert first["canonical"]["tasks"], "the learner task must survive"
    # The flag reaches the PUBLISHED page bundle (the release surface).
    persisted = json.loads(
        (tmp_path / "artifacts" / fallback.GPT_PAGE_ACSD_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert any(
        "figure asset could not be materialized" in flag
        for page in persisted["pages"]
        for flag in page.get("review_flags") or []
    )

    # The retry replays the sealed evidence and completes the same way:
    # deterministic (same flags, same source) and successful.
    second = run()
    assert page_flags(second) == flags
    assert second["mmd_text"] == first["mmd_text"]


def test_page_outside_the_pdf_is_still_fatal_not_a_flag(
    tmp_path, monkeypatch
):
    """The per-figure containment must not swallow corrupt input: a page
    reference outside the PDF is a broken artifact, not one bad crop, and
    the deterministic gate still refuses it outright."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    page_acsd = {
        "pages": [{
            "page_number": 99,
            "blocks": [{"kind": "figure", "bbox": [100, 100, 400, 400]}],
        }]
    }
    with pytest.raises(ValueError, match="outside the PDF"):
        fallback.materialize_visual_assets(
            pdf, page_acsd, job_id=91, artifact_dir=tmp_path / "artifacts"
        )


# ---------------------------------------------------------------------------
# Seam D — cache alteration
# ---------------------------------------------------------------------------

def _counting_verified(calls):
    def provider(pages):
        calls["n"] += 1
        return {
            "status": "verified",
            "pages": _pages_payload(pages),
            "verification": _verification(pages),
        }
    return provider


def test_corrupt_batch_cache_is_a_silent_miss_never_served(
    tmp_path, monkeypatch
):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(fallback, "_CACHE_DIR", cache_dir)
    calls = {"n": 0}
    fallback.extract_pdf_to_page_acsd(pdf, provider=_counting_verified(calls))
    baseline = calls["n"]
    assert baseline >= 1

    for path in cache_dir.glob("*.json"):
        path.write_text("{ this is not json", encoding="utf-8")
    fallback.extract_pdf_to_page_acsd(pdf, provider=_counting_verified(calls))
    assert calls["n"] == baseline * 2, "corruption must re-extract, not serve"


def test_wrong_status_cache_entry_is_never_served(tmp_path, monkeypatch):
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(fallback, "_CACHE_DIR", cache_dir)
    calls = {"n": 0}
    fallback.extract_pdf_to_page_acsd(pdf, provider=_counting_verified(calls))
    baseline = calls["n"]

    for path in cache_dir.glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        value["status"] = "review_required"
        path.write_text(json.dumps(value), encoding="utf-8")
    fallback.extract_pdf_to_page_acsd(pdf, provider=_counting_verified(calls))
    assert calls["n"] == baseline * 2


def test_tampered_sealed_bundle_is_rejected_and_reextracted(
    tmp_path, monkeypatch
):
    """FLIPPED PIN (map §7 asymmetry repaired): the seal now records a
    canonical digest of its pages (result_sha256) and reuse requires it,
    so well-formed tampered page content — pdf_sha256 and page count
    preserved — is a cache MISS that re-extracts, never a replay. The
    per-batch caches are cleared alongside the tamper so the re-extraction
    is observable as a real provider call (re-bill) instead of a free
    per-batch replay; either way the tampered text never ships."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("AEGIS_PUBLIC_BASE_URL", "https://aegis.example")
    monkeypatch.setenv("AEGIS_SOURCE_ASSET_SECRET", "test-secret")
    monkeypatch.setattr(fallback, "_CACHE_DIR", cache_dir)

    calls = {"n": 0}
    fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=92,
        artifact_dir=tmp_path / "artifacts",
        fallback_reason=["pdf_source"],
        provider=_counting_verified(calls),
    )
    baseline = calls["n"]
    assert baseline >= 1

    pdf_sha = fallback._pdf_sha256(pdf)
    bundle_path = fallback._batch_cache_path(
        fallback._bundle_cache_key(pdf_sha)
    )
    sealed = json.loads(bundle_path.read_text(encoding="utf-8"))
    tampered_line = "TAMPERED SOURCE LINE 9931"
    page = sealed["result"]["pages"][0]
    page["blocks"][1]["text"] = tampered_line
    bundle_path.write_text(
        json.dumps(sealed, ensure_ascii=False), encoding="utf-8"
    )
    for path in cache_dir.glob("*.json"):
        if path != bundle_path:
            path.unlink()

    replayed = fallback.reconstruct_pdf_to_acsd(
        pdf,
        job_id=92,
        artifact_dir=tmp_path / "artifacts2",
        fallback_reason=["pdf_source"],
        provider=_counting_verified(calls),
    )
    assert calls["n"] == baseline * 2, (
        "a tampered seal must re-extract (re-bill), never replay"
    )
    assert tampered_line not in json.dumps(replayed, ensure_ascii=False), (
        "tampered cache content must never reach the result"
    )
    # The rebuilt bundle resealed with a digest that matches its pages.
    resealed = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert resealed["result_sha256"] == fallback._bundle_pages_sha256(
        resealed["result"]
    )


def test_legacy_seal_without_digest_is_a_miss_that_reseals(
    tmp_path, monkeypatch
):
    """A seal written before result_sha256 existed is treated as a MISS —
    re-derived (free: the per-batch caches still hold their verified
    judgments) and resealed WITH the digest, never replayed on trust."""
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf)
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(fallback, "_CACHE_DIR", cache_dir)

    calls = {"n": 0}
    fallback.extract_pdf_to_page_acsd(pdf, provider=_counting_verified(calls))
    baseline = calls["n"]

    bundle_path = fallback._batch_cache_path(
        fallback._bundle_cache_key(fallback._pdf_sha256(pdf))
    )
    legacy = json.loads(bundle_path.read_text(encoding="utf-8"))
    legacy.pop("result_sha256")
    bundle_path.write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )

    fallback.extract_pdf_to_page_acsd(pdf, provider=_counting_verified(calls))
    assert calls["n"] == baseline, (
        "the seal-format upgrade must not re-bill verified batches"
    )
    resealed = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert resealed.get("result_sha256"), "the miss must reseal with a digest"
