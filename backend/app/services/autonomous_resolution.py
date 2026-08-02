"""Two-step, evidence-bounded semantic decision agent.

The agent may select only a server-offered action and target.  Its first call
either resolves the issue or asks the server for exact offered candidate/block
identities.  The server then expands that evidence deterministically and makes
one final call.  It never repeats an unchanged packet, and the orchestration
layer owns durable exactly-once dispatch state.
"""
from __future__ import annotations

import copy
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


RESOLVER_VERSION = "semantic-resolution-agent-4"
_ISSUE_KEY_VERSION = 1
_DEFAULT_MAX_DECISIONS = 100
_DEFAULT_MAX_PATHWAY_TURNS = 24
_DEFAULT_SOURCE_CHARS = 96_000
_MAX_PACKET_CHARS = 320_000
_DEFAULT_RESOLUTION_MODEL = "gpt-5.6-terra"
_RESOLUTION_MODEL_ENV = "AEGIS_AUTONOMOUS_RESOLUTION_MODEL"
_CANDIDATE_WORKSPACE_POLICY = (
    "complete-candidate-catalog-v4:all-opaque-identities;"
    "content-bound-sha256;relevance-ranked-detail;"
    "separate-evidence-and-topology-detail-quotas;"
    "deterministic-one-time-evidence-expansion;compound-evidence-refs;"
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
_DEFAULT_EVIDENCE_CANDIDATE_DETAILS = 18
_DEFAULT_TOPOLOGY_CANDIDATE_DETAILS = 12
_DEFAULT_PENDING_EVIDENCE_ROWS = 24
_MAX_EXPANSION_CANDIDATES = 24
_MAX_EXPANSION_BLOCKS = 32
_MAX_EXPANSION_REFS = 32
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
    supporting_target_ids: tuple[str, ...] = ()

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
    return max(0, min(500, value))


def maximum_pathway_turns() -> int:
    """Return the bounded number of distinct repairs one issue may explore."""

    raw = os.environ.get(
        "AEGIS_AUTONOMOUS_RESOLUTION_MAX_PATHWAY_TURNS",
        str(_DEFAULT_MAX_PATHWAY_TURNS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_PATHWAY_TURNS
    return max(1, min(100, value))


def resolution_model() -> str:
    """Return the high-intelligence planner/solver model for discrepancies."""

    return (
        os.environ.get(_RESOLUTION_MODEL_ENV, _DEFAULT_RESOLUTION_MODEL).strip()
        or _DEFAULT_RESOLUTION_MODEL
    )


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


def _prior_pathway_history(
    checkpoint: Mapping[str, Any] | None,
    pending: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return bounded agent actions that did not finish this semantic scope.

    Only completed actions are included. Dispatch claims and abstentions are
    transport/audit state, not a pathway for the next solver to avoid. The
    resulting rows are deliberately compact but retain the exact offered
    action and target chosen by the prior solver.
    """

    ledger = (
        checkpoint.get("human_decisions")
        if isinstance(checkpoint, Mapping)
        and isinstance(checkpoint.get("human_decisions"), Mapping)
        else {}
    )
    wanted_issue = issue_key(pending)
    rows: list[dict[str, Any]] = []

    def add(review: object, resolution: object = None) -> None:
        if not isinstance(review, Mapping):
            return
        if (
            str(review.get("issue_key") or "") != wanted_issue
            or str(review.get("status") or "") != "resolved"
        ):
            return
        resolved = resolution if isinstance(resolution, Mapping) else {}
        rows.append({
            "resolver_version": str(review.get("resolver_version") or ""),
            "capability_key": str(review.get("capability_key") or ""),
            "choice": str(
                resolved.get("choice") or review.get("choice") or ""
            ),
            "target_id": str(
                resolved.get("target_id") or review.get("target_id") or ""
            ),
            "target_concept_id": str(
                resolved.get("target_concept_id")
                or review.get("target_concept_id")
                or ""
            ),
            "confidence": review.get("confidence", 0.0),
            "reason": _compact_text(review.get("reason"), 800),
            "completed_at": str(review.get("completed_at") or ""),
        })

    for review in ledger.get("agent_review_history") or []:
        add(review)
    for resolution in ledger.get("resolutions") or []:
        if not isinstance(resolution, Mapping):
            continue
        original = resolution.get("pending_decision")
        review = (
            original.get("agent_review")
            if isinstance(original, Mapping)
            else None
        )
        add(review, resolution)

    rows.sort(key=lambda row: (
        row["completed_at"], row["capability_key"], row["choice"],
        row["target_id"], row["target_concept_id"],
    ))
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _sha256_json(row)
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(row)
    return deduplicated[-maximum_pathway_turns():]


def capability_key(
    pending: Mapping[str, Any],
    *,
    checkpoint: Mapping[str, Any] | None = None,
) -> str:
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
    evidence = [
        _stable_json_value(dict(row))
        for row in pending.get("evidence") or []
        if isinstance(row, Mapping)
    ]
    evidence.sort(key=lambda row: json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ))
    item = (
        _stable_json_value(dict(pending.get("item") or {}))
        if isinstance(pending.get("item"), Mapping)
        else {}
    )
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
        # Changed critic conclusions, evidence, item scope, or canonical
        # context are a materially new workspace and may be replanned. Pure
        # list reordering remains immaterial.
        "semantic_workspace": {
            "context_hash": str(pending.get("context_hash") or ""),
            "kind": str(pending.get("kind") or ""),
            "phase": str(pending.get("phase") or ""),
            "conflict": str(pending.get("conflict") or ""),
            "diagnosis": str(pending.get("diagnosis") or ""),
            "decision_question": str(
                pending.get("decision_question")
                or pending.get("question")
                or ""
            ),
            "item": item,
            "evidence": evidence,
        },
        # A repeated issue after one applied action is a pathway turnover, not
        # the same request. Sealing the prior actions lets the next Terra pass
        # choose a different repair without reopening identical-packet loops.
        "prior_pathways": _prior_pathway_history(checkpoint, pending),
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
    return max(8_000, min(240_000, value))


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
    evidence_indexes = [
        index for index in detail_order
        if bindings[index]["action"] == "use_verified_evidence"
        or bool(bindings[index]["source_block_ids"])
    ][:_DEFAULT_EVIDENCE_CANDIDATE_DETAILS]
    topology_indexes = [
        index for index in detail_order
        if bindings[index]["action"] != "use_verified_evidence"
        and not bindings[index]["source_block_ids"]
    ][:_DEFAULT_TOPOLOGY_CANDIDATE_DETAILS]

    def detail(index: int, *, rank: int, detail_kind: str) -> dict[str, Any]:
        raw = raw_candidates[index]
        binding = bindings[index]
        return {
            "relevance_rank": rank,
            "detail_kind": detail_kind,
            "target_id": binding["target_id"],
            "concept_id": binding["concept_id"],
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
        }

    evidence_candidate_details = [
        detail(index, rank=rank, detail_kind="evidence")
        for rank, index in enumerate(evidence_indexes, start=1)
    ]
    topology_candidate_details = [
        detail(index, rank=rank, detail_kind="topology")
        for rank, index in enumerate(topology_indexes, start=1)
    ]
    candidate_details = [
        *evidence_candidate_details,
        *topology_candidate_details,
    ]

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
        "evidence_candidate_detail_target_ids": [
            row["target_id"] for row in evidence_candidate_details
        ],
        "topology_candidate_detail_target_ids": [
            row["target_id"] for row in topology_candidate_details
        ],
        "candidate_detail_quotas": {
            "evidence": _DEFAULT_EVIDENCE_CANDIDATE_DETAILS,
            "topology": _DEFAULT_TOPOLOGY_CANDIDATE_DETAILS,
        },
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
        "prior_agent_pathways": _prior_pathway_history(
            checkpoint, pending
        ),
    }


def build_packet(
    pending: Mapping[str, Any],
    *,
    source_text: str,
    checkpoint: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], set[str]]:
    model_pending = _model_pending_decision(pending)
    prior_pathways = _prior_pathway_history(checkpoint, pending)
    tried_targetless_choices = {
        str(row.get("choice") or "")
        for row in prior_pathways
        if str(row.get("choice") or "")
        and not str(row.get("target_id") or "")
        and not str(row.get("target_concept_id") or "")
    }
    tried_target_ids = {
        str(row.get("target_id") or "")
        for row in prior_pathways
        if str(row.get("target_id") or "")
    }
    tried_concept_ids = {
        str(row.get("target_concept_id") or "")
        for row in prior_pathways
        if str(row.get("target_concept_id") or "")
    }

    def option_was_tried(row: Mapping[str, Any]) -> bool:
        choice = str(row.get("choice") or "")
        target_id = str(row.get("target_id") or "")
        concept_id = str(row.get("target_concept_id") or "")
        if target_id or concept_id:
            return target_id in tried_target_ids or concept_id in tried_concept_ids
        return choice in tried_targetless_choices

    untried_options = [
        row for row in model_pending.get("options") or []
        if not option_was_tried(row)
    ]
    if prior_pathways:
        model_pending["options"] = untried_options
        model_pending["excluded_prior_targetless_choices"] = sorted(
            tried_targetless_choices
        )
        model_pending["excluded_prior_pathways"] = [
            {
                "choice": str(row.get("choice") or ""),
                "target_id": str(row.get("target_id") or ""),
                "target_concept_id": str(
                    row.get("target_concept_id") or ""
                ),
            }
            for row in prior_pathways
        ]
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
        if len(evidence) >= _DEFAULT_PENDING_EVIDENCE_ROWS:
            break
    windows = _mmd_windows(source_text, pending)
    true_mmd_match = any(
        row.get("issue_match") is True for row in windows
    )
    packet = {
        "resolver_version": RESOLVER_VERSION,
        "capability_key": capability_key(
            pending, checkpoint=checkpoint
        ),
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
            "Choose the best safe, source-preserving offered continuation. If "
            "the current packet lacks exact evidence, request the precise "
            "offered candidate, BLK, or evidence identities needed for one "
            "deterministic expansion instead of asking the user."
        ),
        "pending_decision": model_pending,
        "checkpoint_context": _relevant_checkpoint_context(checkpoint, pending),
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
            "prefer_safe_non_destructive_continuation": True,
            "request_evidence_before_human_escalation": True,
            "compound_claims_may_cite_multiple_bound_candidates": True,
            "first_pass_may_request_exact_evidence": True,
            "final_pass_must_choose_best_safe_offered_pathway": True,
            "do_not_repeat_a_prior_ineffective_pathway": True,
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

    evidence_refs = _packet_evidence_refs(packet)
    return packet, evidence_refs


def _packet_evidence_refs(packet: Mapping[str, Any]) -> set[str]:
    """Return only exact evidence identities physically present in a packet."""

    evidence_refs = {
        str(row.get("evidence_id") or "")
        for row in packet.get("source_evidence") or []
        if isinstance(row, Mapping)
        if row.get("evidence_id")
    }
    evidence_refs.update(
        str(row.get("evidence_id") or "")
        for row in packet.get("mmd_windows") or []
        if isinstance(row, Mapping)
        if row.get("evidence_id") and row.get("issue_match") is True
    )
    # A binding hash is selectable evidence only when the packet also exposes
    # that candidate's semantic text. The compact catalog may list 100 opaque
    # identities, but a hash alone cannot prove what an undetailed target says.
    model_pending = packet.get("pending_decision") or {}
    detailed_rows = [
        row
        for row in model_pending.get("candidate_details") or []
        if isinstance(row, Mapping)
    ]
    expansion = packet.get("evidence_expansion")
    if isinstance(expansion, Mapping):
        detailed_rows.extend(
            row
            for row in expansion.get("candidate_details") or []
            if isinstance(row, Mapping)
        )
    evidence_refs.update(
        str(row.get("binding_hash") or "")
        for row in detailed_rows
        if row.get("binding_hash")
        and row.get("server_binding_valid") is not False
    )
    return evidence_refs


def _requested_expansion(
    response: Mapping[str, Any],
    *,
    pending: Mapping[str, Any],
    evidence_refs: set[str],
) -> dict[str, list[str]] | None:
    """Validate a planner's exact, server-expandable evidence request."""

    candidate_ids = {
        str(row.get("target_id") or "")
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping) and row.get("target_id")
    }
    block_ids = {
        block_id
        for row in pending.get("candidates") or []
        if isinstance(row, Mapping)
        for block_id in _candidate_block_ids(row)[0]
        if block_id
    }

    def requested(name: str, allowed: set[str], maximum: int) -> list[str] | None:
        values = list(dict.fromkeys(
            str(value) for value in response.get(name) or [] if str(value)
        ))
        if len(values) > maximum or any(value not in allowed for value in values):
            return None
        return values

    requested_candidates = requested(
        "requested_candidate_ids", candidate_ids, _MAX_EXPANSION_CANDIDATES
    )
    requested_blocks = requested(
        "requested_block_ids", block_ids, _MAX_EXPANSION_BLOCKS
    )
    requested_refs = requested(
        "requested_evidence_refs", evidence_refs, _MAX_EXPANSION_REFS
    )
    if any(value is None for value in (
        requested_candidates, requested_blocks, requested_refs
    )):
        return None
    if not any((requested_candidates, requested_blocks, requested_refs)):
        return None
    return {
        "candidate_ids": requested_candidates or [],
        "block_ids": requested_blocks or [],
        "evidence_refs": requested_refs or [],
    }


def _exact_source_expansion_windows(
    source_text: str,
    *,
    needles: list[str],
    maximum_chars: int = 72_000,
) -> list[dict[str, Any]]:
    """Retrieve every requested exact source neighborhood without fuzzy edits."""

    source = str(source_text or "")
    if not source:
        return []
    folded = source.casefold()
    spans: list[tuple[int, int]] = []
    for needle in list(dict.fromkeys(value for value in needles if value)):
        normalized = _SPACE_RE.sub(" ", needle).strip()
        if not normalized:
            continue
        variants = [normalized]
        words = normalized.split()
        if len(words) > 12:
            variants.extend((" ".join(words[:12]), " ".join(words[-12:])))
        matched_this_needle = False
        for variant in variants:
            start_at = 0
            while len(spans) < 40:
                position = folded.find(variant.casefold(), start_at)
                if position < 0:
                    break
                spans.append((
                    max(0, position - 1_200),
                    min(len(source), position + len(variant) + 3_600),
                ))
                matched_this_needle = True
                start_at = position + max(1, len(variant))
            if matched_this_needle:
                break
        if len(spans) >= 40:
            break
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 400:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    out: list[dict[str, Any]] = []
    used = 0
    for index, (start, end) in enumerate(merged, start=1):
        text = source[start:end]
        if used + len(text) > maximum_chars:
            text = text[:max(0, maximum_chars - used)]
        if not text:
            break
        out.append({
            "evidence_id": f"MMD-WINDOW-EXPANDED-{index:03d}",
            "source_offsets": f"{start}:{start + len(text)}",
            "text": text,
            "issue_match": True,
            "requested_exact_expansion": True,
        })
        used += len(text)
        if used >= maximum_chars:
            break
    return out


def _expand_packet_once(
    packet: Mapping[str, Any],
    *,
    pending: Mapping[str, Any],
    source_text: str,
    request: Mapping[str, list[str]],
) -> tuple[dict[str, Any], set[str]]:
    """Build a different, deterministic final workspace from exact IDs."""

    expanded = copy.deepcopy(dict(packet))
    requested_candidates = set(request.get("candidate_ids") or [])
    requested_blocks = set(request.get("block_ids") or [])
    selected_rows: list[Mapping[str, Any]] = []
    for row in pending.get("candidates") or []:
        if not isinstance(row, Mapping):
            continue
        binding = _candidate_binding(row)
        if (
            binding["target_id"] in requested_candidates
            or requested_blocks.intersection(binding["source_block_ids"])
        ):
            selected_rows.append(row)
    selected_rows = selected_rows[:_MAX_EXPANSION_CANDIDATES]

    expanded_details: list[dict[str, Any]] = []
    source_needles = list(requested_blocks)
    for rank, row in enumerate(selected_rows, start=1):
        binding = _candidate_binding(row)
        coverage = str(row.get("coverage") or "")
        gap = str(row.get("gap") or "")
        source_needles.extend(binding["source_block_ids"])
        source_needles.extend((coverage, gap))
        expanded_details.append({
            "expansion_rank": rank,
            "target_id": binding["target_id"],
            "concept_id": binding["concept_id"],
            "action": binding["action"],
            "source_block_ids": binding["source_block_ids"],
            "binding_hash": binding["binding_hash"],
            "server_binding_valid": binding["server_binding_valid"],
            "text_sha256": binding["text_sha256"],
            "coverage_sha256": binding["coverage_sha256"],
            "coverage": _compact_text(coverage, 4_000),
            "gap_sha256": binding["gap_sha256"],
            "gap": _compact_text(gap, 1_500),
        })

    requested_refs = set(request.get("evidence_refs") or [])
    expanded_source_evidence = []
    for row in expanded.get("source_evidence") or []:
        if not isinstance(row, Mapping):
            continue
        evidence_id = str(row.get("evidence_id") or "")
        if evidence_id in requested_refs or any(
            block_id in str(row.get("text") or "")
            for block_id in requested_blocks
        ):
            expanded_source_evidence.append(dict(row))

    expansion_windows = _exact_source_expansion_windows(
        source_text,
        needles=source_needles,
    )
    existing_offsets = {
        str(row.get("source_offsets") or "")
        for row in expanded.get("mmd_windows") or []
        if isinstance(row, Mapping)
    }
    expansion_windows = [
        row for row in expansion_windows
        if row["source_offsets"] not in existing_offsets
    ]
    expanded["mmd_windows"] = [
        *(expanded.get("mmd_windows") or []),
        *expansion_windows,
    ]
    expanded["source_evidence"] = [
        *(expanded.get("source_evidence") or []),
        *(
            [] if not expanded_source_evidence
            else [{**row, "requested_expansion": True}
                  for row in expanded_source_evidence]
        ),
    ]
    expanded["evidence_expansion"] = {
        "attempt": 1,
        "request": _stable_json_value(dict(request)),
        "candidate_details": expanded_details,
        "source_windows_added": len(expansion_windows),
        "same_packet_retry": False,
        "final_call": True,
    }
    expanded["task"] = (
        "Make the final best safe source-preserving decision. Use multiple "
        "candidate binding hashes when a compound claim requires several "
        "verified blocks. Apply one offered target/action. The server has "
        "already filtered source-changing and user-only actions from this "
        "final workspace, so do not defer the choice."
    )
    expanded["constraints"][
        "first_pass_may_request_exact_evidence"
    ] = False
    expanded["constraints"][
        "final_pass_must_choose_best_safe_offered_pathway"
    ] = True
    model_pending = expanded.get("pending_decision") or {}
    model_pending["legacy_exact_source_matches"] = (
        _legacy_exact_source_matches(pending, expanded["mmd_windows"])
    )
    expanded["pending_decision"] = model_pending
    if len(json.dumps(expanded, ensure_ascii=False, default=str)) > _MAX_PACKET_CHARS:
        expanded["mmd_windows"] = [
            *(packet.get("mmd_windows") or []),
            *_exact_source_expansion_windows(
                source_text,
                needles=source_needles,
                maximum_chars=32_000,
            ),
        ]
    if len(json.dumps(expanded, ensure_ascii=False, default=str)) > _MAX_PACKET_CHARS:
        expanded["evidence_expansion"]["candidate_details"] = (
            expanded_details[:12]
        )
    if len(json.dumps(expanded, ensure_ascii=False, default=str)) > _MAX_PACKET_CHARS:
        raise ValueError("expanded autonomous resolution packet exceeds safety cap")
    return expanded, _packet_evidence_refs(expanded)


def _response_schema(
    pending: Mapping[str, Any],
    evidence_refs: set[str],
    *,
    final: bool = False,
) -> dict[str, Any]:
    candidate_rows = _model_candidate_rows(pending)
    choices = list(dict.fromkeys(
        str(row.get("choice") or "")
        for row in pending.get("options") or []
        if isinstance(row, Mapping)
        and is_automatable_choice(row.get("choice"))
    ))
    excluded_pathways = [
        row for row in pending.get("excluded_prior_pathways") or []
        if isinstance(row, Mapping)
    ]
    excluded_target_ids = {
        str(row.get("target_id") or "")
        for row in excluded_pathways
        if str(row.get("target_id") or "")
    }
    excluded_concept_ids = {
        str(row.get("target_concept_id") or "")
        for row in excluded_pathways
        if str(row.get("target_concept_id") or "")
    }
    all_target_ids = list(dict.fromkeys(
        str(row.get("target_id") or "")
        for row in [*candidate_rows, *(pending.get("options") or [])]
        if isinstance(row, Mapping)
        and row.get("target_id")
    ))
    target_ids = [
        value for value in all_target_ids
        if value not in excluded_target_ids
    ]
    concept_ids = list(dict.fromkeys(
        str(row.get("concept_id") or row.get("target_concept_id") or "")
        for row in [*candidate_rows, *(pending.get("options") or [])]
        if isinstance(row, Mapping)
        and (row.get("concept_id") or row.get("target_concept_id"))
        and str(
            row.get("concept_id") or row.get("target_concept_id") or ""
        ) not in excluded_concept_ids
    ))
    refs = sorted(evidence_refs) or ["NONE"]
    block_ids = list(dict.fromkeys(
        str(block_id)
        for row in candidate_rows
        for block_id in row.get("source_block_ids") or []
        if str(block_id)
    ))
    return {
        "name": "aegis_autonomous_semantic_resolution",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "disposition": {
                    "type": "string",
                    "enum": (
                        ["apply"]
                        if final
                        else ["apply", "request_evidence", "ask_human"]
                    ),
                },
                "choice": {"type": "string", "enum": ["", *choices]},
                "target_id": {
                    "type": "string", "enum": ["", *target_ids]
                },
                "target_concept_id": {
                    "type": "string", "enum": ["", *concept_ids]
                },
                "supporting_target_ids": {
                    "type": "array",
                    "maxItems": min(24, len(all_target_ids)),
                    "items": {
                        "type": "string",
                        # A previously attempted evidence target cannot be the
                        # applied action again, but its exact bound source text
                        # may still support a new compound repair.
                        "enum": all_target_ids or ["NONE"],
                    },
                },
                # Explanatory prose belongs in ``reason``.  Keeping this
                # compatibility field fixed to the empty string makes a
                # malformed provider response fail its strict contract before
                # it can be mistaken for a custom human direction.
                "instruction": {"type": "string", "enum": [""]},
                "confidence": {
                    "type": "number",
                    "minimum": (
                        confidence_policy.minimum(_gate_for(pending))
                        if final else 0
                    ),
                    "maximum": 1,
                },
                "reason": {
                    "type": "string", "minLength": 1, "maxLength": 8000
                },
                "evidence_refs": {
                    "type": "array",
                    "maxItems": min(100, len(refs)),
                    "items": {"type": "string", "enum": refs},
                },
                "uncertainties": {
                    "type": "array", "maxItems": 0 if final else 20,
                    "items": {"type": "string", "maxLength": 1000},
                },
                "requested_candidate_ids": {
                    "type": "array",
                    "maxItems": 0 if final else min(
                        _MAX_EXPANSION_CANDIDATES, len(target_ids)
                    ),
                    "items": {
                        "type": "string", "enum": target_ids or ["NONE"]
                    },
                },
                "requested_block_ids": {
                    "type": "array",
                    "maxItems": 0 if final else min(
                        _MAX_EXPANSION_BLOCKS, len(block_ids)
                    ),
                    "items": {
                        "type": "string", "enum": block_ids or ["NONE"]
                    },
                },
                "requested_evidence_refs": {
                    "type": "array",
                    "maxItems": 0 if final else min(
                        _MAX_EXPANSION_REFS, len(refs)
                    ),
                    "items": {"type": "string", "enum": refs},
                },
            },
            "required": [
                "disposition", "choice", "target_id", "target_concept_id",
                "supporting_target_ids", "instruction", "confidence", "reason",
                "evidence_refs", "uncertainties", "requested_candidate_ids",
                "requested_block_ids", "requested_evidence_refs",
            ],
            "additionalProperties": False,
        },
    }


def _provider_call(
    *,
    packet: dict[str, Any],
    response_schema: dict[str, Any],
) -> dict[str, Any]:
    final_call = bool(packet.get("evidence_expansion"))
    kwargs = dict(
        system=(
            "You are Aegis's autonomous semantic planner and solver. Apply your "
            "domain knowledge to reason about the best pathway, while grounding "
            "every factual output in the supplied evidence and opaque IDs. Do not "
            "repair content, invent a "
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
            "A compound claim may cite multiple exact candidate binding hashes, "
            "but the applied action/target must still be one server-offered safe "
            "continuation. If checkpoint_context.prior_agent_pathways is non-empty, "
            "the same semantic scope reappeared after those actions; diagnose why "
            "they were insufficient and choose a different offered pathway or "
            "target unless newly supplied evidence proves the earlier pathway is "
            "now complete. On the first call, if exact proof is absent, use "
            "request_evidence with precise offered candidate, block, or evidence "
            "IDs; do not ask the user merely because the packet is incomplete. "
            + (
                "This is the final expanded call: choose the best non-destructive "
                "offered pathway. The server has already verified that the "
                "canonical source exists and exposed only bounded, permitted "
                "actions, so return apply and do not defer the choice. "
                if final_call
                else "The server permits exactly one deterministic expansion. "
            )
            +
            "The instruction field must be exactly empty; put every explanation "
            "in reason."
        ),
        prompt=json.dumps(packet, ensure_ascii=False),
        pages=[],
        response_schema=response_schema,
        purpose="semantic_resolution",
        max_tokens=config.OPENAI_MAX_OUTPUT_TOKENS,
        # Provider transport/protocol retries are allowed at the wrapper
        # layer. They do not change the semantic packet or create an agentic
        # pathway loop, and keep a transient API failure from becoming a
        # manual decision.
        single_attempt=False,
    )
    requested_model = resolution_model()
    try:
        return phase22._openai_multimodal_json(
            **kwargs,
            model=requested_model,
        )
    except Exception as exc:
        message = str(exc).casefold()
        primary_model = str(config.OPENAI_MODEL or "").strip()
        unavailable_model = any(marker in message for marker in (
            "model_not_found",
            "model not found",
            "does not exist or you do not have access",
            "not have access to model",
        ))
        if (
            not unavailable_model
            or not primary_model
            or primary_model == requested_model
        ):
            raise
        # A provider rejection before inference is not a semantic retry. Use
        # the configured primary model once; quota/auth/timeout failures never
        # enter this fallback and remain visible to orchestration.
        packet["provider_model_fallback"] = {
            "requested": requested_model,
            "used": primary_model,
            "reason": "requested resolution model unavailable",
        }
        kwargs["prompt"] = json.dumps(packet, ensure_ascii=False)
        return phase22._openai_multimodal_json(
            **kwargs,
            model=primary_model,
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
    supporting_target_ids = tuple(dict.fromkeys(
        str(value)
        for value in response.get("supporting_target_ids") or []
        if str(value)
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
    if any(target not in candidate_target_ids for target in supporting_target_ids):
        return ResolutionResult(
            "escalated",
            "The agent cited a compound-support target that was not offered.",
            confidence,
            refs,
        )
    for supporting_target in supporting_target_ids:
        supporting_binding = candidate_binding_by_target.get(supporting_target)
        if candidate_valid_by_target.get(supporting_target) is False:
            return ResolutionResult(
                "escalated",
                "A compound-support candidate has an invalid or stale binding.",
                confidence,
                refs,
            )
        if supporting_binding and supporting_binding not in refs:
            return ResolutionResult(
                "escalated",
                "Compound evidence was not tied to every exact candidate binding.",
                confidence,
                refs,
            )
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
    if supporting_target_ids:
        model_pending = (
            packet.get("pending_decision")
            if isinstance(packet, Mapping)
            and isinstance(packet.get("pending_decision"), Mapping)
            else {}
        )
        exact_match_catalog = (
            model_pending.get("legacy_exact_source_matches")
            if isinstance(model_pending.get("legacy_exact_source_matches"), Mapping)
            else {}
        )
        refs_by_index = {
            int(row[0]): {str(ref) for ref in row[1] if str(ref)}
            for row in exact_match_catalog.get("rows") or []
            if isinstance(row, list)
            and len(row) == 2
            and isinstance(row[0], int)
            and isinstance(row[1], list)
        }
        for index, candidate in enumerate(pending.get("candidates") or []):
            if not isinstance(candidate, Mapping):
                continue
            if str(candidate.get("target_id") or "") not in supporting_target_ids:
                continue
            binding = _candidate_binding(candidate)
            if (
                binding["binding_origin"] == "resolver_legacy"
                and binding["action"] == "use_verified_evidence"
                and not refs_by_index.get(index, set()).intersection(refs)
            ):
                return ResolutionResult(
                    "escalated",
                    "Compound legacy evidence was not matched to and cited "
                    "from exact canonical source text.",
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
        supporting_target_ids=supporting_target_ids,
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
    """Resolve with at most two semantic calls and no same-packet retry."""

    sealed_patch = verified_source_patch_resolution(pending)
    if sealed_patch is not None:
        return sealed_patch

    if not str(source_text or "").strip():
        return ResolutionResult(
            status="unavailable",
            reason=(
                "The canonical working source is empty, so no source-grounded "
                "autonomous pathway can be applied safely."
            ),
            workspace_hash=capability_key(
                pending, checkpoint=checkpoint
            ),
        )

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
            workspace_hash=capability_key(
                pending, checkpoint=checkpoint
            ),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=0,
        )
    packet: dict[str, Any] | None = None
    final_packet: dict[str, Any] | None = None

    def inspected_count(value: Mapping[str, Any] | None) -> int:
        if not isinstance(value, Mapping):
            return 0
        pending_packet = value.get("pending_decision")
        pending_packet = pending_packet if isinstance(pending_packet, Mapping) else {}
        ids = {
            str(row.get("target_id") or "")
            for row in pending_packet.get("candidate_details") or []
            if isinstance(row, Mapping) and row.get("target_id")
        }
        expansion = value.get("evidence_expansion")
        expansion = expansion if isinstance(expansion, Mapping) else {}
        ids.update(
            str(row.get("target_id") or "")
            for row in expansion.get("candidate_details") or []
            if isinstance(row, Mapping) and row.get("target_id")
        )
        return len(ids)

    def default_expansion_request(
        value: Mapping[str, Any], refs: set[str]
    ) -> dict[str, list[str]] | None:
        model_pending = value.get("pending_decision")
        model_pending = model_pending if isinstance(model_pending, Mapping) else {}
        target_ids = list(dict.fromkeys(
            str(row.get("target_id") or "")
            for row in model_pending.get("candidate_details") or []
            if isinstance(row, Mapping) and row.get("target_id")
        ))[:_MAX_EXPANSION_CANDIDATES]
        if not target_ids:
            target_ids = [
                str(row.get("target_id") or "")
                for row in pending.get("candidates") or []
                if isinstance(row, Mapping) and row.get("target_id")
            ][:_MAX_EXPANSION_CANDIDATES]
        selected = set(target_ids)
        block_ids = list(dict.fromkeys(
            block_id
            for row in pending.get("candidates") or []
            if isinstance(row, Mapping)
            and str(row.get("target_id") or "") in selected
            for block_id in _candidate_block_ids(row)[0]
        ))[:_MAX_EXPANSION_BLOCKS]
        evidence = [
            ref for ref in sorted(refs)
            if ref.startswith(("PENDING-EVIDENCE-", "MMD-WINDOW-"))
        ][:_MAX_EXPANSION_REFS]
        if not any((target_ids, block_ids, evidence)):
            return None
        return {
            "candidate_ids": target_ids,
            "block_ids": block_ids,
            "evidence_refs": evidence,
        }

    try:
        packet, evidence_refs = build_packet(
            pending,
            source_text=source_text,
            checkpoint=checkpoint,
        )
        if not packet["pending_decision"].get("options"):
            return ResolutionResult(
                status="escalated",
                reason=(
                    "Every currently offered autonomous pathway for this "
                    "semantic scope was already applied without finishing "
                    "the issue; Aegis did not repeat one."
                ),
                workspace_hash=_sha256_json(packet),
                offered_candidate_count=offered_candidate_count,
                inspected_candidate_count=inspected_count(packet),
            )
        schema = _response_schema(packet["pending_decision"], evidence_refs)
        raw = (provider or _provider_call)(
            packet=packet,
            response_schema=schema,
        )
        if not isinstance(raw, Mapping):
            raise TypeError("resolution planner returned no structured decision")

        disposition = str(raw.get("disposition") or "")
        first_result = (
            _validate_response(
                raw,
                pending=pending,
                evidence_refs=evidence_refs,
                packet=packet,
            )
            if disposition == "apply"
            else None
        )
        if first_result is not None and first_result.resolved:
            final_packet = packet
            final_refs = evidence_refs
            final_raw = raw
        else:
            request = (
                _requested_expansion(
                    raw,
                    pending=pending,
                    evidence_refs=evidence_refs,
                )
                if disposition == "request_evidence"
                else None
            )
            if request is None and disposition == "apply":
                proposed_target = str(raw.get("target_id") or "")
                proposed_concept = str(
                    raw.get("target_concept_id") or ""
                )
                proposed_candidate_ids = [
                    str(row.get("target_id") or "")
                    for row in pending.get("candidates") or []
                    if isinstance(row, Mapping)
                    and str(row.get("target_id") or "")
                    and (
                        str(row.get("target_id") or "") == proposed_target
                        or (
                            proposed_concept
                            and str(
                                row.get("concept_id")
                                or row.get("target_concept_id")
                                or ""
                            ) == proposed_concept
                        )
                    )
                ]
                if proposed_candidate_ids:
                    request = {
                        "candidate_ids": list(dict.fromkeys(
                            proposed_candidate_ids
                        ))[:_MAX_EXPANSION_CANDIDATES],
                        "block_ids": [],
                        "evidence_refs": [],
                    }
            # A premature abstention *or an invalid/low-confidence first
            # proposal* is not sent to the user. Give Terra a deterministic
            # expansion of the most relevant exact offered identities, then
            # require the final best-safe action.
            request = request or default_expansion_request(packet, evidence_refs)
            if request is None:
                final_packet = packet
                final_refs = evidence_refs
                final_raw = raw
            else:
                final_packet, final_refs = _expand_packet_once(
                    packet,
                    pending=pending,
                    source_text=source_text,
                    request=request,
                )
                if _sha256_json(final_packet) == _sha256_json(packet):
                    raise RuntimeError("evidence expansion did not change workspace")
                final_schema = _response_schema(
                    final_packet["pending_decision"],
                    final_refs,
                    final=True,
                )
                final_raw = (provider or _provider_call)(
                    packet=final_packet,
                    response_schema=final_schema,
                )
                if not isinstance(final_raw, Mapping):
                    raise TypeError("resolution solver returned no structured decision")
                if str(final_raw.get("disposition") or "") == "request_evidence":
                    raise ValueError("final resolution call requested a third pass")
    except Exception as exc:
        used_packet = final_packet or packet
        return ResolutionResult(
            status="unavailable",
            reason=(
                "The bounded autonomous planner/solver could not complete "
                f"({type(exc).__name__}); the checkpoint and source identity "
                "were preserved for an infrastructure-safe resume."
            ),
            workspace_hash=(
                _sha256_json(used_packet) if isinstance(used_packet, Mapping)
                else capability_key(pending, checkpoint=checkpoint)
            ),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=inspected_count(used_packet),
        )
    try:
        result = _validate_response(
            final_raw,
            pending=pending,
            evidence_refs=final_refs,
            packet=final_packet,
        )
        return replace(
            result,
            workspace_hash=_sha256_json(final_packet),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=inspected_count(final_packet),
        )
    except Exception as exc:
        return ResolutionResult(
            status="unavailable",
            reason=(
                "The bounded autonomous review could not be validated "
                f"({type(exc).__name__}). Your saved decision is still available."
            ),
            workspace_hash=_sha256_json(final_packet),
            offered_candidate_count=offered_candidate_count,
            inspected_candidate_count=inspected_count(final_packet),
        )
