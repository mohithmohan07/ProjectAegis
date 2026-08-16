"""Failure classification and the human-pause exception types.

The checkpoint-repair replay machinery that used to live here is retired
with the legacy post-81% lane (docs/phase3-rewrite-spec.md §9, PR 4):
under the rewritten Phase 3 a semantic rejection fails closed with the
real defect, and the release ships everything already decided. What
remains is the surface other modules still legitimately need:

* ``HumanDecisionRequired`` — the pause vehicle for the three pre-81%
  human-decision sites (source review, source-topic recovery, Type
  granularity), which fire before generation spend;
* ``ProviderResponseContractError`` — a mechanical transport failure,
  raised by provider boundaries across the pipeline;
* ``classify_failure`` / ``implicated_row_indexes`` — read-only failure
  projections used by the run report diagnostics.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
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
