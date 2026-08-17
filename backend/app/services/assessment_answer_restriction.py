"""Recorded Open/Specific verdicts for Output-02 assessment candidates.

The corrected Open/Specific registry is evidence for the model, never an
executable lookup table.  Each semantically complete candidate is classified
exactly once through the shared Phase-3 decision kernel after answer/rubric
authoring and before the later marking allocation.  The unweighted semantic
answer/rubric evidence is complete for answer-space classification; marking
may subsequently allocate weights and other mechanical values, but may not
change that answer space.  The complete, unparsed registry transcription and
the complete recursively de-positioned candidate ride in the decision payload;
the registry hashes and sealed envelope bind replay.

Q10/Q11 govern any older workflow language retained inside the source
workbook: the public verdict is always ``Open`` or ``Specific``, there is no
adjudicator or third value, and uncertainty becomes a review flag.  Critic
dissent is advisory.  A Fixer response is checked by the same mechanical
contract as the author's response, so an invalid classification can never ship
as acceptable-with-flag.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .. import config
from . import assessment_release as rel
from . import semantic_confidence_policy as confidence_policy
from .phase3 import kernel


REGISTRY_ID = "registry-v2.0"
POLICY_BASE_VERSION = "assessment-answer-restriction-2"
REGISTRY_MARKDOWN_FILENAME = "open-specific-registry-v2.md"
REGISTRY_WORKBOOK_FILENAME = "open-specific-registry-v2.xlsx"

# In a source checkout ``config.ROOT`` is ``<repo>/backend``; both Docker
# runtimes copy the same repo-root files to ``/app/docs`` where ``config.ROOT``
# is ``/app``.  Path selection is packaging mechanics only.  The first
# directory containing either artifact is authoritative and must contain both;
# there is no partial or embedded fallback.
REGISTRY_DIRECTORY_CANDIDATES = (
    config.ROOT.parent / "docs",
    config.ROOT / "docs",
)


ANSWER_RESTRICTION_SYSTEM = (
    "You are the Aegis Open/Specific author. Decide answer_restriction for "
    "ONE semantically complete, unweighted assessment candidate. This pass "
    "runs after semantic question, expected-answer, and rubric authoring and "
    "before the later marking allocation. The supplied unweighted rubric "
    "evidence is complete for answer-space classification. Later marking may "
    "allocate weights and other mechanical values, but it may not change the "
    "semantic answer space. Read the complete question, source context, images "
    "and tables, expected answer, rubric/scoring evidence, response modality, "
    "subject, and grade together with the "
    "complete supplied policy registry. The registry is evidence to reason "
    "over; it is never a lookup table. Never classify from a command word, "
    "keyword, subject, question type, Policy ID, family name, option count, "
    "or any other local rule or default.\n"
    "Aegis rulings Q10 and Q11 have workflow precedence over older language "
    "retained in the registry evidence: return exactly Open or Specific. "
    "There is no adjudicator, classification_unresolved value, deferral, or "
    "third public state. If the evidence cannot support a confident choice, "
    "make the least-distorting evidence-bound choice, set review_required to "
    "true, and explain the uncertainty in review_reason. The Math/Physics "
    "method-equivalence carve-out lives only in the supplied evidence; local "
    "code supplies no subject branch.\n"
    "Describe the actual full-credit answer space, required elements, and "
    "accepted variations. Cite the decisive item/scoring evidence.\n"
    "Return ONLY strict JSON:\n"
    '{"candidate_id":"","answer_restriction":"Open|Specific",'
    '"restriction_reason":"","answer_space_contract":"",'
    '"required_elements":[],"accepted_variations":[],"evidence":"",'
    '"rationale":"","review_required":false,"review_reason":""}'
)


ANSWER_RESTRICTION_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one Aegis Open/Specific "
    "verdict. Independently audit the proposed verdict against the complete "
    "semantically authored, unweighted candidate, rubric/scoring evidence, "
    "source evidence, metadata, and complete supplied policy registry. This "
    "classification runs before later marking allocation; the unweighted "
    "semantic evidence is complete, and later marking may not change the "
    "answer space. Check whether the proposed answer "
    "space, required elements, accepted variations, evidence, and rationale "
    "support Open or Specific without any command-word, keyword, subject, "
    "question-type, Policy-ID, family, or default shortcut.\n"
    "Aegis Q10/Q11 workflow precedence applies even where older registry text "
    "mentions adjudication or classification_unresolved: there is no "
    "adjudicator or third value. Do not rewrite, replace, gate, or retry the "
    "author's verdict. Dissent ships only as review evidence and the recorded "
    "Open/Specific verdict stands. State honest confidence.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)


_PRINT_POSITION_FIELDS = frozenset({
    "bbox",
    "example_number",
    "page",
    "page_hint",
    "position",
    "printer_page",
    "row",
    "row_index",
    "row_number",
    "source_end",
    "source_order",
    "source_page",
    "source_paper_number",
    "source_start",
    "_phase32_segment_order",
    "_phase32_source_order",
})

_RESPONSE_FIELDS = frozenset({
    "candidate_id",
    "answer_restriction",
    "restriction_reason",
    "answer_space_contract",
    "required_elements",
    "accepted_variations",
    "evidence",
    "rationale",
    "review_required",
    "review_reason",
})


class AnswerRestrictionError(ValueError):
    """The registry or candidate obligation cannot be bound mechanically."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_registry() -> dict[str, str]:
    """Load and hash the two authoritative artifacts without parsing either.

    The Markdown bytes are decoded as UTF-8 solely so the exact text can enter
    the model payload.  No headings, Policy IDs, examples, families, keywords,
    or subject sections are extracted or indexed.
    """

    for raw_directory in REGISTRY_DIRECTORY_CANDIDATES:
        directory = Path(raw_directory)
        markdown_path = directory / REGISTRY_MARKDOWN_FILENAME
        workbook_path = directory / REGISTRY_WORKBOOK_FILENAME
        markdown_exists = markdown_path.is_file()
        workbook_exists = workbook_path.is_file()
        if not markdown_exists and not workbook_exists:
            continue
        if not markdown_exists or not workbook_exists:
            missing = (
                REGISTRY_MARKDOWN_FILENAME
                if not markdown_exists else REGISTRY_WORKBOOK_FILENAME
            )
            raise AnswerRestrictionError(
                f"Open/Specific registry directory {directory} is incomplete; "
                f"missing {missing}"
            )
        try:
            markdown_bytes = markdown_path.read_bytes()
            workbook_bytes = workbook_path.read_bytes()
        except OSError as exc:
            raise AnswerRestrictionError(
                f"Open/Specific registry could not be read: {exc}"
            ) from exc
        if not markdown_bytes or not workbook_bytes:
            raise AnswerRestrictionError(
                "Open/Specific registry artifacts must both be non-empty"
            )
        try:
            markdown_text = markdown_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AnswerRestrictionError(
                "Open/Specific registry Markdown must be valid UTF-8"
            ) from exc
        if not markdown_text.strip():
            raise AnswerRestrictionError(
                "Open/Specific registry Markdown contains no policy evidence"
            )
        return {
            "registry_id": REGISTRY_ID,
            "markdown_text": markdown_text,
            "markdown_sha256": _sha256_bytes(markdown_bytes),
            "workbook_sha256": _sha256_bytes(workbook_bytes),
            "markdown_source": f"docs/{REGISTRY_MARKDOWN_FILENAME}",
            "workbook_source": f"docs/{REGISTRY_WORKBOOK_FILENAME}",
        }
    searched = ", ".join(str(path) for path in REGISTRY_DIRECTORY_CANDIDATES)
    raise AnswerRestrictionError(
        "Open/Specific registry artifacts are unavailable; searched " + searched
    )


def _policy_version(registry: Mapping[str, str]) -> str:
    """Content-address both registry artifacts in the decision policy."""

    markdown_sha = str(registry.get("markdown_sha256") or "")
    workbook_sha = str(registry.get("workbook_sha256") or "")
    if len(markdown_sha) != 64 or len(workbook_sha) != 64:
        raise AnswerRestrictionError(
            "Open/Specific registry hashes must be full SHA-256 values"
        )
    return (
        f"{POLICY_BASE_VERSION};registry-md-sha256={markdown_sha};"
        f"registry-xlsx-sha256={workbook_sha}"
    )


def _content_evidence(value: Any) -> Any:
    """Deep-copy all candidate evidence while removing printer coordinates."""

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
        raise AnswerRestrictionError(
            "answer-restriction decisions require an envelope hash"
        )
    return envelope_sha


def _metadata(meta: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        raise AnswerRestrictionError(
            "answer-restriction metadata must be an object"
        )
    for field in ("subject", "grade"):
        if not str(meta.get(field) or "").strip():
            raise AnswerRestrictionError(
                f"answer-restriction metadata requires {field}"
            )
    return copy.deepcopy(dict(meta))


def _candidate_id(candidate: Mapping[str, Any], position: int) -> str:
    if not isinstance(candidate, Mapping):
        raise AnswerRestrictionError(
            f"answer-restriction candidate {position} is not an object"
        )
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise AnswerRestrictionError(
            f"answer-restriction candidate {position} has no candidate_id"
        )
    return candidate_id.strip()


def _checker(candidate_id: str) -> kernel.Checker:
    """Mechanics only: identity, public enum, and response schema."""

    def check(response: Mapping[str, Any]) -> list[str]:
        if not isinstance(response, Mapping):
            return ["response is not an object"]
        defects: list[str] = []
        unexpected = sorted(
            str(field) for field in set(response) - _RESPONSE_FIELDS
        )
        if unexpected:
            defects.append(f"response has unexpected fields {unexpected!r}")
        if response.get("candidate_id") != candidate_id:
            defects.append(f"candidate_id must echo {candidate_id!r}")
        if response.get("answer_restriction") not in rel.ANSWER_RESTRICTIONS:
            defects.append(
                "answer_restriction must be exactly Open or Specific; "
                "adjudication, deferral, and third values are forbidden"
            )
        for field in (
            "restriction_reason",
            "answer_space_contract",
            "evidence",
            "rationale",
        ):
            value = response.get(field)
            if not isinstance(value, str) or not value.strip():
                defects.append(f"{field} must be a non-empty string")
        for field in ("required_elements", "accepted_variations"):
            value = response.get(field)
            if not isinstance(value, list):
                defects.append(f"{field} must be an array of strings")
                continue
            for position, item in enumerate(value, start=1):
                if not isinstance(item, str) or not item.strip():
                    defects.append(
                        f"{field} item {position} must be a non-empty string"
                    )
        if not isinstance(response.get("review_required"), bool):
            defects.append("review_required must be boolean")
        review_reason = response.get("review_reason")
        if not isinstance(review_reason, str):
            defects.append("review_reason must be a string")
        elif response.get("review_required") and not review_reason.strip():
            defects.append(
                "review_reason must be non-empty when review_required is true"
            )
        return defects

    return check


def _live_author(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        ANSWER_RESTRICTION_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_mapping",
    )


def _live_critic(payload: dict[str, Any]) -> dict[str, Any]:
    from . import generation

    return generation._openai_json(
        ANSWER_RESTRICTION_CRITIC_SYSTEM,
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
    return _live_author, critic or _live_critic, fixer or fixer_mod.live_fixer


def _kernel_provider(provider: kernel.Provider) -> kernel.Provider:
    """Keep malformed author JSON inside the same-checker Fixer lane."""

    def call(request: dict[str, Any]) -> Mapping[str, Any]:
        response = provider(request)
        if isinstance(response, Mapping):
            return response
        return {
            "_aegis_invalid_response_type": type(response).__name__,
            "_aegis_raw_response": copy.deepcopy(response),
        }

    return call


def _kernel_critic(critic: kernel.Critic | None) -> kernel.Critic | None:
    """Make malformed advisory JSON visible without letting it gate output."""

    if critic is None:
        return None

    def call(request: dict[str, Any]) -> Mapping[str, Any]:
        review = critic(request)
        defects: list[str] = []
        if not isinstance(review, Mapping):
            defects.append(
                f"critic response is not an object ({type(review).__name__})"
            )
            copied: dict[str, Any] = {}
        else:
            copied = copy.deepcopy(dict(review))
            unexpected = sorted(
                str(field)
                for field in set(copied) - {"verdict", "confidence", "issues"}
            )
            if unexpected:
                defects.append(
                    f"critic response has unexpected fields {unexpected!r}"
                )
            if copied.get("verdict") not in {"verified", "dissent"}:
                defects.append("critic verdict must be verified or dissent")
            raw_confidence = copied.get("confidence")
            if isinstance(raw_confidence, bool):
                confidence = float("nan")
            else:
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError):
                    confidence = float("nan")
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                defects.append("critic confidence must be finite in [0, 1]")
            if not isinstance(copied.get("issues"), list):
                defects.append("critic issues must be an array")
        if defects:
            return {
                "verdict": "malformed",
                "confidence": 0.0,
                "issues": defects,
            }
        if confidence_policy.semantic_band(confidence) == "rejected":
            copied["issues"] = [
                *copied["issues"],
                f"critic confidence {confidence:.3f} is below the semantic "
                "acceptance floor; decision stands flagged",
            ]
        return copied

    return call


def _review_flags(decision: Mapping[str, Any]) -> list[str]:
    return [
        str(flag)
        for flag in decision.get("review_flags") or []
        if str(flag).strip()
    ]


def _stable_authority(
    decision: Mapping[str, Any], *, review_flags: list[str]
) -> dict[str, Any]:
    return {
        "decision_key": str(decision.get("key") or ""),
        "policy_version": str(decision.get("policy_version") or ""),
        "review_flags": list(review_flags),
        "fixer": bool(decision.get("fixer")),
    }


def _registry_identity(registry: Mapping[str, str]) -> dict[str, str]:
    return {
        "registry_id": str(registry["registry_id"]),
        "markdown_source": str(registry["markdown_source"]),
        "markdown_sha256": str(registry["markdown_sha256"]),
        "workbook_source": str(registry["workbook_source"]),
        "workbook_sha256": str(registry["workbook_sha256"]),
    }


def _decision_payload(
    candidate: Mapping[str, Any],
    *,
    meta: Mapping[str, Any],
    registry: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "stage": "assessment.answer_restriction",
        "rules": ANSWER_RESTRICTION_SYSTEM,
        "critic_rules": ANSWER_RESTRICTION_CRITIC_SYSTEM,
        "metadata": _content_evidence(meta),
        "policy_registry": {
            **_registry_identity(registry),
            # Exact decoded bytes: never stripped, summarized, indexed, or
            # split by subject/family/Policy ID.
            "markdown_text": registry["markdown_text"],
        },
        "candidate": _content_evidence(candidate),
    }


def _assemble(
    response: Mapping[str, Any],
    *,
    candidate_id: str,
    decision: Mapping[str, Any],
    registry: Mapping[str, str],
) -> dict[str, Any]:
    flags = _review_flags(decision)
    review_required = bool(response.get("review_required"))
    review_reason = str(response.get("review_reason") or "")
    if review_required:
        flags.append(
            "author requested answer-restriction review: " + review_reason
        )
    # A critic and the author may report the same concern.  Preserve order while
    # keeping the stable audit compact.
    flags = list(dict.fromkeys(flags))
    return {
        "candidate_id": candidate_id,
        "answer_restriction": str(response.get("answer_restriction") or ""),
        "restriction_reason": str(response.get("restriction_reason") or ""),
        "answer_space_contract": str(
            response.get("answer_space_contract") or ""
        ),
        "required_elements": copy.deepcopy(
            list(response.get("required_elements") or [])
        ),
        "accepted_variations": copy.deepcopy(
            list(response.get("accepted_variations") or [])
        ),
        "evidence": str(response.get("evidence") or ""),
        "rationale": str(response.get("rationale") or ""),
        "review_required": review_required,
        "review_reason": review_reason,
        "flags": flags,
        "authority": _stable_authority(decision, review_flags=flags),
        "registry": _registry_identity(registry),
    }


def decide_restrictions(
    candidates: list[Mapping],
    *,
    meta: Mapping,
    envelope_sha256: str,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    store: kernel.DecisionStore | None = None,
    fixer: kernel.Provider | None = None,
) -> list[dict[str, Any]]:
    """Return one cached Open/Specific verdict per candidate, in input order."""

    envelope_sha = _envelope_hash(envelope_sha256)
    metadata = _metadata(meta)
    prepared: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for position, candidate in enumerate(candidates, start=1):
        candidate_id = _candidate_id(candidate, position)
        if candidate_id in seen:
            raise AnswerRestrictionError(
                "answer-restriction candidates repeat candidate_id "
                f"{candidate_id!r}"
            )
        seen.add(candidate_id)
        prepared.append((candidate_id, _content_evidence(candidate)))
    if not prepared:
        return []

    registry = _load_registry()
    policy_version = _policy_version(registry)
    provider, critic, fixer = _live_authorities(provider, critic, fixer)
    provider = _kernel_provider(provider)
    critic = _kernel_critic(critic)
    decision_store = store or kernel.DecisionStore()

    def decide_one(unit: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        candidate_id, candidate_evidence = unit
        payload = _decision_payload(
            candidate_evidence,
            meta=metadata,
            registry=registry,
        )
        decision = kernel.decide(
            kind="assessment.answer_restriction",
            unit_id=candidate_id,
            envelope_sha256=envelope_sha,
            payload=payload,
            provider=provider,
            checker=_checker(candidate_id),
            critic=critic,
            store=decision_store,
            policy_version=policy_version,
            fixer=fixer,
        )
        return _assemble(
            decision["response"],
            candidate_id=candidate_id,
            decision=decision,
            registry=registry,
        )

    verdicts = kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
    )
    expected_ids = [candidate_id for candidate_id, _candidate in prepared]
    returned_ids = [str(verdict.get("candidate_id") or "") for verdict in verdicts]
    if returned_ids != expected_ids or len(returned_ids) != len(set(returned_ids)):
        missing = [value for value in expected_ids if value not in returned_ids]
        unexpected = [value for value in returned_ids if value not in expected_ids]
        raise AnswerRestrictionError(
            "answer-restriction decisions broke exact ordered coverage "
            f"(missing={missing}, unexpected={unexpected})"
        )
    return verdicts
