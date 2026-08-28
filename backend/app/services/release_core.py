"""The shared release core: one immutable row per lane per run.

Two release systems grew side by side. ``build_concepts_release`` stages a
payload into a slot on the job and owns the reviewer's evidence — the issue
ledger, the ``_aegis_*`` audit registry, the diagnostics archive, the two
lane siblings. ``models.AssessmentRelease`` owns identity, version,
supersession, artifact hashing, the publication lease and the receipt. Each
had its own name for "is this release all right", and a reviewer reading the
two surfaces was reading two vocabularies about one run.

This module is the seam between them (spec-step8 T1/T2, slice S6), and it
holds exactly three things:

* **the public vocabulary.** ``build_concepts_release``'s three §4 names —
  *Ready*, *Ready with flags*, *Diagnostic release* — are the only public
  release vocabulary, for BOTH systems. ``AssessmentRelease.state`` (the
  7-state internal lifecycle) and ``diagnostics["readiness"]`` (the 3-value
  publication verdict) stay exactly where they are and keep doing exactly
  what they do; they become INPUTS to the projection here rather than a
  second public truth.
* **the lane resolution.** Which immutable row is a job's release of record
  for one lane, resolved through the indexed ``AssessmentRelease.job_id``
  plus the ``lane`` column S6 adds. The manifest's Master entries and the
  supersede chain both ask this one question of this one function.
* **the run context.** What the frozen row records about the run it
  executed under — profile, layout, provider identity, and the staged draft
  version it was frozen from — assembled in one place so the row and the
  reviewer's surface cannot disagree about it.

**What this module deliberately does NOT do.** It does not gate anything.
The assessment lane's database write is refused by
``assessment_release_service`` — ``diagnostics["payload_errors"]`` and
``_readiness``'s read of the renderer's ``unplaced`` list — and the concept
lane's by ``build_concepts_release.structural_defects``. Naming a state and
refusing a write are two jobs, and folding them together here is how a
fault in one lane would come to cost the other one its publication.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .. import models
from ..bulk_import import layouts
from .build_concepts_release import (
    DIAGNOSTIC_RELEASE,
    LANE_POST,
    LANE_PRE,
    READY,
    READY_WITH_FLAGS,
    normalize_lane,
    release_payload,
    staged_version,
    structural_defects,
)


__all__ = [
    "DIAGNOSTIC_RELEASE",
    "LANE_POST",
    "LANE_PRE",
    "READY",
    "READY_WITH_FLAGS",
    "RELEASE_STATES",
    "lane_of",
    "layout_id",
    "latest_release_for_lane",
    "normalize_lane",
    "release_chain_head",
    "release_state",
    "run_context",
]

# The whole public vocabulary, in one tuple, so a fourth name cannot be
# added by accident in one of the two systems.
RELEASE_STATES = (READY, READY_WITH_FLAGS, DIAGNOSTIC_RELEASE)

# B's readiness verdict, spelled here so this module does not import the
# service (which imports the workbook renderer, which imports openpyxl) to
# read three string constants. Pinned against the service by a regression:
# a rename there that is not made here would leave this projection reading
# a verdict that no longer exists.
_BLOCKED = "blocked_for_database_upload"
_RELEASED_WITH_WARNINGS = "released_with_warnings"


def layout_id() -> str:
    """The workbook layout every release in this process executes under.

    One accessor, so the row's ``layout_id`` and the run context's copy of
    it can never be filled from two different constants. The layout
    MIGRATION is S7's; this only records which one was in force.
    """

    return layouts.REFERENCE_LAYOUT_ID


def lane_of(release: models.AssessmentRelease) -> str:
    """The lane a row declares, or "" when it declares none.

    Blank is a real answer, not a missing one: a legacy Build Assessments
    release predates the lane column and named no lane, and guessing one
    for it would put a row into a lane's manifest entry on no evidence.
    """

    return str(getattr(release, "lane", "") or "").strip().lower()


def _rows_for_lane(
    db: Session | None,
    job_id: int | None,
    lane: object,
) -> list[models.AssessmentRelease]:
    """One job's rows in one lane, newest version first.

    Resolved through the indexed ``AssessmentRelease.job_id`` plus the
    ``lane`` column, which is why the Master entries could not join the
    manifest before this slice: there was no column to ask.
    """

    if db is None or not job_id:
        return []
    resolved = normalize_lane(lane)
    return (
        db.query(models.AssessmentRelease)
        .filter(models.AssessmentRelease.job_id == int(job_id))
        .filter(models.AssessmentRelease.lane == resolved)
        .order_by(models.AssessmentRelease.version.desc())
        .all()
    )


def latest_release_for_lane(
    db: Session | None,
    job_id: int | None,
    lane: object,
) -> models.AssessmentRelease | None:
    """The newest LIVE release row for one job's lane, or None.

    "Live" means not superseded: a superseded row keeps its artifacts and
    its receipt — that is the whole point of an append-only release — but
    it is not what the reviewer's manifest should point at, and this
    function is what the manifest asks.

    It says only what it means. A superseded-only lane answers None here
    and the manifest's Master entry goes present-and-disabled with a
    reason that says so, rather than being handed a superseded row and
    advertising an enabled download for it — which is what the earlier
    ``(live or rows)`` fallback did, in flat contradiction of this
    docstring. The LINEAGE question that fallback was really answering
    now has its own function, ``release_chain_head``.
    """

    live = [
        row
        for row in _rows_for_lane(db, job_id, lane)
        if row.state != "superseded"
    ]
    return live[0] if live else None


def release_chain_head(
    db: Session | None,
    job_id: int | None,
    lane: object,
) -> models.AssessmentRelease | None:
    """The newest row for one job's lane, live or superseded, or None.

    The LINEAGE question, and it is not the manifest's question. A second
    run of a lane appends a VERSION to the chain the first run started,
    and this is the row it appends to — so a superseded row is a correct
    answer here where it is a wrong one above. If every row in a chain
    were superseded (a successor deleted out from under it, say),
    continuing the chain is right and minting a fresh ``release_uid`` at
    version 1 would orphan every published receipt already in it.
    """

    rows = _rows_for_lane(db, job_id, lane)
    return rows[0] if rows else None


def release_state(release: models.AssessmentRelease | None) -> str:
    """One of the three §4 names, for an immutable assessment release row.

    The projection, and every input to it is a decision something else
    already made and recorded:

    * ``diagnostics["payload_errors"]`` — the frozen payload failed its
      own schema. Structural corruption blocks (§4).
    * ``structural_defects(payload)`` — the shared counter, which since S6
      knows this payload shape as well as the concept lane's. An
      assessment release with no depositable candidate has nothing to
      publish.
    * ``diagnostics["readiness"]`` — B's publication verdict, recorded at
      publish time. It is read as an INPUT here, which is precisely T1's
      resolution: it keeps refusing the database write where it always
      did, and it stops being a second public NAME for the same run.
    * the payload's own flags — semantic doubt on rows that ship. Flags
      never block (Rule E), so they name the release *Ready with flags*
      and leave both the downloads and the explicit publication open.

    Judges nothing about content: every branch reads a verdict already in
    the row.
    """

    if release is None:
        return DIAGNOSTIC_RELEASE
    diagnostics = release.diagnostics or {}
    if diagnostics.get("payload_errors"):
        return DIAGNOSTIC_RELEASE
    payload = release.payload or {}
    if structural_defects(payload):
        return DIAGNOSTIC_RELEASE
    readiness = str(diagnostics.get("readiness") or "").strip()
    if readiness == _BLOCKED:
        return DIAGNOSTIC_RELEASE
    if readiness == _RELEASED_WITH_WARNINGS:
        return READY_WITH_FLAGS
    flagged = any(
        candidate.get("flags")
        or candidate.get("assessment_eligibility") == "flagged"
        for candidate in payload.get("candidates") or []
        if isinstance(candidate, Mapping)
    ) or any(
        group.get("flags")
        for group in payload.get("groups") or []
        if isinstance(group, Mapping)
    ) or bool(payload.get("materialization_blocked")) or bool(
        payload.get("duplicates_removed")
    ) or bool(payload.get("pre_learning_claimed"))
    return READY_WITH_FLAGS if flagged else READY


def run_context(
    job: models.UploadJob | None,
    *,
    lane: object,
    profile: Mapping[str, Any] | None = None,
    layout_id: str = "",
) -> dict[str, Any]:
    """What the frozen row records about the run it executed under.

    §3 guard 2's run-context pinning, assembled once so the row and every
    surface reading it agree. It goes into ``AssessmentRelease
    .provider_identity`` — declared since the first MES release and, until
    this slice, never written by anything.

    The staged draft version travels with it deliberately: it is the only
    thing that ties an immutable row back to the exact draft it was frozen
    from, and a re-stage after a freeze changes it. Without it, "this
    Master was built from a payload that has since moved" is a question
    with no recorded answer.
    """

    resolved = normalize_lane(lane)
    context: dict[str, Any] = {
        "lane": resolved,
        "layout_id": str(layout_id or layouts.REFERENCE_LAYOUT_ID),
        "profile": str((profile or {}).get("name") or ""),
        # Keep the exact run-resolved mapping as well as the historical name.
        # Metadata-selected sheet/blanking overrides are part of the immutable
        # run contract and cannot be reconstructed from ``reference-1`` alone
        # at the later create/publish seams.
        "assessment_profile": copy.deepcopy(dict(profile or {})),
    }
    if job is not None:
        context["job_id"] = int(job.id)
        payload = release_payload(job, lane=resolved)
        context["staged_release_version"] = staged_version(payload)
        # The draft LINEAGE the freeze ties back to (S10 repair, Round 7):
        # the version restarts on a legitimate inventory reset, so the seal
        # gate keys on this uid instead. Empty for drafts staged before the
        # uid existed — the gate then has nothing to compare and says so.
        context["staged_release_uid"] = str(
            (payload or {}).get("staged_release_uid") or ""
        )
    return context
