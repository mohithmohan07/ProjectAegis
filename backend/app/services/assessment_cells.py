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


CELL_POLICY_VERSION = "assessment-cell-1"

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
    "active assessment profile. cognitive_skill is Remember, Understand, "
    "Apply, Analyse, Evaluate, or Create. difficulty is Less, Moderate, or "
    "High; Bloom and difficulty are independent. marks is a realistic "
    "positive number for the task, grade, and response contract.\n"
    "Return ONLY strict JSON:\n"
    '{"source_qid":"","sheet_kind":"","question_category":"",'
    '"cognitive_skill":"","difficulty":"","marks":1,'
    '"rationale":"evidence-bound reason"}'
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


def _cell_checker(
    source_qid: str, allowed_sheet_kinds: tuple[str, ...]
) -> kernel.Checker:
    """Mechanics only: identity, required fields, enums, and numeric shape."""

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get("source_qid") or "") != source_qid:
            defects.append(f"source_qid must echo {source_qid!r}")
        if response.get("sheet_kind") not in allowed_sheet_kinds:
            defects.append(
                "sheet_kind must be one of "
                f"{allowed_sheet_kinds} (got {response.get('sheet_kind')!r})"
            )
        if not isinstance(response.get("question_category"), str) or not str(
            response.get("question_category") or ""
        ).strip():
            defects.append("question_category must be a non-empty string")
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
