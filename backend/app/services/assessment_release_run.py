"""End-to-end assessment release orchestration for one generated job.

The glue between the finished Build Concepts job and the two output files:

    source atoms  ->  cell classification (author + critic)
                  ->  question/answer/rubric materialization (author + critic)
                  ->  one-home routing (author + critic)
                  ->  level verdicts  ->  variant clustering
                  ->  group descriptions  ->  touched-group QA
                  ->  immutable release  ->  atomic dual publication

Every semantic stage already exists; this module only sequences them,
carries identities, and never decides content itself. All model calls are
injectable per stage so the whole pipeline is testable offline; the live
defaults inside each stage module are used when nothing is injected.

Fail-closed composition: structural impossibility raises with its exact
diagnostic. Semantic dissent and Fixer decisions ship with stable warning
codes and row-private audit evidence. Nothing is dropped anywhere in the
chain.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

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
from .phase3 import envelope as phase3_envelope
from .phase3 import kernel

MAX_ATTEMPTS = 3

_LEVEL_AUDIT_FIELD = "_aegis_assessment_level_verdict"
_CLUSTER_AUDIT_FIELD = "_aegis_assessment_variant_cluster"
_DESCRIPTION_AUDIT_FIELD = "_aegis_assessment_group_description"
_QUALITY_AUDIT_FIELD = "_aegis_assessment_group_quality"

_LEVEL_WARNING = "assessment_level_review"
_CLUSTER_WARNING = "assessment_variant_cluster_review"
_DESCRIPTION_WARNING = "assessment_group_description_review"
_QUALITY_WARNING = "assessment_group_quality_review"

_LEVELS_SNAPSHOT = "source.phase3-assessment-levels.json"
_GROUPS_SNAPSHOT = "source.phase3-assessment-groups.json"

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


def _authority_pair(
    authorities: Mapping[str, Any], key: str,
) -> tuple[Any | None, Any | None]:
    """Return one injected author/critic pair without inventing defaults."""

    value = authorities.get(key, ())
    if isinstance(value, (tuple, list)):
        return (
            value[0] if value else None,
            value[1] if len(value) > 1 else None,
        )
    return (value, None) if callable(value) else (None, None)


def _stable_authority(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project a decision onto its timestamp-free release authority."""

    authority = result.get("authority")
    source = authority if isinstance(authority, Mapping) else {}
    if not source and isinstance(result.get("decision"), Mapping):
        source = result["decision"]
    decision_key = str(
        source.get("decision_key") or source.get("key") or ""
    )
    policy_version = str(source.get("policy_version") or "")
    review_flags = [
        str(flag)
        for flag in source.get("review_flags") or []
        if str(flag).strip()
    ]
    projected: dict[str, Any] = {}
    if decision_key:
        projected["decision_key"] = decision_key
    if policy_version:
        projected["policy_version"] = policy_version
    if review_flags:
        projected["review_flags"] = review_flags
    if bool(source.get("fixer")):
        projected["fixer"] = True
    return projected


def _needs_review(result: Mapping[str, Any]) -> bool:
    authority = _stable_authority(result)
    return bool(
        result.get("flags")
        or authority.get("review_flags")
        or authority.get("fixer")
    )


def _append_warning(record: dict, warning: str) -> None:
    flags = [str(flag) for flag in record.get("flags") or []]
    if warning not in flags:
        flags.append(warning)
    record["flags"] = flags


def _job_artifact_directory(job_id: int) -> Path | None:
    helper = getattr(uploads, "source_artifact_directory", None)
    if not callable(helper):
        return None
    try:
        directory = Path(helper(int(job_id)))
    except (OSError, TypeError, ValueError):
        return None
    return directory if directory.is_dir() else None


def _decision_context(
    job_id: int,
    *,
    envelope_sha256: str | None,
    decision_store: kernel.DecisionStore | None,
) -> tuple[str, kernel.DecisionStore, Path | None]:
    """Resolve one verified envelope/store pair for every assessment pass.

    Production uses the job's sealed Phase-3 wrapper and durable decision
    directory. Tests may inject both identities together; accepting only one
    would make replay provenance ambiguous, so that is a mechanical error.
    """

    if (envelope_sha256 is None) != (decision_store is None):
        raise ReleaseRunError(
            "envelope_sha256 and decision_store must be supplied together"
        )
    if envelope_sha256 is not None and decision_store is not None:
        envelope_sha = str(envelope_sha256).strip()
        if not envelope_sha:
            raise ReleaseRunError("envelope_sha256 must not be empty")
        store_directory = getattr(decision_store, "_directory", None)
        snapshot_directory = (
            Path(store_directory).parent if store_directory is not None
            else _job_artifact_directory(job_id)
        )
        return envelope_sha, decision_store, snapshot_directory

    artifact_directory = _job_artifact_directory(job_id)
    if artifact_directory is None:
        raise ReleaseRunError(
            "the job has no durable Phase-3 artifact directory"
        )
    envelope_path = artifact_directory / "source.phase3-envelope.json"
    try:
        wrapper = json.loads(envelope_path.read_text(encoding="utf-8"))
        if not isinstance(wrapper, Mapping):
            raise ValueError("the envelope wrapper is not an object")
        envelope = phase3_envelope.validate(wrapper.get("envelope") or {})
    except (OSError, TypeError, ValueError) as exc:
        raise ReleaseRunError(
            "the job's sealed Phase-3 envelope is unavailable or invalid: "
            f"{exc}"
        ) from exc
    envelope_sha = str(envelope.get("envelope_sha256") or "").strip()
    if not envelope_sha:
        raise ReleaseRunError("the verified Phase-3 envelope has no seal")
    return (
        envelope_sha,
        kernel.DecisionStore(artifact_directory / "phase3-decisions"),
        artifact_directory,
    )


def _concept_evidence(concept: models.Concept) -> dict[str, Any]:
    concept_key = f"db:{concept.id}"
    return {
        "concept_key": concept_key,
        "concept_id": concept_key,
        "concept_machine_id": _label_base(concept),
        "concept_title": str(concept.concept_title or ""),
        "concept_display_name": str(concept.concept_display_name or ""),
        "teaching_description": str(concept.concept_details or ""),
    }


def _group_evidence(group: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic group state only; private audits never become new evidence."""

    private = {
        _CLUSTER_AUDIT_FIELD,
        _DESCRIPTION_AUDIT_FIELD,
        _QUALITY_AUDIT_FIELD,
        "authority",
        "flags",
    }
    return {
        key: value for key, value in group.items() if key not in private
    }


def _learner_text_snapshot(candidates: list[Mapping]) -> list[tuple]:
    return [
        (
            str(candidate.get("candidate_id") or ""),
            candidate.get("question"),
            candidate.get("question_text"),
        )
        for candidate in candidates
    ]


def _assert_learner_text_unchanged(
    before: list[tuple], candidates: list[Mapping],
) -> None:
    after = _learner_text_snapshot(candidates)
    if after == before:
        return
    before_by_id = {str(row[0]): row[1:] for row in before}
    after_by_id = {str(row[0]): row[1:] for row in after}
    changed = sorted(
        candidate_id
        for candidate_id in set(before_by_id) | set(after_by_id)
        if before_by_id.get(candidate_id) != after_by_id.get(candidate_id)
    )
    raise grouping.GroupingError(
        "assessment grouping altered immutable learner-facing question text"
        + (f": {changed}" if changed else "")
    )


def _write_snapshot(
    directory: Path | None, filename: str, payload: Mapping[str, Any],
) -> None:
    if directory is None:
        return
    from . import canonical_source_phase3 as phase3_core

    try:
        phase3_core._atomic_write(
            directory / filename,
            json.dumps(
                dict(payload), ensure_ascii=False, indent=1, sort_keys=True
            ),
        )
    except OSError as exc:
        raise ReleaseRunError(
            f"could not persist assessment decision snapshot {filename}: "
            f"{exc}"
        ) from exc


def _snapshot_levels(
    directory: Path | None,
    *,
    envelope_sha256: str,
    candidates: list[Mapping],
) -> None:
    rows = []
    for candidate in candidates:
        audit = candidate.get(_LEVEL_AUDIT_FIELD)
        if not isinstance(audit, Mapping):
            continue
        rows.append({
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "concept_key": str(candidate.get("concept_key") or ""),
            "tier": str(audit.get("tier") or ""),
            "rationale": str(audit.get("rationale") or ""),
            "flags": list(audit.get("flags") or []),
            "authority": dict(audit.get("authority") or {}),
        })
    _write_snapshot(
        directory,
        _LEVELS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "levels": rows,
            "levels_sha256": rel.sha256_json(rows),
        },
    )


def _snapshot_groups(
    directory: Path | None,
    *,
    envelope_sha256: str,
    groups: list[Mapping],
) -> None:
    rows = []
    for group in groups:
        rows.append({
            "group_key": str(group.get("group_key") or ""),
            "concept_key": str(group.get("concept_key") or ""),
            "tier": str(group.get("group_type") or ""),
            "family": str(group.get("family") or ""),
            "member_candidate_ids": list(
                group.get("member_candidate_ids") or []
            ),
            "semantic_description": str(
                group.get("semantic_description") or ""
            ),
            "flags": list(group.get("flags") or []),
            _CLUSTER_AUDIT_FIELD: dict(
                group.get(_CLUSTER_AUDIT_FIELD) or {}
            ),
            _DESCRIPTION_AUDIT_FIELD: dict(
                group.get(_DESCRIPTION_AUDIT_FIELD) or {}
            ),
            _QUALITY_AUDIT_FIELD: dict(
                group.get(_QUALITY_AUDIT_FIELD) or {}
            ),
        })
    _write_snapshot(
        directory,
        _GROUPS_SNAPSHOT,
        {
            "envelope_sha256": envelope_sha256,
            "groups": rows,
            "groups_sha256": rel.sha256_json(rows),
        },
    )


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
    authorities: Mapping[str, Any] | None = None,
    envelope_sha256: str | None = None,
    decision_store: kernel.DecisionStore | None = None,
    supersedes: models.AssessmentRelease | None = None,
) -> models.AssessmentRelease:
    """Run the complete assessment pipeline for one generated job.

    ``authorities`` optionally injects (author, critic) call pairs per stage
    — keys: cells, materialize, route, level, cluster, describe, qa, plus an
    optional one-call ``fixer`` tuple. Absent keys use each stage's live
    default. Tests inject ``envelope_sha256`` and ``decision_store`` together;
    production verifies the job's sealed envelope and uses its durable store.
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

    envelope_sha, store, snapshot_directory = _decision_context(
        job.id,
        envelope_sha256=envelope_sha256,
        decision_store=decision_store,
    )

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

    # Stage 8 — a recorded level verdict for every valid routed candidate.
    # No blueprint label is executable tier logic. An unresolved home remains
    # explicitly unplaced.
    learner_text_before = _learner_text_snapshot(candidates)
    eligible: list[dict] = []
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
        candidate["concept_key"] = f"db:{concept_id}"
        eligible.append(candidate)

    level_provider, level_critic = _authority_pair(authorities, "level")
    fixer = _authority_pair(authorities, "fixer")[0]
    level_rows = grouping.decide_levels(
        [
            {
                "candidate": candidate,
                "concept": _concept_evidence(
                    concepts_by_id[
                        int(str(candidate["concept_key"]).removeprefix("db:"))
                    ]
                ),
            }
            for candidate in eligible
        ],
        meta=meta,
        envelope_sha256=envelope_sha,
        provider=level_provider,
        critic=level_critic,
        store=store,
        fixer=fixer,
    )
    expected_level_ids = [
        str(candidate.get("candidate_id") or "") for candidate in eligible
    ]
    returned_level_ids: list[str] = []
    duplicate_level_ids: set[str] = set()
    level_by_candidate: dict[str, Mapping[str, Any]] = {}
    for level in level_rows:
        if not isinstance(level, Mapping):
            raise grouping.GroupingError(
                "assessment level verdict is not an object"
            )
        candidate_id = str(level.get("candidate_id") or "")
        if candidate_id in level_by_candidate:
            duplicate_level_ids.add(candidate_id)
        returned_level_ids.append(candidate_id)
        level_by_candidate[candidate_id] = level
    level_report = rel.zero_loss_report(
        expected_level_ids, returned_level_ids, []
    )
    if (
        not level_report["holds"]
        or duplicate_level_ids
        or len(returned_level_ids) != len(expected_level_ids)
    ):
        raise grouping.GroupingError(
            "assessment level coverage mismatch: "
            f"missing={level_report['missing']}; "
            f"unexpected={level_report['unexpected']}; "
            f"duplicates={sorted(duplicate_level_ids)}"
        )

    buckets: dict[tuple[int, str], list[dict]] = {}
    for candidate in eligible:
        candidate_id = str(candidate.get("candidate_id") or "")
        level = level_by_candidate[candidate_id]
        tier = str(level.get("tier") or "")
        if tier not in grouping.TIER_CODES:
            raise grouping.GroupingError(
                f"assessment level for {candidate_id!r} has unknown tier "
                f"{tier!r}"
            )
        level_flags = [
            str(flag) for flag in level.get("flags") or []
            if str(flag).strip()
        ]
        candidate[_LEVEL_AUDIT_FIELD] = {
            "tier": tier,
            "rationale": str(level.get("rationale") or ""),
            "flags": level_flags,
            "authority": _stable_authority(level),
        }
        if _needs_review(level):
            _append_warning(candidate, _LEVEL_WARNING)
        concept_id = int(str(candidate["concept_key"]).removeprefix("db:"))
        buckets.setdefault((concept_id, tier), []).append(candidate)

    _assert_learner_text_unchanged(learner_text_before, candidates)
    _snapshot_levels(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        candidates=candidates,
    )

    # Stage 9 — decide every concept+tier partition, then assemble every
    # occupied group before any later pass sees the family set.
    cluster_provider, cluster_critic = _authority_pair(
        authorities, "cluster"
    )
    tier_order = {tier: index for index, tier in enumerate(grouping.TIER_CODES)}

    groups: list[dict] = []
    for (concept_id, tier), members in sorted(
        buckets.items(),
        key=lambda item: (item[0][0], tier_order[item[0][1]]),
    ):
        concept = concepts_by_id[concept_id]
        concept_evidence = _concept_evidence(concept)
        clustered = grouping.cluster_tier(
            members,
            concept=concept_evidence,
            tier=tier,
            existing_groups=[],
            meta=meta,
            envelope_sha256=envelope_sha,
            provider=cluster_provider,
            critic=cluster_critic,
            store=store,
            fixer=fixer,
        )
        members_by_id = {m["candidate_id"]: m for m in members}
        machine = _label_base(concept)
        for sequence, family in enumerate(clustered["families"], start=1):
            member_ids = [
                str(candidate_id)
                for candidate_id in family.get("member_candidate_ids") or []
            ]
            unknown = sorted(set(member_ids) - set(members_by_id))
            if unknown:
                raise grouping.GroupingError(
                    f"variant family names unknown candidates: {unknown}"
                )
            record = grouping.group_record(
                concept_id=f"db:{concept_id}",
                concept_machine_id=machine,
                concept_name=concept.concept_display_name,
                tier=tier,
                sequence=sequence,
                member_candidate_ids=member_ids,
                family=family.get("family", ""),
                flags=[],
                authority=_stable_authority(clustered),
            )
            record["concept_key"] = f"db:{concept_id}"
            record[_CLUSTER_AUDIT_FIELD] = {
                "family": str(family.get("family") or ""),
                "member_candidate_ids": member_ids,
                "flags": [
                    str(flag) for flag in clustered.get("flags") or []
                    if str(flag).strip()
                ],
                "authority": _stable_authority(clustered),
            }
            if _needs_review(clustered):
                _append_warning(record, _CLUSTER_WARNING)
            for member_id in member_ids:
                member = members_by_id[member_id]
                member["group_key"] = record["group_key"]
                member[_CLUSTER_AUDIT_FIELD] = {
                    "group_key": record["group_key"],
                    "family": str(family.get("family") or ""),
                    "flags": [
                        str(flag)
                        for flag in clustered.get("flags") or []
                        if str(flag).strip()
                    ],
                    "authority": _stable_authority(clustered),
                }
                if _needs_review(clustered):
                    _append_warning(member, _CLUSTER_WARNING)
                placement = placement_by_candidate[member["candidate_id"]]
                placement["group_key"] = record["group_key"]
            groups.append(record)

    duplicate_group_keys = rel.duplicate_group_keys(groups)
    if duplicate_group_keys:
        raise grouping.GroupingError(
            f"duplicate group_key values: {duplicate_group_keys}"
        )
    grouped_ids = [
        str(candidate_id)
        for group in groups
        for candidate_id in group.get("member_candidate_ids") or []
    ]
    duplicate_grouped_ids: set[str] = set()
    seen_grouped_ids: set[str] = set()
    for candidate_id in grouped_ids:
        if candidate_id in seen_grouped_ids:
            duplicate_grouped_ids.add(candidate_id)
        seen_grouped_ids.add(candidate_id)
    grouping_report = rel.zero_loss_report(
        expected_level_ids, grouped_ids, []
    )
    if (
        not grouping_report["holds"]
        or duplicate_grouped_ids
        or len(grouped_ids) != len(expected_level_ids)
    ):
        raise grouping.GroupingError(
            "assessment candidate-to-group coverage mismatch: "
            f"missing={grouping_report['missing']}; "
            f"unexpected={grouping_report['unexpected']}; "
            f"duplicates={sorted(duplicate_grouped_ids)}"
        )

    # Stage 10 — describe only after the complete family partition exists.
    describe_provider, describe_critic = _authority_pair(
        authorities, "describe"
    )
    all_members_by_id = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in eligible
    }
    for record in groups:
        concept_id = int(str(record["concept_key"]).removeprefix("db:"))
        concept = concepts_by_id[concept_id]
        family_members = [
            all_members_by_id[str(candidate_id)]
            for candidate_id in record.get("member_candidate_ids") or []
        ]
        description = grouping.describe_group(
            _group_evidence(record),
            family_members,
            concept=_concept_evidence(concept),
            meta=meta,
            envelope_sha256=envelope_sha,
            provider=describe_provider,
            critic=describe_critic,
            store=store,
            fixer=fixer,
        )
        record["semantic_description"] = str(
            description.get("description") or ""
        )
        record[_DESCRIPTION_AUDIT_FIELD] = {
            "description": record["semantic_description"],
            "flags": [
                str(flag) for flag in description.get("flags") or []
                if str(flag).strip()
            ],
            "authority": _stable_authority(description),
        }
        if _needs_review(description):
            _append_warning(record, _DESCRIPTION_WARNING)

    # Stage 11 — every group receives the complete, symmetric same-home/tier
    # sibling context. QA only flags and never changes the authored records.
    qa_provider, qa_critic = _authority_pair(authorities, "qa")
    quality_groups = [_group_evidence(group) for group in groups]
    for record in groups:
        concept_id = int(str(record["concept_key"]).removeprefix("db:"))
        concept = concepts_by_id[concept_id]
        family_members = [
            all_members_by_id[str(candidate_id)]
            for candidate_id in record.get("member_candidate_ids") or []
        ]
        siblings = [
            {
                "group": sibling,
                "members": [
                    all_members_by_id[str(candidate_id)]
                    for candidate_id in sibling.get("member_candidate_ids")
                    or []
                ],
            }
            for sibling in quality_groups
            if sibling.get("group_key") != record.get("group_key")
            and sibling.get("concept_key") == record.get("concept_key")
            and sibling.get("group_type") == record.get("group_type")
        ]
        review = quality.review_group(
            _group_evidence(record),
            family_members,
            siblings=siblings,
            concept=_concept_evidence(concept),
            meta=meta,
            envelope_sha256=envelope_sha,
            provider=qa_provider,
            critic=qa_critic,
            store=store,
            fixer=fixer,
        )
        review_flags = [
            dict(flag) if isinstance(flag, Mapping) else str(flag)
            for flag in review.get("flags") or []
        ]
        record[_QUALITY_AUDIT_FIELD] = {
            "quality_review": str(review.get("quality_review") or ""),
            "flags": review_flags,
            "authority": _stable_authority(review),
        }
        if (
            _needs_review(review)
            or str(review.get("quality_review") or "") == "flagged"
        ):
            _append_warning(record, _QUALITY_WARNING)

    _assert_learner_text_unchanged(learner_text_before, candidates)
    _snapshot_groups(
        snapshot_directory,
        envelope_sha256=envelope_sha,
        groups=groups,
    )

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
