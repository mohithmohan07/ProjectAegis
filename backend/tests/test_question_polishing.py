"""Pass 4 — Question Polishing (docs/build-concepts-manual-process.md, Step 4).

The pass's two invariants, tested from both sides:

* The source audit copy is sacred: ``raw_task`` / ``normalized_task`` are
  never modified, so every deterministic anchor and match key downstream
  keeps working. Polished wording lives in new fields only.
* The polished wording is what ships: ``generation._inventory_task_text`` —
  the one function mining's deterministic backfill copies Example wording
  from — presents ``polished_task`` when it exists, and is byte-identical
  pass-through when it does not (hub rows, pre-polishing checkpoints).
"""
from __future__ import annotations

import json
from types import ModuleType

import pytest

from app import config
from app.services import generation, question_polishing, question_polishing_contract

META = {"subject": "History", "board": "CBSE", "grade": "10",
        "chapter_title": "Nationalism in Europe"}


def _item(qid: str, task: str, **extra) -> dict:
    return {
        "qid": qid, "raw_task": task, "normalized_task": task,
        "source_kind": "exercise", "options": [], "image_urls": [],
        **extra,
    }


LOOK_AT_FIGURE = (
    "Look at the figure once again and guess why the artist has portrayed "
    "Germania with a broken chain at her feet."
)


@pytest.fixture(autouse=True)
def _isolated_polishing_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "use_live_generation", lambda: True)
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()
    yield
    with question_polishing._memory_lock:
        question_polishing._memory_cache.clear()


def _api_polish(system, user, **kwargs):
    payload = json.loads(user)
    items = []
    for question in payload["questions"]:
        if question["qid"] == "QINV-0001":
            items.append({
                "qid": "QINV-0001",
                "polished_task": (
                    "The illustration provided shows the artist's portrayal "
                    "of Germania. Explain why she is portrayed with a broken "
                    "chain at her feet."
                ),
                "fragments": [],
                "note": "page-relative reference removed",
            })
        elif question["qid"] == "QINV-0002":
            items.append({
                "qid": "QINV-0002",
                "polished_task": (
                    "Describe the zollverein's economic role and explain how "
                    "the Frankfurt Parliament's failure shaped unification."
                ),
                "fragments": [
                    {"polished_task": (
                        "Describe the economic role played by the zollverein "
                        "in binding the German states."
                    ), "reason": "economic nationalism concept"},
                    {"polished_task": (
                        "Explain how the failure of the Frankfurt Parliament "
                        "shaped the course of German unification."
                    ), "reason": "unification concept"},
                ],
            })
        else:
            items.append({
                "qid": question["qid"],
                "polished_task": question["task"],
                "fragments": [],
            })
    return {"items": items}


def _inventory() -> dict:
    return {"items": [
        _item("QINV-0001", LOOK_AT_FIGURE, image_urls=["img/germania.png"]),
        _item("QINV-0002",
              "Describe the zollverein and the Frankfurt Parliament."),
        _item("QINV-0003", "Name the allegory of the French nation."),
        _item("QINV-0004", "Collect stamps and discuss in class.",
              source_kind="activity"),
    ], "stats": {}}


# --------------------------------------------------------------------------- #
# The pass
# --------------------------------------------------------------------------- #

def _by_qid(result: dict, qid: str) -> dict:
    return next(i for i in result["items"] if i["qid"] == qid)


def test_polishing_adds_fields_and_never_touches_source_wording():
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    polished = _by_qid(result, "QINV-0001")
    assert polished["raw_task"] == LOOK_AT_FIGURE
    assert polished["normalized_task"] == LOOK_AT_FIGURE
    assert "Look at the figure once again" not in polished["polished_task"]
    assert polished["polish_flag"] == question_polishing.FLAG_POLISHED

    untouched = _by_qid(result, "QINV-0003")
    assert "polished_task" not in untouched
    assert "polish_flag" not in untouched


def test_a_spanning_question_becomes_first_class_fragment_items():
    """Fragments are ordinary inventory items; the parent is preserved."""
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    qids = [item["qid"] for item in result["items"]]
    assert "QINV-0002" not in qids
    assert qids[1:3] == ["QINV-0002.1", "QINV-0002.2"]
    fragments = result["items"][1:3]
    for fragment in fragments:
        assert fragment["parent_qid"] == "QINV-0002"
        assert fragment["polish_flag"] == question_polishing.FLAG_SPLIT
        assert fragment["polished_task"]
        # Source audit identity is the parent's, so anchors still match.
        assert fragment["raw_task"] == (
            "Describe the zollverein and the Frankfurt Parliament."
        )
    parents = result["split_parents"]
    assert [p["qid"] for p in parents] == ["QINV-0002"]
    # The dotted shape is the one existing qid tooling already parses.
    from app.services import semantic_recovery

    for item in fragments:
        assert semantic_recovery._QID_RE.fullmatch(item["qid"]), item["qid"]


def test_fragments_resolve_to_their_parents_sealed_task():
    """Phase 3.3 certifies a fragment against the parent's graph task."""
    from app.services import canonical_source_phase3 as phase3

    sealed = {"QINV-0002": {"qid": "QINV-0002", "topic_id": "TOPIC-0002"}}

    task = phase3._graph_task_for_qid(
        sealed, "QINV-0002.1", parent_qid="QINV-0002")

    assert task is sealed["QINV-0002"]
    # Even without the explicit parent pointer, the dotted shape resolves.
    assert phase3._graph_task_for_qid(sealed, "QINV-0002.2") is (
        sealed["QINV-0002"]
    )


def test_fragments_carry_exact_once_coverage_individually():
    """Each fragment must be placed once; covering both covers the split."""
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)
    types = [{
        "type_id": "TYPE-0001",
        "source_question_ids": [
            "QINV-0001", "QINV-0002.1", "QINV-0003",
        ],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "examples": [
                {"source_question_id": qid, "example_prompt": "x"}
                for qid in ("QINV-0001", "QINV-0002.1", "QINV-0003")
            ],
        }],
    }]

    missed = generation._uncovered_inventory_items(result, types)

    # The unplaced fragment is missed on its own; its placed sibling is not.
    # (The activity row is legitimately uncovered by this Type too.)
    assert [item["qid"] for item in missed] == ["QINV-0002.2", "QINV-0004"]


def test_collapse_and_expand_round_trip():
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)
    expanded_qids = [item["qid"] for item in result["items"]]

    collapsed = question_polishing.collapse_split_items(result)
    assert [i["qid"] for i in collapsed["items"]][1] == "QINV-0002"
    assert collapsed["items"][1]["polish_fragments"]
    assert collapsed["split_parents"] == []

    re_expanded = question_polishing.expand_split_items(collapsed)
    assert [item["qid"] for item in re_expanded["items"]] == expanded_qids
    assert [p["qid"] for p in re_expanded["split_parents"]] == ["QINV-0002"]


def test_anchor_refresh_sees_the_parent_and_returns_the_fragments(monkeypatch):
    """The refresh wrapper collapses for the anchors and re-expands after."""
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)
    seen: dict = {}

    stub = ModuleType("stub_generation")

    def refresh(inventory, sections):
        seen["qids"] = [i["qid"] for i in inventory["items"]]
        return inventory

    stub._refresh_inventory_from_source_anchors = refresh
    stub._inventory_stats = lambda items: {"total_inventory_items": len(items)}
    stub._extract_question_task_inventory_via_api = lambda **kwargs: {"items": []}
    stub._inventory_task_text = lambda item: str(item.get("raw_task") or "")
    question_polishing_contract.install(stub)

    refreshed = stub._refresh_inventory_from_source_anchors(result, [])

    assert "QINV-0002" in seen["qids"]
    assert "QINV-0002.1" not in seen["qids"]
    refreshed_qids = [i["qid"] for i in refreshed["items"]]
    assert "QINV-0002.1" in refreshed_qids and "QINV-0002" not in refreshed_qids
    assert refreshed["stats"]["total_inventory_items"] == len(
        refreshed["items"])


def test_hub_rows_are_never_polished():
    """Phase 3.9 compares hub wire text exactly; polishing must skip hubs."""
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    hub = _by_qid(result, "QINV-0004")
    assert "polished_task" not in hub and "polish_flag" not in hub


def test_skip_kinds_match_generations_hub_kinds():
    assert question_polishing.SKIP_KINDS == generation._HUB_INVENTORY_KINDS


def test_batch_failure_keeps_originals_flagged_and_continues():
    def failing(system, user, **kwargs):
        raise RuntimeError("OpenAI unavailable after 12 transient retries")

    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=failing)

    kept = result["items"][0]
    assert kept["raw_task"] == LOOK_AT_FIGURE
    assert kept["polish_flag"] == question_polishing.FLAG_KEPT
    assert "polished_task" not in kept


def test_quota_exhaustion_still_stops_the_run():
    def quota(system, user, **kwargs):
        raise RuntimeError("OpenAI quota exhausted (insufficient_quota)")

    with pytest.raises(RuntimeError, match="insufficient_quota"):
        question_polishing.polish_inventory(
            _inventory(), meta=META, api_call=quota)


def test_dropped_mcq_option_invalidates_the_polish():
    inventory = {"items": [_item(
        "QINV-0001",
        "Which treaty? (A) Vienna (B) Versailles",
        source_kind="mcq",
        options=["(A) Vienna", "(B) Versailles"],
    )], "stats": {}}

    def drops_option(system, user, **kwargs):
        return {"items": [{
            "qid": "QINV-0001",
            "polished_task": "Which treaty restored conservative power? "
                             "(A) Vienna",
            "fragments": [],
        }]}

    result = question_polishing.polish_inventory(
        inventory, meta=META, api_call=drops_option)

    item = result["items"][0]
    assert item["polish_flag"] == question_polishing.FLAG_KEPT
    assert "polished_task" not in item


def test_unchanged_wording_records_nothing():
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    already_clean = _by_qid(result, "QINV-0003")
    assert "polish_flag" not in already_clean


def test_decisions_are_cached_and_never_rebilled():
    question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    def forbidden(system, user, **kwargs):
        raise AssertionError("cached polishing must not call the model")

    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=forbidden)

    assert result["items"][0]["polish_flag"] == question_polishing.FLAG_POLISHED


def test_dry_mode_is_a_no_op(monkeypatch):
    monkeypatch.setattr(config, "use_live_generation", lambda: False)

    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    assert all("polish_flag" not in item for item in result["items"])


# --------------------------------------------------------------------------- #
# The contract wiring
# --------------------------------------------------------------------------- #

def test_public_wording_prefers_the_polished_task():
    """The one function Example backfill copies from presents the polish."""
    item = _item("QINV-0001", LOOK_AT_FIGURE)
    item["polished_task"] = (
        "The illustration provided shows Germania. Explain the broken chain."
    )
    item["polish_flag"] = question_polishing.FLAG_POLISHED

    text = generation._inventory_task_text(item)

    assert "The illustration provided shows Germania" in text
    assert "Look at the figure once again" not in text


def test_public_wording_is_pass_through_without_a_polish():
    """Pre-polishing checkpoints and hub rows keep byte-identical wording."""
    item = _item("QINV-0001", LOOK_AT_FIGURE)

    assert generation._inventory_task_text._question_polishing_installed
    assert "Look at the figure once again" in generation._inventory_task_text(item)


def test_extraction_is_wrapped_and_polishes_before_checkpoint():
    assert getattr(
        generation._extract_question_task_inventory_via_api,
        "_question_polishing_installed",
        False,
    )


def test_install_is_idempotent():
    stub = ModuleType("stub_generation")
    calls = {"extract": 0}

    def extract(*args, **kwargs):
        calls["extract"] += 1
        return {"items": []}

    stub._extract_question_task_inventory_via_api = extract
    stub._inventory_task_text = lambda item: str(item.get("raw_task") or "")
    stub._refresh_inventory_from_source_anchors = lambda inventory, s: inventory
    stub._inventory_stats = lambda items: {}

    question_polishing_contract.install(stub)
    wrapped_once = stub._extract_question_task_inventory_via_api
    question_polishing_contract.install(stub)

    assert stub._extract_question_task_inventory_via_api is wrapped_once
    stub._extract_question_task_inventory_via_api(meta=META, sections=[])
    assert calls["extract"] == 1


def test_prompt_carries_the_hard_requirements():
    from app.services import prompts

    prompt = prompts.get_text("concepts.question_polishing.system")

    assert "NEVER change what the question asks" in prompt
    assert "standalone" in prompt
    assert "never translate" in prompt.casefold()
    assert "Never answer the question" in prompt
    assert "fragments ONLY when" in prompt
    assert "cover everything the original asked" in prompt
