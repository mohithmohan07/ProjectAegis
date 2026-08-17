"""Recorded question, answer, and rubric materialization for Output 02.

Each source-atom/blueprint-cell obligation is authored once through the
Phase-3 decision kernel. Mechanical defects receive bounded correction, an
independent critic is advisory, and The Fixer handles a structurally blocked
response. The decision store is the sole replay authority.

This pass keeps the existing workbook wire fields intact while Step 6 moves
Open/Specific and marking authorship into their own later decisions. It does
not infer either field locally: in particular, Objective is not a deterministic
alias for Specific.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping

from .. import config
from . import assessment_release as rel
from . import katex_rules
from .phase3 import kernel

MATERIALIZE_POLICY_VERSION = "assessment-materialize-1"

# Workbook capacities are positional mechanics, not content judgments.
MAX_OBJECTIVE_OPTIONS = 6
MAX_DESCRIPTIVE_ANSWERS = 10
MAX_SUBQUESTIONS = 15
MAX_SUBQUESTION_KEYWORDS = 6

MATERIALIZE_SYSTEM = (
    "You are the Aegis assessment materialization author. Materialize ONE "
    "complete assessment item from the supplied source atom and blueprint "
    "cell. The cell's sheet kind, category, cognitive skill, difficulty, and "
    "marks are fixed and are not yours to change. There is no quota.\n"
    "For a standalone or exercise source, write a clear, complete, "
    "self-contained item while preserving its meaning and answer space. For "
    "an activity, checkpoint, or experiment, preserve its procedure, "
    "materials, sequence, context, and required assets. With no source atom, "
    "stay strictly inside the supplied curricular evidence. Never invent "
    "facts, values, or constraints.\n"
    "For Objective cells, return no more than six canonical options with "
    "exactly one correct marker, its weightage equal to the cell marks, and "
    "wrong options weighted zero. For Descriptive cells, return a complete "
    "display answer, answer/rubric blocks, and any source-owned subquestions "
    "with their marks and keyword slots. Preserve the exact marking "
    "arithmetic.\n"
    "Return Open or Specific from the complete answer space with a concise "
    "reason. There is no default based on sheet kind, command word, subject, "
    "or local label; an Objective item may be Open or Specific.\n"
    "Use [Katex]...[/Katex] for rich mathematics and meaningful, neutral alt "
    "text for every image. Do not leak an answer in the question, options, or "
    "alt text.\n"
    "Return ONLY strict JSON:\n"
    '{"candidate_id":"","question":"","answer_restriction":'
    '"Open|Specific","restriction_reason":"","display_answer":"",'
    '"answers":[],"sub_questions":[],"answer_explanation":"",'
    '"requires_visual":false,"rationale":"evidence-bound reason"}'
)

MATERIALIZE_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis assessment "
    "materialization decision. Audit the exact proposed item against the "
    "complete source atom, curricular evidence, assets, and blueprint cell: "
    "source fidelity, answer correctness, answer-space preservation, "
    "clarity and grade fit, rubric quality, marking, visual dependence, and "
    "answer leakage. Open or Specific has no sheet-kind default. Do not "
    "rewrite, retry, or gate the proposal. Your dissent ships for review and "
    "the authored decision stands. State your honest confidence. There is no "
    "quota.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)

_AUDIT_FIELD = "_aegis_assessment_materialization"


class MaterializationError(ValueError):
    """The materialization obligation cannot be bound mechanically."""


def _to_float(value: Any) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _candidate_id(atom: Mapping | None, cell: Mapping) -> str:
    seed = rel.canonical_json([
        (atom or {}).get("source_qid"), cell.get("cell_id"),
    ])
    return "CAND-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _envelope_hash(value: str) -> str:
    envelope_sha = str(value or "").strip()
    if not envelope_sha:
        raise MaterializationError(
            "assessment materialization requires an envelope hash"
        )
    return envelope_sha


def _validate_obligation(
    atom: Mapping | None, cell: Mapping, meta: Mapping,
) -> str:
    if atom is not None and not isinstance(atom, Mapping):
        raise MaterializationError("source atom is not an object")
    if not isinstance(cell, Mapping):
        raise MaterializationError("blueprint cell is not an object")
    if not isinstance(meta, Mapping):
        raise MaterializationError("materialization metadata is not an object")
    cell_id = str(cell.get("cell_id") or "").strip()
    if not cell_id:
        raise MaterializationError("blueprint cell has no cell_id")
    if str(cell.get("sheet_kind") or "") not in rel.SHEET_KINDS:
        raise MaterializationError(
            f"blueprint cell {cell_id!r} has an unknown sheet_kind"
        )
    marks = _to_float(cell.get("marks"))
    if marks is None or marks <= 0:
        raise MaterializationError(
            f"blueprint cell {cell_id!r} has non-positive or non-numeric marks"
        )
    if atom is not None and not str(atom.get("source_qid") or "").strip():
        raise MaterializationError("source atom has no source_qid")
    return _candidate_id(atom, cell)


def _learner_rich_text(proposal: Mapping) -> list[str]:
    values = [
        proposal.get("question"),
        proposal.get("restriction_reason"),
        proposal.get("display_answer"),
        proposal.get("answer_explanation"),
    ]
    raw_answers = proposal.get("answers")
    for answer in raw_answers if isinstance(raw_answers, list) else []:
        if isinstance(answer, Mapping):
            values.extend((answer.get("answer_content"), answer.get("answer")))
    raw_subquestions = proposal.get("sub_questions")
    for subquestion in (
        raw_subquestions if isinstance(raw_subquestions, list) else []
    ):
        if not isinstance(subquestion, Mapping):
            continue
        values.append(subquestion.get("text"))
        raw_keywords = subquestion.get("keywords")
        for keyword in raw_keywords if isinstance(raw_keywords, list) else []:
            if isinstance(keyword, Mapping):
                values.append(keyword.get("keyword"))
    return [str(value or "") for value in values]


def _rich_text_defects(proposal: Mapping) -> list[str]:
    blob = "\n".join(_learner_rich_text(proposal))
    return [
        f"rich-text: {code}" for code in katex_rules.rich_text_issues(blob)
    ]


def _proposal_defects(
    proposal: Mapping[str, Any], cell: Mapping, candidate_id: str,
) -> list[str]:
    """Validate response mechanics only; semantic quality belongs to models."""

    if not isinstance(proposal, Mapping):
        return ["response is not an object"]
    defects: list[str] = []
    if str(proposal.get("candidate_id") or "") != candidate_id:
        defects.append(f"candidate_id must echo {candidate_id!r}")
    if not isinstance(proposal.get("question"), str) or not str(
        proposal.get("question") or ""
    ).strip():
        defects.append("question must be a non-empty string")
    restriction = proposal.get("answer_restriction")
    if restriction not in rel.ANSWER_RESTRICTIONS:
        defects.append(
            f"answer_restriction must be one of {rel.ANSWER_RESTRICTIONS} "
            f"(got {restriction!r}); never defaulted"
        )
    for field in (
        "restriction_reason", "display_answer", "answer_explanation", "rationale",
    ):
        if not isinstance(proposal.get(field), str):
            defects.append(f"{field} must be a string")
    if not str(proposal.get("rationale") or "").strip():
        defects.append("response has no rationale")
    if not isinstance(proposal.get("requires_visual"), bool):
        defects.append("requires_visual must be boolean")

    raw_answers = proposal.get("answers")
    if not isinstance(raw_answers, list):
        defects.append("answers must be an array")
        answers: list[Mapping] = []
    else:
        answers = []
        for position, answer in enumerate(raw_answers, start=1):
            if not isinstance(answer, Mapping):
                defects.append(f"answer {position} is not an object")
            else:
                answers.append(answer)

    raw_subquestions = proposal.get("sub_questions")
    if not isinstance(raw_subquestions, list):
        defects.append("sub_questions must be an array")
        subquestions: list[Mapping] = []
    else:
        subquestions = []
        for position, subquestion in enumerate(raw_subquestions, start=1):
            if not isinstance(subquestion, Mapping):
                defects.append(f"subquestion {position} is not an object")
            else:
                subquestions.append(subquestion)

    kind = str(cell.get("sheet_kind") or "")
    marks = _to_float(cell.get("marks")) or 0.0
    if kind == "objective":
        if not 1 <= len(answers) <= MAX_OBJECTIVE_OPTIONS:
            defects.append(
                f"objective needs 1..{MAX_OBJECTIVE_OPTIONS} options "
                f"(got {len(answers)})"
            )
        correct = [
            answer for answer in answers
            if rel.is_correct_option(answer.get("correct_answer"))
        ]
        if len(correct) != 1:
            defects.append(
                f"exactly one correct option required (got {len(correct)})"
            )
        else:
            weight = _to_float(correct[0].get("answer_weightage"))
            if weight is None or abs(weight - marks) > 0.01:
                defects.append(
                    "correct option weightage "
                    f"{correct[0].get('answer_weightage')!r} != marks {marks:g}"
                )
        for answer in answers:
            if answer in correct:
                continue
            raw_weight = answer.get("answer_weightage")
            if str(raw_weight or "").strip() == "":
                continue  # blank workbook slots are mechanically zero
            weight = _to_float(raw_weight)
            if weight is None or abs(weight) > 0.01:
                defects.append("wrong option weightage must be zero")
        contents = [
            str(answer.get("answer_content") or "").strip()
            for answer in answers
        ]
        populated = [content for content in contents if content]
        if len(set(populated)) != len(populated):
            defects.append("duplicate option text")
        if not str(proposal.get("answer_explanation") or "").strip():
            defects.append("missing answer explanation")
    elif kind == "descriptive":
        if not str(proposal.get("display_answer") or "").strip():
            defects.append("missing display answer")
        if len(answers) > MAX_DESCRIPTIVE_ANSWERS:
            defects.append(
                f"more than {MAX_DESCRIPTIVE_ANSWERS} answer blocks"
            )
        weights: list[float] = []
        invalid_weights = False
        for position, answer in enumerate(answers, start=1):
            raw_weight = answer.get("answer_weightage")
            if str(raw_weight or "").strip() == "":
                continue
            weight = _to_float(raw_weight)
            if weight is None:
                defects.append(
                    f"answer {position} weightage must be finite and numeric"
                )
                invalid_weights = True
            else:
                weights.append(weight)
        if weights and not invalid_weights and marks:
            total = sum(weights)
            if abs(total - marks) > 0.01:
                defects.append(
                    f"answer weightage sum {total:g} != marks {marks:g}"
                )
        if len(subquestions) > MAX_SUBQUESTIONS:
            defects.append(f"more than {MAX_SUBQUESTIONS} subquestions")
        for position, subquestion in enumerate(subquestions, start=1):
            keywords = subquestion.get("keywords")
            if not isinstance(keywords, list):
                defects.append(
                    f"subquestion {position} keywords must be an array"
                )
                continue
            if len(keywords) > MAX_SUBQUESTION_KEYWORDS:
                defects.append(
                    f"subquestion with more than "
                    f"{MAX_SUBQUESTION_KEYWORDS} keyword slots"
                )
            for keyword_position, keyword in enumerate(keywords, start=1):
                if not isinstance(keyword, Mapping):
                    defects.append(
                        f"subquestion {position} keyword {keyword_position} "
                        "is not an object"
                    )
        if subquestions and marks:
            sub_marks = [
                _to_float(subquestion.get("marks"))
                for subquestion in subquestions
            ]
            for position, sub_mark in enumerate(sub_marks, start=1):
                if sub_mark is None:
                    defects.append(
                        f"subquestion {position} marks must be finite and numeric"
                    )
            if None not in sub_marks:
                total = sum(sub_marks)
                if abs(total - marks) > 0.01:
                    defects.append(
                        f"subquestion marks sum {total:g} != parent marks "
                        f"{marks:g}"
                    )
    defects.extend(_rich_text_defects(proposal))
    return defects


def _checker(cell: Mapping, candidate_id: str) -> kernel.Checker:
    def check(response: Mapping[str, Any]) -> list[str]:
        return _proposal_defects(response, cell, candidate_id)

    return check


def _live_materialize(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        MATERIALIZE_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_mapping",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        MATERIALIZE_CRITIC_SYSTEM,
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
    return _live_materialize, critic or _live_critic, fixer or fixer_mod.live_fixer


def _review_flags(decision: Mapping[str, Any]) -> list[str]:
    return [
        str(flag) for flag in decision.get("review_flags") or []
        if str(flag).strip()
    ]


def _stable_authority(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision_key": str(decision.get("key") or ""),
        "policy_version": str(decision.get("policy_version") or ""),
        "review_flags": _review_flags(decision),
        "fixer": bool(decision.get("fixer")),
    }


def _assemble(
    response: Mapping,
    atom: Mapping | None,
    cell: Mapping,
    *,
    candidate_id: str,
    decision: Mapping[str, Any],
) -> dict:
    question = str(response.get("question") or "")
    source = dict(atom or {})
    review_flags = _review_flags(decision)
    authority = _stable_authority(decision)
    audit = {
        "rationale": str(response.get("rationale") or ""),
        "flags": review_flags,
        "authority": authority,
    }
    return {
        "candidate_id": candidate_id,
        "source_atom_ids": (
            [str(atom.get("source_qid"))]
            if atom and atom.get("source_qid") else []
        ),
        "blueprint_cell_id": str(cell.get("cell_id") or ""),
        "question": question,
        "question_text": question,
        "sheet_kind": str(cell.get("sheet_kind") or ""),
        "question_category": str(cell.get("question_category") or ""),
        "cognitive_skill": str(cell.get("cognitive_skill") or ""),
        "difficulty": str(cell.get("difficulty") or ""),
        "marks": cell.get("marks"),
        "appears_in": list(cell.get("appears_in") or []),
        "question_appears_in": ", ".join(
            str(value) for value in cell.get("appears_in") or []
        ),
        "answer_restriction": str(
            response.get("answer_restriction") or ""
        ),
        "restriction_reason": str(response.get("restriction_reason") or ""),
        "display_answer": str(response.get("display_answer") or ""),
        "answers": copy.deepcopy(list(response.get("answers") or [])),
        "sub_questions": copy.deepcopy(
            list(response.get("sub_questions") or [])
        ),
        "answer_explanation": str(
            response.get("answer_explanation") or ""
        ),
        "requires_visual": bool(response.get("requires_visual")),
        "assets": copy.deepcopy(list(source.get("assets") or [])),
        "image_manifest": copy.deepcopy(source.get("image_manifest") or []),
        "image_urls": copy.deepcopy(source.get("image_urls") or []),
        "tables": copy.deepcopy(source.get("tables") or []),
        "content_objects": copy.deepcopy(
            source.get("content_objects") or {}
        ),
        "source_qid": str(source.get("source_qid") or ""),
        "source_document_hash": str(
            source.get("source_document_hash") or ""
        ),
        "source_kind": str(source.get("source_kind") or ""),
        "source_evidence": str(source.get("raw_text") or ""),
        "shared_context": copy.deepcopy(source.get("shared_context")),
        "source_context": copy.deepcopy(
            source.get("source_context") or {
                "raw_text": source.get("raw_text"),
                "normalized_public_text": source.get(
                    "normalized_public_text"
                ),
                "source_answer": source.get("source_answer"),
                "shared_context": source.get("shared_context"),
                "parent_qid": source.get("parent_qid"),
                "subpart": source.get("subpart"),
                "alternative_set_id": source.get("alternative_set_id"),
            }
        ),
        "route_evidence": copy.deepcopy(source.get("route_evidence") or {}),
        "assessment_gist": copy.deepcopy(source.get("assessment_gist")),
        "assessment_eligibility": (
            "flagged" if review_flags else "accepted"
        ),
        "flags": list(review_flags),
        "authority": authority,
        _AUDIT_FIELD: audit,
    }


def _decision_payload(
    atom: Mapping | None,
    cell: Mapping,
    *,
    candidate_id: str,
    meta: Mapping,
    context: Any,
) -> dict[str, Any]:
    return {
        "stage": "assessment.materialize",
        "rules": MATERIALIZE_SYSTEM,
        "candidate_id": candidate_id,
        "metadata": copy.deepcopy(dict(meta)),
        "source_atom": copy.deepcopy(dict(atom)) if atom is not None else None,
        "blueprint_cell": copy.deepcopy(dict(cell)),
        "curricular_evidence": copy.deepcopy(context),
    }


def _materialize_prepared(
    atom: Mapping | None,
    cell: Mapping,
    *,
    candidate_id: str,
    meta: Mapping,
    context: Any,
    envelope_sha256: str,
    provider: kernel.Provider,
    critic: kernel.Critic | None,
    store: kernel.DecisionStore,
    fixer: kernel.Provider | None,
) -> dict:
    payload = _decision_payload(
        atom, cell, candidate_id=candidate_id, meta=meta, context=context,
    )
    decision = kernel.decide(
        kind="assessment.materialize",
        unit_id=candidate_id,
        envelope_sha256=envelope_sha256,
        payload=payload,
        provider=provider,
        checker=_checker(cell, candidate_id),
        critic=critic,
        store=store,
        policy_version=MATERIALIZE_POLICY_VERSION,
        fixer=fixer,
    )
    return _assemble(
        decision["response"], atom, cell,
        candidate_id=candidate_id, decision=decision,
    )


def materialize_candidate(
    atom: Mapping | None,
    cell: Mapping,
    *,
    meta: Mapping,
    context: Any = "",
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict:
    """Materialize one obligation through a content-addressed decision."""

    candidate_id = _validate_obligation(atom, cell, meta)
    envelope_sha = _envelope_hash(envelope_sha256)
    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    return _materialize_prepared(
        atom,
        cell,
        candidate_id=candidate_id,
        meta=meta,
        context=context,
        envelope_sha256=envelope_sha,
        provider=provider,
        critic=critic,
        store=store or kernel.DecisionStore(),
        fixer=fixer,
    )


def materialize_candidates(
    pairs: list[tuple[Mapping | None, Mapping]],
    *,
    meta: Mapping,
    context: Any = "",
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> dict:
    """Materialize every obligation in order with exact-once accounting."""

    if not isinstance(meta, Mapping):
        raise MaterializationError("materialization metadata is not an object")
    envelope_sha = _envelope_hash(envelope_sha256)
    prepared: list[tuple[Mapping | None, Mapping, str]] = []
    seen: set[str] = set()
    for position, pair in enumerate(pairs, start=1):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise MaterializationError(
                f"materialization pair {position} is not an atom/cell pair"
            )
        atom, cell = pair
        candidate_id = _validate_obligation(atom, cell, meta)
        if candidate_id in seen:
            raise MaterializationError(
                "materialization obligations repeat candidate_id "
                f"{candidate_id!r}"
            )
        seen.add(candidate_id)
        prepared.append((atom, cell, candidate_id))
    if not prepared:
        return {
            "candidates": [], "accepted": 0, "flagged": 0,
            "zero_loss": rel.zero_loss_report([], [], []),
        }

    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    store = store or kernel.DecisionStore()

    def decide_one(unit: tuple[Mapping | None, Mapping, str]) -> dict:
        atom, cell, candidate_id = unit
        return _materialize_prepared(
            atom,
            cell,
            candidate_id=candidate_id,
            meta=meta,
            context=context,
            envelope_sha256=envelope_sha,
            provider=provider,
            critic=critic,
            store=store,
            fixer=fixer,
        )

    candidates = kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
    )
    returned_ids = [
        str(candidate.get("candidate_id") or "") for candidate in candidates
    ]
    if len(returned_ids) != len(set(returned_ids)):
        raise MaterializationError(
            "materialization returned a duplicate candidate_id"
        )
    accepted_ids = [
        candidate["candidate_id"] for candidate in candidates
        if candidate["assessment_eligibility"] == "accepted"
    ]
    flagged_ids = [
        candidate["candidate_id"] for candidate in candidates
        if candidate["assessment_eligibility"] != "accepted"
    ]
    report = rel.zero_loss_report(
        [candidate_id for _atom, _cell, candidate_id in prepared],
        accepted_ids,
        flagged_ids,
    )
    if (
        not report["holds"]
        or len(returned_ids) != len(prepared)
        or returned_ids != [unit[2] for unit in prepared]
    ):
        raise MaterializationError(
            "materialization broke exact ordered coverage: "
            f"missing={report['missing']} unexpected={report['unexpected']}"
        )
    return {
        "candidates": candidates,
        "accepted": len(accepted_ids),
        "flagged": len(flagged_ids),
        "zero_loss": report,
    }
