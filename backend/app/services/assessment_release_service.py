"""MES release lifecycle: assembly, atomic dual publication, explicit upload
(spec §13, §16, §17 "Release and publication").

Generation stops at downloadable output. A release is assembled from one
frozen chapter snapshot plus the release payload, rendered into the two
MES projections, published atomically — both files or neither, exposed only
through a versioned manifest pointer — and uploaded to the database only by
the separate, explicit, idempotent ``upload_master_to_database`` action.
Nothing here auto-publishes, and a failed upload rolls back while the
downloads survive.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Mapping

from sqlalchemy.orm import Session

import re

from .. import config, models
from ..bulk_import import assessment_workbook
from . import assessment_grouping as grouping
from . import assessment_release as rel
from . import identity

_TITLE_TAG_RE = re.compile(r"\(([^()]+)\)\s*$")

CONCEPTS_FILENAME = "aegis_concepts.xlsx"
MASTER_FILENAME = "aegis_master.xlsx"
MANIFEST_FILENAME = "manifest.json"

# Readiness (spec §13.3).
READY = "ready"
RELEASED_WITH_WARNINGS = "released_with_warnings"
BLOCKED = "blocked_for_database_upload"

# The recorded name for Rule G's idempotent second act (T5-2). It exists so
# that "this label was already published by this release" is a note a reviewer
# can read, rather than a bare ``continue``.
QUESTION_LABEL_REISSUED = "question_label_reissued"

# ``models.Question.origin`` values this release lane may mint.  A
# reused source question keeps the long-standing value; a GENERATED
# pre-learning question carries its own explicit one.  Before this, the
# only way to tell them apart on an uploaded row was an empty
# ``source_qid`` — an absence, which is exactly what every unfilled
# field looks like, and far too weak to carry the owner steer's
# "generated questions only" property (docs/aegis-restructure.md §4
# Phase 03).
QUESTION_ORIGIN_ASSESSMENT_RELEASE = "assessment_release"
QUESTION_ORIGIN_PRE_LEARNING = "pre_learning_generated"
QUESTION_ORIGINS = (
    QUESTION_ORIGIN_ASSESSMENT_RELEASE,
    QUESTION_ORIGIN_PRE_LEARNING,
)


class ReleaseNotFound(ValueError):
    """Absent or foreign releases look identical (no ID probing)."""


class UploadRefused(RuntimeError):
    """The explicit upload action refused to run; downloads are unaffected."""


def _releases_root() -> Path:
    return config.DATA_DIR / "assessment_releases"


def _version_dir(release: models.AssessmentRelease) -> Path:
    return _releases_root() / release.release_uid / f"v{release.version}"


# --------------------------------------------------------------------------- #
# Assembly (Stage 1 freeze + snapshot projection)
# --------------------------------------------------------------------------- #

def snapshot_from_chapter(
    db: Session, chapter_id: int, payload: Mapping,
    *,
    pre_post: str | None = None,
) -> dict:
    """Project one chapter's accepted hierarchy plus the release payload
    into the immutable renderer snapshot. DB rows are read exactly once,
    here — never again during rendering or upload.

    THE SECOND SNAPSHOT WRITER. ``snapshot_from_staged_release`` below is
    the first, and it carries each topic's lane through from the staged
    payload; this one reads live Topic ORM rows, and a chapter holds BOTH
    lanes' topics at once. Output 04 must therefore be able to say which
    lane it is projecting — without that, a Pre Master built through this
    path would carry the chapter's POST topics and resolve its concepts
    against them. ``pre_post=None`` keeps every existing caller's
    projection byte-identical (both lanes, as before); naming a lane
    restricts the projection to it.
    """
    chapter = db.get(models.Chapter, chapter_id)
    if chapter is None:
        raise ReleaseNotFound("chapter not found")
    lane = str(pre_post or "").strip()
    topics = sorted(
        (
            topic for topic in chapter.topics
            if not lane
            or str(topic.pre_post_learning or "").strip().casefold()
            == lane.casefold()
        ),
        key=lambda t: (t.source_order, t.id),
    )
    snapshot_topics = []
    for topic in topics:
        concepts = sorted(
            topic.concepts, key=lambda c: (c.source_order, c.id))
        snapshot_topics.append({
            "topic_title": topic.topic_title,
            "topic_display_name": topic.topic_display_name,
            "pre_post_learning": topic.pre_post_learning,
            # §6:509-512 makes this column the Topic→Concept join
            # ("joined by exact text labels"), and the gold fills it on
            # 23/23 populated rows. Hard-coding "" here deleted the join
            # from every projection that reads this snapshot (T3.7). The
            # composition is the SHARED ``identity.titled``, not a second
            # copy of it.
            "topic_concept_labels": ", ".join(
                identity.titled(
                    concept.concept_title,
                    identity.machine_id_for_concept(concept),
                )
                for concept in concepts
            ),
            "related_topics": topic.related_topics,
            "topic_description": topic.topic_description,
            "concepts": [
                {
                    "concept_key": f"db:{concept.id}",
                    "concept_title": concept.concept_title,
                    "concept_display_name": concept.concept_display_name,
                    "concept_details": concept.concept_details,
                    "keywords": concept.keywords,
                    "related_concepts": concept.related_concepts,
                    "digicards": concept.digicards,
                    "concept_source": concept.sources,
                }
                for concept in concepts
            ],
        })
    snapshot = {
        "chapter": {
            "chapter_title": chapter.chapter_title,
            "chapter_display_name": chapter.chapter_display_name,
            # Carried, not omitted (spec-step8 T12/M4): the profile's
            # ``forced_blank_fields`` is what decides whether it ships, and
            # a key the snapshot never carries leaves that lever with
            # nothing to un-blank.
            "chapter_duration": chapter.chapter_duration,
            "pre_topics": chapter.pre_topics,
            "post_topics": chapter.post_topics,
            "chapter_description": chapter.chapter_description,
        },
        "topics": snapshot_topics,
        "groups": [dict(g) for g in payload.get("groups") or []],
        "candidates": [dict(c) for c in payload.get("candidates") or []],
    }
    _complete_required_shells(snapshot)
    return snapshot


def snapshot_from_staged_release(payload: Mapping) -> dict:
    """Assemble Output 02 from the immutable staged Output-01 hierarchy."""

    source = payload.get("concept_snapshot")
    if not isinstance(source, Mapping):
        raise ReleaseNotFound("assessment payload has no staged concept snapshot")
    chapter = source.get("chapter")
    topics = source.get("topics")
    if not isinstance(chapter, Mapping) or not isinstance(topics, list):
        raise ReleaseNotFound("staged concept snapshot has invalid hierarchy")
    snapshot = {
        "source_concept_release_sha256": str(
            source.get("source_concept_release_sha256") or ""
        ),
        "target_chapter_id": int(source.get("target_chapter_id") or 0),
        "concept_provenance": copy.deepcopy(
            list(source.get("concept_provenance") or [])
        ),
        "chapter": copy.deepcopy(dict(chapter)),
        "topics": copy.deepcopy(topics),
        "groups": [dict(group) for group in payload.get("groups") or []],
        "candidates": [
            dict(candidate) for candidate in payload.get("candidates") or []
        ],
    }
    if not snapshot["source_concept_release_sha256"]:
        raise ReleaseNotFound("staged concept snapshot has no release seal")
    _complete_required_shells(snapshot)
    return snapshot


def _machine_id(concept: Mapping) -> str:
    """Mechanical machine identity for shell naming: the ID tag embedded in
    the concept title when present, else a stable db-derived key."""
    explicit = str(concept.get("concept_machine_id") or "").strip()
    if explicit:
        return explicit
    match = _TITLE_TAG_RE.search(str(concept.get("concept_title") or ""))
    if match:
        return match.group(1).strip()
    key = str(concept.get("concept_key") or "")
    return "C" + key.removeprefix("db:")


def _complete_required_shells(snapshot: dict) -> None:
    """Every concept carries BG01/IG01/AG01 (spec §8.1) — mechanical
    bookkeeping: any exact required identity absent from the payload gets its
    NA shell so questionless concepts survive into the Master."""
    concept_names: dict[str, str] = {}
    for topic in snapshot["topics"]:
        for concept in topic.get("concepts") or []:
            concept_key = str(concept.get("concept_key") or "")
            concept_names[concept_key] = str(
                concept.get("concept_display_name") or "")

    for group in snapshot["groups"]:
        concept_key = str(group.get("concept_key") or "")
        concept_name = concept_names.get(concept_key)
        if concept_name is None:
            continue
        visible_name = grouping.friendly_group_name(
            concept_name, str(group.get("group_type") or ""))
        group["group_name"] = visible_name
        group["group_display_name"] = visible_name

    groups_by_home_key: dict[tuple[str, str], list[dict]] = {}
    for group in snapshot["groups"]:
        home_key = (
            str(group.get("concept_key") or ""),
            str(group.get("group_key") or ""),
        )
        groups_by_home_key.setdefault(home_key, []).append(group)
    for topic in snapshot["topics"]:
        for concept in topic.get("concepts") or []:
            concept_key = str(concept.get("concept_key") or "")
            machine = _machine_id(concept)
            concept_name = concept_names[concept_key]
            for shell in grouping.required_shells(
                concept_key, machine, concept_name,
            ):
                home_key = (concept_key, shell["group_key"])
                matches = groups_by_home_key.get(home_key, [])
                if any(
                    str(group.get("group_type") or "")
                    == shell["group_type"]
                    and int(group.get("group_sequence") or 0)
                    == shell["group_sequence"]
                    for group in matches
                ):
                    continue
                if matches:
                    raise grouping.GroupingError(
                        f"required shell {shell['group_key']!r} has "
                        "inconsistent group_type or group_sequence")
                shell = dict(shell)
                shell["concept_key"] = concept_key
                snapshot["groups"].append(shell)
                groups_by_home_key.setdefault(home_key, []).append(shell)


def _run_profile(provider_identity: Mapping | None) -> Mapping | str | None:
    """The run's profile off the run context, as a name OR a dict.

    ``release_core.run_context`` writes a NAME under ``profile``;
    spec-step8 T12/M7 also names ``assessment_profile``, and
    ``assessment_release_run`` builds ``{"assessment_profile": <dict>}``
    for the refiner.  Coercing with ``str()`` turned a dict into
    ``"{'name': …}"`` and ``get_profile`` then raised ``KeyError: unknown
    assessment profile`` out of ``freeze_payload`` — no release row, no
    workbooks, on a slice whose whole theme is that nothing costs the four
    outputs.  Everything downstream (``freeze_payload``,
    ``unresolved_question_homes``, ``build_dual_output``) already accepts
    ``Mapping | str | None``, so the Mapping is passed straight through.
    An empty value resolves to the default rather than raising on
    ``get_profile("")``.
    """
    # NOT named ``identity``: this module imports ``identity`` for the
    # shared title composition, and a local of that name would shadow it.
    context = provider_identity or {}
    value = context.get("assessment_profile") or context.get("profile")
    if isinstance(value, Mapping):
        return value
    return str(value or "").strip() or None


def create_release(
    db: Session,
    *,
    chapter_id: int,
    payload: Mapping,
    job_id: int | None = None,
    owner_sub: str,
    supersedes: models.AssessmentRelease | None = None,
    lane: str = "",
    layout_id: str = "",
    provider_identity: Mapping | None = None,
) -> models.AssessmentRelease:
    """Assemble and persist one immutable release (state: materialized).

    ``lane``, ``layout_id`` and ``provider_identity`` are the run context
    the converged core records on the row (spec-step8 T2/S6): which lane
    this row is the release of record for, which workbook layout the run
    executed under, and the provider/model/prompt identity plus the staged
    draft version it was frozen from. All three default to empty, because
    a legacy Build Assessments payload declares no lane and inventing one
    for it would put a row into a lane's manifest on no evidence.
    """
    run_profile = _run_profile(provider_identity)
    frozen = rel.freeze_payload(payload, run_profile)
    staged = payload.get("concept_snapshot")
    if isinstance(staged, Mapping):
        staged_chapter_id = int(staged.get("target_chapter_id") or 0)
        if staged_chapter_id != int(chapter_id):
            raise ReleaseNotFound(
                "staged concept snapshot and assessment target disagree"
            )
        staged_sha = str(
            staged.get("source_concept_release_sha256") or ""
        )
        payload_sha = str(
            payload.get("source_concept_release_sha256") or staged_sha
        )
        if not staged_sha or payload_sha != staged_sha:
            raise ReleaseNotFound(
                "staged concept snapshot and assessment release seal disagree"
            )
    snapshot = (
        snapshot_from_staged_release(payload)
        if isinstance(staged, Mapping)
        # The lane a release DECLARES, when it declares one. Output 04
        # always carries a staged snapshot and takes the branch above; a
        # legacy Build Assessments payload declares nothing and keeps the
        # both-lanes projection it has always had.
        else snapshot_from_chapter(
            db, chapter_id, payload,
            pre_post=str(payload.get("pre_post_learning") or "") or None,
        )
    )
    # THE STAGED HOME/SHAPE VERDICT (spec-step8 T7.5, D8.5b). It goes
    # BETWEEN the snapshot and the diagnostics dict for one measured reason:
    # it needs the concept universe, and ``freeze_payload`` sees only the
    # payload (the ``snapshot_from_chapter`` branch carries no
    # ``concept_snapshot`` key at all, so it could not reach the concepts
    # even in principle). Its findings append to ``frozen["errors"]`` — the
    # transport the duplicate ``question_label`` of S5 already uses — so
    # ``_readiness`` BLOCKED and the upload refusal need NO new wiring.
    #
    # This lands in the SAME change that deletes ``_readiness``'s read of
    # the renderer's unplaced list below. Either half alone is a hole:
    # the deletion without this leaves an unresolved home refused by
    # nothing (the R4 breach), and this without the deletion leaves two
    # vocabularies refusing one defect.
    frozen["errors"].extend(
        f"{finding['code']}: {finding['message']}"
        for finding in rel.unresolved_question_homes(snapshot, run_profile)
    )
    # S9: the staged concept rows ``assessment_release_snapshot.build``
    # could not carry. They used to be ``SnapshotError`` raises that cost
    # the whole Master (and, through ``ReleaseRunError``, 400'd the build
    # route); they are now recorded rows refused on the SAME transport as
    # the home/shape verdict above, so Outputs 02/04 build from the sound
    # rows and only the database write is refused.
    frozen["errors"].extend(
        f"{str(finding.get('code') or 'concept_snapshot_defect')}: "
        f"{str(finding.get('message') or finding)}"
        for finding in payload.get("concept_snapshot_defects") or []
        if isinstance(finding, Mapping)
    )
    release = models.AssessmentRelease(
        release_uid=(
            supersedes.release_uid if supersedes else rel.new_release_uid()),
        version=(supersedes.version + 1 if supersedes else 1),
        owner_sub=owner_sub,
        job_id=job_id,
        lane=str(lane or "").strip().lower(),
        layout_id=str(layout_id or ""),
        provider_identity=dict(provider_identity or {}),
        state=rel.advance_state("draft", "materialized"),
        concept_snapshot=snapshot,
        concept_snapshot_sha256=assessment_workbook.snapshot_sha256(snapshot),
        source_inventory_sha256=frozen["hashes"]["source_atoms"],
        blueprint_sha256=frozen["hashes"]["blueprint_cells"],
        payload=dict(payload),
        diagnostics={"payload_errors": frozen["errors"]},
    )
    db.add(release)
    if supersedes is not None:
        supersedes.state = "superseded"
        supersedes.superseded_at = datetime.utcnow()
    db.commit()
    db.refresh(release)
    return release


# --------------------------------------------------------------------------- #
# Atomic dual publication (spec §13.2)
# --------------------------------------------------------------------------- #

def _readiness(release: models.AssessmentRelease, manifest: Mapping) -> str:
    """Readiness from the STAGED verdict, never from a projection-time one.

    The read of the renderer's unplaced list that used to sit below
    the payload-error check is GONE (spec-step8 T7.5 item 5), deleted in the
    same change that landed
    ``assessment_release.unresolved_question_homes`` and wired it into
    ``create_release``. Two vocabularies refusing one defect is what T1
    exists to end, and the reviewer's experience improves in the same
    commit: instead of an unexplained BLOCKED they get a named defect
    carrying the candidate's ``question_label``.

    The renderer's unplaced list is still WRITTEN and still durable —
    ``publish_release`` puts the whole issues manifest on disk as
    ``manifest.json`` — so every unplaced candidate survives with its
    identity, reason and flags. What is gone is the DECIDER, not the record.
    """
    read_back = manifest.get("read_back") or {}
    if (
        read_back.get("concepts_errors")
        or read_back.get("master_errors")
        or (release.diagnostics or {}).get("payload_errors")
    ):
        return BLOCKED
    flagged = [
        c for c in (release.payload or {}).get("candidates") or []
        if c.get("flags") or c.get("assessment_eligibility") == "flagged"
    ]
    flagged_groups = [
        g for g in (release.payload or {}).get("groups") or []
        if g.get("flags")
    ]
    if flagged or flagged_groups:
        return RELEASED_WITH_WARNINGS
    return READY


def publish_release(
    db: Session, release: models.AssessmentRelease,
) -> models.AssessmentRelease:
    """Render, validate, and expose both projections atomically.

    The version directory is the publication lease: a second publisher of
    the same release version fails on the exclusive staging directory
    instead of interleaving writes. Exposure is the manifest pointer,
    written LAST — a crash beforehand leaves staging debris that is never
    served, so a release with only one successfully published file cannot
    exist (spec §13.2 step 7).
    """
    # The run's own profile, read back off the row it was persisted on
    # (spec-step8 T12/M7), through the same accessor ``create_release``
    # staged it with — so a run whose context carried a profile DICT
    # publishes instead of raising ``KeyError`` and shipping no workbook.
    output = assessment_workbook.build_dual_output(
        release.concept_snapshot,
        _run_profile(release.provider_identity),
    )
    target = _version_dir(release)
    staging = target.with_name(target.name + ".staging")
    staging_parent = staging.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.mkdir(exist_ok=False)  # publication lease
    except FileExistsError as exc:
        raise UploadRefused(
            f"release {release.release_uid} v{release.version} is already "
            "being published"
        ) from exc
    try:
        (staging / CONCEPTS_FILENAME).write_bytes(output["concepts_xlsx"])
        (staging / MASTER_FILENAME).write_bytes(output["master_xlsx"])
        manifest = dict(output["manifest"])
        manifest["release_uid"] = release.release_uid
        manifest["version"] = release.version
        manifest["readiness"] = _readiness(release, manifest)
        # Reopen from disk and re-hash: what will be served is what was
        # validated, not what was in memory.
        for filename, expected in (
            (CONCEPTS_FILENAME,
             manifest["workbook_sha256s"]["concepts_xlsx"]),
            (MASTER_FILENAME, manifest["workbook_sha256s"]["master_xlsx"]),
        ):
            digest = hashlib.sha256(
                (staging / filename).read_bytes()).hexdigest()
            if digest != expected:
                raise UploadRefused(
                    f"{filename} changed between render and publication")
        (staging / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1),
            encoding="utf-8")
        os.replace(staging, target)  # atomic exposure of the whole version
    except Exception:
        for stale in staging.glob("*"):
            stale.unlink(missing_ok=True)
        staging.rmdir()
        raise

    release.workbook_hashes = dict(manifest["workbook_sha256s"])
    release.diagnostics = {
        **(release.diagnostics or {}),
        "readiness": manifest["readiness"],
        "issues": manifest["issues"],
        "read_back": manifest["read_back"],
    }
    release.publication = {
        "directory": str(target),
        "manifest": str(target / MANIFEST_FILENAME),
        "published_at": datetime.utcnow().isoformat(),
    }
    release.state = rel.advance_state(
        release.state,
        "ready_for_upload"
        if manifest["readiness"] == READY
        else "validated_with_flags",
    )
    db.commit()
    db.refresh(release)
    return release


def recover_incomplete_publications() -> list[str]:
    """Remove staging debris left by a crash. Served versions are only ever
    complete directories exposed by the atomic rename, so recovery never
    touches anything a reader can see."""
    removed: list[str] = []
    root = _releases_root()
    if not root.is_dir():
        return removed
    for staging in root.glob("*/v*.staging"):
        for stale in staging.glob("*"):
            stale.unlink(missing_ok=True)
        staging.rmdir()
        removed.append(str(staging))
    return removed


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #

def get_release(
    db: Session, release_id: int, *, owner_sub: str,
) -> models.AssessmentRelease:
    release = db.get(models.AssessmentRelease, release_id)
    if release is None or release.owner_sub != owner_sub:
        raise ReleaseNotFound("release not found")
    return release


def published_artifact(
    release: models.AssessmentRelease, filename: str,
) -> bytes:
    directory = Path((release.publication or {}).get("directory") or "")
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ReleaseNotFound(
            "this release has no published artifacts")
    return (directory / filename).read_bytes()


# --------------------------------------------------------------------------- #
# Explicit database upload (spec §13.4)
# --------------------------------------------------------------------------- #

def _question_origin(candidate: Mapping) -> str:
    """The explicit provenance marker one candidate carries to the database.

    Mechanics: a candidate either declares one of the values this lane
    mints, or declares nothing and keeps the long-standing default. An
    unrecognised declaration is refused rather than written through —
    ``origin`` is how a later reader tells a generated pre-learning
    question from a reused source one, so an unknown value there is a
    broken artifact, not a judgment call.
    """

    declared = str(candidate.get("origin") or "").strip()
    if not declared:
        return QUESTION_ORIGIN_ASSESSMENT_RELEASE
    if declared not in QUESTION_ORIGINS:
        raise UploadRefused(
            f"candidate {candidate.get('candidate_id')!r} declares unknown "
            f"question origin {declared!r}; this lane mints "
            f"{QUESTION_ORIGINS!r}"
        )
    return declared


def upload_master_to_database(
    db: Session, release: models.AssessmentRelease, *, owner_sub: str,
) -> dict:
    """The one and only path from a release into the database.

    Revalidates the exact release and artifact hashes, imports from the
    immutable snapshot (never from mutable current rows), commits one
    transaction, and is idempotent on (release uid, version, master hash).
    A failure rolls back and the downloads survive untouched.
    """
    if release.owner_sub != owner_sub:
        raise ReleaseNotFound("release not found")

    # Idempotency first: repeating a completed upload of this exact release
    # version + master hash returns the prior result and touches nothing.
    idempotency_key = (
        f"{release.release_uid}:v{release.version}:"
        f"{(release.workbook_hashes or {}).get('master_xlsx')}")
    publication = dict(release.publication or {})
    if publication.get("uploaded_key") == idempotency_key:
        return dict(publication.get("uploaded_result") or {})

    # Revalidate the exact release and artifact hashes before anything else.
    directory = Path(publication.get("directory") or "")
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise UploadRefused("release artifacts are not published")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for filename, key in (
        (CONCEPTS_FILENAME, "concepts_xlsx"),
        (MASTER_FILENAME, "master_xlsx"),
    ):
        digest = hashlib.sha256(
            (directory / filename).read_bytes()).hexdigest()
        if digest != (release.workbook_hashes or {}).get(key) or (
            digest != (manifest.get("workbook_sha256s") or {}).get(key)
        ):
            raise UploadRefused(
                f"{filename} on disk no longer matches the release hash")
    if manifest.get("concept_snapshot_sha256") != (
        release.concept_snapshot_sha256
    ) or assessment_workbook.snapshot_sha256(release.concept_snapshot) != (
        release.concept_snapshot_sha256
    ):
        raise UploadRefused("release snapshot drifted since publication")

    readiness = (release.diagnostics or {}).get("readiness")
    if release.state not in {"ready_for_upload", "validated_with_flags"}:
        raise UploadRefused(
            f"release in state {release.state!r} cannot be uploaded")
    if readiness == BLOCKED:
        raise UploadRefused(
            "release is blocked for database upload; resolve the named "
            "issues and publish a new version")

    snapshot = release.concept_snapshot
    concept_ids = _resolve_snapshot_concept_ids(db, snapshot)
    try:
        groups_by_key: dict[str, models.Group] = {}
        groups_created = 0
        for group in snapshot.get("groups") or []:
            concept_id = concept_ids.get(str(group.get("concept_key") or ""))
            if concept_id is None:
                raise UploadRefused(
                    f"group {group.get('group_key')!r} references a concept "
                    "outside the release snapshot")
            group_key = str(group.get("group_key") or "")
            group_type = str(group.get("group_type") or "")
            key_matches = db.query(models.Group).filter_by(
                group_key=group_key).all()
            exact_matches = [
                record for record in key_matches
                if record.concept_id == concept_id
                and record.group_type == group_type
            ]
            if len(key_matches) != len(exact_matches) or len(
                exact_matches
            ) > 1:
                raise UploadRefused(
                    f"group_key {group_key!r} already belongs to a "
                    "different or duplicate database group")
            record = exact_matches[0] if exact_matches else None
            if record is None:
                blank_shells = db.query(models.Group).filter(
                    models.Group.concept_id == concept_id,
                    models.Group.group_type == group_type,
                    models.Group.group_key == "",
                ).all()
                if len(blank_shells) > 1:
                    raise UploadRefused(
                        f"concept {concept_id} has duplicate blank "
                        f"{group_type} shells; refusing ambiguous reuse"
                    )
                record = blank_shells[0] if blank_shells else None
                if record is None:
                    record = models.Group(
                        concept_id=concept_id,
                        group_type=group_type,
                    )
                    db.add(record)
                    db.flush()
                    groups_created += 1
            record.group_key = group_key
            record.group_name = str(group.get("group_name") or "")
            record.group_display_name = str(
                group.get("group_display_name") or "")
            record.group_sequence = int(group.get("group_sequence") or 0)
            record.group_status = str(
                group.get("group_status") or "Active")
            record.group_description = str(
                group.get("semantic_description") or "")
            groups_by_key[group_key] = record

        # The label a row was published under, with the release that published
        # it. ``route_audit.release_uid`` is the key because it is the one
        # identity this insert ALREADY writes (:648-651) and the only one that
        # exists for both lanes: a generated pre-learning question is permitted
        # to carry no source atom at all, so it stores ``source_qid = ""`` and
        # a source-identity key cannot tell two of them apart.
        #
        # Only the two columns the verdict reads are materialised: the whole
        # corpus as ORM objects costs the same table scan and materially more
        # memory as it grows, for information the label check never touches.
        #
        # THE ROW THIS RELEASE OWNS WINS. A database that already carries the
        # same label twice is precisely the database ``db._ensure_question_
        # label_index`` deliberately keeps bootable rather than crashing, and on
        # it a first-row-wins lookup makes the verdict depend on row order: if
        # a foreign row happens to sort first, a LEGITIMATE second act of the
        # owning release compares against the wrong row and refuses. The
        # direction is safe — a wrong pick refuses rather than drops — but a
        # refusal on a legitimate re-publication is the failure this branch is
        # atomic with T5-3 to prevent, and it would fire on exactly the
        # databases a reviewer is trying to repair. Preferring the row whose
        # ``route_audit.release_uid`` is ours removes the order dependence
        # without weakening the refusal: if no row is ours, any of them refuses.
        existing_by_label: dict[str, str] = {}
        for label, route_audit in db.query(
            models.Question.question_label, models.Question.route_audit
        ):
            if not label:
                continue
            prior_uid = str((route_audit or {}).get("release_uid") or "")
            if label not in existing_by_label or (
                prior_uid and prior_uid == release.release_uid
            ):
                existing_by_label[label] = prior_uid
        questions_created = 0
        labels_reissued: list[str] = []
        # Rows this loop has already inserted are not in the query above, and
        # they are the one collision the freeze-time check cannot cover for a
        # release FROZEN BEFORE T5-1 existed: its ``payload_errors`` were
        # computed without ``duplicate_question_labels``, so a pre-existing
        # snapshot carrying one label twice reaches this loop with readiness
        # READY.
        #
        # [measured] what happened then depended on the database, and the worse
        # branch is the one this slice exists for. WITH the partial index, the
        # second insert raised and the generic ``except Exception`` below
        # converted it into ``upload failed and was rolled back
        # (IntegrityError: UNIQUE constraint failed…)`` — safe, but it names a
        # driver error instead of the label and the reason. WITHOUT the index —
        # i.e. on exactly the database ``db._ensure_question_label_index``
        # declines to index rather than crash on, the database a reviewer is in
        # the middle of repairing — the upload SUCCEEDED with
        # ``questions_created: 2`` and wrote TWO rows under one
        # ``question_label``: identity corruption committed to the database,
        # the T9-1 B1 defect the publication act is supposed to block. Refused
        # by name on both, before anything is written.
        written_here: set[str] = set()
        for candidate in snapshot.get("candidates") or []:
            label = str(candidate.get("question_label") or "")
            if not label:
                raise UploadRefused(
                    f"candidate {candidate.get('candidate_id')!r} carries "
                    "no question label")
            if label in written_here:
                raise UploadRefused(
                    f"{label}: two candidates in this release carry the same "
                    "question label")
            if label in existing_by_label:
                prior_uid = existing_by_label[label]
                if prior_uid and prior_uid == release.release_uid:
                    # Rule G's idempotent second act: this exact release
                    # already put this exact label in the database. Skipping is
                    # correct — but it is RECORDED, because an unflagged
                    # ``continue`` here is what made a learner's question
                    # disappear between the release and the database (R4).
                    labels_reissued.append(label)
                    continue
                # A different candidate wants a label that is already
                # published. Never drop it: refuse the whole upload, keep both
                # downloads, and let a reviewer resolve the collision.
                raise UploadRefused(
                    f"{label}: already published under different content")
            group = groups_by_key.get(str(candidate.get("group_key") or ""))
            if group is None:
                raise UploadRefused(
                    f"{label}: home group is not part of this release")
            db.add(models.Question(
                group_id=group.id,
                sheet_kind=str(candidate.get("sheet_kind") or ""),
                question_label=label,
                question_category=str(
                    candidate.get("question_category") or ""),
                cognitive_skills=str(candidate.get("cognitive_skill") or ""),
                question_appears_in=str(
                    candidate.get("question_appears_in") or ""),
                level_of_difficulty=str(candidate.get("difficulty") or ""),
                question=str(candidate.get("question") or ""),
                question_text=str(candidate.get("question_text") or ""),
                marks=float(candidate.get("marks") or 0),
                question_duration=float(
                    candidate.get("question_duration") or 0
                ),
                math_keyboard=str(candidate.get("math_keyboard") or ""),
                display_answer=str(candidate.get("display_answer") or ""),
                answer_explanation=str(
                    candidate.get("answer_explanation") or ""),
                answers=list(candidate.get("answers") or []),
                sub_questions=list(candidate.get("sub_questions") or []),
                origin=_question_origin(candidate),
                answer_restriction=str(
                    candidate.get("answer_restriction") or ""),
                source_qid=", ".join(
                    candidate.get("source_atom_ids") or []),
                blueprint_cell_id=str(
                    candidate.get("blueprint_cell_id") or ""),
                route_audit={
                    "release_uid": release.release_uid,
                    "version": release.version,
                },
            ))
            written_here.add(label)
            questions_created += 1

        result = {
            "release_uid": release.release_uid,
            "version": release.version,
            "groups_created": groups_created,
            "questions_created": questions_created,
            "labels_reissued": list(labels_reissued),
        }
        if labels_reissued:
            notes = list((release.diagnostics or {}).get("release_notes") or [])
            note = {
                "code": QUESTION_LABEL_REISSUED,
                "release_uid": release.release_uid,
                "version": release.version,
                "question_labels": list(labels_reissued),
                "detail": (
                    "this release had already published these labels; the "
                    "second act skipped them and wrote nothing new"
                ),
            }
            # Recorded once, not once per act. Every field of the note is
            # constant for one release row, so a third and fourth second act
            # append a byte-identical copy and grow ``release_notes`` without
            # adding anything a reviewer can read. The note says WHAT was
            # skipped; the count of acts is the publication record's business.
            if note not in notes:
                notes.append(note)
                release.diagnostics = {
                    **(release.diagnostics or {}),
                    "release_notes": notes,
                }
        release.publication = {
            **publication,
            "uploaded_key": idempotency_key,
            "uploaded_result": result,
        }
        if release.state != "publication_pending":
            release.state = rel.advance_state(
                release.state, "publication_pending")
        release.state = rel.advance_state(release.state, "uploaded")
        release.uploaded_at = datetime.utcnow()
        db.commit()
    except UploadRefused:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise UploadRefused(
            f"upload failed and was rolled back ({type(exc).__name__}: "
            f"{exc}); downloads are unaffected"
        ) from exc
    return result


def _resolve_snapshot_concept_ids(
    db: Session, snapshot: Mapping,
) -> dict[str, int]:
    """Resolve private release keys only against exact published identities.

    A staged Output-01 concept is never inferred from similar text.  Until its
    exact concept release has been explicitly uploaded, Master publication is
    refused while both downloadable workbooks remain available.
    """

    chapter_id = int(snapshot.get("target_chapter_id") or 0)
    concept_ids: dict[str, int] = {}
    for topic in snapshot.get("topics") or []:
        topic_title = str(topic.get("topic_title") or "")
        pre_post = str(topic.get("pre_post_learning") or "")
        for concept in topic.get("concepts") or []:
            key = str(concept.get("concept_key") or "")
            if not key or key in concept_ids:
                raise UploadRefused(
                    f"snapshot repeats or omits concept identity {key!r}"
                )
            if key.startswith("db:"):
                concept_ids[key] = int(key[3:])
                continue
            if not chapter_id:
                raise UploadRefused(
                    "staged concept snapshot has no target chapter identity"
                )
            matches = (
                db.query(models.Concept)
                .join(models.Topic)
                .filter(
                    models.Topic.chapter_id == chapter_id,
                    models.Topic.topic_title == topic_title,
                    models.Topic.pre_post_learning == pre_post,
                    models.Concept.concept_title
                    == str(concept.get("concept_title") or ""),
                )
                .all()
            )
            exact = [
                row for row in matches
                if str(row.concept_display_name or "")
                == str(concept.get("concept_display_name") or "")
                and str(row.parent_concept or "")
                == str(concept.get("parent_concept") or "")
                and str(row.concept_details or "")
                == str(concept.get("concept_details") or "")
                and str(row.keywords or "")
                == str(concept.get("keywords") or "")
            ]
            if len(exact) != 1 or len(matches) != 1:
                raise UploadRefused(
                    f"concept {concept.get('concept_title')!r} under "
                    f"topic {topic_title!r} does not have one exact published "
                    "Output-01 identity; upload that concept release first"
                )
            concept_ids[key] = exact[0].id
    return concept_ids
