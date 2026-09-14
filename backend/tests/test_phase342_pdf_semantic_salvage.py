"""Regression coverage for Phase 3.4.2 PDF semantic salvage.

Also covers the correction record the salvage contract hands the batch
assembly, because the two halves are one claim: a conversion that paid for
extra repair passes has to be able to say so afterwards. Every attempt here
is a real provider call that the owner was billed for, so an attempt that is
recorded nowhere is a charge with no explanation beside it.
"""
from __future__ import annotations

import copy
import secrets

import pytest

from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase221_fallback as page_acsd
from app.services import canonical_source_phase342_pdf_semantic_salvage_contract as phase342


def _page(number: int, text: str) -> page_acsd.PdfPage:
    return page_acsd.PdfPage(
        page_id=f"PDF-PAGE-{number:04d}",
        page_number=number,
        text=text,
        image_data_url="data:image/jpeg;base64,AA==",
        width=600.0,
        height=800.0,
    )


def _block(text: str, *, order: int = 1) -> dict:
    return {
        "reading_order": order,
        "kind": "paragraph",
        "bbox": [50, 80, 950, 240],
        "text": text,
        "heading_level": 0,
        "source_label": "",
        "latex": "",
        "table_rows": [],
        "linked_visual_orders": [],
        "linked_context_orders": [],
        "caption": "",
        "confidence": 0.999,
    }


def test_semantic_salvage_still_requires_deterministic_and_independent_verification(
    monkeypatch,
):
    page = _page(1, "Visible paragraph from the original page.")
    calls: list[str] = []

    def fake_call(**kwargs):
        name = kwargs["response_schema"]["name"]
        calls.append(name)
        if "verify" in name:
            return {
                "verdict": "verified",
                "approved_page_ids": [page.page_id],
                "rejected_page_ids": [],
                "confidence": 0.999,
                "issues": [],
            }
        return {
            "pages": [{
                "page_id": page.page_id,
                "confidence": 0.999,
                "blocks": [_block(page.text)],
            }]
        }

    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)
    result = phase342._semantic_salvage(
        [page],
        {
            "status": "review_required",
            "reason": "the earlier bbox geometry did not converge",
            "correction_history": [],
        },
    )

    assert result["status"] == "verified"
    assert result["recovered_by"] == "phase3.4.2-semantic-salvage"
    assert result["pages"][0]["blocks"][0]["text"] == page.text
    assert result["correction_history"][-1]["stage"] == "semantic_salvage"
    assert calls == [
        "aegis_pdf_page_acsd_extract",
        "aegis_pdf_page_acsd_verify",
    ]


def test_unresolved_multi_page_batch_isolated_instead_of_stopping(monkeypatch):
    pages = [
        _page(1, "First visible page paragraph."),
        _page(2, "Second visible page paragraph."),
    ]
    extraction_batch_sizes: list[int] = []

    def extraction_page_ids(schema: dict) -> list[str]:
        return list(
            schema["schema"]["properties"]["pages"]["items"][
                "properties"
            ]["page_id"]["enum"]
        )

    def verification_page_ids(schema: dict) -> list[str]:
        return list(
            schema["schema"]["properties"]["approved_page_ids"][
                "items"
            ]["enum"]
        )

    def fake_call(**kwargs):
        schema = kwargs["response_schema"]
        name = schema["name"]
        if "verify" in name:
            ids = verification_page_ids(schema)
            return {
                "verdict": "verified",
                "approved_page_ids": ids,
                "rejected_page_ids": [],
                "confidence": 0.999,
                "issues": [],
            }
        ids = extraction_page_ids(schema)
        extraction_batch_sizes.append(len(ids))
        if len(ids) > 1:
            # Keep the page identities exact but force deterministic
            # rejection: a missing blocks array is genuinely unusable
            # (structural quirks are normalized, never rejected, now).
            return {
                "pages": [
                    {
                        "page_id": page_id,
                        "confidence": 0.999,
                        "blocks": None,
                    }
                    for page_id in ids
                ]
            }
        source = next(page.text for page in pages if page.page_id == ids[0])
        return {
            "pages": [{
                "page_id": ids[0],
                "confidence": 0.999,
                "blocks": [_block(source)],
            }]
        }

    monkeypatch.delenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", raising=False)
    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)

    result = page_acsd.extract_batch_via_openai(pages)

    assert result["status"] == "verified"
    assert result["recovered_by"] == "phase3.4.2-page-isolation"
    assert [row["page_number"] for row in result["pages"]] == [1, 2]
    assert [
        row["blocks"][0]["text"] for row in result["pages"]
    ] == [page.text for page in pages]
    assert extraction_batch_sizes.count(2) == 4  # initial + three corrections
    assert extraction_batch_sizes[-2:] == [1, 1]
    # The four group extractions above are three recorded correction attempts
    # that the run was billed for. They used to be dropped on the floor here:
    # the merge published the per-page histories only, so a batch that cost
    # eight model calls returned an empty correction record and read exactly
    # like one that converged on the first try.
    assert [
        (entry["origin"], entry["stage"])
        for entry in result["correction_history"]
    ] == [("group", "deterministic_validation")] * 3
    assert [entry["attempt"] for entry in result["correction_history"]] == [
        1, 2, 3,
    ]
    # Both pages verified on their own; nothing shipped on a flag.
    assert result["flagged_page_ids"] == []


def test_default_pdf_correction_budget_is_three_but_operator_override_wins(
    monkeypatch,
):
    monkeypatch.delenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", raising=False)
    assert page_acsd._max_correction_attempts() == 3

    monkeypatch.setenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", "1")
    assert page_acsd._max_correction_attempts() == 1


def _extraction_page_ids(schema: dict) -> list[str]:
    return list(
        schema["schema"]["properties"]["pages"]["items"]["properties"][
            "page_id"
        ]["enum"]
    )


def _verification_page_ids(schema: dict) -> list[str]:
    return list(
        schema["schema"]["properties"]["approved_page_ids"]["items"]["enum"]
    )


def _approving_verification(ids: list[str]) -> dict:
    return {
        "verdict": "verified",
        "approved_page_ids": ids,
        "rejected_page_ids": [],
        "confidence": 0.999,
        "issues": [],
    }


def _unusable_group(ids: list[str]) -> dict:
    """A group extraction the deterministic validator must reject.

    Page identities stay exact so the rejection is about content, not about
    the batch contract; a missing blocks array is the one genuinely unusable
    shape (structural quirks are normalized, never rejected).
    """
    return {
        "pages": [
            {"page_id": page_id, "confidence": 0.999, "blocks": None}
            for page_id in ids
        ]
    }


def test_each_isolated_page_keeps_the_attempts_it_bought_under_its_own_name(
    monkeypatch,
):
    """A merged correction record has to say which page spent what.

    Page isolation runs a whole correction loop per page. Copied into one
    flat list the entries are indistinguishable, so an operator reading the
    record cannot tell a batch whose every page needed repair from a batch
    where one page did. The group's own attempts lead, then each page's,
    each carrying the name of the unit that recorded it.
    """
    pages = [
        _page(1, "First visible page paragraph."),
        _page(2, "Second visible page paragraph."),
    ]
    single_attempts: dict[str, int] = {}

    def fake_call(**kwargs):
        schema = kwargs["response_schema"]
        if "verify" in schema["name"]:
            return _approving_verification(_verification_page_ids(schema))
        ids = _extraction_page_ids(schema)
        if len(ids) > 1:
            return _unusable_group(ids)
        seen = single_attempts.get(ids[0], 0)
        single_attempts[ids[0]] = seen + 1
        if ids[0] == pages[1].page_id and seen == 0:
            # One deterministic rejection for the second page only.
            return {"pages": None}
        source = next(page.text for page in pages if page.page_id == ids[0])
        return {
            "pages": [{
                "page_id": ids[0],
                "confidence": 0.999,
                "blocks": [_block(source)],
            }]
        }

    monkeypatch.delenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", raising=False)
    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)

    result = page_acsd.extract_batch_via_openai(pages)

    assert result["status"] == "verified"
    assert [
        (entry["origin"], entry["stage"], entry["attempt"])
        for entry in result["correction_history"]
    ] == [
        ("group", "deterministic_validation", 1),
        ("group", "deterministic_validation", 2),
        ("group", "deterministic_validation", 3),
        ("PDF-PAGE-0002", "deterministic_validation", 1),
    ]
    assert result["flagged_page_ids"] == []


def test_two_pages_failing_the_same_way_keep_both_attempts(monkeypatch):
    """The anti-de-duplication pin.

    The deterministic validator's refusals are fixed sentences, so two pages
    that fail the same way produce byte-identical ``(attempt, stage, reason)``
    triples. Both were separately paid for. Any collapse keyed on that triple
    — the obvious tidy-up — silently deletes a real retry and makes a
    two-page repair look like a one-page one.
    """
    pages = [
        _page(1, "First visible page paragraph."),
        _page(2, "Second visible page paragraph."),
    ]
    single_attempts: dict[str, int] = {}

    def fake_call(**kwargs):
        schema = kwargs["response_schema"]
        if "verify" in schema["name"]:
            return _approving_verification(_verification_page_ids(schema))
        ids = _extraction_page_ids(schema)
        if len(ids) > 1:
            return _unusable_group(ids)
        seen = single_attempts.get(ids[0], 0)
        single_attempts[ids[0]] = seen + 1
        if seen == 0:
            # Identical refusal for both pages: no page id is named in it.
            return {"pages": None}
        source = next(page.text for page in pages if page.page_id == ids[0])
        return {
            "pages": [{
                "page_id": ids[0],
                "confidence": 0.999,
                "blocks": [_block(source)],
            }]
        }

    monkeypatch.delenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", raising=False)
    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)

    result = page_acsd.extract_batch_via_openai(pages)

    per_page = [
        (entry["attempt"], entry["stage"], entry["reason"])
        for entry in result["correction_history"]
        if entry["origin"] != "group"
    ]
    assert len(per_page) == 2
    assert per_page[0] == per_page[1]
    assert per_page[0] == (1, "deterministic_validation",
                           "extractor returned no pages array")
    assert [
        entry["origin"] for entry in result["correction_history"]
        if entry["origin"] != "group"
    ] == ["PDF-PAGE-0001", "PDF-PAGE-0002"]


def test_a_page_that_only_ships_under_a_review_flag_is_named(monkeypatch):
    """``status`` alone cannot tell a flagged batch from a clean one.

    Page isolation returns ``verified`` for the whole batch even when a page
    reached that state only because the review-flag branch carried it. The
    flag is written into the page row, but nothing at the batch level named
    the page, so the batch record claimed a clean conversion.
    """
    pages = [
        _page(1, "First visible page paragraph."),
        _page(2, "Second visible page paragraph."),
    ]

    def fake_call(**kwargs):
        schema = kwargs["response_schema"]
        if "verify" in schema["name"]:
            ids = _verification_page_ids(schema)
            if pages[1].page_id in ids:
                return {
                    "verdict": "rejected",
                    "approved_page_ids": [],
                    "rejected_page_ids": [pages[1].page_id],
                    "confidence": 0.5,
                    "issues": ["the second column reads as one paragraph"],
                }
            return _approving_verification(ids)
        ids = _extraction_page_ids(schema)
        if len(ids) > 1:
            return _unusable_group(ids)
        source = next(page.text for page in pages if page.page_id == ids[0])
        return {
            "pages": [{
                "page_id": ids[0],
                "confidence": 0.999,
                "blocks": [_block(source)],
            }]
        }

    monkeypatch.delenv("AEGIS_GPT_PDF_ACSD_MAX_CORRECTIONS", raising=False)
    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)

    result = page_acsd.extract_batch_via_openai(pages)

    assert result["status"] == "verified"
    assert result["flagged_page_ids"] == ["PDF-PAGE-0002"]
    assert [row["page_number"] for row in result["pages"]] == [1, 2]
    assert any(
        "verification unresolved" in flag
        for flag in result["pages"][1].get("review_flags") or []
    )


def test_semantic_salvage_separates_the_group_attempts_from_its_own(
    monkeypatch,
):
    """The extra paid pass has to be visible as an extra paid pass.

    ``_semantic_salvage`` inherits the ordinary correction loop's history and
    appends one entry for the call it just bought. Flat, that entry is
    indistinguishable from the loop's, and the whole point of this contract
    is that it spends beyond the bounded budget.
    """
    page = _page(1, "Visible paragraph from the original page.")

    def fake_call(**kwargs):
        name = kwargs["response_schema"]["name"]
        if "verify" in name:
            return _approving_verification([page.page_id])
        return {
            "pages": [{
                "page_id": page.page_id,
                "confidence": 0.999,
                "blocks": [_block(page.text)],
            }]
        }

    monkeypatch.setattr(phase22, "_openai_multimodal_json", fake_call)
    previous = {
        "status": "review_required",
        "reason": "the earlier bbox geometry did not converge",
        "correction_history": [
            {"attempt": 1, "stage": "deterministic_validation",
             "reason": "blocks overlap"},
        ],
    }
    result = phase342._semantic_salvage([page], previous)

    assert [
        (entry["origin"], entry["stage"])
        for entry in result["correction_history"]
    ] == [
        ("group", "deterministic_validation"),
        ("salvage", "semantic_salvage"),
    ]
    # The caller's own list is never rewritten in place.
    assert previous["correction_history"] == [
        {"attempt": 1, "stage": "deterministic_validation",
         "reason": "blocks overlap"},
    ]


def test_the_collector_reads_two_carrier_levels_and_mutates_nothing():
    """What a failed batch paid for lives inside its carriers, not on top.

    An isolation refusal keeps the group attempt inside ``batch_result`` and
    the page's inside ``page_result``; a salvage refusal keeps its input
    inside ``previous_result``. Reading only the top level reports zero
    attempts for a batch that made eight calls. Two levels is the whole
    shape those contracts build, and the bound keeps a hand-edited cache
    entry from recursing without end.
    """
    too_deep = {"correction_history": [
        {"attempt": 1, "stage": "third_level", "reason": "not collected"},
    ]}
    result = {
        "correction_history": [
            {"attempt": 1, "stage": "top", "reason": "t"},
        ],
        # A carrier that is not a result at all is not a result.
        "previous_result": "verification did not converge",
        "page_result": {
            "correction_history": [
                {"attempt": 1, "stage": "page", "reason": "p"},
                "not a recorded attempt",
            ],
            "previous_result": {
                "correction_history": [
                    {"attempt": 1, "stage": "salvage", "reason": "s"},
                ],
                "previous_result": too_deep,
            },
        },
        "batch_result": {
            "correction_history": [
                {"attempt": 1, "stage": "batch", "reason": "b"},
            ],
        },
    }
    before = copy.deepcopy(result)

    collected = page_acsd._collect_correction_history(result)

    assert [entry["stage"] for entry in collected] == [
        "top", "page", "salvage", "batch",
    ]
    assert page_acsd._collect_correction_history("not a result") == []
    # Mutating the collected copies cannot reach back into the result.
    for entry in collected:
        entry["reason"] = "rewritten"
    assert result == before


# --------------------------------------------------------------------------- #
# The batch record the assembly writes into the published bundle
# --------------------------------------------------------------------------- #

def _lane_pages(start: int, end: int) -> list[page_acsd.PdfPage]:
    return [_page(number, f"page {number} text")
            for number in range(start, end + 1)]


def _lane_rows() -> list[dict]:
    return [
        {"page_id": "PDF-PAGE-0001", "page_number": 1, "blocks": []},
        {"page_id": "PDF-PAGE-0002", "page_number": 2, "blocks": []},
    ]


@pytest.fixture()
def pdf_lane(monkeypatch, tmp_path):
    """A two-page PDF whose page transcription the test supplies directly."""
    sha = secrets.token_hex(32)
    monkeypatch.setattr(page_acsd, "_pdf_page_count", lambda _path: 2)
    monkeypatch.setattr(page_acsd, "_pdf_sha256", lambda _path: sha)
    monkeypatch.setattr(page_acsd, "_batch_size", lambda: 2)
    monkeypatch.setattr(page_acsd, "_CACHE_DIR", tmp_path / "pdf-acsd-cache")
    monkeypatch.setattr(
        page_acsd,
        "collect_pdf_pages",
        lambda _path, *, start_page=1, end_page=None: _lane_pages(
            start_page, end_page or 2
        ),
    )
    logs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        page_acsd.progress,
        "log",
        lambda message, **kwargs: logs.append(
            (str(message), str(kwargs.get("level") or "info"))
        ),
    )
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-fake")
    return {"sha": sha, "path": path, "logs": logs}


def test_a_first_pass_batch_records_the_empty_correction_shape(pdf_lane):
    """A missing key and "nothing went wrong" must never look the same.

    The five accounting keys are always written. If they appeared only when
    something was repaired, every bundle sealed before this change would read
    as a clean conversion, and a reader would have to know the deploy date to
    interpret the record at all.
    """
    def provider(_batch):
        return {"status": "verified", "pages": copy.deepcopy(_lane_rows())}

    bundle = page_acsd.extract_pdf_to_page_acsd(
        pdf_lane["path"], provider=provider
    )

    assert bundle["batches"] == [{
        "batch": 1,
        "page_ids": ["PDF-PAGE-0001", "PDF-PAGE-0002"],
        "cache": "miss",
        "status": "verified",
        "correction_history": [],
        "correction_attempts": 0,
        "unresolved_reason": "",
        "recovered_by": "",
        "flagged_page_ids": [],
    }]


def test_a_salvaged_batch_row_says_what_it_cost_and_who_recovered_it(pdf_lane):
    """The isolation lane's own record has to survive into the bundle.

    ``status`` stays ``verified`` for a batch every page of which was
    salvaged, so without these keys the published artifact cannot tell that
    apart from a batch that converged on the first call — and the flagged
    page is named nowhere above the page row.
    """
    history = [
        {"attempt": 1, "stage": "deterministic_validation",
         "reason": "PDF-PAGE-0002 blocks are missing", "origin": "group"},
        {"attempt": 1, "stage": "semantic_salvage",
         "reason": "geometry did not converge", "origin": "PDF-PAGE-0002"},
    ]

    def provider(_batch):
        return {
            "status": "verified",
            "pages": copy.deepcopy(_lane_rows()),
            "correction_history": copy.deepcopy(history),
            "flagged_page_ids": ["PDF-PAGE-0002"],
            "recovered_by": "phase3.4.2-page-isolation",
        }

    bundle = page_acsd.extract_pdf_to_page_acsd(
        pdf_lane["path"], provider=provider
    )
    row = bundle["batches"][0]

    assert row["status"] == "verified"
    assert row["correction_history"] == history
    assert row["correction_attempts"] == 2
    assert row["recovered_by"] == "phase3.4.2-page-isolation"
    assert row["flagged_page_ids"] == ["PDF-PAGE-0002"]
    assert row["unresolved_reason"] == ""


def test_a_flagged_batch_row_collects_the_attempts_nested_in_its_carriers(
    pdf_lane,
):
    """A batch that ships under review flags carries its history nested.

    The refusal it came from keeps the earlier attempts in ``page_result`` /
    ``batch_result`` rather than at the top, so the row has to collect
    through them or it reports zero attempts for a batch that made several
    calls — and the reason it shipped flagged has to survive untruncated.
    """
    reason = "the verifier and the extractor disagree about column order"

    def provider(_batch):
        return {
            "status": "review_required",
            "reason": reason,
            "pages": copy.deepcopy(_lane_rows()),
            "page_result": {
                "correction_history": [
                    {"attempt": 1, "stage": "independent_verification",
                     "reason": reason},
                ],
            },
            "batch_result": {
                "correction_history": [
                    {"attempt": 1, "stage": "deterministic_validation",
                     "reason": "extractor returned no pages array"},
                ],
            },
        }

    bundle = page_acsd.extract_pdf_to_page_acsd(
        pdf_lane["path"], provider=provider
    )
    row = bundle["batches"][0]

    assert row["status"] == "accepted_with_review_flags"
    assert [entry["stage"] for entry in row["correction_history"]] == [
        "independent_verification", "deterministic_validation",
    ]
    assert row["correction_attempts"] == 2
    assert row["unresolved_reason"] == reason
    assert row["flagged_page_ids"] == []
    # The page rows still carry the flag the run produced, unchanged.
    assert all(
        any("verification unresolved" in flag for flag in row_.get("review_flags") or [])
        for row_ in bundle["pages"]
    )


def test_a_batch_with_no_candidate_names_its_whole_reason_before_stopping(
    pdf_lane,
):
    """The one hard stop left in this lane must not lose its own diagnosis.

    The raise is the only record today, and every layer above reshapes and
    truncates it; the correction attempts behind it exist nowhere else once
    this frame unwinds. An operator asked to fix an unreadable scan needs the
    whole reason, not its first 300 characters.
    """
    reason = "the scan is unreadable: " + "detail " * 200

    def provider(_batch):
        return {
            "status": "review_required",
            "reason": reason,
            "batch_result": {
                "correction_history": [
                    {"attempt": 1, "stage": "deterministic_validation",
                     "reason": "PDF-PAGE-0001 blocks are missing"},
                ],
            },
        }

    with pytest.raises(ValueError, match="requires review"):
        page_acsd.extract_pdf_to_page_acsd(
            pdf_lane["path"], provider=provider
        )

    errors = [message for message, level in pdf_lane["logs"]
              if level == "error"]
    assert len(errors) == 1
    assert reason in errors[0]
    assert "PDF-PAGE-0001 blocks are missing" in errors[0]


def test_a_bundle_sealed_with_four_key_batch_rows_still_replays_free(pdf_lane):
    """Adding keys to the batch record must not re-read a converted PDF.

    ``batches`` is not part of the seal digest, which covers the verified
    pages only. A bundle sealed before these keys existed therefore has to
    come back untouched and without a single provider call — the owner's
    standing constraint of 14 September 2026.
    """
    provider_calls = 0

    def provider(_batch):
        nonlocal provider_calls
        provider_calls += 1
        return {"status": "verified", "pages": copy.deepcopy(_lane_rows())}

    fresh = page_acsd.extract_pdf_to_page_acsd(
        pdf_lane["path"], provider=provider
    )
    assert provider_calls == 1

    # Re-seal the same bundle exactly as a pre-change run would have left it.
    legacy = copy.deepcopy(fresh)
    legacy["batches"] = [
        {key: row[key] for key in ("batch", "page_ids", "cache", "status")}
        for row in fresh["batches"]
    ]
    page_acsd._write_verified_bundle_cache(pdf_lane["sha"], legacy)

    first = page_acsd.extract_pdf_to_page_acsd(
        pdf_lane["path"], provider=provider
    )
    second = page_acsd.extract_pdf_to_page_acsd(
        pdf_lane["path"], provider=provider
    )

    assert provider_calls == 1
    assert second == first
    assert first["batches"] == legacy["batches"]
    assert sorted(first["batches"][0]) == [
        "batch", "cache", "page_ids", "status",
    ]
