"""Transport the accepted Post Concept sequence into its Master questions.

The Phase 3 API already decided concept/Type/Case/question order. The accepted
Concept rows carry that sequence in `_aegis_release_qids`; original print order
must not silently replace it at the Master source-inventory boundary.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import generation_quality_policy as quality

AUDIT_FIELD = "_aegis_source_teaching_order"


class TeachingOrderError(ValueError):
    """An explicit accepted order contains ambiguous identities."""


def project_atoms(
    atoms: list[Mapping[str, Any]], release: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project recorded row/QID ordinals; preserve unallocated evidence once."""
    if not quality.is_current(release):
        return list(atoms), {}
    locations: dict[str, dict[str, Any]] = {}
    for row_index, row in enumerate(release.get("records") or []):
        qids = row.get("_aegis_release_qids") or []
        if not isinstance(qids, list):
            raise TeachingOrderError("accepted Concept question order must be a QID array")
        for question_index, qid in enumerate(qids):
            if not isinstance(qid, str) or not qid:
                raise TeachingOrderError("accepted Concept question order has a missing QID")
            if qid in locations:
                raise TeachingOrderError(f"accepted Concept question order repeats {qid}")
            locations[qid] = {
                "source_qid": qid, "concept_record_index": row_index,
                "concept_question_index": question_index, "ordinal": len(locations),
            }
    qids = [str(atom.get("source_qid") or "") for atom in atoms]
    if any(not qid for qid in qids) or len(qids) != len(set(qids)):
        raise TeachingOrderError("Master atom identities must be nonempty and unique")
    # The only ordering key is the ordinal already supplied by the semantic
    # author. Stable tails retain complete unallocated inventory evidence.
    result = sorted(
        (copy.deepcopy(dict(atom)) for atom in atoms),
        key=lambda atom: locations.get(str(atom["source_qid"]), {}).get("ordinal", len(locations)),
    )
    unmapped = []
    for atom in result:
        qid = str(atom["source_qid"])
        location = locations.get(qid)
        atom[AUDIT_FIELD] = {
            "policy_version": quality.VERSION,
            **copy.deepcopy(location or {"source_qid": qid, "ordinal": None}),
        }
        if location is None:
            unmapped.append(qid)
            atom.setdefault("flags", []).append(
                f"{qid}: no accepted Concept question-order receipt; retained "
                "after the ordered questions in original inventory sequence for review"
            )
    return result, {
        "policy_version": quality.VERSION,
        "accepted_concept_qids": list(locations),
        "ordered_atom_qids": [str(atom["source_qid"]) for atom in result],
        "unmapped_qids": unmapped,
        "concept_positions": list(locations.values()),
    }
