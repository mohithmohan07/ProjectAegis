"""Step 11 — the language-mode topology adapter (docs/aegis-restructure.md Q9).

Turns the Architect's recorded ``language_mode`` selection (poem | prose)
into a model-authored, source-grounded, replayable **topology plan** before
concept skeleton authoring. One pipeline, adapted instructions: the plan
rides the Architect slot transport into the sealed Phase 3 envelope
(``language_topology_plan``), and its hash joins the run's instruction
identity so a changed plan can never silently replay old verdicts.

Authorities (build-sprint guardrails):

* The **Architect** owns the mode. This module never inspects rhyme, line
  lengths, keywords, or filenames to choose poem vs prose — it consumes the
  recorded selection and rationale.
* The **author model** owns every boundary: stanzas, meaning-carrying line
  units, sizeable prose breaks, episodes, analytical facets, and the
  threading home of grammar/listening/writing components. No line counts,
  no vocabularies.
* **Mechanics stay deterministic**: schema shape, id uniqueness, role-field
  presence, closed-world block accounting, the exact final-topic display
  template, hashing, caching, artifact persistence.
* **Critic advises** (Q10): dissent lands in the plan's ``review_flags``.
* **The Fixer** (Q13) resolves an exhausted correction with one recorded
  best-judgment plan; provider unavailability is a named pre-spend halt —
  never a fallback to line pairing or generic expository topology.

Deliberate sprint scope (docs/residue-ledger.md): the plan's semantic roles
are recorded in the plan artifact and ride the envelope; per-record role
transport into skeleton rows and the Output-02/04 identity-composition
repair are recorded residues, not silently claimed.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .. import config
from . import canonical_source
from . import progress

LANGUAGE_ADAPTER_VERSION = "language-topology-1"
PLAN_FILENAME = "source.language-plan.json"
PLAN_SLOT_KEY = "language_topology_plan"

THREADING_PLACEMENT_CONTEXTS = (
    "opening_pre_reading",
    "contextual_support",
    "post_reading",
)

_MODES = ("poem", "prose")
SEMANTIC_ROLES = (
    "ordinary",
    "stanza_culmination",
    "detailed_analysis",
    "chapter_culmination",
)

_CACHE_DIR = config.DATA_DIR / "language-plan-cache"

DETAILED_ANALYSIS_TEMPLATE = "Detailed Analysis of '{name}'"

SOURCE_FAITHFUL_PROSE_ROUTING_POLICY = """\
SOURCE-FAITHFUL PROSE ROUTING
- For short prose, a new dialogue line, reaction, plan, or decision beat is not
  by itself a Topic or concept boundary. Merge adjacent beats that form one
  compact teaching arc. Split only at a substantial transition a teacher would
  plan independently; do not fragment the work merely to label every event.
- Preserve an opening Warm-up's source position and pre-reading purpose. Home
  it in the opening/preparatory activity context or the earliest truthful
  teaching concept. Never re-parent it to a later plot event merely because it
  echoes the story's theme, moral, or final decision.
- A separately headed instructional grammar block that directly teaches a
  rule or pattern through a coherent progression of explanation, examples,
  tables, or practice may remain its own source-aligned Topic or concept. Do
  not force that mini-unit under story analysis or Language & Literary
  Devices. Incidental usage notes and small language exercises without an
  independent teaching progression remain threaded to their truthful home.
- Every threaded support verdict must record placement_context. Use
  opening_pre_reading for a separate opening Warm-up whose purpose precedes
  the reading, contextual_support for material taught with its destination
  concept, and post_reading for a source-owned follow-up whose after-reading
  phase must remain visible. Destination alone must not erase that distinction.
"""


class LanguagePlanError(RuntimeError):
    """Named pre-spend halt: the topology plan could not be authored."""


def _provider_ready() -> bool:
    return config.use_live_generation()


def _max_output_tokens() -> int:
    try:
        return max(
            2000,
            int(os.environ.get("AEGIS_LANGUAGE_PLAN_MAX_TOKENS", "24000")),
        )
    except ValueError:
        return 24000


def _call_provider(
    *,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    purpose: str = "source_adjudication",
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """``model`` overrides the configured model for THIS call only. The
    Fixer supplies ``phase3.fixer.fixer_model()``, which shares the active
    model; ``None`` keeps the deployment's configured model."""

    from . import canonical_source_phase22 as phase22

    return phase22._openai_multimodal_json(
        system=system,
        prompt=prompt,
        pages=[],
        response_schema=schema,
        purpose=purpose,  # type: ignore[arg-type]
        max_tokens=max_tokens or _max_output_tokens(),
        model=model,
    )


# ---------------------------------------------------------------------------
# Prompts (frozen wording; hashed into the plan identity)
# ---------------------------------------------------------------------------

_AUTHOR_SYSTEM = f"""\
You are the language-chapter topology author for a school pipeline.

The Architect has already selected the chapter's mode (poem or prose) —
you never re-decide it. You receive the complete ordered source blocks of
one literature chapter, the chapter's tasks, and the Architect's
instructions. Author the chapter's teaching topology as a plan:

POEM mode: one topic per stanza, in reading order. Within each stanza,
one concept per pair of lines that jointly conveys a meaning (literal and
metaphorical reading, line analysis, setup), concepts for poetic devices
and vocabulary grounded in those lines, and one concept with role
stanza_culmination (rhyme/form, the stanza's elements together). The
bodies of teaching must not coincide. "Pair of lines" is the poem's
meaning-bearing unit — judge it from the work, never by counting lines.

PROSE mode: topics at the story's sizeable teaching breaks — coherent
changes in scene, conflict, perspective, or development a teacher would
plan separately. Within each topic, one concept per significant plot
episode with a dramatic, source-grounded title and a rationale for why it
is independently teachable. A short source does not need a separate Topic
or concept for each dialogue, reaction, plan, or decision beat.

{SOURCE_FAITHFUL_PROSE_ROUTING_POLICY}

BOTH modes: the final topic is the Detailed Analysis topic with its
display name exactly as given in the request (detailed_analysis_title).
Create the applicable standard concepts in order: Theme / Central Idea,
Plot / Development of Ideas, Characterisation / Speaker, Setting &
Atmosphere, Language & Literary Devices, and a final concept with role
chapter_culmination. Do not invent a character cast for a speakerless poem
or a plot for a non-narrative work — record the work-appropriate
interpretation under the analytical slot instead.

Grammar, listening, and writing components printed in the chapter are
threaded when they are supporting occurrences: for each such block, record
the destination concept whose content is its best teaching home, the skill
it teaches, and why. Apply the separately headed grammar-mini-unit exception
above when the source actually supplies an independent teaching progression.

Every concept carries an achieving_mastery line: what it takes to master
this concept. Every source block must be accounted for: inside a concept's
source_block_ids, a topic's evidence_block_ids, threaded_components, or
non_teaching_block_ids. Nothing is silently dropped."""

_CORRECTION_DIRECTIVE = """\
You are correcting your previous language-topology plan. The listed
defects are MECHANICAL contract violations (unaccounted blocks, unknown
ids, missing roles or mastery lines, a wrong final-topic display name).
Return the complete corrected plan. Do not change judgments the defects
do not touch."""

_FIXER_DIRECTIVE = """\
You are The Fixer for a language-chapter topology plan. The ordinary
author pass could not produce a mechanically valid plan. Read the source
blocks, the Architect's mode and rationale, the failed plan, and the
defect list, and record your best-judgment COMPLETE valid plan. Your
decision is recorded verbatim and flagged for human review; the run
completes with it. Never drop a stanza, episode, component, task, or
block — account for everything."""


def _correction_system() -> str:
    """Give correction the complete, currently installed author policy.

    ``language_topology_grade_contract`` replaces ``_AUTHOR_SYSTEM`` at
    runtime.  Composing here, instead of freezing a second copy at import,
    keeps the bounded correction on the exact same live curriculum policy.
    """
    return f"{_AUTHOR_SYSTEM}\n\n{_CORRECTION_DIRECTIVE}"


def _fixer_system() -> str:
    """Give The Fixer the complete, currently installed author policy."""
    return f"{_AUTHOR_SYSTEM}\n\n{_FIXER_DIRECTIVE}"

_CRITIC_SYSTEM = """\
You are the independent critic of a language-chapter topology plan.
Review the plan against the source blocks: stanza/break boundaries that
misread the work, meaning units that overlap or miss teaching, episode
grain that is too fine or too coarse, threading that homes a component
badly, a short prose source fragmented into one unit per minor beat, an
opening Warm-up re-parented to a later plot event, a coherent separately
headed grammar mini-unit buried in story analysis, or analytical slots
filled with invented content. Your dissent is an advisory review flag for
a human reviewer — it blocks nothing — so dissent freely and precisely."""


# ---------------------------------------------------------------------------
# Evidence + schema (mechanics)
# ---------------------------------------------------------------------------

def _content_blocks(canonical: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks = [
        block for block in canonical.get("blocks") or []
        if isinstance(block, dict) and str(block.get("kind") or "") != "layout"
    ]
    blocks.sort(key=lambda block: int(block.get("source_start") or 0))
    return blocks


def _task_payloads(canonical: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "qid": str(task.get("qid") or ""),
            "prompt": str(task.get("display_prompt") or task.get("raw_prompt") or ""),
        }
        for task in canonical.get("tasks") or []
        if isinstance(task, dict) and task.get("qid")
    ]


def _concept_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "plan_concept_id", "display_name", "semantic_role", "facets",
            "source_block_ids", "task_qids", "achieving_mastery", "rationale",
        ],
        "properties": {
            "plan_concept_id": {"type": "string"},
            "display_name": {"type": "string"},
            "semantic_role": {"type": "string", "enum": list(SEMANTIC_ROLES)},
            "facets": {"type": "array", "items": {"type": "string"}},
            "source_block_ids": {"type": "array", "items": {"type": "string"}},
            "task_qids": {"type": "array", "items": {"type": "string"}},
            "achieving_mastery": {"type": "string"},
            "rationale": {"type": "string"},
        },
    }


def plan_schema() -> dict[str, Any]:
    return {
        "name": "language_topology_plan",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "topics", "threaded_components", "non_teaching_block_ids",
                "notes",
            ],
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "plan_topic_id", "display_name",
                            "evidence_block_ids", "concepts",
                        ],
                        "properties": {
                            "plan_topic_id": {"type": "string"},
                            "display_name": {"type": "string"},
                            "evidence_block_ids": {
                                "type": "array", "items": {"type": "string"},
                            },
                            "concepts": {
                                "type": "array", "items": _concept_schema(),
                            },
                        },
                    },
                },
                "threaded_components": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "block_id", "destination_plan_concept_id",
                            "placement_context", "skill", "rationale",
                        ],
                        "properties": {
                            "block_id": {"type": "string"},
                            "destination_plan_concept_id": {"type": "string"},
                            "placement_context": {
                                "type": "string",
                                "enum": list(THREADING_PLACEMENT_CONTEXTS),
                                "description": (
                                    "The source-aligned learning phase of this "
                                    "support occurrence. A separate opening "
                                    "Warm-up is opening_pre_reading even though "
                                    "it is transported to an opening concept."
                                ),
                            },
                            "skill": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                },
                "non_teaching_block_ids": {
                    "type": "array", "items": {"type": "string"},
                },
                "notes": {"type": "string"},
            },
        },
        "strict": True,
    }


def critic_schema() -> dict[str, Any]:
    return {
        "name": "language_plan_review",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "dissents"],
            "properties": {
                "verdict": {"type": "string", "enum": ["concur", "dissent"]},
                "dissents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["target", "reason"],
                        "properties": {
                            "target": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                },
            },
        },
        "strict": True,
    }


def _prompt_identity_sha() -> str:
    material = "␟".join([
        LANGUAGE_ADAPTER_VERSION,
        _AUTHOR_SYSTEM,
        _correction_system(),
        _fixer_system(),
        _CRITIC_SYSTEM,
        json.dumps(plan_schema(), sort_keys=True),
        json.dumps(critic_schema(), sort_keys=True),
    ])
    return canonical_source._sha256_text(material)


def detailed_analysis_title(work_name: str) -> str:
    return DETAILED_ANALYSIS_TEMPLATE.format(name=str(work_name or "").strip())


# ---------------------------------------------------------------------------
# Deterministic validation (mechanics only — shape, ids, accounting)
# ---------------------------------------------------------------------------

def plan_defects(
    plan: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, str]],
    *,
    work_name: str,
) -> list[str]:
    defects: list[str] = []
    block_ids = {str(b.get("block_id") or "") for b in blocks}
    task_qids = {t["qid"] for t in tasks}

    topics = [t for t in plan.get("topics") or [] if isinstance(t, Mapping)]
    if not topics:
        defects.append("the plan has no topics")
        return defects

    seen_topic_ids: set[str] = set()
    seen_concept_ids: set[str] = set()
    first_plan_concept_id = ""
    accounted: set[str] = set()
    roles_last_topic: list[str] = []

    for index, topic in enumerate(topics):
        tid = str(topic.get("plan_topic_id") or "")
        if not tid or tid in seen_topic_ids:
            defects.append(f"topic {index + 1} has a missing/duplicate id")
        seen_topic_ids.add(tid)
        for bid in topic.get("evidence_block_ids") or []:
            if str(bid) not in block_ids:
                defects.append(f"topic {tid} names unknown block {bid}")
            accounted.add(str(bid))
        concepts = [
            c for c in topic.get("concepts") or [] if isinstance(c, Mapping)
        ]
        if not concepts:
            defects.append(f"topic {tid} has no concepts")
        for concept in concepts:
            cid = str(concept.get("plan_concept_id") or "")
            if not cid or cid in seen_concept_ids:
                defects.append(
                    f"concept in topic {tid} has a missing/duplicate id"
                )
            seen_concept_ids.add(cid)
            if cid and not first_plan_concept_id:
                first_plan_concept_id = cid
            role = str(concept.get("semantic_role") or "")
            if role not in SEMANTIC_ROLES:
                defects.append(f"concept {cid} has invalid role {role!r}")
            if index == len(topics) - 1:
                roles_last_topic.append(role)
            if not str(concept.get("achieving_mastery") or "").strip():
                defects.append(f"concept {cid} has no achieving_mastery line")
            for bid in concept.get("source_block_ids") or []:
                if str(bid) not in block_ids:
                    defects.append(f"concept {cid} names unknown block {bid}")
                accounted.add(str(bid))
            for qid in concept.get("task_qids") or []:
                if str(qid) not in task_qids:
                    defects.append(f"concept {cid} names unknown task {qid}")

    final = topics[-1]
    expected = detailed_analysis_title(work_name)
    if str(final.get("display_name") or "") != expected:
        defects.append(
            "the final topic display name must be exactly "
            f"{expected!r} (got {final.get('display_name')!r})"
        )
    if "detailed_analysis" not in roles_last_topic:
        defects.append(
            "the final topic carries no concept with role detailed_analysis"
        )
    if "chapter_culmination" not in roles_last_topic:
        defects.append(
            "the final topic carries no concept with role chapter_culmination"
        )

    concept_ids = seen_concept_ids
    seen_threaded_blocks: set[str] = set()
    for row in plan.get("threaded_components") or []:
        if not isinstance(row, Mapping):
            continue
        bid = str(row.get("block_id") or "")
        if bid not in block_ids:
            defects.append(f"threaded component names unknown block {bid}")
        if bid in seen_threaded_blocks:
            defects.append(
                f"threaded component repeats source block {bid}; one source "
                "occurrence requires exactly one threading verdict"
            )
        seen_threaded_blocks.add(bid)
        accounted.add(bid)
        dest = str(row.get("destination_plan_concept_id") or "")
        if dest not in concept_ids:
            defects.append(
                f"threaded component {bid} routes to unknown concept {dest}"
            )
        placement = str(row.get("placement_context") or "")
        if placement not in THREADING_PLACEMENT_CONTEXTS:
            defects.append(
                f"threaded component {bid} has invalid placement_context "
                f"{placement!r}"
            )
        if (
            placement == "opening_pre_reading"
            and first_plan_concept_id
            and dest != first_plan_concept_id
        ):
            defects.append(
                f"threaded component {bid} is opening_pre_reading but routes "
                f"to {dest!r}; opening support must route to the first plan "
                f"concept {first_plan_concept_id!r}"
            )
    for bid in plan.get("non_teaching_block_ids") or []:
        if str(bid) not in block_ids:
            defects.append(f"non_teaching names unknown block {bid}")
        accounted.add(str(bid))

    unaccounted = sorted(block_ids - accounted)
    if unaccounted:
        defects.append(
            "unaccounted source blocks (closed world, R4): "
            + ", ".join(unaccounted[:12])
        )
    return defects


# ---------------------------------------------------------------------------
# Cache + artifact (decide once)
# ---------------------------------------------------------------------------

def plan_sha256(plan_core: Mapping[str, Any]) -> str:
    return canonical_source._sha256_text(
        json.dumps(dict(plan_core), ensure_ascii=False, sort_keys=True)
    )


def _decision_key(
    canonical: Mapping[str, Any],
    instruction_set_sha256: str,
    mode: str,
    *,
    work_name: str = "",
) -> str:
    """The plan's replay identity (audit F8).

    Beyond raw source + instructions, the key binds everything the author's
    verdict actually depends on: the work name (the Detailed Analysis
    template), the ordered block identity, the adjudicated task/QID
    identity (a QX policy change that recovers a new ask must re-author the
    plan), and the compiler/contract versions that shaped the bundle.
    """
    contract = canonical.get("source_contract") or {}
    blocks = _content_blocks(canonical)
    tasks = _task_payloads(canonical)
    material = "␟".join([
        LANGUAGE_ADAPTER_VERSION,
        config.OPENAI_MODEL,
        str(contract.get("source_sha256") or ""),
        str(contract.get("schema_version") or ""),
        str(contract.get("compiler_version") or ""),
        str(contract.get("hardening_version") or ""),
        str(instruction_set_sha256 or ""),
        mode,
        " ".join(str(work_name or "").split()),
        _prompt_identity_sha(),
        "␞".join(
            f"{b.get('block_id')}:{b.get('raw_sha256')}" for b in blocks
        ),
        "␞".join(f"{t['qid']}:{t['prompt']}" for t in tasks),
    ])
    return canonical_source._sha256_text(material)


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def _read_cached(key: str) -> dict[str, Any] | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("status") != "sealed":
        return None
    return value


def _write_cached(key: str, value: dict[str, Any]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    canonical_source._atomic_write(
        _cache_path(key), canonical_source._json_text(value)
    )


def write_plan_artifact(
    plan: Mapping[str, Any], artifact_dir: str | Path
) -> Path:
    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PLAN_FILENAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(plan), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def load_plan_artifact(artifact_dir: str | Path) -> dict[str, Any] | None:
    path = Path(artifact_dir) / PLAN_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def plan_slot_text(plan: Mapping[str, Any]) -> str:
    """The plan body as the envelope slot text (compact, deterministic)."""
    core = plan.get("plan") if isinstance(plan.get("plan"), Mapping) else plan
    return json.dumps(dict(core), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# The authoring pipeline
# ---------------------------------------------------------------------------

def _author_request(
    blocks: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, str]],
    *,
    mode: str,
    rationale: str,
    slots: Mapping[str, Any],
    work_name: str,
) -> str:
    return json.dumps({
        "mode": mode,
        "mode_rationale": rationale,
        "detailed_analysis_title": detailed_analysis_title(work_name),
        "architect_slots": {
            "subject_topology_guidance": str(
                slots.get("subject_topology_guidance") or ""
            ),
            "grade_band_vocabulary": str(
                slots.get("grade_band_vocabulary") or ""
            ),
            "board_publication_conventions": str(
                slots.get("board_publication_conventions") or ""
            ),
            "chapter_cautions": list(slots.get("chapter_cautions") or []),
        },
        "blocks": [
            {
                "block_id": str(b.get("block_id") or ""),
                "source_order": position,
                "source_start": int(b.get("source_start") or 0),
                "kind": str(b.get("kind") or ""),
                "text": str(b.get("raw_text") or ""),
            }
            for position, b in enumerate(blocks, start=1)
        ],
        "tasks": list(tasks),
    }, ensure_ascii=False)


def author_language_plan(
    canonical: Mapping[str, Any],
    *,
    instruction_set: Mapping[str, Any],
    work_name: str,
) -> dict[str, Any]:
    """Author (or replay) the plan. Returns the sealed plan envelope.

    Raises :class:`LanguagePlanError` on the named impossibilities:
    provider unavailable, no block evidence, or a plan contract that could
    not be made mechanically applicable even by the Fixer.
    """
    slots = instruction_set.get("slots") or {}
    mode_obj = slots.get("language_mode") or {}
    mode = str(mode_obj.get("mode") or "")
    rationale = str(mode_obj.get("rationale") or "")
    if mode not in _MODES:
        raise LanguagePlanError(
            f"language mode {mode!r} is not a poem/prose selection; the "
            "adapter only runs on the Architect's literary modes"
        )
    instruction_sha = str(instruction_set.get("instruction_set_sha256") or "")
    blocks = _content_blocks(canonical)
    tasks = _task_payloads(canonical)
    if not blocks:
        raise LanguagePlanError(
            "the canonical bundle carries no source blocks; a closed-world "
            "topology plan is impossible without them"
        )

    key = _decision_key(
        canonical, instruction_sha, mode, work_name=work_name
    )
    cached = _read_cached(key)
    if cached is not None:
        defects = plan_defects(
            cached.get("plan") or {}, blocks, tasks, work_name=work_name
        )
        # Audit F8: the sealed hash must authenticate the sealed body — a
        # well-formed edit that kept the old plan_sha256 is a miss.
        if str(cached.get("plan_sha256") or "") != plan_sha256(
            cached.get("plan") or {}
        ):
            defects = ["sealed plan_sha256 does not match the sealed plan"]
        if not defects:
            sealed = dict(cached)
            sealed["replayed"] = True
            return sealed
        progress.log(
            "Sealed language plan no longer matches the bundle; "
            "re-authoring.",
            level="warning",
        )

    if not _provider_ready():
        raise LanguagePlanError(
            "no live provider is configured; the language topology plan "
            "cannot be authored and no deterministic topology may stand in"
        )

    request = _author_request(
        blocks, tasks, mode=mode, rationale=rationale, slots=slots,
        work_name=work_name,
    )
    try:
        plan = _call_provider(
            system=_AUTHOR_SYSTEM, prompt=request, schema=plan_schema()
        )
    except Exception as exc:
        raise LanguagePlanError(
            f"language plan author pass failed: {exc}"
        ) from exc

    correction_history: list[dict[str, Any]] = []
    fixer_record: dict[str, Any] | None = None
    defects = plan_defects(plan, blocks, tasks, work_name=work_name)
    if defects:
        correction_history.append({
            "stage": "deterministic_validation", "defects": list(defects),
        })
        try:
            plan = _call_provider(
                system=_correction_system(),
                prompt=json.dumps({
                    "defects": defects,
                    "previous_plan": plan,
                    "request": json.loads(request),
                }, ensure_ascii=False),
                schema=plan_schema(),
            )
        except Exception as exc:
            raise LanguagePlanError(
                f"language plan bounded correction failed: {exc}"
            ) from exc
        defects = plan_defects(plan, blocks, tasks, work_name=work_name)
    if defects:
        # Audit F14: the durable Fixer reason records the defects ACTUALLY
        # sent to it (post-correction), and the round itself is appended to
        # the history — never the author's initial defect list.
        fixer_defects = list(defects)
        correction_history.append({
            "stage": "fixer", "defects": copy.deepcopy(fixer_defects),
        })
        try:
            from .phase3 import fixer as fixer_mod

            plan = _call_provider(
                system=_fixer_system(),
                prompt=json.dumps({
                    "defects": fixer_defects,
                    "failed_plan": plan,
                    "mode": mode,
                    "mode_rationale": rationale,
                    "request": json.loads(request),
                }, ensure_ascii=False),
                schema=plan_schema(),
                # The Fixer follows the same active model as the run.
                model=fixer_mod.fixer_model(),
            )
        except Exception as exc:
            raise LanguagePlanError(
                f"the Fixer call for the language plan failed: {exc}"
            ) from exc
        defects = plan_defects(plan, blocks, tasks, work_name=work_name)
        if defects:
            raise LanguagePlanError(
                "the language plan contract could not be made mechanically "
                "applicable after the bounded correction and Fixer round: "
                + "; ".join(defects[:8])
            )
        fixer_record = {
            "decision_sha256": plan_sha256(plan),
            "reason": "; ".join(str(d) for d in fixer_defects[:8]),
        }

    review_flags: list[str] = []
    critic: dict[str, Any]
    try:
        critic = _call_provider(
            system=_CRITIC_SYSTEM,
            prompt=json.dumps({
                "plan": plan,
                "request": json.loads(request),
            }, ensure_ascii=False),
            schema=critic_schema(),
            purpose="advisory_critic",
        )
    except Exception as exc:  # advisory: never a gate, never a halt (Q10)
        # Audit F14: a critic that never ran is recorded as UNAVAILABLE,
        # never as a concurrence that did not happen. (Only the model's own
        # schema is limited to concur|dissent; this record is ours.)
        critic = {"verdict": "unavailable", "dissents": [],
                  "unavailable": str(exc)}
        review_flags.append(
            f"language plan critic unavailable ({type(exc).__name__}); "
            "the plan stands unreviewed"
        )
    if str(critic.get("verdict") or "") == "dissent":
        for dissent in critic.get("dissents") or []:
            review_flags.append(
                "language plan critic dissent: "
                f"{dissent.get('target')}: {dissent.get('reason')}"
            )
    if fixer_record is not None:
        review_flags.append(
            "language plan decided by The Fixer's best-judgment round; "
            "review the plan artifact"
        )

    sealed = {
        "status": "sealed",
        "adapter_version": LANGUAGE_ADAPTER_VERSION,
        "model": config.OPENAI_MODEL,
        "mode": mode,
        "mode_rationale": rationale,
        "instruction_set_sha256": instruction_sha,
        "prompt_sha256": _prompt_identity_sha(),
        "source_sha256": str(
            (canonical.get("source_contract") or {}).get("source_sha256")
            or ""
        ),
        "decision_key": key,
        "plan": plan,
        "plan_sha256": plan_sha256(plan),
        "correction_history": correction_history,
        "fixer_decision": fixer_record,
        "critic": critic,
        "review_flags": review_flags,
        "replayed": False,
    }
    _write_cached(key, {k: v for k, v in sealed.items() if k != "replayed"})
    return sealed


def ensure_language_plan(
    canonical: Mapping[str, Any],
    *,
    instruction_set: Mapping[str, Any],
    work_name: str,
    artifact_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Replay the persisted plan when its identity matches, else author.

    Decide-once: the artifact replays only while its decision key matches
    the current canonical + instruction identity and its own hash
    verifies; anything else re-authors (live) or halts with the named
    reason.
    """
    slots = instruction_set.get("slots") or {}
    mode = str((slots.get("language_mode") or {}).get("mode") or "")
    instruction_sha = str(instruction_set.get("instruction_set_sha256") or "")
    if artifact_dir is not None and mode in _MODES:
        stored = load_plan_artifact(artifact_dir)
        if (
            isinstance(stored, dict)
            and str(stored.get("adapter_version") or "")
            == LANGUAGE_ADAPTER_VERSION
            and str(stored.get("decision_key") or "")
            == _decision_key(
                canonical, instruction_sha, mode, work_name=work_name
            )
            and str(stored.get("plan_sha256") or "")
            == plan_sha256(stored.get("plan") or {})
            # Audit F8: replay only a plan that still satisfies the
            # mechanical contract against the CURRENT bundle and work name.
            and not plan_defects(
                stored.get("plan") or {},
                _content_blocks(canonical),
                _task_payloads(canonical),
                work_name=work_name,
            )
        ):
            replayed = dict(stored)
            replayed["replayed"] = True
            return replayed
    built = author_language_plan(
        canonical,
        instruction_set=instruction_set,
        work_name=work_name,
    )
    if artifact_dir is not None:
        write_plan_artifact(
            {k: v for k, v in built.items() if k != "replayed"},
            artifact_dir,
        )
    return built
