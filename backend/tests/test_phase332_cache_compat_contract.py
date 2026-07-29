"""Regression coverage for Phase 3.3-compatible Phase 3.2 cache reuse."""
from __future__ import annotations

import copy

from app.services import canonical_source_phase32_topology_adjudication_contract as phase32
from app.services import canonical_source_phase33_preflight_contract as phase33


def _normal_record() -> dict:
    return {
        "topic": "The Making of Nationalism in Europe",
        "parent_concept": "Economic Nationalism",
        "concept_title": "Economic Integration through the Zollverein",
        "concept_details": "Description: Tariff barriers were removed.",
    }


def test_pre_phase33_whole_topology_cache_is_not_reused(monkeypatch):
    monkeypatch.setattr(phase32, "_read_cached_records", lambda _key: [_normal_record()])
    # The installed adapter is already active on the production module. Reinstall
    # is intentionally idempotent, so exercise the wrapper captured at import by
    # checking that an unmarked legacy row is rejected.
    from app.services import canonical_source_phase332_cache_compat_contract as compat

    # Isolate the adapter logic around a synthetic legacy reader.
    original_version = getattr(phase32, "_PHASE332_CACHE_COMPAT_VERSION", 0)
    monkeypatch.setattr(phase32, "_PHASE332_CACHE_COMPAT_VERSION", 0)
    compat.install()
    try:
        assert phase32._read_cached_records("legacy") is None
    finally:
        monkeypatch.setattr(
            phase32,
            "_PHASE332_CACHE_COMPAT_VERSION",
            original_version,
        )


def test_marked_cache_is_bypassed_while_grounding_feedback_is_active(monkeypatch):
    from app.services import canonical_source_phase332_cache_compat_contract as compat

    marked = _normal_record()
    marked[compat._CACHE_FIELD] = compat._CACHE_MARKER
    monkeypatch.setattr(phase32, "_read_cached_records", lambda _key: [copy.deepcopy(marked)])
    original_version = getattr(phase32, "_PHASE332_CACHE_COMPAT_VERSION", 0)
    monkeypatch.setattr(phase32, "_PHASE332_CACHE_COMPAT_VERSION", 0)
    compat.install()
    token = phase33._EXTERNAL_GROUNDING_FEEDBACK.set({"TOPOLOGY-CONCEPT-0001": "reject"})
    try:
        assert phase32._read_cached_records("marked") is None
    finally:
        phase33._EXTERNAL_GROUNDING_FEEDBACK.reset(token)
        monkeypatch.setattr(
            phase32,
            "_PHASE332_CACHE_COMPAT_VERSION",
            original_version,
        )
