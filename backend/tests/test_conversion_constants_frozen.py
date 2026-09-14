"""The constants that decide whether an already-converted PDF is read again.

The owner's instruction of 14 September 2026, given after two chapters were
lost and re-reading every converted PDF was proposed as the cure:

    "No no wait. I don't want it to re read. It doesn't make sense."

That is a standing constraint, not a one-off. Every material in
``_bundle_cache_key`` is a switch that re-buys the vision transcription of
every PDF ever converted, and none of them announces itself as one — they read
like ordinary version strings. This module is the announcement.

Four separate pieces of work each wanted their own "no constant moved" test
with the literals pinned inline. Written four times they collide the moment one
is legitimately bumped, so they are written once, here.

WHEN A TEST IN THIS FILE FAILS, the fix is almost never to update the literal.
Read which key the constant belongs to first:

* ``_bundle_cache_key``  — the sealed complete page bundle. Moving anything
  here re-reads EVERY already-converted PDF from page one. Do not move these
  without the owner saying so in as many words.
* ``_batch_cache_key``   — unsealed page batches only. Sealed bundles replay
  free; work in flight re-pays. This is the affordable gate.
* ``_outline_cache_key`` — the chapter outline decision, page-image-free.
"""
from __future__ import annotations

import hashlib

import pytest

from app import config
from app.services import canonical_source_phase221_fallback as fallback
from app.services import canonical_source_phase22 as phase22
from app.services import canonical_source_phase3 as phase3
from app.services import reviewed_file_input


_REREAD = (
    "This constant is material in _bundle_cache_key: changing it re-reads "
    "EVERY already-converted PDF from page one, at full vision-transcription "
    "price. The owner forbade that on 14 September 2026. If the owner has "
    "since decided otherwise, update this literal in the SAME commit and say "
    "the cost in the PR body."
)
_BATCH_ONLY = (
    "This constant is material in _batch_cache_key but NOT _bundle_cache_key: "
    "sealed bundles replay free and only unsealed batches re-pay. That is the "
    "affordable gate — but it is still a real cost, so move it deliberately."
)
_DECISION = (
    "This is a decision/compiler identity. It does not re-read a PDF, but it "
    "does re-ask decisions and can make sealed artifacts read as stale. Move "
    "it only with a stated reason."
)


# --------------------------------------------------------------------------- #
# The bundle key — the expensive ones
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,expected", [
    ("FALLBACK_VERSION", "2.6.0"),
    ("FALLBACK_COMPILER", "gpt-pdf-to-acsd-2"),
    ("INGESTION_CONTRACT_VERSION", "supported-text-atoms-2"),
])
def test_a_bundle_key_constant_has_not_moved(name, expected):
    assert getattr(fallback, name) == expected, f"{name}: {_REREAD}"


def test_the_bundle_key_is_built_from_exactly_these_materials():
    """A NEW material is as expensive as changing an old one.

    The literals above are only a guard while the key is made of them. If
    someone folds, say, the render zoom or a new contract token into this
    function, every pinned literal still passes and every PDF still re-reads.
    """
    import inspect

    source = inspect.getsource(fallback._bundle_cache_key_for_contract)
    for material in (
        "fallback_version", "FALLBACK_COMPILER", "ingestion_contract",
        "config.OPENAI_MODEL", "pdf_sha256", "full-verified-bundle",
        "_routing_cache_parts",
    ):
        assert material in source, (
            f"{material} left _bundle_cache_key_for_contract; the digest test "
            "below no longer proves what it claims"
        )
    # Nothing else. Counting the appended parts catches an addition that the
    # membership check above cannot see.
    assert source.count("parts.append") + source.count("parts.extend") == 2, (
        "a material was added to _bundle_cache_key. " + _REREAD
    )


# --------------------------------------------------------------------------- #
# The batch key and the outline key — real, but affordable
# --------------------------------------------------------------------------- #

def test_the_page_extraction_decision_version_is_batch_only():
    assert fallback.PAGE_EXTRACTION_DECISION_VERSION == (
        "page-extraction-decision-3"
    ), f"PAGE_EXTRACTION_DECISION_VERSION: {_BATCH_ONLY}"


def test_the_decision_version_is_absent_from_the_bundle_key():
    """This is the property that makes it the affordable gate.

    ``_bundle_cache_key_for_contract`` takes no ``decision_version`` parameter
    at all, which is what lets a page-extraction change re-ask unsealed batches
    while sealed bundles replay for nothing. If that ever stops being true, a
    change believed to be cheap becomes a full re-read.
    """
    import inspect

    signature = inspect.signature(fallback._bundle_cache_key_for_contract)
    assert "decision_version" not in signature.parameters, (
        "the bundle key grew a decision_version; the cheap gate is gone. "
        + _REREAD
    )


@pytest.mark.parametrize("name,expected", [
    ("PAGE_ACSD_SCHEMA_VERSION", "1.3.0"),
    ("OUTLINE_VERSION", "chapter-outline-10"),
    ("OUTLINE_DECISION_VERSION", "chapter-outline-decision-12"),
    ("RENDER_VERSION", "task-cues-verbatim-full-table-assets-2"),
])
def test_a_conversion_identity_has_not_moved(name, expected):
    assert getattr(fallback, name) == expected, f"{name}: {_DECISION}"


def test_the_other_lanes_identities_have_not_moved():
    assert phase22.ADJUDICATION_VERSION == "2.2.1", _DECISION
    assert reviewed_file_input.VERSION == (
        "independent-reviewed-file-2026-09-11-v1"
    ), _DECISION
    assert phase3.SCHEMA_VERSION == "3.0.0", _DECISION
    assert phase3.COMPILER_VERSION == "phase-3-semantic-graph-2", _DECISION
    assert phase3._SOURCE_REVIEW_VERSION == "phase3-source-review-1", _DECISION


# --------------------------------------------------------------------------- #
# The digests — what the constants actually add up to
# --------------------------------------------------------------------------- #

def _digest(parts: list[str]) -> str:
    return hashlib.sha256("␟".join(parts).encode()).hexdigest()


def _pages():
    return [
        fallback.PdfPage(
            page_id=f"PDF-PAGE-{index:04d}", page_number=index, text="",
            image_data_url="", width=612.0, height=792.0,
        )
        for index in (1, 2)
    ]


def test_the_model_routing_policy_is_bundle_key_material(monkeypatch):
    """The least obvious re-read switch in the codebase.

    ``_routing_cache_parts()`` appends the whole bound routing profile to the
    bundle key, so changing which MODEL reads pages — or its reasoning effort,
    or the policy version stamp — re-reads every already-converted PDF just as
    surely as bumping FALLBACK_VERSION. Q50 moved every stage to one model and
    Q46 moved them all before that; the next such instruction carries this
    cost, and nothing in the routing module says so.
    """
    baseline = fallback._bundle_cache_key("pdf-sha")

    from app.services import model_provider

    parts = fallback._routing_cache_parts()
    assert parts, (
        "no routing profile is bound, so this test proves nothing; it was "
        "written against a profile bound at import"
    )
    assert "owner-stage-model-routing-2026-09-11-v3" in parts[0], (
        "the routing policy version moved. " + _REREAD
    )
    assert '"page_transcription"' in parts[0], (
        "page_transcription left the narrow purposes; the page reader's route "
        "changed. " + _REREAD
    )

    monkeypatch.setattr(fallback, "_routing_cache_parts", lambda: ["other"])
    assert fallback._bundle_cache_key("pdf-sha") != baseline, (
        "the routing profile must be bundle-key material; if it stops being "
        "so, two different readers can share one sealed bundle"
    )
    assert model_provider is not None


def test_the_three_cache_keys_are_byte_exact(monkeypatch):
    """Belt and braces over the literals: this catches a reordering, a
    changed separator or a new material, none of which move a constant."""
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5.6-luna", raising=False)
    sha = "pdf-sha"
    routing = fallback._routing_cache_parts()
    prefix = [
        fallback.FALLBACK_VERSION, fallback.FALLBACK_COMPILER,
        fallback.INGESTION_CONTRACT_VERSION,
    ]

    expected_bundle = _digest(
        prefix + [config.OPENAI_MODEL, sha, "full-verified-bundle"] + routing
    )
    expected_batch = _digest(
        prefix + [fallback.PAGE_EXTRACTION_DECISION_VERSION,
                  config.OPENAI_MODEL, sha,
                  "PDF-PAGE-0001,PDF-PAGE-0002"] + routing
    )
    expected_outline = _digest([
        fallback.FALLBACK_VERSION, fallback.INGESTION_CONTRACT_VERSION,
        fallback.OUTLINE_VERSION, fallback.OUTLINE_DECISION_VERSION,
        fallback._outline_prompt_sha256(), config.OPENAI_MODEL, sha,
        "chapter-outline",
    ] + routing)

    assert fallback._bundle_cache_key(sha) == expected_bundle, _REREAD
    assert fallback._batch_cache_key_from_sha(sha, _pages()) == expected_batch, (
        _BATCH_ONLY
    )
    assert fallback._outline_cache_key(sha) == expected_outline, _DECISION
