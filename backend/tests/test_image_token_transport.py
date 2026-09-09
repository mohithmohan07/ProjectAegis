"""Quoted image alt text remains one token through validation and polishing."""
from __future__ import annotations

import copy

import pytest

from app.bulk_import import assessment_workbook
from app.bulk_import import from_workbook_rich_text
from app.services import assessment_master_refiner as master_refiner
from app.services import assessment_release_service
from app.services import generation
from app.services import katex_rules
from app.services import source_visual_contract
from tests import test_assessment_master_refiner as master_fixtures


_URL = (
    "https://projectaegis.fly.dev/source-assets/42/"
    + "a" * 64
    + ".jpg?sig=" + "b" * 40
)
_ALT = "Interval [0, 1] with the endpoint included"
_IMAGE = f'[img src="{_URL}" alt="{_ALT}"]'


@pytest.mark.parametrize("alt", [
    _ALT,
    "Grid cell [A] and nested labels [B [C]] remain visible",
])
def test_valid_quoted_brackets_do_not_create_image_validation_defects(alt):
    # Model-authored canonical tags can contain literal brackets; the wire
    # grammar accepts them even though the image() helper escapes its input.
    image = f'[img src="{_URL}" alt="{alt}"]'

    assert katex_rules.rich_text_issues("Use the supplied figure. " + image) == []


def test_malformed_image_attribute_still_fails_validation():
    malformed = f'[img src="{_URL}" alt"Endpoint included"]'

    issues = katex_rules.rich_text_issues(malformed)

    assert "missing_image_alt" in issues
    assert "noncanonical_image" in issues


def test_source_display_keeps_the_owned_url_without_leaking_the_alt_tail():
    prompt = "Describe the interval in the supplied number line."
    source = prompt + " " + _IMAGE
    item = {
        "qid": "QINV-0042",
        "raw_task": source,
        "normalized_task": source,
        "image_urls": [_URL],
        "_image_captions": {_URL: _ALT},
        "requires_visual": True,
    }
    before = copy.deepcopy(item)

    rendered = source_visual_contract._render_inventory_display(
        generation, item, source,
    )

    tags = list(katex_rules._CANONICAL_IMAGE_TAG_RE.finditer(rendered))
    assert len(tags) == 1
    assert tags[0].group("src") == _URL
    assert "with the endpoint included" in tags[0].group("alt")
    # Strip with the complete canonical wire parser, independently of the
    # permissive parser used by the production source-rendering path.
    assert katex_rules._CANONICAL_IMAGE_TAG_RE.sub("", rendered).strip() == prompt
    assert katex_rules.rich_text_issues(rendered) == []
    assert item == before


def test_example_figure_reference_ignores_a_reference_inside_bracketed_alt():
    existing = f'[img src="{_URL}" alt="Interval [0, 1] from Fig. 99"]'
    source_url = "https://assets.example/figure-one.jpg"
    registry = {
        "1": [{"figure_id": "1", "url": source_url, "caption": "Fig. 1 - Number line"}],
    }

    tags = generation._source_figure_tags_for_example(
        "Describe the interval in Fig. 1. " + existing, registry,
    )

    assert tags == [katex_rules.image(source_url, "Fig. 1 - Number line")]


@pytest.mark.parametrize("candidate_index,field", [
    (0, "answer_explanation"),
    (1, "display_answer"),
])
def test_master_rejects_an_image_alt_tail_change_in_editable_answers(
    candidate_index, field,
):
    original = master_fixtures._payload()["candidates"][candidate_index]
    original[field] += " " + _IMAGE
    checker = master_refiner._response_checker(
        unit_kind="candidate",
        unit_id=original["candidate_id"],
        original=original,
    )

    def response(record):
        return {
            "record_kind": "candidate",
            "row_ref": original["candidate_id"],
            "record": record,
            "rationale": "Polish the surrounding explanation.",
        }

    polished = copy.deepcopy(original)
    polished[field] = "Observe the supplied figure. " + polished[field]
    assert checker(response(polished)) == []

    changed = copy.deepcopy(polished)
    changed[field] = changed[field].replace(
        "with the endpoint included", "with the endpoint excluded",
    )

    assert any(
        "QID/URL/image/KaTeX identity tokens" in defect
        for defect in checker(response(changed))
    )


def test_literal_bracket_alt_and_signed_url_survive_both_serialized_workbooks():
    payload = master_fixtures._payload()
    concept = payload["concept_snapshot"]["topics"][0]["concepts"][0]
    concept["concept_details"] = "Description: " + _IMAGE
    candidate = next(
        row for row in payload["candidates"]
        if row["candidate_id"] == master_fixtures.DESCRIPTIVE_ID
    )
    fields = ("question", "question_text", "display_answer", "answer_explanation")
    for field in fields:
        candidate[field] += " " + _IMAGE

    snapshot = assessment_release_service.snapshot_from_staged_release(payload)
    outputs = assessment_workbook.build_dual_output(
        snapshot, master_fixtures._LEGACY_PROFILE,
    )

    assert outputs["valid"], outputs["manifest"]["read_back"]
    concept_rows = assessment_workbook.parse_workbook(
        outputs["concepts_xlsx"],
    )["sheets"]["Objective"]["rows"]
    assert any(
        from_workbook_rich_text(row["concept_details"]) == concept["concept_details"]
        for row in concept_rows
    )
    master_rows = assessment_workbook.parse_workbook(
        outputs["master_xlsx"],
    )["sheets"]["Descriptive"]["rows"]
    exported = next(
        row for row in master_rows
        if row.get("question_label") == candidate["question_label"]
    )
    assert from_workbook_rich_text(exported["concept_details"]) == concept["concept_details"]
    for field in fields:
        restored = from_workbook_rich_text(exported[field])
        # The multipart question_text projection may append the recorded
        # child prompts, but every authored image token stays exact/in order.
        assert candidate[field] in restored
        assert [
            match.group(0)
            for match in katex_rules._CANONICAL_IMAGE_TAG_RE.finditer(restored)
        ] == [
            match.group(0)
            for match in katex_rules._CANONICAL_IMAGE_TAG_RE.finditer(candidate[field])
        ]
        assert _IMAGE in restored
        assert _URL in restored
