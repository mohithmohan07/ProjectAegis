"""Pass 2.9c — Prequestions: the Pre-Learning coverage plan and its questions.

docs/aegis-restructure.md §4 Phase 03 (Q4, resolved per D3; calibration
re-set by owner steer 2026-08-20 to ~10, then 2026-08-21 to about 5
questions per pre-concept with the tier split left to the model, plus
the diagnostic posture: plan the minimum coverage that genuinely
verifies the prerequisite): neither mandatory quota nor maximum; the
model authors a concept-specific coverage plan; variance carries an
authored, critic-flagged rationale; an explicit blueprint may override;
a thin pre-concept is never padded.

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

The stated norm therefore appears in exactly one place: as
explicitly-labelled CALIBRATION prose inside ``_plan_rules``, quoted with
Q4's own qualifiers attached, because "variance carries a rationale" is
undefined if the model cannot see the norm it may vary from. It appears
in no constant, no checker, no threshold, no default and no comparison —
pinned by a regression over the whole validator surface. And because a
stated norm becoming a de facto quota is the real hazard, the advisory
critic is asked in as many words whether the plan is **evidence-led or
anchored on the stated norm**: a thin pre-concept planning six questions
with a good reason is a correct outcome, not a defect.

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

**The tier/difficulty axis is not conflated, and the level verdict is not
pre-empted.** Q4's original "20 Basic and 20 Intermediate" — and every
later calibration of it, Q20's ~5-with-the-split-left-to-the-model
included — names the GROUP TIER (``assessment_grouping.TIER_CODES``),
which is decided per question by an independent level verdict in the
assessment lane that is explicitly told no other label has authority
over it. The workbook's
``level_of_difficulty`` (``bulk_import.DIFFICULTY_LEVELS`` —
Less/Moderate/High) is a different vocabulary entirely. So: the plan's
split is an authored statement of the COVERAGE it intends, read in the
tier vocabulary (taken from its one owner, never re-typed here), and an
authored question carries **no tier and no difficulty field at all**.
Each question's tier is the assessment lane's to decide, later,
independently.

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

import re
from typing import Any, Callable, Mapping

from . import envelope as envelope_mod
from . import kernel
from ... import config
from .. import progress

POLICY_VERSION = "prequestions-1"

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


def concept_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
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


def _plan_checker(
    pre_concept_ids: list[str],
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: every concept planned once, the plan self-consistent.

    What total any pre-concept's evidence supports, and how it splits, is
    entirely the model's judgment — nothing here bounds, floors, ceilings
    or compares a count to any external number, and there is no external
    number in this function to compare one to. The three things checked
    are marker accounting and arithmetic between numbers the model itself
    wrote:

    * every pre-concept in the request is decided exactly once (R4 — a
      pre-concept nobody planned is a learner's questions lost silently);
    * the split's tiers come from the group-tier vocabulary, no tier
      twice, and the split's counts sum to the plan's OWN total;
    * **the rationale is required unconditionally** (spec T6). Every
      plan says why it is the size it is. That is what makes a
      comparison against a norm unnecessary anywhere in this codebase.
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
            if total is not None and planned != total:
                # Arithmetic between the model's OWN two numbers.
                defects.append(
                    f"{concept_id} split sums to {planned} but its own total "
                    f"says {total}; the split accounts for the total you "
                    "authored"
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
) -> Callable[[Mapping[str, Any]], list[str]]:
    """Mechanics only: positional ids, non-empty fields, plan↔output.

    ``planned_total`` is the model's OWN number, carried over from the
    plan decision it authored — this is the questions delivered matching
    the plan the model itself wrote, with no external number on either
    side of the comparison. A shortfall the bounded corrections cannot
    close routes to The Fixer, and if that fails too the caller records
    the concept as blocked and the run completes; it never raises a
    chapter down.
    """

    def check(response: Mapping[str, Any]) -> list[str]:
        defects: list[str] = []
        rows = response.get("questions")
        if not isinstance(rows, list):
            return ["response has no questions array"]
        expected = mint_question_ids(len(rows))
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                defects.append("a question entry is not an object")
                continue
            label = expected[position]
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
        return defects

    return check


# ---------------------------------------------------------------------------
# live adapters


def _live_plan(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREQUESTIONS_PLAN_SYSTEM, prompts.render(payload),
        purpose="pre_learning",
    )


def _live_author(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREQUESTIONS_AUTHOR_SYSTEM, prompts.render(payload),
        purpose="pre_learning",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import prompts
    from .. import generation

    return generation._openai_json(
        prompts.PREQUESTIONS_CRITIC_SYSTEM, prompts.render(payload),
        purpose="pre_learning",
    )


# The ONE place the doc's stated norm appears, and it appears as what it
# is: labelled calibration prose inside the plan payload's rules, quoted
# with Q4's own qualifiers attached. Q4's "any variance in total or split
# carries an authored rationale" is undefined if the model cannot see the
# norm it may vary from — but the norm is evidence the model weighs, never
# a number any code holds. It is in no constant, checker, threshold,
# default or comparison anywhere in this codebase, and a regression pins
# that over the whole validator surface.
def _plan_rules(rules_suffix: str) -> str:
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
        "are. CALIBRATION (the reference point, not a rule): the "
        "recorded target for pre-learning coverage is about 5 questions "
        "per concept, with the split across the tiers left to your "
        "judgment of the prerequisite (owner steer, 2026-08-21; "
        "supersedes the earlier ~10). That figure is a target this "
        "project recorded, not an observed practice of any school, "
        "board or grade, and it is neither a mandatory quota nor a "
        "maximum; a thin pre-concept is never padded to reach it, and a "
        "rich one is not capped at it. POSTURE (owner steer, "
        "2026-08-21): plan the minimum coverage that genuinely verifies "
        "the prerequisite — prefer fewer, more diagnostic questions, "
        "each earning its place by testing something the chapter's "
        "learning actually depends on, over breadth or drill. "
        "Plan what the evidence in front of you supports and say why — a "
        "prerequisite whose evidence supports six questions is planned at "
        "six, and that is a correct answer, not a shortfall. A plan of "
        "zero is legitimate where the fundamental is held by a single "
        "definition the learner either knows or does not. Naming a tier "
        "here states the coverage you intend; it is not a verdict on any "
        "question, and each question's own level is decided later and "
        "independently. Decide EVERY pre_concept_id in the request "
        "exactly once, and make the split account for the total you "
        "state." + rules_suffix
    )


def _author_rules(rules_suffix: str) -> str:
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
) -> dict[str, Any]:
    """Author the coverage plan, then the questions, for every Pre concept.

    ``pre_map`` is ``premap.build``'s result. Returns ``{"plans":
    {pre_concept_id: {"total", "split", "rationale"}}, "questions":
    {pre_concept_id: [question]}, "blocked": {pre_concept_id: reason},
    "review_flags": {pre_concept_id: [flags]}, "decision_flags":
    {decision: [flags]}}``.

    An empty Pre map returns without spending a decision, and so does a
    pre-concept the model planned at zero — a chapter whose prerequisites
    the evidence supports thinly is never padded.
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
        return empty

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
    evidence = [concept_evidence(row) for row in rows]
    concept_ids = [entry["pre_concept_id"] for entry in evidence]

    # ---- the coverage plan: one decision over the whole Pre map -------
    #
    # Chapter-wide on purpose. Seeing every pre-concept at once is what
    # lets the model say "this fundamental is a single definition, six
    # questions; that one carries three distinct procedures, thirty" —
    # a judgment about relative depth that a per-concept decision cannot
    # make. It is still one plan per concept, decided once each.
    plan_payload = {
        "stage": "prequestions.plan",
        "rules": _plan_rules(rules_suffix),
        "chapter": calibration,
        "pre_concepts": evidence,
    }
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
            checker=_plan_checker(concept_ids),
            critic=critic,
            store=store,
            policy_version=POLICY_VERSION,
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
        return {
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
        }
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
            "rules": _author_rules(rules_suffix),
            "chapter": calibration,
            "coverage_plan": plan,
            "pre_concept": evidence_by_id[concept_id],
        }
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
                checker=_author_checker(concept_id, int(plan["total"])),
                critic=critic,
                store=store,
                policy_version=POLICY_VERSION,
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
            authored.append({
                "pre_question_id": pre_question_id(concept_id, question_id),
                "question_id": question_id,
                "pre_concept_id": concept_id,
                "question_text": _normal(row.get("question_text")),
                "answer": _normal(row.get("answer")),
                "rationale": _normal(row.get("rationale")),
            })
        return concept_id, authored, "", list(decision.get("review_flags") or [])

    workers = config.phase3_decision_workers()
    for concept_id, authored, block, flags in kernel.parallel_map_in_order(
        wanted, _author, max_workers=workers
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
    return {
        "plans": plans,
        "questions": questions,
        "blocked": blocked,
        "review_flags": review_flags,
        "decision_flags": decision_flags,
    }
