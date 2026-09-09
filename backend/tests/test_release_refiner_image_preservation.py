"""Concept polish must not erase or detach source figures in editable prose."""
from __future__ import annotations

import copy

import pytest

from app.services import release_refiner
from app.services.phase3 import kernel
from tests import test_release_refiner as fixtures


_URL = "https://projectaegis.fly.dev/api/source-assets/42/" + "a" * 64 + ".jpg"
_IMAGE = f'[img src="{_URL}" alt="Revolutionary symbol and its labels"]'
_SECOND_IMAGE = _IMAGE.replace("a" * 64, "b" * 64).replace(
    "Revolutionary symbol and its labels", "A second revolutionary symbol"
)


def _row_with_image(section):
    row = fixtures._rows()[0]
    marker = (
        "Description: "
        if section == "description"
        else "// Misconception/ Error Analysis: "
    )
    row["concept_details"] = row["concept_details"].replace(
        marker, marker + _IMAGE + " "
    )
    return row


@pytest.mark.parametrize("section", ["description", "learner_analysis"])
@pytest.mark.parametrize("mutation", ["remove", "change_url", "change_alt", "duplicate"])
def test_refiner_discards_visual_identity_drift_without_blocking_other_rows(
    monkeypatch, section, mutation,
):
    monkeypatch.setenv(release_refiner.SCOPE_ENV, "all")
    sibling = fixtures._rows()[0]
    sibling["concept_title"] = "An Independent Concept"
    original = [_row_with_image(section), sibling]
    replacement = {
        "remove": "",
        "change_url": _IMAGE.replace("a" * 64, "c" * 64),
        "change_alt": _IMAGE.replace("its labels", "invented labels"),
        "duplicate": _IMAGE + " " + _IMAGE,
    }[mutation]

    def edit(details):
        return details.replace(_IMAGE, replacement).replace(
            "Learners see how", "Learners discover how"
        )

    refined, diff, flags = release_refiner.refine_release(
        original,
        metadata=fixtures._METADATA,
        provider=fixtures._Provider(details_override=edit),
        store=kernel.DecisionStore(),
    )

    assert refined[0] == original[0]
    assert "Learners discover how" in refined[1]["concept_details"]
    assert diff["changes"]
    assert any("identity drift" in flag and "image attachments" in flag for flag in flags)


def test_visual_order_and_section_attachment_are_identity():
    row = _row_with_image("description")
    row["concept_details"] = row["concept_details"].replace(
        _IMAGE, _IMAGE + " " + _SECOND_IMAGE
    )
    before = row["concept_details"]
    reordered = before.replace(_IMAGE + " " + _SECOND_IMAGE, _SECOND_IMAGE + " " + _IMAGE)
    moved = before.replace(_IMAGE, "").replace(
        "// Misconception/ Error Analysis: ",
        "// Misconception/ Error Analysis: " + _IMAGE + " ",
    )
    for changed in (reordered, moved):
        defects = release_refiner._identity_violations(row, changed, row["keywords"])
        assert any("image attachments changed in section" in defect for defect in defects)


def test_a_keyword_field_cannot_receive_or_lose_a_figure():
    row = _row_with_image("description")
    defects = release_refiner._identity_violations(
        row, row["concept_details"].replace(_IMAGE, ""), row["keywords"] + " " + _IMAGE
    )
    assert "image attachments changed in keywords" in defects
    row["keywords"] += " " + _IMAGE
    defects = release_refiner._identity_violations(
        row, row["concept_details"], row["keywords"].replace(_IMAGE, "")
    )
    assert "image attachments changed in keywords" in defects


def test_image_alt_text_with_brackets_is_preserved_as_a_complete_token():
    row = _row_with_image("description")
    row["concept_details"] = row["concept_details"].replace(
        "Revolutionary symbol and its labels", "Interval [0, 1] with the endpoint included"
    )
    changed = row["concept_details"].replace("endpoint included", "endpoint excluded")
    assert any("image attachments" in defect for defect in release_refiner._identity_violations(
        row, changed, row["keywords"]
    ))


@pytest.mark.parametrize("section", ["description", "learner_analysis"])
@pytest.mark.parametrize("output_kind,pre_post", [
    ("concepts_release", "Post"),
    ("pre_concepts_release", "Pre"),
])
def test_prose_polish_keeps_source_figures_in_both_learning_outputs(
    monkeypatch, output_kind, pre_post, section,
):
    monkeypatch.setenv(release_refiner.SCOPE_ENV, "all")
    original = [_row_with_image(section)]
    requests = []

    def provider(payload):
        requests.append(copy.deepcopy(payload))
        return fixtures._Provider(replace=("Learners see how", "Learners discover how"))(payload)

    refined, diff, flags = release_refiner.refine_release(
        original,
        metadata={**fixtures._METADATA, "pre_post": pre_post},
        provider=provider,
        output_kind=output_kind,
        store=kernel.DecisionStore(),
    )
    assert _IMAGE in refined[0]["concept_details"]
    assert "Learners discover how" in refined[0]["concept_details"]
    assert diff["changes"]
    assert not any("identity drift" in flag for flag in flags)
    assert "full source URL and alt text" in requests[0]["rules"]


def test_normalization_that_drops_a_visual_rolls_the_refinement_back(monkeypatch):
    monkeypatch.setenv(release_refiner.SCOPE_ENV, "all")
    original = [_row_with_image("description")]
    real_normalize = release_refiner._deposit_deterministic_pipeline

    def lossy_normalize(rows, metadata):
        normalized = real_normalize(rows, metadata)
        normalized[0]["concept_details"] = normalized[0]["concept_details"].replace(_IMAGE, "")
        return normalized

    monkeypatch.setattr(release_refiner, "_deposit_deterministic_pipeline", lossy_normalize)
    refined, diff, flags = release_refiner.refine_release(
        original,
        metadata=fixtures._METADATA,
        provider=fixtures._Provider(replace=("see how", "discover how")),
        store=kernel.DecisionStore(),
    )
    assert refined == original
    assert not diff["changes"]
    assert any("normalization drifted identity" in flag for flag in flags)


def test_visual_identity_check_still_allows_authorized_math_formatting_repair():
    row = _row_with_image("description")
    row["concept_details"] = row["concept_details"].replace(
        "\nAchieving Mastery:", r" Compare \(x=2\)." + "\nAchieving Mastery:"
    )
    corrected = row["concept_details"].replace(r"\(x=2\)", "[Katex]x=2[/Katex]")
    assert not release_refiner._identity_violations(row, corrected, row["keywords"])
