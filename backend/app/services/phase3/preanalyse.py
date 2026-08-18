"""Pass 2.9b — Preanalyse: the PRE lane's own misconception/error inventory.

Q1 reaches the Pre lane (docs/aegis-restructure.md §4 Phase 2.4 + Phase
04 4.3, §12 Q1). The retired every-concept learner-analysis writer (§10
step 7's delete ledger — the Pre lane's old critic-gated retry loop with
a max-attempts ceiling and an allowed/banned verb vocabulary) went with
the lane earlier on this branch. This pass replaces it under exactly the
law Q1 states for the Post lane:

* the inventory is the ONLY analysis mechanism;
* a pre-concept's ``Misconception/ Error Analysis`` section exists ONLY
  where an inventory item was allotted to it, and **not every
  pre-concept receives one** — that is the design, never spread items to
  cover concepts;
* **Achieving Mastery is unaffected** and stays on EVERY pre-concept
  (``premap.compose_details`` mints it; nothing here can remove it).

**Why a second, Pre-scoped inventory rather than pointing the Post pass
at Pre rows** (spec T5, verified against the committed code):

* ``analyse.build_evidence`` reads this chapter's graph/canonical source
  blocks and its question/task inventory. A prerequisite misconception is
  about the PREREQUISITE, and the Pre lane may not be shown a source
  question's identity at all (premap's no-extraction guard), so that
  evidence set is both wrong and forbidden here;
* ``assemble.stamp_analysis_allotments`` raises unless every item lands
  on exactly one existing non-culmination row of the POST ordering;
* ``analyse``'s concept-id mint runs over ``[*settled, *new_concepts]``
  — Pre rows are not in it and cannot be cited.

Widening the Post pass's allotment target set to span both lanes was
rejected on two grounds: a prerequisite misconception would compete with
Post rows for the same item (Q1 allots each item to ONE concept), and
the Post pass's payload would change, re-keying every stored Post
analysis decision (``kernel.decision_key`` hashes the whole payload).
So: own kinds, own ``POLICY_VERSION``, own item-id mint — and
``phase3/analyse.py`` untouched, byte for byte.

**Evidence is the Pre lane's own**: the merged prerequisite capture and
the Pre map's own Descriptions. Never the current chapter's source
blocks, never the question/task inventory. The prerequisite text is
carried through ``premap._redact_ids`` for the same reason the map
payload is (upstream provenance may legitimately name the question it
was read from), and the assembled payload is then put through
``premap._refuse_source_qids`` before any spend.

**No count quota of any kind.** How many items the captured evidence
genuinely supports is entirely the model's judgment: a thin Pre map
yields few items or none, and an empty inventory is a legal, clean
return. Nothing here scales a count from the number of prerequisites,
the number of pre-concepts, or the size of anything (Rule 1: no
volume-derived structure).

**R4 exact-once.** Every inventory item is allotted to exactly one
pre-concept. An item the model leaves undecided is a contract defect the
checker names, so it takes the bounded corrections and then The Fixer's
one recorded decision (Q13) — it is never dropped.

**Rendering uses the SAME canonical section mechanics as the Post lane.**
``concept_refiner``'s ``split_sections``/``join_sections`` and
``assemble``'s own text join produce the one canonical
``// Misconception/ Error Analysis: Misconceptions: …; Error Analysis: …``
section, and the row-private allotment marker is the SAME
``_aegis_analysis_allotments`` field the Post lane stamps (already
registered in ``build_concepts_release._RELEASE_AUDIT_FIELDS`` and
already read by ``concept_validator.analysis_allotted_keys``). This is
an existing house-format section, not a new one — no detailing parser
learns anything new, and the PLA- ids inside the marker are what says
which lane allotted it.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from ... import config
from .. import progress

POLICY_VERSION = "pre-analysis-1"

ITEM_KINDS = ("misconception", "error_analysis")

# Items are allotted in bounded batches (payload size mechanics only —
# the inventory itself carries no count structure of any kind).
_ALLOT_BATCH_SIZE = 8


class PreAnalysisError(RuntimeError):
    """A Pre analysis allotment could not be stamped onto a Pre row.

    Unreachable after the checker plus The Fixer seam; kept as the
    belt-and-braces the Post lane keeps in ``assemble`` for the same
    accounting, and handled in the runner the same way a refused Pre map
    is — the finished Post map still ships.
    """


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def mint_item_ids(count: int) -> list[str]:
    """Positional Pre inventory item ids: PLA-0001, PLA-0002, …

    A distinct mint from the Post lane's ``LA-`` so a stored allotment
    marker says which inventory allotted it, and so the two lanes can
    never collide in an accounting that reads both.
    """
    return [f"PLA-{index:04d}" for index in range(1, count + 1)]


def build_evidence(
    prerequisites: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    qids: list[str] | None = None,
) -> dict[str, Any]:
    """The Pre lane's own evidence: the capture, and the Pre Descriptions.

    Deliberately NOT the current chapter's source blocks and NOT the
    question/task inventory — a prerequisite misconception is about the
    prerequisite, evidenced by what the capture found and by what the Pre
    map went on to teach. The capture's free text is redacted exactly as
    ``premap.map_evidence`` redacts it. No counts, ratios or size-derived
    fields ride any of it.
    """
    from . import premap as premap_mod

    qids = list(qids or [])
    captured = [
        {
            "prerequisite_id": str(row.get("prerequisite_id") or ""),
            "text": premap_mod._redact_ids(_normal(row.get("text")), qids),
            "rationale": premap_mod._redact_ids(
                _normal(row.get("rationale")), qids
            ),
        }
        for row in (prerequisites or {}).get("prerequisites") or []
        if isinstance(row, Mapping)
        and str(row.get("prerequisite_id") or "").strip()
    ]
    concepts = [
        {
            "pre_concept_id": str(row.get("_pre_concept_id") or ""),
            "concept_title": _normal(row.get("concept_title")),
            "description": premap_mod._description_of(
                row.get("concept_details")
            ),
        }
        for row in rows
    ]
    return {"prerequisites": captured, "pre_concepts": concepts}


def _inventory_checker() -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: positional ids, kind enum, non-empty text.

    Distinctness and genuineness are the model's authored judgment (the
    advisory critic reviews them); an EMPTY items list is legal — a thin
    Pre map yields few items or none and is never padded.
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
                    f"mint PLA-0001, PLA-0002, … (expected "
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
            concept_id = str(row.get("pre_concept_id") or "").strip()
            if concept_id not in concept_ids:
                defects.append(
                    f"{item_id} names unknown pre_concept_id "
                    f"{concept_id or '<empty>'}; cite a pre_concept_id "
                    "from the request"
                )
        missing = sorted(expected - seen)
        if missing:
            # R4: every inventory item is allotted exactly once — an
            # undecided item is a contract defect that takes the bounded
            # corrections and then The Fixer, never a silent drop.
            defects.append("unallotted item(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# live adapters


def _live_build(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREANALYSE_INVENTORY_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_allot(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREANALYSE_ALLOT_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREANALYSE_CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _inventory_rules(rules_suffix: str) -> str:
    return (
        "Phase 2.4, PRE-LEARNING lane: build the inventory of DISTINCT "
        "Misconceptions and Error Analyses for this chapter's "
        "PREREQUISITES — the fundamentals the chapter assumes the learner "
        "already holds. Each item must be a genuine, strong addition to "
        "the pre-learning, never filler. A misconception is an incorrect "
        "belief a learner plausibly holds about the prerequisite itself; "
        "an error analysis is a concrete process error — a faulty action "
        "or reasoning step made while APPLYING the prerequisite. They are "
        "two distinct meanings; never restate one as the other. Judge "
        "purely from the evidence in this request: the captured "
        "prerequisite set and the pre-learning concepts' own teaching. "
        "The current chapter's own content is deliberately not here — an "
        "item about what this chapter goes on to teach belongs to the "
        "post-learning inventory, not this one, and no question of this "
        "chapter appears anywhere in this lane. Name the specific "
        "quantity, step, term, or claim the item is about — never a vague "
        "'confuses X with Y'. How many items the captured evidence "
        "genuinely supports is entirely your judgment: a thin "
        "pre-learning map may yield few items or none, and an item must "
        "never be invented to fill space. An empty items list is a "
        "legitimate answer. Mint item_id positionally as PLA-0001, "
        "PLA-0002, … in the order you list items. evidence cites the "
        "prerequisite_id or pre_concept_id values the item is grounded "
        "on; rationale states why the item is a genuine, distinct "
        "addition." + rules_suffix
    )


def _allot_rules(rules_suffix: str) -> str:
    return (
        "Phase 4.3, PRE-LEARNING lane: allot each inventory item to the "
        "ONE pre-learning concept it belongs to — the concept whose "
        "teaching the misconception distorts, or whose application the "
        "error analysis derails. Judge purely from each pre-learning "
        "concept's description against the item's text and evidence. "
        "Every item gets exactly one concept; NOT every concept receives "
        "an item — that is the design, never spread items to cover "
        "concepts. Cite only pre_concept_id values from the request."
        + rules_suffix
    )


# ---------------------------------------------------------------------------
# public entry


def analyse(
    env: Mapping[str, Any],
    prerequisites: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
    *,
    qids: list[str] | None = None,
    rules_suffix: str = "",
    provider: kernel.Provider | None = None,
    allot_provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Build the Pre inventory (2.4) and allot every item (4.3).

    ``rows`` are the finished Pre rows, each carrying its
    ``_pre_concept_id``. Returns ``{"inventory": [items], "allotments":
    {item_id: pre_concept_id}, "rationales": {item_id: rationale},
    "review_flags": {item_id: [flags]}}``. An empty Pre map, or an empty
    inventory over a thin one, returns cleanly — never padded.
    """
    from . import fixer as fixer_mod
    from . import premap as premap_mod

    env = envelope_mod.validate(env)
    rows = list(rows)
    empty: dict[str, Any] = {
        "inventory": [],
        "allotments": {},
        "rationales": {},
        "review_flags": {},
    }
    if not rows:
        return empty

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_build
        allot_provider = allot_provider or _live_allot
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    allot_provider = allot_provider or provider
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    qids = list(qids or [])

    # ---- 2.4 Build: one decision over the Pre lane's own evidence -----
    build_payload = {
        "stage": "prelearn.analyse.inventory",
        "rules": _inventory_rules(rules_suffix),
        "chapter": premap_mod.chapter_calibration(env),
        "evidence": build_evidence(prerequisites, rows, qids),
    }
    # The pre-spend post-condition of the redaction above: the model
    # authoring a prerequisite misconception is never shown a source
    # question's identity, so it cannot echo one (premap's guard, same
    # act, same fail-closed reason).
    premap_mod._refuse_source_qids(
        build_payload, qids, where="the Pre analysis inventory payload"
    )
    build_decision = kernel.decide(
        kind="prelearn.analyse.inventory",
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
    review_flags: dict[str, list[str]] = {}
    for item in inventory:
        if build_flags:
            review_flags[item["item_id"]] = list(build_flags)

    progress.log(
        f"Pre-Learning analysis: the prerequisite inventory holds "
        f"{len(inventory)} distinct misconception/error-analysis item(s) "
        "(model-judged; a thin pre-learning map is never padded)."
    )
    if not inventory:
        return empty

    # ---- 4.3 Allot: every item to exactly one pre-concept ------------
    #
    # EVERY Pre row is a legal destination — no title filter of any kind.
    # The Post pass excludes culmination rows because the PIPELINE mints
    # those rows and their titles, so there the exclusion is marker
    # accounting over an identity this code created. The Pre lane mints
    # no culmination at all (``premap.PRE_VALIDATOR_FLAGS`` records
    # ``require_culmination=False``: a prerequisite topic exists to be
    # already known, so there is nothing for it to consolidate), so the
    # same line here would be a keyword prefix reading MODEL-AUTHORED
    # free text — Rule 1's "keyword vocabularies that classify content".
    # A pre-concept the model happens to call "Culmination of the
    # map-reading skills of earlier years" is an ordinary prerequisite,
    # and its item must be able to land on it. The target set is
    # therefore exactly what ``build_evidence`` offered the inventory.
    concepts_payload = [
        {
            "pre_concept_id": str(row.get("_pre_concept_id") or ""),
            "pre_topic_id": str(row.get("_semantic_topic_id") or ""),
            "concept_title": _normal(row.get("concept_title")),
            "description": premap_mod._description_of(
                row.get("concept_details")
            ),
        }
        for row in rows
    ]
    known_concept_ids = {row["pre_concept_id"] for row in concepts_payload}

    allotments: dict[str, str] = {}
    rationales: dict[str, str] = {}

    def _decide_batch(start: int):
        batch = inventory[start:start + _ALLOT_BATCH_SIZE]
        payload = {
            "stage": "prelearn.analyse.allot",
            "rules": _allot_rules(rules_suffix),
            "pre_concepts": concepts_payload,
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
        premap_mod._refuse_source_qids(
            payload, qids, where="the Pre analysis allotment payload"
        )
        decision = kernel.decide(
            kind="prelearn.analyse.allot",
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
        for item_id, verdict, flags in batch_result:
            allotments[item_id] = str(verdict.get("pre_concept_id") or "")
            rationale = _normal(verdict.get("rationale"))
            if rationale:
                rationales[item_id] = rationale
            if flags:
                review_flags.setdefault(item_id, []).extend(flags)

    progress.log(
        f"Pre-Learning analysis: {len(allotments)} item(s) allotted, each "
        "to exactly one pre-concept (R4); a pre-concept with no item "
        "carries no analysis section, which is the design (Q1).",
        level="success",
    )
    return {
        "inventory": inventory,
        "allotments": allotments,
        "rationales": rationales,
        "review_flags": review_flags,
    }


def stamp(
    rows: list[dict[str, Any]],
    analysis: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    """Q1 stamping for the Pre lane: the section exists only where allotted.

    The SAME canonical mechanics the Post lane uses — the item texts of
    an allotted row are merged into ONE
    ``// Misconception/ Error Analysis:`` section (Misconceptions before
    Error Analysis, items in item-id order) with no visible item id, and
    the ids ride the row-private ``_aegis_analysis_allotments`` marker
    instead. Achieving Mastery is untouched: it lives inside the
    Description section this never rewrites.

    Returns the per-pre-concept review flags the items contributed, for
    the caller to stamp after its no-extraction guard has run.
    """
    from . import assemble as assemble_mod
    from .. import concept_refiner as cr
    from .. import concept_validator
    from .. import katex_rules as kr

    result = dict(analysis or {})
    inventory = [
        dict(item)
        for item in result.get("inventory") or []
        if isinstance(item, Mapping)
    ]
    allotments = {
        str(item_id): str(concept_id)
        for item_id, concept_id in (result.get("allotments") or {}).items()
    }
    item_flags = {
        str(item_id): [str(flag) for flag in flags]
        for item_id, flags in (result.get("review_flags") or {}).items()
        if isinstance(flags, list)
    }
    item_by_id = {str(item.get("item_id") or ""): item for item in inventory}
    row_by_id = {
        str(row.get("_pre_concept_id") or ""): row for row in rows
    }
    row_flags: dict[str, list[str]] = {}

    unallotted = sorted(set(item_by_id) - set(allotments))
    if unallotted:
        raise PreAnalysisError(
            "pre-learning analysis item(s) reached stamping without an "
            "allotment (R4 exact-once): " + ", ".join(unallotted)
            + " (unreachable after fixer seam — report if hit)"
        )
    unknown = sorted(set(allotments) - set(item_by_id))
    if unknown:
        raise PreAnalysisError(
            "pre-learning analysis allotment(s) name item(s) missing from "
            "the inventory: " + ", ".join(unknown)
            + " (unreachable after fixer seam — report if hit)"
        )

    items_by_row: dict[str, list[dict[str, Any]]] = {}
    for item_id in sorted(allotments):
        concept_id = allotments[item_id]
        row = row_by_id.get(concept_id)
        if row is None:
            raise PreAnalysisError(
                f"pre-learning analysis allotment for {item_id} names a "
                f"pre-concept that does not exist: {concept_id}"
                " (unreachable after fixer seam — report if hit)"
            )
        items_by_row.setdefault(concept_id, []).append(item_by_id[item_id])

    for row in rows:
        items = items_by_row.get(str(row.get("_pre_concept_id") or ""))
        if items is None:
            # Q1: a pre-concept the inventory allotted nothing to carries
            # no analysis section at all. Not a gap — the design.
            continue
        misconceptions = assemble_mod._join_analysis_texts([
            str(item.get("text") or "")
            for item in items
            if str(item.get("kind") or "") == "misconception"
        ])
        error_analyses = assemble_mod._join_analysis_texts([
            str(item.get("text") or "")
            for item in items
            if str(item.get("kind") or "") == "error_analysis"
        ])
        combined: list[str] = []
        if misconceptions:
            combined.append(f"Misconceptions: {misconceptions}")
        if error_analyses:
            combined.append(f"Error Analysis: {error_analyses}")
        if not combined:
            continue
        sections = cr.split_sections(str(row.get("concept_details") or ""))
        sections.append((
            "Misconception/ Error Analysis",
            kr.repair_unwrapped_math("; ".join(combined)),
        ))
        row["concept_details"] = cr.join_sections(sections)
        row[concept_validator.ANALYSIS_ALLOTMENTS_FIELD] = [
            str(item.get("item_id") or "") for item in items
        ]
        pre_id = str(row.get("_pre_concept_id") or "")
        for item in items:
            for flag in item_flags.get(str(item.get("item_id") or ""), []):
                flags = row_flags.setdefault(pre_id, [])
                if flag not in flags:
                    flags.append(flag)
    return row_flags
