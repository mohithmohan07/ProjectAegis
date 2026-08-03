"""Make the Phase 3.2 whole-topology cache compatible with Phase 3.3.

Phase 3.3 changes production keep/move semantics and may deliberately feed an
exact-grounding rejection back into topology adjudication. A Phase 3.2 cache
written before that contract must not bypass the new projection or the bounded
retry. This adapter invalidates legacy whole-topology entries once, marks newly
written entries, and bypasses even a marked cache while Phase 3.3 is carrying
external grounding feedback. Per-concept Phase 3.3 decision caches still avoid
repeating accepted model calls.
"""
from __future__ import annotations

import copy
from typing import Any

from . import canonical_source_phase32_topology_adjudication_contract as phase32
from . import canonical_source_phase33_preflight_contract as phase33
from . import concept_refiner as cr

_CONTRACT_VERSION = 2
_CACHE_MARKER = "phase3.3-projected-topology-cache-2-placement-policy"
_CACHE_FIELD = "_phase33_phase32_cache_contract"


def install(_generation: Any | None = None) -> None:
    if (
        getattr(phase32, "_PHASE332_CACHE_COMPAT_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original_read = phase32._read_cached_records
    original_write = phase32._write_cached_records

    def read_cached_records(cache_key: str):
        if phase33._EXTERNAL_GROUNDING_FEEDBACK.get():
            return None
        records = original_read(cache_key)
        if not isinstance(records, list):
            return None
        normal = [
            row for row in records
            if isinstance(row, dict)
            and not cr.is_culmination(
                str(row.get("concept_title") or row.get("concept") or "")
            )
        ]
        if normal and any(row.get(_CACHE_FIELD) != _CACHE_MARKER for row in normal):
            return None
        clean = copy.deepcopy(records)
        for row in clean:
            if isinstance(row, dict):
                row.pop(_CACHE_FIELD, None)
        return clean

    def write_cached_records(cache_key: str, **kwargs):
        marked = [copy.deepcopy(row) for row in kwargs.get("records") or []]
        for row in marked:
            if isinstance(row, dict):
                row[_CACHE_FIELD] = _CACHE_MARKER
        return original_write(cache_key, **{**kwargs, "records": marked})

    phase32._read_cached_records = read_cached_records
    phase32._write_cached_records = write_cached_records
    phase32._PHASE332_CACHE_COMPAT_VERSION = _CONTRACT_VERSION
