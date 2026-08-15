"""Question, answer, and rubric authority (MES spec §6 Stages 4–6, §17 PR 3).

A primary API author materializes each blueprint obligation from its source
atom (or from curricular evidence for blueprint-generated cells); an
independent read-only API critic accepts or rejects the exact proposal,
bound to it by SHA-256; deterministic code validates mechanics only —
option counts, mark arithmetic, rich-text syntax, enum presence.

Fail-closed all the way (spec §14): a proposal the critic never accepts
within the bounded repair budget ships as the best evidence-bound candidate,
explicitly flagged — never dropped, never silently accepted, never replaced
by a deterministic fallback. A provider failure preserves the source
evidence as a flagged candidate; an exception is never turned into
acceptance. The run never pauses for a human.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Callable, Mapping

from .. import config
from . import assessment_release as rel
from . import katex_rules

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3

# Objective sheet capacity: six option blocks (spec §6 Stage 6, §11).
MAX_OBJECTIVE_OPTIONS = 6
# Descriptive sheet capacity: ten answer blocks, fifteen subquestions with
# six keyword/rubric slots each.
MAX_DESCRIPTIVE_ANSWERS = 10
MAX_SUBQUESTIONS = 15
MAX_SUBQUESTION_KEYWORDS = 6

AUTHOR_SYSTEM = (
    "You are the Aegis assessment author. Materialize ONE assessment "
    "question that fulfills the supplied blueprint cell exactly — its sheet "
    "kind, category, cognitive skill, difficulty, and marks are fixed by "
    "the blueprint and are not yours to change.\n"
    "Transformation policy by source kind:\n"
    "- standalone/exercise source: reauthor into a clear, complete, "
    "self-contained assessment item while preserving the source's meaning "
    "and answer space. Never copy awkward wording merely because OCR "
    "succeeded.\n"
    "- checkpoint/activity/experiment source: preserve procedure, "
    "materials, sequence, context, and images unless a specific useful "
    "edit is justified. Never flatten an activity into an unrelated test "
    "question.\n"
    "- no source atom (blueprint-generated): author strictly inside the "
    "supplied curricular evidence; never invent facts, values, or "
    "constraints the evidence does not contain.\n"
    "Answers: for objective cells return up to six canonical options with "
    "exactly one correct option whose weightage equals the marks and wrong "
    "options weighted zero, plausible same-family distractors, no "
    "overlapping choices, and no answer leakage in the question, options, "
    "or image alt text. For descriptive cells return a complete "
    "display_answer, mark-wise answer blocks, subquestions where the "
    "source has parts (their marks summing to the parent marks), and "
    "keyword/rubric slots.\n"
    "Propose answer_restriction — Specific for a closed token, value, "
    "option, or uniquely specified result; Open when multiple "
    "semantically equivalent phrasings or valid methods earn credit — "
    "with a one-sentence restriction_reason. Objective items are always "
    "Specific.\n"
    "Rich mathematics uses [Katex]...[/Katex]. Images use "
    '[img src="https://..." alt="..."] with meaningful neutral alt text.\n'
    "Return ONLY strict JSON:\n"
    '{"question":"","question_display":"","answer_restriction":"",'
    '"restriction_reason":"","display_answer":"","answers":[],'
    '"sub_questions":[],"answer_explanation":"","requires_visual":false}'
)

CRITIC_SYSTEM = (
    "You are the independent Aegis assessment critic. You are READ-ONLY: "
    "you never author or repair wording; you accept or reject the exact "
    "proposal identified by its SHA-256 hash.\n"
    "Judge, against the supplied source atom, assets, and blueprint cell: "
    "source fidelity; no invented facts or constraints; answer-space "
    "preservation; clarity and grade/board fit; exact category, cognitive "
    "skill, difficulty, and marks; the proposed Open/Specific restriction; "
    "correctness of the answer and rubric; image necessity and placement; "
    "answer and explanation adequacy; no answer leakage anywhere "
    "(including alt text); no duplicated shared directive or context.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"accept","proposal_sha256":"","feedback":[]} or '
    '{"verdict":"reject","proposal_sha256":"","feedback":["evidence-bound '
    'reason ..."]}\n'
    "proposal_sha256 MUST echo the exact hash you were given; feedback "
    "must cite the evidence, never taste."
)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def proposal_sha256(proposal: Mapping) -> str:
    return rel.sha256_json(proposal)


# --------------------------------------------------------------------------- #
# Deterministic mechanics: proposal shape, counts, and arithmetic
# --------------------------------------------------------------------------- #

def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _rich_text_defects(proposal: Mapping) -> list[str]:
    blob = "\n".join(str(part or "") for part in (
        proposal.get("question"),
        proposal.get("display_answer"),
        proposal.get("answer_explanation"),
        *(str(a.get("answer_content") or "") + str(a.get("answer") or "")
          for a in proposal.get("answers") or [] if isinstance(a, Mapping)),
    ))
    return [
        f"rich-text: {code}"
        for code in katex_rules.rich_text_issues(blob)
    ]


def proposal_defects(proposal: Mapping, cell: Mapping) -> list[str]:
    """Mechanical defects only — never a judgment about meaning."""
    defects: list[str] = []
    if not str(proposal.get("question") or "").strip():
        defects.append("empty question")
    restriction = proposal.get("answer_restriction")
    if restriction not in rel.ANSWER_RESTRICTIONS:
        defects.append(
            f"answer_restriction must be one of {rel.ANSWER_RESTRICTIONS} "
            f"(got {restriction!r}); never defaulted")
    kind = str(cell.get("sheet_kind") or "")
    marks = _to_float(cell.get("marks")) or 0.0
    answers = [
        a for a in proposal.get("answers") or [] if isinstance(a, Mapping)
    ]
    if kind == "objective":
        if restriction is not None and restriction != "Specific":
            defects.append("objective is always Specific")
        if not 1 <= len(answers) <= MAX_OBJECTIVE_OPTIONS:
            defects.append(
                f"objective needs 1..{MAX_OBJECTIVE_OPTIONS} options "
                f"(got {len(answers)})")
        correct = [
            a for a in answers
            if rel.is_correct_option(a.get("correct_answer"))
        ]
        if len(correct) != 1:
            defects.append(
                f"exactly one correct option required (got {len(correct)})")
        else:
            weight = _to_float(correct[0].get("answer_weightage"))
            if weight is None or abs(weight - marks) > 0.01:
                defects.append(
                    f"correct option weightage {correct[0].get('answer_weightage')!r} "
                    f"!= marks {marks:g}")
        for a in answers:
            if a in correct:
                continue
            weight = _to_float(a.get("answer_weightage"))
            if weight not in (0.0, None) and weight != 0.0:
                defects.append("wrong option weightage must be zero")
        contents = [
            str(a.get("answer_content") or "").strip() for a in answers
        ]
        if len({c for c in contents if c}) != len([c for c in contents if c]):
            defects.append("overlapping duplicate options")
        if not str(proposal.get("answer_explanation") or "").strip():
            defects.append("missing answer explanation")
    elif kind == "descriptive":
        if not str(proposal.get("display_answer") or "").strip():
            defects.append("missing display answer")
        if len(answers) > MAX_DESCRIPTIVE_ANSWERS:
            defects.append(
                f"more than {MAX_DESCRIPTIVE_ANSWERS} answer blocks")
        weights = [
            _to_float(a.get("answer_weightage")) for a in answers
            if str(a.get("answer_weightage") or "").strip() != ""
        ]
        if weights and None not in weights and marks:
            total = sum(weights)
            if abs(total - marks) > 0.01:
                defects.append(
                    f"answer weightage sum {total:g} != marks {marks:g}")
        sub_questions = [
            s for s in proposal.get("sub_questions") or []
            if isinstance(s, Mapping)
        ]
        if len(sub_questions) > MAX_SUBQUESTIONS:
            defects.append(f"more than {MAX_SUBQUESTIONS} subquestions")
        for s in sub_questions:
            keywords = s.get("keywords") or []
            if len(keywords) > MAX_SUBQUESTION_KEYWORDS:
                defects.append(
                    f"subquestion with more than "
                    f"{MAX_SUBQUESTION_KEYWORDS} keyword slots")
        if sub_questions and marks:
            sub_marks = [_to_float(s.get("marks")) for s in sub_questions]
            if None not in sub_marks:
                total = sum(sub_marks)
                if abs(total - marks) > 0.01:
                    defects.append(
                        f"subquestion marks sum {total:g} != parent marks "
                        f"{marks:g}")
    defects.extend(_rich_text_defects(proposal))
    return defects


# --------------------------------------------------------------------------- #
# Cache (spec §15)
# --------------------------------------------------------------------------- #

_memory_cache: dict[str, dict] = {}
_memory_lock = threading.Lock()


def cache_identity(atom: Mapping | None, cell: Mapping, meta: Mapping) -> dict:
    """The complete semantic cache identity for one obligation."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source_atom_sha256": rel.sha256_json(atom or {}),
        "asset_sha256s": [
            str(asset.get("sha256") or asset.get("url") or "")
            for asset in (atom or {}).get("assets") or []
        ],
        "blueprint_cell_sha256": rel.sha256_json(cell),
        "meta_sha256": rel.sha256_json(dict(meta)),
        "provider": "openai",
        "model": config.OPENAI_MODEL,
        "author_prompt_sha256": _sha256_text(AUTHOR_SYSTEM),
        "critic_prompt_sha256": _sha256_text(CRITIC_SYSTEM),
        "attempt_policy": f"author_critic_x{MAX_ATTEMPTS}",
    }


def _cache_key(identity: Mapping) -> str:
    return rel.sha256_json(identity)[:32]


def _cache_path(key: str):
    directory = config.DATA_DIR / "assessment_materialization"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}.json"


def _record_integrity(record: Mapping) -> str:
    body = {k: v for k, v in record.items() if k != "integrity_sha256"}
    return rel.sha256_json(body)


def load_cached_candidate(identity: Mapping) -> dict | None:
    """Reuse only an integrity-checked accepted candidate (spec §14/§15)."""
    key = _cache_key(identity)
    with _memory_lock:
        hit = _memory_cache.get(key)
    record = copy.deepcopy(hit) if hit is not None else None
    if record is None:
        try:
            path = _cache_path(key)
            if path.is_file():
                record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if not isinstance(record, dict):
        return None
    if record.get("integrity_sha256") != _record_integrity(record):
        return None
    if rel.sha256_json(record.get("identity") or {}) != rel.sha256_json(
            dict(identity)):
        return None
    candidate = record.get("candidate")
    if not isinstance(candidate, dict):
        return None
    if candidate.get("assessment_eligibility") != "accepted":
        return None
    # Proposal/critic binding: the accepted proposal hash the critic echoed
    # must still match the stored proposal content.
    authority = candidate.get("authority") or {}
    if authority.get("proposal_sha256") != authority.get(
            "critic_reviewed_sha256"):
        return None
    with _memory_lock:
        _memory_cache[key] = copy.deepcopy(record)
    return copy.deepcopy(candidate)


def store_cached_candidate(identity: Mapping, candidate: Mapping) -> None:
    if candidate.get("assessment_eligibility") != "accepted":
        return  # only accepted artifacts are reusable (spec §14)
    record = {
        "identity": dict(identity),
        "candidate": copy.deepcopy(dict(candidate)),
    }
    record["integrity_sha256"] = _record_integrity(record)
    key = _cache_key(identity)
    with _memory_lock:
        _memory_cache[key] = copy.deepcopy(record)
    try:
        _cache_path(key).write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def reset_cache() -> None:
    with _memory_lock:
        _memory_cache.clear()


# --------------------------------------------------------------------------- #
# Author + critic rounds
# --------------------------------------------------------------------------- #

def _author_user(
    atom: Mapping | None, cell: Mapping, meta: Mapping,
    context: str, feedback: list[str],
) -> str:
    payload = {
        "metadata": dict(meta),
        "blueprint_cell": dict(cell),
        "source_atom": dict(atom) if atom else None,
        "curricular_evidence": context,
    }
    body = json.dumps(payload, ensure_ascii=False)
    if feedback:
        body += (
            "\n\nYOUR PREVIOUS PROPOSAL WAS REJECTED BY THE INDEPENDENT "
            "CRITIC. Address every item, then return a fresh complete "
            "proposal:\n- " + "\n- ".join(feedback)
        )
    return body


def _critic_user(
    proposal: Mapping, sha: str, atom: Mapping | None, cell: Mapping,
) -> str:
    return json.dumps({
        "proposal": dict(proposal),
        "proposal_sha256": sha,
        "source_atom": dict(atom) if atom else None,
        "blueprint_cell": dict(cell),
    }, ensure_ascii=False)


def _candidate_id(atom: Mapping | None, cell: Mapping) -> str:
    seed = rel.canonical_json([
        (atom or {}).get("source_qid"), cell.get("cell_id"),
    ])
    return "CAND-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _assemble(
    proposal: Mapping | None,
    atom: Mapping | None,
    cell: Mapping,
    *,
    eligibility: str,
    flags: list[str],
    authority: Mapping,
) -> dict:
    """Candidate record (spec §5.3). Blueprint fields come from the CELL —
    the blueprint is authoritative for kind/category/Bloom/difficulty/marks.
    ``question_text`` is a byte-exact copy of the rich question (spec §3.8);
    a plain-text evaluator derivation stays mechanical and lossless.
    """
    proposal = proposal or {}
    question = str(proposal.get("question") or "")
    return {
        "candidate_id": _candidate_id(atom, cell),
        "source_atom_ids": (
            [atom["source_qid"]] if atom and atom.get("source_qid") else []),
        "blueprint_cell_id": str(cell.get("cell_id") or ""),
        "question": question,
        "question_text": question,
        "sheet_kind": str(cell.get("sheet_kind") or ""),
        "question_category": str(cell.get("question_category") or ""),
        "cognitive_skill": str(cell.get("cognitive_skill") or ""),
        "difficulty": str(cell.get("difficulty") or ""),
        "marks": cell.get("marks"),
        "appears_in": list(cell.get("appears_in") or []),
        # The workbook wire field: the blueprint's appears-in value(s),
        # emitted verbatim — the writer never expands or normalizes it.
        "question_appears_in": ", ".join(
            str(v) for v in cell.get("appears_in") or []),
        "answer_restriction": str(proposal.get("answer_restriction") or ""),
        "restriction_reason": str(proposal.get("restriction_reason") or ""),
        "display_answer": str(proposal.get("display_answer") or ""),
        "answers": copy.deepcopy(list(proposal.get("answers") or [])),
        "sub_questions": copy.deepcopy(
            list(proposal.get("sub_questions") or [])),
        "answer_explanation": str(proposal.get("answer_explanation") or ""),
        "requires_visual": bool(proposal.get("requires_visual")),
        "assets": copy.deepcopy(list((atom or {}).get("assets") or [])),
        "source_evidence": str((atom or {}).get("raw_text") or ""),
        "assessment_eligibility": eligibility,
        "flags": list(flags),
        "authority": dict(authority),
    }


def materialize_candidate(
    atom: Mapping | None,
    cell: Mapping,
    *,
    meta: Mapping,
    context: str = "",
    author_call: Callable[..., dict] | None = None,
    critic_call: Callable[..., dict] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    use_cache: bool = True,
) -> dict:
    """One obligation -> one immutable candidate, accepted or flagged.

    Never raises for semantic or provider trouble: the zero-loss invariant
    requires a candidate for every obligation, so failure modes produce
    flagged evidence-bound candidates instead of exceptions or silence.
    """
    identity = cache_identity(atom, cell, meta)
    if use_cache:
        cached = load_cached_candidate(identity)
        if cached is not None:
            return cached

    if author_call is None or critic_call is None:
        from . import generation

        def _default_author(system, user):
            return generation._openai_json(
                system, user, purpose="concept_mapping")

        def _default_critic(system, user):
            return generation._openai_json(
                system, user, purpose="concept_validation")

        author_call = author_call or _default_author
        critic_call = critic_call or _default_critic

    attempts: list[dict] = []
    feedback: list[str] = []
    best: Mapping | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            proposal = author_call(
                AUTHOR_SYSTEM,
                _author_user(atom, cell, meta, context, feedback))
        except Exception as exc:  # noqa: BLE001 — never exception->acceptance
            attempts.append({"attempt": attempt, "outcome": "author_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        if not isinstance(proposal, Mapping):
            attempts.append(
                {"attempt": attempt, "outcome": "malformed_proposal"})
            feedback = ["the previous response was not a JSON proposal"]
            continue
        best = proposal
        defects = proposal_defects(proposal, cell)
        if defects:
            attempts.append({"attempt": attempt, "outcome": "mechanical",
                             "defects": defects})
            feedback = defects
            continue
        sha = proposal_sha256(proposal)
        try:
            review = critic_call(
                CRITIC_SYSTEM, _critic_user(proposal, sha, atom, cell))
        except Exception as exc:  # noqa: BLE001
            attempts.append({"attempt": attempt, "outcome": "critic_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        review = review if isinstance(review, Mapping) else {}
        echoed = str(review.get("proposal_sha256") or "")
        verdict = str(review.get("verdict") or "").strip().lower()
        review_feedback = [
            str(f) for f in review.get("feedback") or [] if str(f).strip()
        ]
        if echoed != sha:
            # An unbound review certifies nothing; it is a failed round.
            attempts.append({"attempt": attempt, "outcome": "critic_unbound",
                             "echoed": echoed})
            feedback = ["the critic review did not bind to the proposal"]
            continue
        if verdict == "accept":
            authority = {
                "proposal_sha256": sha,
                "critic_reviewed_sha256": echoed,
                "critic_response_sha256": rel.sha256_json(dict(review)),
                "author_prompt_sha256": identity["author_prompt_sha256"],
                "critic_prompt_sha256": identity["critic_prompt_sha256"],
                "attempts": attempts + [
                    {"attempt": attempt, "outcome": "accepted"}],
            }
            candidate = _assemble(
                proposal, atom, cell,
                eligibility="accepted", flags=[], authority=authority)
            if use_cache:
                store_cached_candidate(identity, candidate)
            return candidate
        attempts.append({"attempt": attempt, "outcome": "critic_rejected",
                         "feedback": review_feedback})
        feedback = review_feedback or ["rejected without usable feedback"]

    # Bounded repair exhausted or provider failed: ship the best
    # evidence-bound candidate, flagged — never dropped, never accepted.
    flags = ["unresolved_author_critic"] + [
        f"attempt_{entry['attempt']}:{entry['outcome']}"
        for entry in attempts
    ]
    authority = {
        "proposal_sha256": proposal_sha256(best) if best else "",
        "critic_reviewed_sha256": "",
        "author_prompt_sha256": identity["author_prompt_sha256"],
        "critic_prompt_sha256": identity["critic_prompt_sha256"],
        "attempts": attempts,
    }
    return _assemble(
        best, atom, cell,
        eligibility="flagged", flags=flags, authority=authority)


def materialize_candidates(
    pairs: list[tuple[Mapping | None, Mapping]],
    *,
    meta: Mapping,
    context: str = "",
    author_call: Callable[..., dict] | None = None,
    critic_call: Callable[..., dict] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    use_cache: bool = True,
) -> dict:
    """Materialize every (atom, cell) obligation; zero-loss guaranteed."""
    candidates = [
        materialize_candidate(
            atom, cell, meta=meta, context=context,
            author_call=author_call, critic_call=critic_call,
            max_attempts=max_attempts, use_cache=use_cache)
        for atom, cell in pairs
    ]
    report = rel.zero_loss_report(
        obligations=[
            _candidate_id(atom, cell) for atom, cell in pairs],
        accepted=[
            c["candidate_id"] for c in candidates
            if c["assessment_eligibility"] == "accepted"],
        flagged=[
            c["candidate_id"] for c in candidates
            if c["assessment_eligibility"] != "accepted"],
    )
    if not report["holds"]:
        raise RuntimeError(
            "materialization broke the zero-loss invariant: "
            f"missing={report['missing']} unexpected={report['unexpected']}")
    return {
        "candidates": candidates,
        "accepted": sum(
            1 for c in candidates
            if c["assessment_eligibility"] == "accepted"),
        "flagged": sum(
            1 for c in candidates
            if c["assessment_eligibility"] != "accepted"),
        "zero_loss": report,
    }
