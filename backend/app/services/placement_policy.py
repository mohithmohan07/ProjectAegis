"""Typed topic-ownership policy for concepts, Types, Cases and tasks.

Placement used to be a free-form model judgement expressed only as prose
inside prompts.  That could not be tested, could not be audited, and could
silently disagree with the stage that actually moved a concept.  Worse, the
rules were wired into exact grounding (Phase 3.1/3.8) while ``keep / move /
refine / split`` is decided in topology adjudication (Phase 3.2), which never
received them.

This module restores the intended authority boundary:

* the **provider model** classifies which topics a claim genuinely relates to
  and cites exact evidence for each;
* **deterministic code here** computes the owner from those certified
  relationships plus the sealed teaching order;
* an **independent critic** challenges the classification, the dependency
  direction, evidence sufficiency, and the resulting placement.

The ownership rule, stated once:

    owner_topic_id = the latest teaching-ranked topic whose knowledge,
    method, or interpretive framework is genuinely necessary to understand
    or perform the atomic claim or task.

None of the following may establish ownership on its own — each is explicitly
neutralised by :func:`compute_placement`:

* physical page or block location          * incidental mention
* section numbering                        * optional later solution methods
* chronology                               * candidate-list or QID order
* terminology / lexical overlap

See ``docs/concept-placement-rules.md`` for the product policy this encodes.
That document is authoritative; where it and this module disagree, this
module has the defect.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

POLICY_VERSION = "aegis-placement-policy-2"
SPLIT_ATTESTATION_VERSION = "aegis-split-survival-attestation-1"


class RelationshipType(str, Enum):
    """How one atomic claim relates to one topic.

    Only ``CORE_TEACHING`` and ``REQUIRED_LATER_METHOD`` can carry ownership.
    ``REQUIRED_PREREQUISITE`` is genuinely necessary but, by definition,
    earlier — it makes that topic a prerequisite, not the owner.  The
    remaining three are explicitly non-owning and exist so the model has a
    truthful way to say "this topic is involved but does not own it", instead
    of being forced into a binary that pulls material to the wrong section.
    """

    #: The topic teaches this claim directly.
    CORE_TEACHING = "CORE_TEACHING"
    #: Needed to understand the claim, and taught earlier.  Never owns.
    REQUIRED_PREREQUISITE = "REQUIRED_PREREQUISITE"
    #: The claim cannot be understood or performed without this later topic's
    #: method or interpretive framework.  Owns when it is the latest such.
    REQUIRED_LATER_METHOD = "REQUIRED_LATER_METHOD"
    #: A later topic points back at this material.  Creates an edge only.
    RETROSPECTIVE_REFERENCE = "RETROSPECTIVE_REFERENCE"
    #: A later topic independently teaches an illustration/consequence of it.
    #: Justifies a separate later-owned concept, not moving this claim.
    SUBSTANTIVE_LATER_ILLUSTRATION = "SUBSTANTIVE_LATER_ILLUSTRATION"
    #: Named in passing.  Never relevant to ownership.
    INCIDENTAL_MENTION = "INCIDENTAL_MENTION"


#: Relationship types that may make a topic the owner.
OWNING_TYPES = frozenset({
    RelationshipType.CORE_TEACHING,
    RelationshipType.REQUIRED_LATER_METHOD,
})

#: Genuinely necessary, but never sufficient for ownership on its own.
NECESSARY_TYPES = OWNING_TYPES | {RelationshipType.REQUIRED_PREREQUISITE}

#: Types that create a typed edge and must never move or duplicate a claim.
EDGE_ONLY_TYPES = frozenset({
    RelationshipType.RETROSPECTIVE_REFERENCE,
    RelationshipType.INCIDENTAL_MENTION,
})


class PlacementPolicyError(RuntimeError):
    """A placement could not be computed from certified relationships."""


def relationship_type(value: object) -> RelationshipType | None:
    """Parse a provider-supplied relationship label, or None if unknown.

    Unknown labels are never coerced to a default.  An unparsable
    classification is treated as absent so it cannot silently acquire
    ownership authority.
    """

    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    try:
        return RelationshipType(text)
    except ValueError:
        return None


def _normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        ).encode("utf-8")
    ).hexdigest()


def claim_text_sha256(value: object) -> str:
    """Hash one normalized source-facing claim for split-lineage attestation."""

    return hashlib.sha256(_normal(value).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Teaching order
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TeachingOrder:
    """Sealed order in which a chapter's topics are taught.

    Derived from the position of each topic's earliest source block in the
    sealed source contract — never from parsing section numbers.  A chapter
    whose headings are "2.2", "Unit Two", "Ch. II" or unnumbered all rank
    identically well, and a converter that renumbers headings cannot change
    the ranking as long as reading order is unchanged.
    """

    ranks: dict[str, int]
    titles: dict[str, str]
    source_contract_hash: str = ""

    def rank(self, topic_id: object) -> int | None:
        return self.ranks.get(str(topic_id or ""))

    def title(self, topic_id: object) -> str:
        return self.titles.get(str(topic_id or ""), "")

    def known(self, topic_id: object) -> bool:
        return str(topic_id or "") in self.ranks

    def latest(self, topic_ids: Iterable[object]) -> str:
        """Return the latest-taught topic among ``topic_ids``.

        Ties cannot occur: ranks are dense and unique per topic.
        """

        ranked = [
            (self.ranks[str(topic_id)], str(topic_id))
            for topic_id in topic_ids
            if str(topic_id or "") in self.ranks
        ]
        if not ranked:
            return ""
        return max(ranked)[1]

    @property
    def sha256(self) -> str:
        return _sha256_json({
            "policy_version": POLICY_VERSION,
            "source_contract_hash": self.source_contract_hash,
            "order": [
                topic_id
                for _rank, topic_id in sorted(
                    (rank, topic_id) for topic_id, rank in self.ranks.items()
                )
            ],
        })


def seal_teaching_order(
    graph: Mapping[str, Any],
    *,
    source_contract_hash: str = "",
) -> TeachingOrder:
    """Seal the teaching order from a verified semantic graph.

    Topics are ranked by the earliest source block assigned to them.  A topic
    with no blocks keeps its declared position relative to the topics around
    it, so an empty section cannot silently jump to the front or back.
    """

    first_block: dict[str, int] = {}
    for block in graph.get("blocks") or []:
        if not isinstance(block, Mapping):
            continue
        topic_id = str(block.get("topic_id") or "")
        if not topic_id:
            continue
        try:
            order = int(block.get("order") or 0)
        except (TypeError, ValueError):
            continue
        if topic_id not in first_block or order < first_block[topic_id]:
            first_block[topic_id] = order

    declared: list[str] = []
    titles: dict[str, str] = {}
    for topic in graph.get("topics") or []:
        if not isinstance(topic, Mapping):
            continue
        topic_id = str(topic.get("topic_id") or "")
        if not topic_id or topic_id in titles:
            continue
        declared.append(topic_id)
        titles[topic_id] = str(topic.get("title") or "")

    # Sort by earliest block. A blockless heading is interpolated between its
    # nearest declared neighbours instead of being pushed to the chapter end.
    # Fractions make this stable without depending on floating-point rounding.
    known_indices = [
        index for index, topic_id in enumerate(declared)
        if topic_id in first_block
    ]

    def sort_key(item: tuple[int, str]) -> tuple[Fraction, int]:
        index, topic_id = item
        if topic_id in first_block:
            return (Fraction(first_block[topic_id]), index)
        previous = next(
            (value for value in reversed(known_indices) if value < index),
            None,
        )
        following = next(
            (value for value in known_indices if value > index),
            None,
        )
        if previous is not None and following is not None:
            left = Fraction(first_block[declared[previous]])
            right = Fraction(first_block[declared[following]])
            distance = following - previous
            return (
                left + (right - left) * Fraction(index - previous, distance),
                index,
            )
        if previous is not None:
            return (
                Fraction(first_block[declared[previous]] + index - previous),
                index,
            )
        if following is not None:
            return (
                Fraction(first_block[declared[following]] - following + index),
                index,
            )
        return (Fraction(index), index)

    ordered = sorted(enumerate(declared), key=sort_key)
    ranks = {topic_id: rank for rank, (_i, topic_id) in enumerate(ordered)}
    return TeachingOrder(
        ranks=ranks,
        titles=titles,
        source_contract_hash=str(source_contract_hash or ""),
    )


# --------------------------------------------------------------------------- #
# Data contract
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AtomicClaim:
    """One independently verifiable claim or task.

    ``protected_source_items`` carries the QIDs, figure IDs, image tags and
    KaTeX spans the claim owns.  Nothing may drop a claim while these are
    unaccounted for, which is what makes split survival checkable.
    """

    claim_id: str
    normalized_claim: str
    source_location_topic_id: str = ""
    evidence_block_ids: tuple[str, ...] = ()
    origin_claim_ids: tuple[str, ...] = ()
    split_group_id: str = ""
    protected_source_items: tuple[str, ...] = ()

    @property
    def sha256(self) -> str:
        return _sha256_json({
            "claim": _normal(self.normalized_claim),
            "evidence": sorted(self.evidence_block_ids),
            "protected": sorted(self.protected_source_items),
        })


@dataclass(frozen=True)
class TopicRelationship:
    """One provider-classified, critic-checkable claim/topic relationship."""

    claim_id: str
    topic_id: str
    relationship_type: RelationshipType
    necessity: bool
    evidence_block_ids: tuple[str, ...] = ()
    provider_reason: str = ""
    critic_verdict: str = ""

    @property
    def relationship_id(self) -> str:
        """Stable identity for one exact claim/topic/evidence assertion.

        A claim can legitimately have more than one relationship to the same
        topic over its lifetime.  ``claim_id|topic_id`` is therefore not a safe
        review key: it lets one verdict accidentally certify another relation.
        The ID binds the relationship kind, necessity assertion and exact
        evidence set as well.
        """

        return "REL-" + _sha256_json({
            "claim_id": self.claim_id,
            "topic_id": self.topic_id,
            "relationship_type": self.relationship_type.value,
            "necessity": bool(self.necessity),
            "evidence_block_ids": sorted(set(self.evidence_block_ids)),
        })[:24].upper()

    @property
    def provisionally_usable(self) -> bool:
        """Whether provider data is structurally usable for critic orientation.

        This deliberately does *not* mean certified.  It permits code to show
        the independent critic the owner that the provider's classifications
        would imply, without granting those classifications commit authority.
        """

        if not self.evidence_block_ids:
            return False
        if self.relationship_type in NECESSARY_TYPES:
            return bool(self.necessity)
        return not self.necessity

    @property
    def certified(self) -> bool:
        """Whether this relationship may participate in ownership.

        A relationship the critic rejected, or one asserting necessity with
        no evidence, is inert: it can neither own nor make a topic required.
        """

        return (
            _normal(self.critic_verdict) in {"accepted", "verified"}
            and self.provisionally_usable
        )


@dataclass(frozen=True)
class PlacementDecision:
    """Deterministic ownership computed from certified relationships."""

    claim_id: str
    owner_topic_id: str
    required_topic_ids: tuple[str, ...] = ()
    prerequisite_topic_ids: tuple[str, ...] = ()
    reference_edges: tuple[tuple[str, str], ...] = ()
    illustration_topic_ids: tuple[str, ...] = ()
    split_lineage: tuple[str, ...] = ()
    parent_claim_coverage: tuple[str, ...] = ()
    teaching_order_sha256: str = ""
    policy_version: str = POLICY_VERSION
    rationale: str = ""

    def as_audit_row(self, order: TeachingOrder) -> dict[str, Any]:
        """Human-readable relationship → required topics → owner."""

        return {
            "claim_id": self.claim_id,
            "required": [
                {"topic_id": t, "title": order.title(t), "rank": order.rank(t)}
                for t in self.required_topic_ids
            ],
            "prerequisites": [
                {"topic_id": t, "title": order.title(t)}
                for t in self.prerequisite_topic_ids
            ],
            "references": [
                {"topic_id": t, "type": kind}
                for t, kind in self.reference_edges
            ],
            "owner": {
                "topic_id": self.owner_topic_id,
                "title": order.title(self.owner_topic_id),
                "rank": order.rank(self.owner_topic_id),
            },
            "rationale": self.rationale,
        }


# --------------------------------------------------------------------------- #
# Deterministic ownership
# --------------------------------------------------------------------------- #

def compute_placement(
    claim: AtomicClaim,
    relationships: Sequence[TopicRelationship],
    order: TeachingOrder,
) -> PlacementDecision:
    """Compute ownership deterministically from certified relationships.

    The result depends only on the *set* of certified relationships and the
    sealed teaching order.  It is invariant to the order relationships are
    supplied in, to candidate-list order, to QID order, and to where the
    claim physically sits in the source — each of which has historically
    decided placement by accident.
    """

    certified = [
        relation for relation in relationships
        if relation.claim_id == claim.claim_id
        and relation.certified
        and order.known(relation.topic_id)
    ]

    owning: set[str] = set()
    prerequisite: set[str] = set()
    references: set[tuple[str, str]] = set()
    illustrations: set[str] = set()

    for relation in certified:
        kind = relation.relationship_type
        if kind in OWNING_TYPES:
            owning.add(relation.topic_id)
        elif kind is RelationshipType.REQUIRED_PREREQUISITE:
            prerequisite.add(relation.topic_id)
        elif kind is RelationshipType.SUBSTANTIVE_LATER_ILLUSTRATION:
            illustrations.add(relation.topic_id)
        elif kind in EDGE_ONLY_TYPES:
            references.add((relation.topic_id, kind.value))

    if not owning:
        raise PlacementPolicyError(
            f"claim {claim.claim_id} has no certified owning relationship; "
            "a claim must be taught by at least one topic. Physical location "
            "is not ownership."
        )

    owner = order.latest(owning)
    owner_rank = order.rank(owner)

    # A prerequisite later than the owner is a contradiction: the owner would
    # be unreachable when the claim is taught. Surface it rather than quietly
    # producing an unteachable ordering.
    late_prerequisites = sorted(
        topic_id for topic_id in prerequisite
        if (order.rank(topic_id) or 0) > (owner_rank or 0)
    )
    if late_prerequisites:
        raise PlacementPolicyError(
            f"claim {claim.claim_id} is owned by {owner} but declares "
            f"prerequisites taught later: {late_prerequisites}. A "
            "prerequisite cannot follow the topic that needs it."
        )

    rationale = (
        f"owner={owner} is the latest teaching-ranked topic among "
        f"{sorted(owning)} whose knowledge or method is necessary"
    )
    return PlacementDecision(
        claim_id=claim.claim_id,
        owner_topic_id=owner,
        required_topic_ids=tuple(sorted(owning | prerequisite)),
        prerequisite_topic_ids=tuple(sorted(prerequisite)),
        reference_edges=tuple(sorted(references)),
        illustration_topic_ids=tuple(sorted(illustrations)),
        split_lineage=tuple(claim.origin_claim_ids),
        teaching_order_sha256=order.sha256,
        rationale=rationale,
    )


def compute_provisional_placement(
    claim: AtomicClaim,
    relationships: Sequence[TopicRelationship],
    order: TeachingOrder,
) -> PlacementDecision:
    """Compute a non-authoritative owner for the critic review packet.

    The ordinary :func:`compute_placement` accepts only independently accepted
    relationships.  This helper is intentionally separate so provider-only
    classifications can never be mistaken for a certified placement by a
    caller that forgets which stage it is in.
    """

    provisional = [
        TopicRelationship(
            claim_id=relation.claim_id,
            topic_id=relation.topic_id,
            relationship_type=relation.relationship_type,
            necessity=relation.necessity,
            evidence_block_ids=relation.evidence_block_ids,
            provider_reason=relation.provider_reason,
            critic_verdict="accepted",
        )
        for relation in relationships
        if relation.provisionally_usable
    ]
    return compute_placement(claim, provisional, order)


def compute_placements(
    claims: Sequence[AtomicClaim],
    relationships: Sequence[TopicRelationship],
    order: TeachingOrder,
) -> dict[str, PlacementDecision]:
    return {
        claim.claim_id: compute_placement(claim, relationships, order)
        for claim in claims
    }


# --------------------------------------------------------------------------- #
# Deterministic fallback ownership
# --------------------------------------------------------------------------- #
#
# Certification is a provider/critic pair, and a paid pair can fail: it can
# omit a QID, reformat one, blow its contract-correction budget, or have its
# relationship set rejected.  Every one of those used to end the run.
#
# None of them is a reason to stop, because Rule 5 -- "a task requiring
# methods from more than one topic belongs to the latest of those topics" --
# has a deterministic reading that needs no model at all.  Walk the topics in
# teaching order and ask which ones the task's own words first become
# answerable in.  The last such topic is the one a learner must have reached,
# and therefore the owner.
#
# What this deliberately does *not* do is fall back to where the task is
# printed.  Physical location is provenance, not ownership; reviving it here
# would undo the whole point of the certification stage.  Location is used
# only when the source yields no evidence for any topic at all, and the
# result is labelled so nobody mistakes it for a certified owner.

#: Verdict recorded on a deterministically derived relationship.  It is
#: deliberately neither "accepted" nor "verified": no critic saw it, and
#: ``TopicRelationship.certified`` must keep saying so.
DETERMINISTIC_VERDICT = "deterministic"

#: How a placement contract came to exist.
CERTIFIED_BASIS = "independent_certification"
DETERMINISTIC_BASIS = "deterministic_evidence_fallback"

#: Claim tokens a topic must newly supply before it counts as required.  One
#: shared word is a coincidence; two is the task depending on that topic.
#: A single token exclusive to one topic in the whole chapter is decisive on
#: its own -- see ``deterministic_relationships``.
_MIN_NEW_TOKENS = 2

#: Words describing what the learner must *do*, not what the task is *about*.
#: They are stripped before matching because a task's imperative wording is
#: identical across every topic and would otherwise let whichever section
#: happens to phrase itself as an instruction claim the task.
_INSTRUCTION_WORDS = frozenset({
    "answer", "attempt", "calculate", "check", "choose", "compare",
    "complete", "compute", "consider", "define", "derive", "describe",
    "determine", "discuss", "draw", "evaluate", "explain", "express",
    "find", "following", "give", "given", "gives", "identify", "illustrate",
    "justify", "list", "mention", "name", "obtain", "prove", "question",
    "reason", "show", "solve", "state", "substitute", "suppose", "use",
    "used", "using", "verify", "whether", "which", "whose", "write",
})


def deterministic_relationships(
    claim: AtomicClaim,
    order: TeachingOrder,
    topic_blocks: Mapping[str, Sequence[tuple[str, str]]],
) -> list[TopicRelationship]:
    """Derive relationships for ``claim`` from source text alone.

    ``topic_blocks`` maps each topic to its ``(block_id, text)`` pairs.  A
    topic becomes ``REQUIRED_LATER_METHOD`` when it is the last topic the
    task demonstrably needs, and the earlier needed topics become
    ``REQUIRED_PREREQUISITE``.  Evidence block IDs are the blocks that
    actually carry the matched words, so the relationship is checkable
    against the source rather than asserted.

    A topic is needed when either test passes:

    * it is the first topic in teaching order to supply **two or more** of
      the task's words -- one shared word is a coincidence; or
    * it supplies a word found in **no other topic in the chapter**, which
      is decisive on its own.

    The second test is what makes this work.  The naive version -- "count
    the words each topic contributes that earlier topics did not" --
    systematically under-weights the very topic the rule is about, because
    the early topics absorb all the shared vocabulary first.  A task needing
    both aâ‚™ and Sâ‚™ shares most of its wording with Â§5.2 and Â§5.3 and may
    reach Â§5.4 with only "sum" left.  That one word is the whole point: it
    appears nowhere else, so it proves the learner must have reached Â§5.4.
    """

    from . import evidence_narrowing

    wanted = {
        token
        for token in evidence_narrowing.content_tokens(
            claim.normalized_claim
        )
        if token not in _INSTRUCTION_WORDS
    }
    if not wanted:
        return []

    ranked = sorted(
        (
            (rank, topic_id)
            for topic_id, rank in order.ranks.items()
            if topic_id in topic_blocks
        ),
    )
    matched: dict[str, set[str]] = {}
    blocks_for: dict[str, dict[str, set[str]]] = {}
    for _rank, topic_id in ranked:
        per_block: dict[str, set[str]] = {}
        for block_id, text in topic_blocks.get(topic_id) or []:
            hit = evidence_narrowing.content_tokens(text) & wanted
            if hit:
                per_block[str(block_id)] = hit
        blocks_for[topic_id] = per_block
        matched[topic_id] = set().union(*per_block.values()) if per_block else set()

    # A token that only one topic in the chapter uses identifies that topic
    # unambiguously; a token several topics share identifies none of them.
    exclusive = {
        topic_id: {
            token for token in tokens
            if not any(
                token in other_tokens
                for other_id, other_tokens in matched.items()
                if other_id != topic_id
            )
        }
        for topic_id, tokens in matched.items()
    }

    covered: set[str] = set()
    required: list[tuple[str, tuple[str, ...]]] = []
    for _rank, topic_id in ranked:
        tokens = matched[topic_id]
        if not tokens:
            continue
        decisive = exclusive[topic_id] or (
            tokens - covered
            if len(tokens - covered) >= _MIN_NEW_TOKENS
            else set()
        )
        if not decisive:
            continue
        covered |= tokens
        contributing = sorted(
            block_id
            for block_id, hit in blocks_for[topic_id].items()
            if hit & decisive
        )
        required.append((topic_id, tuple(contributing)))

    if not required:
        return []

    owner_topic_id = required[-1][0]
    out: list[TopicRelationship] = []
    for topic_id, block_ids in required:
        is_owner = topic_id == owner_topic_id
        out.append(TopicRelationship(
            claim_id=claim.claim_id,
            topic_id=topic_id,
            relationship_type=(
                RelationshipType.REQUIRED_LATER_METHOD if is_owner
                else RelationshipType.REQUIRED_PREREQUISITE
            ),
            necessity=True,
            evidence_block_ids=block_ids,
            provider_reason=(
                "deterministic: this topic is where the task's remaining "
                "vocabulary first becomes answerable in teaching order"
            ),
            critic_verdict=DETERMINISTIC_VERDICT,
        ))
    return out


def compute_deterministic_placement(
    claim: AtomicClaim,
    order: TeachingOrder,
    topic_blocks: Mapping[str, Sequence[tuple[str, str]]],
) -> tuple[PlacementDecision, list[TopicRelationship]]:
    """Return an owner for ``claim`` without any model call.

    Never raises.  When the source supports no topic, the claim keeps its
    printed location, because that is the only remaining answer and losing
    the task entirely is not one.
    """

    relationships = deterministic_relationships(claim, order, topic_blocks)
    owning = [
        relation.topic_id for relation in relationships
        if relation.relationship_type in OWNING_TYPES
    ]
    prerequisites = sorted(
        relation.topic_id for relation in relationships
        if relation.relationship_type is RelationshipType.REQUIRED_PREREQUISITE
    )
    if owning:
        owner = order.latest(owning)
        rationale = (
            f"deterministic: owner={owner} is the latest teaching-ranked "
            f"topic whose source text the task requires, among {sorted(owning)}"
        )
    else:
        owner = claim.source_location_topic_id
        rationale = (
            "deterministic: no topic's source text evidenced this task, so "
            "it keeps the topic it is printed under. This is a fallback of "
            "last resort and is not a certified owner."
        )
    return (
        PlacementDecision(
            claim_id=claim.claim_id,
            owner_topic_id=owner,
            required_topic_ids=tuple(sorted(set(owning) | set(prerequisites))),
            prerequisite_topic_ids=tuple(prerequisites),
            split_lineage=tuple(claim.origin_claim_ids),
            teaching_order_sha256=order.sha256,
            rationale=rationale,
        ),
        relationships,
    )


# --------------------------------------------------------------------------- #
# Split survival
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SplitAudit:
    """Whether a split preserved everything its parents were carrying."""

    committed: bool
    missing_claims: tuple[str, ...] = ()
    missing_protected_items: tuple[str, ...] = ()
    reason: str = ""


def _split_relationship_material(
    relation: TopicRelationship,
) -> dict[str, Any]:
    """Return the exact relationship assertion a split critic must review."""

    return {
        "relationship_id": relation.relationship_id,
        "claim_id": relation.claim_id,
        "topic_id": relation.topic_id,
        "relationship_type": relation.relationship_type.value,
        "necessity": bool(relation.necessity),
        "evidence_block_ids": sorted(set(relation.evidence_block_ids)),
    }


def split_review_material(
    parent: AtomicClaim,
    children: Sequence[AtomicClaim],
    relationships: Sequence[TopicRelationship],
    *,
    split_group_id: str,
) -> dict[str, Any]:
    """Build the content-addressed semantic material for one proposed split.

    This deliberately contains no lexical-coverage score.  It binds the exact
    parent claim, exact child claims, protected inventory, and every typed
    child/topic/evidence assertion so an independent critic can make the
    semantic survival judgement over immutable material.
    """

    child_ids = [str(child.claim_id or "") for child in children]
    if not split_group_id:
        raise PlacementPolicyError("split review requires a split_group_id")
    if not parent.claim_id or not parent.normalized_claim:
        raise PlacementPolicyError("split review requires one complete parent claim")
    if not children or any(not child_id for child_id in child_ids):
        raise PlacementPolicyError("split review requires identified child claims")
    if len(set(child_ids)) != len(child_ids):
        raise PlacementPolicyError("split review child claim IDs must be unique")

    known_children = set(child_ids)
    unknown_relationship_claims = sorted({
        relation.claim_id
        for relation in relationships
        if relation.claim_id not in known_children
    })
    if unknown_relationship_claims:
        raise PlacementPolicyError(
            "split review relationship material names unknown child claim(s): "
            + ", ".join(unknown_relationship_claims)
        )

    return {
        "version": SPLIT_ATTESTATION_VERSION,
        "split_group_id": str(split_group_id),
        "parent": {
            "claim_id": str(parent.claim_id),
            "claim_sha256": claim_text_sha256(parent.normalized_claim),
            "origin_claim_ids": sorted(set(parent.origin_claim_ids)),
            "protected_source_items": sorted(set(parent.protected_source_items)),
        },
        "children": [
            {
                "claim_id": str(child.claim_id),
                "claim_sha256": claim_text_sha256(child.normalized_claim),
                "origin_claim_ids": sorted(set(child.origin_claim_ids)),
                "protected_source_items": sorted(
                    set(child.protected_source_items)
                ),
                "irreducible_relationship_material": sorted(
                    (
                        _split_relationship_material(relation)
                        for relation in relationships
                        if relation.claim_id == child.claim_id
                    ),
                    key=lambda row: row["relationship_id"],
                ),
            }
            for child in sorted(children, key=lambda value: value.claim_id)
        ],
    }


def split_review_id(material: Mapping[str, Any]) -> str:
    """Return the stable identity of one exact proposed split."""

    return "SPLIT-REVIEW-" + _sha256_json(material)[:24].upper()


def pending_split_attestation(
    parent: AtomicClaim,
    children: Sequence[AtomicClaim],
    relationships: Sequence[TopicRelationship],
    *,
    split_group_id: str,
) -> dict[str, Any]:
    """Create the immutable review packet copied into every split child."""

    material = split_review_material(
        parent,
        children,
        relationships,
        split_group_id=split_group_id,
    )
    return {
        "version": SPLIT_ATTESTATION_VERSION,
        "split_review_id": split_review_id(material),
        "material": material,
        "critic_verdict": "",
        "complete_parent_claim_preserved": False,
        "protected_items_preserved": False,
        "irreducible_relationships_preserved": False,
        "critic_reason": "",
    }


def normalized_split_attestation(value: object) -> dict[str, Any]:
    """Canonicalize an attestation without granting it validity."""

    if not isinstance(value, Mapping) or not value:
        return {}
    material = value.get("material")
    if not isinstance(material, Mapping):
        material = {}
    return {
        "version": str(value.get("version") or ""),
        "split_review_id": str(value.get("split_review_id") or ""),
        "material": json.loads(json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )),
        "critic_verdict": _normal(value.get("critic_verdict")),
        "complete_parent_claim_preserved": (
            value.get("complete_parent_claim_preserved") is True
        ),
        "protected_items_preserved": (
            value.get("protected_items_preserved") is True
        ),
        "irreducible_relationships_preserved": (
            value.get("irreducible_relationships_preserved") is True
        ),
        "critic_reason": str(value.get("critic_reason") or ""),
    }


def validate_split_attestation(
    value: object,
    *,
    child: AtomicClaim | None = None,
    relationships: Sequence[TopicRelationship] | None = None,
    require_accepted: bool = True,
) -> dict[str, Any]:
    """Validate one content-addressed, independently reviewed split packet."""

    attestation = normalized_split_attestation(value)
    if not attestation:
        raise PlacementPolicyError("split lacks a structured survival attestation")
    if attestation["version"] != SPLIT_ATTESTATION_VERSION:
        raise PlacementPolicyError("split survival attestation uses a stale version")
    material = attestation["material"]
    if material.get("version") != SPLIT_ATTESTATION_VERSION:
        raise PlacementPolicyError("split review material uses a stale version")
    expected_id = split_review_id(material)
    if attestation["split_review_id"] != expected_id:
        raise PlacementPolicyError(
            "split survival attestation has a stale content-addressed identity"
        )
    parent = material.get("parent")
    children = material.get("children")
    if (
        not material.get("split_group_id")
        or not isinstance(parent, Mapping)
        or not str(parent.get("claim_id") or "")
        or not re.fullmatch(r"[0-9a-f]{64}", str(parent.get("claim_sha256") or ""))
        or not isinstance(children, list)
        or len(children) < 2
    ):
        raise PlacementPolicyError("split review material is incomplete")
    child_ids = [
        str(row.get("claim_id") or "")
        for row in children
        if isinstance(row, Mapping)
    ]
    if len(child_ids) != len(children) or len(set(child_ids)) != len(child_ids):
        raise PlacementPolicyError("split review child identities are incomplete or duplicate")
    if child is not None:
        if str(material.get("split_group_id") or "") != child.split_group_id:
            raise PlacementPolicyError(
                f"split review group is stale for child {child.claim_id}"
            )
        matching = [
            row for row in children
            if isinstance(row, Mapping)
            and str(row.get("claim_id") or "") == child.claim_id
        ]
        if len(matching) != 1:
            raise PlacementPolicyError(
                f"split review does not identify child {child.claim_id} exactly once"
            )
        row = matching[0]
        if (
            str(row.get("claim_sha256") or "")
            != claim_text_sha256(child.normalized_claim)
            or sorted(set(row.get("origin_claim_ids") or []))
            != sorted(set(child.origin_claim_ids))
            or sorted(set(row.get("protected_source_items") or []))
            != sorted(set(child.protected_source_items))
        ):
            raise PlacementPolicyError(
                f"split review material is stale for child {child.claim_id}"
            )
        if relationships is not None:
            expected_relationships = sorted(
                (
                    _split_relationship_material(relation)
                    for relation in relationships
                    if relation.claim_id == child.claim_id
                ),
                key=lambda relation: relation["relationship_id"],
            )
            if row.get("irreducible_relationship_material") != expected_relationships:
                raise PlacementPolicyError(
                    f"split relationship material is stale for child {child.claim_id}"
                )
    if require_accepted and not (
        attestation["critic_verdict"] in {"accepted", "verified"}
        and attestation["complete_parent_claim_preserved"] is True
        and attestation["protected_items_preserved"] is True
        and attestation["irreducible_relationships_preserved"] is True
    ):
        raise PlacementPolicyError(
            "split was not independently certified to preserve the complete "
            "parent claim, protected items, and irreducible relationships"
        )
    return attestation


def audit_split_structure(
    parents: Sequence[AtomicClaim],
    children: Sequence[AtomicClaim],
) -> SplitAudit:
    """Check split lineage and protected inventory before semantic review.

    This is intentionally preliminary.  Copied ``origin_claim_ids`` are not
    proof that the child text preserves the parent meaning; only the structured
    independent split attestation accepted by :func:`audit_split` can commit.
    """

    if not parents:
        return SplitAudit(committed=False, reason="no parent claims supplied")
    if not children:
        return SplitAudit(
            committed=False,
            missing_claims=tuple(parent.claim_id for parent in parents),
            reason="a split must produce at least one grounded child",
        )

    covered_origins = {
        origin
        for child in children
        for origin in child.origin_claim_ids
    }
    missing_claims = tuple(sorted(
        parent.claim_id for parent in parents
        if parent.claim_id not in covered_origins
    ))

    child_items = {
        item for child in children
        for item in child.protected_source_items
    }
    missing_items = tuple(sorted(
        item for parent in parents
        for item in parent.protected_source_items
        if item not in child_items
    ))

    if missing_claims or missing_items:
        return SplitAudit(
            committed=False,
            missing_claims=missing_claims,
            missing_protected_items=missing_items,
            reason=(
                "split refused: "
                + ", ".join(filter(None, [
                    f"unrepresented parent claim(s) {list(missing_claims)}"
                    if missing_claims else "",
                    f"dropped protected item(s) {list(missing_items)}"
                    if missing_items else "",
                ]))
            ),
        )
    return SplitAudit(committed=True)


def audit_split(
    parents: Sequence[AtomicClaim],
    children: Sequence[AtomicClaim],
    *,
    split_attestation: object | None = None,
) -> SplitAudit:
    """Commit a split only after structure and semantic survival are proven."""

    structural = audit_split_structure(parents, children)
    if not structural.committed:
        return structural
    if len(parents) != 1:
        return SplitAudit(
            committed=False,
            reason="split attestation requires one exact parent claim",
        )
    try:
        attestation = validate_split_attestation(split_attestation)
        material = attestation["material"]
        parent = material["parent"]
        if (
            str(parent.get("claim_id") or "") != parents[0].claim_id
            or str(parent.get("claim_sha256") or "")
            != claim_text_sha256(parents[0].normalized_claim)
            or sorted(set(parent.get("origin_claim_ids") or []))
            != sorted(set(parents[0].origin_claim_ids))
            or sorted(set(parent.get("protected_source_items") or []))
            != sorted(set(parents[0].protected_source_items))
        ):
            raise PlacementPolicyError("split review material is stale for its parent")
        for child in children:
            validate_split_attestation(attestation, child=child)
    except PlacementPolicyError as exc:
        return SplitAudit(committed=False, reason=str(exc))
    return SplitAudit(committed=True)


# --------------------------------------------------------------------------- #
# Provider / critic contract
# --------------------------------------------------------------------------- #

def relationship_schema(claim_ids: Sequence[str], topic_ids: Sequence[str]) -> dict:
    """Strict schema constraining the provider to supplied opaque IDs.

    Enumerating claim and topic IDs makes an invented or reformatted
    identifier structurally impossible, which is how the chapter-wide
    placement call previously lost sub-part QIDs.
    """

    return {
        "name": "aegis_placement_relationships",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["relationships"],
            "properties": {
                "relationships": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "claim_id", "topic_id", "relationship_type",
                            "necessity", "evidence_block_ids", "reason",
                        ],
                        "properties": {
                            "claim_id": {
                                "type": "string",
                                "enum": list(dict.fromkeys(claim_ids)),
                            },
                            "topic_id": {
                                "type": "string",
                                "enum": list(dict.fromkeys(topic_ids)),
                            },
                            "relationship_type": {
                                "type": "string",
                                "enum": [kind.value for kind in RelationshipType],
                            },
                            "necessity": {"type": "boolean"},
                            "evidence_block_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "reason": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    }


PROVIDER_SYSTEM = (
    "You classify how each atomic academic claim relates to each candidate "
    "topic of one chapter. You do NOT decide placement; deterministic code "
    "computes ownership from your classifications and the sealed teaching "
    "order.\n"
    "Use exactly these relationship types:\n"
    "- CORE_TEACHING: this topic teaches the claim directly.\n"
    "- REQUIRED_PREREQUISITE: the claim cannot be understood without "
    "knowledge this topic establishes, and that topic comes earlier.\n"
    "- REQUIRED_LATER_METHOD: the claim cannot be understood or performed "
    "without this topic's method or interpretive framework, and that topic "
    "comes later. Use this when a learner could only attempt it after "
    "reaching that topic.\n"
    "- RETROSPECTIVE_REFERENCE: this topic merely points back at the "
    "material; it does not teach it.\n"
    "- SUBSTANTIVE_LATER_ILLUSTRATION: this topic independently teaches an "
    "illustration, consequence, or application of the claim.\n"
    "- INCIDENTAL_MENTION: the claim is named in passing only.\n"
    "Set necessity=true only for CORE_TEACHING, REQUIRED_PREREQUISITE, or "
    "REQUIRED_LATER_METHOD, and only when the claim genuinely cannot be "
    "understood or performed without that topic. An optional alternative "
    "solution method is NOT necessity. Shared terminology is NOT necessity. "
    "Appearing on the same page, in the same section number, or at a "
    "particular point in a chronology is NOT necessity.\n"
    "Cite exact supplied block IDs as evidence for every relationship, "
    "including references and incidental mentions. Use only supplied claim "
    "IDs and topic IDs."
)

CRITIC_SYSTEM = (
    "You independently audit placement relationships proposed by another "
    "model. For each relationship decide accepted or rejected.\n"
    "Reject when: necessity is asserted without exact supporting evidence; a "
    "mere mention, shared term, chronological adjacency, physical page "
    "position, or optional alternative method has been dressed up as "
    "necessity; the dependency direction is reversed (the later topic merely "
    "refers back, rather than being required); or an irreducible relationship "
    "has been broken into unrelated parts that no longer teach it.\n"
    "Accept RETROSPECTIVE_REFERENCE and INCIDENTAL_MENTION only as edges: "
    "they must never be used to move the claim.\n"
    "Judge each relationship on evidence, not on the order it was presented "
    "in. Do not rewrite claims."
)


def parse_relationships(
    payload: Mapping[str, Any],
    *,
    known_claim_ids: Iterable[str],
    known_topic_ids: Iterable[str],
) -> list[TopicRelationship]:
    """Parse a provider response, discarding anything unusable.

    Unknown IDs and unparsable relationship types are dropped rather than
    guessed, so a malformed response can only ever produce *less* ownership
    authority, never wrong ownership.
    """

    claims = {str(value) for value in known_claim_ids}
    topics = {str(value) for value in known_topic_ids}
    out: list[TopicRelationship] = []
    for row in payload.get("relationships") or []:
        if not isinstance(row, Mapping):
            continue
        claim_id = str(row.get("claim_id") or "")
        topic_id = str(row.get("topic_id") or "")
        kind = relationship_type(row.get("relationship_type"))
        if claim_id not in claims or topic_id not in topics or kind is None:
            continue
        out.append(TopicRelationship(
            claim_id=claim_id,
            topic_id=topic_id,
            relationship_type=kind,
            necessity=bool(row.get("necessity")),
            evidence_block_ids=tuple(
                str(value) for value in row.get("evidence_block_ids") or []
                if str(value)
            ),
            provider_reason=str(row.get("reason") or "")[:4000],
        ))
    return out


def apply_critic_verdicts(
    relationships: Sequence[TopicRelationship],
    verdicts: Mapping[str, str],
) -> list[TopicRelationship]:
    """Attach critic verdicts keyed by stable relationship identity.

    ``claim_id|topic_id`` remains a read-only compatibility fallback for old
    audit fixtures, but a live exhaustive critic response always uses the
    content-addressed relationship ID.
    """

    out: list[TopicRelationship] = []
    for relation in relationships:
        legacy_key = f"{relation.claim_id}|{relation.topic_id}"
        verdict = str(
            verdicts.get(relation.relationship_id)
            or verdicts.get(legacy_key)
            or ""
        )
        out.append(TopicRelationship(
            claim_id=relation.claim_id,
            topic_id=relation.topic_id,
            relationship_type=relation.relationship_type,
            necessity=relation.necessity,
            evidence_block_ids=relation.evidence_block_ids,
            provider_reason=relation.provider_reason,
            critic_verdict=verdict or relation.critic_verdict,
        ))
    return out


def relationship_audit_row(relation: TopicRelationship) -> dict[str, Any]:
    """Return the exact relationship material stored in a placement contract."""

    return {
        "relationship_id": relation.relationship_id,
        "claim_id": relation.claim_id,
        "topic_id": relation.topic_id,
        "relationship_type": relation.relationship_type.value,
        "necessity": bool(relation.necessity),
        "evidence_block_ids": sorted(set(relation.evidence_block_ids)),
        "provider_reason": relation.provider_reason,
        "critic_verdict": _normal(relation.critic_verdict),
    }


def claim_placement_certificate_sha256(
    claim: AtomicClaim,
    decision: PlacementDecision,
    relationships: Sequence[TopicRelationship],
    order: TeachingOrder,
) -> str:
    """Bind one final owner to its claim, exact relationships and evidence."""

    return _sha256_json({
        "policy_version": POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "claim": {
            "claim_id": claim.claim_id,
            "normalized_claim": _normal(claim.normalized_claim),
            "source_location_topic_id": claim.source_location_topic_id,
            "evidence_block_ids": sorted(set(claim.evidence_block_ids)),
            "origin_claim_ids": sorted(set(claim.origin_claim_ids)),
            "split_group_id": claim.split_group_id,
            "protected_source_items": sorted(set(claim.protected_source_items)),
        },
        "placement": placement_certificate_material(
            {decision.claim_id: decision}, order
        )["placements"][0],
        "relationships": [
            relationship_audit_row(relation)
            for relation in sorted(
                relationships, key=lambda row: row.relationship_id
            )
        ],
    })


_PLACEMENT_CONTRACT_FIELDS = (
    "certified",
    "policy_version",
    "teaching_order_sha256",
    "claim_id",
    "normalized_claim",
    "origin_claim_sha256",
    "source_location_topic_id",
    "owner_topic_id",
    "required_topic_ids",
    "prerequisite_topic_ids",
    "reference_edges",
    "illustration_topic_ids",
    "topic_relationships",
    "origin_claim_ids",
    "split_group_id",
    "protected_source_items",
    "split_attestation",
)


def normalized_placement_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the exact per-row placement material covered by its digest."""

    value = {field: contract.get(field) for field in _PLACEMENT_CONTRACT_FIELDS}
    value["certified"] = bool(value.get("certified"))
    value["policy_version"] = str(value.get("policy_version") or "")
    value["teaching_order_sha256"] = str(
        value.get("teaching_order_sha256") or ""
    )
    value["claim_id"] = str(value.get("claim_id") or "")
    value["normalized_claim"] = _normal(value.get("normalized_claim"))
    value["origin_claim_sha256"] = str(
        value.get("origin_claim_sha256") or ""
    )
    value["source_location_topic_id"] = str(
        value.get("source_location_topic_id") or ""
    )
    value["owner_topic_id"] = str(value.get("owner_topic_id") or "")
    for field in (
        "required_topic_ids",
        "prerequisite_topic_ids",
        "illustration_topic_ids",
        "origin_claim_ids",
        "protected_source_items",
    ):
        value[field] = sorted({
            str(item) for item in value.get(field) or [] if str(item)
        })
    value["reference_edges"] = sorted({
        (str(edge[0]), str(edge[1]))
        for edge in value.get("reference_edges") or []
        if isinstance(edge, (list, tuple)) and len(edge) == 2
        and str(edge[0]) and str(edge[1])
    })
    value["reference_edges"] = [list(edge) for edge in value["reference_edges"]]
    relationships: list[dict[str, Any]] = []
    for raw in value.get("topic_relationships") or []:
        if not isinstance(raw, Mapping):
            continue
        relationships.append({
            "relationship_id": str(raw.get("relationship_id") or ""),
            "claim_id": str(raw.get("claim_id") or ""),
            "topic_id": str(raw.get("topic_id") or ""),
            "relationship_type": str(raw.get("relationship_type") or ""),
            "necessity": bool(raw.get("necessity")),
            "evidence_block_ids": sorted({
                str(item) for item in raw.get("evidence_block_ids") or []
                if str(item)
            }),
            "provider_reason": str(raw.get("provider_reason") or ""),
            "critic_verdict": _normal(raw.get("critic_verdict")),
        })
    value["topic_relationships"] = sorted(
        relationships, key=lambda row: row["relationship_id"]
    )
    value["split_group_id"] = str(value.get("split_group_id") or "")
    value["split_attestation"] = normalized_split_attestation(
        value.get("split_attestation")
    )
    return value


def placement_contract_sha256(contract: Mapping[str, Any]) -> str:
    """Hash the authoritative nested placement contract, excluding its digest."""

    return _sha256_json(normalized_placement_contract(contract))


# --------------------------------------------------------------------------- #
# Certificate material
# --------------------------------------------------------------------------- #

def placement_certificate_material(
    decisions: Mapping[str, PlacementDecision],
    order: TeachingOrder,
) -> dict[str, Any]:
    """Content-addressed material binding placements to the teaching order.

    Any change to a claim's owner, required topics, prerequisites, reference
    edges, or split lineage — or to the teaching order itself — changes this
    digest and therefore invalidates any certificate built over it.
    """

    return {
        "policy_version": POLICY_VERSION,
        "teaching_order_sha256": order.sha256,
        "placements": [
            {
                "claim_id": decision.claim_id,
                "owner_topic_id": decision.owner_topic_id,
                "required_topic_ids": list(decision.required_topic_ids),
                "prerequisite_topic_ids": list(
                    decision.prerequisite_topic_ids),
                "reference_edges": [
                    list(edge) for edge in decision.reference_edges
                ],
                "illustration_topic_ids": list(
                    decision.illustration_topic_ids),
                "split_lineage": list(decision.split_lineage),
            }
            for decision in sorted(
                decisions.values(), key=lambda row: row.claim_id
            )
        ],
    }


def placement_certificate_sha256(
    decisions: Mapping[str, PlacementDecision],
    order: TeachingOrder,
) -> str:
    return _sha256_json(placement_certificate_material(decisions, order))
