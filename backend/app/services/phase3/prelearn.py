"""Pass 2.7 — Prelearn: the running pre-requisite capture (doc §4, Q3).

docs/aegis-restructure.md §4 Phase 03: *"While every phase above runs,
the model keeps a running capture of pre-requisite elements: things
taught in previous years, vocabulary, and the basics needed to
understand a line or concept."* Q3 decided one Build Concepts run
produces the Post map and the Pre material together, so the capture is
made INSIDE the same run, not derived afterwards from its conclusions.

**Pre/Post is a property of an OUTPUT, not of this run** (spec T2):
``UploadJob.learning_kind`` stays what it is and the run is hardwired to
"post"; what this pass records is the prerequisite material a later step
turns into the Pre map. Nothing here re-lanes the run.

*Running* is implemented literally. One capture decision is made at each
stage boundary, over **that stage's own evidence** — the evidence that
stage actually had in hand:

* after **Settle** — the chapter's source blocks, its topics, and the
  settled concepts;
* after **Host** — the mined Type/Case assignment units (this is the
  only place in phase 3 that sees them) and each question's own wording;
* after **Place** — the pooled Container-02 material: the hub items and
  the unclaimed figure blocks, which is exactly the pool Place placed
  from;
* after **Analyse** — the chapter's misconception/error-analysis
  inventory.

No single existing pass sees that union, and the union is available here
only because the runner controls each payload. The alternative shapes
were rejected deliberately (spec T1): riding a ``prerequisites`` field
on the existing passes would re-key Settle/Host/Place/Analyse at once
(``kernel.decision_key`` hashes the whole payload and ``store.put``
never overwrites — a re-key re-bills the entire run), and one post-hoc
pass over the finished Post map would read prerequisites out of
conclusions rather than out of evidence.

A final ``prelearn.merge`` decision consolidates the per-stage captures
into one prerequisite set. **The model** judges which captures are the
same prerequisite seen from two stages — never string matching, never a
normalized-text dedupe. R4 holds mechanically: the merge checker refuses
any response that does not account for every capture exactly once, so a
prerequisite a learner would need can never be dropped in the join.

There is NO count quota of any kind — a chapter with few prerequisites
yields few items, an empty capture is legal and ships cleanly, and
nothing is ever padded to look richer (Rule 1: no volume-derived
structure). Decide-once through the kernel, advisory critic whose
dissent ships as review flags (Q10), The Fixer on exhaustion (Q13). The
recorded output (``source.phase3-prelearn-capture.json``) rides beside
the decision store and out through the runner's return.

Three choices recorded here rather than left as omissions:

* **The dissent channel is two-part.** ``review_flags`` addresses rows;
  ``stage_flags`` records the decision's dissent whole. An empty capture
  has no row, and "the evidence plainly assumes a prerequisite you
  missed" is the critic's most valuable verdict precisely there, so a
  row-only channel would discard it (Q10).
* **This pass can end a run, and that is deliberate.** ``merge`` is
  called unwrapped: a ``kernel.ContractError`` here fails the whole Post
  run even though no Post row depends on the capture. It is reached only
  when the bounded corrections AND The Fixer both failed to produce a
  contract-satisfying decision — the kernel's protocol impossibility,
  which CLAUDE.md permits stopping on. Swallowing it instead would drop
  a learner's prerequisite silently, which R4 forbids outright; the
  weaker failure is the one that is loud.
* **The settle capture's source blocks are NOT truncated**, where its
  siblings window text (``host.py`` 400/600, ``polish.py`` 800). Reading
  what prose ASSUMES needs the prose; the payload is the largest in
  phase 3 for that reason.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from .. import progress
from .. import prelearning_capture_policy as capture_policy
from .. import prelearning_foundation_policy as foundation_policy
from .. import generation_quality_policy as quality
from . import evidence as visual_evidence

POLICY_VERSION = "prelearn-1"

# The stage boundaries the capture runs at, in run order. Each name is a
# decision unit_id (``stage:<name>``) and a key of the recorded output.
STAGES = ("settle", "host", "place", "analyse")

# The payload keys each stage's capture carries, and the field under each
# that holds a citable id. This table IS the per-stage evidence contract:
# a stage's checker resolves citations against these keys of its own
# payload only, so a capture can never cite an id from another stage's
# evidence.
_CITABLE_ID_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "settle": (
        ("source_blocks", "block_id"),
        ("topics", "topic_id"),
        ("settled_concepts", "concept_id"),
    ),
    "host": (
        ("type_case_units", "unit_id"),
        ("type_case_units", "type_id"),
        ("type_case_units", "case_id"),
        ("type_case_units", "qids"),
        ("questions", "qid"),
    ),
    "place": (
        ("pooled_hub_items", "item_ref"),
        ("pooled_figures", "item_ref"),
    ),
    "analyse": (
        ("analysis_inventory", "item_id"),
    ),
}


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _chapter_calibration(env: Mapping[str, Any]) -> dict[str, str]:
    """Project sealed chapter metadata for fresh foundation decisions."""

    metadata = env.get("metadata") if isinstance(env, Mapping) else None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return {
        key: _normal(metadata.get(key))
        for key in ("board", "grade", "subject", "unit", "chapter_title")
    }


def mint_prerequisite_ids(count: int) -> list[str]:
    """Deterministic positional prerequisite ids: PR-0001, PR-0002, …"""
    return [f"PR-{index:04d}" for index in range(1, count + 1)]


def _description_of(details: object) -> str:
    match = re.search(
        r"Description:\s*(.*?)(?=\n[A-Z][A-Za-z ]{2,24}:|//|$)",
        str(details or ""),
        re.DOTALL,
    )
    return _normal(match.group(1)) if match else ""


def _evidence_list(value: object, *, dedupe: bool = True) -> list[str]:
    """The cited-evidence field, normalized to a list of id strings.

    ``dedupe=False`` keeps repeats visible: the merge checker's
    exact-once accounting must see a capture named twice inside one row,
    which a collapsing read would hide.
    """
    if isinstance(value, (str, bytes)):
        rows: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        rows = list(value)
    elif value in (None, ""):
        rows = []
    else:
        rows = [value]
    cited: list[str] = []
    for row in rows:
        text = _normal(row)
        if text and (not dedupe or text not in cited):
            cited.append(text)
    return cited


# ---------------------------------------------------------------------------
# per-stage evidence


def _legacy_stage_evidence(
    env: Mapping[str, Any],
    stage: str,
    *,
    settled: list[Mapping[str, Any]] | None = None,
    analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The evidence THAT stage had in hand, and nothing from another.

    Each branch builds only the keys ``_CITABLE_ID_KEYS[stage]`` names,
    so the payload of one stage's capture never carries another stage's
    material. No counts, ratios, or size-derived fields ride any of them.
    """
    if stage not in _CITABLE_ID_KEYS:
        raise ValueError(f"unknown prelearn capture stage {stage!r}")
    if stage == "settle":
        text_by_id = {
            str(row.get("block_id") or ""): str(row.get("display_text") or "")
            for row in env["canonical"]["blocks"]
            if isinstance(row, Mapping)
        }
        source_blocks = [
            {
                "block_id": str(row.get("block_id") or ""),
                "topic_id": str(row.get("topic_id") or ""),
                "kind": str(row.get("kind") or ""),
                "text": text_by_id.get(str(row.get("block_id") or ""), ""),
            }
            for row in env["graph"]["blocks"]
            if isinstance(row, Mapping) and str(row.get("block_id") or "")
        ]
        from . import place as place_mod

        rows = list(settled or [])
        concept_ids = place_mod.mint_concept_ids(rows)
        return {
            "source_blocks": source_blocks,
            "topics": [
                {
                    "topic_id": str(row.get("topic_id") or ""),
                    "title": str(row.get("title") or ""),
                }
                for row in env["graph"]["topics"]
                if isinstance(row, Mapping)
            ],
            "settled_concepts": [
                {
                    "concept_id": concept_id,
                    "topic_id": str(row.get("_semantic_topic_id") or ""),
                    "concept_title": _normal(row.get("concept_title")),
                    "parent_concept": _normal(row.get("parent_concept")),
                    "description": _description_of(row.get("concept_details")),
                }
                for concept_id, row in zip(concept_ids, rows)
            ],
        }

    if stage == "host":
        from . import host as host_mod

        questions: list[dict[str, Any]] = []
        for item in (env.get("inventory") or {}).get("items") or []:
            if not isinstance(item, Mapping):
                continue
            qid = str(item.get("qid") or "").strip()
            if not qid:
                continue
            questions.append({
                "qid": qid,
                "kind": str(item.get("source_kind") or ""),
                "printed_under_topic": str(
                    item.get("source_location_topic_title")
                    or item.get("topic_hint")
                    or ""
                ),
                "text": str(
                    item.get("polished_task")
                    or item.get("normalized_task")
                    or item.get("raw_task")
                    or ""
                )[:600],
            })
        return {
            "type_case_units": [
                {
                    "unit_id": str(unit.get("unit_id") or ""),
                    "type_id": str(unit.get("type_id") or ""),
                    "case_id": str(unit.get("case_id") or ""),
                    "topic_id": str(unit.get("topic_id") or ""),
                    "task": _normal(unit.get("task")),
                    "pattern": _normal(unit.get("pattern")),
                    "concept_match_hint": _normal(
                        unit.get("concept_match_hint")
                    ),
                    "qids": [str(qid) for qid in unit.get("qids") or []],
                }
                for unit in host_mod.derive_units(env)
            ],
            "questions": questions,
        }

    if stage == "place":
        from . import place as place_mod

        return {
            # Place pooled BOTH halves of Container 02 before deciding
            # (place.py: ``pool = [*hub_pool(env), *figure_pool(env)]``),
            # so the capture at this boundary is shown both. Dropping the
            # unclaimed figures would leave a caption that assumes prior
            # knowledge — the doc's own worked example for this pass is an
            # illustration (aegis-restructure.md §4) — readable by no
            # capture at any stage: the settle capture sees figure blocks
            # only as ids with empty display text.
            "pooled_hub_items": [
                {
                    "item_ref": str(entry.get("item_ref") or ""),
                    "source_kind": str(entry.get("source_kind") or ""),
                    "activity_origin": bool(entry.get("activity_origin")),
                    "source_label": _normal(entry.get("source_label")),
                    "text": str(entry.get("text") or ""),
                    "image_captions": [
                        _normal(image.get("caption"))
                        for image in entry.get("images") or []
                        if isinstance(image, Mapping)
                        and _normal(image.get("caption"))
                    ],
                }
                for entry in place_mod.hub_pool(env)
            ],
            "pooled_figures": [
                {
                    "item_ref": str(entry.get("item_ref") or ""),
                    "caption": _normal(entry.get("caption")),
                    "image_captions": [
                        _normal(image.get("caption"))
                        for image in entry.get("images") or []
                        if isinstance(image, Mapping)
                        and _normal(image.get("caption"))
                    ],
                }
                for entry in place_mod.figure_pool(env)
            ],
        }

    # stage == "analyse"
    return {
        "analysis_inventory": [
            {
                "item_id": str(item.get("item_id") or ""),
                "kind": str(item.get("kind") or ""),
                "text": _normal(item.get("text")),
            }
            for item in (analysis or {}).get("inventory") or []
            if isinstance(item, Mapping) and str(item.get("item_id") or "")
        ],
    }


def stage_evidence(
    env: Mapping[str, Any], stage: str, *,
    settled: list[Mapping[str, Any]] | None = None,
    analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep legacy replay exact; new runs carry complete owned evidence."""
    packet = _legacy_stage_evidence(env, stage, settled=settled, analysis=analysis)
    if not capture_policy.active(env):
        return packet
    if stage == "settle":
        blocks = {
            str(row.get("block_id") or ""): row
            for row in env["canonical"]["blocks"] if isinstance(row, Mapping)
        }
        if quality.active(env):
            projected = {row["block_id"] for row in packet["source_blocks"]}
            # Canonical evidence belongs to this sealed chapter even when a
            # downstream topology omitted its ownership. Do not silently make
            # that omission an eligibility decision for prior learning.
            packet["source_blocks"].extend(
                {
                    "block_id": block_id,
                    "topic_id": str(row.get("topic_id") or ""),
                    "kind": str(row.get("kind") or ""),
                    "text": visual_evidence.block_text(row),
                }
                for block_id, row in blocks.items()
                if block_id and block_id not in projected
            )
        for row in packet["source_blocks"]:
            original = blocks.get(row["block_id"], {})
            row["text"] = visual_evidence.block_text(original)
            row["source_context"] = visual_evidence.block_context(original)
        for row, original in zip(packet["settled_concepts"], settled or []):
            row["concept_details"] = str(original.get("concept_details") or "")
            row["source_block_ids"] = copy.deepcopy(original.get("_source_block_ids") or [])
    elif stage == "host":
        from . import host as host_mod

        items = {
            str(row.get("qid") or ""): row
            for row in (env.get("inventory") or {}).get("items") or []
            if isinstance(row, Mapping)
        }
        for row in packet["questions"]:
            original = items.get(row["qid"], {})
            row["text"] = str(original.get("polished_task") or original.get("normalized_task") or original.get("raw_task") or "")
            # The source already owns these options, context, figures and
            # relationships. Copy the record rather than guess an allowlist.
            row["source_task"] = copy.deepcopy(dict(original))
        packet["type_case_units"] = copy.deepcopy(host_mod.derive_units(env))
    elif stage == "place":
        from . import place as place_mod

        packet["pooled_hub_items"] = copy.deepcopy(place_mod.hub_pool(env))
        packet["pooled_figures"] = copy.deepcopy(place_mod.figure_pool(env))
    else:
        packet["analysis_inventory"] = [
            copy.deepcopy(dict(row)) for row in (analysis or {}).get("inventory") or []
            if isinstance(row, Mapping) and str(row.get("item_id") or "")
        ]
    return packet


def indexed_evidence(stage: str, packet: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve every citable namespace to the exact stage evidence it names."""
    index: dict[str, list[dict[str, Any]]] = {}
    for section, field in _CITABLE_ID_KEYS.get(stage, ()):
        for row in packet.get(section) or []:
            if not isinstance(row, Mapping):
                continue
            value = row.get(field)
            for ref in value if isinstance(value, (list, tuple)) else [value]:
                if not ref:
                    continue
                record = {"stage": stage, "section": section, "content": copy.deepcopy(dict(row))}
                entries = index.setdefault(str(ref), [])
                if record not in entries:
                    entries.append(record)
    return index


def resolve_evidence(index: Mapping[str, Any], cited: set[str]) -> dict[str, Any]:
    """Follow explicit known-ID relationships, including task/block owners.

    A Type citation carries its QIDs and a concept carries its source blocks.
    Preserve that closure without selecting evidence by wording or proximity.
    Cycles and shared references are emitted once, in the index's stable order.
    """
    wanted: set[str] = set()
    pending = list(sorted(cited))

    def references(value: Any):
        if isinstance(value, str):
            if value in index:
                yield value
        elif isinstance(value, Mapping):
            for child in value.values():
                yield from references(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                yield from references(child)

    while pending:
        ref = pending.pop()
        if ref in wanted or ref not in index:
            continue
        wanted.add(ref)
        pending.extend(references(index[ref]))
    return {ref: copy.deepcopy(entries) for ref, entries in index.items() if ref in wanted}


def citable_ids(stage: str, evidence: Mapping[str, Any]) -> set[str]:
    """Every id a capture at ``stage`` may cite — from its own payload."""
    known: set[str] = set()
    for section, field in _CITABLE_ID_KEYS.get(stage, ()):  # noqa: B007
        for row in evidence.get(section) or []:
            if not isinstance(row, Mapping):
                continue
            value = row.get(field)
            values = value if isinstance(value, (list, tuple)) else [value]
            for entry in values:
                text = _normal(entry)
                if text:
                    known.add(text)
    return known


# ---------------------------------------------------------------------------
# mechanics-only checkers


def _capture_checker(
    known_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: positional ids, non-empty text, citations resolve.

    Whether an element genuinely is a prerequisite — and how many the
    chapter has — is the model's authored judgment (the advisory critic
    reviews it). An EMPTY capture is legal: a stage whose evidence
    carries no prerequisite yields none and is never padded.
    """

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("prerequisites")
        if not isinstance(rows, list):
            return ["response has no prerequisites array"]
        expected = mint_prerequisite_ids(len(rows))
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                defects.append("a prerequisite entry is not an object")
                continue
            item_id = str(row.get("prerequisite_id") or "")
            if item_id != expected[position]:
                defects.append(
                    f"prerequisite at position {position + 1} carries id "
                    f"{item_id or '<empty>'!r}; ids are the positional "
                    f"mint PR-0001, PR-0002, … (expected "
                    f"{expected[position]})"
                )
            label = item_id or expected[position]
            if not _normal(row.get("text")):
                defects.append(f"{label} has empty text")
            cited = _evidence_list(row.get("evidence"))
            if not cited:
                defects.append(
                    f"{label} cites no evidence; every prerequisite names "
                    "the ids in this stage's evidence it was read from"
                )
                continue
            unknown = [entry for entry in cited if entry not in known_ids]
            if unknown:
                defects.append(
                    f"{label} cites {', '.join(unknown)}, which "
                    "this stage's evidence does not contain; cite only ids "
                    "from the request"
                )
        return defects

    return check


def _merge_checker(
    capture_refs: list[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only, plus R4's exact-once accounting over the captures.

    Every per-stage capture is consolidated into exactly one merged
    prerequisite: a capture named twice is a contract defect, and a
    capture named by nothing is a silent loss of a prerequisite a learner
    would need. How the captures group is entirely the model's judgment.
    """
    expected = set(capture_refs)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("prerequisites")
        if not isinstance(rows, list):
            return ["response has no prerequisites array"]
        minted = mint_prerequisite_ids(len(rows))
        seen: set[str] = set()
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                defects.append("a prerequisite entry is not an object")
                continue
            item_id = str(row.get("prerequisite_id") or "")
            if item_id != minted[position]:
                defects.append(
                    f"prerequisite at position {position + 1} carries id "
                    f"{item_id or '<empty>'!r}; ids are the positional "
                    f"mint PR-0001, PR-0002, … (expected {minted[position]})"
                )
            label = item_id or minted[position]
            if not _normal(row.get("text")):
                defects.append(f"{label} has empty text")
            refs = _evidence_list(row.get("captures"), dedupe=False)
            if not refs:
                defects.append(
                    f"{label} names no capture_ref; every merged "
                    "prerequisite names the stage captures it consolidates"
                )
                continue
            for ref in refs:
                if ref not in expected or ref in seen:
                    defects.append(
                        f"{label} names unknown or repeated capture_ref "
                        f"{ref}"
                    )
                    continue
                seen.add(ref)
        missing = sorted(expected - seen)
        if missing:
            # R4: every stage capture is consolidated exactly once — an
            # unconsolidated capture is a contract defect, never a silent
            # drop of a prerequisite a learner would need.
            defects.append(
                "unconsolidated stage capture(s): " + ", ".join(missing)
            )
        return defects

    return check


# ---------------------------------------------------------------------------
# live adapters


def _live_capture(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PRELEARN_CAPTURE_SYSTEM + _policy_instruction(payload), prompts.render(payload),
        purpose="concept_mapping",
        **_vision_kwargs(payload),
    )


def _live_merge(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PRELEARN_MERGE_SYSTEM + _policy_instruction(payload), prompts.render(payload),
        purpose="concept_mapping",
        **_vision_kwargs(payload),
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PRELEARN_CRITIC_SYSTEM + _policy_instruction(payload), prompts.render(payload),
        purpose="advisory_critic",
        **_vision_kwargs(payload),
    )


def _policy_instruction(payload: Mapping[str, Any]) -> str:
    return (("\n" + capture_policy.CAPTURE_INSTRUCTION) if payload.get("capture_policy") == capture_policy.VERSION else "") + capture_policy.boundary_instruction(payload)


def _vision_kwargs(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"image_urls": visual_evidence.image_inputs(payload)} if "visual_evidence" in payload else {}


_CAPTURE_RULES = {
    "settle": (
        "You are reading what SETTLE had in hand: the chapter's source "
        "blocks, its topics in teaching order, and the settled concepts "
        "with their teaching descriptions."
    ),
    "host": (
        "You are reading what HOST had in hand: the chapter's mined "
        "Type/Case assignment units — the task patterns the chapter "
        "actually sets — and each question's own wording, kind, and "
        "printed position."
    ),
    "place": (
        "You are reading what PLACE had in hand: the chapter's pooled "
        "Container-02 material — its activities, experiments, and info "
        "hubs, with their own full text and image captions, and the "
        "unclaimed figure blocks with their captions."
    ),
    "analyse": (
        "You are reading what ANALYSE had in hand: the chapter's "
        "inventory of distinct misconceptions and error analyses — what "
        "a learner is likely to get wrong here."
    ),
}


def _capture_rules(stage: str, rules_suffix: str) -> str:
    return (
        "Phase 03: keep the run's running capture of PRE-REQUISITE "
        "elements — what a learner must already know BEFORE this chapter "
        "can be understood. Three families: things taught in previous "
        "years, vocabulary the chapter uses without teaching, and the "
        "basics needed to understand a particular line or concept. "
        + _CAPTURE_RULES[stage]
        + " Read that evidence for what it ASSUMES rather than for what "
        "it teaches: a term used as if already known, a procedure the "
        "text expects the learner to be able to carry out, a fact from "
        "an earlier year the explanation rests on. Never restate this "
        "chapter's own NEW teaching as a prerequisite — with one "
        "deliberate exception (owner ruling, 2026-08-27): material the "
        "chapter itself presents as revision of EARLIER learning, such as "
        "an opening basics/recap passage that re-teaches what a previous "
        "year established before the new teaching begins, is prior "
        "knowledge stated in the chapter's own words, and what it revises "
        "IS captured as prerequisites. Judge that from what the passage "
        "does with the learner's existing knowledge, never from a banner "
        "word alone: a warm-up that opens the new material is not "
        "revision, and a revision passage without any banner still is. "
        "Name the element "
        "precisely enough to be taught on its own — never a vague "
        "'basic knowledge of the topic'. Which elements this evidence "
        "genuinely assumes is entirely your judgment: this evidence may "
        "assume few prerequisites or none at all, and an element must "
        "never be invented to fill space; an empty prerequisites list is "
        "a legitimate answer. Mint prerequisite_id positionally as "
        "PR-0001, PR-0002, … in the order you list elements. evidence is "
        "the list of ids from THIS request the element was read from — "
        "cite only ids that appear here. rationale states what in that "
        "evidence assumes the element without teaching it."
        + rules_suffix
    )


def _merge_rules(rules_suffix: str) -> str:
    return (
        "Phase 03: consolidate the run's running prerequisite capture "
        "into ONE prerequisite set for the chapter. Each capture was "
        "made at a different stage of the run, over that stage's own "
        "evidence, so the SAME prerequisite is often captured twice — "
        "seen once in the chapter's prose and again in the task it is "
        "needed for. Judge which captures are the same prerequisite by "
        "what the elements MEAN, never by how similarly they are worded: "
        "two differently-phrased captures of one assumed idea are one "
        "prerequisite, and two similarly-phrased captures of genuinely "
        "different assumed ideas stay two. Consolidate EVERY capture "
        "exactly once — a capture that belongs with no other is its own "
        "single-capture prerequisite, never dropped. Write each merged "
        "text as the prerequisite itself, precise enough to be taught on "
        "its own; when captures merge, write the text that covers what "
        "all of them assume. Mint prerequisite_id positionally as "
        "PR-0001, PR-0002, … in the order you list them. captures is the "
        "list of capture_ref values from this request that this "
        "prerequisite consolidates. rationale states why those captures "
        "are one prerequisite (or, for a single capture, what it "
        "assumes)." + rules_suffix
    )


# ---------------------------------------------------------------------------
# public entries


def capture_stage(
    env: Mapping[str, Any],
    stage: str,
    *,
    settled: list[Mapping[str, Any]] | None = None,
    analysis: Mapping[str, Any] | None = None,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """One capture decision at one stage boundary, over that stage's own
    evidence.

    Returns ``{"stage": stage, "items": [{"prerequisite_id", "text",
    "evidence", "rationale"}], "review_flags": {prerequisite_id:
    [flags]}, "stage_flags": [flags]}``. A stage whose evidence carries
    nothing citable skips the decision (place.py's empty-pool
    precedent); an empty capture over real evidence is equally legal —
    never padded.

    ``review_flags`` is the row-addressable projection of this
    decision's dissent; ``stage_flags`` is the same dissent recorded
    whole, at the decision. The two overlap deliberately. Broadcasting
    onto rows alone would DROP the critic's dissent exactly when it
    matters most (Q10): the critic is asked about prerequisites the
    evidence assumes but the capture MISSED, and a capture that missed
    everything has no row for that flag to land on.
    """
    from . import fixer as fixer_mod

    env = envelope_mod.validate(env)
    if stage not in _CITABLE_ID_KEYS:
        raise ValueError(f"unknown prelearn capture stage {stage!r}")
    evidence = stage_evidence(env, stage, settled=settled, analysis=analysis)
    enhanced = capture_policy.active(env)
    evidence_receipt = {
        "capture_policy": capture_policy.VERSION,
        "evidence_packet": copy.deepcopy(evidence),
    } if enhanced else {}
    known_ids = citable_ids(stage, evidence)
    if not known_ids:
        return {
            "stage": stage,
            "items": [],
            "review_flags": {},
            "stage_flags": [],
            **evidence_receipt,
        }

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_capture
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    from . import prompts as prompts_mod

    rules_suffix = (
        prompts_mod.instruction_rules_suffix(
            env, slots=prompts_mod.PRE_LEARNING_SLOTS,
        )
        if foundation_policy.fields(env)
        else prompts_mod.instruction_rules_suffix(env)
    )

    payload = {
        "stage": f"prelearn.capture:{stage}",
        **capture_policy.boundary_fields(env),
        "rules": _capture_rules(stage, rules_suffix),
        "evidence": evidence,
    }
    if foundation_policy.fields(env):
        # Fresh foundation policy decisions must receive the same grade
        # calibration that the empty-capture audit already receives.
        payload["chapter"] = _chapter_calibration(env)
    if enhanced:
        payload["capture_policy"] = capture_policy.VERSION
        payload["rules"] += " " + capture_policy.CAPTURE_INSTRUCTION
    if quality.active(env):
        payload["rules"] += "\n" + capture_policy.QUALITY_INSTRUCTION
    decide = visual_evidence.decide_with_visual_evidence if enhanced else kernel.decide
    decision = decide(
        kind="prelearn.capture",
        unit_id=f"stage:{stage}",
        envelope_sha256=envelope_sha,
        payload=payload,
        provider=provider,
        checker=_capture_checker(known_ids),
        critic=critic,
        store=store,
        policy_version=POLICY_VERSION
        + (";" + capture_policy.VERSION if enhanced else "")
        + (";" + foundation_policy.VERSION if foundation_policy.fields(env) else "")
        + (";" + quality.VERSION if quality.active(env) else ""),
        fixer=fixer,
    )
    items: list[dict[str, Any]] = []
    for row in decision["response"].get("prerequisites") or []:
        if not isinstance(row, Mapping):
            continue
        items.append({
            "prerequisite_id": str(row.get("prerequisite_id") or ""),
            "text": _normal(row.get("text")),
            "evidence": _evidence_list(row.get("evidence")),
            "rationale": _normal(row.get("rationale")),
        })
    flags = list(decision.get("review_flags") or [])
    review_flags = (
        {item["prerequisite_id"]: list(flags) for item in items}
        if flags else {}
    )
    progress.log(
        f"Prerequisites: the capture at {stage} holds {len(items)} "
        "element(s) this stage's evidence assumes without teaching "
        "(model-judged; thin evidence is never padded)."
    )
    return {
        "stage": stage,
        "items": items,
        "review_flags": review_flags,
        "stage_flags": flags,
        **evidence_receipt,
    }


def merge(
    env: Mapping[str, Any],
    captures: list[Mapping[str, Any]],
    *,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, Any]:
    """Consolidate the per-stage captures into one prerequisite set.

    Returns ``{"captures": {stage: [items]}, "prerequisites":
    [{"prerequisite_id", "text", "evidence", "rationale", "stages",
    "captures"}], "review_flags": {prerequisite_id: [flags]},
    "stage_flags": {decision: [flags]}}``. With nothing captured
    anywhere the merge decision is not spent and the empty set ships —
    a chapter whose prerequisites are genuinely thin is never padded to
    look richer.

    ``stage_flags`` is the chapter-level record of every advisory
    dissent this capture drew, keyed by the decision that drew it: a
    stage name for a capture, ``merge`` for this consolidation. It ships
    even when there is no prerequisite row to carry a flag — an
    all-empty capture that the critic rejected is precisely the run a
    reviewer must be told about, and ``review_flags`` alone would report
    it clean (Q10).
    """
    from . import fixer as fixer_mod

    env = envelope_mod.validate(env)
    by_stage: dict[str, list[dict[str, Any]]] = {}
    enhanced = capture_policy.active(env)
    packets: dict[str, Any] = {}
    capture_rows: list[dict[str, Any]] = []
    for capture in captures or []:
        if not isinstance(capture, Mapping):
            continue
        stage = str(capture.get("stage") or "")
        items = [
            dict(item) for item in capture.get("items") or []
            if isinstance(item, Mapping)
        ]
        by_stage[stage] = items
        if enhanced and isinstance(capture.get("evidence_packet"), Mapping):
            packets[stage] = copy.deepcopy(capture["evidence_packet"])
        for item in items:
            capture_rows.append({
                "capture_ref": f"{stage}:{item['prerequisite_id']}",
                "stage": stage,
                "text": item.get("text") or "",
                "evidence": list(item.get("evidence") or []),
                "rationale": item.get("rationale") or "",
            })

    stage_flags: dict[str, list[str]] = {}
    decision_flags: dict[str, list[str]] = {}
    for capture in captures or []:
        if not isinstance(capture, Mapping):
            continue
        stage = str(capture.get("stage") or "")
        for item_id, flags in (capture.get("review_flags") or {}).items():
            stage_flags[f"{stage}:{item_id}"] = list(flags)
        carried_stage = [str(flag) for flag in capture.get("stage_flags") or []]
        if carried_stage:
            decision_flags[stage] = carried_stage

    if not capture_rows:
        progress.log(
            "Prerequisites: no stage captured a prerequisite element; the "
            "chapter's prerequisite set is empty and is never padded."
        )
        # An all-empty capture still ships whatever the critic said about
        # it: there is no row for the dissent to land on, and dropping it
        # here would report a rejected run as clean (Q10).
        return {
            "captures": by_stage,
            "prerequisites": [],
            "review_flags": {},
            "stage_flags": decision_flags,
            **({"capture_policy": capture_policy.VERSION, "evidence_packets": packets} if enhanced else {}),
        }

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_merge
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    from . import prompts as prompts_mod

    rules_suffix = (
        prompts_mod.instruction_rules_suffix(
            env, slots=prompts_mod.PRE_LEARNING_SLOTS,
        )
        if foundation_policy.fields(env)
        else prompts_mod.instruction_rules_suffix(env)
    )

    payload = {
        "stage": "prelearn.merge",
        **capture_policy.boundary_fields(env),
        "rules": _merge_rules(rules_suffix),
        "captures": capture_rows,
    }
    if foundation_policy.fields(env):
        payload["chapter"] = _chapter_calibration(env)
    if quality.active(env):
        payload["rules"] += "\n" + capture_policy.QUALITY_INSTRUCTION
    if enhanced:
        payload["capture_policy"] = capture_policy.VERSION
        payload["prior_review_flags"] = copy.deepcopy(decision_flags)
        all_evidence: dict[str, Any] = {}
        for stage, packet in packets.items():
            for ref, entries in indexed_evidence(stage, packet).items():
                all_evidence.setdefault(ref, []).extend(entries)
        payload["evidence_index"] = resolve_evidence(
            all_evidence, {ref for row in capture_rows for ref in row["evidence"]},
        )
    decide = visual_evidence.decide_with_visual_evidence if enhanced else kernel.decide
    decision = decide(
        kind="prelearn.merge",
        unit_id="chapter",
        envelope_sha256=envelope_sha,
        payload=payload,
        provider=provider,
        checker=_merge_checker(
            [row["capture_ref"] for row in capture_rows]
        ),
        critic=critic,
        store=store,
        policy_version=POLICY_VERSION
        + (";" + capture_policy.VERSION if enhanced else "")
        + (";" + foundation_policy.VERSION if foundation_policy.fields(env) else "")
        + (";" + quality.VERSION if quality.active(env) else ""),
        fixer=fixer,
    )
    by_ref = {row["capture_ref"]: row for row in capture_rows}
    merge_flags = list(decision.get("review_flags") or [])
    if merge_flags:
        decision_flags["merge"] = list(merge_flags)
    prerequisites: list[dict[str, Any]] = []
    review_flags: dict[str, list[str]] = {}
    for row in decision["response"].get("prerequisites") or []:
        if not isinstance(row, Mapping):
            continue
        refs = _evidence_list(row.get("captures"))
        # Bookkeeping only: the merged element inherits the union of its
        # captures' cited evidence and the stages it was seen at, so the
        # recorded set stays traceable back to the evidence each stage
        # actually held.
        evidence: list[str] = []
        stages: list[str] = []
        carried: list[str] = []
        for ref in refs:
            source = by_ref.get(ref)
            if source is None:
                continue
            if source["stage"] not in stages:
                stages.append(source["stage"])
            for cited in source["evidence"]:
                if cited not in evidence:
                    evidence.append(cited)
            carried.extend(stage_flags.get(ref) or [])
        item_id = str(row.get("prerequisite_id") or "")
        prerequisites.append({
            "prerequisite_id": item_id,
            "text": _normal(row.get("text")),
            "evidence": evidence,
            "rationale": _normal(row.get("rationale")),
            "stages": stages,
            "captures": refs,
        })
        flags = [*carried, *merge_flags]
        if flags:
            review_flags[item_id] = flags

    progress.log(
        f"Prerequisites: {len(capture_rows)} stage capture(s) consolidated "
        f"into {len(prerequisites)} prerequisite element(s), each capture "
        "accounted exactly once (R4).",
        level="success",
    )
    return {
        "captures": by_stage,
        "prerequisites": prerequisites,
        "review_flags": review_flags,
        "stage_flags": decision_flags,
        **({"capture_policy": capture_policy.VERSION, "evidence_packets": packets} if enhanced else {}),
    }
