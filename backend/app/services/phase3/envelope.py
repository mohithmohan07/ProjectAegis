"""The sealed 81% boundary envelope.

The rewritten Phase 3 consumes exactly one input: this envelope,
materialized at the ``pre_type_assignment`` checkpoint. It never reads
live session state. A malformed envelope is a hard error before any
model spend, and a production run without a live model API fails closed
here instead of degrading (spec §10, seam closures 1–2).
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .. import canonical_source_phase3 as phase3_core

ENVELOPE_VERSION = 1

_SEAL_FIELD = "envelope_sha256"

_REQUIRED_FIELDS = (
    "envelope_version",
    "source_contract_hash",
    "metadata",
    "graph",
    "canonical",
    "skeleton_rows",
    "inventory",
    "mined_types",
)


class EnvelopeError(ValueError):
    """The envelope is missing, malformed, or its seal does not verify."""


class LiveApiUnavailable(RuntimeError):
    """No live model API: the run must not start (never degrade silently)."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def seal_sha256(envelope: Mapping[str, Any]) -> str:
    body = {
        key: value
        for key, value in envelope.items()
        if key != _SEAL_FIELD
    }
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def build(
    *,
    graph: Mapping[str, Any],
    canonical: Mapping[str, Any],
    skeleton_rows: list[Mapping[str, Any]],
    inventory: Mapping[str, Any],
    mined_types: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    acsd_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble and seal an envelope from the 81% checkpoint material."""

    envelope: dict[str, Any] = {
        "envelope_version": ENVELOPE_VERSION,
        "source_contract_hash": str(
            (graph or {}).get("source_contract_hash") or ""
        ),
        "metadata": copy.deepcopy(
            dict(metadata or (graph or {}).get("metadata") or {})
        ),
        # The graph is carried VERBATIM: the deposit chain recomputes
        # semantic_topology_sha256 from the ACTIVE graph, whose identity
        # fields (schema/compiler versions, classification mode, source
        # contract) live at the top level. Dropping them here made the
        # sealed topology hash unmatchable (staged run 2).
        "graph": {
            **{
                key: copy.deepcopy(value)
                for key, value in dict(graph or {}).items()
                if key not in ("topics", "subtopics", "blocks")
            },
            "topics": copy.deepcopy(list(graph.get("topics") or [])),
            "subtopics": copy.deepcopy(list(graph.get("subtopics") or [])),
            "blocks": copy.deepcopy(list(graph.get("blocks") or [])),
        },
        "canonical": {
            "blocks": copy.deepcopy(list(canonical.get("blocks") or [])),
        },
        "skeleton_rows": [
            copy.deepcopy(dict(row))
            for row in skeleton_rows
            if isinstance(row, Mapping)
        ],
        "inventory": copy.deepcopy(dict(inventory or {})),
        "mined_types": copy.deepcopy(dict(mined_types or {})),
        "acsd_ledger": copy.deepcopy(dict(acsd_ledger or {})),
    }
    envelope[_SEAL_FIELD] = seal_sha256(envelope)
    validate(envelope)
    return envelope


def validate(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Return the envelope as a plain dict, or raise EnvelopeError."""

    if not isinstance(envelope, Mapping):
        raise EnvelopeError("envelope is not a mapping")
    missing = [
        field
        for field in _REQUIRED_FIELDS
        if field not in envelope
    ]
    if missing:
        raise EnvelopeError(
            "envelope is missing required field(s): " + ", ".join(missing)
        )
    if int(envelope.get("envelope_version") or 0) != ENVELOPE_VERSION:
        raise EnvelopeError(
            "unsupported envelope_version "
            f"{envelope.get('envelope_version')!r}"
        )
    if not str(envelope.get("source_contract_hash") or ""):
        raise EnvelopeError("envelope has no source_contract_hash")
    graph = envelope.get("graph")
    if not isinstance(graph, Mapping) or not graph.get("topics"):
        raise EnvelopeError("envelope graph has no topics")
    canonical = envelope.get("canonical")
    if not isinstance(canonical, Mapping) or not canonical.get("blocks"):
        raise EnvelopeError("envelope canonical has no blocks")
    if not envelope.get("skeleton_rows"):
        raise EnvelopeError("envelope has no skeleton rows")
    recorded = str(envelope.get(_SEAL_FIELD) or "")
    if recorded and recorded != seal_sha256(envelope):
        raise EnvelopeError("envelope seal does not verify")
    return copy.deepcopy(dict(envelope))


def load(path: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EnvelopeError(f"envelope file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"envelope file is not valid JSON: {path}") from exc
    return validate(raw)


def require_live_api() -> None:
    """Fail closed when no live model API is available.

    Spec §10 seam closure 1: the legacy pipeline silently fell back to
    deterministic grounding without an API credential. The rewrite refuses
    to run instead — before any model spend.
    """

    if not phase3_core.semantic_api_enabled():
        raise LiveApiUnavailable(
            "Phase 3 requires a live model API (OpenAI or Gemini credential "
            "with live mode enabled). Refusing to run rather than degrade "
            "to non-API output."
        )
