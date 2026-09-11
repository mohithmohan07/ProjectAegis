"""Pass 2.9c — Prequestions: the Pre-Learning coverage plan and its questions.

Latest owner amendment (9 September 2026): fresh envelopes carry an explicit
adaptive coverage policy. The API chooses each prerequisite's sufficient
total and tier split with no fixed or per-tier quota and no scope expansion.
The accepted split is carried on authored questions. Historical sealed Q30
fixed-rule runs and no-rule runs retain their respective behavior below.

docs/aegis-restructure.md §4 Phase 03 (Q4, resolved per D3; the recorded
numeric calibration targets of Q20 — re-set by owner steer 2026-08-20 and
2026-08-21, with the tier split left to the model — are superseded by
register Q26, 2026-09-04: the Master Governing Contract v2.0 §8 states
"complete diagnostic coverage of the Mastery, at least one routed
question, no quotas", and the diagnostic posture of 2026-08-21 stands:
plan the minimum coverage that genuinely verifies the prerequisite):
neither mandatory quota nor maximum; the model authors a
concept-specific coverage plan; every plan carries an authored,
critic-flagged rationale; an explicit blueprint may override; a thin
pre-concept is never padded.

This pass authors the plan and the questions and carries them. Routing
them through the assessment lane — cells, materialization, grouping,
marking, Output 04 — is the next slice's work and is deliberately absent
here: nothing in this module touches ``assessment_release_run``,
``assessment_cells``, or the release path.

**Q4 without a quota, and without the literal in a validator (spec T6).**
The obvious implementation of "variance carries a rationale" is
``if planned != 40: require_rationale()``. That is a numeric threshold
deciding meaning AND the literal 40 in a validator — forbidden twice
over by CLAUDE.md Rule 1. The decided resolution is to require the
rationale **unconditionally**: every plan states its total, its split and
why, whatever the numbers are. There is then nothing for any checker to
compare a plan against, so no code here compares one to anything. A plan
of exactly forty carries a rationale for the same reason a plan of six
does — because every plan does.

The coverage rule therefore appears in exactly one place: as
explicitly-labelled COVERAGE prose inside ``_plan_rules`` (contract §8:
complete diagnostic coverage of the Mastery, no quotas). No number of
any kind is stated there since Q26, and no norm appears in any constant,
checker, threshold, default or comparison — pinned by a regression over
the whole validator surface. And because a stated figure becoming a de
facto quota is the real hazard, the advisory critic is asked in as many
words whether the plan is **evidence-led or anchored on a customary
number**: a thin pre-concept planning one question with a good reason
is a correct outcome, not a defect.

**The checker's only arithmetic is between two numbers the model itself
wrote.** A plan's split sums to the plan's own total; a concept's
authored questions number what that concept's own plan asked for. No
external number enters either side. A shortfall the bounded corrections
cannot close goes to The Fixer (Q13), and if even the Fixer cannot close
it the affected pre-concepts are recorded as blocked, flagged, and the
run completes — it never takes a chapter down. That is the "flags or
routes to the Fixer, never raises" rule, and BOTH halves implement it:
the authoring decision blocks the one concept it is for, and the
chapter-wide plan decision — the harsher of the two, since one
unreconcilable split would otherwise end a run holding a finished Post
map and a finished Pre map — blocks every pre-concept by name and
returns. What did not arrive to contract is never shipped alongside the
flag: ``kernel.ContractError`` carries the defects, not the failing
candidate, and rows that failed the mechanical contract are the broken
artefact a gate exists to refuse. (Blocking is also the one place a
replay re-spends: a blocked decision durably decided nothing, so there
is nothing to replay. Every decision that DID land replays at zero
spend.)

**NO EXTRACTION OF ANY QUESTIONS, ANYWHERE (owner steer, 17 Aug 2026).**
Every question here is GENERATED. Two guards, deliberately different
acts, and the same two ``premap`` already draws:

* mechanically, ``premap._refuse_source_qids`` — the SAME guard, not a
  second one — runs over the plan and authoring payloads before any
  spend, and over the authored questions, their answers and their
  rationales afterwards. It fails closed. The ordering is load-bearing
  and is ``premap``'s recorded rule: the guard runs over AUTHORED
  content BEFORE any critic prose or review flag is stamped. A critic
  asked "is this question really generated?" may answer most usefully by
  naming the source question it resembles; scanning after stamping would
  turn that advisory dissent into the hardest gate in the pipeline,
  replaying forever at zero spend from a content-addressed store, which
  is exactly what Q10 forbids;
* semantically, "is this generated question merely a paraphrase of a
  source question?" is a MODEL judgment and a NAMED critic dimension
  whose dissent flags. Never a string-similarity test — a similarity
  threshold over chapter text is precisely the shape-matching Rule 1
  forbids.

**Why the critic is not shown the chapter's questions**, recorded as a
decision rather than left as an omission. The kernel hands the critic the
author's own payload plus the proposed decision, so putting the source
questions in front of the critic would put source-question WORDING into
a Pre payload, and from there into critic prose stamped on Pre rows —
importing the leak the steer exists to prevent, through the auditor's
door. The paraphrase dimension is therefore asked about what the
question is ABOUT: a question that interrogates this chapter's own
content or reads as one of its tasks reworded is not a prerequisite
question, and the critic can judge that from the prerequisite evidence it
does hold. Stated plainly so it is not mistaken for a stronger guarantee
than it is.

**The tier/difficulty axis is not conflated.** Q4's original "20 Basic
and 20 Intermediate" — and every later calibration of it — names the
GROUP TIER (``assessment_grouping.TIER_CODES``). The workbook's
``level_of_difficulty`` (``bulk_import.DIFFICULTY_LEVELS`` —
Less/Moderate/High) is a different vocabulary entirely, and no authored
question ever carries a difficulty. The tier vocabulary is taken from its
one owner, never re-typed here.

**Under the owner's coverage rule (register Q30, ``pre_coverage``), the
tier IS authored.** An envelope that records the rule fixes every
pre-concept's plan at the rule's split, and each question is written AT
its tier and carries a ``tier`` field; the assessment lane groups it by
that authored tier and does not re-decide it (a second verdict could
only break the five-and-five the owner fixed). An envelope that records
no rule keeps the earlier posture in full: the plan's split is a
statement of intended coverage, an authored question carries no tier,
and each question's tier is the assessment lane's to decide, later,
independently. Which posture a run executed under is a recorded fact of
its envelope, logged when the pass starts.

**Calibration is evidence, never a branch** (steer point 2). Each
question is authored for the level, grade, context, subject and board of
the chapter in hand. The mechanism already exists and is reused, not
reinvented: the chapter's own board/grade/subject/unit ride the payload,
and The Architect's assembled instruction set (§8.1) rides it through
``prompts.instruction_rules_suffix`` — the same mechanism ``premap`` and
``preanalyse`` use, and its hash already joins every decision key. This
pass reads one slot more than the rest of the Pre lane
(``PRE_QUESTION_SLOTS``): the steer names *context* alongside level,
grade, subject and board, and ``chapter_cautions`` is the Architect's
chapter-context slot. No line of this module branches on a grade, a
subject or a board value; a per-grade or per-subject branch deciding
question shape would be Rule 1's forbidden judgment wearing a curriculum
hat.
"""
from __future__ import annotations

from .. import prelearning_capture_policy as capture_policy
from .. import generation_quality_policy as quality

import copy
import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from . import pre_coverage
from ... import config
from .. import progress

POLICY_VERSION = "prequestions-1"
ADAPTIVE_POLICY_VERSION = "prequestions-2-adaptive-coverage"

_FOUNDATION_QUESTION_GUIDANCE = """
For a Grade 1 foundation, keep each generated check at simple readiness and
within the child's demonstrated level. Prefer one short step, recognition or
an explicit correct choice, or a tiny familiar response. Do not ask for an
essay, a why justification, technical grammar terminology, or multi-step
reasoning. Do not inflate a plan with Advanced questions; an Intermediate
question is allowed only when the retained, source-supported prior capability
is still within the child's level. Choose a modest context-based set and use
only the tiers the evidence needs; never force a trio, balance tiers, or add a
question to fill a customary count. The question must not introduce chapter
teaching or a future-grade extension.
""".strip()


def _foundation_instruction(payload: Mapping[str, Any]) -> str:
    """Return only question-specific guidance after shared policy text."""

    from .. import prelearning_foundation_policy

    instruction = prelearning_foundation_policy.instruction(payload)
    if not instruction:
        return ""
    return "\n" + _FOUNDATION_QUESTION_GUIDANCE


def _foundation_policy_suffix(payload: Mapping[str, Any]) -> str:
    """Identify the foundation policy in the durable decision audit."""

    from .. import prelearning_foundation_policy

    return (
        ";" + prelearning_foundation_policy.VERSION
        if payload.get(prelearning_foundation_policy.KEY)
        == prelearning_foundation_policy.VERSION
        else ""
    ) + quality.suffix(payload)


# The generated questions ride their OWN carry channel (the runner's
# ``pre_questions`` key and its snapshot). They are deliberately not
# stamped into the Pre concept detailing here: putting a question under a
# Type or a Case is a Phase 04 classification act, and this slice's brief
# is to produce the questions and carry them.


class PreQuestionError(RuntimeError):
    """A generated pre-learning question id collided with another.

    Mint mechanics only — the durable id is built from two ids this
    pipeline assigned positionally, so a collision is a defect in the
    mint rather than a judgment about content. The no-extraction refusal
    is NOT this exception: that is ``premap.PreExtractionError``, raised
    by ``premap._refuse_source_qids``, which is reused here rather than
    re-implemented.
    """


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def tier_names() -> tuple[str, ...]:
    """The group-tier vocabulary, read from its one owner.

    ``assessment_grouping.TIER_CODES`` is where Basic/Intermediate/
    Advanced is defined, and it is read here rather than re-typed so the
    two can never drift. Naming a tier in a coverage PLAN is a statement
    about intended coverage; it is not a verdict on any question, and it
    has no authority over the assessment lane's independent per-question
    level verdict. It is also NOT ``bulk_import.DIFFICULTY_LEVELS``
    (Less/Moderate/High), which is a different vocabulary for a different
    axis — a regression pins the two apart.
    """
    from .. import assessment_grouping

    return tuple(assessment_grouping.TIER_CODES)


def mint_question_ids(count: int) -> list[str]:
    """Positional per-concept question ids: PRQ-0001, PRQ-0002, …

    Scoped to one pre-concept's authoring response. The durable identity
    a later slice routes on is ``pre_question_id`` —
    ``"<pre_concept_id>-<PRQ-nnnn>"`` — minted mechanically below and
    asserted unique across the whole map.
    """
    return [f"PRQ-{index:04d}" for index in range(1, count + 1)]


def pre_question_id(pre_concept_id: str, question_id: str) -> str:
    """The map-wide durable id for one generated question."""
    return f"{pre_concept_id}-{question_id}"


# ---------------------------------------------------------------------------
# evidence


def concept_evidence(
    row: Mapping[str, Any], *, complete_scope: bool = False,
) -> dict[str, Any]:
    """One pre-concept's own teaching, as evidence for its questions.

    Everything here was AUTHORED by the Pre lane and has already passed
    ``premap``'s fail-closed guard, or is upstream provenance ``premap``
    already redacted. The chapter's source blocks and its question/task
    inventory are deliberately absent: a prerequisite question is about
    the prerequisite, and a lane that is never shown a source question
    cannot echo one.
    """
    from . import premap as premap_mod

    return {
        "pre_concept_id": str(row.get("_pre_concept_id") or ""),
        "concept_title": _normal(row.get("concept_title")),
        "description": premap_mod._description_of(row.get("concept_details")),
        "concept_details": _normal(row.get("concept_details")),
        "prerequisites": [
            {
                "prerequisite_id": str(item.get("prerequisite_id") or ""),
                "text": _normal(item.get("text")),
                **({"retained_atoms": copy.deepcopy(
                    item.get("retained_atoms") or []
                )} if complete_scope else {}),
            }
            for item in row.get(premap_mod.PREREQUISITES_FIELD) or []
            if isinstance(item, Mapping)
        ],
        "needed_for": [
            _normal(item.get("post_concept_title"))
            for item in row.get(premap_mod.NEEDED_FOR_FIELD) or []
            if isinstance(item, Mapping)
        ],
    }


# ---------------------------------------------------------------------------
# mechanics-only checkers


def _rule_plan_defects(
    concept_id: str,
    total: int,
    counts_by_tier: Mapping[str, int],
    rule: Mapping[str, Any],
) -> list[str]:
    """The owner's rule (register Q30) against one plan — mechanics.

    Exactly the rule's split, or a total of zero with no split (the
    recorded request to drop the concept, Q29). Comparison of recorded
    numbers; no judgment about the prerequisite.
    """

    if pre_coverage.is_adaptive(rule):
        # Count/split arithmetic is already checked against the author's own
        # numbers. This policy imposes no additional numeric target.
        return []
    if total == 0 and not counts_by_tier:
        return []
    if total == pre_coverage.total(rule) and dict(counts_by_tier) == dict(
        rule["per_tier"]
    ):
        return []
    stated = ", ".join(
        f"{count} {tier}" for tier, count in counts_by_tier.items()
    ) or "no split"
    return [
        f"{concept_id} plans {total} ({stated}) but the owner's coverage "
        f"rule {rule['version']} fixes every pre-learning concept at "
        f"{pre_coverage.describe(rule)}; state exactly that, or a total "
        "of zero with no split as a recorded request to drop the concept"
    ]


def _plan_checker(
    pre_concept_ids: list[str],
    rule: Mapping[str, Any] | None = None,
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: every concept planned once, the plan self-consistent.

    Without a coverage rule, what total any pre-concept's evidence
    supports, and how it splits, is entirely the model's judgment —
    nothing here bounds, floors, ceilings or compares a count to any
    external number, and there is no external number in this function to
    compare one to. The three things checked are marker accounting and
    arithmetic between numbers the model itself wrote:

    * every pre-concept in the request is decided exactly once (R4 — a
      pre-concept nobody planned is a learner's questions lost silently);
    * the split's tiers come from the group-tier vocabulary, no tier
      twice, and the split's counts sum to the plan's OWN total;
    * **the rationale is required unconditionally** (spec T6). Every
      plan says why it is the size it is. That is what makes a
      comparison against a norm unnecessary anywhere in this codebase.

    With the owner's coverage rule recorded on the envelope (register
    Q30, ``pre_coverage``) one comparison is added, against the ONE
    external number the owner fixed: every plan states exactly the rule's
    split, or zero as a recorded drop request (``_rule_plan_defects``).
    """

    expected = set(pre_concept_ids)
    tiers = tier_names()

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("plans")
        if not isinstance(rows, list):
            return ["response has no plans array"]
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                defects.append("a plan entry is not an object")
                continue
            concept_id = str(row.get("pre_concept_id") or "")
            if concept_id not in expected or concept_id in seen:
                defects.append(
                    "unknown or repeated pre_concept_id "
                    f"{concept_id or '<empty>'}"
                )
                continue
            seen.add(concept_id)
            total = row.get("total")
            if not isinstance(total, int) or isinstance(total, bool):
                defects.append(
                    f"{concept_id} total {total!r} is not a whole number"
                )
                total = None
            elif total < 0:
                defects.append(f"{concept_id} total {total} is negative")
                total = None
            split = row.get("split")
            if not isinstance(split, list):
                defects.append(
                    f"{concept_id} has no split array; state the split you "
                    "intend across the tiers, even when it is all in one"
                )
                split = []
            planned = 0
            named: set[str] = set()
            counts_by_tier: dict[str, int] = {}
            for entry in split:
                if not isinstance(entry, Mapping):
                    defects.append(f"{concept_id} has a split entry that is "
                                   "not an object")
                    continue
                tier = str(entry.get("tier") or "").strip()
                if tier not in tiers:
                    defects.append(
                        f"{concept_id} split names tier {tier!r}, which is "
                        "not one of " + "/".join(tiers)
                    )
                elif tier in named:
                    defects.append(
                        f"{concept_id} split names tier {tier!r} twice"
                    )
                else:
                    named.add(tier)
                count = entry.get("count")
                if not isinstance(count, int) or isinstance(count, bool):
                    defects.append(
                        f"{concept_id} split count {count!r} is not a whole "
                        "number"
                    )
                elif count < 0:
                    defects.append(
                        f"{concept_id} split count {count} is negative"
                    )
                else:
                    planned += count
                    if tier in named:
                        counts_by_tier[tier] = count
            if total is not None and planned != total:
                # Arithmetic between the model's OWN two numbers.
                defects.append(
                    f"{concept_id} split sums to {planned} but its own total "
                    f"says {total}; the split accounts for the total you "
                    "authored"
                )
            if rule is not None and total is not None:
                defects.extend(
                    _rule_plan_defects(concept_id, total, counts_by_tier, rule)
                )
            if not _normal(row.get("rationale")):
                # Unconditional, by design (Q4 per spec T6): EVERY plan
                # states why it is the size it is, so no code anywhere
                # ever compares a plan to a norm to decide whether a
                # rationale is owed.
                defects.append(
                    f"{concept_id} has an empty rationale; every plan states "
                    "why it is the size and shape it is, whatever its "
                    "numbers"
                )
        missing = sorted(expected - seen)
        if missing:
            defects.append("unplanned pre-concept(s): " + ", ".join(missing))
        return defects

    return check


def _author_checker(
    concept_id: str,
    planned_total: int,
    *,
    rule: Mapping[str, Any] | None = None,
    split: Mapping[str, int] | None = None,
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: positional ids, non-empty fields, plan↔output.

    ``planned_total`` is the model's OWN number, carried over from the
    plan decision it authored — this is the questions delivered matching
    the plan the model itself wrote, with no external number on either
    side of the comparison. A shortfall the bounded corrections cannot
    close routes to The Fixer, and if that fails too the caller records
    the concept as blocked and the run completes; it never raises a
    chapter down.

    Under the owner's coverage rule (register Q30) each question also
    carries the tier the plan's ``split`` assigns it, and the per-tier
    counts match that split — the plan checker already held the split to
    the rule, so this is again the model's own plan against its own
    questions, tier by tier.
    """

    expected_by_tier = dict(split or {}) if rule is not None else {}

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("questions")
        if not isinstance(rows, list):
            return ["response has no questions array"]
        expected = mint_question_ids(len(rows))
        seen_by_tier: dict[str, int] = {}
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                defects.append("a question entry is not an object")
                continue
            label = expected[position]
            if expected_by_tier:
                tier = str(row.get("tier") or "").strip()
                if tier not in expected_by_tier:
                    defects.append(
                        f"question at position {position + 1} carries tier "
                        f"{tier or '<empty>'}; each question carries exactly "
                        "one tier from the coverage plan's split ("
                        + "/".join(expected_by_tier) + ")"
                    )
                else:
                    seen_by_tier[tier] = seen_by_tier.get(tier, 0) + 1
            # The defect text names the POSITION and the id this pipeline
            # expects there — never the id the model sent. A defect string
            # becomes ``blocked[concept_id]`` and a review flag, and both
            # ride out on the questions snapshot; echoing model-authored
            # text there would open a channel for a source QID to reach an
            # artefact through the error path (see the guard note in
            # ``build``). Naming the position and the expected mint is
            # strictly more actionable anyway — the model can see what it
            # sent.
            if str(row.get("question_id") or "") != label:
                defects.append(
                    f"question at position {position + 1} does not carry "
                    f"its positional id; ids are the mint PRQ-0001, "
                    f"PRQ-0002, … in listing order (expected {label} here)"
                )
            for field in ("question_text", "answer", "rationale"):
                if not _normal(row.get(field)):
                    defects.append(f"{label} has an empty {field}")
        if len(rows) != planned_total:
            defects.append(
                f"{concept_id} was authored {len(rows)} question(s) but its "
                f"own coverage plan asked for {planned_total}; author "
                "exactly that many — the plan is already decided and "
                "cannot be amended from here"
            )
        for tier, count in expected_by_tier.items():
            if seen_by_tier.get(tier, 0) != count:
                defects.append(
                    f"{concept_id} was authored {seen_by_tier.get(tier, 0)} "
                    f"{tier} question(s) but its coverage plan asks for "
                    f"{count} at that tier; author exactly that many at "
                    "each tier — the split is already decided and cannot "
                    "be amended from here"
                )
        return defects

    return check


# ---------------------------------------------------------------------------
# live adapters


def _plan_system(payload: Mapping[str, Any]) -> str:
    from . import prompts

    if not pre_coverage.is_adaptive(payload.get("coverage_rule")):
        return prompts.PREQUESTIONS_PLAN_SYSTEM
    return prompts._SHARED + (
        " Task: author the adaptive diagnostic coverage plan for every supplied "
        "Pre-Learning concept. Response schema: {\"plans\":[{\"pre_concept_id\","
        "\"total\",\"split\":[{\"tier\":\"Basic|Intermediate|Advanced\",\"count\"}],"
        "\"rationale\"}]}. "
        "The explicit adaptive coverage_rule leaves total and tier split to "
        "your judgment of this prerequisite's retained mastery and prior-grade "
        "scope. There is no fixed total or per-tier quota. Choose only questions "
        "that earn their place through distinct diagnostic coverage; never pad, "
        "fill every tier, balance tiers, or extend teaching to justify more "
        "questions. Account for every supplied pre_concept_id exactly once. "
        "All counts are nonnegative integers; the split sums to its own total. "
        "Every plan explains why its chosen total and split are sufficient "
        "for the actual prerequisite. A zero plan is a recorded request to drop "
        "an unassessable concept, not proof that a retained concept is assessed. "
        "Questions are authored at the tiers you plan; tier is not difficulty. "
        "Use supplied prerequisite evidence only; no current-chapter task is "
        "copied, reworded or used to expand the prerequisite."
    )


def _author_system(payload: Mapping[str, Any]) -> str:
    from . import prompts

    if not pre_coverage.is_adaptive(payload.get("coverage_rule")):
        return prompts.PREQUESTIONS_AUTHOR_SYSTEM
    return prompts._SHARED + (
        " Task: author one Pre-Learning concept's fresh diagnostic questions "
        "under its accepted adaptive coverage plan. Response schema: "
        "{\"questions\":[{\"question_id\":\"PRQ-0001\",\"question_text\":\"\","
        "\"answer\":\"\",\"rationale\":\"\",\"tier\":\"Basic|Intermediate|Advanced\"}]}. "
        "The plan's total and tier split were chosen from this prerequisite's "
        "context, not a fixed quota; author exactly that recorded plan without "
        "padding, repetitions, tier balancing or scope extension. Cite the "
        "distinct prior capability each question verifies in rationale. Each "
        "question carries its planned tier and no difficulty label; use "
        "PRQ-0001, PRQ-0002, … in listing order. Every question is new, for "
        "the learner's earlier-grade prerequisite only; never copy, paraphrase "
        "or test a current-chapter source question or add current-chapter "
        "teaching. Respect supplied grade, subject, board and context. Solve "
        "each task before returning. question_text contains the complete "
        "learner task with its required data, options and parts, without "
        "answers or evaluator commentary. answer gives the complete expected "
        "response and reasoning. Do not award unasked demands. Wrap mathematics "
        "as [Katex] valid LaTeX [/Katex]."
    )


def _critic_system(payload: Mapping[str, Any]) -> str:
    from . import prompts
    from .. import column_spec

    if not pre_coverage.is_adaptive(payload.get("coverage_rule")):
        return prompts.PREQUESTIONS_CRITIC_SYSTEM
    return prompts._SHARED + column_spec.REVIEW_QUALITY + (
        " Task: independently audit the adaptive Pre question plan or its "
        "authored questions. For a PLAN, judge sufficiency, proportion and "
        "rationale against each prerequisite's retained mastery, needed-for "
        "context and prior-grade boundary. Its total and tier split are "
        "API-authored choices, never a fixed owner quota. Flag unsupported "
        "numeric anchoring, automatic equal totals, tier balancing, padding, "
        "redundancy, missing diagnostic coverage and expanding prerequisite "
        "scope to justify more questions. A small sufficient plan is correct; "
        "do not demand a customary count or a question in every tier. For "
        "QUESTIONS, verify the complete ask and answer, planned coverage, "
        "tier fit, grade/context calibration, genuine diagnostic variety, "
        "self-contained wording and absence of answer leakage. Flag current-"
        "chapter content or tasks reworded as prior learning. The source "
        "questions are deliberately absent: assess what the question is "
        "about against the supplied prerequisite evidence; never invent "
        "source evidence. Response schema: {\"verdict\":\"verified|rejected\","
        "\"confidence\":0.0,\"issues\":[]}. Dissent is recorded and advisory; "
        "never rewrite, retry, gate or enlarge the concept or question set."
    )


def _live_plan(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        _plan_system(payload)
        + capture_policy.boundary_instruction(payload)
        + _foundation_instruction(payload),
        prompts.render(payload),
        purpose="pre_learning",
    )


def _live_author(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation, model_provider
    from ..response_schemas import pre_question_author_schema

    return generation._openai_json(
        _author_system(payload)
        + capture_policy.boundary_instruction(payload)
        + _foundation_instruction(payload),
        prompts.render(payload),
        purpose="pre_learning", stage="prequestions.author",
        **({"response_schema": pre_question_author_schema()} if model_provider.bound_profile() is not None else {}),
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        _critic_system(payload)
        + capture_policy.boundary_instruction(payload)
        + _foundation_instruction(payload),
        prompts.render(payload),
        purpose="advisory_critic",
    )


# The ONE place the coverage rule appears, and it appears as what it is:
# labelled COVERAGE prose inside the plan payload's rules. Until register
# Q26 (2026-09-04) this prose quoted a recorded numeric target ("about 5
# questions per concept", Q20) as calibration evidence; the Master
# Governing Contract v2.0 §8 replaces every such target with "complete
# diagnostic coverage of the Mastery, at least one routed question, no
# quotas", so no number of any kind is stated here any more. Q4's "every
# plan carries a rationale" stands unchanged: the rationale is required
# unconditionally, so no code ever compares a plan to anything, and a
# regression pins that over the whole validator surface.
def _rule_plan_rules(rules_suffix: str, rule: Mapping[str, Any]) -> str:
    """The plan rules under the owner's coverage rule (register Q30).

    Every number in this prose is read off the rule object; the module
    itself holds none. The model's judgment is the RATIONALE — which
    capabilities of the Mastery each tier's questions verify — and the
    one exception is the recorded drop request of Q29.
    """

    tier_list = list(pre_coverage.tiers(rule))
    split_text = ", ".join(
        f"{count} {tier}" for tier, count in rule["per_tier"].items()
    )
    per_tier_asks = " and ".join(
        f"which the {tier} questions verify" for tier in tier_list
    )
    return (
        "Phase 03: author a coverage plan for the generated questions of "
        "EACH pre-learning concept in this request. These questions are "
        "GENERATED for the prerequisite itself — never taken from this "
        "chapter, which is why none of its questions appear anywhere in "
        "this request. COVERAGE RULE (owner ruling, register Q30, rule "
        f"{rule['version']}): the owner fixes the coverage of EVERY "
        f"pre-learning concept at exactly {pre_coverage.describe(rule)} — "
        f"state total {pre_coverage.total(rule)} and a split of exactly "
        f"{split_text} for each pre_concept_id; no other total and no "
        "other tier. The counts are not yours to judge; the COVERAGE is. "
        "Your rationale says which capabilities of THIS prerequisite's "
        f"Mastery {per_tier_asks}, so that together they diagnose "
        "completely what a learner must already hold before this chapter "
        "starts — and it says so whatever the concept's "
        "depth, because every plan carries one. The one exception is a "
        "total of zero with no split: that is never coverage for a "
        "concept that ships, it is your recorded request to DROP the "
        "concept from the Pre map, because a prerequisite with no Mastery "
        "worth verifying before this chapter should not have become a "
        "pre-learning concept; state that judgment in the rationale, the "
        "run records it as a blocking finding on the Pre lane for a "
        "reviewer, and never ships the concept as if it were assessed. "
        "Naming a tier here states the tier each question is AUTHORED at "
        "(the Master's group tiers); the assessment lane groups the "
        "questions by that authored tier. Decide EVERY pre_concept_id in "
        "the request exactly once." + rules_suffix
    )


def _plan_rules(
    rules_suffix: str, rule: Mapping[str, Any] | None = None,
) -> str:
    if pre_coverage.is_adaptive(rule):
        return (
            "Phase 03: author the context-sufficient diagnostic coverage plan "
            "for EACH supplied pre-learning concept. The recorded adaptive "
            f"coverage policy {rule['version']} has no fixed total, per-tier "
            "quota, mandatory tier balance or target copied from another "
            "concept. Decide the total and split from the retained prerequisite "
            "mastery, what the learner must already know in earlier grades, "
            "and why the current chapter depends on it. State why each planned "
            "question is necessary for distinct diagnostic coverage; prefer "
            "a smaller sufficient set; never pad or add variations or drill merely "
            "to increase its size. Do not introduce new teaching or skills "
            "to make additional questions possible. No current-chapter source "
            "task or its paraphrase belongs here. Every plan carries a "
            "rationale; its tier split sums to its own total. Any subset of "
            "the allowed tiers may be appropriate, including a single tier. "
            "The plan's tiers are authored intent, carried on each question "
            "into grouping; they are not difficulty labels. Decide every "
            "pre_concept_id exactly once. A zero total with no split remains "
            "a recorded request to drop an unassessable concept, not coverage "
            "for a concept retained in the release. This explicit adaptive "
            "policy supersedes inherited numeric targets; use the following "
            "instructions for curricular scope and calibration, not to "
            "reinstate a question quota." + rules_suffix
        )
    if rule is not None:
        return _rule_plan_rules(rules_suffix, rule)
    return (
        "Phase 03: author a coverage plan for the generated questions of "
        "EACH pre-learning concept in this request. These questions are "
        "GENERATED for the prerequisite itself — never taken from this "
        "chapter, which is why none of its questions appear anywhere in "
        "this request. For each concept, state the total number of "
        "questions its own evidence supports, the split you intend across "
        "the tiers Basic, Intermediate and Advanced, and a rationale "
        "saying why that total and that split are right for THIS "
        "prerequisite: what the fundamental contains, how much of it a "
        "learner must be able to do before this chapter starts, and what "
        "the post-learning concepts listed as needing it will demand. "
        "EVERY plan carries a rationale — the one of ordinary size as "
        "much as the unusual one — so state yours whatever your numbers "
        "are. COVERAGE (Master Governing Contract v2.0 §8 — there are no "
        "quotas): plan the complete diagnostic coverage of THIS "
        "prerequisite's Mastery. Each capability a learner must already "
        "hold before the chapter starts is verified by a question, and "
        "nothing the chapter itself goes on to teach is. There is no "
        "target count, no minimum and no maximum, and no figure from any "
        "other concept, school, board or grade is a reference point: a "
        "thin pre-concept is never padded, and a rich one is never "
        "capped. The split across the tiers is left to your judgment of "
        "the prerequisite. POSTURE (owner steer, 2026-08-21): plan the "
        "minimum coverage that genuinely verifies the prerequisite — "
        "prefer fewer, more diagnostic questions, each earning its place "
        "by testing something the chapter's learning actually depends "
        "on, over breadth or drill. Plan what the evidence in front of "
        "you supports and say why — a prerequisite whose evidence "
        "supports six questions is planned at six, and one whose Mastery "
        "is a single definition the learner either holds or does not is "
        "planned at one; both are correct answers, not shortfalls. Every "
        "shipped pre-learning concept carries at least one diagnostic "
        "question (Master Governing Contract v2.0 §8.6), so a total of "
        "zero is never a coverage plan for a concept that ships: it is "
        "your recorded request to DROP the concept from the Pre map, "
        "because a prerequisite with no Mastery worth verifying before "
        "this chapter should not have become a pre-learning concept. "
        "State that judgment in the rationale. The run does not drop the "
        "concept itself — it records your request as a blocking finding "
        "on the Pre lane so a reviewer removes the concept or re-runs, "
        "and never ships the concept as if it were assessed. Naming a "
        "tier here states the coverage you intend; it is not "
        "a verdict on any question, and each question's own level is "
        "decided later and independently. Decide EVERY pre_concept_id in "
        "the request exactly once, and make the split account for the "
        "total you state." + rules_suffix
    )


def _rule_author_rules(rules_suffix: str, rule: Mapping[str, Any]) -> str:
    """The authoring rules under the owner's coverage rule (register Q30).

    The tier is authored, not decided later: each question carries the
    tier the plan's split assigns it, and the tiers are described in the
    assessment lane's own terms (what capability and construction the
    question actually requires), never as a difficulty label.
    """

    tier_list = list(pre_coverage.tiers(rule))
    split_text = ", then ".join(
        f"{count} {tier}" for tier, count in rule["per_tier"].items()
    )
    first, rest = tier_list[0], tier_list[1:]
    tier_meaning = (
        f"{first} questions verify that the learner holds the fundamental "
        "as this concept states it — recall, recognition and direct use in "
        "the form the Mastery names. "
    )
    if rest:
        tier_meaning += (
            f"{' and '.join(rest)} questions verify that the learner can "
            "apply the fundamental in a situation the post-learning "
            "concepts named as needing it will depend on — a step of "
            "transfer, never this chapter's own new teaching. "
        )
    return (
        "Phase 03: write this pre-learning concept's questions. Every "
        "question is GENERATED for the fundamental this concept teaches — "
        "no question of this chapter is ever lifted, reworded or "
        "paraphrased into a pre-learning artefact, and the chapter's own "
        "questions are deliberately absent from this request. COVERAGE "
        f"RULE (owner ruling, register Q30, rule {rule['version']}): write "
        f"exactly the coverage plan's split — {split_text} — and give "
        f"every question its tier field ({'|'.join(tier_list)}) exactly "
        "as the split assigns it; never pad, never trim, and never move a "
        "question between tiers to balance anything. " + tier_meaning
        + "Author every question for the level, grade, subject, board and "
        "context named in the chapter calibration and the run "
        "instructions: the same fundamental asked of a younger class in "
        "one subject and of an older class in another is two different "
        "questions, in vocabulary, in framing and in what counts as a "
        "complete answer. Each question tests whether the learner already "
        "holds the prerequisite, never what this chapter goes on to "
        "teach. answer is the complete expected answer; rationale says "
        "what the question checks the learner can do and why it sits at "
        "its tier. Vary the questions genuinely — never the same question "
        "with a number or a name changed. Do not label a question with a "
        "difficulty. Mint question_id positionally as PRQ-0001, PRQ-0002, "
        f"… in listing order across the whole list, {first} first. Wrap "
        "every mathematical expression exactly as [Katex] valid LaTeX "
        "[/Katex]." + rules_suffix
    )


def _author_rules(
    rules_suffix: str, rule: Mapping[str, Any] | None = None,
) -> str:
    if pre_coverage.is_adaptive(rule):
        return (
            "Phase 03: author this prerequisite's questions under adaptive "
            f"coverage policy {rule['version']}. The coverage plan has already "
            "chosen its own context-sufficient total and tier split. Write "
            "exactly that total and split, with each question's tier field "
            "carrying its intended tier; no fixed per-tier quota is in force. "
            "Stay inside the retained prior-grade description and mastery. "
            "Use needed-for links to calibrate relevance, never to import "
            "the current chapter's new teaching, its source questions or "
            "paraphrases. Questions must be distinct checks of what the "
            "learner should already know; do not pad, repeat with changed "
            "numbers/names, balance tiers or enlarge prerequisite scope. "
            "Basic verifies recognition/direct use of the retained foundation; "
            "Intermediate and Advanced require only source-supported prior "
            "transfer/integration when the chosen plan calls for it, not "
            "knowledge taught in the current chapter. Respect grade, subject, "
            "board and context. Provide complete self-contained tasks and "
            "solved expected answers; no solution or hint leaks into the "
            "learner question. Each rationale explains the prior capability "
            "checked and its planned tier. Do not assign difficulty. Mint "
            "PRQ-0001, PRQ-0002, … in listing order. Preserve the accepted "
            "plan's count and split; an author cannot amend a settled plan. "
            "Wrap math as [Katex] valid LaTeX [/Katex]. Inherited numeric "
            "targets do not override this explicit adaptive policy." + rules_suffix
        )
    if rule is not None:
        return _rule_author_rules(rules_suffix, rule)
    return (
        "Phase 03: write this pre-learning concept's questions. Every "
        "question is GENERATED for the fundamental this concept teaches — "
        "no question of this chapter is ever lifted, reworded or "
        "paraphrased into a pre-learning artefact, and the chapter's own "
        "questions are deliberately absent from this request. Write "
        "exactly the number your own coverage plan states, and cover what "
        "its rationale says it covers; never pad the set to look fuller "
        "and never trim it. Author every question for the level, grade, "
        "subject, board and context named in the chapter calibration and "
        "the run instructions: the same fundamental asked of a younger "
        "class in one subject and of an older class in another is two "
        "different questions, in vocabulary, in framing and in what "
        "counts as a complete answer. Each question tests whether the "
        "learner already holds the prerequisite, never what this chapter "
        "goes on to teach. answer is the complete expected answer; "
        "rationale says what the question checks the learner can do. Vary "
        "the questions genuinely — never the same question with a number "
        "or a name changed. Do not label a question with a tier or a "
        "difficulty: its level is decided later and independently. Mint "
        "question_id positionally as PRQ-0001, PRQ-0002, … in listing "
        "order. Wrap every mathematical expression exactly as [Katex] "
        "valid LaTeX [/Katex]." + rules_suffix
    )


# ---------------------------------------------------------------------------
# public entry


def build(
    env: Mapping[str, Any],
    pre_map: Mapping[str, Any],
    *,
    provider: kernel.Provider | None = None,
    author_provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
    progress_span: progress.Span | None = None,
) -> dict[str, Any]:
    """Author the coverage plan, then the questions, for every Pre concept.

    ``progress_span``, when given, is this stage's slice of the progress
    bar (mechanics only): the chapter plan takes the first unit and each
    authored pre-concept advances the rest, so the console bar moves
    through the authoring pass instead of freezing on its opening value.

    ``pre_map`` is ``premap.build``'s result. Returns ``{"plans":
    {pre_concept_id: {"total", "split", "rationale"}}, "questions":
    {pre_concept_id: [question]}, "blocked": {pre_concept_id: reason},
    "review_flags": {pre_concept_id: [flags]}, "decision_flags":
    {decision: [flags]}}``.

    An empty Pre map returns without spending a decision, and so does a
    pre-concept the model planned at zero — a chapter whose prerequisites
    the evidence supports thinly is never padded. A zero plan is the
    model's recorded request to drop that concept (register Q29): nothing
    here drops it, the concept reaches release staging with no question,
    and ``release_qc`` names it there as a blocking finding on the Pre
    lane (contract v2.0 §8.6) so a reviewer acts on the recorded rationale.
    """
    from . import fixer as fixer_mod
    from . import premap as premap_mod
    from . import prompts as prompts_mod

    env = envelope_mod.validate(env)
    qids = premap_mod.inventory_qids(env)
    rows = [
        row for row in (pre_map or {}).get("rows") or []
        if isinstance(row, Mapping)
        and str(row.get("_pre_concept_id") or "").strip()
    ]
    empty: dict[str, Any] = {
        "plans": {},
        "questions": {},
        "blocked": {},
        "review_flags": {},
        "decision_flags": {},
    }
    if not rows:
        rule = pre_coverage.rule_for(env)
        return _with_rule(empty, rule) if pre_coverage.is_adaptive(rule) else empty

    if provider is None:
        envelope_mod.require_live_api()
        provider = _live_plan
        author_provider = author_provider or _live_author
        critic = critic if critic is not None else _live_critic
        fixer = fixer or fixer_mod.live_fixer
    author_provider = author_provider or provider
    store = store or kernel.DecisionStore()
    envelope_sha = str(env.get("envelope_sha256") or "")
    # Redacted at the mint (premap._redact_ids, the same authority): the
    # Architect's ``language_topology_plan`` slot routes ``task_qids`` on
    # literary chapters, and carrying them verbatim would trip this
    # payload's own no-extraction post-condition exactly as it tripped
    # the Pre map's (job 64, 2026-08-21). Expository chapters author no
    # such slot, so their suffix — and their decision keys — are
    # unchanged.
    rules_suffix = premap_mod._redact_ids(
        prompts_mod.instruction_rules_suffix(
            env, slots=prompts_mod.PRE_QUESTION_SLOTS
        ),
        qids,
    )
    calibration = premap_mod.chapter_calibration(env)
    evidence = [
        concept_evidence(row, complete_scope=quality.active(env)) for row in rows
    ]
    concept_ids = [entry["pre_concept_id"] for entry in evidence]

    # Register Q30: which coverage posture this run executes under is a
    # recorded fact of its envelope, and it is said out loud either way.
    rule = pre_coverage.rule_for(env)
    if pre_coverage.is_adaptive(rule):
        progress.log(
            "Pre-Learning questions: adaptive coverage policy "
            f"{rule['version']} — the API chooses each prerequisite's "
            "context-sufficient total and tier split, with no padding or quota."
        )
    elif rule is not None:
        progress.log(
            "Pre-Learning questions: the owner's coverage rule "
            f"{rule['version']} is in force — every pre-concept is planned "
            f"at {pre_coverage.describe(rule)}, and each question is "
            "authored at its tier (register Q30)."
        )
    else:
        progress.log(
            "Pre-Learning questions: this envelope records no coverage "
            "rule, so the plan is the model's under contract v2.0 §8 "
            "(register Q26: no quota)."
        )

    # ---- the coverage plan: one decision over the whole Pre map -------
    #
    # Chapter-wide on purpose. Seeing every pre-concept at once is what
    # lets the model say "this fundamental is a single definition, six
    # questions; that one carries three distinct procedures, thirty" —
    # a judgment about relative depth that a per-concept decision cannot
    # make. It is still one plan per concept, decided once each. Under
    # the owner's rule the numbers are fixed and the judgment is the
    # rationale's coverage; the payload carries the rule explicitly so
    # the decision key moves with it.
    plan_payload = {
        "stage": "prequestions.plan",
        **capture_policy.boundary_fields(env),
        "rules": _plan_rules(rules_suffix, rule),
        "chapter": calibration,
        "pre_concepts": evidence,
    }
    if quality.active(env):
        plan_payload["rules"] += "\n" + capture_policy.QUALITY_INSTRUCTION
    if rule is not None:
        plan_payload["coverage_rule"] = copy.deepcopy(rule)
    # The pre-spend post-condition of the Pre lane's redaction discipline
    # (premap._redact_ids): everything in this payload was either authored
    # by the Pre lane behind premap's own fail-closed guard or redacted
    # before it got there, so this can only fire if a channel is added
    # here without being filtered. The SAME guard, not a second one.
    premap_mod._refuse_source_qids(
        plan_payload, qids, where="the pre-learning coverage plan payload"
    )

    def _record_block(error: object) -> str:
        """One recorded block, with any source identity dropped from it.

        A block is mechanical prose ABOUT a failed decision, and it rides
        out on ``blocked`` and on a review flag — neither of which is
        inside the fail-closed guarded surface (see the guard note below).
        A checker defect may quote text the model sent, so the recorded
        note is REDACTED on the way in (``premap._redact_ids``, the same
        act premap applies to upstream provenance): dropping an id token
        from an error note is plainly mechanically applicable, and it
        keeps the "no source QID in a Pre artefact" property true of the
        error path as well as the content path, without making one
        blocked concept refuse the whole lane.
        """

        return premap_mod._redact_ids(str(error), qids)

    try:
        plan_decision = kernel.decide(
            kind="prequestions.plan",
            unit_id="chapter",
            envelope_sha256=envelope_sha,
            payload=plan_payload,
            provider=provider,
            checker=_plan_checker(concept_ids, rule),
            critic=critic,
            store=store,
            policy_version=(
                ADAPTIVE_POLICY_VERSION
                if pre_coverage.is_adaptive(rule)
                else POLICY_VERSION
            ) + _foundation_policy_suffix(plan_payload),
            fixer=fixer,
        )
    except kernel.ContractError as error:
        # Flags — never raises a run down. The SAME rule the authoring
        # half applies one decision later, for the same reason: The Fixer
        # has already had its one recorded decision inside
        # ``kernel.decide`` (Q13) wherever one is wired, and taking the
        # chapter down here would discard a finished Post map AND a
        # finished Pre map over the questions alone, which CLAUDE.md
        # forbids in as many words ("finished work always ships"). This
        # half is the harsher of the two, because the plan is one
        # decision over every pre-concept:
        # unwrapped, a single concept's unreconcilable split would end a
        # run that has everything else in hand. So every pre-concept is
        # recorded as blocked, loudly and by name, and the run completes
        # — nothing is guessed, nothing is silent, and R4 is satisfied by
        # a flag saying exactly what did not ship.
        block = _record_block(error)
        progress.log(
            "Pre-Learning questions: the coverage plan could not be made "
            "to contract, so no pre-concept has generated questions; the "
            f"chapter's finished maps ship and all {len(concept_ids)} "
            "pre-concept(s) are flagged for review: " + block,
            level="error",
        )
        return _with_rule({
            "plans": {},
            "questions": {},
            "blocked": {concept_id: block for concept_id in concept_ids},
            "review_flags": {
                concept_id: [
                    "the coverage plan for the whole Pre map could not be "
                    "authored to contract, so this pre-concept ships with "
                    "no generated questions and needs review: " + block
                ]
                for concept_id in concept_ids
            },
            "decision_flags": {},
        }, rule)
    plan_flags = list(plan_decision.get("review_flags") or [])
    decision_flags: dict[str, list[str]] = {}
    if plan_flags:
        decision_flags["plan"] = list(plan_flags)

    plans: dict[str, dict[str, Any]] = {}
    for row in plan_decision["response"].get("plans") or []:
        if not isinstance(row, Mapping):
            continue
        concept_id = str(row.get("pre_concept_id") or "")
        if concept_id not in set(concept_ids):
            continue
        plans[concept_id] = {
            "total": int(row.get("total") or 0),
            "split": [
                {
                    "tier": str(entry.get("tier") or ""),
                    "count": int(entry.get("count") or 0),
                }
                for entry in row.get("split") or []
                if isinstance(entry, Mapping)
            ],
            "rationale": _normal(row.get("rationale")),
        }

    progress.log(
        "Pre-Learning questions: coverage planned for "
        f"{len(plans)} pre-concept(s), each with its own authored "
        "rationale (model-judged; a thin pre-concept is never padded)."
    )
    # One unit for the plan (recorded above), one per pre-concept whose
    # plan asked for questions. A fixed mechanical allocation, not a
    # duration estimate.
    span_tracker = None
    if progress_span is not None:
        wanted_count = sum(
            1 for concept_id in concept_ids
            if int((plans.get(concept_id) or {}).get("total") or 0) > 0
        )
        span_tracker = progress_span.tracker(1.0 + wanted_count)
        span_tracker.set_units(
            1.0,
            label=(
                "Pre-Learning questions: coverage planned for "
                f"{len(plans)} pre-concept(s)"
            ),
        )

    # ---- authoring: one decision per pre-concept its plan asked for ---
    review_flags: dict[str, list[str]] = {}
    questions: dict[str, list[dict[str, Any]]] = {}
    blocked: dict[str, str] = {}
    evidence_by_id = {entry["pre_concept_id"]: entry for entry in evidence}
    wanted = [
        concept_id for concept_id in concept_ids
        if int((plans.get(concept_id) or {}).get("total") or 0) > 0
    ]

    def _author(concept_id: str):
        plan = plans[concept_id]
        payload = {
            "stage": "prequestions.author",
            **capture_policy.boundary_fields(env),
            "rules": _author_rules(rules_suffix, rule),
            "chapter": calibration,
            "coverage_plan": plan,
            "pre_concept": evidence_by_id[concept_id],
        }
        if quality.active(env):
            payload["rules"] += "\n" + capture_policy.QUALITY_INSTRUCTION
        if rule is not None:
            payload["coverage_rule"] = copy.deepcopy(rule)
        premap_mod._refuse_source_qids(
            payload, qids,
            where="the pre-learning question authoring payload",
        )
        try:
            decision = kernel.decide(
                kind="prequestions.author",
                unit_id=concept_id,
                envelope_sha256=envelope_sha,
                payload=payload,
                provider=author_provider,
                checker=_author_checker(
                    concept_id, int(plan["total"]),
                    rule=rule,
                    split={
                        str(entry.get("tier") or ""): int(entry.get("count") or 0)
                        for entry in plan.get("split") or []
                        if isinstance(entry, Mapping)
                    },
                ),
                critic=critic,
                store=store,
                policy_version=(
                    ADAPTIVE_POLICY_VERSION
                    if pre_coverage.is_adaptive(rule)
                    else POLICY_VERSION
                ) + _foundation_policy_suffix(payload),
                fixer=fixer,
            )
        except kernel.ContractError as error:
            # Flags, or routes to the Fixer — never raises a run down.
            # The Fixer already ran inside ``kernel.decide`` (Q13); this
            # is what is left when even its one recorded decision could
            # not satisfy the contract. Taking the chapter down here
            # would discard a finished Post map and a finished Pre map
            # over one concept's questions, which CLAUDE.md forbids in as
            # many words ("finished work always ships"). So the concept
            # is recorded as blocked, loudly, and the run completes:
            # nothing is guessed, nothing is silent, and R4 is satisfied
            # by the flag naming exactly what did not ship. What did NOT
            # arrive to contract is deliberately not shipped alongside
            # it: ``ContractError`` carries the defects, not the failing
            # candidate, and shipping rows that failed the mechanical
            # contract (an empty question_text, a broken mint) is exactly
            # the broken artefact CLAUDE.md's gates exist to refuse.
            return concept_id, [], _record_block(error), []
        authored: list[dict[str, Any]] = []
        for row in decision["response"].get("questions") or []:
            if not isinstance(row, Mapping):
                continue
            question_id = str(row.get("question_id") or "")
            entry = {
                "pre_question_id": pre_question_id(concept_id, question_id),
                "question_id": question_id,
                "pre_concept_id": concept_id,
                "question_text": _normal(row.get("question_text")),
                "answer": _normal(row.get("answer")),
                "rationale": _normal(row.get("rationale")),
            }
            if rule is not None:
                # Register Q30: the tier is authored, and it rides the
                # question into the assessment lane, which groups by it.
                entry["tier"] = _normal(row.get("tier"))
            authored.append(entry)
        return concept_id, authored, "", list(decision.get("review_flags") or [])

    workers = config.phase3_decision_workers()
    for concept_id, authored, block, flags in kernel.parallel_map_in_order(
        wanted,
        _author,
        max_workers=workers,
        on_result=(
            None if span_tracker is None
            else lambda index, item, result: span_tracker.advance(
                1.0,
                label=(
                    "Pre-Learning questions: pre-concept "
                    f"{index + 1}/{len(wanted)} authored"
                ),
            )
        ),
    ):
        if block:
            blocked[concept_id] = block
            review_flags.setdefault(concept_id, []).append(
                "the questions this pre-concept's own coverage plan asked "
                "for could not be authored to contract; the concept ships "
                "with no generated questions and needs review: " + block
            )
            continue
        questions[concept_id] = authored
        if flags:
            decision_flags[f"author#{concept_id}"] = list(flags)
            review_flags.setdefault(concept_id, []).extend(flags)

    # Explicit output uniqueness over the durable id, rather than relying
    # on a downstream duplicate raise to notice (the step-6 lesson
    # recorded in docs/restructure-handoff.md about implicit cell_id
    # uniqueness). Mechanics: the id is minted from two ids this pipeline
    # assigned positionally, so a collision is a defect in the mint.
    minted = [
        row["pre_question_id"]
        for rows_for_concept in questions.values()
        for row in rows_for_concept
    ]
    if len(minted) != len(set(minted)):
        duplicates = sorted({
            value for value in minted if minted.count(value) > 1
        })
        raise PreQuestionError(
            "generated pre-learning question id(s) collided: "
            + ", ".join(duplicates)
            + " (a defect in the positional mint — report if hit)"
        )

    # ---- the fail-closed guard, over AUTHORED content only ------------
    #
    # The guarded surface is stated explicitly rather than implied by
    # ordering: exactly ``plans`` and ``questions`` — the content this
    # pass authors — are scanned. ``plans`` is inside for the reason
    # premap's ``topics`` is: a plan rationale rides the same carry
    # channel and the same snapshot, which is the artefact the steer
    # names.
    #
    # Three channels are deliberately OUTSIDE it, each for a recorded
    # reason rather than by omission:
    #
    # * ``review_flags`` and ``decision_flags`` — premap records the same
    #   rule for the same reason. The critic is asked whether a question
    #   is a source question reworded; a critic that answers by naming
    #   one must not thereby turn its own advisory dissent into a
    #   permanent, replaying gate against a content-addressed store,
    #   which Q10 forbids in as many words;
    # * ``blocked`` — a block is mechanical prose about a decision that
    #   did not arrive, and refusing on it would convert ONE blocked
    #   pre-concept into a refusal of the whole lane (the runner zeroes
    #   ``pre_questions`` on ``PreExtractionError``), which is strictly
    #   worse than the defect. So the identity property is kept the other
    #   way, by the act premap uses for text it records rather than
    #   authors: a checker defect may quote what the model sent, so every
    #   block is redacted on the way in (``_record_block``), and the
    #   authoring checker names positions and the expected mint instead
    #   of echoing the ids it was handed.
    #
    # Nothing a learner would see rides any of the three: they carry
    # auditor and error prose about the artefact, never its content.
    premap_mod._refuse_source_qids(
        {"plans": plans, "questions": questions},
        qids,
        where="a generated pre-learning question",
    )

    authored_count = sum(len(rows_for) for rows_for in questions.values())
    progress.log(
        f"Pre-Learning questions: {authored_count} generated question(s) "
        f"across {len(questions)} pre-concept(s), each set exactly the "
        "size its own plan authored"
        + (f"; {len(blocked)} pre-concept(s) blocked and flagged"
           if blocked else "")
        + ".",
        level="success",
    )
    return _with_rule({
        "plans": plans,
        "questions": questions,
        "blocked": blocked,
        "review_flags": review_flags,
        "decision_flags": decision_flags,
    }, rule)


def _with_rule(
    result: dict[str, Any], rule: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Record the coverage rule the questions were authored under.

    Present only when a rule was in force (register Q30): the release
    audit reads it back to hold the staged questions to the rule, and its
    absence is the legacy shape — no rule recorded — never a verdict.
    """

    if rule is not None:
        result["coverage_rule"] = copy.deepcopy(dict(rule))
    return result
