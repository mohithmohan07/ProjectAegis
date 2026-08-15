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

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Mapping

from sqlalchemy.orm import Session

import re

from .. import config, models
from ..bulk_import import mes_profile
from . import assessment_grouping as grouping
from . import assessment_release as rel

_TITLE_TAG_RE = re.compile(r"\(([^()]+)\)\s*$")

CONCEPTS_FILENAME = "aegis_concepts.xlsx"
MASTER_FILENAME = "aegis_master.xlsx"
MANIFEST_FILENAME = "manifest.json"

# Readiness (spec §13.3).
READY = "ready"
RELEASED_WITH_WARNINGS = "released_with_warnings"
BLOCKED = "blocked_for_database_upload"


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
) -> dict:
    """Project one chapter's accepted hierarchy plus the release payload
    into the immutable renderer snapshot. DB rows are read exactly once,
    here — never again during rendering or upload."""
    chapter = db.get(models.Chapter, chapter_id)
    if chapter is None:
        raise ReleaseNotFound("chapter not found")
    topics = sorted(
        chapter.topics, key=lambda t: (t.source_order, t.id))
    snapshot_topics = []
    for topic in topics:
        concepts = sorted(
            topic.concepts, key=lambda c: (c.source_order, c.id))
        snapshot_topics.append({
            "topic_title": topic.topic_title,
            "topic_display_name": topic.topic_display_name,
            "pre_post_learning": topic.pre_post_learning,
            "topic_concept_labels": "",
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


def _machine_id(concept: Mapping) -> str:
    """Mechanical machine identity for shell naming: the ID tag embedded in
    the concept title when present, else a stable db-derived key."""
    match = _TITLE_TAG_RE.search(str(concept.get("concept_title") or ""))
    if match:
        return match.group(1).strip()
    key = str(concept.get("concept_key") or "")
    return "C" + key.removeprefix("db:")


def _complete_required_shells(snapshot: dict) -> None:
    """Every concept carries BG01/IG01/AG01 (spec §8.1) — mechanical
    bookkeeping: any tier the payload's groups do not already cover gets
    its NA shell so questionless concepts survive into the Master."""
    covered: set[tuple[str, str]] = {
        (str(g.get("concept_key") or ""), str(g.get("group_type") or ""))
        for g in snapshot["groups"]
    }
    for topic in snapshot["topics"]:
        for concept in topic.get("concepts") or []:
            concept_key = str(concept.get("concept_key") or "")
            machine = _machine_id(concept)
            for shell in grouping.required_shells(concept_key, machine):
                if (concept_key, shell["group_type"]) in covered:
                    continue
                shell = dict(shell)
                shell["concept_key"] = concept_key
                snapshot["groups"].append(shell)


def create_release(
    db: Session,
    *,
    chapter_id: int,
    payload: Mapping,
    job_id: int | None = None,
    owner_sub: str,
    supersedes: models.AssessmentRelease | None = None,
) -> models.AssessmentRelease:
    """Assemble and persist one immutable release (state: materialized)."""
    frozen = rel.freeze_payload(payload)
    snapshot = snapshot_from_chapter(db, chapter_id, payload)
    release = models.AssessmentRelease(
        release_uid=(
            supersedes.release_uid if supersedes else rel.new_release_uid()),
        version=(supersedes.version + 1 if supersedes else 1),
        owner_sub=owner_sub,
        job_id=job_id,
        state=rel.advance_state("draft", "materialized"),
        concept_snapshot=snapshot,
        concept_snapshot_sha256=mes_profile.snapshot_sha256(snapshot),
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
    read_back = manifest.get("read_back") or {}
    if (
        read_back.get("concepts_errors")
        or read_back.get("master_errors")
        or (release.diagnostics or {}).get("payload_errors")
    ):
        return BLOCKED
    unplaced = (manifest.get("issues") or {}).get("unplaced") or []
    if unplaced:
        # A question with no resolved home can corrupt graph identity on
        # import: downloads stay available, the database stays protected.
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
    output = mes_profile.build_dual_output(release.concept_snapshot)
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
    ) or mes_profile.snapshot_sha256(release.concept_snapshot) != (
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
    concept_ids: dict[str, int] = {}
    for topic in snapshot.get("topics") or []:
        for concept in topic.get("concepts") or []:
            key = str(concept.get("concept_key") or "")
            if key.startswith("db:"):
                concept_ids[key] = int(key[3:])
    try:
        groups_by_key: dict[str, models.Group] = {}
        groups_created = 0
        for group in snapshot.get("groups") or []:
            concept_id = concept_ids.get(str(group.get("concept_key") or ""))
            if concept_id is None:
                raise UploadRefused(
                    f"group {group.get('group_key')!r} references a concept "
                    "outside the release snapshot")
            record = db.query(models.Group).filter_by(
                concept_id=concept_id,
                group_type=str(group.get("group_type") or ""),
                group_key=str(group.get("group_key") or ""),
            ).one_or_none()
            if record is None:
                record = models.Group(
                    concept_id=concept_id,
                    group_type=str(group.get("group_type") or ""),
                    group_key=str(group.get("group_key") or ""),
                    group_sequence=int(group.get("group_sequence") or 0),
                    group_name=str(group.get("group_name") or ""),
                    group_display_name=str(
                        group.get("group_display_name") or ""),
                    group_status=str(group.get("group_status") or "Active"),
                )
                db.add(record)
                db.flush()
                groups_created += 1
            record.group_description = str(
                group.get("semantic_description") or "")
            groups_by_key[str(group.get("group_key") or "")] = record

        existing_labels = {
            q.question_label
            for q in db.query(models.Question).all()
            if q.question_label
        }
        questions_created = 0
        for candidate in snapshot.get("candidates") or []:
            label = str(candidate.get("question_label") or "")
            if not label:
                raise UploadRefused(
                    f"candidate {candidate.get('candidate_id')!r} carries "
                    "no question label")
            if label in existing_labels:
                continue  # append-only: an uploaded label is immutable
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
                display_answer=str(candidate.get("display_answer") or ""),
                answer_explanation=str(
                    candidate.get("answer_explanation") or ""),
                answers=list(candidate.get("answers") or []),
                sub_questions=list(candidate.get("sub_questions") or []),
                origin="assessment_release",
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
            questions_created += 1

        result = {
            "release_uid": release.release_uid,
            "version": release.version,
            "groups_created": groups_created,
            "questions_created": questions_created,
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
