"""Shared data contracts for terminal evidence narrowing."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


DISPOSITION_VERSION = "aegis-evidence-narrowing-2-model-critic"

NARROWED_FLAG_FIELD = "_evidence_narrowed"
NARROWED_KIND_FIELD = "_evidence_narrowed_kind"
NARROWED_DROPPED_FIELD = "_evidence_narrowed_dropped_clauses"
NARROWED_EVIDENCE_FIELD = "_evidence_narrowed_block_ids"
NARROWED_NOTE_FIELD = "_evidence_narrowed_note"
NARROWED_ORIGINAL_FIELD = "_evidence_narrowed_original_claim"
NARROWED_CONTRACT_FIELD = "_evidence_narrowed_contract"

NARROWED_CONTRACT = "model-provider-independent-critic-evidence-narrowing-v2"
UNVERIFIED_CONTRACT = "unmodified-terminal-semantic-disposition-v2"

DESCRIPTION_LABEL = "Description"
MAX_DESCRIPTION_CHARS = 12_000
MAX_REASON_CHARS = 4_000
MAX_AUDIT_CLAIMS = 64
MAX_AUDIT_CLAIM_CHARS = 1_000
CACHE_CHUNK_CHARS = 12_000
CACHE_PREFIX = "__aegis_terminal_evidence_narrowing_v2__"

_SPACE_RE = re.compile(r"\s+")


class EvidenceNarrowingError(RuntimeError):
    """The terminal semantic contract could not be executed safely."""


def normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def sha256_json(value: Any) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def plain_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


@dataclass(frozen=True)
class TopicEvidence:
    """Canonical semantic blocks available to one topic."""

    topic_id: str
    topic_title: str
    blocks: tuple[tuple[str, str], ...] = ()

    @property
    def block_ids(self) -> tuple[str, ...]:
        return tuple(block_id for block_id, _text in self.blocks)

    @property
    def empty(self) -> bool:
        return not self.blocks

    def payload(self) -> list[dict[str, str]]:
        return [
            {"block_id": block_id, "text": text}
            for block_id, text in self.blocks
        ]


@dataclass(frozen=True)
class NarrowedRow:
    """One row's terminal disposition, suitable for logs and audit output."""

    concept_title: str
    topic: str
    kind: str
    kept_clauses: tuple[str, ...] = ()
    dropped_clauses: tuple[str, ...] = ()
    evidence_block_ids: tuple[str, ...] = ()
    note: str = ""
    verified: bool = False

    @property
    def changed(self) -> bool:
        # An unchanged-but-unverified row still needs a visible terminal log.
        return self.kind != "unchanged"


@dataclass
class NarrowingAudit:
    """What the terminal disposition did across a complete candidate."""

    version: str = DISPOSITION_VERSION
    context_hash: str = ""
    cache_status: str = ""
    rows: list[NarrowedRow] = field(default_factory=list)

    @property
    def changed_rows(self) -> list[NarrowedRow]:
        return [row for row in self.rows if row.changed]

    def summary(self) -> str:
        rewritten = [
            row for row in self.rows if row.kind in {"narrowed", "restated"}
        ]
        unverified = [
            row for row in self.rows if row.kind == "unmodified_unverified"
        ]
        verified_unchanged = [
            row for row in self.rows if row.kind == "unchanged" and row.verified
        ]
        parts: list[str] = []
        if rewritten:
            parts.append(
                f"{len(rewritten)} concept(s) rewritten by the provider and "
                "independently verified against exact source blocks"
            )
        if verified_unchanged:
            parts.append(
                f"{len(verified_unchanged)} concept(s) independently verified "
                "without a rewrite"
            )
        if unverified:
            parts.append(
                f"{len(unverified)} concept(s) released unchanged and flagged "
                "unverified because no safe semantic decision was available"
            )
        if not parts:
            return "no concept had usable canonical evidence for terminal narrowing"
        if self.cache_status == "accepted_replay":
            parts.append("the accepted disposition was replayed from durable cache")
        return "; ".join(parts)


@dataclass(frozen=True)
class ConceptPacket:
    concept_id: str
    row_index: int
    concept_title: str
    topic: str
    topic_id: str
    description: str
    evidence: TopicEvidence

    def payload(self) -> dict[str, Any]:
        return {
            "concept_id": self.concept_id,
            "concept_title": self.concept_title,
            "topic": self.topic,
            "topic_id": self.topic_id,
            "description": self.description,
            "allowed_evidence_blocks": self.evidence.payload(),
        }
