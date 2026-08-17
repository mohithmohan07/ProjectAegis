"""Lossless assessment source inventory (MES spec §5.1, Stage 2).

Transforms the canonical Build Concepts Question/Task Inventory into
release-scoped ``AssessmentSourceAtom`` records without losing provenance:
raw and public task text, source kind, page/block identity, parent/subpart
and OR relationships, options and source answers, shared context, ordered
images, and Type/Case route evidence.

Everything here is deterministic mechanics: transformation, identity, and
accounting. No field is classified, shortened, or dropped by shape — a QID
that cannot be carried forward stops the run rather than disappearing
(zero-loss invariant, spec §14).
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import assessment_release as rel


class SourceInventoryError(ValueError):
    """The inventory cannot be carried into a release without loss."""


_POSITION_FIELDS = frozenset({
    "bbox",
    "example_number",
    "page",
    "page_hint",
    "position",
    "printer_page",
    "row",
    "row_index",
    "row_number",
    "source_end",
    "source_order",
    "source_page",
    "source_paper_number",
    "source_start",
    "_phase32_segment_order",
    "_phase32_source_order",
})


def _semantic_evidence(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _semantic_evidence(raw)
            for key, raw in value.items()
            if str(key) not in _POSITION_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_evidence(raw) for raw in value]
    return copy.deepcopy(value)


def _mentions_qid(value: Any, qid: str) -> bool:
    """Whether a staged evidence tree names this exact source identity."""

    if isinstance(value, Mapping):
        return any(_mentions_qid(raw, qid) for raw in value.values())
    if isinstance(value, (list, tuple)):
        return any(_mentions_qid(raw, qid) for raw in value)
    return isinstance(value, str) and value.strip() == qid


def _subpart_of(qid: str, parent_qid: str) -> str:
    """Dotted-suffix subpart identity (``QINV-0022.4`` under ``QINV-0022``)."""
    qid = str(qid or "").strip()
    parent = str(parent_qid or "").strip()
    if parent and qid.startswith(parent + "."):
        return qid[len(parent) + 1:]
    return ""


def _assets_of(item: Mapping) -> list[dict]:
    """Ordered source assets. Order is reading order of the item's images."""
    assets: list[dict] = []
    for order, url in enumerate(item.get("image_urls") or [], start=1):
        url = str(url or "").strip()
        if not url:
            continue
        meta = {}
        for candidate in item.get("image_assets") or []:
            if isinstance(candidate, Mapping) and (
                str(candidate.get("url") or "").strip() == url
            ):
                meta = candidate
                break
        assets.append({
            "source_page": meta.get("source_page"),
            "bbox": meta.get("bbox"),
            "sha256": str(meta.get("sha256") or ""),
            "url": url,
            "alt": str(meta.get("alt") or ""),
            "order": order,
        })
    return assets


def _route_evidence(
    qid: str, mined_types: Any, type_case_rows: Any = None,
) -> dict:
    """Complete staged Type/Case evidence for one QID, without coordinates."""

    evidence: dict[str, Any] = {}
    if isinstance(mined_types, Mapping):
        certifications = (
            (mined_types.get("placement_certifications") or {}).get("hosts")
            if isinstance(
                mined_types.get("placement_certifications"), Mapping
            )
            else None
        )
        if isinstance(certifications, Mapping) and qid in certifications:
            host = certifications.get(qid)
            if isinstance(host, Mapping):
                evidence.update(_semantic_evidence(host))
        raw_types = mined_types.get("types") or []
    elif isinstance(mined_types, list):
        raw_types = mined_types
    else:
        raw_types = []

    relevant_types = [
        _semantic_evidence(type_row)
        for type_row in raw_types
        if isinstance(type_row, Mapping) and _mentions_qid(type_row, qid)
    ]
    if relevant_types:
        evidence["mined_types"] = relevant_types

    relevant_rows = [
        _semantic_evidence(row)
        for row in type_case_rows or []
        if isinstance(row, Mapping) and _mentions_qid(row, qid)
    ]
    if relevant_rows:
        evidence["type_case_rows"] = relevant_rows
    return evidence


def source_atom_from_item(
    item: Mapping,
    *,
    source_document_hash: str,
    mined_types: Any = None,
    type_case_rows: Any = None,
) -> dict:
    """One inventory item -> one AssessmentSourceAtom (spec §5.1)."""
    qid = str(item.get("qid") or "").strip()
    parent_qid = str(item.get("parent_qid") or "").strip()
    raw_text = str(
        item.get("raw_task") or item.get("normalized_task") or "")
    public_text = str(
        item.get("polished_task")
        or item.get("normalized_task")
        or item.get("raw_task")
        or ""
    )
    return {
        "source_qid": qid,
        "source_document_hash": source_document_hash,
        "source_paper_number": str(item.get("source_label") or ""),
        "page": item.get("page_hint") or None,
        "block_ids": list(item.get("block_ids") or []),
        "source_kind": str(item.get("source_kind") or ""),
        "parent_qid": parent_qid or None,
        "subpart": _subpart_of(qid, parent_qid) or None,
        "alternative_set_id": (
            str(item.get("alternative_set_id") or "").strip() or None),
        "raw_text": raw_text,
        "normalized_public_text": public_text,
        "shared_context": str(item.get("shared_context") or ""),
        "source_answer": str(item.get("raw_solution_or_answer") or ""),
        "options": list(item.get("options") or []),
        "topic_hint": str(item.get("topic_hint") or ""),
        "polish_flag": str(item.get("polish_flag") or ""),
        "assets": _assets_of(item),
        "route_evidence": _route_evidence(
            qid, mined_types, type_case_rows
        ),
    }


def build_source_atoms(
    question_inventory: Mapping | None,
    *,
    source_document_hash: str = "",
) -> dict:
    """The complete lossless atom set for one job's inventory.

    Returns ``{"atoms": [...], "ledger": {qid: index}, "sha256": ...}``.
    Every inventory QID maps to exactly one atom and back; any break stops
    the run with the exact identities named.
    """
    inventory = question_inventory or {}
    raw_items = inventory.get("items") or []
    if not isinstance(raw_items, list):
        raise SourceInventoryError("source inventory items are not an array")
    items: list[Mapping] = []
    for position, item in enumerate(raw_items, start=1):
        if not isinstance(item, Mapping):
            raise SourceInventoryError(
                f"source inventory item {position} is not an object"
            )
        items.append(item)
    mined_types = inventory.get("mined_types")
    type_case_rows = inventory.get("type_case_rows")
    atoms: list[dict] = []
    ledger: dict[str, int] = {}
    for item in items:
        qid = str(item.get("qid") or "").strip()
        if not qid:
            raise SourceInventoryError(
                "inventory item without a QID cannot enter a release: "
                f"{str(item.get('raw_task') or '')[:120]!r}"
            )
        if qid in ledger:
            raise SourceInventoryError(
                f"duplicate inventory QID {qid}; the QID ledger must be "
                "bidirectional"
            )
        ledger[qid] = len(atoms)
        atoms.append(source_atom_from_item(
            item,
            source_document_hash=source_document_hash,
            mined_types=mined_types,
            type_case_rows=type_case_rows,
        ))

    report = rel.zero_loss_report(
        obligations=[str(item.get("qid") or "") for item in items],
        accepted=[atom["source_qid"] for atom in atoms],
        flagged=[],
    )
    if not report["holds"]:
        raise SourceInventoryError(
            "source inventory would lose or invent QIDs: "
            f"missing={report['missing']} unexpected={report['unexpected']}"
        )
    return {
        "atoms": atoms,
        "ledger": ledger,
        "sha256": rel.sha256_json(atoms),
    }
