"""End-to-end assessment release orchestration for one generated job.

The glue between the finished Build Concepts job and the two output files:

    source atoms  ->  cell classification (author + critic)
                  ->  question/answer/rubric materialization (author + critic)
                  ->  one-home routing (author + critic)
                  ->  mechanical tiering  ->  variant clustering (author + critic)
                  ->  group descriptions (author + critic)  ->  touched-group QA
                  ->  immutable release  ->  atomic dual publication

Every semantic stage already exists; this module only sequences them,
carries identities, and never decides content itself. All model calls are
injectable per stage so the whole pipeline is testable offline; the live
defaults inside each stage module are used when nothing is injected.

Fail-closed composition: a stage that cannot positively decide produces a
flagged record that rides the release — an unclassifiable atom, an
unrouted candidate, or an untiered difficulty ends up in the manifest's
issue ledger and blocks database upload while the downloads survive
(spec §13.3, §14). Nothing is dropped anywhere in the chain.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable, Mapping

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import models
from . import assessment_grouping as grouping
from . import assessment_materialization as materialization
from . import assessment_quality as quality
from . import assessment_release as rel
from . import assessment_release_service as release_service
from . import assessment_routing as routing
from . import assessment_source_inventory as source_inventory
from . import assessment_profile
from . import concept_refiner as cr
from . import progress, uploads

MAX_ATTEMPTS = 3

CELL_AUTHOR_SYSTEM = (
    "You are the Aegis assessment-cell classifier. For ONE source task from "
    "a textbook chapter, decide the blueprint cell it should fulfill when "
    "reused as an assessment item: sheet kind, question category, cognitive "
    "skill (Bloom), difficulty, and marks. Judge from the task's own text, "
    "its answer evidence, and its source kind — never from its length, its "
    "position, or its neighbours.\n"
    "sheet_kind: objective (one closed correct choice/token) or descriptive "
    "(constructed response). cognitive_skill: one of Remember, Understand, "
    "Apply, Analyse, Evaluate, Create. difficulty: Less, Moderate, or High "
    "— Bloom and difficulty are independent. marks: a realistic positive "
    "integer for this grade.\n"
    "Return ONLY strict JSON:\n"
    '{"sheet_kind":"","question_category":"","cognitive_skill":"",'
    '"difficulty":"","marks":1,"reason":""}'
)

CELL_CRITIC_SYSTEM = (
    "You are the independent Aegis cell-classification critic. You are "
    "READ-ONLY. Verify one proposed blueprint-cell classification against "
    "the source task: reject a sheet kind, category, Bloom, difficulty, or "
    "marks value the task itself does not support.\n"
    "Return ONLY strict JSON:\n"
    '{"verdict":"accept","proposal_sha256":"","feedback":[]} or '
    '{"verdict":"reject","proposal_sha256":"","feedback":["evidence-bound '
    'reason ..."]}\n'
    "proposal_sha256 MUST echo the exact hash you were given."
)


class ReleaseRunError(ValueError):
    """The job cannot enter the release pipeline at all."""


# --------------------------------------------------------------------------- #
# Stage: cell classification (sheet kind/category/Bloom/difficulty/marks
# are semantic when no explicit blueprint fixes them — spec §4)
# --------------------------------------------------------------------------- #

def _cell_defects(proposal: Mapping, profile: Mapping) -> list[str]:
    defects: list[str] = []
    allowed_kinds = (
        rel.SHEET_KINDS if not profile["allow_subjective_rows"]
        else ("objective", "subjective", "descriptive"))
    if proposal.get("sheet_kind") not in allowed_kinds:
        defects.append(
            f"sheet_kind must be one of {allowed_kinds} "
            f"(got {proposal.get('sheet_kind')!r})")
    if not str(proposal.get("question_category") or "").strip():
        defects.append("missing question_category")
    if proposal.get("cognitive_skill") not in bi.COGNITIVE_SKILLS:
        defects.append(
            f"cognitive_skill must be one of {bi.COGNITIVE_SKILLS} "
            f"(got {proposal.get('cognitive_skill')!r})")
    if proposal.get("difficulty") not in bi.DIFFICULTY_LEVELS:
        defects.append(
            f"difficulty must be one of {bi.DIFFICULTY_LEVELS} "
            f"(got {proposal.get('difficulty')!r})")
    try:
        if float(proposal.get("marks") or 0) <= 0:
            defects.append("marks must be positive")
    except (TypeError, ValueError):
        defects.append("marks not numeric")
    return defects


def author_cell_for_atom(
    atom: Mapping,
    *,
    meta: Mapping,
    profile: Mapping,
    author_call: Callable[..., dict] | None = None,
    critic_call: Callable[..., dict] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """One source atom -> one reuse blueprint cell, classified or flagged."""
    if author_call is None or critic_call is None:
        from . import generation

        def _default_author(system, user):
            return generation._openai_json(
                system, user, purpose="concept_mapping")

        def _default_critic(system, user):
            return generation._openai_json(
                system, user, purpose="concept_validation")

        author_call = author_call or _default_author
        critic_call = critic_call or _default_critic

    payload = {
        "metadata": dict(meta),
        "source_atom": {
            "source_qid": atom.get("source_qid"),
            "source_kind": atom.get("source_kind"),
            "raw_text": atom.get("raw_text"),
            "normalized_public_text": atom.get("normalized_public_text"),
            "options": atom.get("options"),
            "source_answer": atom.get("source_answer"),
            "shared_context": atom.get("shared_context"),
        },
    }
    attempts: list[dict] = []
    feedback: list[str] = []
    best: Mapping | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        user = json.dumps(payload, ensure_ascii=False)
        if feedback:
            user += (
                "\n\nYOUR PREVIOUS CLASSIFICATION WAS REJECTED. Address "
                "every item, then return a fresh classification:\n- "
                + "\n- ".join(feedback)
            )
        try:
            proposal = author_call(CELL_AUTHOR_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001 — never exception->acceptance
            attempts.append({"attempt": attempt, "outcome": "author_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        proposal = proposal if isinstance(proposal, Mapping) else {}
        best = proposal
        defects = _cell_defects(proposal, profile)
        if defects:
            attempts.append({"attempt": attempt, "outcome": "mechanical",
                             "defects": defects})
            feedback = defects
            continue
        sha = rel.sha256_json(dict(proposal))
        try:
            review = critic_call(
                CELL_CRITIC_SYSTEM,
                json.dumps({
                    "proposal": dict(proposal),
                    "proposal_sha256": sha,
                    "source_atom": payload["source_atom"],
                }, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            attempts.append({"attempt": attempt, "outcome": "critic_error",
                             "error": f"{type(exc).__name__}: {exc}"})
            break
        review = review if isinstance(review, Mapping) else {}
        if str(review.get("proposal_sha256") or "") != sha:
            attempts.append({"attempt": attempt, "outcome": "critic_unbound"})
            feedback = ["the critic review did not bind to the proposal"]
            continue
        if str(review.get("verdict") or "").strip().lower() == "accept":
            return {
                "cell_id": "CELL-" + hashlib.sha256(
                    f"reuse|{atom.get('source_qid')}|{sha}"
                    .encode("utf-8")).hexdigest()[:16],
                "sheet_kind": proposal["sheet_kind"],
                "question_category": proposal["question_category"],
                "cognitive_skill": proposal["cognitive_skill"],
                "difficulty": proposal["difficulty"],
                "marks": float(proposal["marks"]),
                "count": 1,
                "appears_in": [profile["appears_in"]],
                "concept_id": None,
                "source_policy": "reuse",
                "accepted_source_qids": [str(atom.get("source_qid") or "")],
                "flags": [],
                "authority": {
                    "proposal_sha256": sha,
                    "critic_response_sha256": rel.sha256_json(dict(review)),
                    "attempts": attempts + [
                        {"attempt": attempt, "outcome": "accepted"}],
                },
            }
        review_feedback = [
            str(f) for f in review.get("feedback") or [] if str(f).strip()]
        attempts.append({"attempt": attempt, "outcome": "critic_rejected",
                         "feedback": review_feedback})
        feedback = review_feedback or ["rejected without usable feedback"]

    best = best or {}
    return {
        "cell_id": "CELL-" + hashlib.sha256(
            f"reuse|{atom.get('source_qid')}|unresolved"
            .encode("utf-8")).hexdigest()[:16],
        "sheet_kind": str(best.get("sheet_kind") or ""),
        "question_category": str(best.get("question_category") or ""),
        "cognitive_skill": str(best.get("cognitive_skill") or ""),
        "difficulty": str(best.get("difficulty") or ""),
        "marks": best.get("marks"),
        "count": 1,
        "appears_in": [profile["appears_in"]],
        "concept_id": None,
        "source_policy": "reuse",
        "accepted_source_qids": [str(atom.get("source_qid") or "")],
        "flags": ["unresolved_cell_classification"],
        "authority": {"attempts": attempts},
    }


# --------------------------------------------------------------------------- #
# Label reservation (spec §8.5): source order, append-only continuation
# --------------------------------------------------------------------------- #

def _label_base(concept: models.Concept) -> str:
    match = release_service._TITLE_TAG_RE.search(concept.concept_title or "")
    return match.group(1).strip() if match else f"C{concept.id}"


def _next_label_index(db: Session, base: str) -> int:
    """Continue numbering after every label already committed for this base."""
    prefix = f"{base} Q"
    taken = [
        q.question_label
        for q in db.query(models.Question)
        .filter(models.Question.question_label.startswith(prefix))
    ]
    highest = 0
    for label in taken:
        tail = label[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def run_release_for_job(
    db: Session,
    job_id: int,
    *,
    owner_sub: str,
    blueprint_cells: list[dict] | None = None,
    profile: Mapping | str | None = None,
    authorities: Mapping[str, tuple] | None = None,
    supersedes: models.AssessmentRelease | None = None,
) -> models.AssessmentRelease:
    """Run the complete assessment pipeline for one generated job.

    ``authorities`` optionally injects (author, critic) call pairs per stage
    — keys: cells, materialize, route, cluster, describe, qa (qa takes a
    single reviewer callable). Absent keys use each stage's live default.
    Returns the published release; its readiness says whether the database
    upload is open, and its manifest carries every flag and unplaced item.
    """
    authorities = dict(authorities or {})
    profile = assessment_profile.resolve(profile)
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_concepts")
    inventory = job.question_inventory or {}
    if not (inventory.get("items") or []):
        raise ReleaseRunError(
            "this job has no question/task inventory; generate concepts "
            "before building an assessment release")
    if job.deposit_scope_type != "chapter" or not job.deposit_scope_ids:
        raise ReleaseRunError("this job has no chapter deposit scope")
    chapter_id = int(job.deposit_scope_ids[0])
    chapter = db.get(models.Chapter, chapter_id)
    if chapter is None:
        raise ReleaseRunError("the job's chapter no longer exists")

    meta = {
        "subject": chapter.subject, "board": chapter.board,
        "grade": chapter.grade, "chapter_title": chapter.chapter_title,
    }

    # Stage 1-2 — freeze the lossless source inventory.
    progress.log("Assessment release: freezing the source inventory.")
    built = source_inventory.build_source_atoms(
        inventory,
        source_document_hash="sha256:" + hashlib.sha256(
            (job.mmd_text or "").encode("utf-8")).hexdigest(),
    )
    atoms = built["atoms"]

    # Stage 3 — explicit cells: provided blueprint, or per-atom
    # classification under the author/critic authority.
    if blueprint_cells is not None:
        if len(blueprint_cells) != len(atoms):
            raise ReleaseRunError(
                f"blueprint provides {len(blueprint_cells)} cells for "
                f"{len(atoms)} source atoms; reuse cells pair one-to-one")
        cells = [dict(cell) for cell in blueprint_cells]
    else:
        progress.log(
            f"Classifying {len(atoms)} source atom(s) into blueprint cells.")
        author, critic = authorities.get("cells", (None, None))
        cells = [
            author_cell_for_atom(
                atom, meta=meta, profile=profile,
                author_call=author, critic_call=critic)
            for atom in atoms
        ]

    # Stage 4-6 — materialize candidates (zero-loss inside).
    progress.log(f"Materializing {len(atoms)} assessment candidate(s).")
    author, critic = authorities.get("materialize", (None, None))
    materialized = materialization.materialize_candidates(
        list(zip(atoms, cells)),
        meta=meta,
        author_call=author, critic_call=critic,
    )
    candidates = materialized["candidates"]
    for candidate, atom, cell in zip(candidates, atoms, cells):
        candidate["route_evidence"] = atom.get("route_evidence") or {}
        if cell.get("flags"):
            candidate["flags"] = list(candidate.get("flags") or []) + [
                "cell:" + flag for flag in cell["flags"]]

    # Stage 7 — one home concept per candidate.
    concepts = sorted(
        (c for t in chapter.topics for c in t.concepts),
        key=lambda c: (c.topic.source_order, c.topic_id, c.source_order, c.id),
    )
    concept_payload = [
        {
            "concept_id": concept.id,
            "concept_title": concept.concept_title,
            "teaching_description": concept.concept_details,
            "is_culmination": cr.is_culmination(concept.concept_title or ""),
        }
        for concept in concepts
    ]
    progress.log(
        f"Routing {len(candidates)} candidate(s) across "
        f"{len(concept_payload)} concept(s).")
    author, critic = authorities.get("route", (None, None))
    routed = routing.route_candidates(
        candidates, concept_payload, meta=meta,
        router_call=author, critic_call=critic,
    )
    placements = routed["placements"]
    placement_by_candidate = {
        p["candidate_id"]: p for p in placements
    }
    concepts_by_id = {c.id: c for c in concepts}

    # Stage 8-9 — mechanical tiering, then variant clustering per
    # (concept, tier). Candidates without a routed home or a mappable
    # difficulty stay unplaced and ride the manifest ledger.
    buckets: dict[tuple[int, str], list[dict]] = {}
    for candidate in candidates:
        placement = placement_by_candidate.get(candidate["candidate_id"])
        concept_id = placement.get("concept_id") if placement else None
        if placement and placement.get("flags"):
            candidate["flags"] = list(candidate.get("flags") or []) + [
                "route:" + flag for flag in placement["flags"]]
        if (
            concept_id is None
            or concept_id not in concepts_by_id
            or placement.get("basis") == "unresolved"
        ):
            # An uncertified home is never grouped or labelled: the best
            # evidence-bound route stays visible on the placement record,
            # and the candidate rides the manifest's unplaced ledger,
            # blocking database upload (spec §14).
            continue
        try:
            tier = grouping.tier_for_difficulty(candidate.get("difficulty"))
        except grouping.GroupingError:
            candidate["flags"] = list(candidate.get("flags") or []) + [
                "untierable_difficulty"]
            continue
        candidate["concept_key"] = f"db:{concept_id}"
        buckets.setdefault((concept_id, tier), []).append(candidate)

    author, critic = authorities.get("cluster", (None, None))
    describe_author, describe_critic = authorities.get(
        "describe", (None, None))
    qa_reviewer = authorities.get("qa", (None,))[0]

    groups: list[dict] = []
    for (concept_id, tier), members in sorted(
        buckets.items(), key=lambda item: (item[0][0], item[0][1]),
    ):
        concept = concepts_by_id[concept_id]
        clustered = grouping.cluster_tier(
            members,
            concept_title=concept.concept_title,
            tier=tier,
            meta=meta,
            author_call=author, critic_call=critic,
        )
        members_by_id = {m["candidate_id"]: m for m in members}
        machine = _label_base(concept)
        for sequence, family in enumerate(clustered["families"], start=1):
            family_members = [
                members_by_id[cid] for cid in family["member_candidate_ids"]
            ]
            record = grouping.group_record(
                concept_id=f"db:{concept_id}",
                concept_machine_id=machine,
                tier=tier,
                sequence=sequence,
                member_candidate_ids=list(family["member_candidate_ids"]),
                family=family.get("family", ""),
                flags=list(clustered["flags"]),
                authority=clustered["authority"],
            )
            record["concept_key"] = f"db:{concept_id}"
            description = grouping.describe_group(
                record, family_members, meta=meta,
                author_call=describe_author, critic_call=describe_critic,
            )
            record["semantic_description"] = description["description"]
            record["flags"] = list(record["flags"]) + list(
                description["flags"])
            review = quality.review_group(
                record, family_members,
                siblings=[g for g in groups
                          if g["concept_key"] == record["concept_key"]
                          and g["group_type"] == tier],
                concept_description=concept.concept_details or "",
                meta=meta,
                reviewer_call=qa_reviewer,
            )
            record["flags"] = list(record["flags"]) + [
                f"qa:{flag['code']}" for flag in review["flags"]]
            for member in family_members:
                member["group_key"] = record["group_key"]
                placement = placement_by_candidate[member["candidate_id"]]
                placement["group_key"] = record["group_key"]
            groups.append(record)

    # Stage 8.5 — labels from accepted source order, append-only.
    label_cursor: dict[str, int] = {}
    for candidate in candidates:
        concept_key = str(candidate.get("concept_key") or "")
        if not concept_key:
            continue
        concept = concepts_by_id[int(concept_key.removeprefix("db:"))]
        base = _label_base(concept)
        if base not in label_cursor:
            label_cursor[base] = _next_label_index(db, base)
        candidate["question_label"] = f"{base} Q{label_cursor[base]:02d}"
        label_cursor[base] += 1

    payload = {
        "source_atoms": atoms,
        "blueprint_cells": cells,
        "candidates": candidates,
        "groups": groups,
        "placements": placements,
    }
    release = release_service.create_release(
        db,
        chapter_id=chapter_id,
        payload=payload,
        job_id=job.id,
        owner_sub=owner_sub,
        supersedes=supersedes,
    )
    release = release_service.publish_release(db, release)
    progress.log(
        f"Assessment release {release.release_uid} v{release.version} "
        f"published: readiness "
        f"{(release.diagnostics or {}).get('readiness')!r}.",
        level="success",
    )
    return release
