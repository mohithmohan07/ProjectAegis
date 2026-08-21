"""Recorded assessment-cell verdicts for source-owned questions.

One source atom becomes one reuse blueprint cell through the shared decision
kernel.  The model owns sheet kind, question category, cognitive skill,
difficulty, and marks.  Local code only binds identities, validates the
response schema, assembles the cell record, and preserves the decision audit.
Critic dissent is advisory and The Fixer handles a mechanically blocked
response without inventing a local semantic fallback.
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any, Mapping

from .. import bulk_import as bi
from .. import config
from . import assessment_profile
from .phase3 import kernel


CELL_POLICY_VERSION = "assessment-cell-2"

CELL_SYSTEM = (
    "You are the Aegis assessment-cell author. For ONE source-owned question "
    "or task, decide the blueprint cell it fulfils when reused as an "
    "assessment item: sheet kind, question category, cognitive skill "
    "(Bloom), difficulty, and marks. Read the complete task, answer evidence, "
    "shared context, alternatives, multipart relationships, and assets. Never "
    "infer meaning from text length, print position, neighbouring questions, "
    "or how many questions exist. There is no quota: do not balance or spread "
    "categories, skills, difficulties, or marks. The no-local-fallback "
    "invariant applies; give your evidence-bound verdict rather than applying "
    "a default.\n"
    "sheet_kind is objective, subjective, or descriptive as allowed by the "
    "active assessment profile. question_category must be the CMS's exact "
    "name for the category, from the sheet kind's own list — objective: "
    "Multiple Choice Question, Assertion & Reasons, True/False, Fill in "
    "the Blanks; subjective: Fill in the Blanks, Very Short Answer, Short "
    "Answer, Sentence Transformation, Error Correction; descriptive: Long "
    "Answer, Case Based Questions, Passage Based Questions, Extract Based "
    "Questions, Composition Writing — never an abbreviation or paraphrase. "
    "cognitive_skill is Remember, Understand, "
    "Apply, Analyse, Evaluate, or Create. difficulty is Less, Moderate, or "
    "High; Bloom and difficulty are independent. marks is a realistic "
    "positive number for the task, grade, and response contract.\n"
    "Return ONLY strict JSON:\n"
    '{"source_qid":"","sheet_kind":"","question_category":"",'
    '"cognitive_skill":"","difficulty":"","marks":1,'
    '"rationale":"evidence-bound reason"}'
)

GENERATED_CELL_POLICY_VERSION = "assessment-generated-cell-2"

GENERATED_CELL_SYSTEM = (
    "You are the Aegis assessment-cell author for ONE GENERATED "
    "pre-learning question. The question was authored for a prerequisite "
    "concept and deliberately carries no tier or difficulty of its own — "
    "this verdict is that later, independent decision. From the complete "
    "question, its answer, its rationale, and the pre-learning concept it "
    "checks, decide the blueprint cell it fulfils as an assessment item: "
    "sheet kind, question category, cognitive skill (Bloom), difficulty, "
    "and marks. Never infer meaning from text length or from how many "
    "questions exist. There is no quota: do not balance or spread "
    "categories, skills, difficulties, or marks. The no-local-fallback "
    "invariant applies; give your evidence-bound verdict rather than "
    "applying a default.\n"
    "sheet_kind is objective, subjective, or descriptive as allowed by the "
    "active assessment profile. question_category must be the CMS's exact "
    "name for the category, from the sheet kind's own list — objective: "
    "Multiple Choice Question, Assertion & Reasons, True/False, Fill in "
    "the Blanks; subjective: Fill in the Blanks, Very Short Answer, Short "
    "Answer, Sentence Transformation, Error Correction; descriptive: Long "
    "Answer, Case Based Questions, Passage Based Questions, Extract Based "
    "Questions, Composition Writing — never an abbreviation or paraphrase. "
    "cognitive_skill is Remember, Understand, "
    "Apply, Analyse, Evaluate, or Create. difficulty is Less, Moderate, or "
    "High; Bloom and difficulty are independent. marks is a realistic "
    "positive number for the task, grade, and response contract.\n"
    "Return ONLY strict JSON:\n"
    '{"pre_question_id":"","sheet_kind":"","question_category":"",'
    '"cognitive_skill":"","difficulty":"","marks":1,'
    '"rationale":"evidence-bound reason"}'
)

GENERATED_CELL_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis assessment-cell "
    "verdict over a GENERATED pre-learning question. Audit the proposed "
    "sheet kind, category, cognitive skill, difficulty, and marks against "
    "the complete question, its answer, its rationale, the pre-learning "
    "concept it checks, the metadata, and the active profile. Never infer "
    "meaning from length or question volume. There is no quota. Do not "
    "revise, gate, retry, or replace the verdict; dissent ships as review "
    "evidence while the recorded verdict stands. State your honest "
    "confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)

CELL_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis assessment-cell "
    "verdict. Audit the proposed sheet kind, category, cognitive skill, "
    "difficulty, and marks against the complete source task, answer evidence, "
    "shared context, alternatives, multipart relationships, assets, metadata, "
    "and active profile. Never infer meaning from length, print position, "
    "neighbours, or question volume. There is no quota. Do not revise, gate, "
    "retry, or replace the verdict; dissent ships as review evidence while the "
    "recorded verdict stands. State your honest confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)


class CellDecisionError(ValueError):
    """The cell-decision input cannot be bound mechanically."""


_PRINT_POSITION_FIELDS = frozenset({
    "bbox",
    "page",
    "page_hint",
    "source_end",
    "source_page",
    "source_paper_number",
    "source_start",
})


def _content_evidence(value: Any) -> Any:
    """Deep-copy source evidence while excluding printer-position metadata."""

    if isinstance(value, Mapping):
        return {
            str(key): _content_evidence(raw)
            for key, raw in value.items()
            if str(key) not in _PRINT_POSITION_FIELDS
        }
    if isinstance(value, list):
        return [_content_evidence(raw) for raw in value]
    if isinstance(value, tuple):
        return [_content_evidence(raw) for raw in value]
    return copy.deepcopy(value)


def _envelope_hash(value: str) -> str:
    envelope_sha = str(value or "").strip()
    if not envelope_sha:
        raise CellDecisionError("assessment cell decisions require an envelope hash")
    return envelope_sha


def _allowed_sheet_kinds(profile: Mapping[str, Any]) -> tuple[str, ...]:
    # The PROFILE answers this, through the one accessor (spec-step8
    # T12/M5, M5b).  It used to ignore its argument and return the module
    # constant, so the payload told the model something the profile denied.
    # A profile that carries no ``sheet_kinds`` falls back to the default,
    # which is byte-identical for ``reference-1`` — zero decisions re-key.
    return assessment_profile.sheet_kinds(profile)


def _profile_payload(profile: Mapping[str, Any]) -> dict[str, Any]:
    appears_in = str(profile.get("appears_in") or "").strip()
    if not appears_in:
        raise CellDecisionError(
            "assessment cell decisions require an explicit appears_in profile value"
        )
    return {
        "name": str(profile.get("name") or ""),
        "allowed_sheet_kinds": list(_allowed_sheet_kinds(profile)),
        "appears_in": appears_in,
    }


def _verdict_checker(
    id_field: str, id_value: str, allowed_sheet_kinds: tuple[str, ...]
) -> kernel.Checker:
    """Mechanics only: identity, required fields, enums, and numeric shape.

    Shared by the source-atom checker (identity ``source_qid``) and the
    generated-question checker (identity ``pre_question_id``); both
    verdicts demand the same judgment fields.
    """

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get(id_field) or "") != id_value:
            defects.append(f"{id_field} must echo {id_value!r}")
        if response.get("sheet_kind") not in allowed_sheet_kinds:
            defects.append(
                "sheet_kind must be one of "
                f"{allowed_sheet_kinds} (got {response.get('sheet_kind')!r})"
            )
        category = response.get("question_category")
        allowed_categories = bi.QUESTION_CATEGORIES.get(
            str(response.get("sheet_kind") or ""), []
        )
        if not isinstance(category, str) or not str(category or "").strip():
            defects.append("question_category must be a non-empty string")
        elif allowed_categories and category not in allowed_categories:
            # The CMS's own per-sheet vocabulary (the owner's review found
            # "MCQ" / "Multiple Choice" / "Multiple Choice Question" on
            # sibling rows). An enum-membership gate is schema mechanics,
            # the same shape as the cognitive-skill and difficulty gates
            # beside it; naming the list lets corrections converge.
            defects.append(
                "question_category must be one of "
                f"{tuple(allowed_categories)} for sheet_kind "
                f"{response.get('sheet_kind')!r} (got {category!r})"
            )
        if response.get("cognitive_skill") not in bi.COGNITIVE_SKILLS:
            defects.append(
                "cognitive_skill must be one of "
                f"{bi.COGNITIVE_SKILLS} "
                f"(got {response.get('cognitive_skill')!r})"
            )
        if response.get("difficulty") not in bi.DIFFICULTY_LEVELS:
            defects.append(
                "difficulty must be one of "
                f"{bi.DIFFICULTY_LEVELS} "
                f"(got {response.get('difficulty')!r})"
            )
        marks = response.get("marks")
        if isinstance(marks, bool):
            defects.append("marks must be numeric")
        else:
            try:
                numeric_marks = float(marks)
                if not math.isfinite(numeric_marks) or numeric_marks <= 0:
                    defects.append("marks must be finite and positive")
            except (TypeError, ValueError):
                defects.append("marks must be numeric")
        if not isinstance(response.get("rationale"), str) or not str(
            response.get("rationale") or ""
        ).strip():
            defects.append("response has no rationale")
        return defects

    return check


def _cell_checker(
    source_qid: str, allowed_sheet_kinds: tuple[str, ...]
) -> kernel.Checker:
    return _verdict_checker("source_qid", source_qid, allowed_sheet_kinds)


def _generated_cell_checker(
    pre_question_id: str, allowed_sheet_kinds: tuple[str, ...]
) -> kernel.Checker:
    return _verdict_checker(
        "pre_question_id", pre_question_id, allowed_sheet_kinds
    )


def _review_flags(decision: Mapping[str, Any]) -> list[str]:
    return [
        str(flag)
        for flag in decision.get("review_flags") or []
        if str(flag).strip()
    ]


def _decision_authority(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Timestamp-free authority suitable for an immutable release payload."""

    authority = {
        "decision_key": str(decision.get("key") or ""),
        "policy_version": str(decision.get("policy_version") or ""),
        "review_flags": _review_flags(decision),
    }
    if bool(decision.get("fixer")):
        authority["fixer"] = True
    return authority


def _live_cell(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        CELL_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_mapping",
    )


def _live_cell_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        CELL_CRITIC_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_validation",
    )


def _live_generated_cell(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        GENERATED_CELL_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_mapping",
    )


def _live_generated_cell_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        GENERATED_CELL_CRITIC_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_validation",
    )


def _live_authorities(
    provider: kernel.Provider | None,
    critic: kernel.Critic | None,
    fixer: kernel.Provider | None,
) -> tuple[kernel.Provider, kernel.Critic | None, kernel.Provider | None]:
    if provider is not None:
        return provider, critic, fixer

    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod

    envelope_mod.require_live_api()
    return _live_cell, critic or _live_cell_critic, fixer or fixer_mod.live_fixer


def _live_generated_authorities(
    provider: kernel.Provider | None,
    critic: kernel.Critic | None,
    fixer: kernel.Provider | None,
) -> tuple[kernel.Provider, kernel.Critic | None, kernel.Provider | None]:
    if provider is not None:
        return provider, critic, fixer

    from .phase3 import envelope as envelope_mod
    from .phase3 import fixer as fixer_mod

    envelope_mod.require_live_api()
    return (
        _live_generated_cell,
        critic or _live_generated_cell_critic,
        fixer or fixer_mod.live_fixer,
    )


def decide_cells(
    atoms: list[Mapping],
    *,
    meta: Mapping,
    profile: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> list[dict]:
    """Record one assessment-cell verdict per atom, preserving input order."""

    envelope_sha = _envelope_hash(envelope_sha256)
    if not isinstance(meta, Mapping):
        raise CellDecisionError("assessment cell metadata must be an object")
    if not isinstance(profile, Mapping):
        raise CellDecisionError("assessment cell profile must be an object")
    profile_evidence = _profile_payload(profile)
    allowed_sheet_kinds = tuple(profile_evidence["allowed_sheet_kinds"])

    prepared: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for position, atom in enumerate(atoms, start=1):
        if not isinstance(atom, Mapping):
            raise CellDecisionError(f"assessment source atom {position} is not an object")
        source_qid = str(atom.get("source_qid") or "").strip()
        if not source_qid:
            raise CellDecisionError(
                f"assessment source atom {position} has no source_qid"
            )
        if source_qid in seen:
            raise CellDecisionError(
                f"assessment source atoms repeat source_qid {source_qid!r}"
            )
        seen.add(source_qid)
        prepared.append((source_qid, _content_evidence(atom)))

    if not prepared:
        return []

    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    store = store or kernel.DecisionStore()

    def decide_one(unit: tuple[str, dict[str, Any]]) -> dict:
        source_qid, source_atom = unit
        payload = {
            "stage": "assessment.cell",
            "rules": CELL_SYSTEM,
            "metadata": copy.deepcopy(dict(meta)),
            "profile": copy.deepcopy(profile_evidence),
            "source_atom": source_atom,
        }
        decision = kernel.decide(
            kind="assessment.cell",
            unit_id=source_qid,
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_cell_checker(source_qid, allowed_sheet_kinds),
            critic=critic,
            store=store,
            policy_version=CELL_POLICY_VERSION,
            fixer=fixer,
        )
        response = copy.deepcopy(dict(decision["response"]))
        decision_key = str(decision.get("key") or "")
        return {
            "cell_id": "CELL-" + decision_key[:16],
            "sheet_kind": str(response.get("sheet_kind") or ""),
            "question_category": str(response.get("question_category") or ""),
            "cognitive_skill": str(response.get("cognitive_skill") or ""),
            "difficulty": str(response.get("difficulty") or ""),
            "marks": float(response["marks"]),
            "count": 1,
            "appears_in": [profile_evidence["appears_in"]],
            "concept_id": None,
            "source_policy": "reuse",
            "accepted_source_qids": [source_qid],
            "rationale": str(response.get("rationale") or ""),
            "flags": _review_flags(decision),
            "authority": _decision_authority(decision),
        }

    rows = kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
    )
    seen_cell_ids: set[str] = set()
    duplicate_cell_ids: set[str] = set()
    for row in rows:
        cell_id = str(row.get("cell_id") or "")
        if cell_id in seen_cell_ids:
            duplicate_cell_ids.add(cell_id)
        seen_cell_ids.add(cell_id)
    if duplicate_cell_ids:
        raise CellDecisionError(
            "assessment cell decisions emitted duplicate cell_id values: "
            f"{sorted(duplicate_cell_ids)!r}"
        )
    return rows


def concept_keys_by_pre_id(
    concept_records_by_key: Mapping[str, Mapping] | None,
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """The mechanical PRC → concept_key translation, with ambiguity named.

    A staged Pre row carries the ``pre_concept_id`` its questions were
    authored to (release_snapshot row projection), and the minted release
    ``concept_key`` is what routing understands. Both ids were assigned by
    this pipeline; the join decides nothing.

    Returns ``(mapping, ambiguous)``: ``mapping`` holds every
    ``pre_concept_id`` carried by exactly ONE snapshot row; ``ambiguous``
    names, per id, the concept keys of every row claiming it. An ambiguous
    id never routes first-wins — its questions take the blocked path (the
    release runner) or a named ``CellDecisionError`` (a direct
    ``decide_generated_cells`` caller). One shared accessor so the runner's
    partition and this module's join can never drift.
    """

    claims: dict[str, list[str]] = {}
    for concept_key, row in dict(concept_records_by_key or {}).items():
        pre_id = str((row or {}).get("pre_concept_id") or "").strip()
        if pre_id:
            claims.setdefault(pre_id, []).append(str(concept_key))
    mapping = {
        pre_id: keys[0]
        for pre_id, keys in claims.items()
        if len(keys) == 1
    }
    ambiguous = {
        pre_id: tuple(keys)
        for pre_id, keys in claims.items()
        if len(keys) > 1
    }
    return mapping, ambiguous


def decide_generated_cells(
    questions: list[Mapping],
    *,
    meta: Mapping,
    profile: Mapping,
    envelope_sha256: str,
    concept_records_by_key: Mapping[str, Any] | None = None,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> list[dict]:
    """One recorded cell verdict per GENERATED pre-learning question.

    The generated lane's parallel of ``decide_cells``, deliberately its
    parallel rather than a widening (the same relationship
    ``_bind_generated_cells`` has to ``_bind_explicit_cells``): the unit
    identity is the minted ``pre_question_id``, the evidence is the
    authored question plus the pre-learning concept it checks, and the
    resulting cell carries ``concept_key`` = the question's own
    ``pre_concept_id`` so routing stays mechanical and free. The judgment
    fields — sheet kind, category, cognitive skill, difficulty, marks —
    are the model's verdict: a generated question is authored WITHOUT a
    tier or difficulty by design ("its level is decided later and
    independently", prequestions.py), and this is that later, independent
    decision. Output 02 therefore needs no externally supplied blueprint;
    an explicit ``blueprint_cells`` argument remains the zero-spend path.
    """

    envelope_sha = _envelope_hash(envelope_sha256)
    if not isinstance(meta, Mapping):
        raise CellDecisionError("assessment cell metadata must be an object")
    if not isinstance(profile, Mapping):
        raise CellDecisionError("assessment cell profile must be an object")
    profile_evidence = _profile_payload(profile)
    allowed_sheet_kinds = tuple(profile_evidence["allowed_sheet_kinds"])
    concepts = dict(concept_records_by_key or {})
    concept_key_by_pre_id, ambiguous_pre_ids = concept_keys_by_pre_id(
        concepts
    )

    prepared: list[tuple[str, str, Mapping]] = []
    seen: set[str] = set()
    for position, question in enumerate(questions, start=1):
        if not isinstance(question, Mapping):
            raise CellDecisionError(
                f"generated question {position} is not an object"
            )
        pre_question_id = str(question.get("pre_question_id") or "").strip()
        if not pre_question_id:
            raise CellDecisionError(
                f"generated question {position} has no pre_question_id"
            )
        if pre_question_id in seen:
            raise CellDecisionError(
                "generated questions repeat pre_question_id "
                f"{pre_question_id!r}"
            )
        seen.add(pre_question_id)
        pre_concept_id = str(question.get("pre_concept_id") or "").strip()
        concept_key = concept_key_by_pre_id.get(pre_concept_id, "")
        if not concept_key:
            # Fail closed with the reason named: the question's home row
            # is not in the carried snapshot (skipped as defective, or the
            # payload predates the pre_concept_id projection), or two rows
            # claim its id, so its cell cannot route mechanically and
            # inventing a home would be a silent mis-placement. The
            # release runner partitions such questions onto the BLOCKED
            # path before calling here; a direct caller gets the refusal.
            if pre_concept_id in ambiguous_pre_ids:
                claimants = ", ".join(ambiguous_pre_ids[pre_concept_id])
                raise CellDecisionError(
                    f"generated question {pre_question_id!r} names "
                    f"pre-concept {pre_concept_id!r}, which "
                    f"{len(ambiguous_pre_ids[pre_concept_id])} snapshot "
                    f"rows claim ({claimants}); the routing is ambiguous "
                    "and is never resolved first-wins"
                )
            raise CellDecisionError(
                f"generated question {pre_question_id!r} names pre-concept "
                f"{pre_concept_id!r}, which the staged Pre release snapshot "
                "does not carry (row skipped as defective, or a stale "
                "staged payload without the pre_concept_id projection); "
                "its assessment cell cannot route mechanically"
            )
        prepared.append((pre_question_id, concept_key, question))
    if not prepared:
        return []

    provider, critic, fixer = _live_generated_authorities(
        provider, critic, fixer
    )
    store = store or kernel.DecisionStore()

    def decide_one(unit: tuple[str, str, Mapping]) -> dict:
        pre_question_id, concept_key, question = unit
        pre_concept_id = str(question.get("pre_concept_id") or "")
        payload = {
            "stage": "assessment.generated_cell",
            "rules": GENERATED_CELL_SYSTEM,
            "metadata": copy.deepcopy(dict(meta)),
            "profile": copy.deepcopy(profile_evidence),
            "generated_question": {
                "pre_question_id": pre_question_id,
                "pre_concept_id": pre_concept_id,
                "question_id": str(question.get("question_id") or ""),
                "question_text": str(question.get("question_text") or ""),
                "answer": str(question.get("answer") or ""),
                "rationale": str(question.get("rationale") or ""),
            },
            "pre_concept": _content_evidence(
                dict(concepts.get(concept_key) or {})
            ),
        }
        decision = kernel.decide(
            kind="assessment.generated_cell",
            unit_id=pre_question_id,
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_generated_cell_checker(
                pre_question_id, allowed_sheet_kinds
            ),
            critic=critic,
            store=store,
            policy_version=GENERATED_CELL_POLICY_VERSION,
            fixer=fixer,
        )
        response = copy.deepcopy(dict(decision["response"]))
        # ``_bind_generated_cells`` owns the mechanical identity fields
        # (cell_id, count, appears_in, accepted_source_qids, the bound
        # question) and validates everything again; this record carries
        # the judgment fields plus the mechanical routing bind and the
        # decision audit.
        return {
            "sheet_kind": str(response.get("sheet_kind") or ""),
            "question_category": str(response.get("question_category") or ""),
            "cognitive_skill": str(response.get("cognitive_skill") or ""),
            "difficulty": str(response.get("difficulty") or ""),
            "marks": float(response["marks"]),
            "count": 1,
            "concept_key": concept_key,
            "accepted_source_qids": [],
            "rationale": str(response.get("rationale") or ""),
            "flags": _review_flags(decision),
            "authority": _decision_authority(decision),
        }

    return kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
        labels=[f"Cell · {pid}" for pid, _key, _question in prepared],
        announce="Generated-question cell verdicts",
    )
