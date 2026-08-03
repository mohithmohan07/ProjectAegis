"""Content-addressed integrity seals for concept grounding.

Grounding is a semantic assertion about an exact concept claim and an exact
set of canonical source blocks.  A label such as
``api-verified-source-block-ids`` is not enough: if a later checkpoint repair
rewrites the Description while leaving that label and the old block IDs in
place, the final validator can mistake stale evidence for a verified result.

This module binds those values together twice:

* a per-row seal covers the source-facing claim, concept identity, topic,
  grounding contract, source contract, and exact evidence block set;
* a final certificate covers the exact ordered payload sent to deposit plus
  every row seal/evidence set.

The functions are deliberately deterministic and provider-independent.  They
do not decide whether evidence is academically sufficient; the grounding
provider and independent critic do that.  They prove that the payload which
reaches deposit is the same payload/evidence relationship that was verified.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from . import concept_refiner as cr


ROW_IDENTITY_VERSION = "concept-grounding-row-identity-1"
SEMANTIC_TOPOLOGY_VERSION = "concept-grounding-semantic-topology-1"
ROW_CERTIFICATE_VERSION = "concept-grounding-row-certificate-4"
LINEAGE_CERTIFICATE_VERSION = "concept-grounding-lineage-certificate-3"
FINAL_CERTIFICATE_VERSION = "concept-grounding-final-certificate-4"
ROW_CERTIFICATE_FIELD = "_source_grounding_record_sha256"
ROW_IDENTITY_FIELD = "_source_grounding_row_identity_sha256"
SOURCE_CONTRACT_FIELD = "_source_grounding_source_contract_hash"
SEMANTIC_TOPOLOGY_FIELD = "_source_grounding_semantic_topology_sha256"
CONCEPT_ID_FIELD = "_source_grounding_concept_id"
EVIDENCE_SET_FIELD = "_source_grounding_evidence_block_ids"
SOURCE_CLAIM_FIELD = "_source_grounding_source_claim_sha256"
LINEAGE_CERTIFICATE_FIELD = "_source_grounding_lineage_sha256"
LINEAGE_COUNT_FIELD = "_source_grounding_lineage_record_count"
FINAL_CERTIFICATE_FIELD = "final_grounding_certificate"

VERIFIED_GROUNDING_CONTRACTS = frozenset({
    "api-verified-source-block-ids",
    "api-verified-boundary-aware-source-block-ids",
    "api-created-missing-type-host",
    "derived-from-verified-topic-concepts",
})

_MASTERY_TAIL_RE = re.compile(
    r"(?:\n|\s)+Achieving\s+Mastery\s*:\s*.*$",
    re.IGNORECASE | re.DOTALL,
)
_SPACE_RE = re.compile(r"\s+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GroundingCertificateError(RuntimeError):
    """The completed concept payload is not the payload that was grounded."""


def _json_safe(value: Any) -> Any:
    """Return a stable JSON value without retaining live containers."""

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(raw)
            for key, raw in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(raw) for raw in value]
    if isinstance(value, set):
        return sorted(
            (_json_safe(raw) for raw in value),
            key=lambda raw: json.dumps(
                raw, ensure_ascii=False, sort_keys=True, default=str
            ),
        )
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normal(value: object) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip()


def semantic_topology_material(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Return the active graph identity relevant to grounded concept rows.

    ``source_contract_hash`` seals canonical source contents, but deliberately
    does not contain the semantic graph's topic/subtopic assignments.  A
    regenerated classifier can therefore produce a different topology for the
    same canonical source contract.  Grounding must bind both layers.
    """

    def ordered_rows(
        values: object,
        *,
        fields: tuple[str, ...],
        identity: str,
    ) -> list[dict[str, Any]]:
        rows = [
            {
                field: _json_safe(row.get(field))
                for field in fields
            }
            for row in values or []
            if isinstance(row, Mapping)
        ]
        return sorted(
            rows,
            key=lambda row: (
                int(row.get("order") or 0),
                str(row.get(identity) or ""),
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

    return {
        "version": SEMANTIC_TOPOLOGY_VERSION,
        "schema_name": str(graph.get("schema_name") or ""),
        "schema_version": str(graph.get("schema_version") or ""),
        "compiler_version": str(graph.get("compiler_version") or ""),
        "classification_mode": str(graph.get("classification_mode") or ""),
        "source_contract_hash": str(
            graph.get("source_contract_hash") or ""
        ),
        "semantic_context_hash": str(
            graph.get("semantic_context_hash") or ""
        ),
        "source_sha256": str(graph.get("source_sha256") or ""),
        "semantic_source_sha256": str(
            graph.get("semantic_source_sha256") or ""
        ),
        "numbered_main_bindings": _json_safe(
            graph.get("numbered_main_bindings") or []
        ),
        "topics": ordered_rows(
            graph.get("topics"),
            fields=(
                "topic_id", "order", "title", "source_start", "source_end",
            ),
            identity="topic_id",
        ),
        "subtopics": ordered_rows(
            graph.get("subtopics"),
            fields=(
                "subtopic_id", "topic_id", "order", "title",
                "source_start", "source_end",
            ),
            identity="subtopic_id",
        ),
        "blocks": ordered_rows(
            graph.get("blocks"),
            fields=(
                "block_id", "topic_id", "subtopic_id", "order", "kind",
                "source_start", "source_end", "boundary_relation",
            ),
            identity="block_id",
        ),
    }


def semantic_topology_sha256(graph: Mapping[str, Any]) -> str:
    """Hash the exact semantic topology used by grounding."""

    return _sha256_json(semantic_topology_material(graph))


def source_claim(record: Mapping[str, Any]) -> str:
    """Return only the source-facing Description proposition for one row."""

    details = str(
        record.get("concept_details")
        or record.get("concept_description")
        or ""
    )
    description = ""
    for label, content in cr.split_sections(details):
        if str(label or "").strip().casefold().startswith("description"):
            description = str(content or "").strip()
            break
    description = _MASTERY_TAIL_RE.sub("", description).strip()
    if description:
        return _normal(description)
    return _normal(record.get("concept_title") or record.get("concept"))


def row_identity_material(record: Mapping[str, Any]) -> dict[str, str]:
    """Return the stable public/graph identity fixed at topology freeze."""

    return {
        "version": ROW_IDENTITY_VERSION,
        "topic": _normal(record.get("topic")),
        "parent_concept": _normal(record.get("parent_concept")),
        "concept_title": _normal(
            record.get("concept_title") or record.get("concept")
        ),
        # Graph IDs are exact identifiers rather than presentation text.
        "semantic_topic_id": str(
            record.get("_semantic_topic_id") or ""
        ).strip(),
    }


def verify_row_identity(
    record: Mapping[str, Any], *, row_index: int,
) -> dict[str, str]:
    """Reject a post-ground concept rename, move, or parent reassignment."""

    concept_id = _concept_id(record, row_index)
    stored = str(record.get(ROW_IDENTITY_FIELD) or "").strip()
    current = row_identity_material(record)
    expected = _sha256_json(current)
    readable = ", ".join(
        f"{field}={value!r}"
        for field, value in current.items()
        if field != "version"
    )
    if not stored:
        raise GroundingCertificateError(
            f"grounding certificate {concept_id} lost its row identity "
            f"attestation; current identity: {readable}"
        )
    if stored != expected:
        raise GroundingCertificateError(
            f"grounding certificate identity/topology drift for {concept_id}: "
            "topic, parent concept, concept title, or semantic topic ID "
            f"changed after grounding; current identity: {readable}"
        )
    return current


def _block_ids(record: Mapping[str, Any]) -> list[str]:
    return [
        str(value).strip()
        for value in record.get("_source_block_ids") or []
        if str(value).strip()
    ]


def _attested_block_ids(record: Mapping[str, Any]) -> list[str]:
    return [
        str(value).strip()
        for value in record.get(EVIDENCE_SET_FIELD) or []
        if str(value).strip()
    ]


def _concept_id(record: Mapping[str, Any], row_index: int) -> str:
    stored = str(record.get(CONCEPT_ID_FIELD) or "").strip()
    return stored or f"CONCEPT-GROUND-{row_index + 1:04d}"


def _expected_concept_id(row_index: int) -> str:
    return f"CONCEPT-GROUND-{row_index + 1:04d}"


def _format_block_ids(values: Iterable[str]) -> str:
    rows = [str(value) for value in values if str(value)]
    return ",".join(rows) if rows else "none"


def _evidence_change_message(
    *,
    concept_id: str,
    attested: list[str],
    current: list[str],
) -> str | None:
    """Describe evidence drift before an opaque row-hash comparison.

    The ordered evidence list is part of the semantic assertion.  Report set
    membership changes first because they identify the exact source block
    lost or introduced; when membership is unchanged, distinguish ordering
    (or duplicate multiplicity) from a generic certificate failure.
    """

    if current == attested:
        return None
    attested_set = set(attested)
    current_set = set(current)
    missing = [value for value in attested if value not in current_set]
    added = [value for value in current if value not in attested_set]
    details: list[str] = []
    if missing:
        details.append(
            "missing attested source block(s): "
            + _format_block_ids(missing)
        )
    if added:
        details.append(
            "added unattested source block(s): "
            + _format_block_ids(added)
        )
    if not details:
        details.append(
            "evidence order or multiplicity changed; attested="
            + _format_block_ids(attested)
            + "; current="
            + _format_block_ids(current)
        )
    return (
        f"grounding certificate evidence mismatch for {concept_id}: "
        + "; ".join(details)
    )


def _source_contract(
    record: Mapping[str, Any],
    source_contract_hash: str = "",
) -> str:
    return str(
        source_contract_hash
        or record.get(SOURCE_CONTRACT_FIELD)
        or record.get("_semantic_graph_contract")
        or ""
    ).strip()


def row_certificate_material(
    record: Mapping[str, Any],
    *,
    source_contract_hash: str = "",
) -> dict[str, Any]:
    """Return the exact semantic/evidence relationship sealed for one row."""

    boundary_rows = [
        {
            "block_id": str(row.get("block_id") or ""),
            "source_topic_id": str(row.get("source_topic_id") or ""),
            "target_topic_id": str(row.get("target_topic_id") or ""),
        }
        for row in record.get("_source_grounding_boundary_blocks") or []
        if isinstance(row, Mapping)
    ]
    return {
        "version": ROW_CERTIFICATE_VERSION,
        "concept_id": str(record.get(CONCEPT_ID_FIELD) or "").strip(),
        "row_identity_sha256": str(
            record.get(ROW_IDENTITY_FIELD) or ""
        ).strip(),
        "source_contract_hash": _source_contract(
            record, source_contract_hash
        ),
        "semantic_topology_sha256": str(
            record.get(SEMANTIC_TOPOLOGY_FIELD) or ""
        ).strip(),
        "concept_title": _normal(
            record.get("concept_title") or record.get("concept")
        ),
        "parent_concept": _normal(record.get("parent_concept")),
        "topic": _normal(record.get("topic")),
        "semantic_topic_id": str(
            record.get("_semantic_topic_id") or ""
        ).strip(),
        "source_claim": source_claim(record),
        "source_claim_sha256": str(
            record.get(SOURCE_CLAIM_FIELD) or ""
        ).strip(),
        "source_block_ids": _block_ids(record),
        "attested_source_block_ids": _attested_block_ids(record),
        "semantic_subtopic_ids": sorted({
            str(value).strip()
            for value in record.get("_semantic_subtopic_ids") or []
            if str(value).strip()
        }),
        "grounding_contract": str(
            record.get("_source_grounding_contract") or ""
        ).strip(),
        "grounding_version": str(
            record.get("_source_grounding_version") or ""
        ).strip(),
        "boundary_blocks": boundary_rows,
    }


def lineage_certificate_material(
    records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the ordered row lineage established at grounding time.

    A collection of individually valid row seals is not enough: a downstream
    cleanup could otherwise remove or reorder a row and mint a new aggregate
    certificate for the surviving subset.  The lineage seal makes the complete
    ordered list part of every grounded row's attestation.
    """

    return {
        "version": LINEAGE_CERTIFICATE_VERSION,
        "record_count": len(records),
        "rows": [
            {
                "row_index": index,
                "concept_id": str(
                    record.get(CONCEPT_ID_FIELD) or ""
                ).strip(),
                "row_certificate_sha256": str(
                    record.get(ROW_CERTIFICATE_FIELD) or ""
                ).strip(),
                "row_identity_sha256": str(
                    record.get(ROW_IDENTITY_FIELD) or ""
                ).strip(),
                "source_contract_hash": _source_contract(record),
                "semantic_topology_sha256": str(
                    record.get(SEMANTIC_TOPOLOGY_FIELD) or ""
                ).strip(),
            }
            for index, record in enumerate(records)
        ],
    }


def _verify_lineage(records: list[Mapping[str, Any]]) -> str:
    """Verify that no grounded row was removed, duplicated, or reordered."""

    expected_digest = _sha256_json(lineage_certificate_material(records))
    expected_count = len(records)
    for index, record in enumerate(records):
        expected_id = _expected_concept_id(index)
        stored_id = str(record.get(CONCEPT_ID_FIELD) or "").strip()
        if stored_id != expected_id:
            raise GroundingCertificateError(
                "grounding certificate lineage mismatch at row "
                f"{index}: expected {expected_id}, found "
                f"{stored_id or 'missing'}; a grounded concept was removed, "
                "duplicated, or reordered"
            )
        try:
            stored_count = int(record.get(LINEAGE_COUNT_FIELD))
        except (TypeError, ValueError):
            stored_count = -1
        if stored_count != expected_count:
            attested_count: int | str = (
                stored_count
                if stored_count >= 0
                else "an unknown number of"
            )
            raise GroundingCertificateError(
                f"grounding certificate lineage mismatch for {stored_id}: "
                f"grounding attested {attested_count} "
                f"record(s), but the final payload contains {expected_count}"
            )
        stored_digest = str(
            record.get(LINEAGE_CERTIFICATE_FIELD) or ""
        ).strip()
        if stored_digest != expected_digest:
            raise GroundingCertificateError(
                f"grounding certificate lineage mismatch for {stored_id}: "
                "the ordered grounded concept set changed after verification"
            )
    return expected_digest


def verify_lineage(records: list[Mapping[str, Any]]) -> str:
    """Public fail-closed check for the grounded ordered concept lineage."""

    if not records:
        raise GroundingCertificateError(
            "grounding certificate lineage cannot verify an empty concept map"
        )
    return _verify_lineage(records)


def seal_records(
    records: list[dict[str, Any]],
    *,
    source_contract_hash: str,
    semantic_topology_sha256: str,
    allowed_block_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Attach row seals after provider/critic grounding has completed."""

    allowed = {str(value) for value in allowed_block_ids if str(value)}
    if not source_contract_hash:
        raise GroundingCertificateError(
            "grounding certificate requires a source contract hash"
        )
    if not _SHA256_RE.fullmatch(str(semantic_topology_sha256 or "")):
        raise GroundingCertificateError(
            "grounding certificate requires a semantic topology SHA-256"
        )
    for index, record in enumerate(records):
        block_ids = _block_ids(record)
        if not block_ids:
            raise GroundingCertificateError(
                f"grounding certificate row {index} has no source blocks"
            )
        duplicates = sorted({
            block_id for block_id in block_ids
            if block_ids.count(block_id) > 1
        })
        if duplicates:
            raise GroundingCertificateError(
                f"grounding certificate row {index} repeats source block(s): "
                + ",".join(duplicates)
            )
        unknown = sorted(set(block_ids) - allowed)
        if unknown:
            raise GroundingCertificateError(
                f"grounding certificate row {index} references unknown "
                "source block(s): " + ",".join(unknown)
            )
        if not str(record.get("_source_grounding_contract") or "").strip():
            raise GroundingCertificateError(
                f"grounding certificate row {index} has no grounding contract"
            )
        if not str(record.get("_source_grounding_version") or "").strip():
            raise GroundingCertificateError(
                f"grounding certificate row {index} has no grounding version"
            )
        record[CONCEPT_ID_FIELD] = f"CONCEPT-GROUND-{index + 1:04d}"
        record[ROW_IDENTITY_FIELD] = _sha256_json(
            row_identity_material(record)
        )
        record[EVIDENCE_SET_FIELD] = list(block_ids)
        record[SOURCE_CLAIM_FIELD] = _sha256_json(source_claim(record))
        record[SOURCE_CONTRACT_FIELD] = source_contract_hash
        record[SEMANTIC_TOPOLOGY_FIELD] = semantic_topology_sha256
        record[ROW_CERTIFICATE_FIELD] = _sha256_json(
            row_certificate_material(
                record,
                source_contract_hash=source_contract_hash,
            )
        )
    lineage_digest = _sha256_json(lineage_certificate_material(records))
    for record in records:
        record[LINEAGE_CERTIFICATE_FIELD] = lineage_digest
        record[LINEAGE_COUNT_FIELD] = len(records)
    return records


def verify_row(record: Mapping[str, Any], *, row_index: int) -> None:
    stored = str(record.get(ROW_CERTIFICATE_FIELD) or "")
    source_contract_hash = _source_contract(record)
    concept_id = _concept_id(record, row_index)
    verify_row_identity(record, row_index=row_index)
    if not source_contract_hash:
        raise GroundingCertificateError(
            f"grounding certificate {concept_id} lost its source contract"
        )
    semantic_topology = str(
        record.get(SEMANTIC_TOPOLOGY_FIELD) or ""
    ).strip()
    if not _SHA256_RE.fullmatch(semantic_topology):
        raise GroundingCertificateError(
            f"grounding certificate {concept_id} lost its semantic topology "
            "attestation"
        )
    current_block_ids = _block_ids(record)
    attested_block_ids = _attested_block_ids(record)
    if not attested_block_ids:
        raise GroundingCertificateError(
            f"grounding certificate {concept_id} lost its attested evidence "
            "manifest"
        )
    evidence_change = _evidence_change_message(
        concept_id=concept_id,
        attested=attested_block_ids,
        current=current_block_ids,
    )
    if evidence_change:
        raise GroundingCertificateError(evidence_change)
    if not current_block_ids:
        raise GroundingCertificateError(
            f"grounding certificate {concept_id} lost its evidence set"
        )
    contract = str(record.get("_source_grounding_contract") or "").strip()
    if contract not in VERIFIED_GROUNDING_CONTRACTS:
        raise GroundingCertificateError(
            f"grounding certificate {concept_id} is not independently "
            f"verified (contract={contract or 'missing'})"
        )
    attested_claim = str(record.get(SOURCE_CLAIM_FIELD) or "").strip()
    current_claim = _sha256_json(source_claim(record))
    if not attested_claim:
        raise GroundingCertificateError(
            f"grounding certificate {concept_id} lost its source-claim "
            "attestation"
        )
    if current_claim != attested_claim:
        raise GroundingCertificateError(
            f"grounding certificate mismatch for {concept_id}: the "
            "source-facing Description changed after grounding"
        )
    expected = _sha256_json(row_certificate_material(record))
    if not stored or stored != expected:
        title = _normal(
            record.get("concept_title") or record.get("concept")
        )
        raise GroundingCertificateError(
            f"grounding certificate mismatch for {concept_id} "
            f"({title or 'untitled'}): the grounded concept identity or "
            "metadata changed after grounding"
        )


def build_final_certificate(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a certificate for the exact ordered payload sent to deposit."""

    if not records:
        raise GroundingCertificateError(
            "final grounding certificate cannot certify an empty concept map"
        )
    lineage_digest = verify_lineage(records)
    source_contracts: set[str] = set()
    semantic_topologies: set[str] = set()
    evidence: list[dict[str, Any]] = []
    public_payload: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        verify_row(record, row_index=index)
        source_contracts.add(_source_contract(record))
        semantic_topologies.add(str(
            record.get(SEMANTIC_TOPOLOGY_FIELD) or ""
        ).strip())
        public_payload.append({
            key: copy.deepcopy(record.get(key, ""))
            for key in (
                "topic",
                "parent_concept",
                "concept_title",
                "concept_details",
                "keywords",
            )
        })
        evidence.append({
            "row_index": index,
            "concept_id": _concept_id(record, index),
            "row_identity": row_identity_material(record),
            "row_identity_sha256": str(
                record.get(ROW_IDENTITY_FIELD) or ""
            ),
            "concept_title": _normal(
                record.get("concept_title") or record.get("concept")
            ),
            "topic": _normal(record.get("topic")),
            "source_block_ids": _block_ids(record),
            "row_certificate_sha256": str(
                record.get(ROW_CERTIFICATE_FIELD) or ""
            ),
        })
    source_contracts.discard("")
    if len(source_contracts) != 1:
        raise GroundingCertificateError(
            "final grounding certificate requires exactly one source contract"
        )
    semantic_topologies.discard("")
    if len(semantic_topologies) != 1:
        raise GroundingCertificateError(
            "final grounding certificate requires exactly one semantic "
            "topology"
        )
    base = {
        "version": FINAL_CERTIFICATE_VERSION,
        "source_contract_hash": next(iter(source_contracts)),
        "semantic_topology_sha256": next(iter(semantic_topologies)),
        "record_count": len(records),
        "lineage_sha256": lineage_digest,
        "public_payload_sha256": _sha256_json(public_payload),
        "grounding_manifest_sha256": _sha256_json(evidence),
        "evidence": evidence,
    }
    return {
        **base,
        "certificate_sha256": _sha256_json(base),
    }


def verify_certificate_envelope(
    certificate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate the bounded, self-addressed final certificate envelope.

    This check is useful when records are not present beside an audit copy,
    such as ``UploadJob.question_inventory`` in a portable checkpoint bundle.
    It does not replace ``verify_final_certificate`` at a consume boundary.
    """

    if not isinstance(certificate, Mapping):
        raise GroundingCertificateError(
            "final grounding certificate must be an object"
        )
    value = copy.deepcopy(dict(certificate))
    base_fields = {
        "version",
        "source_contract_hash",
        "semantic_topology_sha256",
        "record_count",
        "lineage_sha256",
        "public_payload_sha256",
        "grounding_manifest_sha256",
        "evidence",
    }
    if set(value) != base_fields | {"certificate_sha256"}:
        raise GroundingCertificateError(
            "final grounding certificate has unsupported or missing fields"
        )
    if value.get("version") != FINAL_CERTIFICATE_VERSION:
        raise GroundingCertificateError(
            "final grounding certificate version is not supported"
        )
    for field in (
        "source_contract_hash",
        "semantic_topology_sha256",
        "lineage_sha256",
        "public_payload_sha256",
        "grounding_manifest_sha256",
        "certificate_sha256",
    ):
        if not _SHA256_RE.fullmatch(str(value.get(field) or "")):
            raise GroundingCertificateError(
                f"final grounding certificate {field} is not a SHA-256"
            )
    record_count = value.get("record_count")
    if (
        isinstance(record_count, bool)
        or not isinstance(record_count, int)
        or not 1 <= record_count <= 5_000
    ):
        raise GroundingCertificateError(
            "final grounding certificate record_count is out of bounds"
        )
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != record_count:
        raise GroundingCertificateError(
            "final grounding certificate evidence count does not match its "
            "record count"
        )
    evidence_fields = {
        "row_index",
        "concept_id",
        "row_identity",
        "row_identity_sha256",
        "concept_title",
        "topic",
        "source_block_ids",
        "row_certificate_sha256",
    }
    identity_fields = {
        "version",
        "topic",
        "parent_concept",
        "concept_title",
        "semantic_topic_id",
    }
    for index, raw in enumerate(evidence):
        if not isinstance(raw, Mapping) or set(raw) != evidence_fields:
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} is invalid"
            )
        if raw.get("row_index") != index:
            raise GroundingCertificateError(
                "final grounding certificate evidence rows are not ordered"
            )
        if str(raw.get("concept_id") or "") != _expected_concept_id(index):
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has an "
                "invalid concept ID"
            )
        identity = raw.get("row_identity")
        if not isinstance(identity, Mapping) or set(identity) != identity_fields:
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has an "
                "invalid row identity"
            )
        if identity.get("version") != ROW_IDENTITY_VERSION:
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has an "
                "unsupported identity version"
            )
        identity_hash = str(raw.get("row_identity_sha256") or "")
        if (
            not _SHA256_RE.fullmatch(identity_hash)
            or identity_hash != _sha256_json(identity)
        ):
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has a "
                "stale identity hash"
            )
        if not _SHA256_RE.fullmatch(str(
            raw.get("row_certificate_sha256") or ""
        )):
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has an "
                "invalid row certificate hash"
            )
        block_ids = raw.get("source_block_ids")
        if (
            not isinstance(block_ids, list)
            or not block_ids
            or len(block_ids) > 20_000
            or any(
                not isinstance(block_id, str) or not block_id.strip()
                for block_id in block_ids
            )
            or len(block_ids) != len(set(block_ids))
        ):
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has an "
                "invalid source block manifest"
            )
    base = {field: copy.deepcopy(value[field]) for field in base_fields}
    if value["grounding_manifest_sha256"] != _sha256_json(evidence):
        raise GroundingCertificateError(
            "final grounding certificate manifest hash is stale"
        )
    if value["certificate_sha256"] != _sha256_json(base):
        raise GroundingCertificateError(
            "final grounding certificate self-hash is stale"
        )
    return value


def verify_semantic_topology(
    certificate: Mapping[str, Any],
    semantic_graph: Mapping[str, Any],
) -> str:
    """Require a certificate to match the currently active semantic graph."""

    expected = semantic_topology_sha256(semantic_graph)
    attested = str(certificate.get("semantic_topology_sha256") or "")
    if attested != expected:
        raise GroundingCertificateError(
            "final grounding certificate was sealed against a different "
            "semantic graph/topology"
        )
    return expected


def verify_final_certificate(
    records: list[dict[str, Any]],
    certificate: Mapping[str, Any] | None,
    *,
    semantic_graph: Mapping[str, Any] | None = None,
    require_semantic_graph: bool = False,
) -> dict[str, Any]:
    """Recompute and compare the final certificate immediately before use."""

    supplied = verify_certificate_envelope(certificate)
    if require_semantic_graph and not isinstance(semantic_graph, Mapping):
        raise GroundingCertificateError(
            "final grounding certificate cannot be consumed without the "
            "active semantic graph"
        )
    if isinstance(semantic_graph, Mapping):
        verify_semantic_topology(supplied, semantic_graph)
    # Compare the readable evidence manifest before rebuilding opaque row
    # digests.  This makes a stale propagation defect actionable: callers see
    # the exact BLK identity which disappeared instead of only "hash changed".
    supplied_evidence = supplied.get("evidence")
    if isinstance(supplied_evidence, list):
        for index, record in enumerate(records):
            if index >= len(supplied_evidence):
                break
            sealed = supplied_evidence[index]
            if not isinstance(sealed, Mapping):
                continue
            concept_id = str(
                sealed.get("concept_id") or _concept_id(record, index)
            ).strip()
            evidence_change = _evidence_change_message(
                concept_id=concept_id,
                attested=[
                    str(value).strip()
                    for value in sealed.get("source_block_ids") or []
                    if str(value).strip()
                ],
                current=_block_ids(record),
            )
            if evidence_change:
                raise GroundingCertificateError(evidence_change)
    expected = build_final_certificate(records)
    if supplied != expected:
        raise GroundingCertificateError(
            "final grounding certificate does not match the exact concept "
            "payload and evidence set"
        )
    return expected
