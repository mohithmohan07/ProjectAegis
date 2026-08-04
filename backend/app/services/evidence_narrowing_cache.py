"""Portable exactly-once cache for terminal evidence narrowing."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .. import config
from .evidence_narrowing_types import (
    CACHE_CHUNK_CHARS,
    CACHE_PREFIX,
    DISPOSITION_VERSION,
    EvidenceNarrowingError,
    plain_json,
)


# Phase 3.8 holds a local state reference while its persistence helper swaps the
# active store to detached JSON copies. Mirror that original reference so the
# final terminal-status write cannot erase a later accepted disposition.
_PHASE38_STATE_MIRROR: ContextVar[dict[str, Any] | None] = ContextVar(
    "aegis_evidence_narrowing_phase38_state_mirror", default=None
)


def bind_state_mirror() -> Any:
    return _PHASE38_STATE_MIRROR.set(None)


def reset_state_mirror(token: Any) -> None:
    _PHASE38_STATE_MIRROR.reset(token)


def _cache_root(cache_dir: Path | str | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    return Path(config.DATA_DIR) / "evidence-narrowing-cache"


def _cache_path(context_hash: str, cache_dir: Path | str | None) -> Path:
    return _cache_root(cache_dir) / f"{context_hash}.json"


def _disk_read(context_hash: str, cache_dir: Path | str | None) -> dict[str, Any] | None:
    path = _cache_path(context_hash, cache_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if (
        value.get("version") != DISPOSITION_VERSION
        or value.get("context_hash") != context_hash
    ):
        return None
    return value


def _disk_write(
    context_hash: str,
    value: dict[str, Any],
    cache_dir: Path | str | None,
) -> bool:
    root = _cache_root(cache_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{context_hash}.", suffix=".tmp", dir=str(root)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, _cache_path(context_hash, cache_dir))
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        return True
    except OSError:
        return False


def _phase38_feedback() -> tuple[Any, dict[str, Any], dict[str, str]] | None:
    """Return the active durable Phase 3.8 state without an import cycle."""

    try:
        from . import (  # local import: Phase 3.8 imports the public facade
            canonical_source_phase38_boundary_grounding_turnover_contract
            as phase38,
        )
        active = phase38._ACTIVE_CONVERGENCE_STORE.get()
    except Exception:
        return None
    if not isinstance(active, dict) or not isinstance(active.get("state"), dict):
        return None
    state = active["state"]
    if _PHASE38_STATE_MIRROR.get() is None:
        _PHASE38_STATE_MIRROR.set(state)
    feedback = {
        str(key): str(value)
        for key, value in (state.get("feedback") or {}).items()
        if str(key)
    }
    return phase38, state, feedback


def _feedback_prefix(context_hash: str) -> str:
    return f"{CACHE_PREFIX}:{context_hash}"


def _checkpoint_read(context_hash: str) -> dict[str, Any] | None:
    active = _phase38_feedback()
    if active is None:
        return None
    _phase38, _state, feedback = active
    prefix = _feedback_prefix(context_hash)
    try:
        meta = json.loads(feedback[f"{prefix}:meta"])
        count = int(meta.get("chunks") or 0)
        if count < 1 or count > 200:
            return None
        text = "".join(feedback[f"{prefix}:{index}"] for index in range(count))
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != meta.get("sha256"):
            return None
        value = json.loads(text)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if (
        value.get("version") != DISPOSITION_VERSION
        or value.get("context_hash") != context_hash
    ):
        return None
    return value


def _checkpoint_write(context_hash: str, value: dict[str, Any]) -> bool:
    active = _phase38_feedback()
    if active is None:
        return False
    phase38, state, feedback = active
    prefix = _feedback_prefix(context_hash)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    chunks = [
        text[index:index + CACHE_CHUNK_CHARS]
        for index in range(0, len(text), CACHE_CHUNK_CHARS)
    ] or [""]
    if len(chunks) > 200:
        return False

    feedback = {
        key: raw for key, raw in feedback.items()
        if not key.startswith(CACHE_PREFIX + ":")
    }
    feedback[f"{prefix}:meta"] = json.dumps({
        "chunks": len(chunks),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }, sort_keys=True)
    for index, chunk in enumerate(chunks):
        feedback[f"{prefix}:{index}"] = chunk

    targets = [state]
    mirror = _PHASE38_STATE_MIRROR.get()
    if isinstance(mirror, dict) and mirror is not state:
        targets.append(mirror)
    for target in targets:
        target["feedback"] = copy.deepcopy(feedback)
        issue_key = str(target.get("active_issue_key") or "")
        buckets = dict(target.get("issue_buckets") or {})
        if issue_key and isinstance(buckets.get(issue_key), dict):
            bucket = copy.deepcopy(buckets[issue_key])
            bucket["feedback"] = copy.deepcopy(feedback)
            buckets[issue_key] = bucket
            target["issue_buckets"] = buckets
    try:
        phase38._persist_convergence_state(state)
    except Exception:
        return False
    return True


def _ledger_rank(value: dict[str, Any]) -> int:
    return {
        "accepted": 100,
        "unmodified": 90,
        "critic_returned": 80,
        "provider_returned": 70,
        "critic_started": 60,
        "provider_started": 50,
        "critic_pending": 40,
        "provider_pending": 30,
        "new": 0,
    }.get(str(value.get("status") or ""), -1)


def load_ledger(
    context_hash: str,
    cache_dir: Path | str | None,
) -> dict[str, Any] | None:
    candidates = [
        value for value in (
            _checkpoint_read(context_hash),
            _disk_read(context_hash, cache_dir),
        )
        if isinstance(value, dict)
    ]
    if not candidates:
        return None
    return max(candidates, key=_ledger_rank)


def persist_ledger(
    context_hash: str,
    value: dict[str, Any],
    cache_dir: Path | str | None,
    *,
    required: bool,
) -> None:
    detached = plain_json(value)
    checkpoint_required = _phase38_feedback() is not None
    checkpoint_ok = _checkpoint_write(context_hash, detached)
    disk_ok = _disk_write(context_hash, detached, cache_dir)
    if not required:
        return
    if checkpoint_required and not checkpoint_ok:
        raise EvidenceNarrowingError(
            "terminal semantic disposition could not persist its portable "
            "Phase 3.8 exactly-once claim before model transport"
        )
    if not checkpoint_required and not disk_ok:
        raise EvidenceNarrowingError(
            "terminal semantic disposition could not persist its local "
            "exactly-once claim before model transport"
        )
