"""Pass 2.6 — Analyse: the chapter's misconception/error-analysis inventory.

Phase 2.4 + Phase 4.3 in one pass (docs/aegis-restructure.md §4, decided
by Q1): the chapter-level inventory of **distinct** Misconceptions and
Error Analyses is the ONLY analysis mechanism for the Post lane. The
every-concept learner-analysis contract is retired — a concept's
``Misconception/ Error Analysis`` section exists only where an inventory
item was allotted to it, and *not every concept receives one*. Achieving
Mastery is untouched: every concept still carries its Mastery line.

* **2.4 Build** — one kernel decision over chapter-wide evidence: the
  canonical source blocks plus the question/task inventory
  (experiment/practical items are named evidence for error analysis).
  Distinctness and genuineness are the model's authored judgment; the
  checker is mechanics only (positional ids, kind enum, non-empty text).
  There is NO count quota of any kind — a thin chapter may yield few or
  zero items and is never padded (Rule 1: no volume-derived structure).
* **4.3 Allot** — each item is allotted to exactly one settled concept.
  Evidence is the settled concept Descriptions ONLY (place.py doctrine:
  no Types/Examples text, no circular evidence). The checker enforces
  R4's exact-once accounting: every item allotted exactly once, every
  cited concept resolves. Culmination recaps never receive an item.

Decide-once through the kernel, advisory critic whose dissent ships as
review flags (Q10), The Fixer on exhaustion (Q13). The recorded output
(``source.phase3-analysis.json``) rides beside the decision store and
into Assemble, which stamps the allotted items onto their rows.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from ... import config
from .. import progress

# Items are allotted in bounded batches (payload size mechanics only —
# the inventory itself carries no count structure of any kind).
_ALLOT_BATCH_SIZE = 8

POLICY_VERSION = "analysis-1"

ITEM_KINDS = ("misconception", "error_analysis")

_ITEM_ID_RE = re.compile(r"^LA-(\d{4})$")

# Inventory kinds that are named evidence for error analysis (process
# errors typically surface around practical/experimental work).
_PRACTICAL_KINDS = frozenset({
    "experiment_task", "practical", "activity", "lab_task",
})


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mint_item_ids(count: int) -> list[str]:
    """Deterministic positional inventory item ids: LA-0001, LA-0002, …"""
    return [f"LA-{index:04d}" for index in range(1, count + 1)]


def _description_of(details: object) -> str:
    match = re.search(
        r"Description:\s*(.*?)(?=\n[A-Z][A-Za-z ]{2,24}:|//|$)",
        str(details or ""),
        re.DOTALL,
    )
    return _normal(match.group(1)) if match else ""


def build_evidence(env: Mapping[str, Any]) -> dict[str, Any]:
    """Chapter-wide evidence for the 2.4 Build decision.

    Canonical source blocks plus the question/task inventory. Each
    inventory entry carries its kind; experiment/practical items are
    marked ``practical_evidence`` so the model reads them as the named
    evidence for error analysis. No counts, ratios, or size-derived
    fields ride the payload.
    """
    from .. import generation

    blocks = [
        {
            "block_id": str(row.get("block_id") or ""),
            "topic_id": str(row.get("topic_id") or ""),
            "kind": str(row.get("kind") or ""),
            "text": "",
        }
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    ]
    text_by_id = {
        str(row.get("block_id") or ""): str(row.get("display_text") or "")
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }
    for row in blocks:
        row["text"] = text_by_id.get(row["block_id"], "")

    tasks: list[dict[str, Any]] = []
    for item in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid:
            continue
        kind = str(item.get("source_kind") or "").strip().lower()
        tasks.append({
            "qid": qid,
            "source_kind": kind,
            "practical_evidence": kind in _PRACTICAL_KINDS
            or bool(item.get("_activity_origin")),
            "text": generation._inventory_task_text(item)[:600],
        })
    return {"source_blocks": blocks, "question_task_inventory": tasks}


def _inventory_checker() -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: positional ids, kind enum, non-empty text.

    Distinctness/genuineness are the model's authored judgment (the
    advisory critic reviews them); an empty items list is legal — a thin
    chapter yields few or no items, never padded.
    """

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("items")
        if not isinstance(rows, list):
            return ["response has no items array"]
        expected = mint_item_ids(len(rows))
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                defects.append("an item entry is not an object")
                continue
            item_id = str(row.get("item_id") or "")
            if item_id != expected[position]:
                defects.append(
                    f"item at position {position + 1} carries id "
                    f"{item_id or '<empty>'!r}; ids are the positional "
                    f"mint LA-0001, LA-0002, … (expected "
                    f"{expected[position]})"
                )
            kind = str(row.get("kind") or "").strip()
            if kind not in ITEM_KINDS:
                defects.append(
                    f"{item_id or expected[position]} kind {kind!r} is not "
                    "one of misconception/error_analysis"
                )
            if not _normal(row.get("text")):
                defects.append(
                    f"{item_id or expected[position]} has empty text"
                )
        return defects

    return check


def _allot_checker(
    item_ids: list[str],
    concept_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = set(item_ids)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("allotments")
        if not isinstance(rows, list):
            return ["response has no allotments array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("an allotment entry is not an object")
                continue
            item_id = str(row.get("item_id") or "")
            if item_id not in expected or item_id in seen:
                defects.append(
                    f"unknown or repeated item_id {item_id or '<empty>'}"
                )
                continue
            seen.add(item_id)
            concept_id = str(row.get("concept_id") or "").strip()
            if concept_id not in concept_ids:
                defects.append(
                    f"{item_id} names unknown concept_id "
                    f"{concept_id or '<empty>'}; cite a concept_id from "
                    "the request"
                )
        missing = sorted(expected - seen)
        if missing:
            # R4: every inventory item is allotted exactly once — an
            # undecided item is a contract defect, never a silent drop.
            defects.append("unallotted item(s): " + ", ".join(missing))
        return defects

    return check


def _live_build(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.ANALYSE_INVENTORY_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_allot(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.ANALYSE_ALLOT_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.ANALYSE_CRITIC_SYSTEM, prompts.render(payload),
        purpose="advisory_critic",
    )


def analyse(
    env: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    *,
    provider: kernel.Provider | None = None,
    allot_provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Build the chapter inventory (2.4) and allot every item (4.3).

    ``rows`` is the ordered ``[*settled, *new_concepts]`` list Assemble
    will resolve against (the positional concept-id mint is shared with
    place.py). Returns ``{"inventory": [items], "allotments":
    {item_id: concept_id}, "rationales": {item_id: rationale},
    "review_flags": {item_id: [flags]}}``. An empty inventory (a thin
    chapter) returns empty allotments — that is legal, never padded.
    """
    from . import fixer as fixer_mod
    from . import place as place_mod
    from .. import concept_refiner as cr

    env = envelope_mod.validate(env)
    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_build
        allot_provider = allot_provider or _live_allot
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    allot_provider = allot_provider or provider
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    from . import prompts as prompts_mod

    rules_suffix = prompts_mod.instruction_rules_suffix(env)

    # ---- 2.4 Build: one decision over chapter-wide evidence ----------
    evidence = build_evidence(env)
    build_payload = {
        "stage": "analyse.inventory",
        "rules": (
            "Phase 2.4: build the chapter's inventory of DISTINCT "
            "Misconceptions and Error Analyses — each a genuine, strong "
            "addition to the chapter's learning, never filler. A "
            "misconception is an incorrect belief a learner plausibly "
            "holds about this chapter's content; an error analysis is a "
            "concrete process error — a faulty action or reasoning step "
            "made while applying the chapter's content. They are two "
            "distinct meanings; never restate one as the other. Error "
            "analysis typically surfaces around practical/experimental "
            "work — the question/task inventory entries marked "
            "practical_evidence are named evidence for it. Judge purely "
            "from the source evidence: every item must be grounded in "
            "what this chapter actually teaches, with the specific "
            "quantity, step, or claim named — never a vague 'confuses X "
            "with Y'. How many items the chapter genuinely supports is "
            "entirely your judgment: a thin chapter may yield few items "
            "or none, and an item must never be invented to fill space. "
            "Mint item_id positionally as LA-0001, LA-0002, … in the "
            "order you list items. evidence cites the block ids or qids "
            "the item is grounded on; rationale states why the item is "
            "a genuine, distinct addition." + rules_suffix
        ),
        "evidence": evidence,
    }
    build_decision = kernel.decide(
        kind="analyse.inventory",
        unit_id="chapter",
        envelope_sha256=envelope_sha,
        payload=build_payload,
        provider=provider,
        checker=_inventory_checker(),
        critic=critic,
        store=store,
        policy_version=POLICY_VERSION,
        fixer=fixer,
    )
    inventory: list[dict[str, Any]] = []
    for row in build_decision["response"].get("items") or []:
        if not isinstance(row, Mapping):
            continue
        inventory.append({
            "item_id": str(row.get("item_id") or ""),
            "kind": str(row.get("kind") or "").strip(),
            "text": _normal(row.get("text")),
            "evidence": _normal(row.get("evidence")),
            "rationale": _normal(row.get("rationale")),
        })
    build_flags = list(build_decision.get("review_flags") or [])

    # A flag naming one inventory item lands on that item only; only a
    # genuinely chapter-scope flag (naming no item) reaches every item
    # (kernel.pin_flags — settle's staging fix).
    all_item_ids = [str(item["item_id"]) for item in inventory]
    review_flags: dict[str, list[str]] = {}
    for item in inventory:
        pinned = kernel.pin_flags(
            build_flags, all_item_ids, str(item["item_id"])
        )
        if pinned:
            review_flags[item["item_id"]] = pinned

    progress.log(
        f"Analyse: chapter inventory holds {len(inventory)} distinct "
        "misconception/error-analysis item(s) (model-judged; a thin "
        "chapter is never padded)."
    )
    if not inventory:
        return {
            "inventory": [],
            "allotments": {},
            "rationales": {},
            "review_flags": {},
        }

    # ---- 4.3 Allot: every item to exactly one settled concept --------
    # Evidence is each concept's Description ONLY (place.py doctrine);
    # culmination recaps never receive an item (§5: the section exists
    # only on concepts allotted from the inventory).
    concept_ids = place_mod.mint_concept_ids(list(rows))
    concepts_payload = [
        {
            "concept_id": concept_id,
            "topic_id": str(row.get("_semantic_topic_id") or ""),
            "concept_title": _normal(row.get("concept_title")),
            "parent_concept": _normal(row.get("parent_concept")),
            "description": _description_of(row.get("concept_details")),
        }
        for concept_id, row in zip(concept_ids, rows)
        if not cr.is_culmination(_normal(row.get("concept_title")))
    ]
    known_concept_ids = {row["concept_id"] for row in concepts_payload}

    allotments: dict[str, str] = {}
    rationales: dict[str, str] = {}

    def _decide_batch(start: int) -> list[tuple[str, Mapping[str, Any], list[str]]]:
        batch = inventory[start:start + _ALLOT_BATCH_SIZE]
        payload = {
            "stage": "analyse.allot",
            "rules": (
                "Phase 4.3: allot each inventory item to the ONE settled "
                "concept it belongs to — the concept whose teaching the "
                "misconception distorts or whose application the error "
                "analysis derails. Judge purely from each concept's "
                "description against the item's text and evidence. Every "
                "item gets exactly one concept; NOT every concept "
                "receives an item — that is the design, never spread "
                "items to cover concepts. Cite only concept_id values "
                "from the request." + rules_suffix
            ),
            "settled_concepts": concepts_payload,
            "items": [
                {
                    "item_id": item["item_id"],
                    "kind": item["kind"],
                    "text": item["text"],
                    "evidence": item["evidence"],
                }
                for item in batch
            ],
        }
        decision = kernel.decide(
            kind="analyse.allot",
            unit_id=f"items#{start}",
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=allot_provider,
            checker=_allot_checker(
                [item["item_id"] for item in batch], known_concept_ids
            ),
            critic=critic,
            store=store,
            policy_version=POLICY_VERSION,
            fixer=fixer,
        )
        decided = {
            str(row.get("item_id") or ""): row
            for row in decision["response"].get("allotments") or []
            if isinstance(row, Mapping)
        }
        flags = list(decision.get("review_flags") or [])
        return [
            (item["item_id"], decided[item["item_id"]], flags)
            for item in batch
        ]

    workers = config.phase3_decision_workers()
    for batch_result in kernel.parallel_map_in_order(
        range(0, len(inventory), _ALLOT_BATCH_SIZE),
        _decide_batch,
        max_workers=workers,
    ):
        batch_item_ids = [
            str(batch_item_id) for batch_item_id, _v, _f in batch_result
        ]
        for item_id, verdict, flags in batch_result:
            allotments[item_id] = str(verdict.get("concept_id") or "")
            rationale = _normal(verdict.get("rationale"))
            if rationale:
                rationales[item_id] = rationale
            pinned = kernel.pin_flags(
                list(flags), batch_item_ids, str(item_id)
            )
            if pinned:
                review_flags.setdefault(item_id, []).extend(pinned)

    progress.log(
        f"Analyse: {len(allotments)} item(s) allotted, each to exactly "
        "one concept (R4).",
        level="success",
    )
    return {
        "inventory": inventory,
        "allotments": allotments,
        "rationales": rationales,
        "review_flags": review_flags,
    }
