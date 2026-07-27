"""Regression coverage for Mathpix MMD visual-source boundaries."""
from __future__ import annotations

from app.services import generation as g


FIG6_URL = (
    "https://cdn.mathpix.com/cropped/"
    "6607f4a6-cb7c-4963-a6ea-e5e36dc69d32-09.jpg"
    "?height=816&width=1463&top_left_y=1439&top_left_x=136"
)
FIG17_URL = (
    "https://cdn.mathpix.com/cropped/"
    "6607f4a6-cb7c-4963-a6ea-e5e36dc69d32-21.jpg"
    "?height=1157&width=746&top_left_y=1179&top_left_x=1147"
)
FIG18_URL = (
    "https://cdn.mathpix.com/cropped/"
    "6607f4a6-cb7c-4963-a6ea-e5e36dc69d32-22.jpg"
    "?height=614&width=1287&top_left_y=1630&top_left_x=114"
)
FIG19_URL = (
    "https://cdn.mathpix.com/cropped/"
    "6607f4a6-cb7c-4963-a6ea-e5e36dc69d32-23.jpg"
    "?height=1509&width=1204&top_left_y=244&top_left_x=119"
)


def _figure(url: str, caption: str) -> str:
    return (
        "\\begin{figure}\n"
        f"\\includegraphics[alt={{}},max width=\\textwidth]{{{url}}}\n"
        "\\captionsetup{labelformat=empty}\n"
        f"\\caption{{{caption}}}\n"
        "\\end{figure}"
    )


def test_vienna_hub_removes_mathpix_layout_and_keeps_owned_figure():
    caption = (
        "Fig. 6 - The Club of Thinkers, anonymous caricature dating to c. 1820."
    )
    raw = (
        "What is the caricaturist trying to depict?\n\n"
        + _figure(FIG6_URL, caption)
    )
    item = {
        "qid": "QINV-VIENNA",
        "source_kind": "activity",
        "source_label": "Discuss",
        "topic_hint": "The Age of Revolutions: 1830-1848",
        "raw_task": raw,
        "normalized_task": raw,
        "image_urls": [FIG6_URL],
        "_image_captions": {FIG6_URL: caption},
        "_figure_images_resolved": True,
        "requires_visual": True,
    }

    note = g._compact_activity_hub_note(item)

    assert "What is the caricaturist trying to depict?" in note
    assert "\\captionsetup" not in note
    assert "\\caption" not in note
    assert "\\includegraphics" not in note
    assert "\\begin{figure}" not in note
    assert note.count("[img ") == 1
    assert "Fig. 6 - The Club of Thinkers" in note
    assert g.kr.rich_text_issues(note) == []


def test_hubner_task_uses_adjacent_fig18_caption_without_mutating_raw_source():
    sections = [
        {
            "heading": "New words",
            "body": _figure(
                FIG17_URL,
                "Fig. 17 - Germania, Philip Veit, 1848.",
            ),
        },
        {
            "heading": "Activity",
            "body": (
                "With the help of Box 3, interpret Veit's Germania.\n"
                + _figure(
                    FIG18_URL,
                    "Fig. 18 - The fallen Germania, Julius Hübner, 1850.",
                )
            ),
        },
        {
            "heading": "Activity",
            "body": (
                "Describe what you see in Fig. 17. What historical events "
                "could Hübner be referring to in this allegorical vision of "
                "the nation?\n"
                + _figure(
                    FIG19_URL,
                    "Fig. 19 - Germania guarding the Rhine, Lorenz Clasen.",
                )
            ),
        },
    ]
    source_task = (
        "Describe what you see in Fig. 17. What historical events could "
        "Hübner be referring to in this allegorical vision of the nation?"
    )
    items = [{
        "qid": "QINV-HUBNER",
        "source_kind": "exercise",
        "source_label": "Activity",
        "topic_hint": "Visualising the Nation",
        "raw_task": source_task,
        "normalized_task": source_task,
        "_source_section_index": 2,
        "_source_position": 0,
    }]

    attached = g._attach_explicit_figure_images(items, sections)
    inventory = g._enable_source_visual_display_repairs({
        "items": attached,
        "stats": {},
    })
    item = inventory["items"][0]
    rendered = g._inventory_task_text(item)

    # Audit truth remains the exact MMD wording and attachment.
    assert item["raw_task"] == source_task
    assert "Fig. 17" in item["raw_task"]
    assert item["image_urls"] == [FIG17_URL]

    # Teacher-facing truth follows the uniquely matching adjacent Hübner caption.
    repairs = item["_source_visual_reference_repairs"]
    assert repairs[0]["from_figure_id"] == "17"
    assert repairs[0]["to_figure_id"] == "18"
    assert repairs[0]["matched_names"] == ["hubner"]
    assert repairs[0]["url"] == FIG18_URL
    assert "Fig. 18" in rendered
    assert "Fig. 17" not in rendered
    assert FIG18_URL in rendered
    assert "Julius Hübner" in rendered
    assert FIG17_URL not in rendered
    assert FIG19_URL not in rendered
    assert g.kr.rich_text_issues(rendered) == []


def test_named_figure_reference_is_not_changed_when_caption_already_matches():
    sections = [{
        "heading": "Visualising the Nation",
        "body": (
            _figure(
                FIG17_URL,
                "Fig. 17 - Germania, Philip Veit, 1848.",
            )
            + "\n"
            + _figure(
                FIG18_URL,
                "Fig. 18 - The fallen Germania, Julius Hübner, 1850.",
            )
        ),
    }]
    task = "Describe Philip Veit's Germania in Fig. 17."
    item = {
        "qid": "QINV-VEIT",
        "source_kind": "exercise",
        "source_label": "Activity",
        "topic_hint": "Visualising the Nation",
        "raw_task": task,
        "normalized_task": task,
        "_source_section_index": 0,
        "_source_position": 0,
    }

    attached = g._attach_explicit_figure_images([item], sections)[0]
    rendered = g._inventory_task_text(attached)

    assert "_source_visual_reference_repairs" not in attached
    assert "Fig. 17" in rendered
    assert "Fig. 18" not in rendered
    assert FIG17_URL in rendered


def test_corrected_visual_example_passes_rich_text_and_figure_contracts():
    sections = [
        {
            "heading": "Activity",
            "body": _figure(
                FIG18_URL,
                "Fig. 18 - The fallen Germania, Julius Hübner, 1850.",
            ),
        },
        {
            "heading": "Activity",
            "body": (
                "Describe what you see in Fig. 17. What historical events "
                "could Hübner be referring to?\n"
                + _figure(
                    FIG19_URL,
                    "Fig. 19 - Germania guarding the Rhine, Lorenz Clasen.",
                )
            ),
        },
    ]
    source_task = (
        "Describe what you see in Fig. 17. What historical events could "
        "Hübner be referring to?"
    )
    attached = g._attach_explicit_figure_images([{
        "qid": "QINV-HUBNER",
        "source_kind": "exercise",
        "source_label": "Activity",
        "topic_hint": "Visualising the Nation",
        "raw_task": source_task,
        "normalized_task": source_task,
        "_source_section_index": 1,
        "_source_position": 0,
    }], sections)
    inventory = g._enable_source_visual_display_repairs({
        "items": attached,
        "stats": {},
    })
    item = inventory["items"][0]
    example = g._inventory_task_text(item)
    rows = [{
        "topic": "Visualising the Nation",
        "parent_concept": "National Allegories",
        "concept_title": "Female Allegories of National Identity",
        "concept_details": (
            "Description: Nations were represented through female allegories.\n"
            "Achieving Mastery: Interpreting the symbols in a national allegory. "
            "// Types: Type 01: Interpreting a national allegory "
            "Case 01: A named visual source is connected to historical events "
            f"Example 01: {example} // "
            "Misconception/ Error Analysis: Misconceptions: Students may "
            "believe an allegory is a portrait of a real woman.; Error Analysis: "
            "Students may list visible objects without linking them to the "
            "historical argument."
        ),
        "keywords": "allegory, Germania",
    }]

    report = g.cv.validate_concept_rows(
        rows,
        allow_types=True,
        require_culmination=False,
        allow_culmination=False,
        allowed_source_examples=[example],
    )
    fatal_codes = {
        error["code"] for error in report["errors"]
        if error.get("severity") == "error"
    }

    assert "rich_text_format" not in fatal_codes
    assert "figure_reference_without_image" not in fatal_codes
    assert "figure_reference_image_mismatch" not in fatal_codes
