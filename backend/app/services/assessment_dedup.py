"""Duplicate generated questions are removed by a recorded verdict (Q15).

Owner ruling, 21 Aug 2026 (job-65 review: T01_C01 Q02 and Q03 were the
same question re-worded): when the model judges two GENERATED
pre-learning questions to be the same question — a paraphrase, a number
or a name swapped — one survivor ships and the others are REMOVED from
the Master, recorded on the release under ``duplicates_removed`` with
the reason, reviewable, never silent. This amends the flag-only doctrine
for exactly this case.

P3 (owner audit + approval, 2026-08-29) extends the same recorded-verdict
mechanics to SOURCE questions: both audit correctors deleted source items
the pipeline shipped twice in different guises (a verbatim double-ship
under two labels; an umbrella whose parts were already standalone rows).
``decide_source_duplicates`` below folds those before spend as a recorded
``source_duplicates_represented`` disposition — coverage moves to the
survivor, never lost — keeping Rule C's exactly-once accounting through
the disposition record rather than through a second physical row.

The verdict runs per pre-learning concept group BEFORE the cell
verdicts, so a removed question costs nothing downstream. String
similarity is not consulted anywhere — sameness is the model's judgment
(Rule 1), checked only mechanically: every cited id exists, a survivor
is never also removed, no question is ruled twice.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .phase3 import kernel

GENERATED_DEDUP_POLICY_VERSION = "assessment-generated-dedup-1"

GENERATED_DEDUP_SYSTEM = (
    "You are the Aegis duplicate-question judge. You are given every "
    "GENERATED pre-learning question authored for ONE pre-learning "
    "concept. Identify sets that are the same question re-worded — a "
    "paraphrase, a number or a name changed, the same ask with a "
    "different opener — and for each set choose the ONE survivor that "
    "asks it best (clearest wording, cleanest answer). Questions that "
    "merely share a topic are NOT duplicates: they must ask the same "
    "thing with the same answer. When nothing duplicates, return an "
    "empty duplicate_sets array — there is no quota and removal is "
    "never a goal. Respond with a single JSON object and nothing else:\n"
    '{"duplicate_sets":[{"survivor_pre_question_id":"",'
    '"removed":[{"pre_question_id":"","reason":"why it is the same '
    'question as the survivor"}]}],"confidence":0.0,'
    '"rationale":"evidence-bound reason"}'
)

GENERATED_DEDUP_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis "
    "duplicate-question decision. Audit the proposed duplicate sets "
    "against the actual question texts and answers: is any removed "
    "question genuinely a DIFFERENT ask (different answer, different "
    "skill) mislabelled as a duplicate, and is any obvious re-wording "
    "pair missed? Dissent must name the pre_question_id(s). Respond "
    'with a single JSON object: {"verdict":"concur|dissent",'
    '"confidence":0.0,"issues":["..."]}'
)


class DedupDecisionError(ValueError):
    """A duplicate verdict violated its mechanical contract."""


def _live_dedup(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        GENERATED_DEDUP_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_dedup_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        GENERATED_DEDUP_CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_validation",
    )


def _dedup_checker(
    question_ids: set[str],
    *,
    survivor_field: str = "survivor_pre_question_id",
    removed_field: str = "pre_question_id",
) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        sets = response.get("duplicate_sets")
        if not isinstance(sets, list):
            return ["duplicate_sets must be an array"]
        ruled: set[str] = set()
        for position, entry in enumerate(sets, start=1):
            if not isinstance(entry, Mapping):
                defects.append(f"duplicate set {position} is not an object")
                continue
            survivor = str(entry.get(survivor_field) or "")
            if survivor not in question_ids:
                defects.append(
                    f"duplicate set {position}: survivor "
                    f"{survivor!r} is not one of the supplied questions"
                )
            removed = entry.get("removed")
            if not isinstance(removed, list) or not removed:
                defects.append(
                    f"duplicate set {position}: removed must be a "
                    "non-empty array"
                )
                removed = []
            cited = {survivor} if survivor in question_ids else set()
            for row in removed:
                if not isinstance(row, Mapping):
                    defects.append(
                        f"duplicate set {position}: a removed entry is "
                        "not an object"
                    )
                    continue
                removed_id = str(row.get(removed_field) or "")
                if removed_id not in question_ids:
                    defects.append(
                        f"duplicate set {position}: removed id "
                        f"{removed_id!r} is not one of the supplied "
                        "questions"
                    )
                if removed_id == survivor:
                    defects.append(
                        f"duplicate set {position}: the survivor "
                        f"{survivor!r} cannot also be removed"
                    )
                if not str(row.get("reason") or "").strip():
                    defects.append(
                        f"duplicate set {position}: removed "
                        f"{removed_id!r} carries no reason"
                    )
                cited.add(removed_id)
            overlap = ruled & cited
            if overlap:
                defects.append(
                    "a question may be ruled in at most one duplicate "
                    f"set; {sorted(overlap)!r} appear twice"
                )
            ruled |= cited
        return defects

    return check


def decide_generated_duplicates(
    questions: list[Mapping],
    *,
    concept_id: str,
    concept_evidence: Mapping[str, Any] | None,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> tuple[list[Mapping], list[dict[str, Any]]]:
    """One recorded duplicate verdict for one concept's generated set.

    Returns ``(survivors, removed_records)`` — survivors in input order,
    each removed record carrying the question, its survivor, the reason,
    and the decision audit. A group of fewer than two questions costs
    nothing and removes nothing.
    """

    rows = [q for q in questions if isinstance(q, Mapping)]
    if len(rows) < 2:
        return list(questions), []
    by_id = {
        str(q.get("pre_question_id") or ""): q
        for q in rows
        if str(q.get("pre_question_id") or "")
    }
    if len(by_id) < 2:
        return list(questions), []

    if provider is None:
        provider = _live_dedup
        critic = critic if critic is not None else _live_dedup_critic
    store = store or kernel.DecisionStore()

    payload = {
        "stage": "assessment.generated_dedup",
        "rules": GENERATED_DEDUP_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "pre_concept": copy.deepcopy(dict(concept_evidence or {})),
        "questions": [
            {
                "pre_question_id": str(q.get("pre_question_id") or ""),
                "question_text": str(q.get("question_text") or ""),
                "answer": str(q.get("answer") or ""),
                "rationale": str(q.get("rationale") or ""),
            }
            for q in rows
            if str(q.get("pre_question_id") or "")
        ],
    }
    decision = kernel.decide(
        kind="assessment.generated_dedup",
        unit_id=concept_id,
        envelope_sha256=envelope_sha256,
        payload=payload,
        provider=provider,
        checker=_dedup_checker(set(by_id)),
        critic=critic,
        store=store,
        policy_version=GENERATED_DEDUP_POLICY_VERSION,
        fixer=fixer,
    )
    response = decision["response"]
    decision_flags = list(decision.get("review_flags") or [])

    removed_records: list[dict[str, Any]] = []
    removed_ids: set[str] = set()
    for entry in response.get("duplicate_sets") or []:
        survivor = str(entry.get("survivor_pre_question_id") or "")
        for row in entry.get("removed") or []:
            removed_id = str(row.get("pre_question_id") or "")
            removed_ids.add(removed_id)
            removed_records.append({
                "pre_question_id": removed_id,
                "pre_concept_id": concept_id,
                "survivor_pre_question_id": survivor,
                "reason": str(row.get("reason") or ""),
                "generated_question": copy.deepcopy(
                    dict(by_id.get(removed_id) or {})
                ),
                "flags": [
                    "duplicate generated question removed (Q15): "
                    f"the survivor is {survivor!r}; "
                    + str(row.get("reason") or "")
                ] + decision_flags,
            })
    survivors = [
        q for q in questions
        if str(
            (q or {}).get("pre_question_id") if isinstance(q, Mapping)
            else ""
        ) not in removed_ids
    ]
    return survivors, removed_records


# --------------------------------------------------------------------------- #
# Source-lane duplicate coverage (P3, owner audit 2026-08-29)
# --------------------------------------------------------------------------- #

SOURCE_DEDUP_POLICY_VERSION = "assessment-source-dedup-1"

SOURCE_DEDUP_SYSTEM = (
    "You are the Aegis duplicate-question judge for one Post-Learning "
    "Master. You are given EVERY chapter-teaching source question headed "
    "into this Master file, after compound sub-parts have already folded "
    "into their parents. Identify sets that ship the same assessment more "
    "than once: the same question shipped under two labels (verbatim or "
    "trivially reworded), the same ask with the same answer in another "
    "guise, or an umbrella item whose every part already ships as a "
    "standalone question in this set — the audited Master shipped "
    "'Is 5-5=0 a natural number?' twice under two labels, and a "
    "discuss-in-pairs umbrella whose two parts were already standalone "
    "rows (owner audit, 2026-08-29). For each set choose the ONE survivor "
    "that asks it best. Questions that merely share a topic, a method, "
    "numbers, or a context are NOT duplicates: they must ask the same "
    "thing with the same answer. Members of one recorded alternative set "
    "(an either/or choice the source paper itself offers) are a "
    "deliberate pair, never duplicates. When nothing duplicates, return "
    "an empty duplicate_sets array — removal is never a goal and there "
    "is no quota. Respond with a single JSON object and nothing else:\n"
    '{"duplicate_sets":[{"survivor_source_qid":"",'
    '"removed":[{"source_qid":"","reason":"why it ships the same '
    'assessment as the survivor"}]}],"confidence":0.0,'
    '"rationale":"evidence-bound reason"}'
)

SOURCE_DEDUP_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis source-lane "
    "duplicate decision. Audit the proposed duplicate sets against the "
    "actual question texts and answers: is any removed question genuinely "
    "a DIFFERENT ask (different answer, different skill) mislabelled as a "
    "duplicate, is a recorded alternative-set pair being wrongly removed, "
    "and is any obvious double-ship missed? Dissent must name the "
    'source_qid(s). Respond with a single JSON object: '
    '{"verdict":"concur|dissent","confidence":0.0,"issues":["..."]}'
)


def _live_source_dedup(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        SOURCE_DEDUP_SYSTEM, prompts.render(payload),
        purpose="concept_mapping",
    )


def _live_source_dedup_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation
    from .phase3 import prompts

    return generation._openai_json(
        SOURCE_DEDUP_CRITIC_SYSTEM, prompts.render(payload),
        purpose="concept_validation",
    )


def decide_source_duplicates(
    atoms: list[Mapping],
    *,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> tuple[list[Mapping], list[dict[str, Any]]]:
    """One recorded duplicate verdict across the whole source-question set.

    P3 (owner audit 2026-08-29): both audit correctors deleted source
    questions the pipeline shipped twice in different guises — a verdict
    the compound fold's recorded-identity mechanics cannot make, so the
    model makes it here, once, BEFORE any cell verdict is paid for, with
    the advisory critic and one recorded decision per run. Returns
    ``(kept_atoms, represented_records)`` — kept atoms in input order;
    each record names the removed qid, its survivor, and the reason, so
    the removal is a reviewable disposition, never silent loss (R4).
    Fewer than two atoms costs nothing and removes nothing.
    """

    rows = [a for a in atoms if isinstance(a, Mapping)]
    by_id = {
        str(a.get("source_qid") or ""): a
        for a in rows
        if str(a.get("source_qid") or "")
    }
    if len(by_id) < 2:
        return list(atoms), []

    if provider is None:
        provider = _live_source_dedup
        critic = critic if critic is not None else _live_source_dedup_critic
    store = store or kernel.DecisionStore()

    payload = {
        "stage": "assessment.source_dedup",
        "rules": SOURCE_DEDUP_SYSTEM,
        "metadata": copy.deepcopy(dict(meta)),
        "questions": [
            {
                "source_qid": str(a.get("source_qid") or ""),
                "source_label": str(a.get("source_paper_number") or ""),
                "source_kind": str(a.get("source_kind") or ""),
                "question": str(
                    a.get("normalized_public_text")
                    or a.get("raw_text")
                    or ""
                ),
                "answer": str(a.get("source_answer") or ""),
                "shared_context": str(a.get("shared_context") or "")[:600],
                "alternative_set_id": str(
                    a.get("alternative_set_id") or ""
                ),
            }
            for a in rows
            if str(a.get("source_qid") or "")
        ],
    }
    decision = kernel.decide(
        kind="assessment.source_dedup",
        unit_id="post-master-source-set",
        envelope_sha256=envelope_sha256,
        payload=payload,
        provider=provider,
        checker=_dedup_checker(
            set(by_id),
            survivor_field="survivor_source_qid",
            removed_field="source_qid",
        ),
        critic=critic,
        store=store,
        policy_version=SOURCE_DEDUP_POLICY_VERSION,
        fixer=fixer,
    )
    response = decision["response"]
    decision_flags = list(decision.get("review_flags") or [])

    represented: list[dict[str, Any]] = []
    removed_ids: set[str] = set()
    for entry in response.get("duplicate_sets") or []:
        survivor = str(entry.get("survivor_source_qid") or "")
        for row in entry.get("removed") or []:
            removed_id = str(row.get("source_qid") or "")
            removed_ids.add(removed_id)
            removed_atom = by_id.get(removed_id) or {}
            represented.append({
                "source_qid": removed_id,
                "source_label": str(
                    removed_atom.get("source_paper_number") or ""
                ),
                "duplicate_of": survivor,
                "reason": str(row.get("reason") or ""),
                "flags": [
                    "duplicate source question represented by its "
                    f"survivor (P3): {survivor!r}; "
                    + str(row.get("reason") or "")
                ] + decision_flags,
            })
    kept = [
        a for a in atoms
        if str(
            (a or {}).get("source_qid") if isinstance(a, Mapping) else ""
        ) not in removed_ids
    ]
    return kept, represented
