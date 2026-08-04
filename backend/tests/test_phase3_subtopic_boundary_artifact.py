"""A subtopic split from its own figure by a page boundary is not evidence.

Job 21 carried an Activity about Italian unification labelled
``The Strange Case of Britain``.  Nothing was miscomputed: the NCERT page is
two-column, so the Activity box referring to Fig. 14(a)/(b) linearizes just
after the ``4.3 The Strange Case of Britain`` subheading.  Both the task
offsets and the subtopic boundary faithfully describe that stream.

The subtopic is still not evidence of what the task is about, and it must not
be asserted.  Which subtopic the task really belongs to is a semantic
judgement this deterministic layer may not make, so the label is cleared while
the main topic and source provenance stay untouched.
"""
from __future__ import annotations

import copy

from app.services import canonical_source_phase3 as phase3


def _graph() -> dict:
    return {
        "source_contract_hash": "SOURCE-CONTRACT-1",
        "topics": [
            {"topic_id": "TOPIC-0004", "title": "The Making of Germany and Italy"},
        ],
        "subtopics": [
            {
                "subtopic_id": "TOPIC-0004-SUB-002",
                "topic_id": "TOPIC-0004",
                "title": "Italy Unified",
                "source_start": 42872,
                "source_end": 45420,
            },
            {
                "subtopic_id": "TOPIC-0004-SUB-003",
                "topic_id": "TOPIC-0004",
                "title": "The Strange Case of Britain",
                "source_start": 45420,
                "source_end": 50331,
            },
        ],
        "figures": [
            # Fig. 14(a) ends one character before the subheading begins.
            {"figure_id": "FIG-00014", "source_start": 45118, "source_end": 45419},
            {"figure_id": "FIG-00015", "source_start": 45910, "source_end": 46296},
            # A figure from much earlier in the chapter.
            {"figure_id": "FIG-00005", "source_start": 20100, "source_end": 20400},
        ],
        "tasks": [{
            "qid": "QINV-0011",
            "task_id": "TASK-00011",
            "topic_id": "TOPIC-0004",
            "subtopic_id": "TOPIC-0004-SUB-003",
            "figure_ids": ["FIG-00014", "FIG-00015"],
            "source_start": 45624,
            "source_end": 45909,
        }],
    }


def _inventory(qid: str = "QINV-0011", **overrides) -> dict:
    item = {"qid": qid, "raw_task": "Look at Fig. 14(a). ..."}
    item.update(overrides)
    return {"items": [item]}


def test_a_subtopic_split_from_its_own_figure_is_cleared():
    out = phase3.annotate_inventory(_inventory(), _graph())
    item = out["items"][0]

    assert item["_semantic_subtopic_id"] == ""
    assert "_semantic_subtopic_title" not in item
    assert item["_semantic_subtopic_boundary_artifact"] == "FIG-00014"
    # Ownership and provenance are untouched.
    assert item["_semantic_topic_id"] == "TOPIC-0004"
    assert item["source_location_topic_id"] == "TOPIC-0004"


def test_a_split_leaf_inherits_the_cleared_label_too():
    inventory = {"items": [
        {"qid": "QINV-0011.1", "parent_qid": "QINV-0011", "raw_task": "Fig 14(a)?"},
        {"qid": "QINV-0011.2", "parent_qid": "QINV-0011", "raw_task": "Fig 14(b)?"},
    ]}

    out = phase3.annotate_inventory(inventory, _graph())

    assert [row["_semantic_subtopic_id"] for row in out["items"]] == ["", ""]
    assert all(
        row["_semantic_topic_id"] == "TOPIC-0004" for row in out["items"]
    )


def test_a_distant_back_reference_keeps_its_subtopic():
    # "Compare with Fig. 5 from earlier" must not strip a correct label.
    graph = copy.deepcopy(_graph())
    graph["tasks"][0]["figure_ids"] = ["FIG-00005"]

    item = phase3.annotate_inventory(_inventory(), graph)["items"][0]

    assert item["_semantic_subtopic_id"] == "TOPIC-0004-SUB-003"
    assert item["_semantic_subtopic_title"] == "The Strange Case of Britain"
    assert "_semantic_subtopic_boundary_artifact" not in item


def test_a_task_with_no_figures_keeps_its_subtopic():
    graph = copy.deepcopy(_graph())
    graph["tasks"][0].pop("figure_ids")

    item = phase3.annotate_inventory(_inventory(), graph)["items"][0]

    assert item["_semantic_subtopic_id"] == "TOPIC-0004-SUB-003"
    assert "_semantic_subtopic_boundary_artifact" not in item


def test_a_figure_inside_the_subtopic_is_not_an_artifact():
    graph = copy.deepcopy(_graph())
    graph["tasks"][0]["figure_ids"] = ["FIG-00015"]

    item = phase3.annotate_inventory(_inventory(), graph)["items"][0]

    assert item["_semantic_subtopic_id"] == "TOPIC-0004-SUB-003"
    assert "_semantic_subtopic_boundary_artifact" not in item
