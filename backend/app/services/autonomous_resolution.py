"""One-shot, evidence-bounded semantic decision agent.

The agent is deliberately narrower than generation.  It may select only a
server-offered action and target, it receives bounded source/checkpoint
evidence, and it must abstain when that evidence is incomplete or ambiguous.
The orchestration layer owns durable exactly-once dispatch state.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from .. import config
from . import canonical_source_phase22 as phase22
from . import early_semantic_gate
from . import generation
from . import semantic_confidence_policy as confidence_policy


RESOLVER_VERSION = "semantic-resolution-agent-3"
_ISSUE_KEY_VERSION = 1
_DEFAULT_MAX_DECISIONS = 6
_DEFAULT_SOURCE_CHARS = 16_000
_MAX_PACKET_CHARS = 48_000
_CANDIDATE_WORKSPACE_POLICY = (
    "complete-candidate-catalog-v3:all-opaque-identities;"
    "content-bound-sha256;relevance-ranked-detail;"
    "critic-mentions-are-retrieval-only;all-topology-actions-visible;"
    "legacy-exact-canonical-source-map-v1;"
    "automatable-actions-only;instruction-must-be-empty-v1"
)
USER_ONLY_CHOICES = frozenset({"replace_source", "custom_instruction"})
AUTOMATABLE_CHOICES = frozenset({
    "expand_existing",
    "create_new",
    "select_existing",
    "accept_recommended",
    "select_candidate",
    "consolidate_types",
    "keep_distinct_types",
})
_DEFAULT_CANDIDATE_DETAILS = 10
_SPACE_RE = re.compile(r"\s+")
_BLOCK_ID_RE = re.compile(r"\bBLK-[A-Za-z0-9_-]+\b")
_CANDIDATE_CATALOG_FIELDS = (
    "target_id",
    "concept_id",
    "action",
    "topic",
    "title",
    "source_block_ids",
    "source_topic_id",
    "target_topic_id",
    "boundary_relation",
    "source_kind",
    "source_page",
    "text_sha256",
    "binding_hash",
    "binding_origin",
    "server_binding_valid",
)


@dataclass(frozen=True)
class ResolutionResult:
    status: str
    reason: str
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()
    choice: str = ""
    instruction: str = ""
    target_id: str = ""
    target_concept_id: str = ""
    workspace_hash: str = ""
    offered_candidate_count: int = 0
    inspected_candidate_count: int = 0

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"


def enabled() -> bool:
    raw = os.environ.get("AEGIS_AUTONOMOUS_RESOLUTION_ENABLED", "1")
    opted_in = raw.strip().lower() in {"1", "true", "yes", "on"}
    return opted_in and config.use_live_generation() and config.has_openai()


def maximum_decisions() -> int:
    raw = os.environ.get(
        "AEGIS_AUTONOMOUS_RESOLUTION_MAX_DECISIONS",
        str(_DEFAULT_MAX_DECISIONS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_DECISIONS
    return max(0, min(25, value))


def issue_key(pending: Mapping[str, Any]) -> str:
    """Identify a semantic scope across regenerated follow-up decision IDs."""

    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    material = {
        # This schema identity is deliberately independent of the resolver or
        # model version. Deploying new code must not rebill an unresolved scope.
        "issue_key_version": _ISSUE_KEY_VERSION,
        "kind": _normal(pending.get("kind")),
        "phase": _normal(pending.get("phase")),
        "item": {
            "unit_id": _normal(item.get("unit_id")),
            "type_id": _normal(item.get("type_id")),
            "qids": sorted({
                _normal(value) for value in item.get("qids") or [] if value
            }),
            "topic": _normal(item.get("topic")),
        },
    }
    return hashlib.sha256(json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def is_automatable_choice(choice: object) -> bool:
    """Whether a server-offered choice may be applied without user input."""

    value = str(choice or "").strip()
    return value in AUTOMATABLE_CHOICES


def _automatable_options(pending: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in pending.get("options") or []
        if isinstance(row, Mapping)
        and is_automatable_choice(row.get("choice"))
    ]


def _compact_text(value: object, limit: int) -> str:
    text = str(value or "").replace("\x00", "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…"


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip().casefold()


def _stable_json_value(value: object) -> Any:
    """Return a deterministic JSON-safe representation without truncation."""

    if isinstance(value, Mapping):
        return {
            str(key): _stable_json_value(raw)
            for key, raw in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(raw) for raw in value]
    if isinstance(value, set):
        return sorted(
            (_stable_json_value(raw) for raw in value),
            key=lambda raw: json.dumps(
                raw, ensure_ascii=False, sort_keys=True, default=str
            ),
        )
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(
        _stable_json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _candidate_action(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("action") or "").strip()
    if explicit:
        return explicit
    title = _normal(row.get("title"))
    if title.startswith("use verified evidence"):
        return "use_verified_evidence"
    for action, prefixes in (
        ("refine", ("refine ",)),
        ("split", ("split ",)),
        ("retire", ("retire ",)),
        ("move", ("move ",)),
        ("keep", ("keep ",)),
    ):
        if title.startswith(prefixes):
            return action
    return "offered_candidate"


def _candidate_block_ids(row: Mapping[str, Any]) -> tuple[list[str], str]:
    raw_block_ids = row.get("source_block_ids") or []
    if isinstance(raw_block_ids, str) or not isinstance(
        raw_block_ids, (list, tuple, set)
    ):
        raw_block_ids = [raw_block_ids]
    explicit = [
        str(value)
        for value in raw_block_ids
        if str(value)
    ]
    if explicit:
        return list(dict.fromkeys(explicit)), "server_field"
    # Older pending-decision payloads expose the block only in the offered
    # title.  Recording that identity is useful, but it is not evidence that
    # the block supports the claim; the bound text and hash below decide that.
    mentioned = _BLOCK_ID_RE.findall(str(row.get("title") or ""))
    return list(dict.fromkeys(mentioned)), (
        "offered_title" if mentioned else "none"
    )


def _candidate_binding(row: Mapping[str, Any]) -> dict[str, Any]:
    block_ids, block_id_origin = _candidate_block_ids(row)
    coverage = str(row.get("coverage") or "")
    gap = str(row.get("gap") or "")
    action = _candidate_action(row)
    material = {
        "offered_candidate": _stable_json_value(dict(row)),
        "derived_binding": {
            "action": action,
            "source_block_ids": block_ids,
            "block_id_origin": block_id_origin,
        },
    }
    workspace_binding_sha256 = _sha256_json(material)
    server_binding_hash = str(row.get("binding_hash") or "")
    server_binding_valid = early_semantic_gate.candidate_binding_is_valid(row)
    effective_binding_hash = (
        server_binding_hash
        if server_binding_hash
        else workspace_binding_sha256
    )
    return {
        # Opaque identities are never shortened. If a pathological set cannot
        # fit the fixed packet budget, the resolver safely becomes unavailable.
        "target_id": str(row.get("target_id") or ""),
        "concept_id": str(
            row.get("concept_id") or row.get("target_concept_id") or ""
        ),
        "action": action,
        "topic": _compact_text(row.get("topic"), 240),
        "title": _compact_text(row.get("title"), 240),
        "source_block_ids": block_ids,
        "block_id_origin": block_id_origin,
        "source_topic_id": str(row.get("source_topic_id") or ""),
        "target_topic_id": str(row.get("target_topic_id") or ""),
        "boundary_relation": str(row.get("boundary_relation") or ""),
        "source_kind": str(row.get("source_kind") or ""),
        "source_page": str(row.get("source_page") or ""),
        "text_sha256": str(row.get("text_sha256") or ""),
        # Preserve the server seal exactly when supplied. Legacy candidates
        # receive a resolver-local full-row seal so every selectable target
        # still has an exact evidence reference.
        "binding_hash": effective_binding_hash,
        "binding_origin": (
            "server"
            if server_binding_hash and server_binding_valid
            else "invalid_server"
            if server_binding_hash
            else "resolver_legacy"
        ),
        "server_binding_valid": bool(server_binding_valid),
        "workspace_binding_sha256": workspace_binding_sha256,
        "coverage_sha256": hashlib.sha256(
            coverage.encode("utf-8")
        ).hexdigest(),
        "coverage_chars": len(coverage),
        "gap_sha256": hashlib.sha256(gap.encode("utf-8")).hexdigest(),
        "gap_chars": len(gap),
    }


def capability_key(pending: Mapping[str, Any]) -> str:
    """Fingerprint the resolver's complete candidate-workspace capability.

    This deliberately excludes the provider/model identity.  A changed key
    means the model was materially given a different bounded workspace (for
    example, v2's complete candidate catalog), not merely that code redeployed.
    Candidate order is immaterial, while every full candidate binding is
    represented by its content hash and exact opaque identities.
    """

    candidates = [
        _candidate_binding(row)
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
    ]
    candidates.sort(key=lambda row: (
        row["target_id"], row["concept_id"], row["action"],
        row["workspace_binding_sha256"],
    ))
    options = [
        _stable_json_value(dict(row))
        for row in _automatable_options(pending)
    ]
    options.sort(key=lambda row: json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ))
    material = {
        "policy": _CANDIDATE_WORKSPACE_POLICY,
        "output_policy": {
            "options": "automatable_only",
            "instruction": "exactly_empty",
            "explanation_field": "reason",
        },
        "packet_max_chars": _MAX_PACKET_CHARS,
        "source_max_chars": _source_limit(),
        "candidate_bindings": candidates,
        "options": options,
    }
    return _sha256_json(material)


def _source_limit() -> int:
    raw = os.environ.get(
        "AEGIS_AUTONOMOUS_RESOLUTION_SOURCE_CHARS",
        str(_DEFAULT_SOURCE_CHARS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_SOURCE_CHARS
    return max(4_000, min(40_000, value))


def _search_needles(pending: Mapping[str, Any]) -> list[str]:
    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    raw: list[object] = [
        # Critic prose is used only to retrieve and expose the exact binding.
        # A BLK mention here never becomes a recommendation by itself.
        pending.get("conflict"), pending.get("diagnosis"),
        pending.get("decision_question"), pending.get("question"),
        item.get("unit_id"), item.get("type_id"), item.get("type_title"),
        item.get("topic"), *(item.get("qids") or []),
        *(item.get("questions") or []),
    ]
    for key in ("evidence", "candidates"):
        for row in pending.get(key) or []:
            if not isinstance(row, Mapping):
                continue
            raw.extend(row.get(field) for field in (
                "label", "text", "target_id", "concept_id", "title",
                "topic", "coverage", "gap", "action",
            ))
            block_ids = row.get("source_block_ids") or []
            raw.extend(
                block_ids
                if isinstance(block_ids, (list, tuple, set))
                else [block_ids]
            )
            if key == "candidates":
                # Legacy decisions may carry the opaque BLK identity only in
                # the candidate title. It is safe to use that exact ID for
                # retrieval, but never as proof that the block supports the
                # claim; validation below still requires an exact text match.
                derived_block_ids, _origin = _candidate_block_ids(row)
                raw.extend(derived_block_ids)
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        text = _SPACE_RE.sub(" ", str(value or "")).strip()
        if len(text) < 5:
            continue
        # Long evidence often differs only in punctuation.  Its first and last
        # distinctive phrases provide useful non-prefix retrieval anchors.
        variants = [text]
        words = text.split()
        if len(words) > 12:
            variants.extend((" ".join(words[:10]), " ".join(words[-10:])))
        for variant in variants:
            key = variant.casefold()
            if key not in seen:
                seen.add(key)
                result.append(variant)
    return result[:80]


def _mmd_windows(
    source_text: str,
    pending: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Retrieve issue-local windows anywhere in the MMD, including its tail."""

    source = str(source_text or "")
    if not source:
        return []
    folded = source.casefold()
    spans: list[tuple[int, int]] = []
    for needle in _search_needles(pending):
        position = folded.find(needle.casefold())
        if position < 0:
            # Whitespace-normalized exact phrases remain bounded and improve
            # matches against line-wrapped MMD without fuzzy guessing.
            words = [re.escape(word) for word in needle.split()[:12] if word]
            if len(words) >= 3:
                match = re.search(r"\s+".join(words), source, flags=re.I)
                position = match.start() if match else -1
        if position < 0:
            continue
        start = max(0, position - 700)
        end = min(len(source), position + max(len(needle), 1) + 900)
        if any(start <= old_end + 200 and end >= old_start - 200
               for old_start, old_end in spans):
            spans = [
                (min(start, old_start), max(end, old_end))
                if start <= old_end + 200 and end >= old_start - 200
                else (old_start, old_end)
                for old_start, old_end in spans
            ]
        else:
            spans.append((start, end))
        if len(spans) >= 10:
            break
    matched_issue = bool(spans)
    if not spans:
        # A small prefix is diagnostic context only; absence of a true match is
        # recorded separately and can never by itself authorize auto-apply.
        spans = [(0, min(len(source), 1_800))]
    budget = _source_limit()
    out: list[dict[str, str]] = []
    used = 0
    for index, (start, end) in enumerate(sorted(spans), start=1):
        text = source[start:end]
        if used + len(text) > budget:
            text = text[:max(0, budget - used)]
        if not text:
            break
        out.append({
            "evidence_id": f"MMD-WINDOW-{index:03d}",
            "source_offsets": f"{start}:{start + len(text)}",
            "text": text,
            "issue_match": matched_issue,
        })
        used += len(text)
        if used >= budget:
            break
    return out


def _bounded_rows(value: object, *, maximum: int, chars: int) -> list[dict]:
    out: list[dict] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, Mapping):
            continue
        row: dict[str, Any] = {}
        for raw_key, raw_value in raw.items():
            key = _compact_text(raw_key, 96)
            if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
                value_to_add: Any = (
                    _compact_text(raw_value, min(600, max(80, chars // 3)))
                    if isinstance(raw_value, str)
                    else raw_value
                )
            else:
                value_to_add = _compact_text(
                    json.dumps(raw_value, ensure_ascii=False, default=str),
                    min(800, max(120, chars // 2)),
                )
            candidate = {**row, key: value_to_add}
            if len(json.dumps(candidate, ensure_ascii=False, default=str)) > chars:
                continue
            row = candidate
        if not row:
            row = {"summary": _compact_text(
                json.dumps(dict(raw), ensure_ascii=False, default=str),
                max(80, chars - 32),
            )}
        out.append(row)
        if len(out) >= maximum:
            break
    return out


def _compact_string_list(
    values: object,
    *,
    maximum: int,
    chars: int,
) -> list[str]:
    return [
        _compact_text(value, chars)
        for value in (values if isinstance(values, list) else [])[:maximum]
    ]


def _candidate_relevance_order(
    pending: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> list[int]:
    """Rank detail retrieval without turning critic mentions into support."""

    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    context = " ".join(str(value or "") for value in (
        pending.get("conflict"), pending.get("diagnosis"),
        pending.get("decision_question"), pending.get("question"),
        item.get("unit_id"), item.get("type_id"), item.get("type_title"),
        item.get("topic"), *(item.get("qids") or []),
        *(item.get("questions") or []),
    ))
    context_folded = context.casefold()
    context_tokens = set(re.findall(r"[\w-]{4,}", context_folded))
    priority_targets = {
        str(row.get("target_id") or "")
        for row in pending.get("options") or []
        if isinstance(row, Mapping) and row.get("target_id")
    }
    priority_concepts = {
        str(row.get("target_concept_id") or "")
        for row in pending.get("options") or []
        if isinstance(row, Mapping) and row.get("target_concept_id")
    }
    scored: list[tuple[int, int]] = []
    for index, row in enumerate(candidates):
        binding = _candidate_binding(row)
        target_id = binding["target_id"]
        concept_id = binding["concept_id"]
        score = 0
        if target_id and target_id in priority_targets:
            score += 1_000_000
        if concept_id and concept_id in priority_concepts:
            score += 900_000
        if target_id and target_id.casefold() in context_folded:
            score += 500_000
        if concept_id and concept_id.casefold() in context_folded:
            score += 400_000
        for block_id in binding["source_block_ids"]:
            if block_id.casefold() in context_folded:
                score += 300_000
        searchable = " ".join(str(row.get(key) or "") for key in (
            "title", "topic", "coverage", "gap", "action"
        )).casefold()
        score += min(10_000, 20 * len(
            context_tokens.intersection(re.findall(r"[\w-]{4,}", searchable))
        ))
        scored.append((score, index))
    return [index for _score, index in sorted(
        scored, key=lambda pair: (-pair[0], pair[1])
    )]


def _model_pending_decision(pending: Mapping[str, Any]) -> dict[str, Any]:
    """Expose every offered identity plus bounded relevance-ranked detail."""

    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    options: list[dict[str, Any]] = []
    for raw in _automatable_options(pending):
        options.append({
            "choice": _compact_text(raw.get("choice"), 64),
            "label": _compact_text(raw.get("label"), 320),
            "recommended": bool(raw.get("recommended")),
            "target_id": str(raw.get("target_id") or ""),
            "target_concept_id": str(
                raw.get("target_concept_id") or ""
            ),
        })

    raw_candidates = [
        row for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
    ]
    bindings = [_candidate_binding(row) for row in raw_candidates]
    # The catalog is materially complete. Coverage/gap prose is kept out of
    # every row so 100+ evidence blocks fit, while the full-content binding
    # hash makes the relevance-ranked detail cryptographically unambiguous.
    candidate_rows = [
        [row[field] for field in _CANDIDATE_CATALOG_FIELDS]
        for row in bindings
    ]
    detail_order = _candidate_relevance_order(pending, raw_candidates)
    candidate_details = []
    for rank, index in enumerate(
        detail_order[:_DEFAULT_CANDIDATE_DETAILS], start=1
    ):
        raw = raw_candidates[index]
        binding = bindings[index]
        candidate_details.append({
            "relevance_rank": rank,
            "target_id": binding["target_id"],
            "binding_hash": binding["binding_hash"],
            "coverage_sha256": binding["coverage_sha256"],
            "coverage_chars": binding["coverage_chars"],
            "coverage": _compact_text(raw.get("coverage"), 1_400),
            "gap_sha256": binding["gap_sha256"],
            "gap_chars": binding["gap_chars"],
            "gap": _compact_text(raw.get("gap"), 600),
            "retrieval_note": (
                "Ranked for inspection only; an ID mentioned by a critic is "
                "not evidence of support. Verify this bound text and hash."
            ),
        })

    return {
        "decision_id": _compact_text(pending.get("decision_id"), 128),
        "context_hash": _compact_text(pending.get("context_hash"), 64),
        "kind": _compact_text(pending.get("kind"), 128),
        "phase": _compact_text(pending.get("phase"), 128),
        "conflict": _compact_text(pending.get("conflict"), 1_200),
        "diagnosis": _compact_text(pending.get("diagnosis"), 1_200),
        "decision_question": _compact_text(
            pending.get("decision_question"), 1_200
        ),
        "checkpoint_progress": pending.get("checkpoint_progress", 0.0),
        "item": {
            "unit_id": _compact_text(item.get("unit_id"), 512),
            "type_id": _compact_text(item.get("type_id"), 512),
            "type_title": _compact_text(item.get("type_title"), 512),
            "qids": _compact_string_list(
                item.get("qids"), maximum=20, chars=160
            ),
            "questions": _compact_string_list(
                item.get("questions"), maximum=6, chars=600
            ),
            "topic": _compact_text(item.get("topic"), 800),
        },
        "candidates": {
            "fields": list(_CANDIDATE_CATALOG_FIELDS),
            "rows": candidate_rows,
            "count": len(candidate_rows),
            "bindings_sha256": _sha256_json(sorted(
                (
                    row["target_id"], row["concept_id"],
                    row["workspace_binding_sha256"],
                )
                for row in bindings
            )),
            "complete": True,
            "detail_order_is_retrieval_not_recommendation": True,
        },
        "candidate_details": candidate_details,
        "deferred_assignment_unit_ids": _compact_string_list(
            pending.get("deferred_assignment_unit_ids"),
            maximum=30,
            chars=128,
        ),
        "options": options,
    }


def _model_candidate_rows(pending: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Decode either the compact v2 catalog or legacy object rows."""

    candidates = pending.get("candidates")
    if isinstance(candidates, Mapping):
        fields = [str(value) for value in candidates.get("fields") or []]
        out: list[dict[str, Any]] = []
        for raw in candidates.get("rows") or []:
            if not isinstance(raw, list) or len(raw) != len(fields):
                continue
            out.append(dict(zip(fields, raw)))
        return out
    return [
        dict(row)
        for row in candidates or []
        if isinstance(row, Mapping)
    ]


def _legacy_exact_source_matches(
    pending: Mapping[str, Any],
    mmd_windows: object,
) -> dict[str, Any]:
    """Bind every legacy evidence candidate to exact transmitted MMD text.

    The compact catalog remains complete even when only a relevance-ranked
    subset has expanded prose.  These zero-based catalog indexes let the model
    and validator identify any legacy candidate whose full saved coverage is
    literally present in a canonical window that survives packet compaction.
    """

    windows = mmd_windows if isinstance(mmd_windows, list) else []
    canonical_rows = [
        row
        for row in windows
        if isinstance(row, Mapping)
        and row.get("issue_match") is True
        and row.get("evidence_id")
    ]
    matches: list[list[Any]] = []
    for index, raw in enumerate(pending.get("candidates") or []):
        if not isinstance(raw, Mapping):
            continue
        binding = _candidate_binding(raw)
        if not (
            binding["binding_origin"] == "resolver_legacy"
            and binding["action"] == "use_verified_evidence"
        ):
            continue
        coverage = _normal(raw.get("coverage"))
        refs = list(dict.fromkeys(
            str(row.get("evidence_id") or "")
            for row in canonical_rows
            if coverage and coverage in _normal(row.get("text"))
        ))
        if refs:
            matches.append([index, refs])
    return {
        "fields": ["catalog_index_zero_based", "canonical_evidence_refs"],
        "rows": matches,
        "meaning": (
            "Each row proves that the legacy candidate's complete saved "
            "coverage occurs verbatim after whitespace normalization in the "
            "listed canonical MMD window transmitted in this packet."
        ),
    }


def _rows_scan_summary(
    rows: object,
    relevant: list[dict[str, Any]],
) -> dict[str, Any]:
    all_rows = [row for row in rows or [] if isinstance(row, Mapping)]
    return {
        "scanned_count": len(all_rows),
        "matched_count": len(relevant),
        "full_sha256": _sha256_json(all_rows),
        "matched_sha256": _sha256_json(relevant),
    }


def _relevant_checkpoint_context(
    checkpoint: Mapping[str, Any] | None,
    pending: Mapping[str, Any],
) -> dict[str, Any]:
    stage = generation._newest_compatible_concept_checkpoint(
        dict(checkpoint or {})
    ) or {}
    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    wanted_qids = {_normal(value) for value in item.get("qids") or [] if value}
    wanted_topic = _normal(item.get("topic"))
    wanted_titles = {
        _normal(row.get("title"))
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("title")
    }

    inventory = stage.get("question_task_inventory")
    inventory_items = (
        inventory.get("items")
        if isinstance(inventory, Mapping) else []
    )
    relevant_inventory = []
    for row in inventory_items or []:
        if not isinstance(row, Mapping):
            continue
        encoded = _normal(json.dumps(dict(row), ensure_ascii=False, default=str))
        if (
            not wanted_qids and not wanted_topic
            or _normal(row.get("qid")) in wanted_qids
            or (wanted_topic and wanted_topic in encoded)
            or any(title and title in encoded for title in wanted_titles)
        ):
            relevant_inventory.append(dict(row))

    mined = stage.get("mined_types")
    types = mined.get("types") if isinstance(mined, Mapping) else []
    relevant_types = []
    wanted_type = _normal(item.get("type_id"))
    for row in types or []:
        if not isinstance(row, Mapping):
            continue
        encoded = _normal(json.dumps(dict(row), ensure_ascii=False, default=str))
        if (
            not wanted_type and not wanted_qids and not wanted_topic
            or (wanted_type and _normal(row.get("type_id")) == wanted_type)
            or any(qid and qid in encoded for qid in wanted_qids)
            or (wanted_topic and wanted_topic in encoded)
        ):
            relevant_types.append(dict(row))

    relevant_records = []
    for row in stage.get("records") or []:
        if not isinstance(row, Mapping):
            continue
        encoded = _normal(json.dumps(dict(row), ensure_ascii=False, default=str))
        if (
            not wanted_topic and not wanted_titles
            or (wanted_topic and wanted_topic in encoded)
            or any(title and title in encoded for title in wanted_titles)
        ):
            relevant_records.append(dict(row))

    return {
        "stage": str(stage.get("stage") or ""),
        "saved_at": str(stage.get("saved_at") or ""),
        "scan_summary": {
            "records": _rows_scan_summary(
                stage.get("records"), relevant_records
            ),
            "question_inventory": _rows_scan_summary(
                inventory_items, relevant_inventory
            ),
            "mined_types": _rows_scan_summary(types, relevant_types),
        },
        "records": _bounded_rows(relevant_records, maximum=4, chars=1_100),
        "question_inventory": _bounded_rows(
            relevant_inventory, maximum=5, chars=900
        ),
        "mined_types": _bounded_rows(
            relevant_types, maximum=4, chars=1_100
        ),
    }


def build_packet(
    pending: Mapping[str, Any],
    *,
    source_text: str,
    checkpoint: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], set[str]]:
    model_pending = _model_pending_decision(pending)
    evidence: list[dict[str, str]] = []
    for index, row in enumerate(pending.get("evidence") or [], start=1):
        if not isinstance(row, Mapping):
            continue
        evidence.append({
            "evidence_id": f"PENDING-EVIDENCE-{index:03d}",
            "label": _compact_text(row.get("label"), 512),
            "page": _compact_text(row.get("page"), 128),
            "text": _compact_text(row.get("text"), 1_200),
        })
        if len(evidence) >= 6:
            break
    windows = _mmd_windows(source_text, pending)
    true_mmd_match = any(
        row.get("issue_match") is True for row in windows
    )
    packet = {
        "resolver_version": RESOLVER_VERSION,
        "capability_key": capability_key(pending),
        "issue_key": issue_key(pending),
        "source_identity": {
            "mmd_sha256": hashlib.sha256(
                str(source_text or "").encode("utf-8")
            ).hexdigest(),
            "mmd_chars": len(str(source_text or "")),
            "checkpoint_fingerprint": str(
                (checkpoint or {}).get("fingerprint") or ""
            ),
        },
        "task": (
            "Resolve only when one offered action is clearly supported by the "
            "supplied evidence. Otherwise ask the user."
        ),
        "pending_decision": model_pending,
        "checkpoint_context": _relevant_checkpoint_context(
            checkpoint, pending
        ),
        "source_evidence": evidence,
        "mmd_windows": windows,
        "evidence_status": {
            "has_pending_evidence": bool(evidence),
            "has_issue_matched_mmd": bool(true_mmd_match),
        },
        "constraints": {
            "choose_only_offered_action": True,
            "choose_only_offered_target": True,
            "preserve_source_wording_qids_figures_topics_and_types": True,
            "never_replace_source_automatically": True,
            "never_invent_custom_instruction": True,
            "critic_id_mentions_are_retrieval_priority_not_support": True,
            "verify_candidate_bound_text_and_hash_before_selection": True,
            "selected_candidate_must_cite_its_binding_hash": True,
            "legacy_candidate_must_have_exact_canonical_source_match": True,
            "abstain_on_uncertainty": True,
        },
    }

    def refresh_legacy_exact_source_matches() -> None:
        exact_matches = _legacy_exact_source_matches(
            pending,
            packet["mmd_windows"],
        )
        packet["pending_decision"][
            "legacy_exact_source_matches"
        ] = exact_matches
        refs_by_index = {
            int(row[0]): [str(ref) for ref in row[1] if str(ref)]
            for row in exact_matches["rows"]
            if isinstance(row, list)
            and len(row) == 2
            and isinstance(row[0], int)
            and isinstance(row[1], list)
        }
        candidate_index_by_target = {
            str(row.get("target_id") or ""): index
            for index, row in enumerate(pending.get("candidates") or [])
            if isinstance(row, Mapping) and row.get("target_id")
        }
        legacy_candidate_indexes = {
            index
            for index, row in enumerate(pending.get("candidates") or [])
            if isinstance(row, Mapping)
            and _candidate_binding(row)["binding_origin"]
            == "resolver_legacy"
            and _candidate_binding(row)["action"]
            == "use_verified_evidence"
        }
        for detail in packet["pending_decision"].get(
            "candidate_details"
        ) or []:
            if not isinstance(detail, dict):
                continue
            index = candidate_index_by_target.get(
                str(detail.get("target_id") or "")
            )
            if index not in legacy_candidate_indexes:
                detail.pop("legacy_exact_source_refs", None)
                detail.pop("legacy_exact_source_match", None)
                continue
            refs = refs_by_index.get(index, []) if index is not None else []
            detail["legacy_exact_source_refs"] = refs
            detail["legacy_exact_source_match"] = bool(refs)

    refresh_legacy_exact_source_matches()

    def packet_chars() -> int:
        return len(json.dumps(packet, ensure_ascii=False, default=str))

    def bound_windows(total: int, per_window: int) -> None:
        remaining = total
        bounded = []
        for row in packet["mmd_windows"]:
            text = _compact_text(row.get("text"), min(per_window, remaining))
            if not text:
                break
            bounded.append({**row, "text": text})
            remaining -= len(text)
            if remaining <= 0:
                break
        packet["mmd_windows"] = bounded
        # Recompute against only the text that will actually reach the model;
        # pre-compaction matches must never survive a truncated source window.
        refresh_legacy_exact_source_matches()

    if packet_chars() > _MAX_PACKET_CHARS:
        packet["checkpoint_context"]["records"] = []
        packet["checkpoint_context"]["question_inventory"] = []
        packet["checkpoint_context"]["mined_types"] = []
    if packet_chars() > _MAX_PACKET_CHARS:
        packet["pending_decision"]["candidate_details"] = [
            {
                **row,
                "coverage": _compact_text(row.get("coverage"), 700),
                "gap": _compact_text(row.get("gap"), 300),
                "retrieval_note": "Retrieval rank is not a recommendation.",
            }
            for row in packet["pending_decision"]["candidate_details"][:8]
        ]
        packet["source_evidence"] = [
            {**row, "text": _compact_text(row.get("text"), 500)}
            for row in packet["source_evidence"][:3]
        ]
        bound_windows(7_000, 2_500)
    if packet_chars() > _MAX_PACKET_CHARS:
        compact_pending = packet["pending_decision"]
        compact_pending["conflict"] = _compact_text(
            compact_pending.get("conflict"), 500
        )
        compact_pending["diagnosis"] = _compact_text(
            compact_pending.get("diagnosis"), 500
        )
        compact_pending["decision_question"] = _compact_text(
            compact_pending.get("decision_question"), 500
        )
        compact_pending["item"]["questions"] = (
            compact_pending["item"]["questions"][:3]
        )
        compact_pending["deferred_assignment_unit_ids"] = []
        compact_pending["candidate_details"] = [
            {
                **row,
                "coverage": _compact_text(row.get("coverage"), 500),
                "gap": _compact_text(row.get("gap"), 200),
                "retrieval_note": "Retrieval rank is not support.",
            }
            for row in compact_pending["candidate_details"][:5]
        ]
        bound_windows(4_000, 2_000)
    if packet_chars() > _MAX_PACKET_CHARS:
        packet["source_evidence"] = [
            {**row, "text": _compact_text(row.get("text"), 300)}
            for row in packet["source_evidence"][:1]
        ]
        packet["pending_decision"]["candidate_details"] = [
            {
                **row,
                "coverage": _compact_text(row.get("coverage"), 300),
                "gap": _compact_text(row.get("gap"), 120),
                "retrieval_note": "Retrieval only.",
            }
            for row in packet["pending_decision"]["candidate_details"][:3]
        ]
        bound_windows(2_000, 2_000)
    if packet_chars() > _MAX_PACKET_CHARS:
        compact_pending = packet["pending_decision"]
        compact_pending["conflict"] = _compact_text(
            compact_pending.get("conflict"), 220
        )
        compact_pending["diagnosis"] = _compact_text(
            compact_pending.get("diagnosis"), 220
        )
        compact_pending["decision_question"] = _compact_text(
            compact_pending.get("decision_question"), 220
        )
        compact_pending["item"]["questions"] = [
            _compact_text(value, 240)
            for value in compact_pending["item"]["questions"][:1]
        ]
        compact_pending["candidate_details"] = [
            {
                **row,
                "coverage": _compact_text(row.get("coverage"), 240),
                "gap": "",
                "retrieval_note": "Retrieval only.",
            }
            for row in compact_pending["candidate_details"][:2]
        ]
        bound_windows(1_200, 1_200)
    if packet_chars() > _MAX_PACKET_CHARS:
        # Catalog identities/bindings, action options, and one matched source
        # window are non-negotiable. Remove only optional prose/details.
        packet["pending_decision"]["candidate_details"] = [
            {
                "relevance_rank": row.get("relevance_rank", 1),
                "target_id": row.get("target_id", ""),
                "binding_hash": row.get("binding_hash", ""),
                "coverage_sha256": row.get("coverage_sha256", ""),
                "coverage_chars": row.get("coverage_chars", 0),
                "coverage": _compact_text(row.get("coverage"), 240),
                "gap_chars": row.get("gap_chars", 0),
                "gap": _compact_text(row.get("gap"), 80),
                "retrieval_note": "Retrieval only; verify catalog binding.",
            }
            for row in packet["pending_decision"]["candidate_details"][:1]
        ]
        packet["source_evidence"] = []
        packet["checkpoint_context"]["records"] = []
        packet["checkpoint_context"]["question_inventory"] = []
        packet["checkpoint_context"]["mined_types"] = []
        bound_windows(600, 600)
    if packet_chars() > _MAX_PACKET_CHARS:
        raise ValueError("autonomous semantic resolution packet exceeds safety cap")

    evidence_refs = {
        str(row.get("evidence_id") or "")
        for row in packet["source_evidence"]
        if row.get("evidence_id")
    }
    evidence_refs.update(
        str(row.get("evidence_id") or "")
        for row in packet["mmd_windows"]
        if row.get("evidence_id") and row.get("issue_match") is True
    )
    # Each exact server (or legacy resolver) binding hash is itself a sealed
    # evidence reference. A
    # candidate-based action must cite this hash as well as canonical MMD for
    # source-critical phases.
    evidence_refs.update(
        str(row.get("binding_hash") or "")
        for row in _model_candidate_rows(packet["pending_decision"])
        if row.get("binding_hash") and row.get("server_binding_valid") is not False
    )
    return packet, evidence_refs


def _response_schema(
    pending: Mapping[str, Any],
    evidence_refs: set[str],
) -> dict[str, Any]:
    candidate_rows = _model_candidate_rows(pending)
    choices = list(dict.fromkeys(
        str(row.get("choice") or "")
        for row in pending.get("options") or []
        if isinstance(row, Mapping)
        and is_automatable_choice(row.get("choice"))
    ))
    target_ids = list(dict.fromkeys(
        str(row.get("target_id") or "")
        for row in [*candidate_rows, *(pending.get("options") or [])]
        if isinstance(row, Mapping) and row.get("target_id")
    ))
    concept_ids = list(dict.fromkeys(
        str(row.get("concept_id") or row.get("target_concept_id") or "")
        for row in [*candidate_rows, *(pending.get("options") or [])]
        if isinstance(row, Mapping)
        and (row.get("concept_id") or row.get("target_concept_id"))
    ))
    refs = sorted(evidence_refs) or ["NONE"]
    return {
        "name": "aegis_autonomous_semantic_resolution",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "disposition": {
                    "type": "string", "enum": ["apply", "ask_human"]
                },
                "choice": {"type": "string", "enum": ["", *choices]},
                "target_id": {
                    "type": "string", "enum": ["", *target_ids]
                },
                "target_concept_id": {
                    "type": "string", "enum": ["", *concept_ids]
                },
                # Explanatory prose belongs in ``reason``.  Keeping this
                # compatibility field fixed to the empty string makes a
                # malformed provider response fail its strict contract before
                # it can be mistaken for a custom human direction.
                "instruction": {"type": "string", "enum": [""]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {
                    "type": "string", "minLength": 1, "maxLength": 8000
                },
                "evidence_refs": {
                    "type": "array",
                    "maxItems": min(100, len(refs)),
                    "items": {"type": "string", "enum": refs},
                },
                "uncertainties": {
                    "type": "array", "maxItems": 20,
                    "items": {"type": "string", "maxLength": 1000},
                },
            },
            "required": [
                "disposition", "choice", "target_id", "target_concept_id",
                "instruction", "confidence", "reason", "evidence_refs",
                "uncertainties",
            ],
            "additionalProperties": False,
        },
    }


def _provider_call(
    *,
    packet: dict[str, Any],
    response_schema: dict[str, Any],
) -> dict[str, Any]:
    return phase22._openai_multimodal_json(
        system=(
            "You are Aegis's bounded semantic resolution agent. Use only the "
            "evidence and opaque IDs supplied. Do not repair content, invent a "
            "target, weaken an integrity rule, or choose source replacement. "
            "A block ID mentioned by a critic is only a retrieval hint, never "
            "proof or a recommendation. Before selecting any candidate, verify "
            "its catalog row, bound candidate text, text_sha256 and binding_hash, "
            "then cite that exact binding_hash in evidence_refs; source-critical "
            "choices must also cite matched canonical MMD evidence. A legacy "
            "candidate is selectable only when its zero-based catalog index "
            "appears in legacy_exact_source_matches, and the response cites one "
            "of that row's canonical evidence refs. The candidate catalog is "
            "complete even when detailed prose is relevance-bounded. "
            "Select apply only when exactly one offered action is clearly "
            "supported; otherwise ask_human and state the unresolved ambiguity. "
            "The instruction field must be exactly empty; put every explanation "
            "in reason."
        ),
        prompt=json.dumps(packet, ensure_ascii=False),
        pages=[],
        response_schema=response_schema,
        purpose="concept_validation",
        max_tokens=3_000,
        single_attempt=True,
    )


def _gate_for(pending: Mapping[str, Any], *, choice: str = ""):
    kind = _normal(pending.get("kind"))
    phase = _normal(pending.get("phase")).replace("phase ", "")
    if (
        any(token in kind for token in (
            "source", "grounding", "blueprint", "topology"
        ))
        or phase in {"3.1", "3.2", "31", "32"}
        or choice == "create_new"
    ):
        return confidence_policy.ConfidenceGate.SOURCE_CRITICAL
    return confidence_policy.ConfidenceGate.SEMANTIC


def _validate_response(
    response: Mapping[str, Any],
    *,
    pending: Mapping[str, Any],
    evidence_refs: set[str],
    packet: Mapping[str, Any] | None = None,
) -> ResolutionResult:
    reason = _compact_text(response.get("reason"), 8_000).strip()
    try:
        confidence = float(response.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    refs = tuple(dict.fromkeys(
        str(value) for value in response.get("evidence_refs") or [] if value
    ))
    uncertainties = [
        str(value).strip()
        for value in response.get("uncertainties") or []
        if str(value).strip()
    ]
    if str(response.get("disposition") or "") != "apply":
        return ResolutionResult(
            status="escalated",
            reason=reason or "The resolution agent found more than one defensible action.",
            confidence=confidence,
            evidence_refs=refs,
        )

    choice = str(response.get("choice") or "")
    instruction = str(response.get("instruction") or "").strip()
    target_id = str(response.get("target_id") or "")
    target_concept_id = str(response.get("target_concept_id") or "")
    offered = {
        str(row.get("choice") or ""): row
        for row in pending.get("options") or []
        if isinstance(row, Mapping) and row.get("choice")
    }
    if choice not in offered:
        return ResolutionResult("escalated", "The agent selected an action that was not offered.")
    if choice in USER_ONLY_CHOICES:
        return ResolutionResult(
            "escalated",
            "This action changes the source or introduces new instructions and requires the user.",
            confidence,
            refs,
        )
    if not is_automatable_choice(choice):
        return ResolutionResult(
            "escalated",
            "The selected action is not approved for autonomous execution.",
            confidence,
            refs,
        )
    if instruction:
        return ResolutionResult(
            "escalated",
            "The agent added an instruction outside the bounded offered action.",
            confidence,
            refs,
        )
    if uncertainties:
        return ResolutionResult(
            "escalated",
            reason or "The resolution agent reported unresolved uncertainty.",
            confidence,
            refs,
        )
    if not refs or any(ref not in evidence_refs for ref in refs):
        return ResolutionResult(
            "escalated",
            "The proposed action was not tied to supplied evidence.",
            confidence,
            refs,
        )
    gate = _gate_for(pending, choice=choice)
    if (
        gate is confidence_policy.ConfidenceGate.SOURCE_CRITICAL
        and not any(ref.startswith("MMD-WINDOW-") for ref in refs)
    ):
        return ResolutionResult(
            "escalated",
            "This source-critical action lacks issue-matched canonical MMD evidence.",
            confidence,
            refs,
        )
    if not confidence_policy.accepts(confidence, gate):
        threshold = confidence_policy.threshold_text(gate)
        return ResolutionResult(
            "escalated",
            reason or f"Confidence did not meet the {threshold} safety threshold.",
            confidence,
            refs,
        )

    option = offered[choice]
    candidate_target_ids = {
        str(row.get("target_id") or "")
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("target_id")
    }
    candidate_concept_ids = {
        str(row.get("concept_id") or "")
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("concept_id")
    }
    candidate_binding_by_target = {
        str(row.get("target_id") or ""): _candidate_binding(row)[
            "binding_hash"
        ]
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("target_id")
    }
    candidate_binding_by_concept = {
        str(row.get("concept_id") or row.get("target_concept_id") or ""):
        _candidate_binding(row)["binding_hash"]
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
        and (row.get("concept_id") or row.get("target_concept_id"))
    }
    candidate_valid_by_target = {
        str(row.get("target_id") or ""): _candidate_binding(row)[
            "server_binding_valid"
        ]
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("target_id")
    }
    candidate_valid_by_concept = {
        str(row.get("concept_id") or row.get("target_concept_id") or ""):
        _candidate_binding(row)["server_binding_valid"]
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
        and (row.get("concept_id") or row.get("target_concept_id"))
    }
    if choice == "accept_recommended":
        expected = str(option.get("target_id") or "")
        if target_id and target_id != expected:
            return ResolutionResult("escalated", "The recommended target identity changed.")
        target_id = expected
    if choice in {"accept_recommended", "select_candidate"}:
        if not target_id or target_id not in candidate_target_ids:
            return ResolutionResult(
                "escalated",
                "The selected target is not a supplied candidate.",
            )
    if choice == "expand_existing" and not target_concept_id:
        target_concept_id = str(option.get("target_concept_id") or "")
    if choice in {"expand_existing", "select_existing"}:
        if not target_concept_id or target_concept_id not in candidate_concept_ids:
            return ResolutionResult(
                "escalated",
                "The selected concept is not a supplied candidate.",
            )
    selected_binding = (
        candidate_binding_by_target.get(target_id)
        if choice in {"accept_recommended", "select_candidate"}
        else candidate_binding_by_concept.get(target_concept_id)
        if choice in {"expand_existing", "select_existing"}
        else None
    )
    selected_binding_valid = (
        candidate_valid_by_target.get(target_id)
        if choice in {"accept_recommended", "select_candidate"}
        else candidate_valid_by_concept.get(target_concept_id)
        if choice in {"expand_existing", "select_existing"}
        else True
    )
    selected_candidate_index = next(
        (
            index
            for index, row in enumerate(pending.get("candidates") or [])
            if isinstance(row, Mapping)
            and (
                str(row.get("target_id") or "") == target_id
                if choice in {"accept_recommended", "select_candidate"}
                else str(
                    row.get("concept_id")
                    or row.get("target_concept_id")
                    or ""
                ) == target_concept_id
            )
        ),
        None,
    )
    selected_candidate = (
        (pending.get("candidates") or [])[selected_candidate_index]
        if isinstance(selected_candidate_index, int)
        else None
    )
    if selected_binding_valid is False:
        return ResolutionResult(
            "escalated",
            "The selected candidate's exact server binding is invalid or stale.",
            confidence,
            refs,
        )
    if selected_binding and selected_binding not in refs:
        return ResolutionResult(
            "escalated",
            "The selected candidate was not tied to its exact bound text hash.",
            confidence,
            refs,
        )
    if isinstance(selected_candidate, Mapping):
        selected_candidate_binding = _candidate_binding(selected_candidate)
        if (
            selected_candidate_binding["binding_origin"]
            == "resolver_legacy"
            and selected_candidate_binding["action"]
            == "use_verified_evidence"
        ):
            model_pending = (
                packet.get("pending_decision")
                if isinstance(packet, Mapping)
                and isinstance(packet.get("pending_decision"), Mapping)
                else {}
            )
            exact_match_catalog = (
                model_pending.get("legacy_exact_source_matches")
                if isinstance(
                    model_pending.get("legacy_exact_source_matches"),
                    Mapping,
                )
                else {}
            )
            exact_refs = {
                str(ref)
                for row in exact_match_catalog.get("rows") or []
                if isinstance(row, list)
                and len(row) == 2
                and row[0] == selected_candidate_index
                and isinstance(row[1], list)
                for ref in row[1]
                if str(ref)
            }
            if not exact_refs.intersection(refs):
                return ResolutionResult(
                    "escalated",
                    "The legacy BLK candidate's exact saved text was not "
                    "matched to and cited from canonical MMD evidence.",
                    confidence,
                    refs,
                )
    if not reason:
        return ResolutionResult(
            "escalated",
            "The proposed action did not include an evidence-based reason.",
        )
    return ResolutionResult(
        status="resolved",
        reason=reason,
        confidence=confidence,
        evidence_refs=refs,
        choice=choice,
        target_id=target_id,
        target_concept_id=target_concept_id,
    )


def verified_source_patch_resolution(
    pending: Mapping[str, Any],
) -> ResolutionResult | None:
    """Apply a unique server-sealed working-source patch without a GPT call."""

    item = pending.get("item") if isinstance(pending.get("item"), Mapping) else {}
    patch = (
        pending.get("source_patch")
        if isinstance(pending.get("source_patch"), Mapping)
        else None
    )
    if (
        _normal(pending.get("kind")) != "phase3_source_graph_review"
        or _normal(item.get("type_id")) != "numbered_main_topic_coverage"
        or patch is None
    ):
        return None
    material = {
        key: patch.get(key)
        for key in (
            "version", "kind", "target", "raw_source_mutated",
            "source_contract_hash", "semantic_context_hash", "before_sha256",
            "after_sha256", "operations",
        )
    }
    patch_hash = str(patch.get("patch_hash") or "")
    target_id = str(patch.get("target_id") or "")
    expected_target = f"canonical-topic-patch-{patch_hash[:24]}"
    digests = [
        str(patch.get(key) or "")
        for key in (
            "source_contract_hash", "semantic_context_hash", "before_sha256",
            "after_sha256", "patch_hash",
        )
    ]
    if not (
        patch.get("verified") is True
        and patch.get("raw_source_mutated") is False
        and patch.get("version") == "phase3-canonical-topic-patch-1"
        and patch.get("kind") == "canonical_topic_binding"
        and patch.get("target") == "working_derived_source"
        and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in digests)
        and patch_hash == hashlib.sha256(json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        and target_id == expected_target
        and bool(patch.get("operations"))
    ):
        return ResolutionResult(
            "escalated",
            "The working-source patch seal is incomplete or stale; choose how to proceed.",
        )
    candidates = [
        row for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
        and str(row.get("target_id") or "") == target_id
    ]
    options = [
        row for row in pending.get("options") or []
        if isinstance(row, Mapping)
        and row.get("choice") == "accept_recommended"
        and str(row.get("target_id") or "") == target_id
    ]
    if len(candidates) != 1 or len(options) != 1:
        return ResolutionResult(
            "escalated",
            "The verified source patch is not the unique offered recommendation.",
        )
    return ResolutionResult(
        status="resolved",
        reason=(
            "Aegis verified one hash-sealed canonical-topic patch that changes "
            "only the derived working MMD and preserves the raw source identity."
        ),
        confidence=1.0,
        evidence_refs=(f"CANONICAL-PATCH-{patch_hash[:24].upper()}",),
        choice="accept_recommended",
        target_id=target_id,
    )


def resolve_pending(
    pending: Mapping[str, Any],
    *,
    source_text: str,
    checkpoint: Mapping[str, Any] | None,
    provider: Callable[..., Mapping[str, Any]] | None = None,
) -> ResolutionResult:
    """Make one physical provider request and deterministically vet its action."""

    sealed_patch = verified_source_patch_resolution(pending)
    if sealed_patch is not None:
        return sealed_patch

    offered_candidate_count = len([
        row for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
    ])
    if not _automatable_options(pending):
        return ResolutionResult(
            status="escalated",
            reason=(
                "This decision has no action approved for autonomous "
                "execution and requires the user."
            ),
            workspace_hash=capability_key(pending),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=0,
        )
    packet: dict[str, Any] | None = None
    try:
        packet, evidence_refs = build_packet(
            pending,
            source_text=source_text,
            checkpoint=checkpoint,
        )
        schema = _response_schema(packet["pending_decision"], evidence_refs)
        raw = (provider or _provider_call)(
            packet=packet,
            response_schema=schema,
        )
    except Exception as exc:
        return ResolutionResult(
            status="unavailable",
            reason=(
                "The one bounded autonomous review could not complete "
                f"({type(exc).__name__}). Your saved decision is still available."
            ),
            workspace_hash=(
                _sha256_json(packet) if isinstance(packet, Mapping)
                else capability_key(pending)
            ),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=(
                len(packet["pending_decision"].get("candidate_details") or [])
                if isinstance(packet, Mapping) else 0
            ),
        )
    if not isinstance(raw, Mapping):
        return ResolutionResult(
            status="unavailable",
            reason="The autonomous review returned no structured decision.",
            workspace_hash=_sha256_json(packet),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=len(
                packet["pending_decision"].get("candidate_details") or []
            ),
        )
    try:
        result = _validate_response(
            raw,
            pending=pending,
            evidence_refs=evidence_refs,
            packet=packet,
        )
        return replace(
            result,
            workspace_hash=_sha256_json(packet),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=len(
                packet["pending_decision"].get("candidate_details") or []
            ),
        )
    except Exception as exc:
        return ResolutionResult(
            status="unavailable",
            reason=(
                "The bounded autonomous review could not be validated "
                f"({type(exc).__name__}). Your saved decision is still available."
            ),
            workspace_hash=_sha256_json(packet),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=len(
                packet["pending_decision"].get("candidate_details") or []
            ),
        )
