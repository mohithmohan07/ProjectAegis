"""Pass 1 — Settle: topology, grounding, and content authoring.

Consumes the sealed envelope, decides every normal concept exactly once
through the kernel, and emits the settled row set — the artifact the old
pipeline knew as the validated final concept topology. Culminations are
derived, never decided. Stage 3 authors each concept's Description and
Achieving Mastery in a single decision grounded on the concept's own
source blocks — there is no separate description-refinement or mastery
pass under the rewrite. Misconceptions/Error Analysis are NOT authored
here: the chapter inventory pass (phase3/analyse.py, Q1) is the only
analysis mechanism, and Assemble stamps its allotments onto the rows.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from ... import config
from .. import concept_refiner as cr
from .. import katex_rules as kr
from .. import progress
from .. import semantic_confidence_policy as confidence_policy

_BATCH_SIZE = 12

_DECISIONS = {"keep", "refine", "split"}

# The Post-Learning language-plan seam writes these opaque audit fields onto
# every skeleton row it explicitly planned.  Settle does not interpret the
# literary role; it only uses the recorded identity to distinguish an
# authoritative one-concept culmination from the generic recap rows that the
# shared topology normally removes.
_LANGUAGE_PLAN_IDENTITY_FIELD = "_aegis_language_plan_identity"
_LANGUAGE_SEMANTIC_ROLE_FIELD = "_aegis_language_semantic_role"
_PLANNED_CULMINATION_ROLES = frozenset({
    "stanza_culmination",
    "topic_culmination",
    "chapter_culmination",
})

# The Q1 unbundling (docs/aegis-restructure.md §12 Q1) removed
# misconception_error_analysis from settle.author's response schema and
# house string. The suffix re-keys every stored authoring decision so a
# pre-Q1 record can never replay its stale schema past the new checker.
AUTHOR_POLICY_SUFFIX = "-q1"

_ANALYSIS_SPLIT = re.compile(
    r"\s*//\s*Misconception/?\s*Error Analysis:\s*", re.IGNORECASE
)

_FIELD_LABEL = re.compile(
    r"^(?:Description|Achieving Mastery|Misconception/?\s*Error Analysis)"
    r"\s*:\s*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# envelope projections


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _description_of(details: object) -> str:
    match = re.search(
        r"Description:\s*(.*?)(?=\n[A-Z][A-Za-z ]{2,24}:|//|$)",
        str(details or ""),
        re.DOTALL,
    )
    return _normal(match.group(1)) if match else ""


def _strip_analysis(details: str) -> str:
    return _ANALYSIS_SPLIT.split(details, maxsplit=1)[0].rstrip()


def resolve_topic_id(
    row: Mapping[str, Any],
    topics: list[Mapping[str, Any]],
) -> str:
    """Resolve a row's semantic topic id, falling back to its topic title."""

    explicit = str(row.get("_semantic_topic_id") or "")
    if explicit:
        return explicit
    wanted = _normal(row.get("topic")).casefold()
    for topic in topics:
        if _normal(topic.get("title")).casefold() == wanted:
            return str(topic.get("topic_id") or "")
    return ""


def _topic_rows(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in env["graph"]["topics"]
        if isinstance(row, Mapping) and str(row.get("topic_id") or "")
    ]


def _blocks_by_topic(env: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    text_by_id = {
        str(row.get("block_id") or ""): str(row.get("display_text") or "")
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in env["graph"]["blocks"]:
        if not isinstance(row, Mapping):
            continue
        block_id = str(row.get("block_id") or "")
        topic_id = str(row.get("topic_id") or "")
        if not block_id or not topic_id:
            continue
        grouped.setdefault(topic_id, []).append({
            "block_id": block_id,
            "subtopic_id": str(row.get("subtopic_id") or ""),
            "kind": str(row.get("kind") or ""),
            "text": text_by_id.get(block_id, ""),
        })
    return grouped


def _block_texts(env: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(row.get("block_id") or ""): str(row.get("display_text") or "")
        for row in env["canonical"]["blocks"]
        if isinstance(row, Mapping)
    }


def _known_block_ids(env: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("block_id") or "")
        for row in env["graph"]["blocks"]
        if isinstance(row, Mapping) and str(row.get("block_id") or "")
    }


def _batched(values: list, size: int = _BATCH_SIZE) -> list[list]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _is_planned_culmination(row: Mapping[str, Any]) -> bool:
    """Return whether the sealed literary plan explicitly owns this recap.

    A title alone is not authority: generic concept maps also carry authored
    ``Culmination`` rows.  Requiring both the opaque plan identity and its
    recorded culmination role keeps this exception limited to the model plan
    that materialized the Phase-3 envelope.
    """

    identity = row.get(_LANGUAGE_PLAN_IDENTITY_FIELD)
    return bool(
        isinstance(identity, Mapping)
        and str(identity.get("plan_topic_id") or "").strip()
        and str(identity.get("plan_concept_id") or "").strip()
        and _normal(row.get(_LANGUAGE_SEMANTIC_ROLE_FIELD))
        in _PLANNED_CULMINATION_ROLES
    )


def _culminations_to_author(
    rows: list[dict[str, Any]],
    *,
    normal_concept_count: int,
) -> list[dict[str, Any]]:
    """Select the one authored recap this topic may carry.

    Shared maps retain the existing rule: a one-concept topic has nothing to
    consolidate.  The sole exception is a culmination explicitly present in
    the sealed literary plan, where the row represents the stanza/topic as a
    whole rather than a generated restatement of its only ordinary concept.
    At-most-one remains mechanical: an explicitly planned row outranks an
    unmarked row, and source order breaks duplicate ties.
    """

    if not rows:
        return []
    planned = [row for row in rows if _is_planned_culmination(row)]
    if normal_concept_count <= 0:
        return []
    if normal_concept_count == 1:
        return planned[:1]
    return (planned or rows)[:1]


# ---------------------------------------------------------------------------
# stage 1: topology



def _pin_flags(
    flags: list[str],
    batch_ids: list[str],
    row_id: str,
) -> list[str]:
    """Attach concept-specific flags to their concept; general ones to all.

    This stage found the bug (staging showed whole batches flagged for
    one concept's dissent); the one shared implementation now lives in
    ``kernel.pin_flags`` and every batched stage uses it.
    """
    return kernel.pin_flags(flags, batch_ids, row_id)


def _topology_checker(
    batch: list[dict[str, Any]],
) -> Callable[[Mapping[str, Any]], list[str]]:
    by_id = {row["concept_id"]: row for row in batch}

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("decisions")
        if not isinstance(rows, list):
            return ["response has no decisions array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a decision entry is not an object")
                continue
            concept_id = str(row.get("concept_id") or "")
            if concept_id not in by_id:
                defects.append(f"unknown concept_id {concept_id or '<empty>'}")
                continue
            if concept_id in seen:
                defects.append(f"{concept_id} decided more than once")
                continue
            seen.add(concept_id)
            decision = str(row.get("decision") or "").strip().lower()
            if decision not in _DECISIONS:
                defects.append(
                    f"{concept_id} decision {decision!r} is not one of "
                    "keep/refine/split"
                )
                continue
            try:
                confidence = float(row.get("confidence"))
            except (TypeError, ValueError):
                # A missing or non-numeric score is a SHAPE defect (the
                # bounded corrections ask for the number), never an honest
                # sub-floor score that ships flagged on the first attempt.
                defects.append(
                    f"{concept_id} confidence must be a number between "
                    "0 and 1"
                )
                confidence = None
            if confidence is not None and not confidence_policy.accepts(
                confidence
            ):
                defects.append(
                    f"[confidence] {concept_id} confidence "
                    f"{confidence:.3f} is below "
                    f"{confidence_policy.threshold_text()}"
                )
            segments = row.get("segments")
            if not isinstance(segments, list) or not segments:
                defects.append(f"{concept_id} has no segments")
                continue
            if decision in {"keep", "refine"} and len(segments) != 1:
                defects.append(
                    f"{concept_id} {decision} must carry exactly one segment"
                )
            if decision == "split" and len(segments) < 2:
                defects.append(
                    f"{concept_id} split must carry at least two segments"
                )
            for segment in segments:
                if not isinstance(segment, Mapping):
                    defects.append(f"{concept_id} segment is not an object")
                    continue
                if not _normal(segment.get("concept_title")):
                    defects.append(f"{concept_id} segment has no title")
                if not _description_of(segment.get("concept_details")):
                    defects.append(
                        f"{concept_id} segment has no Description claim"
                    )
            if decision == "keep" and segments and isinstance(
                segments[0], Mapping
            ):
                original = _description_of(by_id[concept_id]["concept_details"])
                kept = _description_of(segments[0].get("concept_details"))
                if original and kept and original.casefold() != (
                    kept.casefold()
                ):
                    defects.append(
                        f"{concept_id} keep decision rewrote the claim; a "
                        "changed claim must be a refine"
                    )
        missing = sorted(set(by_id) - seen)
        if missing:
            defects.append("undecided concept(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# stage 2: grounding


def _grounding_checker(
    concept_ids: list[str],
    *,
    topic_block_ids: set[str],
    known_block_ids: set[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = set(concept_ids)

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("concepts")
        if not isinstance(rows, list):
            return ["response has no concepts array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a grounding entry is not an object")
                continue
            concept_id = str(row.get("concept_id") or "")
            if concept_id not in expected or concept_id in seen:
                defects.append(
                    f"unknown or repeated concept_id {concept_id or '<empty>'}"
                )
                continue
            seen.add(concept_id)
            block_ids = [
                str(value)
                for value in row.get("source_block_ids") or []
                if str(value)
            ]
            if not block_ids:
                defects.append(
                    f"{concept_id} has no source block — ground on the "
                    "blocks that teach the claim: the concept's own topic "
                    "when it teaches it, otherwise the other_topic_blocks "
                    "that do; never return an empty grounding"
                )
            unknown_blocks = [
                b for b in block_ids if b not in known_block_ids
            ]
            if unknown_blocks:
                defects.append(
                    f"{concept_id} grounded on unknown block(s): "
                    + ", ".join(unknown_blocks[:4])
                )
            wrong_topic = [
                b for b in block_ids
                if b in known_block_ids and b not in topic_block_ids
            ]
            if wrong_topic:
                # Rule 4a: print position is provenance, so grounding
                # prefers the concept's own topic — the bounded corrections
                # push the model back there. But a concept whose material
                # is genuinely taught elsewhere (recovered chapter-opening
                # rows) must ship flagged with its honest grounding, never
                # fail the chapter.
                defects.append(
                    f"[confidence] {concept_id} grounded on block(s) "
                    "outside its topic: "
                    + ", ".join(wrong_topic[:4])
                    + " (if the concept's own topic teaches this claim, "
                    "ground there instead; if it does not, keep this "
                    "grounding and say so in reason)"
                )
            references = [
                str(value)
                for value in row.get("reference_block_ids") or []
                if str(value)
            ]
            unknown_refs = [b for b in references if b not in known_block_ids]
            if unknown_refs:
                defects.append(
                    f"{concept_id} cites unknown reference block(s): "
                    + ", ".join(unknown_refs[:4])
                )
            try:
                confidence = float(row.get("confidence"))
            except (TypeError, ValueError):
                # A missing or non-numeric score is a SHAPE defect (the
                # bounded corrections ask for the number), never an honest
                # sub-floor score that ships flagged on the first attempt.
                defects.append(
                    f"{concept_id} confidence must be a number between "
                    "0 and 1"
                )
                confidence = None
            if confidence is not None and not confidence_policy.accepts(
                confidence
            ):
                defects.append(
                    f"[confidence] {concept_id} confidence "
                    f"{confidence:.3f} is below "
                    f"{confidence_policy.threshold_text()}"
                )
        missing = sorted(expected - seen)
        if missing:
            defects.append("ungrounded concept(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# stage 3: content authoring (one decision per batch — description and
# mastery together, so neither can drift apart; learner analysis is the
# chapter inventory pass's alone, Q1)


_MATH_FORMAT_ISSUES = {
    "raw_latex",
    "raw_math_delimiter",
    "raw_math_expression",
    "unbalanced_katex",
    "nested_katex",
    "malformed_katex",
}


def _math_format_defect(concept_id: str, field: str, text: str) -> str:
    """Name wire-format violations deterministic repair cannot fix."""
    issues = _MATH_FORMAT_ISSUES.intersection(
        kr.rich_text_issues(kr.repair_unwrapped_math(text))
    )
    if not issues:
        return ""
    return (
        f"{concept_id} {field} violates the canonical rich-text wire "
        f"format ({', '.join(sorted(issues))}) — wrap EVERY mathematical "
        "expression exactly as [Katex] valid LaTeX [/Katex]; never emit "
        "raw TeX, $ delimiters, bare sub/superscripts, or bare equations "
        "outside those tags"
    )


def _authoring_checker(
    concept_ids: list[str],
    culmination_ids: list[str] | None = None,
) -> Callable[[Mapping[str, Any]], list[str]]:
    expected = set(concept_ids)
    expected_culms = set(culmination_ids or ())

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("rows")
        if not isinstance(rows, list):
            return ["response has no rows array"]
        if expected_culms:
            culm_rows = response.get("culminations")
            if not isinstance(culm_rows, list):
                defects.append("response has no culminations array")
                culm_rows = []
            culm_seen: set[str] = set()
            for row in culm_rows:
                if not isinstance(row, Mapping):
                    defects.append("a culmination entry is not an object")
                    continue
                culm_id = str(row.get("concept_id") or "")
                if culm_id not in expected_culms or culm_id in culm_seen:
                    defects.append(
                        "unknown or repeated culmination concept_id "
                        f"{culm_id or '<empty>'}"
                    )
                    continue
                culm_seen.add(culm_id)
                prose = _normal(row.get("consolidation"))
                if len(prose.split()) < 15:
                    defects.append(
                        f"{culm_id} consolidation is too thin — write a "
                        "short teaching paragraph tying the topic's "
                        "concepts together, not a name list"
                    )
            missing_culms = sorted(expected_culms - culm_seen)
            if missing_culms:
                defects.append(
                    "unauthored culmination(s): " + ", ".join(missing_culms)
                )
        seen: set[str] = set()
        mastery_seen: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("an authored entry is not an object")
                continue
            concept_id = str(row.get("concept_id") or "")
            if concept_id not in expected or concept_id in seen:
                defects.append(
                    f"unknown or repeated concept_id {concept_id or '<empty>'}"
                )
                continue
            seen.add(concept_id)

            description = _FIELD_LABEL.sub(
                "", _normal(row.get("concept_description"))
            )
            if len(description.split()) < 30:
                defects.append(
                    f"{concept_id} concept_description is too thin — write "
                    "the full teaching paragraph a writer could author book "
                    "sections, worksheets, notes, and slides from"
                )
            if re.search(
                r"(?:Achieving Mastery|Misconceptions?|Error Analysis)\s*:",
                description,
            ):
                defects.append(
                    f"{concept_id} concept_description must carry only the "
                    "teaching paragraph — mastery goes in its own field, "
                    "and Misconceptions/Error Analysis are never authored "
                    "here (the chapter inventory pass owns them)"
                )
            math_defect = _math_format_defect(
                concept_id, "concept_description", description
            )
            if math_defect:
                defects.append(math_defect)

            mastery = _FIELD_LABEL.sub(
                "", _normal(row.get("achieving_mastery"))
            )
            if not mastery:
                defects.append(f"{concept_id} achieving_mastery is empty")
            else:
                math_defect = _math_format_defect(
                    concept_id, "achieving_mastery", mastery
                )
                if math_defect:
                    defects.append(math_defect)
                key = mastery.casefold()
                if key in mastery_seen:
                    defects.append(
                        f"{concept_id} achieving_mastery repeats "
                        f"{mastery_seen[key]}'s — every concept needs its "
                        "own distinct mastery statement"
                    )
                else:
                    mastery_seen[key] = concept_id
        missing = sorted(expected - seen)
        if missing:
            defects.append("unauthored concept(s): " + ", ".join(missing))
        return defects

    return check


# ---------------------------------------------------------------------------
# live API adapters (production defaults; tests inject providers)


def _api_json(system: str, user: str) -> dict[str, Any]:
    from .. import generation

    return generation._openai_json(system, user, purpose="concept_mapping")


def _live_topology(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts

    return _api_json(prompts.TOPOLOGY_SYSTEM, prompts.render(payload))


def _live_grounding(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts

    return _api_json(prompts.GROUNDING_SYSTEM, prompts.render(payload))


def _live_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts

    return _api_json(prompts.ANALYSIS_SYSTEM, prompts.render(payload))


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.CRITIC_SYSTEM, prompts.render(payload),
        purpose="advisory_critic",
    )


# ---------------------------------------------------------------------------
# the Fixer seam F2: a skeleton row that resolves to no graph topic


def _fix_topic_resolution(
    row: dict[str, Any],
    topics: list[Mapping[str, Any]],
    *,
    row_index: int,
    fixer: kernel.Provider,
    store: kernel.DecisionStore,
    envelope_sha: str,
) -> str:
    """One recorded Fixer decision naming the graph topic hosting ``row``.

    Returns the chosen topic_id, or "" when the Fixer could not name a
    real topic after bounded attempts (the caller then raises exactly as
    a fixer-less run does — protocol impossibility).
    """

    from . import fixer as fixer_mod

    title = _normal(row.get("concept_title") or row.get("concept"))
    topic_ids = {
        str(topic.get("topic_id") or "")
        for topic in topics
        if str(topic.get("topic_id") or "")
    }
    blocked = (
        "skeleton row resolves to no graph topic: " + title[:80]
    )
    payload = {
        "fixer": True,
        "blocked_check": [blocked],
        "contract": {
            "kind": "fixer.topic_resolution",
            "rule": (
                "every skeleton row must belong to exactly one graph "
                "topic; name the topic_id whose material this concept "
                "belongs to. Response schema: {\"topic_id\", "
                "\"rationale\"}"
            ),
        },
        "row": {
            "topic": row.get("topic"),
            "parent_concept": row.get("parent_concept"),
            "concept_title": row.get("concept_title"),
            "concept_details": str(row.get("concept_details") or "")[:1200],
            "keywords": row.get("keywords"),
        },
        "topics": [
            {
                "topic_id": str(topic.get("topic_id") or ""),
                "title": str(topic.get("title") or ""),
            }
            for topic in topics
        ],
    }

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        chosen = str(response.get("topic_id") or "")
        if chosen not in topic_ids:
            defects.append(
                f"topic_id {chosen or '<empty>'!r} is not a graph topic; "
                "name one of: " + ", ".join(sorted(topic_ids))
            )
        if not str(response.get("rationale") or "").strip():
            defects.append("rationale is required")
        return defects

    try:
        decision = kernel.decide(
            kind="fixer.topic_resolution",
            unit_id=f"skeleton-row#{row_index}",
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=fixer,
            checker=check,
            store=store,
            policy_version=fixer_mod.FIXER_POLICY_VERSION,
        )
    except kernel.ContractError:
        return ""
    topic_id = str(decision["response"].get("topic_id") or "")
    title_by_id = {
        str(topic.get("topic_id") or ""): str(topic.get("title") or "")
        for topic in topics
    }
    rationale = " ".join(
        str(decision["response"].get("rationale") or "").split()
    )[:240]
    row.setdefault("_fixer_review_flags", []).append(
        f"fixer: blocked={blocked}; decided=hosted under topic "
        f"{topic_id} ({title_by_id.get(topic_id, '')[:60]})"
        + (f" — {rationale}" if rationale else "")
    )
    return topic_id


# ---------------------------------------------------------------------------
# the pass


def settle(
    env: Mapping[str, Any],
    *,
    topology_provider: kernel.Provider | None = None,
    grounding_provider: kernel.Provider | None = None,
    analysis_provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> list[dict[str, Any]]:
    """Settle every concept: one decision each, flags attached, no pauses."""

    from . import fixer as fixer_mod

    env = envelope_mod.validate(env)
    explicit = topology_provider is not None
    if not explicit:
        envelope_mod.require_live_api()
        topology_provider = _live_topology
        grounding_provider = grounding_provider or _live_grounding
        analysis_provider = analysis_provider or _live_analysis
        critic = critic or _live_critic
        fixer = fixer or fixer_mod.live_fixer
    if grounding_provider is None or analysis_provider is None:
        raise ValueError(
            "settle needs grounding and analysis providers when the "
            "topology provider is injected explicitly"
        )
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    policy = confidence_policy.POLICY_VERSION
    from . import prompts as prompts_mod

    # The Architect's run instructions ride the sealed envelope metadata;
    # empty slots append nothing, so payloads stay byte-identical.
    rules_suffix = prompts_mod.instruction_rules_suffix(env)

    topics = _topic_rows(env)
    blocks_by_topic = _blocks_by_topic(env)
    known_blocks = _known_block_ids(env)
    block_texts = _block_texts(env)

    normal_rows: list[dict[str, Any]] = []
    culmination_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(env["skeleton_rows"]):
        resolved = copy.deepcopy(dict(row))
        topic_id = resolve_topic_id(resolved, topics)
        if not topic_id and fixer is not None:
            # The Fixer seam F2 (Q13): a skeleton row that resolves to no
            # graph topic is a judgment call — which topic hosts it — not
            # a reason to halt. One recorded decision names the hosting
            # topic; the row proceeds flagged. R4: the row is never
            # dropped.
            topic_id = _fix_topic_resolution(
                resolved,
                topics,
                row_index=row_index,
                fixer=fixer,
                store=store,
                envelope_sha=envelope_sha,
            )
        if not topic_id:
            raise envelope_mod.EnvelopeError(
                "skeleton row resolves to no graph topic: "
                + _normal(resolved.get("concept_title"))[:80]
            )
        resolved["_semantic_topic_id"] = topic_id
        title = str(
            resolved.get("concept_title") or resolved.get("concept") or ""
        )
        if cr.is_culmination(title):
            culmination_rows.append(resolved)
        else:
            normal_rows.append(resolved)
    for index, row in enumerate(normal_rows, start=1):
        row["concept_id"] = f"TOPOLOGY-CONCEPT-{index:04d}"

    progress.log(
        f"Settle: deciding {len(normal_rows)} concept(s) across "
        f"{len(topics)} topic(s); {len(culmination_rows)} culmination "
        "recap(s) will be derived without model calls.",
    )
    settled: list[dict[str, Any]] = []
    flags_by_row: dict[int, list[str]] = {}

    def _settle_topic(
        topic: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[int, list[str]], list[dict[str, Any]]]:
        """Settle one topic's concepts; flags are keyed by topic offset."""
        local_flags: dict[int, list[str]] = {}
        topic_id = str(topic.get("topic_id") or "")
        topic_title = str(topic.get("title") or topic_id)
        topic_blocks = blocks_by_topic.get(topic_id, [])
        topic_block_ids = {row["block_id"] for row in topic_blocks}
        topic_concepts = [
            row
            for row in normal_rows
            if str(row.get("_semantic_topic_id") or "") == topic_id
        ]
        if not topic_concepts:
            return [], {}, []

        # -- stage 1: topology -------------------------------------------
        # Batches inside each stage fan out (payloads read only the batch
        # plus static topic context); APPLICATION stays in batch order, so
        # settled rows, flag indexes and culmination riders land
        # byte-identically to the sequential path. The shared OpenAI gate
        # bounds real concurrency under the nested topic x batch pools.
        topic_settled: list[dict[str, Any]] = []
        topology_batches = list(enumerate(_batched(topic_concepts), 1))

        def _decide_topology(pair):
            batch_index, batch = pair
            payload = {
                "stage": "topology",
                "rules": (
                    "Decide keep, refine, or split for every concept, under "
                    "the written placement rules. keep = the claim is "
                    "correct and singular (do not rewrite it). refine = the "
                    "claim needs correction against the source. split is "
                    "RARE: use it only when one row conflates ideas a "
                    "teacher would genuinely lesson-plan apart — never to "
                    "carve one coherent explanation into aspects, steps, "
                    "sub-cases, or definitions. These concepts were already "
                    "consolidated deliberately: reviewers rejected maps "
                    "full of thin micro-concepts, so a bundled-looking "
                    "title alone is not a reason to split. Every segment "
                    "you do emit must stand as a SUBSTANTIAL concept: a "
                    "full teaching paragraph of Description — enough for a "
                    "writer to author book sections, worksheets, notes, and "
                    "slides from it alone (never a sliver of the parent's "
                    "text) — and its own distinct "
                    "'Achieving Mastery:' line — segments must never share "
                    "or paraphrase one mastery sentence. "
                    "Pedagogy/activity banners and enrichment boxes — "
                    "'Activity', 'Project', 'do you know?', fact boxes, "
                    "discussion prompts and the like — are NEVER concepts "
                    "of their own: they cue an action or enrich a concept, "
                    "and their information reaches the learner through the "
                    "concept's Activity/Info Hub after placement, not as a "
                    "concept row. Never keep, refine, or split a row into "
                    "such a banner-concept. State your honest "
                    "confidence; a low-confidence decision ships flagged "
                    "for review." + rules_suffix
                ),
                "topic": {"topic_id": topic_id, "title": topic_title},
                "concepts": [
                    {
                        "concept_id": row["concept_id"],
                        "concept_title": row.get("concept_title"),
                        "parent_concept": row.get("parent_concept"),
                        "concept_details": row.get("concept_details"),
                        "keywords": row.get("keywords"),
                    }
                    for row in batch
                ],
                "source_blocks": topic_blocks,
            }
            return kernel.decide(
                kind="settle.topology",
                unit_id=f"{topic_id}#batch{batch_index}",
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=topology_provider,
                checker=_topology_checker(batch),
                critic=critic,
                store=store,
                policy_version=policy,
                fixer=fixer,
            )

        topology_decisions = kernel.parallel_map_in_order(
            topology_batches,
            _decide_topology,
            max_workers=config.phase3_decision_workers(),
            labels=[
                f"Settle {topic_title} · topology {index}/"
                f"{len(topology_batches)}"
                for index, _batch in topology_batches
            ],
        )
        for (batch_index, batch), decision in zip(
            topology_batches, topology_decisions
        ):
            response_by_id = {
                str(row.get("concept_id") or ""): row
                for row in decision["response"].get("decisions") or []
                if isinstance(row, Mapping)
            }
            for row in batch:
                verdict = response_by_id[row["concept_id"]]
                choice = str(verdict.get("decision") or "").strip().lower()
                for order, segment in enumerate(
                    verdict.get("segments") or [], start=1
                ):
                    settled_row = {
                        "topic": row.get("topic"),
                        "parent_concept": _normal(
                            segment.get("parent_concept")
                            or row.get("parent_concept")
                        ),
                        "concept_title": _normal(
                            segment.get("concept_title")
                        ),
                        "concept_details": _strip_analysis(
                            str(segment.get("concept_details") or "")
                        ),
                        "keywords": _normal(
                            segment.get("keywords") or row.get("keywords")
                        ),
                        "_semantic_topic_id": topic_id,
                        "_phase32_topology_decision": choice,
                        "_phase32_origin_concept_id": row["concept_id"],
                        "_phase32_segment_order": order,
                    }
                    index = len(topic_settled)
                    flags = [
                        # A Fixer topic-resolution decision (seam F2) is
                        # recorded on every row derived from the skeleton
                        # row it unblocked.
                        *(row.get("_fixer_review_flags") or []),
                        *_pin_flags(
                            list(decision.get("review_flags") or []),
                            [c["concept_id"] for c in batch],
                            row["concept_id"],
                        ),
                    ]
                    if flags:
                        local_flags[index] = flags
                    topic_settled.append(settled_row)

        # -- stage 2: grounding ------------------------------------------
        grounding_batches = _batched(list(range(len(topic_settled))))

        def _decide_grounding(offset_batch):
            batch_rows = [topic_settled[i] for i in offset_batch]
            concept_ids = [
                f"{row['_phase32_origin_concept_id']}"
                f"#{row['_phase32_segment_order']}"
                for row in batch_rows
            ]
            payload = {
                "stage": "grounding",
                "rules": (
                    "Ground every claim on the minimal exact source blocks "
                    "from the concept's own topic. Print position is "
                    "provenance, never grounding: a block from another "
                    "topic may only appear in reference_block_ids. ONE "
                    "exception: when the concept's own topic does not "
                    "teach the claim at all (for example a chapter-opening "
                    "concept whose material is actually taught inside a "
                    "later section), ground on the chapter blocks that DO "
                    "teach it — from other_topic_blocks — and explain that "
                    "in reason; such a decision ships flagged for review. "
                    "Never return an empty source_block_ids. State your "
                    "honest confidence; a low-confidence decision ships "
                    "flagged for review." + rules_suffix
                ),
                "topic": {"topic_id": topic_id, "title": topic_title},
                "concepts": [
                    {
                        "concept_id": concept_id,
                        "source_claim": _description_of(
                            row["concept_details"]
                        ),
                    }
                    for concept_id, row in zip(concept_ids, batch_rows)
                ],
                "source_blocks": topic_blocks,
                "other_topic_blocks": [
                    {
                        "block_id": row["block_id"],
                        "topic_id": other_topic_id,
                        "kind": row["kind"],
                        "text": row["text"][:400],
                    }
                    for other_topic_id, rows in blocks_by_topic.items()
                    if other_topic_id != topic_id
                    for row in rows
                ],
            }
            return kernel.decide(
                kind="settle.grounding",
                unit_id=f"{topic_id}#ground{offset_batch[0]}",
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=grounding_provider,
                checker=_grounding_checker(
                    concept_ids,
                    topic_block_ids=topic_block_ids,
                    known_block_ids=known_blocks,
                ),
                critic=critic,
                store=store,
                policy_version=policy,
                fixer=fixer,
            )

        grounding_decisions = kernel.parallel_map_in_order(
            grounding_batches,
            _decide_grounding,
            max_workers=config.phase3_decision_workers(),
            labels=[
                f"Settle {topic_title} · grounding {pos + 1}/"
                f"{len(grounding_batches)}"
                for pos in range(len(grounding_batches))
            ],
        )
        for offset_batch, decision in zip(
            grounding_batches, grounding_decisions
        ):
            batch_rows = [topic_settled[i] for i in offset_batch]
            concept_ids = [
                f"{row['_phase32_origin_concept_id']}"
                f"#{row['_phase32_segment_order']}"
                for row in batch_rows
            ]
            grounded_by_id = {
                str(row.get("concept_id") or ""): row
                for row in decision["response"].get("concepts") or []
                if isinstance(row, Mapping)
            }
            subtopic_by_block = {
                row["block_id"]: row["subtopic_id"] for row in topic_blocks
            }
            for position, (concept_id, row) in zip(
                offset_batch, zip(concept_ids, batch_rows)
            ):
                grounded = grounded_by_id[concept_id]
                block_ids = [
                    str(v) for v in grounded.get("source_block_ids") or []
                ]
                row["_source_block_ids"] = block_ids
                references = [
                    str(v)
                    for v in grounded.get("reference_block_ids") or []
                    if str(v)
                ]
                if references:
                    row["_reference_block_ids"] = references
                row["_semantic_subtopic_ids"] = sorted({
                    subtopic_by_block.get(block_id, "")
                    for block_id in block_ids
                } - {""})
                row["_source_grounding_contract"] = (
                    "api-verified-source-block-ids"
                )
                try:
                    row["_source_grounding_confidence"] = float(
                        grounded.get("confidence") or 0.0
                    )
                except (TypeError, ValueError):
                    row["_source_grounding_confidence"] = 0.0
                flags = _pin_flags(
                    list(decision.get("review_flags") or []),
                    concept_ids,
                    concept_id,
                )
                if flags:
                    local_flags.setdefault(position, []).extend(flags)

        # -- stage 3: content authoring (single pass) --------------------
        topic_culms = [
            row for row in culmination_rows
            if str(row.get("_semantic_topic_id") or "") == topic_id
        ]
        selected_culms = _culminations_to_author(
            topic_culms,
            normal_concept_count=len(topic_settled),
        )
        if len(selected_culms) != len(topic_culms):
            dropped = len(topic_culms) - len(selected_culms)
            reason = (
                "the topic teaches one ordinary concept and the recap was "
                "not explicitly owned by the sealed literary plan"
                if len(topic_settled) < 2 and not selected_culms
                else "the exact-one culmination contract retained the "
                "authoritative/source-first row"
            )
            progress.log(
                f"Settle: dropped {dropped} redundant culmination row(s) "
                f"from {topic_title!r}: {reason}.",
                level="warning",
            )
        topic_culms = selected_culms
        culm_consolidations: dict[str, str] = {}
        authoring_batches = _batched(list(range(len(topic_settled))))

        def _decide_authoring(offset_batch):
            batch_rows = [topic_settled[i] for i in offset_batch]
            concept_ids = [
                f"{row['_phase32_origin_concept_id']}"
                f"#{row['_phase32_segment_order']}"
                for row in batch_rows
            ]
            # Culminations ride the topic's first authoring decision: their
            # consolidation prose needs the same grounded context, and one
            # decision keeps the multi-user API budget flat.
            batch_culm_ids = (
                [f"CULM#{i}" for i in range(len(topic_culms))]
                if offset_batch[0] == 0 and topic_culms
                else []
            )
            payload = {
                "stage": "content_authoring",
                "rules": (
                    "Author each concept's learner-facing content in ONE "
                    "pass, grounded only on its source_blocks. "
                    "concept_description: the full teaching paragraph in "
                    "original language — this text is the basis for books, "
                    "worksheets, notes, slides, and interactive content, so "
                    "it must TEACH, not summarize: define the idea "
                    "precisely, state the key rule, property, or method and "
                    "what each term means, give the conditions and when/why "
                    "it applies, show the reasoning that makes it work, and "
                    "make it concrete with the source's own facts, figures, "
                    "or a compact worked cue. achieving_mastery: ONE "
                    "sentence naming what a learner can DO once this "
                    "concept is mastered — distinct for every concept, "
                    "never shared or paraphrased between concepts. Do NOT "
                    "author Misconceptions or Error Analysis in any field "
                    "— the chapter-level inventory pass owns them (Q1) "
                    "and they are allotted to concepts later. In "
                    "every field, wrap EVERY mathematical expression "
                    "exactly as [Katex] valid LaTeX [/Katex]; never emit "
                    "raw TeX, $ delimiters, bare sub/superscripts, or bare "
                    "equations outside those tags. When the request carries "
                    "culminations, also author each one's consolidation: a "
                    "short teaching paragraph (2-4 sentences, original "
                    "language) tying the topic's member concepts together — "
                    "what the learner can now do with them combined — "
                    "never a list of concept names and never a repeat of "
                    "any single concept's description." + rules_suffix
                ),
                "topic": {"topic_id": topic_id, "title": topic_title},
                "concepts": [
                    {
                        "concept_id": concept_id,
                        "concept_title": row["concept_title"],
                        "draft_concept_details": row["concept_details"],
                        "source_blocks": [
                            {
                                "block_id": block_id,
                                "text": block_texts.get(block_id, ""),
                            }
                            for block_id in (
                                row.get("_source_block_ids") or []
                            )
                        ],
                    }
                    for concept_id, row in zip(concept_ids, batch_rows)
                ],
                **(
                    {
                        "culminations": [
                            {
                                "concept_id": culm_id,
                                "culmination_title": _normal(
                                    culm.get("concept_title")
                                ),
                                "member_concepts": [
                                    r["concept_title"] for r in topic_settled
                                ],
                            }
                            for culm_id, culm in zip(
                                batch_culm_ids, topic_culms
                            )
                        ]
                    }
                    if batch_culm_ids
                    else {}
                ),
            }
            return kernel.decide(
                kind="settle.author",
                unit_id=f"{topic_id}#author{offset_batch[0]}",
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=analysis_provider,
                checker=_authoring_checker(concept_ids, batch_culm_ids),
                critic=critic,
                store=store,
                # Q1 re-key: the authoring schema lost its analysis field,
                # so stored pre-Q1 decisions must never replay here.
                policy_version=policy + AUTHOR_POLICY_SUFFIX,
                fixer=fixer,
            )

        authoring_decisions = kernel.parallel_map_in_order(
            authoring_batches,
            _decide_authoring,
            max_workers=config.phase3_decision_workers(),
            labels=[
                f"Settle {topic_title} · authoring {pos + 1}/"
                f"{len(authoring_batches)}"
                for pos in range(len(authoring_batches))
            ],
        )
        for offset_batch, decision in zip(
            authoring_batches, authoring_decisions
        ):
            batch_rows = [topic_settled[i] for i in offset_batch]
            concept_ids = [
                f"{row['_phase32_origin_concept_id']}"
                f"#{row['_phase32_segment_order']}"
                for row in batch_rows
            ]
            for position, concept_id in zip(offset_batch, concept_ids):
                flags = _pin_flags(
                    list(decision.get("review_flags") or []),
                    concept_ids,
                    concept_id,
                )
                if flags:
                    local_flags.setdefault(position, []).extend(flags)
            authored_by_id = {
                str(row.get("concept_id") or ""): row
                for row in decision["response"].get("rows") or []
                if isinstance(row, Mapping)
            }
            for row in decision["response"].get("culminations") or []:
                if isinstance(row, Mapping):
                    culm_consolidations[str(row.get("concept_id") or "")] = (
                        _normal(row.get("consolidation"))
                    )
            for concept_id, row in zip(concept_ids, batch_rows):
                authored = authored_by_id[concept_id]
                description = _FIELD_LABEL.sub(
                    "", _normal(authored.get("concept_description"))
                )
                mastery = _FIELD_LABEL.sub(
                    "", _normal(authored.get("achieving_mastery"))
                )
                # Q1: Settle mints Description + Achieving Mastery only.
                # The Misconception/ Error Analysis section is stamped by
                # Assemble from the chapter inventory's allotments.
                row["concept_details"] = kr.repair_unwrapped_math(
                    "Description: " + description
                    + "\nAchieving Mastery: " + mastery
                )

        topic_flag_count = sum(1 for flags in local_flags.values() if flags)
        progress.log(
            f"Settle: {topic_title!r} settled — {len(topic_settled)} "
            "concept(s) decided, grounded, and authored"
            + (
                f"; {topic_flag_count} carrying review flags."
                if topic_flag_count
                else "."
            ),
            level="success",
        )

        # -- culminations: structure derived, prose authored above -------
        # The Description is the model-authored consolidation paragraph —
        # never a code-composed "Recap of ..." title list. The authoring
        # checker makes the consolidation mandatory, so an empty one here
        # is an unexpected defect: the row still ships (never dropped),
        # flagged for review.
        culm_rows: list[dict[str, Any]] = []
        for culm_index, row in enumerate(topic_culms):
            derived_blocks: list[str] = []
            for source in topic_settled:
                for block_id in source.get("_source_block_ids") or []:
                    if block_id not in derived_blocks:
                        derived_blocks.append(block_id)
            prose = culm_consolidations.get(f"CULM#{culm_index}", "")
            culm_row = {
                "topic": row.get("topic"),
                "parent_concept": _normal(row.get("parent_concept")),
                "concept_title": _normal(row.get("concept_title")),
                "concept_details": "Description: " + prose,
                "keywords": _normal(row.get("keywords")),
                "_semantic_topic_id": topic_id,
                "_source_block_ids": derived_blocks,
                "_source_grounding_contract": (
                    "derived-from-verified-topic-concepts"
                ),
            }
            culm_flags = list(row.get("_fixer_review_flags") or [])
            if not prose:
                culm_flags.append(
                    "culmination shipped without an authored consolidation "
                    "paragraph; the authoring pass returned none and no "
                    "text was code-composed — needs review"
                )
            if culm_flags:
                culm_row["review_flags"] = culm_flags
            culm_rows.append(culm_row)
        return topic_settled, local_flags, culm_rows

    # Topics are independent decision streams (each one's topology ->
    # grounding -> analysis chain stays sequential inside its worker), so
    # they overlap up to the shared OpenAI concurrency gate. Merging in
    # topic order keeps output byte-identical to the sequential path.
    # Default is sequential: per-run parallelism is a deployment choice
    # sized against concurrent creator runs (see phase3_decision_workers).
    workers = config.phase3_decision_workers()
    topics_done = 0
    for topic_settled, local_flags, culm_rows in kernel.parallel_map_in_order(
        topics, _settle_topic, max_workers=workers,
    ):
        base = len(settled)
        for offset, flags in local_flags.items():
            if flags:
                flags_by_row.setdefault(base + offset, []).extend(flags)
        settled.extend(topic_settled)
        settled.extend(culm_rows)
        topics_done += 1
        # The bar creeps through the Settle band (0.815 → 0.86, the slice
        # runner.py allocates) instead of freezing at its opening value
        # for the whole pass.
        progress.set_progress(
            0.815 + 0.045 * topics_done / max(1, len(topics)),
            label=(
                "Phase 3 — Settle: topic "
                f"{topics_done}/{len(topics)} authored"
            ),
        )

    for index, flags in flags_by_row.items():
        if 0 <= index < len(settled) and flags:
            settled[index]["review_flags"] = [
                *(settled[index].get("review_flags") or []),
                *flags,
            ]

    # Certificate lineage is sealed exactly once, over the FINAL payload in
    # Assemble (attempt 8: a Settle-time seal fights the re-seal after Host
    # adds new concepts — 'ordered grounded concept set changed').
    return settled
