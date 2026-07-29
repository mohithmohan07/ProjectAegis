"""Preserve independently verified Phase 3.3 Type-host identity during allocation.

The legacy Type allocator already supports deterministic high-confidence
routing, but it derives that routing from task/title token overlap. Phase 3.3 has
a stronger authority: an API proposal and independent critic have certified one
exact normal concept host before topology freeze. This final adapter makes that
verified destination authoritative for the existing assignment and semantic
host-review passes, while retaining every legacy fallback for units without a
Phase 3.3 verdict.
"""
from __future__ import annotations

import re
from functools import wraps
from typing import Any

_CONTRACT_VERSION = 1


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def install(generation: Any | None = None) -> None:
    if generation is None:
        from . import generation as generation
    if (
        getattr(generation, "_PHASE331_HOST_AUTHORITY_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original = generation._high_confidence_assignment_override

    @wraps(original)
    def verified_host_override(
        mtype: dict,
        candidate_cids: tuple[str, ...],
        concept_payload_by_id: dict[str, dict],
    ) -> str:
        if mtype.get("_phase33_host_verified"):
            title = _normal(mtype.get("concept_match_hint"))
            parent = _normal(mtype.get("parent_concept_match_hint"))
            topic = _normal(mtype.get("topic_match_hint"))
            matches = [
                cid
                for cid in candidate_cids
                if cid in concept_payload_by_id
                and not concept_payload_by_id[cid].get("is_culmination")
                and _normal(concept_payload_by_id[cid].get("concept")) == title
                and (
                    not parent
                    or _normal(concept_payload_by_id[cid].get("parent_concept"))
                    == parent
                )
                and (
                    not topic
                    or _normal(concept_payload_by_id[cid].get("topic")) == topic
                )
            ]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise RuntimeError(
                    "Phase 3.3 independently verified Type host is absent from "
                    "the frozen concept topology: "
                    f"topic={mtype.get('topic_match_hint')!r}, "
                    f"parent={mtype.get('parent_concept_match_hint')!r}, "
                    f"concept={mtype.get('concept_match_hint')!r}"
                )
            raise RuntimeError(
                "Phase 3.3 independently verified Type host is not unique in "
                "the frozen concept topology: "
                f"concept={mtype.get('concept_match_hint')!r}, matches={matches!r}"
            )
        return original(mtype, candidate_cids, concept_payload_by_id)

    generation._high_confidence_assignment_override = verified_host_override
    generation._PHASE331_HOST_AUTHORITY_VERSION = _CONTRACT_VERSION
