"""Pass 2.5 — Place: Phase 2.2, refining Container 02 (doc §4).

All activities, info hubs and unclaimed source figures are pooled
chapter-wide and each is placed BY THE MODEL with the concept whose
content it depicts, exercises or enriches — never where the printer put
it. That doctrine is enforced structurally: the decision payload
deliberately carries no printer position (no source_start, no page, no
printed-under topic) — position is simply not offered as evidence.

One kernel decision per pooled batch (decide-once, Q10), advisory
critic whose dissent ships as review flags, and The Fixer on
exhaustion (Q13). R4 holds by construction: the checker refuses any
response that does not decide every pooled item exactly once — a hub
item must be placed (it has no disposition escape), a figure is placed
or carries the one recorded disposition. Nothing is ever silently
dropped; when the model cannot positively decide even through the
Fixer, the run stops (recoverable) instead of guessing.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from ... import config
from .. import containers
from .. import progress

_BATCH_SIZE = 8

POLICY_VERSION = "place-1"

# The one recorded disposition a pooled FIGURE may carry (containers is
# the vocabulary home; re-exported here for the pass's own callers).
FIGURE_DISPOSITION = containers.FIGURE_DISPOSITION


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mint_concept_ids(rows: list[Mapping[str, Any]]) -> list[str]:
    """Deterministic positional ids for the settled+created row list.

    Assemble re-mints the same ids over the same list to resolve the
    pass's placements back to row objects — pure bookkeeping, no
    content meaning.
    """
    return [f"CONCEPT-{index:04d}" for index in range(1, len(rows) + 1)]


def _description_of(details: object) -> str:
    match = re.search(
        r"Description:\s*(.*?)(?=\n[A-Z][A-Za-z ]{2,24}:|//|$)",
        str(details or ""),
        re.DOTALL,
    )
    return _normal(match.group(1)) if match else ""


def hub_pool(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every pooled Container-02 inventory item, with its full info text.

    Evidence is the item's own content only: full info text (the same
    canonical full-task renderer the hub note uses), image urls +
    captions, and the visible source label. Printer position fields are
    deliberately absent.
    """
    from .. import generation

    items_by_qid: dict[str, dict[str, Any]] = {}
    for item in (env.get("inventory") or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "").strip()
        if qid and qid not in items_by_qid:
            items_by_qid[qid] = dict(item)

    pool: list[dict[str, Any]] = []
    for qid in containers.hub_item_qids(env.get("inventory")):
        item = items_by_qid.get(qid)
        if item is None:
            continue
        captions = item.get("_image_captions")
        captions = dict(captions) if isinstance(captions, dict) else {}
        pool.append({
            "item_ref": qid,
            "pool_kind": "hub_item",
            "source_kind": str(item.get("source_kind") or ""),
            "activity_origin": bool(item.get("_activity_origin")),
            "source_label": _normal(
                item.get("source_label")
                or item.get("parent_source_label")
            ),
            "text": generation._inventory_task_text(item),
            "images": [
                {
                    "url": str(url),
                    "caption": _normal(captions.get(str(url), "")),
                }
                for url in item.get("image_urls") or []
                if str(url or "").strip()
            ],
        })
    return pool


def figure_pool(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Unclaimed canonical figure blocks: image evidence no item carries.

    A figure block joins the pool when it has at least one extractable
    image URL that appears in no inventory item's ``image_urls`` (a
    claimed figure already ships inside its item's rendered task). A
    block with no extractable URL cannot ship an embed and is accounted
    by the coverage ledger instead of pooled.
    """
    evidence = containers.figure_block_evidence(
        env.get("canonical"), env.get("inventory")
    )
    pool: list[dict[str, Any]] = []
    for row in evidence:
        unclaimed = [str(url) for url in row.get("unclaimed_image_urls") or []]
        if not unclaimed:
            continue
        pool.append({
            "item_ref": str(row.get("block_id") or ""),
            "pool_kind": "figure",
            "caption": _normal(row.get("caption")),
            "images": [{"url": url, "caption": _normal(row.get("caption"))}
                       for url in unclaimed],
        })
    return pool


def _place_checker(
    batch: list[dict[str, Any]],
    concept_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = {row["item_ref"]: row["pool_kind"] for row in batch}

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("placements")
        if not isinstance(rows, list):
            return ["response has no placements array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a placement entry is not an object")
                continue
            ref = str(row.get("item_ref") or "")
            if ref not in expected or ref in seen:
                defects.append(
                    f"unknown or repeated item_ref {ref or '<empty>'}"
                )
                continue
            seen.add(ref)
            disposition = str(row.get("disposition") or "").strip()
            concept_id = str(row.get("concept_id") or "").strip()
            if disposition:
                if expected[ref] != "figure":
                    defects.append(
                        f"{ref} is a hub item and must be placed on a "
                        "concept; hub items have no disposition"
                    )
                elif disposition != FIGURE_DISPOSITION:
                    defects.append(
                        f"{ref} disposition {disposition!r} is not "
                        f"{FIGURE_DISPOSITION!r}"
                    )
            elif concept_id not in concept_ids:
                defects.append(
                    f"{ref} names unknown concept_id "
                    f"{concept_id or '<empty>'}; cite a concept_id from "
                    "the request"
                )
            if not _normal(row.get("rationale")):
                defects.append(f"{ref} has no rationale")
        missing = sorted(set(expected) - seen)
        if missing:
            defects.append("undecided item(s): " + ", ".join(missing))
        return defects

    return check


def _live_place(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PLACE_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PLACE_CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def place(
    env: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    *,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Place every pooled Container-02 item. Empty pool skips the pass.

    Returns ``{"hub_placements": {qid: concept_id},
    "figure_placements": {block_id: concept_id | disposition},
    "figure_pool": {block_id: {"urls", "caption"}},
    "rationales": {item_ref: rationale},
    "review_flags": {item_ref: [flags]}}``.
    """
    from . import fixer as fixer_mod

    env = envelope_mod.validate(env)
    pool = [*hub_pool(env), *figure_pool(env)]
    if not pool:
        return {
            "hub_placements": {},
            "figure_placements": {},
            "figure_pool": {},
            "rationales": {},
            "review_flags": {},
        }

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_place
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    from . import prompts as prompts_mod

    rules_suffix = prompts_mod.instruction_rules_suffix(env)

    concept_ids = mint_concept_ids(rows)
    # Evidence is each concept's core teaching Description ONLY — Types,
    # Examples, existing Hub content and learner-error sections are
    # excluded so copied task wording can never become circular evidence
    # (the independent critic audits against the same bounded evidence).
    concepts_payload = [
        {
            "concept_id": concept_id,
            "topic_id": str(row.get("_semantic_topic_id") or ""),
            "concept_title": _normal(row.get("concept_title")),
            "parent_concept": _normal(row.get("parent_concept")),
            "description": _description_of(row.get("concept_details")),
        }
        for concept_id, row in zip(concept_ids, rows)
    ]
    known_concept_ids = set(concept_ids)
    progress.log(
        f"Place: pooling {len(pool)} Container-02 item(s) chapter-wide "
        "for model placement."
    )

    hub_placements: dict[str, str] = {}
    figure_placements: dict[str, str] = {}
    rationales: dict[str, str] = {}
    review_flags: dict[str, list[str]] = {}
    figure_meta = {
        row["item_ref"]: {
            "urls": [image["url"] for image in row.get("images") or []],
            "caption": row.get("caption") or "",
        }
        for row in pool
        if row["pool_kind"] == "figure"
    }

    def _decide_batch(start: int) -> list[tuple[dict, Mapping[str, Any], list[str]]]:
        batch = pool[start:start + _BATCH_SIZE]
        payload = {
            "stage": "place",
            "rules": (
                "Phase 2.2: place each pooled activity, info hub, and "
                "figure with the settled concept whose content it "
                "depicts, exercises, or enriches. Judge purely from the "
                "item's own text, caption, and images against each "
                "concept's teaching description — the printer's position "
                "is deliberately not given and must play no part. Every "
                "pooled item gets exactly one verdict. Hub items must be "
                "placed on a concept; a figure may instead record the "
                "disposition 'decorative_or_duplicate' only when it "
                "genuinely illustrates no concept's teaching or "
                "duplicates an image an item already carries. Prefer the "
                "normal concept whose teaching the item practices or "
                "illustrates; a Culmination row only when the item "
                "genuinely spans that topic's concepts." + rules_suffix
            ),
            "topics_in_teaching_order": [
                {
                    "topic_id": str(row.get("topic_id") or ""),
                    "title": str(row.get("title") or ""),
                }
                for row in env["graph"]["topics"]
                if isinstance(row, Mapping)
            ],
            "settled_concepts": concepts_payload,
            "pool": batch,
        }
        decision = kernel.decide(
            kind="place.container02",
            unit_id=f"pool#{start}",
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_place_checker(batch, known_concept_ids),
            critic=critic,
            store=store,
            policy_version=POLICY_VERSION,
            fixer=fixer,
        )
        placed = {
            str(row.get("item_ref") or ""): row
            for row in decision["response"].get("placements") or []
            if isinstance(row, Mapping)
        }
        flags = list(decision.get("review_flags") or [])
        return [(entry, placed[entry["item_ref"]], flags) for entry in batch]

    workers = config.phase3_decision_workers()
    for batch_result in kernel.parallel_map_in_order(
        range(0, len(pool), _BATCH_SIZE),
        _decide_batch,
        max_workers=workers,
    ):
        batch_refs = [
            str(batch_entry["item_ref"])
            for batch_entry, _verdict, _flags in batch_result
        ]
        for entry, verdict, flags in batch_result:
            ref = entry["item_ref"]
            rationales[ref] = _normal(verdict.get("rationale"))
            # A flag naming one item lands on that item only, never on its
            # whole batch (kernel.pin_flags — settle's staging fix).
            pinned = kernel.pin_flags(list(flags), batch_refs, str(ref))
            if pinned:
                review_flags[ref] = pinned
            disposition = str(verdict.get("disposition") or "").strip()
            if entry["pool_kind"] == "figure":
                figure_placements[ref] = disposition or str(
                    verdict.get("concept_id") or ""
                )
            else:
                hub_placements[ref] = str(verdict.get("concept_id") or "")

    dispositions = sum(
        1 for value in figure_placements.values()
        if value == FIGURE_DISPOSITION
    )
    progress.log(
        f"Place: {len(hub_placements)} hub item(s) and "
        f"{len(figure_placements) - dispositions} figure(s) placed"
        + (
            f"; {dispositions} figure disposition(s) recorded."
            if dispositions else "."
        ),
        level="success",
    )
    return {
        "hub_placements": hub_placements,
        "figure_placements": figure_placements,
        "figure_pool": figure_meta,
        "rationales": rationales,
        "review_flags": review_flags,
    }
