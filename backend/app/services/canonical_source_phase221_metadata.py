"""Expose Phase 2.2.1 protocol metadata while preserving the Phase 2.2 API family.

The public adjudication family remains ``2.2.0`` for compatibility with saved
Phase-2.2 canonical bundles and regression contracts.  ``patch_version`` and the
cache-key salt identify the evidence-page protocol revision independently, so
old malformed cached decisions can never be replayed.
"""
from __future__ import annotations

import copy
from typing import Any

from . import canonical_source_phase22 as phase22
from . import canonical_source_phase22_contract as phase22_contract
from . import canonical_source_phase221_contract as phase221

_PUBLIC_FAMILY_VERSION = "2.2.0"
_CONTRACT_VERSION = 1


def install() -> None:
    if getattr(phase22, "_PHASE221_METADATA_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_annotate = phase22.annotate_pending_adjudication
    original_cache_key = phase22._cache_key

    def annotate_pending_adjudication(
        canonical: dict[str, Any],
        report: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        value, output = original_annotate(canonical, report)
        marker = value.get("source_adjudication")
        if isinstance(marker, dict):
            marker["patch_version"] = phase221.PATCH_VERSION
            marker["evidence_page_protocol"] = phase221._PROTOCOL
            output["source_adjudication"] = copy.deepcopy(marker)
        value.setdefault("source_contract", {})["adjudication_patch_version"] = (
            phase221.PATCH_VERSION
        )
        return value, output

    def cache_key(*, canonical, source_path, packet) -> str:
        base = original_cache_key(
            canonical=canonical,
            source_path=source_path,
            packet=packet,
        )
        return phase22._sha256_text(
            "\u241f".join([
                base,
                phase221.PATCH_VERSION,
                phase221._PROTOCOL,
            ])
        )

    def phase22_current(canonical: dict[str, Any] | None) -> bool:
        marker = (canonical or {}).get("source_adjudication")
        return bool(
            isinstance(marker, dict)
            and marker.get("version") == _PUBLIC_FAMILY_VERSION
            and marker.get("patch_version") == phase221.PATCH_VERSION
            and marker.get("evidence_page_protocol") == phase221._PROTOCOL
        )

    # Keep the existing Phase 2.2 public-family marker, but ensure all new cache
    # keys and canonical reports carry the patch protocol explicitly. Existing
    # 2.2.0 artifacts without the patch marker are recompiled once before retry.
    phase22.ADJUDICATION_VERSION = _PUBLIC_FAMILY_VERSION
    phase22.annotate_pending_adjudication = annotate_pending_adjudication
    phase22._cache_key = cache_key
    phase22_contract._phase22_current = phase22_current
    phase22._PHASE221_METADATA_VERSION = _CONTRACT_VERSION
