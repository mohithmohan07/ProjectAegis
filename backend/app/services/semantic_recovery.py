"""Bounded, checkpoint-aware recovery for semantic generation failures.

This module is deliberately an orchestration policy, not another runtime
installer.  A generation caller supplies the operation, durable checkpoint
access, GPT JSON caller, and persistence callback.  The policy then:

* reuses the newest safe pre-Type checkpoint instead of restarting a chapter;
* repairs only rows implicated by a semantic failure;
* rejects an identical failure/checkpoint signature and no-op model replies;
* prunes only stages downstream of the repaired checkpoint;
* keeps source identity, explicit Figures, QIDs, and persistence fail-closed.

Transport retry remains the responsibility of the OpenAI request layer.  This
module is for a valid provider response whose semantic result was rejected.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import grounding_certificate


class FailureKind(str, Enum):
    """Disposition of a generation failure at the orchestration boundary."""

    HUMAN_DECISION = "human_decision"
    RECOVERABLE_SEMANTIC = "recoverable_semantic"
    SOURCE_IDENTITY = "source_identity"
    EXPLICIT_FIGURE = "explicit_figure"
    QID_INTEGRITY = "qid_integrity"
    PERSISTENCE = "persistence"
    PROVIDER = "provider"
    NON_SEMANTIC = "non_semantic"


@dataclass(frozen=True)
class FailureAssessment:
    kind: FailureKind
    reason: str

    @property
    def recoverable(self) -> bool:
        return self.kind is FailureKind.RECOVERABLE_SEMANTIC


@dataclass(frozen=True)
class RecoveryContext:
    attempt: int
    failure: Exception
    assessment: FailureAssessment
    failure_signature: str


@dataclass(frozen=True)
class RepairResult:
    checkpoint: dict[str, Any]
    base_stage: str
    changed_row_indexes: tuple[int, ...]
    repair_signature: str
    scope: str
    changed_units: tuple[str, ...] = ()

    @property
    def changed_count(self) -> int:
        return len(self.changed_row_indexes) + len(self.changed_units)


_DEFAULT_MAX_TOTAL_ATTEMPTS = 3
_MAX_TOTAL_ATTEMPTS_CEILING = 8


@dataclass
class RecoveryPolicy:
    """Limits for one logical generation run.

    Attempts are keyed by (issue signature, candidate/checkpoint hash), not by
    one global run counter.  An identical issue against an identical candidate
    is refused outright; the same issue against a materially changed candidate
    receives exactly one fresh verification turn, bounded overall by
    ``max_total_attempts`` so distinct-but-wrong candidates cannot loop
    indefinitely.
    """

    max_attempts: int = 1
    max_total_attempts: int = _DEFAULT_MAX_TOTAL_ATTEMPTS
    max_rows_per_attempt: int = 12
    max_source_chars: int = 28_000
    seen_failure_signatures: set[str] = field(default_factory=set)
    seen_repair_signatures: set[str] = field(default_factory=set)
    attempted_issue_candidates: set[tuple[str, str]] = field(
        default_factory=set)

    def __post_init__(self) -> None:
        """Keep every construction path within the per-candidate budget."""

        try:
            attempts = int(self.max_attempts)
        except (TypeError, ValueError):
            attempts = 1
        self.max_attempts = max(0, min(1, attempts))
        try:
            total = int(self.max_total_attempts)
        except (TypeError, ValueError):
            total = _DEFAULT_MAX_TOTAL_ATTEMPTS
        self.max_total_attempts = max(
            self.max_attempts,
            min(_MAX_TOTAL_ATTEMPTS_CEILING, total),
        )

    @classmethod
    def from_environment(cls) -> "RecoveryPolicy":
        raw = os.getenv("AEGIS_SEMANTIC_RECOVERY_MAX_ATTEMPTS", "1")
        try:
            attempts = int(raw)
        except (TypeError, ValueError):
            attempts = 1
        raw_total = os.getenv(
            "AEGIS_SEMANTIC_RECOVERY_MAX_TOTAL_ATTEMPTS",
            str(_DEFAULT_MAX_TOTAL_ATTEMPTS),
        )
        try:
            total = int(raw_total)
        except (TypeError, ValueError):
            total = _DEFAULT_MAX_TOTAL_ATTEMPTS
        # __post_init__ also clamps explicit callers so deployment
        # configuration cannot opt back into a repeated semantic-repair loop.
        return cls(max_attempts=attempts, max_total_attempts=total)


class SemanticRecoveryExhausted(RuntimeError):
    """A recoverable failure could not be safely changed within the budget."""


class ProviderResponseContractError(RuntimeError):
    """A provider response failed mechanical parsing or schema validation.

    This is intentionally distinct from semantic rejection: changing a
    checkpoint cannot repair malformed structured output, so outer semantic
    recovery must never send this failure through another GPT repair pass.
    """


class HumanDecisionRequired(RuntimeError):
    """Generation paused until a deterministic semantic choice is supplied.

    ``pending_decision`` is deliberately plain JSON data so orchestration code
    can persist it in a checkpoint, return it through the API, and resume the
    same semantic context without replaying exploratory model calls.
    """

    @staticmethod
    def _validated(pending_decision: Mapping[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(
                json.dumps(
                    dict(pending_decision),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            )
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "pending_decision must be JSON-serializable"
            ) from exc
        if (
            not str(value.get("decision_id") or "").strip()
            or not str(value.get("context_hash") or "").strip()
        ):
            raise ValueError(
                "pending_decision requires decision_id and context_hash"
            )
        return value

    def __init__(
        self,
        pending_decision: Mapping[str, Any],
        *,
        companions: Sequence[Mapping[str, Any]] = (),
    ):
        value = self._validated(pending_decision)
        # ``companions`` are further decisions from the same rejection batch.
        # A critic that rejects several concepts at once used to surface only
        # the first; the rest each cost one full pipeline replay to even come
        # into view, so N independent conflicts were settled in N serial
        # cycles. Carrying the whole batch lets orchestration settle every one
        # before replaying once. Each companion is a complete, independently
        # resolvable packet — never a partial reference into this one.
        self.companion_pending_decisions: list[dict[str, Any]] = [
            self._validated(row) for row in companions
        ]
        self.pending_decision: dict[str, Any] = value
        self.decision_id = str(value["decision_id"]).strip()
        self.context_hash = str(value["context_hash"]).strip()
        super().__init__(
            "Generation paused for a human semantic decision "
            f"({self.decision_id}). No semantic retry was attempted."
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a persistence-safe copy of the pending decision."""

        return copy.deepcopy(self.pending_decision)


_SOURCE_IDENTITY_PATTERNS = (
    r"\bphase 3(?:\.4)? hierarchy\b",
    r"\bsemantic source graph is not safe\b",
    r"\bsemantic (?:source )?graph\b.*\b"
    r"(?:unsafe|review_required|review required|not safe)\b",
    r"\bsource review required\b",
    r"\bsource[_ -]?contract(?:[_ -]?hash)?\b.*\b(?:mismatch|changed|invalid)\b",
    r"\bsource identity\b.*\b(?:missing|mismatch|invalid|corrupt)\b",
    r"\bcheckpoint does not match the selected chapter or converted source\b",
    r"\bunknown phase 3 source artifact\b",
    r"\bphase 3 source artifact is unavailable\b",
    r"\bcanonical source\b.*\b(?:missing|unavailable|corrupt)\b",
    r"\bstaged file is missing\b",
    r"\bconverted source\b.*\b(?:missing|empty|corrupt)\b",
    r"\bgpt pdf-to-acsd batch\b.*\brequires review\b",
    r"\b(?:pdf[- ]to[- ]acsd|page acsd|acsd source)\b.*\b"
    r"(?:requires review|verification failed|unverified|invalid|mismatch)\b",
    r"\bsource adjudication\b.*\b"
    r"(?:requires review|verification failed|unverified|invalid|mismatch)\b",
)
_FIGURE_PATTERNS = (
    r"\bfigure_reference_without_image\b",
    r"\bfigure_reference_image_mismatch\b",
    r"\b(?:unresolved_explicit_figure_reference|"
    r"phase2_unresolved_figure_reference)\b",
    r"\bunresolved[_ -]+(?:explicit[_ -]+|phase2[_ -]+)?"
    r"figure[_ -]+reference\b",
    r"\bexplicit figure\b.*\b(?:missing|unresolved|ambiguous|mismatch|invalid)\b",
    r"\bterminal figure\b.*\b(?:missing|unresolved|ambiguous|mismatch|invalid)\b",
    r"\bfigure (?:tag|reference|identity)\b.*\b(?:missing|unresolved|ambiguous|mismatch|invalid)\b",
)
_QID_PATTERNS = (
    r"\binvalid_source_inventory\b",
    r"\b(?:missing|duplicate|duplicated|conflicting|malformed|invalid)\s+qids?\b",
    r"\bqids?\b.*\b(?:missing|duplicate|duplicated|conflicting|malformed|invalid)\b",
    r"\binventory\b.*\bqid\b.*\b(?:missing|duplicate|malformed|invalid)\b",
)
_PERSISTENCE_PATTERNS = (
    r"\b(?:database|sqlite|postgres|transaction|commit|rollback)\b.*"
    r"\b(?:corrupt|failed|failure|locked|integrity|write)\b",
    r"\b(?:workbook|artifact|checkpoint)\b.*\b(?:publish|persist|write)\b.*"
    r"\b(?:failed|failure|corrupt)\b",
    r"\bconcept workbook read-back validation failed\b",
    r"\bworkbook read-back\b.*\b(?:failed|failure|corrupt|mismatch|invalid)\b",
    r"\b(?:workbook|artifact)\b.*\b(?:corrupt|corruption|mismatch)\b",
    r"\b(?:disk full|no space left|read-only file system|permission denied)\b",
)
_PROVIDER_PATTERNS = (
    r"\bopenai quota\b",
    r"\binsufficient_quota\b",
    r"\bapi key\b.*\b(?:missing|invalid)\b",
    r"\bwaiting for (?:an )?available openai generation slot\b",
    r"\bopenai capacity\b",
    r"\brate limit\b",
)
_SEMANTIC_PATTERNS = (
    r"\bsemantic (?:validation|critic|grounding|topic|concept|host)\b",
    r"\bgrounding\b",
    r"\bunsupported (?:claim|concept|content|fact)\b",
    r"\bindependent (?:critic|verification)\b",
    r"\btopic\b.*\b(?:wrong|mismatch|misplaced|assignment|host|collapsed)\b",
    r"\bconcept\b.*\b(?:wrong|mismatch|misplaced|unsupported|validation)\b",
    r"\b(?:hierarchy|topology)\b.*\b(?:rejected|failed|invalid|mismatch)\b",
    r"\bcould not source-verify\b.*\bconcept topology\b",
    r"\b(?:concept|topology|grounding)\b.{0,180}\brequires human review\b",
    r"\b(?:type|activity).{0,30}\bhost\b",
    r"\bhost certification\b",
    r"\bpost-type assignment did not certify\b",
    r"\b(?:deposit|inventory|coverage)\b.{0,80}\b(?:failed|failure|rejected)\b",
    r"\bpre[- ]learning\b.{0,120}\b"
    r"(?:no topics|no concepts|failed|failure|invalid|rejected)\b",
    r"\bvalidation failed\b",
)


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE | re.DOTALL) for pattern in patterns)


def classify_failure(exc: Exception) -> FailureAssessment:
    """Classify an exception conservatively.

    Unknown exceptions are never fed to GPT.  Only explicit semantic rejection
    signals are recoverable; this avoids turning programmer or infrastructure
    failures into an expensive retry loop.
    """
    if isinstance(exc, HumanDecisionRequired):
        return FailureAssessment(
            FailureKind.HUMAN_DECISION,
            "a semantic choice is waiting for human input",
        )
    if isinstance(exc, ProviderResponseContractError):
        return FailureAssessment(
            FailureKind.PROVIDER,
            "the provider response failed its mechanical output contract; "
            "semantic repair is not applicable",
        )
    text = re.sub(r"\s+", " ", str(exc or "")).strip()
    lowered = text.casefold()
    if isinstance(exc, grounding_certificate.GroundingCertificateError):
        # A certificate failure is an integrity result, not a model verdict.
        # It may contain words such as "grounding", "concept", or "topology"
        # which the broad legacy semantic patterns below intentionally match.
        # Typed dispatch must take precedence so a stale lineage/evidence seal
        # can never authorize an unrelated paid checkpoint rewrite.
        if any(
            marker in lowered
            for marker in (
                "semantic graph/topology",
                "semantic graph",
                "source/topology contract",
                "source contract",
            )
        ):
            return FailureAssessment(
                FailureKind.SOURCE_IDENTITY,
                "the certified source/topology identity changed; semantic "
                "checkpoint repair is forbidden",
            )
        return FailureAssessment(
            FailureKind.NON_SEMANTIC,
            "certified payload/evidence integrity failed; semantic "
            "checkpoint repair is unsafe",
        )
    if isinstance(exc, (OSError, IOError)):
        return FailureAssessment(
            FailureKind.PERSISTENCE,
            "filesystem or publication failure",
        )
    if _matches_any(lowered, _SOURCE_IDENTITY_PATTERNS):
        return FailureAssessment(
            FailureKind.SOURCE_IDENTITY,
            "source identity cannot be repaired without changing the source",
        )
    if _matches_any(lowered, _FIGURE_PATTERNS):
        return FailureAssessment(
            FailureKind.EXPLICIT_FIGURE,
            "an explicit Figure is unresolved",
        )
    if _matches_any(lowered, _QID_PATTERNS):
        return FailureAssessment(
            FailureKind.QID_INTEGRITY,
            "source-owned QID identity is missing or duplicated",
        )
    if _matches_any(lowered, _PERSISTENCE_PATTERNS):
        return FailureAssessment(
            FailureKind.PERSISTENCE,
            "durable state or publication is not trustworthy",
        )
    if _matches_any(lowered, _PROVIDER_PATTERNS):
        return FailureAssessment(
            FailureKind.PROVIDER,
            "the provider is unavailable; semantic repair cannot call it safely",
        )
    if isinstance(exc, (ValueError, RuntimeError)) and _matches_any(
        lowered, _SEMANTIC_PATTERNS
    ):
        return FailureAssessment(
            FailureKind.RECOVERABLE_SEMANTIC,
            "bounded concept/topic repair is safe to attempt",
        )
    return FailureAssessment(
        FailureKind.NON_SEMANTIC,
        "failure is not an identified semantic rejection",
    )


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return repr(value)


def checkpoint_signature(checkpoint: Mapping[str, Any] | None) -> str:
    identity = copy.deepcopy(dict(checkpoint or {}))
    # This orchestration-only dispatch ledger is written immediately before a
    # paid repair. Excluding it keeps the pre-dispatch and restart signatures
    # identical, so an unknown outcome cannot masquerade as a new issue.
    identity.pop("semantic_recovery_dispatches", None)
    return hashlib.sha256(
        _stable_json(identity).encode("utf-8")
    ).hexdigest()


def failure_signature(
    exc: Exception,
    checkpoint: Mapping[str, Any] | None,
) -> str:
    normalized = re.sub(r"\s+", " ", str(exc or "")).strip().casefold()
    payload = {
        "type": type(exc).__name__,
        "message": normalized,
        "checkpoint": checkpoint_signature(checkpoint),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def issue_signature(exc: Exception) -> str:
    """Candidate-independent identity of one semantic issue.

    Unlike :func:`failure_signature`, this excludes the checkpoint hash so the
    same rejection (for example ``CONCEPT-GROUND-0023``) keeps one identity
    across materially different repair candidates.
    """

    normalized = re.sub(r"\s+", " ", str(exc or "")).strip().casefold()
    payload = {
        "type": type(exc).__name__,
        "message": normalized,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def run_with_semantic_recovery(
    operation: Callable[[], Any],
    *,
    checkpoint_snapshot: Callable[[], dict[str, Any] | None],
    repair_checkpoint: Callable[
        [dict[str, Any], RecoveryContext], RepairResult | None
    ],
    persist_repair: Callable[[RepairResult, RecoveryContext], None],
    before_repair: Callable[
        [dict[str, Any], RecoveryContext], None
    ] | None = None,
    on_recovery_verified: Callable[
        [tuple[RecoveryContext, ...]], None
    ] | None = None,
    policy: RecoveryPolicy | None = None,
    log: Callable[..., None] | None = None,
) -> Any:
    """Run ``operation`` and recover rejected semantic checkpoint rows in-place.

    Success is postcondition-first: an applied repair is only reported as
    pending re-verification.  The repaired candidate is certified — and
    ``on_recovery_verified`` invoked — only when ``operation`` subsequently
    completes without the issue recurring. Exactly one terminal outcome is
    raised per run. Recovery exhaustion is logged here once; a non-recoverable
    exception is re-raised without a generic precursor so the outer persistence
    boundary owns its one structured terminal event.
    """
    policy = policy or RecoveryPolicy.from_environment()
    attempt = 0
    applied_contexts: list[RecoveryContext] = []
    terminal_logged = False

    def _terminal(message: str, cause: Exception) -> SemanticRecoveryExhausted:
        nonlocal terminal_logged
        error = SemanticRecoveryExhausted(message)
        if log is not None and not terminal_logged:
            terminal_logged = True
            log(message, level="error")
        return error

    while True:
        try:
            outcome = operation()
        except HumanDecisionRequired:
            # A pause is durable workflow state, not a semantic failure. Let the
            # caller persist/return it without invoking any recovery model.
            raise
        except Exception as exc:
            assessment = classify_failure(exc)
            checkpoint = checkpoint_snapshot() or {}
            signature = failure_signature(exc, checkpoint)
            issue = issue_signature(exc)
            candidate = checkpoint_signature(checkpoint)
            if not assessment.recoverable:
                raise
            if (
                signature in policy.seen_failure_signatures
                or (issue, candidate) in policy.attempted_issue_candidates
            ):
                # Identical issue against an identical candidate: the previous
                # repair did not clear this rejection signature, so a retry
                # would replay the same paid loop.
                raise _terminal(
                    "semantic recovery refused an identical failure/checkpoint "
                    f"retry signature after {attempt} repair attempt(s): {exc}",
                    exc,
                ) from exc
            if attempt >= policy.max_total_attempts or policy.max_attempts < 1:
                budget = (
                    policy.max_total_attempts
                    if policy.max_attempts >= 1
                    else policy.max_attempts
                )
                raise _terminal(
                    "semantic recovery exhausted its bounded "
                    f"{budget} repair attempt(s); the unsupported "
                    f"or rejected content remains unresolved: {exc}",
                    exc,
                ) from exc
            policy.seen_failure_signatures.add(signature)
            policy.attempted_issue_candidates.add((issue, candidate))
            if not checkpoint:
                raise _terminal(
                    "semantic recovery has no durable successful checkpoint to "
                    "repair safely; source and completed work were left intact",
                    exc,
                ) from exc

            context = RecoveryContext(
                attempt=attempt + 1,
                failure=exc,
                assessment=assessment,
                failure_signature=signature,
            )
            if log is not None:
                log(
                    "Recoverable semantic rejection detected; repairing only "
                    "the affected checkpoint scope and then continuing this run.",
                    level="warning",
                )
            if before_repair is not None:
                # The caller uses this boundary to durably record dispatch
                # before the first byte of a paid repair request is sent.
                before_repair(checkpoint, context)
            result = repair_checkpoint(checkpoint, context)
            if result is None or result.changed_count <= 0:
                raise _terminal(
                    "semantic recovery produced no safe checkpoint change; "
                    "an identical retry was not attempted",
                    exc,
                ) from exc
            if result.repair_signature in policy.seen_repair_signatures:
                raise _terminal(
                    "semantic recovery produced an already-seen repair "
                    "signature; retry loop stopped",
                    exc,
                ) from exc
            policy.seen_repair_signatures.add(result.repair_signature)
            persist_repair(result, context)
            applied_contexts.append(context)
            attempt += 1
            if log is not None:
                if result.changed_row_indexes:
                    changed = (
                        "row(s) "
                        + ", ".join(
                            str(index)
                            for index in result.changed_row_indexes
                        )
                    )
                else:
                    changed = (
                        "unit(s) "
                        + ", ".join(result.changed_units)
                    )
                log(
                    f"Semantic recovery attempt {attempt}/"
                    f"{policy.max_total_attempts} applied a bounded repair to "
                    f"checkpoint stage '{result.base_stage}' {changed}; "
                    "dependent later stages were invalidated and the repaired "
                    "candidate is being re-verified before it is certified.",
                    level="warning",
                )
        else:
            if applied_contexts:
                # Postcondition holds: the run completed without the repaired
                # issue recurring. Only now is the repair certified as
                # succeeded and reported as such.
                if on_recovery_verified is not None:
                    on_recovery_verified(tuple(applied_contexts))
                if log is not None:
                    log(
                        "Semantic recovery postcondition verified: "
                        f"{len(applied_contexts)} bounded repair(s) passed "
                        "re-verification and generation completed.",
                        level="success",
                    )
            return outcome


_ROW_INDEX_RE = re.compile(r"\brow(?:_index|\s+index)?\s*[=:]\s*(\d+)\b", re.I)
_CONCEPT_ID_RE = re.compile(
    r"\b(?P<family>CONCEPT-GROUND|TOPOLOGY-CONCEPT|HOST-CONCEPT|CONCEPT)-"
    r"(?P<number>\d{1,6})\b",
    re.IGNORECASE,
)
_QID_RE = re.compile(r"\bQINV-\d{1,8}(?:\.\d{1,4})?\b", re.I)
_PAGE_RE = re.compile(
    r"\b(?:pdf[- _]?page|audit[- _]?page|page(?:_number)?)\s*"
    r"(?:[=:_-]\s*|\s+)(\d{1,6})\b",
    re.IGNORECASE,
)
_FIGURE_TOKEN_RE = re.compile(
    r"(?:\[(?:Image|Figure)\s*:[^\]]+\]|\bFig(?:ure)?\.?\s*\d+[A-Za-z]?"
    r"(?:\([A-Za-z0-9]+\))?)",
    re.IGNORECASE,
)
_VISUAL_TOKEN_RE = re.compile(
    r"(?:"
    r"!\[[^\]]*\]\(\s*https?://(?:[^()\s]|(?:\([^()]*\)))+"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
    r"|"
    r"\[img\b[^\]]*\]"
    r"|"
    r"\\includegraphics(?:\[[^\]]*\])?\{https?://[^}\s]+\}"
    r")",
    re.IGNORECASE,
)
_PROTECTED_PRIVATE_FIELDS = {
    "_semantic_graph_contract",
    "_semantic_source_task_id",
    "_activity_hub_qids",
}
_POST_RECOVERY_STAGES = (
    "pre_type_assignment",
    "type_taxonomy_ready",
    "question_inventory",
    "description_method_snapshot",
    "canonical_skeleton",
    "skeleton_complete",
)
_RECOVERY_STAGES = ("pre_learner_analysis", *_POST_RECOVERY_STAGES)


def checkpoint_entries(checkpoint: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(checkpoint, Mapping):
        return []
    history = checkpoint.get("checkpoints")
    if isinstance(history, list):
        return [
            copy.deepcopy(entry)
            for entry in history
            if isinstance(entry, dict)
        ]
    if isinstance(checkpoint.get("records"), list):
        return [copy.deepcopy(dict(checkpoint))]
    return []


def select_recovery_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    stages: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """Choose the furthest safe stage whose dependants can be replayed."""
    entries = checkpoint_entries(checkpoint)
    by_stage = {
        str(entry.get("stage") or ""): entry
        for entry in entries
        if isinstance(entry.get("records"), list)
    }
    for stage in stages or _RECOVERY_STAGES:
        candidate = by_stage.get(stage)
        if candidate and candidate.get("records"):
            return copy.deepcopy(candidate)
    return None


def _normal_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _validation_row_indexes(
    records: Sequence[Mapping[str, Any]],
    validation_errors: Callable[[list[dict[str, Any]]], Sequence[Mapping[str, Any]]]
    | None,
) -> list[int]:
    if validation_errors is None:
        return []
    try:
        errors = validation_errors([copy.deepcopy(dict(row)) for row in records])
    except Exception:
        return []
    return sorted({
        int(error.get("row_index"))
        for error in errors or []
        if isinstance(error, Mapping)
        and isinstance(error.get("row_index"), int)
        and 0 <= int(error["row_index"]) < len(records)
    })


def _missing_certification_topic_keys(checkpoint: Mapping[str, Any]) -> set[str]:
    inventory = checkpoint.get("question_task_inventory") or {}
    mined_types = checkpoint.get("mined_types") or {}
    items = [
        item for item in inventory.get("items") or []
        if isinstance(item, Mapping)
    ]
    ledger = mined_types.get("placement_certifications") or {}
    hosts = ledger.get("hosts") if isinstance(ledger, Mapping) else {}
    if not isinstance(hosts, Mapping):
        hosts = {}
    return {
        _normal_key(item.get("topic_hint"))
        for item in items
        if str(item.get("qid") or "").strip()
        and str(item.get("qid") or "").strip() not in hosts
        and _normal_key(item.get("topic_hint"))
    }


def _inventory_topic_keys(checkpoint: Mapping[str, Any]) -> set[str]:
    inventory = checkpoint.get("question_task_inventory") or {}
    return {
        _normal_key(item.get("topic_hint"))
        for item in inventory.get("items") or []
        if isinstance(item, Mapping) and _normal_key(item.get("topic_hint"))
    }


def _diagnostic_qid_topic_keys(
    checkpoint: Mapping[str, Any],
    diagnostic_qids: set[str],
) -> set[str]:
    inventory = checkpoint.get("question_task_inventory") or {}
    return {
        _normal_key(item.get("topic_hint"))
        for item in inventory.get("items") or []
        if isinstance(item, Mapping)
        and str(item.get("qid") or "").strip().upper() in diagnostic_qids
        and _normal_key(item.get("topic_hint"))
    }


def _diagnostic_qid_host_indexes(
    checkpoint: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    diagnostic_qids: set[str],
) -> set[int]:
    mined_types = checkpoint.get("mined_types") or {}
    certifications = (
        mined_types.get("placement_certifications")
        if isinstance(mined_types, Mapping)
        else None
    )
    hosts = (
        certifications.get("hosts")
        if isinstance(certifications, Mapping)
        else None
    )
    if not isinstance(hosts, Mapping):
        return set()
    indexes: set[int] = set()
    for qid in diagnostic_qids:
        host = hosts.get(qid)
        if not isinstance(host, Mapping):
            continue
        host_topic = _normal_key(
            host.get("topic_key") or host.get("topic"))
        host_concept = _normal_key(
            host.get("concept_key") or host.get("concept"))
        culmination = bool(host.get("is_culmination"))
        for index, row in enumerate(records):
            row_topic = _normal_key(row.get("topic"))
            row_concept = _normal_key(row.get("concept_title"))
            if host_topic and row_topic != host_topic:
                continue
            if culmination:
                if str(row.get("concept_title") or "").casefold().startswith(
                    "culmination"
                ):
                    indexes.add(index)
            elif host_concept and row_concept == host_concept:
                indexes.add(index)
    return indexes


def _page_numbers(value: Any) -> set[int]:
    if isinstance(value, int):
        return {value} if value > 0 else set()
    if isinstance(value, (list, tuple, set)):
        out: set[int] = set()
        for item in value:
            out.update(_page_numbers(item))
        return out
    return {
        int(match.group(1))
        for match in re.finditer(r"\b(\d{1,6})\b", str(value or ""))
        if int(match.group(1)) > 0
    }


def _page_implicated_topic_keys(
    checkpoint: Mapping[str, Any],
    pages: set[int],
) -> set[str]:
    if not pages:
        return set()
    inventory = checkpoint.get("question_task_inventory") or {}
    return {
        _normal_key(item.get("topic_hint"))
        for item in inventory.get("items") or []
        if isinstance(item, Mapping)
        and pages.intersection(_page_numbers(item.get("page_hint")))
        and _normal_key(item.get("topic_hint"))
    }


def implicated_row_indexes(
    checkpoint: Mapping[str, Any],
    failure: Exception,
    *,
    validation_errors: Callable[[list[dict[str, Any]]], Sequence[Mapping[str, Any]]]
    | None = None,
    max_rows: int = 12,
) -> tuple[int, ...]:
    """Resolve a bounded row scope from structured diagnostics and checkpoint data."""
    records = [
        row for row in checkpoint.get("records") or []
        if isinstance(row, Mapping)
    ]
    text = str(failure or "")
    indexes: set[int] = set()
    explicit_indexes: set[int] = set()
    failure_pages = {
        int(match.group(1)) for match in _PAGE_RE.finditer(text)
    }
    explicit_indexes.update(
        int(match.group(1))
        for match in _ROW_INDEX_RE.finditer(text)
        if 0 <= int(match.group(1)) < len(records)
    )
    explicit_indexes.update(
        int(match.group("number")) - 1
        for match in _CONCEPT_ID_RE.finditer(text)
        if 0 <= int(match.group("number")) - 1 < len(records)
    )
    diagnostic_concept_ids = list(_CONCEPT_ID_RE.finditer(text))
    diagnostic_qids = {
        qid.upper() for qid in _QID_RE.findall(text)
    }
    unique_diagnostic_ids = {
        (
            match.group("family").upper(),
            int(match.group("number")),
        )
        for match in diagnostic_concept_ids
    }
    # These ID families are defined as one-based indexes in the Phase 3.1,
    # Phase 3.2, and Phase 3.3 payload builders. If a diagnostic carries one
    # that cannot resolve against this checkpoint, do not fall through to a
    # topic-wide lexical guess: the checkpoint and diagnostic are from
    # different row revisions.
    if diagnostic_concept_ids and (
        len(unique_diagnostic_ids) > max_rows
        or any(
            not 0 <= int(match.group("number")) - 1 < len(records)
            for match in diagnostic_concept_ids
        )
    ):
        return ()
    if (
        "exact_inventory_coverage" in text
        and not diagnostic_qids
        and not explicit_indexes
    ):
        # This token may be accompanied only by aggregate counts. Even topic
        # words in that message are not row-local evidence.
        return ()
    explicit_indexes.update(
        _validation_row_indexes(records, validation_errors)
    )
    # Exact structured diagnostics are authoritative. Do not let a repeated
    # topic/title phrase crowd a late exact row out of the bounded repair set.
    if explicit_indexes:
        if len(explicit_indexes) > max_rows:
            return ()
        return tuple(sorted(explicit_indexes))

    text_key = _normal_key(text)
    for index, row in enumerate(records):
        title_key = _normal_key(
            row.get("concept_title") or row.get("concept"))
        topic_key = _normal_key(row.get("topic"))
        topic_id = str(row.get("_semantic_topic_id") or "").casefold()
        block_ids = [
            str(value or "").casefold()
            for value in row.get("_source_block_ids") or []
        ]
        row_pages = set()
        for field in (
            "_source_page_numbers",
            "_source_pages",
            "source_page_numbers",
            "page_hint",
        ):
            row_pages.update(_page_numbers(row.get(field)))
        if (
            (title_key and title_key in text_key)
            or (topic_key and topic_key in text_key)
            or (topic_id and topic_id in text.casefold())
            or any(block_id and block_id in text.casefold() for block_id in block_ids)
            or bool(failure_pages.intersection(row_pages))
        ):
            indexes.add(index)

    if not indexes and failure_pages:
        topic_keys = _page_implicated_topic_keys(checkpoint, failure_pages)
        indexes.update(
            index
            for index, row in enumerate(records)
            if _normal_key(row.get("topic")) in topic_keys
        )
    if (
        not indexes
        and "exact_inventory_coverage" in text
        and not diagnostic_qids
    ):
        # Count-only coverage diagnostics do not identify a host row and the
        # protected Type/Hub section cannot be edited by this recovery lane.
        # Refuse a topic-wide guess instead of changing the first N rows.
        return ()
    if not indexes and (
        diagnostic_qids
        or "host certification" in text.casefold()
        or "did not certify every source inventory" in text.casefold()
    ):
        indexes.update(_diagnostic_qid_host_indexes(
            checkpoint,
            records,
            diagnostic_qids,
        ))
        topic_keys = _diagnostic_qid_topic_keys(
            checkpoint, diagnostic_qids)
        if not indexes and not topic_keys:
            topic_keys = _missing_certification_topic_keys(checkpoint)
        if not indexes:
            indexes.update(
                index
                for index, row in enumerate(records)
                if _normal_key(row.get("topic")) in topic_keys
            )
    if not indexes:
        indexes.update(_validation_row_indexes(records, validation_errors))
    if not indexes:
        # Last bounded fallback: use lexical overlap, but never shotgun-edit an
        # unrelated chapter.  At least two meaningful title/topic words must
        # occur in the diagnostic.
        diagnostic_words = {
            word for word in text_key.split()
            if len(word) >= 4
        }
        scored: list[tuple[int, int]] = []
        for index, row in enumerate(records):
            row_words = set(_normal_key(
                " ".join([
                    str(row.get("topic") or ""),
                    str(row.get("parent_concept") or ""),
                    str(row.get("concept_title") or ""),
                ])
            ).split())
            score = len(diagnostic_words & row_words)
            if score >= 2:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        indexes.update(index for _score, index in scored[:max_rows])
    if not indexes:
        distinct_topics = {
            _normal_key(row.get("topic")) for row in records
            if _normal_key(row.get("topic"))
        }
        if len(distinct_topics) == 1:
            indexes.update(range(min(len(records), max_rows)))
    return tuple(sorted(indexes)[:max_rows])


def _bounded_source_context(
    source_text: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int,
) -> str:
    source = str(source_text or "")
    if not source or limit <= 0:
        return ""
    windows: list[tuple[int, int]] = []
    radius = max(1200, limit // max(2, len(rows) * 2))
    for row in rows:
        needles = (
            str(row.get("topic") or "").strip(),
            str(row.get("concept_title") or "").strip(),
        )
        for needle in needles:
            if not needle:
                continue
            position = source.casefold().find(needle.casefold())
            if position >= 0:
                windows.append((
                    max(0, position - radius),
                    min(len(source), position + len(needle) + radius),
                ))
                break
    if not windows:
        return source[:limit]
    merged: list[list[int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    fair_share = max(1, limit // max(1, len(merged)))
    pieces: list[str] = []
    used = 0
    for start, end in merged:
        piece = source[start:min(end, start + fair_share)].strip()
        if not piece:
            continue
        remaining = limit - used
        if remaining <= 0:
            break
        pieces.append(piece[:remaining])
        used += min(len(piece), remaining)
    return "\n\n--- SOURCE WINDOW ---\n\n".join(pieces)[:limit]


def _source_owned_tokens(
    row: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    public = " ".join(
        str(row.get(field) or "")
        for field in (
            "topic",
            "parent_concept",
            "concept_title",
            "concept_details",
            "keywords",
        )
    )
    return (
        # Preserve multiplicity as well as identity: removing one of two
        # occurrences or duplicating a source token is also a mutation.
        tuple(sorted(match.upper() for match in _QID_RE.findall(public))),
        tuple(sorted(
            re.sub(r"\s+", " ", match.group(0)).strip()
            for match in _FIGURE_TOKEN_RE.finditer(public)
        )),
        tuple(sorted(
            re.sub(r"\s+", " ", match.group(0)).strip()
            for match in _VISUAL_TOKEN_RE.finditer(public)
        )),
    )


def _is_protected_section(label: str) -> bool:
    key = _normal_key(label).replace(" ", "")
    return (
        key.startswith("types")
        or key.startswith("activityinfohub")
        or key.startswith("activityhub")
    )


def _preserve_source_owned_sections(original: str, candidate: str) -> str:
    # Import lazily so the recovery policy remains usable without initializing
    # the wider service package during isolated tests.
    from . import concept_refiner as cr

    original_sections = cr.split_sections(original)
    candidate_sections = [
        section for section in cr.split_sections(candidate)
        if not _is_protected_section(section[0])
    ]
    protected = [
        section for section in original_sections
        if _is_protected_section(section[0])
    ]
    if not protected:
        # A repair may not introduce Types or Activity/Info Hub material into
        # a row that did not own it in the accepted checkpoint.
        filtered = cr.join_sections(candidate_sections)
        return filtered or original

    result: list[tuple[str, str]] = []
    inserted = False
    for section in candidate_sections:
        if cr.is_learner_analysis_label(section[0]) and not inserted:
            result.extend(protected)
            inserted = True
        result.append(section)
    if not inserted:
        result.extend(protected)
    return cr.join_sections(result)


def _known_topics(
    records: Sequence[Mapping[str, Any]],
    topic_id_by_title: Mapping[str, str] | None,
) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    authoritative = {
        str(title or "").strip(): str(topic_id or "").strip()
        for title, topic_id in (topic_id_by_title or {}).items()
        if str(title or "").strip()
    }
    if authoritative:
        for title, topic_id in authoritative.items():
            out[_normal_key(title)] = (title, topic_id)
        return out
    for row in records:
        title = str(row.get("topic") or "").strip()
        if title:
            out.setdefault(
                _normal_key(title),
                (title, str(row.get("_semantic_topic_id") or "").strip()),
            )
    return out


def _update_method_snapshots(
    checkpoint: dict[str, Any],
    replacements: Mapping[int, tuple[Mapping[str, Any], Mapping[str, Any]]],
) -> None:
    for field in ("method_row_snapshot", "skeleton_method_row_snapshot"):
        entries = checkpoint.get(field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("row"), dict):
                continue
            snapshot_row = entry["row"]
            snapshot_title = _normal_key(snapshot_row.get("concept_title"))
            snapshot_topic = _normal_key(snapshot_row.get("topic"))
            for _index, (before, after) in replacements.items():
                if (
                    snapshot_title == _normal_key(before.get("concept_title"))
                    and snapshot_topic == _normal_key(before.get("topic"))
                ):
                    entry["row"] = copy.deepcopy(dict(after))
                    entry["topic_key"] = _normal_key(after.get("topic"))
                    break


def repair_concept_checkpoint_via_gpt(
    checkpoint_envelope: Mapping[str, Any],
    context: RecoveryContext,
    *,
    api_call: Callable[..., Mapping[str, Any]],
    metadata: Mapping[str, Any],
    source_text: str,
    topic_id_by_title: Mapping[str, str] | None = None,
    validation_errors: Callable[[list[dict[str, Any]]], Sequence[Mapping[str, Any]]]
    | None = None,
    max_rows: int = 12,
    max_source_chars: int = 28_000,
    recovery_stages: Sequence[str] | None = None,
) -> RepairResult | None:
    """Ask GPT to repair only implicated concept/topic rows in a safe checkpoint."""
    base = select_recovery_checkpoint(
        checkpoint_envelope,
        stages=recovery_stages,
    )
    if base is None:
        return None
    records = [
        copy.deepcopy(row)
        for row in base.get("records") or []
        if isinstance(row, dict)
    ]
    indexes = implicated_row_indexes(
        base,
        context.failure,
        validation_errors=validation_errors,
        max_rows=max_rows,
    )
    if not indexes:
        return None
    selected = [records[index] for index in indexes]
    known_topics = _known_topics(records, topic_id_by_title)
    source_context = _bounded_source_context(
        source_text,
        selected,
        limit=max_source_chars,
    )
    rows_payload = [
        {
            "row_index": index,
            "topic": records[index].get("topic", ""),
            "parent_concept": records[index].get("parent_concept", ""),
            "concept_title": records[index].get("concept_title", ""),
            "concept_details": records[index].get("concept_details", ""),
            "keywords": records[index].get("keywords", ""),
            "source_block_ids": list(
                records[index].get("_source_block_ids") or []),
            "source_page_numbers": sorted(
                _page_numbers(
                    records[index].get("_source_page_numbers")
                    or records[index].get("page_hint")
                )
            ),
        }
        for index in indexes
    ]
    pre_learning = (
        str(metadata.get("learning_kind") or "").strip().casefold() == "pre"
    )
    learning_scope = (
        "The supplied rows are prerequisites for the target chapter. The "
        "source evidence describes the target chapter and is provided only "
        "to establish its dependency boundary: keep repaired content to "
        "knowledge learners should already have, and do not copy a concept "
        "that is introduced in the target chapter. "
        if pre_learning
        else
        "The supplied rows teach the target chapter itself. "
    )
    system = (
        "You are the bounded Aegis semantic recovery editor. A prior model "
        "result was rejected, but the source identity and completed checkpoint "
        "are valid. "
        + learning_scope
        + "Repair only the supplied row indexes. Match every concept "
        "to the most accurate allowed textbook topic and correct unsupported, "
        "over-merged, under-specified, or misplaced concept wording using only "
        "the supplied source evidence. Do not add or remove rows. Do not invent "
        "facts. Do not alter source questions, QIDs, Figure references, image "
        "tags, Activity/Info Hub content, or Types/Case/Example content. Keep "
        "grade and board level appropriate. If evidence is insufficient, set "
        "blocked=true instead of guessing. Return JSON only as "
        '{"blocked":false,"reason":"","repairs":[{"row_index":0,'
        '"topic":"","parent_concept":"","concept_title":"",'
        '"concept_details":"","keywords":"","repair_reason":""}]}.'
    )
    user_payload = {
        "metadata": dict(metadata),
        "failure": {
            "exception_type": type(context.failure).__name__,
            "message": str(context.failure)[:6000],
            "attempt": context.attempt,
        },
        "allowed_topics": [
            value[0] for value in known_topics.values()
        ],
        "rows": rows_payload,
        "source_evidence": source_context,
    }
    data = api_call(
        system,
        json.dumps(user_payload, ensure_ascii=False, indent=2),
        purpose="concept_validation",
        single_attempt=True,
    )
    if not isinstance(data, Mapping) or data.get("blocked") is True:
        return None
    repairs = data.get("repairs")
    if not isinstance(repairs, list):
        return None

    allowed_indexes = set(indexes)
    seen_indexes: set[int] = set()
    replacements: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    for repair in repairs:
        if not isinstance(repair, Mapping):
            continue
        row_index = repair.get("row_index")
        if (
            not isinstance(row_index, int)
            or row_index not in allowed_indexes
            or row_index in seen_indexes
        ):
            continue
        seen_indexes.add(row_index)
        before = copy.deepcopy(records[row_index])
        after = copy.deepcopy(before)
        topic = str(repair.get("topic") or before.get("topic") or "").strip()
        topic_entry = known_topics.get(_normal_key(topic))
        if topic and topic_entry is None:
            # Topic names are source graph identity, never free-form model text.
            continue
        if topic_entry is not None:
            after["topic"] = topic_entry[0]
            if topic_entry[1]:
                after["_semantic_topic_id"] = topic_entry[1]
        for field in ("parent_concept", "concept_title", "keywords"):
            value = str(repair.get(field) or "").strip()
            if value:
                after[field] = value
        details = str(repair.get("concept_details") or "").strip()
        if details:
            after["concept_details"] = _preserve_source_owned_sections(
                str(before.get("concept_details") or ""),
                details,
            )
        # Source-owned identifiers and Figures cannot be changed by recovery.
        if _source_owned_tokens(before) != _source_owned_tokens(after):
            continue
        for field in _PROTECTED_PRIVATE_FIELDS:
            if field in before:
                after[field] = copy.deepcopy(before[field])
        topic_changed = (
            _normal_key(before.get("topic"))
            != _normal_key(after.get("topic"))
        )
        if topic_changed:
            for field in (
                "_semantic_scope",
                "_semantic_subtopic_id",
                "_semantic_subtopic_ids",
                "_semantic_subtopic_title",
                "_semantic_topic_confidence",
                "_semantic_topic_contract",
                "_semantic_topic_ids",
            ):
                after.pop(field, None)
            if topic_entry is None or not topic_entry[1]:
                after.pop("_semantic_topic_id", None)
        semantic_fields = (
            "topic",
            "parent_concept",
            "concept_title",
            "concept_details",
            "keywords",
        )
        if any(before.get(field) != after.get(field) for field in semantic_fields):
            # Grounding and subtopic decisions are derived from these fields and
            # must be independently rerun from the retained source graph.
            for field in list(after):
                if field.startswith("_source_grounding_"):
                    after.pop(field, None)
            after.pop("_source_block_ids", None)
            after.pop("_semantic_subtopic_id", None)
            after.pop("_semantic_subtopic_ids", None)
            records[row_index] = after
            replacements[row_index] = (before, after)

    if not replacements:
        return None
    repaired = copy.deepcopy(base)
    repaired["records"] = records
    _update_method_snapshots(repaired, replacements)
    repaired["semantic_recovery"] = {
        "version": 1,
        "attempt": context.attempt,
        "failure_signature": context.failure_signature,
        "changed_row_indexes": sorted(replacements),
        "scope": "checkpoint_rows",
    }
    repair_sig = hashlib.sha256(
        _stable_json({
            "stage": repaired.get("stage"),
            "rows": {
                str(index): records[index] for index in sorted(replacements)
            },
        }).encode("utf-8")
    ).hexdigest()
    return RepairResult(
        checkpoint=repaired,
        base_stage=str(repaired.get("stage") or ""),
        changed_row_indexes=tuple(sorted(replacements)),
        repair_signature=repair_sig,
        scope="checkpoint_rows",
    )


_PRE_STRUCTURAL_STAGES = (
    "pre_derivation_audited",
    "pre_derivation_draft",
)
_PRE_BASE_STAGES = (
    "final_content_ready",
    "post_type_assignment",
    "pre_type_assignment",
    "type_taxonomy_ready",
    "question_inventory",
    "description_method_snapshot",
    "canonical_skeleton",
    "skeleton_complete",
)


def _checkpoint_for_stages(
    checkpoint: Mapping[str, Any],
    stages: Sequence[str],
) -> dict[str, Any] | None:
    entries = checkpoint_entries(checkpoint)
    by_stage = {
        str(entry.get("stage") or ""): entry
        for entry in entries
    }
    for stage in stages:
        candidate = by_stage.get(stage)
        if isinstance(candidate, dict):
            return copy.deepcopy(candidate)
    return None


def _normalize_pre_map(
    value: Any,
    *,
    max_topics: int = 12,
    max_concepts: int = 60,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    raw_topics = value.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        return None
    if len(raw_topics) > max_topics:
        return None
    topics: list[dict[str, Any]] = []
    total_concepts = 0
    seen_topic_names: set[str] = set()
    seen_concept_names: set[str] = set()
    for raw_topic in raw_topics:
        if not isinstance(raw_topic, Mapping):
            return None
        topic_name = str(raw_topic.get("topic_name") or "").strip()
        raw_concepts = raw_topic.get("concepts")
        if (
            not topic_name
            or not isinstance(raw_concepts, list)
            or not raw_concepts
        ):
            return None
        topic_key = _normal_key(topic_name)
        if not topic_key or topic_key in seen_topic_names:
            return None
        seen_topic_names.add(topic_key)
        concepts: list[dict[str, str]] = []
        for raw_concept in raw_concepts:
            if not isinstance(raw_concept, Mapping):
                return None
            concept_name = str(
                raw_concept.get("concept_name") or "").strip()
            concept_description = str(
                raw_concept.get("concept_description") or "").strip()
            if not concept_name or not concept_description:
                return None
            concept_key = _normal_key(concept_name)
            if not concept_key or concept_key in seen_concept_names:
                return None
            seen_concept_names.add(concept_key)
            tag = str(raw_concept.get("tag") or "").strip().upper()
            if tag not in {"FL", "NU", "VC", "RS", "GR"}:
                return None
            concepts.append({
                "concept_name": concept_name,
                "parent_concept": str(
                    raw_concept.get("parent_concept") or topic_name
                ).strip(),
                "concept_description": concept_description,
                "tag": tag,
            })
            total_concepts += 1
            if total_concepts > max_concepts:
                return None
        topics.append({
            "topic_name": topic_name,
            "concepts": concepts,
        })
    return {"topics": topics}


def _pre_map_shape(value: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(
        len(topic.get("concepts") or [])
        for topic in value.get("topics") or []
        if isinstance(topic, Mapping)
    )


def _pre_map_meets_generation_contract(
    value: Mapping[str, Any],
) -> bool:
    shape = _pre_map_shape(value)
    return (
        4 <= len(shape) <= 6
        and all(5 <= concept_count <= 7 for concept_count in shape)
    )


def _pre_map_has_forbidden_source_identity(
    pre_map: Mapping[str, Any],
) -> bool:
    public = _stable_json(pre_map)
    return bool(
        _QID_RE.search(public)
        or _FIGURE_TOKEN_RE.search(public)
        or _VISUAL_TOKEN_RE.search(public)
    )


def _pre_map_repeats_target_concept(
    pre_map: Mapping[str, Any],
    base_records: Sequence[Mapping[str, Any]],
) -> bool:
    target_titles = {
        _normal_key(row.get("concept_title"))
        for row in base_records
        if _normal_key(row.get("concept_title"))
    }
    return any(
        _normal_key(concept.get("concept_name")) in target_titles
        for topic in pre_map.get("topics") or []
        if isinstance(topic, Mapping)
        for concept in topic.get("concepts") or []
        if isinstance(concept, Mapping)
    )


def repair_pre_learning_checkpoint_via_gpt(
    checkpoint_envelope: Mapping[str, Any],
    context: RecoveryContext,
    *,
    api_call: Callable[..., Mapping[str, Any]],
    metadata: Mapping[str, Any],
    source_text: str,
    topic_id_by_title: Mapping[str, str] | None = None,
    validation_errors: Callable[[list[dict[str, Any]]], Sequence[Mapping[str, Any]]]
    | None = None,
    max_rows: int = 12,
    max_source_chars: int = 28_000,
    failure_scope: str = "",
) -> RepairResult | None:
    """Repair pre-learning derivation or its final rows without restarting.

    Post-map semantic failures still use the ordinary concept-row repair.
    Once pre-learning has begun, draft/audited prerequisite maps are repaired
    structurally; final prerequisite rows use the same protected row editor.
    """
    failure_text = str(context.failure or "")
    normalized_scope = str(failure_scope or "").strip().casefold()
    pre_failure = normalized_scope in {"pre_generation", "pre_deposit"}
    post_failure = bool(
        normalized_scope == "post_generation"
        or (
            not normalized_scope
            and re.search(
                r"\b(?:phase\s*3(?:\.\d+)?|concept-ground-\d+|"
                r"topology-concept-\d+|host-concept-\d+|"
                r"source-grounding)\b",
                failure_text,
                re.IGNORECASE,
            )
        )
    )
    if post_failure:
        return repair_concept_checkpoint_via_gpt(
            checkpoint_envelope,
            context,
            api_call=api_call,
            metadata={**dict(metadata), "learning_kind": "Post"},
            source_text=source_text,
            topic_id_by_title=topic_id_by_title,
            validation_errors=validation_errors,
            max_rows=max_rows,
            max_source_chars=max_source_chars,
            recovery_stages=_POST_RECOVERY_STAGES,
        )

    learner_stage = _checkpoint_for_stages(
        checkpoint_envelope, ("pre_learner_analysis",))
    if learner_stage is not None:
        return repair_concept_checkpoint_via_gpt(
            checkpoint_envelope,
            context,
            api_call=api_call,
            metadata=metadata,
            source_text=source_text,
            # Pre-learning topic labels are derived output identities, not
            # source-graph topic identities. Limit them to the retained rows.
            topic_id_by_title={},
            validation_errors=validation_errors,
            max_rows=max_rows,
            max_source_chars=max_source_chars,
            recovery_stages=("pre_learner_analysis",),
        )

    structural = _checkpoint_for_stages(
        checkpoint_envelope, _PRE_STRUCTURAL_STAGES)
    if (
        structural is None
        and not pre_failure
        and not re.search(
            r"\b(?:pre[- ]learning|prerequisite)\b",
            failure_text,
            re.IGNORECASE,
        )
    ):
        # The target chapter map itself failed before pre-learning began.
        return repair_concept_checkpoint_via_gpt(
            checkpoint_envelope,
            context,
            api_call=api_call,
            metadata={**dict(metadata), "learning_kind": "Post"},
            source_text=source_text,
            topic_id_by_title=topic_id_by_title,
            validation_errors=validation_errors,
            max_rows=max_rows,
            max_source_chars=max_source_chars,
            recovery_stages=_POST_RECOVERY_STAGES,
        )

    if structural is not None:
        base = structural
        source_stage = str(base.get("stage") or "")
        source_field = (
            "pre_audited"
            if source_stage == "pre_derivation_audited"
            else "pre_draft"
        )
        current_map = _normalize_pre_map(base.get(source_field))
        base_records = [
            copy.deepcopy(row)
            for row in base.get("records") or []
            if isinstance(row, dict)
        ]
    else:
        base = _checkpoint_for_stages(
            checkpoint_envelope, _PRE_BASE_STAGES)
        if base is None:
            return None
        current_map = None
        base_records = [
            copy.deepcopy(row)
            for row in base.get("records") or []
            if isinstance(row, dict)
        ]
    if not base_records:
        return None

    source_context = _bounded_source_context(
        source_text,
        base_records,
        limit=max_source_chars,
    )
    target_rows = [
        {
            "topic": str(row.get("topic") or "")[:300],
            "parent_concept": str(row.get("parent_concept") or "")[:300],
            "concept_title": str(row.get("concept_title") or "")[:500],
            "concept_details": str(row.get("concept_details") or "")[:1600],
        }
        for row in base_records[:200]
    ]
    system = (
        "You are the bounded Aegis pre-learning recovery editor. The target "
        "chapter source and completed target concept map are valid, but its "
        "derived prerequisite map was rejected. Produce only prerequisite "
        "knowledge learners should already possess before this grade/chapter. "
        "Do not repeat concepts introduced in the target chapter. Do not copy "
        "source QIDs, questions, Figures, images, Activities, or Info Hubs. "
        "Keep the board and grade boundary strict. If the current prerequisite "
        "map is valid but needs correction, retain exactly its topic count and "
        "concept count per topic. If evidence is insufficient, set "
        "blocked=true instead of guessing. Return JSON only as "
        '{"blocked":false,"reason":"","pre_map":{"topics":[{"topic_name":"",'
        '"concepts":[{"concept_name":"","parent_concept":"",'
        '"concept_description":"Description: ... // Misconception/ Error '
        'Analysis: Misconceptions: ...; Error Analysis: ...","tag":"FL"}]}]}}.'
    )
    data = api_call(
        system,
        json.dumps({
            "metadata": dict(metadata),
            "failure": {
                "exception_type": type(context.failure).__name__,
                "message": failure_text[:6000],
                "attempt": context.attempt,
            },
            "target_chapter_concepts": target_rows,
            "current_pre_map": current_map or {},
            "source_evidence": source_context,
        }, ensure_ascii=False, indent=2),
        purpose="pre_learning",
        single_attempt=True,
    )
    if not isinstance(data, Mapping) or data.get("blocked") is True:
        return None
    candidate = _normalize_pre_map(data.get("pre_map"))
    if candidate is None:
        return None
    if (
        current_map is not None
        and _pre_map_meets_generation_contract(current_map)
        and _pre_map_shape(candidate) != _pre_map_shape(current_map)
    ):
        return None
    if not _pre_map_meets_generation_contract(candidate):
        return None
    if (
        _pre_map_has_forbidden_source_identity(candidate)
        or _pre_map_repeats_target_concept(candidate, base_records)
        or (current_map is not None and candidate == current_map)
    ):
        return None

    if structural is None:
        repaired = {
            "schema_version": 3,
            "stage_schema_version": 1,
            "stage": "pre_derivation_draft",
            "stage_order": 90,
            "saved_at": "",
            "progress": 0.985,
            "stage_label": "Pre-learning dependency draft complete",
            "records": base_records,
            "pre_draft": candidate,
        }
    else:
        # Even an audited map is demoted to the draft contract. The ordinary
        # pre-learning path must rerun its independent syllabus auditor before
        # this recovery output can become learner-facing content.
        repaired = {
            "schema_version": 3,
            "stage_schema_version": 1,
            "stage": "pre_derivation_draft",
            "stage_order": 90,
            "saved_at": "",
            "progress": 0.985,
            "stage_label": "Pre-learning dependency draft complete",
            "records": base_records,
            "pre_draft": candidate,
        }
    field = "pre_draft"
    repaired["semantic_recovery"] = {
        "version": 1,
        "attempt": context.attempt,
        "failure_signature": context.failure_signature,
        "changed_units": [field],
        "scope": "pre_learning_map",
    }
    repair_sig = hashlib.sha256(
        _stable_json({
            "stage": repaired.get("stage"),
            "field": field,
            "pre_map": candidate,
        }).encode("utf-8")
    ).hexdigest()
    return RepairResult(
        checkpoint=repaired,
        base_stage=str(repaired.get("stage") or ""),
        changed_row_indexes=(),
        changed_units=(field,),
        repair_signature=repair_sig,
        scope="pre_learning_map",
    )
