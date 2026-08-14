"""A rejected inventory row is judged by the model, never by its shape.

Deterministic validation can prove a row is unusable — empty, or too short to
render as a public Example. It cannot tell a mangled question apart from a
section banner the extractor mistook for a task, because that depends on what
the book prints, not on the row's shape. The model makes that call against the
source, an independent critic confirms it, and every uncertain path keeps the
row so the run stops rather than silently losing a learner's question.

Live acceptance, Balbharati Std 6 "Three Dimensional Shapes": three runs died
because ``## Practice Set 1.1`` and ``## Exercise 1`` — the outline's two
assessment-kind sections — arrived as task rows with no prompt of their own.
"""
from __future__ import annotations

import pytest

from app.services import generation as g


def _rows():
    items = [
        {
            "qid": "QINV-0026",
            "raw_task": "## Practice Set 1.1",
            "source_label": "Practice Set 1.1",
            "source_kind": "checkpoint_question",
            "_source_section_index": 0,
        },
        {
            "qid": "QINV-0100",
            "raw_task": "Find the volume of the cuboid.",
            "source_label": "Discuss",
            "source_kind": "checkpoint_question",
            "_source_section_index": 0,
        },
    ]
    invalid = [{"index": 0, "qid": "QINV-0026", "reason": "stub_task"}]
    sections = [{
        "heading": "Practice Set 1.1",
        "body": "1) Complete the table below.\n2) Name the solid.",
    }]
    return items, invalid, sections


def _verified_critic(**_kwargs):
    return {"verdict": "verified", "issues": []}


def test_the_model_decides_an_artifact_and_the_critic_confirms_before_a_drop():
    items, invalid, sections = _rows()
    seen: dict = {}

    def provider(**kwargs):
        seen["system"] = kwargs.get("system")
        seen["user"] = kwargs.get("user")
        return {"verdicts": [{
            "qid": "QINV-0026",
            "verdict": "artifact",
            "reason": "names a question collection, asks nothing itself",
        }]}

    kept, dropped = g._adjudicate_invalid_inventory_rows(
        items, invalid, sections=sections,
        provider=provider, critic=_verified_critic,
    )

    assert [item["qid"] for item in kept] == ["QINV-0100"]
    assert [row["qid"] for row in dropped] == ["QINV-0026"]
    # The model is given the surrounding source, not just the row, because the
    # judgment is about what the book prints around it.
    assert "1) Complete the table below." in seen["user"]
    assert "Practice Set 1.1" in seen["user"]


def test_a_real_task_verdict_keeps_the_row_so_the_run_still_stops():
    items, invalid, sections = _rows()

    kept, dropped = g._adjudicate_invalid_inventory_rows(
        items, invalid, sections=sections,
        provider=lambda **_k: {"verdicts": [{
            "qid": "QINV-0026",
            "verdict": "real_task",
            "reason": "the source really asks this; extraction truncated it",
        }]},
        critic=_verified_critic,
    )

    assert len(kept) == 2
    assert dropped == []


def test_a_row_the_model_does_not_rule_on_is_kept():
    """Silence is not permission to drop a question."""
    items, invalid, sections = _rows()

    kept, dropped = g._adjudicate_invalid_inventory_rows(
        items, invalid, sections=sections,
        provider=lambda **_k: {"verdicts": []},
        critic=_verified_critic,
    )

    assert len(kept) == 2
    assert dropped == []


def test_the_critic_can_veto_a_drop():
    items, invalid, sections = _rows()
    critic_calls: list[dict] = []

    def critic(**kwargs):
        critic_calls.append(kwargs)
        return {"verdict": "rejected", "issues": ["that is a real question"]}

    kept, dropped = g._adjudicate_invalid_inventory_rows(
        items, invalid, sections=sections,
        provider=lambda **_k: {"verdicts": [{
            "qid": "QINV-0026", "verdict": "artifact", "reason": "banner",
        }]},
        critic=critic,
    )

    assert len(kept) == 2
    assert dropped == []
    assert critic_calls, "the critic must be consulted before any drop"


@pytest.mark.parametrize("failing", ["provider", "critic"])
def test_a_provider_failure_keeps_every_row(failing: str):
    items, invalid, sections = _rows()

    def boom(**_kwargs):
        raise RuntimeError("provider unavailable")

    artifact = lambda **_k: {"verdicts": [{  # noqa: E731
        "qid": "QINV-0026", "verdict": "artifact", "reason": "banner",
    }]}

    kept, dropped = g._adjudicate_invalid_inventory_rows(
        items, invalid, sections=sections,
        provider=boom if failing == "provider" else artifact,
        critic=boom if failing == "critic" else _verified_critic,
    )

    assert len(kept) == 2
    assert dropped == []


def test_both_prompts_satisfy_json_mode():
    """``_openai_json`` runs in JSON mode, which rejects a request whose
    messages never say "json". A live run lost ten minutes to a 400 here, and
    the failure is invisible in unit tests that stub the provider."""
    for prompt in (
        g._INVALID_ROW_ADJUDICATION_SYSTEM,
        g._INVALID_ROW_CRITIC_SYSTEM,
    ):
        assert "json" in prompt.lower()


def test_nothing_is_judged_by_the_shape_of_the_text():
    """Guards the rule: no regex/threshold may decide this.

    An earlier fix classified 'a task that is only a markdown heading' with a
    regex. That is exactly the deterministic judgment this pipeline must not
    contain — see CLAUDE.md — so the helper must not come back.
    """
    assert not hasattr(g, "_inventory_row_is_heading_only")
    assert not hasattr(g, "_HEADING_ONLY_TASK_RE")
