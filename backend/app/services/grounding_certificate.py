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
from . import placement_policy


ROW_IDENTITY_VERSION = "concept-grounding-row-identity-2"
SEMANTIC_TOPOLOGY_VERSION = "concept-grounding-semantic-topology-1"
ROW_CERTIFICATE_VERSION = "concept-grounding-row-certificate-5"
LINEAGE_CERTIFICATE_VERSION = "concept-grounding-lineage-certificate-4"
FINAL_CERTIFICATE_VERSION = "concept-grounding-final-certificate-6"
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
PLACEMENT_CONTRACT_FIELD = "_placement_contract"
TYPE_CASE_HOST_MANIFEST_FIELD = "_type_case_qid_host_placement_manifest"

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
_NON_CONTENT_EVIDENCE_BLOCK_KINDS = frozenset({
    "layout",
    "heading",
    "navigation",
})


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


def _legacy_placement_contract(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project old flat placement fields into the nested migration contract."""

    aliases = {
        "certified": record.get("_placement_certified"),
        "policy_version": record.get("_placement_policy_version"),
        "teaching_order_sha256": record.get("_placement_teaching_order_sha256"),
        "claim_id": record.get("_placement_claim_id"),
        "normalized_claim": record.get("_placement_normalized_claim"),
        "origin_claim_sha256": record.get("_placement_origin_claim_sha256"),
        "source_location_topic_id": record.get(
            "_placement_source_location_topic_id"
        ),
        "owner_topic_id": record.get("_placement_owner"),
        "required_topic_ids": record.get("_placement_required_topic_ids"),
        "prerequisite_topic_ids": record.get("_placement_prerequisites"),
        "reference_edges": record.get("_placement_reference_edges"),
        "illustration_topic_ids": record.get(
            "_placement_illustration_topic_ids"
        ),
        "topic_relationships": record.get("_placement_topic_relationships"),
        "origin_claim_ids": record.get("_placement_origin_claim_ids"),
        "split_group_id": record.get("_placement_split_group_id"),
        "protected_source_items": record.get(
            "_placement_protected_source_items"
        ),
        "split_attestation": record.get("_placement_split_attestation"),
        "placement_certificate_sha256": record.get(
            "_placement_certificate_sha256"
        ),
    }
    if not any(value not in (None, "", [], {}, ()) for value in aliases.values()):
        return None
    return aliases


def placement_contract(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one canonical row placement contract, including its stored seal."""

    raw = record.get(PLACEMENT_CONTRACT_FIELD)
    if not isinstance(raw, Mapping):
        raw = _legacy_placement_contract(record)
    if not isinstance(raw, Mapping):
        return None
    normalized = placement_policy.normalized_placement_contract(raw)
    return {
        **normalized,
        "placement_certificate_sha256": str(
            raw.get("placement_certificate_sha256") or ""
        ).strip(),
    }


def verify_placement_contract(
    record: Mapping[str, Any],
    *,
    allowed_block_ids: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Validate a certified row placement seal and its exact evidence graph.

    Legacy rows may have no placement contract.  Once a row declares
    ``certified=true``, however, every placement identity becomes mandatory and
    the stored row digest must match the shared placement-policy normalizer.
    """

    raw = record.get(PLACEMENT_CONTRACT_FIELD)
    if not isinstance(raw, Mapping):
        raw = _legacy_placement_contract(record)
    if not isinstance(raw, Mapping):
        return None
    value = placement_contract(record)
    assert value is not None
    if not value.get("certified"):
        raise GroundingCertificateError(
            "placement metadata is present but is not independently certified"
        )

    concept_label = str(
        value.get("claim_id")
        or record.get(CONCEPT_ID_FIELD)
        or record.get("concept_title")
        or "placement row"
    )
    required_text = {
        "policy_version": value.get("policy_version"),
        "claim_id": value.get("claim_id"),
        "normalized_claim": value.get("normalized_claim"),
        "source_location_topic_id": value.get("source_location_topic_id"),
        "owner_topic_id": value.get("owner_topic_id"),
    }
    missing = [field for field, raw_value in required_text.items() if not raw_value]
    if missing:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} is missing "
            + ", ".join(missing)
        )
    if value["policy_version"] != placement_policy.POLICY_VERSION:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} uses stale policy "
            f"version {value['policy_version']}"
        )
    for field in (
        "teaching_order_sha256",
        "origin_claim_sha256",
        "placement_certificate_sha256",
    ):
        if not _SHA256_RE.fullmatch(str(value.get(field) or "")):
            raise GroundingCertificateError(
                f"certified placement contract {concept_label} has invalid "
                f"{field}"
            )

    expected = placement_policy.placement_contract_sha256(raw)
    if value["placement_certificate_sha256"] != expected:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} has a stale or "
            "mismatched placement certificate"
        )

    current_claim_sha256 = placement_policy.claim_text_sha256(
        source_claim(record)
    )
    contract_claim_sha256 = placement_policy.claim_text_sha256(
        value.get("normalized_claim")
    )
    if contract_claim_sha256 != current_claim_sha256:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} no longer matches "
            "the row's normalized source claim"
        )

    semantic_topic_id = str(record.get("_semantic_topic_id") or "").strip()
    if semantic_topic_id and value["owner_topic_id"] != semantic_topic_id:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} owns topic "
            f"{value['owner_topic_id']}, but the row is assigned to "
            f"{semantic_topic_id}"
        )

    required_topics = set(value.get("required_topic_ids") or [])
    prerequisite_topics = set(value.get("prerequisite_topic_ids") or [])
    if value["owner_topic_id"] not in required_topics:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} omits its owner "
            "from required_topic_ids"
        )
    if not prerequisite_topics.issubset(required_topics):
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} has prerequisite "
            "topics outside required_topic_ids"
        )

    allowed = (
        {str(block_id) for block_id in allowed_block_ids if str(block_id)}
        if allowed_block_ids is not None
        else None
    )
    owning_topics: set[str] = set()
    relationship_prerequisites: set[str] = set()
    relationship_references: set[tuple[str, str]] = set()
    relationship_illustrations: set[str] = set()
    typed_relationships: list[placement_policy.TopicRelationship] = []
    for relation in value.get("topic_relationships") or []:
        relationship_id = str(relation.get("relationship_id") or "")
        if not relationship_id:
            raise GroundingCertificateError(
                f"certified placement contract {concept_label} has a "
                "relationship without an identity"
            )
        if str(relation.get("claim_id") or "") != value["claim_id"]:
            raise GroundingCertificateError(
                f"certified placement relationship {relationship_id} belongs "
                "to a different claim"
            )
        relationship_kind = placement_policy.relationship_type(
            relation.get("relationship_type")
        )
        if relationship_kind is None:
            raise GroundingCertificateError(
                f"certified placement relationship {relationship_id} has an "
                "unknown relationship type"
            )
        typed_relation = placement_policy.TopicRelationship(
            claim_id=str(relation.get("claim_id") or ""),
            topic_id=str(relation.get("topic_id") or ""),
            relationship_type=relationship_kind,
            necessity=bool(relation.get("necessity")),
            evidence_block_ids=tuple(
                str(block_id)
                for block_id in relation.get("evidence_block_ids") or []
                if str(block_id)
            ),
        )
        expected_relationship_id = typed_relation.relationship_id
        if relationship_id != expected_relationship_id:
            raise GroundingCertificateError(
                f"certified placement relationship {relationship_id} has a "
                "stale content-addressed identity"
            )
        if str(relation.get("critic_verdict") or "").casefold() not in {
            "accepted", "verified",
        }:
            raise GroundingCertificateError(
                f"certified placement relationship {relationship_id} lacks "
                "an accepted independent critic verdict"
            )
        evidence_ids = {
            str(block_id)
            for block_id in relation.get("evidence_block_ids") or []
            if str(block_id)
        }
        raw_evidence_ids = [
            str(block_id)
            for block_id in relation.get("evidence_block_ids") or []
            if str(block_id)
        ]
        relationship_type = relationship_kind.value
        topic_id = str(relation.get("topic_id") or "")
        if relationship_kind in placement_policy.OWNING_TYPES:
            owning_topics.add(topic_id)
        elif relationship_kind is (
            placement_policy.RelationshipType.REQUIRED_PREREQUISITE
        ):
            relationship_prerequisites.add(topic_id)
        elif relationship_kind is (
            placement_policy.RelationshipType.SUBSTANTIVE_LATER_ILLUSTRATION
        ):
            relationship_illustrations.add(topic_id)
        elif relationship_kind in placement_policy.EDGE_ONLY_TYPES:
            relationship_references.add((topic_id, relationship_type))
        expected_necessity = (
            relationship_kind in placement_policy.NECESSARY_TYPES
        )
        if (
            not evidence_ids
            or len(evidence_ids) != len(raw_evidence_ids)
            or relation.get("necessity") is not expected_necessity
        ):
            raise GroundingCertificateError(
                f"certified placement relationship {relationship_id} lacks "
                "exact evidence or has inconsistent necessity"
            )
        unknown = sorted(evidence_ids - allowed) if allowed is not None else []
        if unknown:
            raise GroundingCertificateError(
                f"certified placement relationship {relationship_id} "
                "references unknown source block(s): " + ",".join(unknown)
            )
        typed_relationships.append(typed_relation)
    expected_required = owning_topics | relationship_prerequisites
    expected_references = {
        tuple(edge) for edge in value.get("reference_edges") or []
        if isinstance(edge, (list, tuple)) and len(edge) == 2
    }
    if value["owner_topic_id"] not in owning_topics:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} is not owned by a "
            "certified teaching relationship"
        )
    if required_topics != expected_required:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} required topics do "
            "not match its relationships"
        )
    if prerequisite_topics != relationship_prerequisites:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} prerequisite topics "
            "do not match its relationships"
        )
    if expected_references != relationship_references:
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} reference edges do "
            "not match its relationships"
        )
    if set(value.get("illustration_topic_ids") or []) != (
        relationship_illustrations
    ):
        raise GroundingCertificateError(
            f"certified placement contract {concept_label} illustration topics "
            "do not match its relationships"
        )
    split_group_id = str(value.get("split_group_id") or "")
    split_attestation = value.get("split_attestation") or {}
    requires_split_attestation = (
        str(record.get("_phase32_topology_decision") or "") == "split"
        or bool(split_attestation)
    )
    if requires_split_attestation:
        if not split_group_id:
            raise GroundingCertificateError(
                f"certified placement contract {concept_label} carries a "
                "split attestation without split lineage"
            )
        child = placement_policy.AtomicClaim(
            claim_id=str(value.get("claim_id") or ""),
            normalized_claim=str(value.get("normalized_claim") or ""),
            source_location_topic_id=str(
                value.get("source_location_topic_id") or ""
            ),
            origin_claim_ids=tuple(
                str(item)
                for item in value.get("origin_claim_ids") or []
                if str(item)
            ),
            split_group_id=split_group_id,
            protected_source_items=tuple(
                str(item)
                for item in value.get("protected_source_items") or []
                if str(item)
            ),
        )
        try:
            placement_policy.validate_split_attestation(
                split_attestation,
                child=child,
                relationships=typed_relationships,
            )
        except placement_policy.PlacementPolicyError as exc:
            raise GroundingCertificateError(
                f"certified placement contract {concept_label} has an "
                f"invalid split survival attestation: {exc}"
            ) from exc
    return value


def _canonical_manifest_value(value: Any) -> Any:
    """Canonicalize Type/Case host manifests without changing list semantics."""

    safe = _json_safe(value)
    if isinstance(safe, dict):
        return {
            key: _canonical_manifest_value(raw)
            for key, raw in sorted(safe.items())
        }
    if isinstance(safe, list):
        return [_canonical_manifest_value(raw) for raw in safe]
    return safe


def _certified_type_case_contract(
    value: object,
) -> dict[str, Any] | None:
    """Delegate Type/Case contract validation to its owning consumer.

    ``generation`` imports this module, so the import must remain local.  At
    runtime its v2 validator is the single authority for the independently
    criticised per-QID placement shape and digest.
    """

    from . import generation

    contract = generation._certified_type_case_placement_contract(value)
    return contract if isinstance(contract, dict) else None


def _type_case_contract_digest(value: dict[str, Any]) -> str:
    from . import generation

    return generation._type_case_placement_digest(value)


def validate_type_case_qid_placement_ledger(
    value: object,
) -> dict[str, Any]:
    """Validate and canonicalize the authoritative per-QID placement ledger."""

    from . import generation

    ledger = generation._valid_type_case_qid_placement_ledger(value)
    if not isinstance(ledger, dict) or ledger.get("certified") is not True:
        raise GroundingCertificateError(
            "Type/Case QID placement ledger is not a certified, "
            "self-addressed v2 ledger"
        )
    if str(ledger.get("policy_version") or "") != (
        placement_policy.POLICY_VERSION
    ):
        raise GroundingCertificateError(
            "Type/Case QID placement ledger uses a stale placement policy"
        )
    placements = ledger.get("placements")
    if not isinstance(placements, Mapping) or not placements:
        raise GroundingCertificateError(
            "Type/Case QID placement ledger has no placements"
        )
    policy_version = str(ledger.get("policy_version") or "")
    teaching_order = str(ledger.get("teaching_order_sha256") or "")
    source_contract = str(ledger.get("source_contract_hash") or "")
    for raw_qid, raw_contract in placements.items():
        qid = str(raw_qid or "").strip()
        contract = _certified_type_case_contract(raw_contract)
        if not qid or contract is None:
            raise GroundingCertificateError(
                "Type/Case QID placement ledger contains an uncertified "
                "placement"
            )
        if (
            str(contract.get("policy_version") or "") != policy_version
            or str(contract.get("teaching_order_sha256") or "")
            != teaching_order
            or str(contract.get("source_contract_hash") or "")
            != source_contract
        ):
            raise GroundingCertificateError(
                f"Type/Case QID placement ledger authority disagrees for {qid}"
            )
        contract_qid = str(contract.get("qid") or "").strip()
        if contract_qid and contract_qid != qid:
            raise GroundingCertificateError(
                f"Type/Case QID placement ledger key disagrees for {qid}"
            )
    return _canonical_manifest_value(ledger)


def _type_case_host_placements(
    manifest: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return canonical QID host entries from a final-certificate manifest."""

    placements: dict[str, dict[str, Any]] = {}
    for row in manifest:
        row_manifest = row.get("manifest")
        if not isinstance(row_manifest, Mapping):
            continue
        raw_placements = row_manifest.get("placements")
        if not isinstance(raw_placements, Mapping):
            continue
        for raw_qid, raw_entry in raw_placements.items():
            qid = str(raw_qid or "").strip()
            if qid in placements:
                raise GroundingCertificateError(
                    f"Type/Case QID {qid} has more than one certified host"
                )
            placements[qid] = _canonical_manifest_value(raw_entry)
    return placements


def _validate_detached_type_case_host_manifest(
    value: object,
    *,
    row_index: int,
    source_contract_hash: str,
    policy_version: str,
    teaching_order_sha256: str,
) -> None:
    """Validate one row manifest when the underlying concept row is absent."""

    if not isinstance(value, Mapping) or int(value.get("version") or 0) != 2:
        raise GroundingCertificateError(
            f"Type/Case QID host placement manifest on row {row_index} is "
            "not a v2 manifest"
        )
    stored = str(value.get("certificate_sha256") or "")
    material = copy.deepcopy(dict(value))
    material.pop("certificate_sha256", None)
    if not _SHA256_RE.fullmatch(stored) or stored != _sha256_json(material):
        raise GroundingCertificateError(
            f"Type/Case QID host placement manifest on row {row_index} has "
            "a stale self-certificate"
        )
    if (
        str(value.get("source_contract_hash") or "") != source_contract_hash
        or str(value.get("policy_version") or "") != policy_version
        or str(value.get("teaching_order_sha256") or "")
        != teaching_order_sha256
    ):
        raise GroundingCertificateError(
            f"Type/Case QID host placement manifest on row {row_index} "
            "disagrees with final certificate authority"
        )
    placements = value.get("placements")
    if not isinstance(placements, Mapping) or not placements:
        raise GroundingCertificateError(
            f"Type/Case QID host placement manifest on row {row_index} has "
            "no placements"
        )
    for raw_qid, raw_entry in placements.items():
        qid = str(raw_qid or "").strip()
        if (
            not qid
            or not isinstance(raw_entry, Mapping)
            or str(raw_entry.get("qid") or "").strip() != qid
        ):
            raise GroundingCertificateError(
                f"Type/Case QID host placement manifest on row {row_index} "
                "has an invalid QID entry"
            )
        child = _certified_type_case_contract(
            raw_entry.get("placement_contract")
        )
        if child is None:
            raise GroundingCertificateError(
                f"Type/Case QID host placement for {qid} has no certified "
                "placement contract"
            )
        child_digest = _type_case_contract_digest(child)
        if (
            str(raw_entry.get("placement_certificate_sha256") or "")
            != child_digest
            or str(child.get("policy_version") or "") != policy_version
            or str(child.get("teaching_order_sha256") or "")
            != teaching_order_sha256
            or str(child.get("source_contract_hash") or "")
            != source_contract_hash
        ):
            raise GroundingCertificateError(
                f"Type/Case QID host placement for {qid} has stale or mixed "
                "placement authority"
            )
        expected_fields = {
            "source_location_topic_ids": list(
                child.get("source_location_topic_ids") or []),
            "owner_topic_id": str(child.get("owner_topic_id") or ""),
            "required_topic_ids": list(
                child.get("required_topic_ids") or []),
            "prerequisite_topic_ids": list(
                child.get("prerequisite_topic_ids") or []),
            "reference_edges": copy.deepcopy(
                child.get("reference_edges") or []),
            "illustration_topic_ids": list(
                child.get("illustration_topic_ids") or []),
            "topic_relationships": copy.deepcopy(
                child.get("topic_relationships") or []),
        }
        if any(
            _canonical_manifest_value(raw_entry.get(field))
            != _canonical_manifest_value(expected)
            for field, expected in expected_fields.items()
        ):
            raise GroundingCertificateError(
                f"Type/Case QID host placement projection disagrees with "
                f"its certified contract for {qid}"
            )
        if str(raw_entry.get("owner_topic_id") or "") != str(
            raw_entry.get("host_topic_id") or ""
        ):
            raise GroundingCertificateError(
                f"Type/Case QID host placement for {qid} is outside its "
                "certified owner topic"
            )


def validate_type_case_host_manifest_against_ledger(
    manifest: list[Mapping[str, Any]],
    ledger: object,
) -> dict[str, Any]:
    """Require exact QID and per-QID contract equality at the host boundary."""

    validated_ledger = validate_type_case_qid_placement_ledger(ledger)
    host_placements = _type_case_host_placements(manifest)
    ledger_placements = validated_ledger["placements"]
    if set(host_placements) != set(ledger_placements):
        missing = sorted(set(ledger_placements) - set(host_placements))
        extra = sorted(set(host_placements) - set(ledger_placements))
        raise GroundingCertificateError(
            "Type/Case QID host manifest does not exactly cover the certified "
            "placement ledger"
            + (f"; missing={','.join(missing)}" if missing else "")
            + (f"; extra={','.join(extra)}" if extra else "")
        )
    for qid, ledger_contract in ledger_placements.items():
        entry = host_placements[qid]
        if entry.get("placement_contract") != ledger_contract:
            raise GroundingCertificateError(
                f"Type/Case QID host manifest carries a different certified "
                f"placement contract for {qid}"
            )
    return validated_ledger


def type_case_host_placement_manifest(
    records: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Collect destination-row QID/Type/Case host attestations, when present."""

    out: list[dict[str, Any]] = []
    seen_qids: set[str] = set()
    for index, record in enumerate(records):
        if TYPE_CASE_HOST_MANIFEST_FIELD not in record:
            continue
        raw = record.get(TYPE_CASE_HOST_MANIFEST_FIELD)
        if isinstance(raw, Mapping) and "certificate_sha256" in raw:
            stored = str(raw.get("certificate_sha256") or "")
            material = copy.deepcopy(dict(raw))
            material.pop("certificate_sha256", None)
            if (
                not _SHA256_RE.fullmatch(stored)
                or stored != _sha256_json(material)
            ):
                raise GroundingCertificateError(
                    f"Type/Case QID host placement manifest on row {index} "
                    "has a stale self-certificate"
                )
            if str(raw.get("policy_version") or "") != (
                placement_policy.POLICY_VERSION
            ):
                raise GroundingCertificateError(
                    f"Type/Case QID host placement manifest on row {index} "
                    "uses a stale placement policy"
                )
            row_placement = placement_contract(record)
            row_teaching_order = str(
                (row_placement or {}).get("teaching_order_sha256") or ""
            )
            if row_teaching_order and str(
                raw.get("teaching_order_sha256") or ""
            ) != row_teaching_order:
                raise GroundingCertificateError(
                    f"Type/Case QID host placement manifest on row {index} "
                    "uses a different teaching order"
                )
            if _source_contract(record) and str(
                raw.get("source_contract_hash") or ""
            ) != _source_contract(record):
                raise GroundingCertificateError(
                    f"Type/Case QID host placement manifest on row {index} "
                    "uses a different source contract"
                )
            placements = raw.get("placements")
            if not isinstance(placements, Mapping):
                raise GroundingCertificateError(
                    f"Type/Case QID host placement manifest on row {index} "
                    "has no placements object"
                )
            semantic_topic_id = str(
                record.get("_semantic_topic_id") or ""
            ).strip()
            concept_title = _normal(
                record.get("concept_title") or record.get("concept")
            )
            for raw_qid, raw_entry in placements.items():
                qid = str(raw_qid or "").strip()
                if (
                    not qid
                    or qid in seen_qids
                    or not isinstance(raw_entry, Mapping)
                    or str(raw_entry.get("qid") or "").strip() != qid
                ):
                    raise GroundingCertificateError(
                        "Type/Case QID host placement manifest does not assign "
                        "every QID to one unambiguous destination"
                    )
                if semantic_topic_id and str(
                    raw_entry.get("host_topic_id") or ""
                ).strip() != semantic_topic_id:
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement for {qid} disagrees with "
                        "the destination row topic"
                    )
                if concept_title and _normal(
                    raw_entry.get("host_concept_title")
                ) != concept_title:
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement for {qid} disagrees with "
                        "the destination concept title"
                    )
                if not _SHA256_RE.fullmatch(str(
                    raw_entry.get("placement_certificate_sha256") or ""
                )):
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement for {qid} has no "
                        "placement certificate"
                    )
                child = _certified_type_case_contract(
                    raw_entry.get("placement_contract")
                )
                if child is None:
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement for {qid} has no exact "
                        "certified placement contract"
                    )
                child_digest = _type_case_contract_digest(child)
                if str(
                    raw_entry.get("placement_certificate_sha256") or ""
                ) != child_digest:
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement for {qid} has a stale "
                        "placement certificate"
                    )
                child_qid = str(child.get("qid") or "").strip()
                if child_qid and child_qid != qid:
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement contract disagrees "
                        f"with key {qid}"
                    )
                child_claim_id = str(
                    (child.get("qid_claim_ids") or {}).get(qid)
                    or child.get("claim_id")
                    or ""
                )
                if str(raw_entry.get("claim_id") or "") != child_claim_id:
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement claim disagrees for "
                        f"{qid}"
                    )
                expected_fields = {
                    "source_location_topic_ids": list(
                        child.get("source_location_topic_ids") or []),
                    "owner_topic_id": str(
                        child.get("owner_topic_id") or ""),
                    "required_topic_ids": list(
                        child.get("required_topic_ids") or []),
                    "prerequisite_topic_ids": list(
                        child.get("prerequisite_topic_ids") or []),
                    "reference_edges": copy.deepcopy(
                        child.get("reference_edges") or []),
                    "illustration_topic_ids": list(
                        child.get("illustration_topic_ids") or []),
                    "topic_relationships": copy.deepcopy(
                        child.get("topic_relationships") or []),
                }
                if any(
                    _canonical_manifest_value(raw_entry.get(field))
                    != _canonical_manifest_value(expected)
                    for field, expected in expected_fields.items()
                ):
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement projection disagrees "
                        f"with its certified contract for {qid}"
                    )
                if (
                    str(raw.get("policy_version") or "")
                    != str(child.get("policy_version") or "")
                    or str(raw.get("teaching_order_sha256") or "")
                    != str(child.get("teaching_order_sha256") or "")
                    or str(raw.get("source_contract_hash") or "")
                    != str(child.get("source_contract_hash") or "")
                ):
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement authority disagrees "
                        f"for {qid}"
                    )
                if str(raw_entry.get("owner_topic_id") or "") != str(
                    raw_entry.get("host_topic_id") or ""
                ):
                    raise GroundingCertificateError(
                        f"Type/Case QID host placement for {qid} is outside "
                        "its certified owner topic"
                    )
                seen_qids.add(qid)
        out.append({
            "row_index": index,
            "concept_id": _concept_id(record, index),
            "manifest": _canonical_manifest_value(
                raw
            ),
        })
    return out


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


def _active_semantic_block_directory(
    graph: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    """Index the active graph authority used to rebind relationship evidence.

    Older/offline callers can still verify a portable certificate without a
    graph.  Once a live semantic graph is supplied, however, a block identity
    is not sufficient authority by itself: its current topic and semantic kind
    must agree with the relationship that cites it.
    """

    directory: dict[str, dict[str, str]] = {}
    for raw in graph.get("blocks") or []:
        if not isinstance(raw, Mapping):
            continue
        block_id = str(raw.get("block_id") or "").strip()
        if not block_id:
            continue
        if block_id in directory:
            raise GroundingCertificateError(
                "active semantic graph repeats source block identity "
                f"{block_id}"
            )
        directory[block_id] = {
            "topic_id": str(raw.get("topic_id") or "").strip(),
            "kind": str(raw.get("kind") or "").strip().casefold(),
        }
    return directory


def _verify_relationship_evidence_against_active_graph(
    contract: Mapping[str, Any],
    *,
    block_directory: Mapping[str, Mapping[str, str]],
    context: str,
) -> None:
    """Rebind every certified relationship edge to the active block graph."""

    for raw in contract.get("topic_relationships") or []:
        if not isinstance(raw, Mapping):
            raise GroundingCertificateError(
                f"{context} has an invalid placement topic relationship"
            )
        relationship_id = str(raw.get("relationship_id") or "").strip()
        relationship_topic_id = str(raw.get("topic_id") or "").strip()
        if not relationship_id or not relationship_topic_id:
            raise GroundingCertificateError(
                f"{context} has a placement relationship without active "
                "identity/topic authority"
            )
        evidence_block_ids = [
            str(value).strip()
            for value in raw.get("evidence_block_ids") or []
            if str(value).strip()
        ]
        for block_id in evidence_block_ids:
            active = block_directory.get(block_id)
            if not isinstance(active, Mapping):
                raise GroundingCertificateError(
                    f"certified placement relationship {relationship_id} "
                    f"references source block {block_id}, which is absent "
                    "from the active semantic graph"
                )
            active_topic_id = str(active.get("topic_id") or "").strip()
            if not active_topic_id:
                raise GroundingCertificateError(
                    f"active semantic graph source block {block_id} has no "
                    "topic authority"
                )
            if active_topic_id != relationship_topic_id:
                raise GroundingCertificateError(
                    f"certified placement relationship {relationship_id} "
                    f"asserts topic {relationship_topic_id}, but active "
                    f"semantic graph source block {block_id} belongs to "
                    f"{active_topic_id}"
                )
            active_kind = str(active.get("kind") or "").strip().casefold()
            if not active_kind:
                raise GroundingCertificateError(
                    f"active semantic graph source block {block_id} has no "
                    "semantic content kind"
                )
            if active_kind in _NON_CONTENT_EVIDENCE_BLOCK_KINDS:
                raise GroundingCertificateError(
                    f"certified placement relationship {relationship_id} "
                    f"uses non-content source block {block_id} "
                    f"(kind={active_kind})"
                )


def _verify_type_case_placement_against_active_teaching_order(
    contract: Mapping[str, Any],
    *,
    order: placement_policy.TeachingOrder,
    source_contract_hash: str,
    qid: str,
) -> None:
    """Recompute one nested QID owner from its accepted relationships.

    Type/Case placement digests are content addresses, not signatures.  A
    consumer must therefore do more than verify that a declared owner has one
    accepted owning relationship: it must independently recompute the latest
    genuinely required owner under the active sealed teaching order.  Portable
    certificate verification remains graph-free; this check runs only from
    :func:`verify_semantic_topology` when the active graph is available.
    """

    context = f"Type/Case QID {qid or '<unknown>'}"
    if str(contract.get("policy_version") or "") != placement_policy.POLICY_VERSION:
        raise GroundingCertificateError(
            f"{context} placement uses a stale placement policy"
        )
    if str(contract.get("teaching_order_sha256") or "") != order.sha256:
        raise GroundingCertificateError(
            f"{context} placement was sealed against a different teaching order"
        )
    if str(contract.get("source_contract_hash") or "") != source_contract_hash:
        raise GroundingCertificateError(
            f"{context} placement was sealed against a different source contract"
        )

    claim_id = str(contract.get("claim_id") or "").strip()
    source_location_topic_id = str(
        contract.get("source_location_topic_id") or ""
    ).strip()
    if not source_location_topic_id:
        source_locations = [
            str(value).strip()
            for value in contract.get("source_location_topic_ids") or []
            if str(value).strip()
        ]
        if len(source_locations) == 1:
            source_location_topic_id = source_locations[0]
    if not claim_id or not source_location_topic_id:
        raise GroundingCertificateError(
            f"{context} placement has no atomic claim/source-location authority"
        )

    relationships: list[placement_policy.TopicRelationship] = []
    for raw in contract.get("topic_relationships") or []:
        if not isinstance(raw, Mapping):
            raise GroundingCertificateError(
                f"{context} placement has an invalid topic relationship"
            )
        kind = placement_policy.relationship_type(raw.get("relationship_type"))
        if kind is None:
            raise GroundingCertificateError(
                f"{context} placement has an unknown topic relationship"
            )
        relationships.append(placement_policy.TopicRelationship(
            claim_id=str(raw.get("claim_id") or ""),
            topic_id=str(raw.get("topic_id") or ""),
            relationship_type=kind,
            necessity=bool(raw.get("necessity")),
            evidence_block_ids=tuple(
                str(value)
                for value in raw.get("evidence_block_ids") or []
                if str(value)
            ),
            provider_reason=str(
                raw.get("provider_reason") or raw.get("reason") or ""
            ),
            critic_verdict=str(raw.get("critic_verdict") or ""),
        ))
    try:
        decision = placement_policy.compute_placement(
            placement_policy.AtomicClaim(
                claim_id=claim_id,
                normalized_claim=str(contract.get("normalized_claim") or ""),
                source_location_topic_id=source_location_topic_id,
                protected_source_items=((qid,) if qid else ()),
            ),
            relationships,
            order,
        )
    except placement_policy.PlacementPolicyError as exc:
        raise GroundingCertificateError(
            f"{context} placement cannot be recomputed against the active "
            f"teaching order: {exc}"
        ) from exc

    declared_references = {
        tuple(str(part) for part in edge)
        for edge in contract.get("reference_edges") or []
        if isinstance(edge, (list, tuple)) and len(edge) == 2
    }
    if (
        decision.owner_topic_id != str(contract.get("owner_topic_id") or "")
        or set(decision.required_topic_ids)
        != {str(value) for value in contract.get("required_topic_ids") or []}
        or set(decision.prerequisite_topic_ids)
        != {
            str(value)
            for value in contract.get("prerequisite_topic_ids") or []
        }
        or set(decision.reference_edges) != declared_references
        or set(decision.illustration_topic_ids)
        != {
            str(value)
            for value in contract.get("illustration_topic_ids") or []
        }
    ):
        raise GroundingCertificateError(
            f"{context} placement disagrees with the active teaching order"
        )


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


def row_identity_material(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable public/graph identity fixed at topology freeze."""

    placement = verify_placement_contract(record)
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
        "placement_contract": copy.deepcopy(placement or {}),
    }


def verify_row_identity(
    record: Mapping[str, Any], *, row_index: int,
) -> dict[str, Any]:
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
    placement = verify_placement_contract(record)
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
        "placement_contract": copy.deepcopy(placement or {}),
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
                "placement_certificate_sha256": str(
                    (placement_contract(record) or {}).get(
                        "placement_certificate_sha256"
                    ) or ""
                ),
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


def _verify_split_attestation_groups(
    records: list[Mapping[str, Any]],
) -> None:
    """Verify every attested split as one exact all-sibling transaction."""

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        contract = placement_contract(record)
        if not contract:
            continue
        attestation = contract.get("split_attestation") or {}
        is_split = str(record.get("_phase32_topology_decision") or "") == "split"
        if is_split and not attestation:
            raise GroundingCertificateError(
                f"split child {contract.get('claim_id') or '<unknown>'} lost "
                "its structured split-survival attestation"
            )
        if not attestation:
            continue
        group_id = str(contract.get("split_group_id") or "")
        groups.setdefault(group_id, []).append({
            "record": record,
            "contract": contract,
            "attestation": placement_policy.normalized_split_attestation(
                attestation
            ),
        })

    for group_id, siblings in groups.items():
        all_group_rows = [
            placement_contract(record)
            for record in records
            if placement_contract(record)
            and str((placement_contract(record) or {}).get("split_group_id") or "")
            == group_id
        ]
        if len(all_group_rows) != len(siblings):
            raise GroundingCertificateError(
                f"split group {group_id} has a sibling without its attestation"
            )
        canonical = siblings[0]["attestation"]
        if any(row["attestation"] != canonical for row in siblings[1:]):
            raise GroundingCertificateError(
                f"split group {group_id} siblings carry different attestations"
            )
        material = canonical.get("material") or {}
        if str(material.get("split_group_id") or "") != group_id:
            raise GroundingCertificateError(
                f"split group {group_id} attestation names a different group"
            )
        expected_children = {
            str(child.get("claim_id") or ""): child
            for child in material.get("children") or []
            if isinstance(child, Mapping)
            and str(child.get("claim_id") or "")
        }
        actual_children = {
            str(row["contract"].get("claim_id") or ""): row["contract"]
            for row in siblings
        }
        if set(expected_children) != set(actual_children):
            raise GroundingCertificateError(
                f"split group {group_id} final child set differs from its "
                "attested child set"
            )
        parent = material.get("parent") or {}
        parent_claim_id = str(parent.get("claim_id") or "")
        parent_claim_sha256 = str(parent.get("claim_sha256") or "")
        parent_origins = sorted(set(parent.get("origin_claim_ids") or []))
        parent_items = sorted(set(parent.get("protected_source_items") or []))
        if parent_claim_id not in parent_origins:
            raise GroundingCertificateError(
                f"split group {group_id} parent is absent from its origin lineage"
            )
        child_items: list[str] = []
        for claim_id, contract in actual_children.items():
            expected_child = expected_children[claim_id]
            if placement_policy.claim_text_sha256(
                contract.get("normalized_claim")
            ) != str(expected_child.get("claim_sha256") or ""):
                raise GroundingCertificateError(
                    f"split group {group_id} child {claim_id} changed after review"
                )
            if sorted(set(contract.get("origin_claim_ids") or [])) != parent_origins:
                raise GroundingCertificateError(
                    f"split group {group_id} child {claim_id} has stale parent lineage"
                )
            if str(contract.get("origin_claim_sha256") or "") != parent_claim_sha256:
                raise GroundingCertificateError(
                    f"split group {group_id} child {claim_id} has a different "
                    "parent claim hash"
                )
            child_items.extend(contract.get("protected_source_items") or [])
        duplicates = sorted({item for item in child_items if child_items.count(item) > 1})
        if duplicates or sorted(set(child_items)) != parent_items:
            detail = (
                "duplicate protected item(s) " + ", ".join(duplicates)
                if duplicates
                else "protected inventory differs from the parent"
            )
            raise GroundingCertificateError(
                f"split group {group_id} {detail}"
            )


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
        verified_placement = verify_placement_contract(
            record,
            allowed_block_ids=allowed,
        )
        if verified_placement is not None:
            # Persist only the shared canonical projection.  Provider-only
            # aliases and object key order must not create a second digest.
            record[PLACEMENT_CONTRACT_FIELD] = verified_placement
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
    _verify_split_attestation_groups(records)
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
    *,
    type_case_qid_placement_ledger: object | None = None,
    require_placement_contracts: bool = False,
) -> dict[str, Any]:
    """Build a certificate for the exact ordered payload sent to deposit."""

    if not records:
        raise GroundingCertificateError(
            "final grounding certificate cannot certify an empty concept map"
        )
    lineage_digest = verify_lineage(records)
    _verify_split_attestation_groups(records)
    source_contracts: set[str] = set()
    semantic_topologies: set[str] = set()
    placement_policy_versions: set[str] = set()
    teaching_orders: set[str] = set()
    evidence: list[dict[str, Any]] = []
    placement_manifest: list[dict[str, Any]] = []
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
        row_placement = verify_placement_contract(record)
        title = str(record.get("concept_title") or record.get("concept") or "")
        if (
            require_placement_contracts
            and not cr.is_culmination(title)
            and row_placement is None
        ):
            raise GroundingCertificateError(
                "final grounding certificate cannot certify production row "
                f"{index} without independent placement authority"
            )
        if row_placement:
            placement_policy_versions.add(str(
                row_placement.get("policy_version") or ""
            ))
            teaching_orders.add(str(
                row_placement.get("teaching_order_sha256") or ""
            ))
        placement_manifest.append({
            "row_index": index,
            "concept_id": _concept_id(record, index),
            "placement_contract": copy.deepcopy(row_placement or {}),
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
            "placement_contract": copy.deepcopy(row_placement or {}),
            "placement_certificate_sha256": str(
                (row_placement or {}).get("placement_certificate_sha256")
                or ""
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
    placement_policy_versions.discard("")
    teaching_orders.discard("")
    if len(placement_policy_versions) > 1 or len(teaching_orders) > 1:
        raise GroundingCertificateError(
            "final grounding certificate requires one placement policy and "
            "one sealed teaching order"
        )
    if bool(placement_policy_versions) != bool(teaching_orders):
        raise GroundingCertificateError(
            "final grounding certificate has an incomplete placement authority"
        )
    type_case_manifest = type_case_host_placement_manifest(records)
    validated_type_case_ledger: dict[str, Any] | None = None
    if type_case_manifest:
        if type_case_qid_placement_ledger is None:
            raise GroundingCertificateError(
                "final grounding certificate cannot bind Type/Case QID hosts "
                "without the authoritative placement ledger"
            )
        validated_type_case_ledger = (
            validate_type_case_host_manifest_against_ledger(
                type_case_manifest,
                type_case_qid_placement_ledger,
            )
        )
    elif type_case_qid_placement_ledger is not None:
        # A certified ledger with no destination manifest is silent QID loss.
        validated_type_case_ledger = (
            validate_type_case_host_manifest_against_ledger(
                type_case_manifest,
                type_case_qid_placement_ledger,
            )
        )
    if validated_type_case_ledger is not None:
        if str(
            validated_type_case_ledger.get("source_contract_hash") or ""
        ) != next(iter(source_contracts)):
            raise GroundingCertificateError(
                "Type/Case QID placement ledger uses a different source "
                "contract"
            )
        placement_policy_versions.add(str(
            validated_type_case_ledger.get("policy_version") or ""
        ))
        teaching_orders.add(str(
            validated_type_case_ledger.get("teaching_order_sha256") or ""
        ))
        if len(placement_policy_versions) != 1 or len(teaching_orders) != 1:
            raise GroundingCertificateError(
                "Type/Case QID placement ledger disagrees with the concept "
                "placement authority"
            )
    ledger_sha256 = str(
        (validated_type_case_ledger or {}).get("certificate_sha256") or ""
    )
    ledger_qid_count = len(
        (validated_type_case_ledger or {}).get("placements") or {}
    )
    base = {
        "version": FINAL_CERTIFICATE_VERSION,
        "source_contract_hash": next(iter(source_contracts)),
        "semantic_topology_sha256": next(iter(semantic_topologies)),
        "placement_policy_version": next(
            iter(placement_policy_versions), ""
        ),
        "teaching_order_sha256": next(iter(teaching_orders), ""),
        "record_count": len(records),
        "lineage_sha256": lineage_digest,
        "public_payload_sha256": _sha256_json(public_payload),
        "grounding_manifest_sha256": _sha256_json(evidence),
        "placement_manifest_sha256": _sha256_json(placement_manifest),
        "type_case_qid_host_placement_manifest_sha256": _sha256_json(
            type_case_manifest
        ),
        "type_case_qid_placement_ledger_sha256": ledger_sha256,
        "type_case_qid_placement_qid_count": ledger_qid_count,
        "evidence": evidence,
        "placement_manifest": placement_manifest,
        "type_case_qid_host_placement_manifest": type_case_manifest,
    }
    return {
        **base,
        "certificate_sha256": _sha256_json(base),
    }


def verify_certificate_envelope(
    certificate: Mapping[str, Any] | None,
    *,
    type_case_qid_placement_ledger: object | None = None,
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
        "placement_policy_version",
        "teaching_order_sha256",
        "record_count",
        "lineage_sha256",
        "public_payload_sha256",
        "grounding_manifest_sha256",
        "placement_manifest_sha256",
        "type_case_qid_host_placement_manifest_sha256",
        "type_case_qid_placement_ledger_sha256",
        "type_case_qid_placement_qid_count",
        "evidence",
        "placement_manifest",
        "type_case_qid_host_placement_manifest",
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
        "placement_manifest_sha256",
        "type_case_qid_host_placement_manifest_sha256",
        "certificate_sha256",
    ):
        if not _SHA256_RE.fullmatch(str(value.get(field) or "")):
            raise GroundingCertificateError(
                f"final grounding certificate {field} is not a SHA-256"
            )
    policy_version = str(value.get("placement_policy_version") or "")
    teaching_order = str(value.get("teaching_order_sha256") or "")
    if bool(policy_version) != bool(teaching_order):
        raise GroundingCertificateError(
            "final grounding certificate has incomplete placement authority"
        )
    if policy_version and policy_version != placement_policy.POLICY_VERSION:
        raise GroundingCertificateError(
            "final grounding certificate placement policy is stale"
        )
    if teaching_order and not _SHA256_RE.fullmatch(teaching_order):
        raise GroundingCertificateError(
            "final grounding certificate teaching order is not a SHA-256"
        )
    ledger_sha256 = str(
        value.get("type_case_qid_placement_ledger_sha256") or ""
    )
    ledger_qid_count = value.get("type_case_qid_placement_qid_count")
    if (
        isinstance(ledger_qid_count, bool)
        or not isinstance(ledger_qid_count, int)
        or not 0 <= ledger_qid_count <= 50_000
        or (ledger_qid_count == 0 and ledger_sha256)
        or (
            ledger_qid_count > 0
            and not _SHA256_RE.fullmatch(ledger_sha256)
        )
    ):
        raise GroundingCertificateError(
            "final grounding certificate Type/Case ledger authority is invalid"
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
        "placement_contract",
        "placement_certificate_sha256",
    }
    identity_fields = {
        "version",
        "topic",
        "parent_concept",
        "concept_title",
        "semantic_topic_id",
        "placement_contract",
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
        if identity.get("placement_contract") != raw.get(
            "placement_contract"
        ):
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has "
                "placement material outside its row identity"
            )
        if not _SHA256_RE.fullmatch(str(
            raw.get("row_certificate_sha256") or ""
        )):
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has an "
                "invalid row certificate hash"
            )
        placement_value = raw.get("placement_contract")
        if not isinstance(placement_value, Mapping):
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has an "
                "invalid placement contract"
            )
        placement_digest = str(
            raw.get("placement_certificate_sha256") or ""
        )
        if placement_value:
            if placement_value.get("certified") is not True:
                raise GroundingCertificateError(
                    f"final grounding certificate evidence row {index} has "
                    "declared but uncertified placement material"
                )
            if placement_value.get("policy_version") != (
                placement_policy.POLICY_VERSION
            ):
                raise GroundingCertificateError(
                    f"final grounding certificate evidence row {index} uses "
                    "a stale placement policy"
                )
            normalized = placement_policy.normalized_placement_contract(
                placement_value
            )
            canonical = {
                **normalized,
                "placement_certificate_sha256": placement_digest,
            }
            if dict(placement_value) != canonical:
                raise GroundingCertificateError(
                    f"final grounding certificate evidence row {index} has "
                    "a non-canonical placement contract"
                )
            if placement_value.get("certified") and (
                not _SHA256_RE.fullmatch(placement_digest)
                or placement_digest
                != placement_policy.placement_contract_sha256(placement_value)
            ):
                raise GroundingCertificateError(
                    f"final grounding certificate evidence row {index} has "
                    "a stale placement certificate"
                )
        elif placement_digest:
            raise GroundingCertificateError(
                f"final grounding certificate evidence row {index} has a "
                "placement digest without placement material"
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
    placement_manifest = value.get("placement_manifest")
    if (
        not isinstance(placement_manifest, list)
        or len(placement_manifest) != record_count
    ):
        raise GroundingCertificateError(
            "final grounding certificate placement manifest count does not "
            "match its record count"
        )
    for index, row in enumerate(placement_manifest):
        if (
            not isinstance(row, Mapping)
            or set(row) != {
                "row_index", "concept_id", "placement_contract"
            }
            or row.get("row_index") != index
            or str(row.get("concept_id") or "")
            != _expected_concept_id(index)
            or row.get("placement_contract")
            != evidence[index].get("placement_contract")
        ):
            raise GroundingCertificateError(
                f"final grounding certificate placement row {index} is invalid"
            )
    manifest_policies = {
        str(row["placement_contract"].get("policy_version") or "")
        for row in placement_manifest
        if row.get("placement_contract")
    }
    manifest_orders = {
        str(row["placement_contract"].get("teaching_order_sha256") or "")
        for row in placement_manifest
        if row.get("placement_contract")
    }
    manifest_policies.discard("")
    manifest_orders.discard("")
    if manifest_policies != ({policy_version} if policy_version else set()) or (
        manifest_orders != ({teaching_order} if teaching_order else set())
    ):
        raise GroundingCertificateError(
            "final grounding certificate placement authority disagrees with "
            "its row manifest"
        )
    if value["placement_manifest_sha256"] != _sha256_json(placement_manifest):
        raise GroundingCertificateError(
            "final grounding certificate placement manifest hash is stale"
        )

    type_case_manifest = value.get(
        "type_case_qid_host_placement_manifest"
    )
    if (
        not isinstance(type_case_manifest, list)
        or len(type_case_manifest) > 5_000
    ):
        raise GroundingCertificateError(
            "final grounding certificate Type/Case host manifest is invalid"
        )
    seen_rows: set[int] = set()
    for row in type_case_manifest:
        row_index = row.get("row_index") if isinstance(row, Mapping) else None
        if (
            not isinstance(row, Mapping)
            or set(row) != {"row_index", "concept_id", "manifest"}
            or isinstance(row_index, bool)
            or not isinstance(row_index, int)
            or not 0 <= row_index < record_count
            or row_index in seen_rows
            or str(row.get("concept_id") or "")
            != _expected_concept_id(row_index)
            or row.get("manifest")
            != _canonical_manifest_value(row.get("manifest"))
        ):
            raise GroundingCertificateError(
                "final grounding certificate Type/Case host manifest contains "
                "an invalid destination row"
            )
        _validate_detached_type_case_host_manifest(
            row.get("manifest"),
            row_index=row_index,
            source_contract_hash=str(value["source_contract_hash"]),
            policy_version=policy_version,
            teaching_order_sha256=teaching_order,
        )
        seen_rows.add(row_index)
    host_placements = _type_case_host_placements(type_case_manifest)
    if len(host_placements) != ledger_qid_count:
        raise GroundingCertificateError(
            "final grounding certificate Type/Case host count does not match "
            "its certified placement ledger"
        )
    if type_case_manifest and not ledger_sha256:
        raise GroundingCertificateError(
            "final grounding certificate Type/Case hosts have no certified "
            "placement ledger"
        )
    if type_case_manifest and type_case_qid_placement_ledger is None:
        raise GroundingCertificateError(
            "final grounding certificate Type/Case hosts cannot be verified "
            "without the authoritative placement ledger"
        )
    # Validate every nested contract even when the records are unavailable,
    # as is the case for a portable checkpoint audit.
    for qid, entry in host_placements.items():
        child = _certified_type_case_contract(entry.get("placement_contract"))
        if child is None or str(
            entry.get("placement_certificate_sha256") or ""
        ) != _type_case_contract_digest(child):
            raise GroundingCertificateError(
                f"final grounding certificate Type/Case host {qid} carries "
                "a stale placement contract"
            )
    if type_case_qid_placement_ledger is not None:
        validated_ledger = validate_type_case_host_manifest_against_ledger(
            type_case_manifest,
            type_case_qid_placement_ledger,
        )
        if (
            validated_ledger.get("certificate_sha256") != ledger_sha256
            or len(validated_ledger.get("placements") or {})
            != ledger_qid_count
        ):
            raise GroundingCertificateError(
                "final grounding certificate was sealed against a different "
                "Type/Case QID placement ledger"
            )
    if value[
        "type_case_qid_host_placement_manifest_sha256"
    ] != _sha256_json(type_case_manifest):
        raise GroundingCertificateError(
            "final grounding certificate Type/Case host manifest hash is stale"
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
    block_directory = _active_semantic_block_directory(semantic_graph)
    for row in certificate.get("placement_manifest") or []:
        contract = (
            row.get("placement_contract")
            if isinstance(row, Mapping)
            else None
        )
        if not isinstance(contract, Mapping) or not contract:
            continue
        _verify_relationship_evidence_against_active_graph(
            contract,
            block_directory=block_directory,
            context=(
                "final grounding certificate concept placement "
                + str(row.get("concept_id") or "row")
            ),
        )
    for row in certificate.get(
        "type_case_qid_host_placement_manifest"
    ) or []:
        manifest = row.get("manifest") if isinstance(row, Mapping) else None
        placements = (
            manifest.get("placements")
            if isinstance(manifest, Mapping)
            else None
        )
        if not isinstance(placements, Mapping):
            continue
        for raw_qid, entry in placements.items():
            contract = (
                entry.get("placement_contract")
                if isinstance(entry, Mapping)
                else None
            )
            if not isinstance(contract, Mapping) or not contract:
                continue
            _verify_relationship_evidence_against_active_graph(
                contract,
                block_directory=block_directory,
                context=(
                    "final grounding certificate Type/Case placement "
                    + str(raw_qid or "QID")
                ),
            )
    teaching_order = str(certificate.get("teaching_order_sha256") or "")
    if teaching_order:
        sealed_order = placement_policy.seal_teaching_order(
            semantic_graph,
            source_contract_hash=str(
                semantic_graph.get("source_contract_hash") or ""
            ),
        )
        if teaching_order != sealed_order.sha256:
            raise GroundingCertificateError(
                "final grounding certificate was sealed against a different "
                "teaching order"
            )
        for row in certificate.get(
            "type_case_qid_host_placement_manifest"
        ) or []:
            manifest = row.get("manifest") if isinstance(row, Mapping) else None
            placements = (
                manifest.get("placements")
                if isinstance(manifest, Mapping)
                else None
            )
            if not isinstance(placements, Mapping):
                continue
            for raw_qid, entry in placements.items():
                contract = (
                    entry.get("placement_contract")
                    if isinstance(entry, Mapping)
                    else None
                )
                validated = _certified_type_case_contract(contract)
                if validated is None:
                    raise GroundingCertificateError(
                        "final grounding certificate Type/Case placement "
                        f"{str(raw_qid or '<unknown>')} is not certified"
                    )
                _verify_type_case_placement_against_active_teaching_order(
                    validated,
                    order=sealed_order,
                    source_contract_hash=str(
                        semantic_graph.get("source_contract_hash") or ""
                    ),
                    qid=str(raw_qid or ""),
                )
        for row in certificate.get("placement_manifest") or []:
            contract = (
                row.get("placement_contract")
                if isinstance(row, Mapping)
                else None
            )
            if not isinstance(contract, Mapping) or not contract:
                continue
            claim = placement_policy.AtomicClaim(
                claim_id=str(contract.get("claim_id") or ""),
                normalized_claim=str(contract.get("normalized_claim") or ""),
                source_location_topic_id=str(
                    contract.get("source_location_topic_id") or ""
                ),
                origin_claim_ids=tuple(
                    str(value)
                    for value in contract.get("origin_claim_ids") or []
                    if str(value)
                ),
                split_group_id=str(contract.get("split_group_id") or ""),
                protected_source_items=tuple(
                    str(value)
                    for value in contract.get("protected_source_items") or []
                    if str(value)
                ),
            )
            relationships: list[placement_policy.TopicRelationship] = []
            for raw in contract.get("topic_relationships") or []:
                if not isinstance(raw, Mapping):
                    continue
                kind = placement_policy.relationship_type(
                    raw.get("relationship_type")
                )
                if kind is None:
                    continue
                relationships.append(placement_policy.TopicRelationship(
                    claim_id=str(raw.get("claim_id") or ""),
                    topic_id=str(raw.get("topic_id") or ""),
                    relationship_type=kind,
                    necessity=bool(raw.get("necessity")),
                    evidence_block_ids=tuple(
                        str(value)
                        for value in raw.get("evidence_block_ids") or []
                        if str(value)
                    ),
                    provider_reason=str(raw.get("provider_reason") or ""),
                    critic_verdict=str(raw.get("critic_verdict") or ""),
                ))
            try:
                decision = placement_policy.compute_placement(
                    claim,
                    relationships,
                    sealed_order,
                )
            except placement_policy.PlacementPolicyError as exc:
                raise GroundingCertificateError(
                    "final grounding certificate placement cannot be "
                    f"recomputed against the active teaching order: {exc}"
                ) from exc
            if (
                decision.owner_topic_id != contract.get("owner_topic_id")
                or list(decision.required_topic_ids)
                != list(contract.get("required_topic_ids") or [])
                or list(decision.prerequisite_topic_ids)
                != list(contract.get("prerequisite_topic_ids") or [])
                or [list(edge) for edge in decision.reference_edges]
                != list(contract.get("reference_edges") or [])
                or list(decision.illustration_topic_ids)
                != list(contract.get("illustration_topic_ids") or [])
            ):
                raise GroundingCertificateError(
                    "final grounding certificate placement disagrees with "
                    "the active teaching order"
                )
    return expected


def verify_final_certificate(
    records: list[dict[str, Any]],
    certificate: Mapping[str, Any] | None,
    *,
    semantic_graph: Mapping[str, Any] | None = None,
    require_semantic_graph: bool = False,
    require_placement_contracts: bool = False,
    type_case_qid_placement_ledger: object | None = None,
) -> dict[str, Any]:
    """Recompute and compare the final certificate immediately before use."""

    supplied = verify_certificate_envelope(
        certificate,
        type_case_qid_placement_ledger=type_case_qid_placement_ledger,
    )
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
            # The per-row identity hash can only say "changed", not what
            # changed; the certificate's readable material can. Diff it
            # field-by-field so a drifted deposit payload names the exact
            # field and both values instead of only the recomputed side.
            sealed_identity = sealed.get("row_identity")
            if isinstance(sealed_identity, Mapping):
                current_identity = row_identity_material(record)
                drifted = [
                    field
                    for field in sorted(
                        set(sealed_identity) | set(current_identity), key=str,
                    )
                    if _json_safe(sealed_identity.get(field))
                    != _json_safe(current_identity.get(field))
                ]
                if drifted:
                    detail = "; ".join(
                        f"{field}: attested "
                        + json.dumps(
                            _json_safe(sealed_identity.get(field)),
                            ensure_ascii=False,
                        )[:300]
                        + " -> current "
                        + json.dumps(
                            _json_safe(current_identity.get(field)),
                            ensure_ascii=False,
                        )[:300]
                        for field in drifted
                    )
                    raise GroundingCertificateError(
                        "grounding certificate identity/topology drift for "
                        f"{concept_id}: {detail}"
                    )
    expected = (
        build_final_certificate(
            records,
            require_placement_contracts=require_placement_contracts,
        )
        if type_case_qid_placement_ledger is None
        else build_final_certificate(
            records,
            type_case_qid_placement_ledger=type_case_qid_placement_ledger,
            require_placement_contracts=require_placement_contracts,
        )
    )
    if supplied != expected:
        raise GroundingCertificateError(
            "final grounding certificate does not match the exact concept "
            "payload and evidence set"
        )
    return expected
