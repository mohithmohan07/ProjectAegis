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
    if kwargs.get("purpose") == "advisory_critic":
        return {"items": [
            {"qid": question["qid"], "verdict": "verified", "issues": []}
            for question in payload["questions"]
        ]}
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


def test_a_spanning_question_stays_whole_and_fragments_are_ignored():
    """Questions are never split: model-proposed fragments are discarded."""
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    qids = [item["qid"] for item in result["items"]]
    assert "QINV-0002" in qids
    assert not any("." in qid for qid in qids)
    assert "split_parents" not in result
    whole = _by_qid(result, "QINV-0002")
    assert whole["polish_flag"] == question_polishing.FLAG_POLISHED
    assert "polish_fragments" not in whole
    # The whole question's polished wording is the shipping artifact.
    assert "zollverein" in whole["polished_task"]
    assert "Frankfurt Parliament" in whole["polished_task"]


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


def test_whole_questions_carry_exact_once_coverage():
    """A never-split multi-part question is covered once, as one unit."""
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)
    types = [{
        "type_id": "TYPE-0001",
        "source_question_ids": [
            "QINV-0001", "QINV-0002", "QINV-0003",
        ],
        "case_prompts": [{
            "case_id": "CASE-0001",
            "examples": [
                {"source_question_id": qid, "example_prompt": "x"}
                for qid in ("QINV-0001", "QINV-0002", "QINV-0003")
            ],
        }],
    }]

    missed = generation._uncovered_inventory_items(result, types)

    # Every placed question is covered whole; only the activity hub row is
    # legitimately uncovered by this Type.
    assert [item["qid"] for item in missed] == ["QINV-0004"]


def _legacy_split_inventory() -> dict:
    """An inventory shape persisted by the old splitting versions."""
    parent = _item(
        "QINV-0002", "Describe the zollverein and the Frankfurt Parliament.")
    parent["polish_fragments"] = [
        {"polished_task": (
            "Describe the economic role played by the zollverein in "
            "binding the German states."
        ), "reason": "economic nationalism concept"},
        {"polished_task": (
            "Explain how the failure of the Frankfurt Parliament shaped "
            "the course of German unification."
        ), "reason": "unification concept"},
    ]
    return {"items": [
        _item("QINV-0001", LOOK_AT_FIGURE),
        parent,
        _item("QINV-0003", "Name the allegory of the French nation."),
    ], "stats": {}}


def test_legacy_split_inventories_still_collapse_and_expand():
    """Old persisted splits keep healing even though new runs never split."""
    expanded = question_polishing.expand_split_items(_legacy_split_inventory())
    expanded_qids = [item["qid"] for item in expanded["items"]]
    assert expanded_qids[1:3] == ["QINV-0002.1", "QINV-0002.2"]

    collapsed = question_polishing.collapse_split_items(expanded)
    assert [i["qid"] for i in collapsed["items"]][1] == "QINV-0002"
    assert collapsed["items"][1]["polish_fragments"]
    assert collapsed["split_parents"] == []

    re_expanded = question_polishing.expand_split_items(collapsed)
    assert [item["qid"] for item in re_expanded["items"]] == expanded_qids
    assert [p["qid"] for p in re_expanded["split_parents"]] == ["QINV-0002"]


def test_a_restored_legacy_parent_is_superseded_by_its_fragments():
    """Job 15: persistence dropped split_parents, and the ACSD-ledger
    refresh re-minted the split parents beside their fragments — putting
    the uncertifiable compound question back into exact-once coverage on
    every replay. Fragments supersede a resurrected parent, always."""
    result = question_polishing.expand_split_items(_legacy_split_inventory())
    result["split_parents"] = []  # simulate the persistence gap
    result["items"].append(_item(
        "QINV-0002", "Describe the zollverein and the Frankfurt Parliament."))

    healed = question_polishing.supersede_restored_parents(result)

    qids = [item["qid"] for item in healed["items"]]
    assert "QINV-0002" not in qids
    assert "QINV-0002.1" in qids and "QINV-0002.2" in qids
    assert [p["qid"] for p in healed["split_parents"]] == ["QINV-0002"]


def test_new_runs_never_produce_fragments_for_the_anchor_refresh(monkeypatch):
    """The refresh wrapper passes whole questions through unchanged."""
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
    refreshed_qids = [i["qid"] for i in refreshed["items"]]
    assert "QINV-0002" in refreshed_qids
    assert not any("." in qid for qid in refreshed_qids)
    assert refreshed["stats"]["total_inventory_items"] == len(
        refreshed["items"])


def test_polished_wording_ships_under_the_acsd_source_contract():
    """Mathematics review: verbatim textbook prose (answers included) was
    shipping as Examples because the ACSD contract pinned the display to
    the canonical source prompt, silently discarding the polish."""
    from app.services import canonical_source_phase2 as phase2

    prose = (
        "Now, what is the minimum information you need? You will find "
        "that you need both."
    )
    item = {
        "qid": "QINV-0001",
        "source_kind": "intext_question",
        "raw_task": prose,
        "normalized_task": prose,
        "polished_task": (
            "What is the minimum information needed to specify an "
            "arithmetic progression?"
        ),
        "_acsd_source_contract": phase2.SOURCE_CONTRACT_MODE,
        "_acsd_display_prompt": prose,
    }
    shipped = generation._inventory_task_text(item)
    assert shipped.startswith("What is the minimum information needed")
    assert "You will find" not in shipped
    # Without a polish, the canonical source display still ships verbatim.
    bare = {k: v for k, v in item.items() if k != "polished_task"}
    assert generation._inventory_task_text(bare) == prose


def test_hub_rows_are_never_polished():
    """Phase 3.9 compares hub wire text exactly; polishing must skip hubs."""
    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=_api_polish)

    hub = _by_qid(result, "QINV-0004")
    assert "polished_task" not in hub and "polish_flag" not in hub


def test_skip_kinds_match_generations_hub_kinds():
    """The single source is containers.HUB_INVENTORY_KINDS: every consumer
    holds the SAME frozenset object, so drift is impossible by
    construction (identity, not equality)."""
    from app.services import containers, coverage_ledger

    assert question_polishing.SKIP_KINDS is containers.HUB_INVENTORY_KINDS
    assert generation._HUB_INVENTORY_KINDS is containers.HUB_INVENTORY_KINDS
    assert coverage_ledger._HUB_KINDS is containers.HUB_INVENTORY_KINDS


def test_info_hubs_are_never_polished():
    """Info hubs are enrichment, not test items; polishing skips them."""
    assert not question_polishing._eligible({
        "qid": "QINV-0009", "source_kind": "info_hub",
        "raw_task": "Do you know? The metre was defined in 1799.",
    })


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


def test_short_polished_wording_is_accepted_not_reverted():
    """A short-but-valid polish ships; no length check second-guesses it."""
    inventory = {"items": [_item(
        "QINV-0001",
        "It is left as an exercise for you to define osmosis in your "
        "own words after the discussion above.",
    )], "stats": {}}

    def short_polish(system, user, **kwargs):
        return {"items": [{
            "qid": "QINV-0001",
            "polished_task": "Define osmosis.",
            "fragments": [],
        }]}

    result = question_polishing.polish_inventory(
        inventory, meta=META, api_call=short_polish)

    item = result["items"][0]
    assert item["polished_task"] == "Define osmosis."
    assert item["polish_flag"] == question_polishing.FLAG_POLISHED
    assert "too short" not in str(item.get("polish_note") or "")


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
    assert "NEVER split a question" in prompt
    assert "stays one question" in prompt


def test_independent_dissent_is_advisory_and_carries_source_evidence():
    source = _item(
        "QINV-0091", "Explain the figure above.",
        image_urls=["https://assets.example/diagram.png"],
        image_ids=["IMG-0042"], block_ids=["BLK-0041"], page_hint="8",
        shared_context="The diagram labels a leaf and a stem.",
    )
    proposed = "Explain how the provided figure shows photosynthesis."
    calls = []

    def author_and_critic(system, user, **kwargs):
        payload = json.loads(user)
        calls.append((system, payload, kwargs["purpose"]))
        if kwargs["purpose"] == "source_extraction":
            return {"items": [{
                "qid": source["qid"], "polished_task": proposed,
                "note": "The figure establishes photosynthesis.",
            }]}
        return {"items": [{
            "qid": source["qid"], "verdict": "dissent", "issues": [
                "The original ask and shared context do not establish photosynthesis.",
            ],
        }]}

    result = question_polishing.polish_inventory(
        {"items": [source]}, meta=META, api_call=author_and_critic,
    )

    assert [call[2] for call in calls] == ["source_extraction", "advisory_critic"]
    assert calls[0][0] != calls[1][0]
    review_question = calls[1][1]["questions"][0]
    assert review_question["task"] == source["raw_task"]
    assert review_question["proposed_task"] == proposed
    assert "note" not in review_question  # independent of the author's rationale
    assert review_question["image_urls"] == source["image_urls"]
    assert review_question["source_evidence"]["image_ids"] == ["IMG-0042"]
    assert review_question["source_evidence"]["block_ids"] == ["BLK-0041"]
    item = result["items"][0]
    assert item["raw_task"] == source["raw_task"]
    assert item["polished_task"] == proposed  # dissent cannot gate or rewrite
    assert item["polish_review_required"] is True
    assert item["polish_audit"]["critic"]["verdict"] == "dissent"
    assert item["polish_audit"]["source_evidence"]["shared_context"] == source["shared_context"]
    assert item["polish_audit"]["author"]["note"] == "The figure establishes photosynthesis."


def test_oral_pronunciation_retains_modality_and_supersedes_old_written_polish():
    original = "Read these words aloud to your partner, paying attention to pronunciation."
    source = _item(
        "QINV-0021", original, source_kind="grammar_task",
        polished_task="Write the meaning of these words.",
        polish_flag=question_polishing.FLAG_POLISHED,
    )
    seen = []

    def author_and_critic(system, user, **kwargs):
        seen.append(system)
        if kwargs["purpose"] == "source_extraction":
            return {"items": [{
                "qid": source["qid"], "polished_task": original,
                "note": "Retained oral pronunciation and partner response modality.",
            }]}
        assert json.loads(user)["questions"][0]["proposed_task"] == original
        return {"items": [{"qid": source["qid"], "verdict": "verified", "issues": []}]}

    result = question_polishing.polish_inventory(
        {"items": [source]}, meta={**META, "subject": "English"},
        api_call=author_and_critic,
    )

    item = result["items"][0]
    assert item["raw_task"] == item["normalized_task"] == original
    assert "polished_task" not in item and "polish_flag" not in item
    assert generation._inventory_task_text(item) == original
    assert item["polish_audit"]["author"]["note"].startswith("Retained oral")
    assert item["polish_review_required"] is False
    assert "nearest written-assessable" not in seen[0]
    assert "original ask and response modality" in seen[0]
    assert all("pronunciation" in system and "Q27" in system for system in seen)


@pytest.mark.parametrize("failure", [
    RuntimeError("provider unavailable"), RuntimeError("insufficient_quota"),
])
def test_critic_failure_is_visible_without_losing_authored_output(failure):
    def author_and_critic(system, user, **kwargs):
        if kwargs["purpose"] == "advisory_critic":
            raise failure
        return _api_polish(system, user, **kwargs)

    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=author_and_critic,
    )

    item = result["items"][0]
    assert item["polish_flag"] == question_polishing.FLAG_POLISHED
    assert item["polish_review_required"] is True
    assert item["polish_audit"]["critic"]["verdict"] == "unavailable"
    assert len(result["items"]) == 4


def test_missing_or_duplicate_critic_verdicts_are_visible():
    def author_and_critic(system, user, **kwargs):
        if kwargs["purpose"] == "advisory_critic":
            return {"items": [
                {"qid": "QINV-0001", "verdict": "verified", "issues": []},
                {"qid": "QINV-0001", "verdict": "dissent", "issues": ["conflict"]},
            ]}
        return _api_polish(system, user, **kwargs)

    result = question_polishing.polish_inventory(
        _inventory(), meta=META, api_call=author_and_critic,
    )

    for item in result["items"][:3]:
        assert item["polish_audit"]["critic"]["verdict"] == "malformed"
        assert item["polish_review_required"] is True
    assert result["items"][0]["polished_task"]


def test_author_only_v2_cache_cannot_skip_independent_review():
    source = _item("QINV-0001", LOOK_AT_FIGURE)
    sha = question_polishing._sha256_text
    from app.services import prompts

    old_key = sha("\0".join((
        "question-polishing-v2", config.OPENAI_MODEL,
        sha(prompts.get_text("concepts.question_polishing.system")),
        sha(json.dumps([[source["qid"], source["raw_task"]]], ensure_ascii=False)),
    )))[:32]
    question_polishing._store_cached(old_key, {
        source["qid"]: {"polished_task": "An old unreviewed adaptation.",
                        "flag": question_polishing.FLAG_POLISHED},
    })
    calls = []

    def record(system, user, **kwargs):
        calls.append(kwargs["purpose"])
        return _api_polish(system, user, **kwargs)

    result = question_polishing.polish_inventory(
        {"items": [source]}, meta=META, api_call=record,
    )

    assert calls == ["source_extraction", "advisory_critic"]
    assert "old unreviewed" not in result["items"][0]["polished_task"]
    assert result["items"][0]["polish_audit"]["critic"]["verdict"] == "verified"


def test_cache_changes_with_shared_context_assets_metadata_and_critic_prompt(monkeypatch):
    from app.services import prompts

    source = _item("QINV-0001", "Explain the figure above.")
    key = question_polishing._cache_key([source], META)
    assert question_polishing._cache_key([
        {**source, "shared_context": "A circuit with two parallel branches."},
    ], META) != key
    assert question_polishing._cache_key([
        {**source, "image_urls": ["https://assets.example/circuit.png"]},
    ], META) != key
    assert question_polishing._cache_key([source], {**META, "grade": "6"}) != key
    original_get = prompts.get_text
    monkeypatch.setattr(prompts, "get_text", lambda name: (
        original_get(name) + " Changed review instruction."
        if name == "concepts.question_polishing.critic" else original_get(name)
    ))
    assert question_polishing._cache_key([source], META) != key


def test_dropped_inline_image_reverts_mechanically_and_records_review():
    image_url = "https://assets.example/diagram.png"
    original = f"Explain this diagram. <img src='{image_url}'>"
    source = _item("QINV-0001", original, image_urls=[image_url])

    def author_and_critic(system, user, **kwargs):
        if kwargs["purpose"] == "source_extraction":
            return {"items": [{"qid": source["qid"], "polished_task": "Explain this diagram."}]}
        assert json.loads(user)["questions"][0]["proposed_task"] == original
        return {"items": [{"qid": source["qid"], "verdict": "verified", "issues": []}]}

    result = question_polishing.polish_inventory(
        {"items": [source]}, meta=META, api_call=author_and_critic,
    )

    item = result["items"][0]
    assert "polished_task" not in item
    assert item["image_urls"] == [image_url]
    assert item["polish_flag"] == question_polishing.FLAG_KEPT
    assert item["polish_note"] == "dropped inline source image URL"
    assert item["polish_audit"]["critic"]["verdict"] == "verified"


def test_option_retention_is_measured_against_the_item_source_text():
    """Only an option the source wording carries can be "dropped" by a rewrite.

    Step 1 splices its rendered options into ``raw_task``
    (``generation._sanitize_inventory_item``), so its option check is unchanged.
    A Q51 Step 2 reviewed item keeps its extracted ``options`` beside a
    ``raw_task`` that is only the reviewed question spans; reverting that
    item's polish for an option the reviewed wording never contained threw
    away a paid author+critic round for no defect.
    """
    in_source = _item(
        "QINV-0001",
        "Which treaty? (A) Vienna (B) Versailles",
        source_kind="mcq",
        options=["(A) Vienna", "(B) Versailles"],
    )
    assert question_polishing._polish_is_usable(
        in_source, "Which treaty restored conservative power? (A) Vienna",
    ) == "dropped MCQ option '(B) Versailles'"
    assert question_polishing._polish_is_usable(
        in_source,
        "Which treaty restored conservative power? (A) Vienna (B) Versailles",
    ) == ""

    outside_source = _item(
        "QFILE-0001",
        "Which molecule carries genetic information?",
        source_kind="reviewed_file",
        options=["(a) DNA", "(b) RNA"],
    )
    assert question_polishing._polish_is_usable(
        outside_source,
        "Which molecule carries genetic information in a cell?",
    ) == ""


def test_reviewed_options_outside_the_question_wording_do_not_revert_the_polish():
    polished = "Which molecule carries the genetic information of a cell?"
    source = _item(
        "QFILE-0001",
        "Which molecule carries genetic information?",
        source_kind="reviewed_file",
        options=["(a) DNA", "(b) RNA"],
    )

    def author_and_critic(system, user, **kwargs):
        if kwargs["purpose"] == "advisory_critic":
            return {"items": [{"qid": source["qid"], "verdict": "verified", "issues": []}]}
        request = json.loads(user)
        assert request["questions"][0]["options"] == ["(a) DNA", "(b) RNA"]
        return {"items": [{"qid": source["qid"], "polished_task": polished}]}

    result = question_polishing.polish_inventory(
        {"items": [source]}, meta=META, api_call=author_and_critic,
    )

    item = result["items"][0]
    assert item["polish_flag"] == question_polishing.FLAG_POLISHED
    assert item["polished_task"] == item["frozen_task_text"] == polished
    assert item["options"] == ["(a) DNA", "(b) RNA"]
    assert item["raw_task"] == "Which molecule carries genetic information?"
