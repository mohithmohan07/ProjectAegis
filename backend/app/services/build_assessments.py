"""Module 1: Build Assessments.

Two paths, exactly as specified:

  (a) From Concept Mapping — drill the directory to a chapter/topic/concept
      scope, stack one or more Blueprint batches (cognitive skill x difficulty x
      category x question type x count), then Generate. Question content always
      comes from the concept level, so chapter/topic scopes fan out to concepts.

  (b) From Upload — upload a PDF/text/image, convert to MMD, pick an upload type
      (and, for textbooks, extract-vs-create), choose where to deposit in the
      directory, then identify questions and fill the Bulk Import columns.

Both paths finish by running the post-generation pipeline (tagging -> column
mapping -> append-only write).
"""
from __future__ import annotations

import copy
import json
import math
import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import workbook_sync
from . import (
    assessment_blueprint, assessment_grouping, assessment_release,
    assessment_routing, auth, directory, generation, identity, mmd,
    post_generation, progress, uploads,
)
from .phase3 import kernel


_LEGACY_CELL_MARK_POLICY_VERSION = "assessment-legacy-cell-contract-2"

_LEGACY_CELL_MARK_SYSTEM = (
    "You are the Aegis legacy blueprint-cell marks author. The user has "
    "explicitly selected the sheet kind, question category, cognitive skill, "
    "difficulty, count, and assessment purpose for ONE concept-scoped cell. "
    "Decide the positive finite marks per question that make that requested "
    "response contract realistic for the supplied grade, subject, concept "
    "teaching description, and axes. Also decide a positive finite duration "
    "and whether a Subjective/Descriptive response needs the math keyboard. "
    "Do not infer any value from sheet kind, use a default, or alter a "
    "selected axis. Objective cells use an empty math_keyboard because that "
    "column is not part of their sheet contract. Return ONLY strict JSON: "
    '{"cell_id":"","marks":1,"question_duration":2,'
    '"math_keyboard":"Yes|No|","rationale":"evidence-bound reason"}'
)

_LEGACY_CELL_MARK_CRITIC_SYSTEM = (
    "You are the independent advisory critic for one legacy blueprint-cell "
    "cell-contract verdict. Audit whether the proposed marks, duration, and "
    "keyboard requirement fit the complete concept and explicitly selected "
    "response contract. Do not replace, gate, or "
    "default the verdict; dissent ships as review evidence. Return ONLY strict "
    'JSON: {"verdict":"verified|dissent","confidence":0.0,"issues":[]}'
)


# --------------------------------------------------------------------------- #
# Path A — From Concept Mapping
# --------------------------------------------------------------------------- #

class AssessmentSessionNotFound(ValueError):
    """Raised when a session is absent or belongs to another principal."""


class AssessmentSessionAlreadyRunning(RuntimeError):
    """Raised when a second mutation targets an actively generating session."""


_session_locks: dict[int, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def is_session_running(session_id: int | None) -> bool:
    if not session_id:
        return False
    with _session_locks_guard:
        lock = _session_locks.get(int(session_id))
        return bool(lock and lock.locked())


@contextmanager
def _exclusive_session_generation(session_id: int):
    with _session_locks_guard:
        lock = _session_locks.setdefault(int(session_id), threading.Lock())
    if not lock.acquire(blocking=False):
        raise AssessmentSessionAlreadyRunning(
            "generation is already running for this assessment session"
        )
    try:
        yield
    finally:
        lock.release()


def get_session(
    db: Session,
    session_id: int,
    *,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> models.AssessmentSession:
    session = db.query(models.AssessmentSession).filter(
        models.AssessmentSession.id == session_id,
        models.AssessmentSession.owner_sub == owner_sub,
        models.AssessmentSession.source == "concept_mapping",
    ).one_or_none()
    if session is None:
        # Missing and foreign rows intentionally look identical so session IDs
        # cannot be used to discover another user's private work.
        raise AssessmentSessionNotFound("session not found")
    return session


def create_session(
    db: Session,
    scope_type: str,
    scope_ids: list[int],
    *,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> models.AssessmentSession:
    if scope_type not in {"chapter", "topic", "concept"}:
        raise ValueError("scope_type must be chapter | topic | concept")
    if not directory.resolve_scope_concepts(db, scope_type, scope_ids):
        raise ValueError("scope selection resolves to no concepts")
    session = models.AssessmentSession(
        owner_sub=owner_sub,
        source="concept_mapping",
        scope_type=scope_type,
        scope_ids=scope_ids,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def add_batch(
    db: Session, session_id: int, *,
    cognitive_skills: list[str], difficulty_levels: list[str],
    categories: list[str], question_type: str, num_questions: int,
    appears_in: list[str] | None = None,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> models.BlueprintBatch:
    session = get_session(db, session_id, owner_sub=owner_sub)
    with _exclusive_session_generation(session.id):
        db.refresh(session)
        if session.status == "generated":
            raise ValueError(
                "this assessment session has already been generated; "
                "create a new session"
            )
        if question_type not in {"objective", "subjective", "descriptive"}:
            raise ValueError(
                "question_type must be objective | subjective | descriptive")
        if not cognitive_skills:
            raise ValueError("select at least one cognitive skill")
        if not difficulty_levels:
            raise ValueError("select at least one difficulty level")
        if not categories or not all(str(value).strip() for value in categories):
            raise ValueError("select at least one question category")
        if int(num_questions) < 1:
            raise ValueError("num_questions must be at least 1")
        invalid_purposes = [
            purpose for purpose in (appears_in or [])
            if purpose not in bi.APPEARS_IN
        ]
        if invalid_purposes:
            raise ValueError(
                f"unknown appears_in values: {invalid_purposes!r}"
            )
        purposes = list(appears_in or [])
        normalized_skills = [
            bi.normalize_cognitive_skills(value)
            for value in cognitive_skills
        ]
        normalized_difficulties = [
            bi.normalize_difficulty(value)
            for value in difficulty_levels
        ]
        if any(value not in bi.COGNITIVE_SKILLS for value in normalized_skills):
            raise ValueError(
                f"cognitive_skills must be chosen from {bi.COGNITIVE_SKILLS}"
            )
        if any(
            value not in bi.DIFFICULTY_LEVELS
            for value in normalized_difficulties
        ):
            raise ValueError(
                f"difficulty_levels must be chosen from {bi.DIFFICULTY_LEVELS}"
            )
        batch = models.BlueprintBatch(
            session_id=session_id,
            # Old gerund forms normalize to standard action-verb values.
            cognitive_skills=normalized_skills,
            difficulty_levels=normalized_difficulties,
            categories=[str(value).strip() for value in categories],
            question_type=question_type,
            num_questions=int(num_questions),
            appears_in=purposes,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch


def _group_for_recorded_tier(
    db: Session, concept: models.Concept, tier: str,
) -> models.Group:
    """Persist a recorded tier without guessing a same-tier variant family.

    The level verdict is semantic authority.  This helper only validates its
    enum, projects Q12's friendly name, and performs unambiguous database
    identity bookkeeping.  More than one existing group in the tier means a
    variant-family verdict is required; choosing the first would silently
    overwrite that decision, so the legacy writer refuses the placement.
    """

    if tier not in assessment_grouping.TIER_CODES:
        raise ValueError(
            "recorded assessment tier must be Basic, Intermediate, or "
            f"Advanced (got {tier!r})"
        )
    visible_name = assessment_grouping.friendly_group_name(
        concept.concept_display_name, tier)
    matches = [group for group in concept.groups if group.group_type == tier]
    if len(matches) > 1:
        raise ValueError(
            f"concept {concept.id} has multiple {tier} variant groups; "
            "the legacy append lane cannot choose one without a recorded "
            "variant-family verdict"
        )
    if matches:
        group = matches[0]
        sequence = int(group.group_sequence or 1)
        if not str(group.group_key or "").strip():
            group.group_key = assessment_grouping.group_key_for(
                identity.machine_id_for_concept(concept), tier, sequence
            )
        group.group_sequence = sequence
        group.group_name = visible_name
        group.group_display_name = visible_name
        return group

    sequence = 1
    group = models.Group(
        concept_id=concept.id,
        group_type=tier,
        group_key=assessment_grouping.group_key_for(
            identity.machine_id_for_concept(concept), tier, sequence),
        group_sequence=sequence,
        group_name=visible_name,
        group_display_name=visible_name,
        group_status="Active",
    )
    db.add(group)
    db.flush()
    concept.groups.append(group)
    return group


def _legacy_candidate(candidate_id: str, record: dict) -> dict:
    """Full legacy evidence, with canonical aliases for shared MES passes."""

    candidate = copy.deepcopy(record)
    candidate.update({
        "candidate_id": candidate_id,
        "cognitive_skill": (
            record.get("cognitive_skill")
            or record.get("cognitive_skills")
        ),
        "difficulty": (
            record.get("difficulty")
            or record.get("level_of_difficulty")
        ),
    })
    return candidate


def _legacy_concept(concept: models.Concept) -> dict:
    concept_key = f"db:{concept.id}"
    return {
        "concept_key": concept_key,
        "concept_id": concept_key,
        "concept_machine_id": identity.machine_id_for_concept(concept),
        "concept_title": concept.concept_title,
        "concept_display_name": concept.concept_display_name,
        "description": concept.concept_details,
        "teaching_description": concept.concept_details,
        "concept_details": concept.concept_details,
    }


def _legacy_level_metadata(concepts: list[models.Concept]) -> dict:
    chapters = [concept.topic.chapter for concept in concepts]
    return {
        "subjects": sorted({str(chapter.subject or "") for chapter in chapters}),
        "boards": sorted({str(chapter.board or "") for chapter in chapters}),
        "grades": sorted({str(chapter.grade or "") for chapter in chapters}),
        "chapters": sorted({
            str(chapter.chapter_title or "") for chapter in chapters
        }),
    }


def _legacy_cell_mark_authorities() -> tuple[
    kernel.Provider | None, kernel.Critic | None, kernel.Provider | None,
]:
    """Production delegates cell-mark authorship to live kernel authorities."""

    return None, None, None


def _live_legacy_cell_mark(payload: dict) -> dict:
    return generation._openai_json(
        _LEGACY_CELL_MARK_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_mapping",
    )


def _live_legacy_cell_mark_critic(payload: dict) -> dict:
    return generation._openai_json(
        _LEGACY_CELL_MARK_CRITIC_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        purpose="concept_validation",
    )


def _legacy_cell_mark_checker(
    cell_id: str, sheet_kind: str,
) -> kernel.Checker:
    """Mechanics only: identity, finite positive numeric shape, rationale."""

    def check(response: dict) -> list[str]:
        if not isinstance(response, dict):
            return ["response is not an object"]
        defects: list[str] = []
        if str(response.get("cell_id") or "") != cell_id:
            defects.append(f"cell_id must echo {cell_id!r}")
        marks = response.get("marks")
        if isinstance(marks, bool):
            defects.append("marks must be numeric")
        else:
            try:
                numeric = float(marks)
                if not math.isfinite(numeric) or numeric <= 0:
                    defects.append("marks must be finite and positive")
            except (TypeError, ValueError):
                defects.append("marks must be numeric")
        duration = response.get("question_duration")
        if isinstance(duration, bool):
            defects.append("question_duration must be numeric")
        else:
            try:
                numeric_duration = float(duration)
                if (
                    not math.isfinite(numeric_duration)
                    or numeric_duration <= 0
                ):
                    defects.append(
                        "question_duration must be finite and positive")
            except (TypeError, ValueError):
                defects.append("question_duration must be numeric")
        keyboard = str(response.get("math_keyboard") or "").strip()
        if sheet_kind in {"subjective", "descriptive"}:
            if keyboard not in {"Yes", "No"}:
                defects.append(
                    "math_keyboard must be Yes or No for non-objective cells")
        elif keyboard:
            defects.append("math_keyboard must be blank for objective cells")
        if not str(response.get("rationale") or "").strip():
            defects.append("response has no rationale")
        return defects

    return check


def _recorded_cell_marks(
    cells: list[dict],
    *,
    concepts_by_id: dict[int, models.Concept],
    meta: dict,
    envelope_sha256: str,
    store: kernel.DecisionStore,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, dict]:
    """Author one cached marks verdict for every legacy blueprint cell."""

    if not cells:
        return {}
    if provider is None and critic is None and fixer is None:
        provider, critic, fixer = _legacy_cell_mark_authorities()
    if provider is None:
        from .phase3 import envelope as envelope_mod
        from .phase3 import fixer as fixer_mod

        envelope_mod.require_live_api()
        provider = _live_legacy_cell_mark
        critic = critic or _live_legacy_cell_mark_critic
        fixer = fixer or fixer_mod.live_fixer

    prepared: list[tuple[str, dict, dict]] = []
    seen: set[str] = set()
    for cell in cells:
        cell_id = str(cell.get("cell_id") or "").strip()
        if not cell_id or cell_id in seen:
            raise ValueError(
                "legacy blueprint cells require unique nonempty cell_id values"
            )
        seen.add(cell_id)
        concept_id = cell.get("concept_id")
        concept = concepts_by_id.get(concept_id)
        if concept is None:
            raise ValueError(
                f"legacy blueprint cell {cell_id!r} has no scoped concept"
            )
        # Materialize ORM evidence before worker threads; no worker may lazily
        # query through the request Session.
        prepared.append((cell_id, cell, _legacy_concept(concept)))

    def decide_one(unit: tuple[str, dict, dict]) -> tuple[str, dict]:
        cell_id, cell, concept_payload = unit
        blueprint_cell = {
            key: cell.get(key)
            for key in (
                "cell_id", "sheet_kind", "question_category",
                "cognitive_skill", "difficulty", "count", "appears_in",
                "concept_id", "source_policy",
            )
        }
        payload = {
            "stage": "assessment.legacy-cell-mark",
            "rules": _LEGACY_CELL_MARK_SYSTEM,
            "critic_rules": _LEGACY_CELL_MARK_CRITIC_SYSTEM,
            "metadata": dict(meta),
            "blueprint_cell": blueprint_cell,
            "concept": copy.deepcopy(concept_payload),
        }
        decision = kernel.decide(
            kind="assessment.legacy_cell_contract",
            unit_id=cell_id,
            envelope_sha256=envelope_sha256,
            payload=payload,
            provider=provider,
            checker=_legacy_cell_mark_checker(
                cell_id, str(cell.get("sheet_kind") or "")),
            critic=critic,
            store=store,
            policy_version=_LEGACY_CELL_MARK_POLICY_VERSION,
            fixer=fixer,
        )
        response = dict(decision["response"])
        review_flags = [
            str(flag) for flag in decision.get("review_flags") or []
            if str(flag).strip()
        ]
        return cell_id, {
            "marks": float(response["marks"]),
            "question_duration": float(response["question_duration"]),
            "math_keyboard": str(response.get("math_keyboard") or ""),
            "rationale": str(response.get("rationale") or ""),
            "flags": review_flags,
            "authority": {
                "decision_key": str(decision.get("key") or ""),
                "policy_version": str(decision.get("policy_version") or ""),
                "review_flags": review_flags,
                "fixer": bool(decision.get("fixer")),
            },
        }

    rows = kernel.parallel_map_in_order(
        prepared,
        decide_one,
        max_workers=config.phase3_decision_workers(),
    )
    return dict(rows)


def _legacy_level_authorities() -> tuple[
    kernel.Provider | None, kernel.Critic | None, kernel.Provider | None,
]:
    """Leave semantic authority to the grouping kernel in production.

    Tests that exercise the offline legacy workflow inject an explicit fixture
    authority at this seam; there is no local production verdict or fallback.
    """

    return None, None, None


def _legacy_decision_store(kind: str, identity: int) -> kernel.DecisionStore:
    return kernel.DecisionStore(
        config.DATA_DIR
        / "assessment-decisions"
        / str(kind)
        / str(int(identity))
    )


def _recorded_tiers(
    units: list[tuple[str, dict, models.Concept]],
    *,
    envelope_sha256: str,
    store: kernel.DecisionStore,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    fixer: kernel.Provider | None = None,
) -> dict[str, str]:
    """Run the shared level pass and bind every candidate exactly once."""

    if not units:
        return {}
    concepts = [concept for _candidate_id, _record, concept in units]
    if provider is None and critic is None and fixer is None:
        provider, critic, fixer = _legacy_level_authorities()
    decisions = assessment_grouping.decide_levels(
        [
            {
                "candidate": _legacy_candidate(candidate_id, record),
                "concept": _legacy_concept(concept),
            }
            for candidate_id, record, concept in units
        ],
        meta=_legacy_level_metadata(concepts),
        envelope_sha256=envelope_sha256,
        provider=provider,
        critic=critic,
        store=store,
        fixer=fixer,
    )
    expected = [candidate_id for candidate_id, _record, _concept in units]
    decided: dict[str, str] = {}
    for decision in decisions:
        candidate_id = str(decision.get("candidate_id") or "")
        if candidate_id in decided:
            raise ValueError(
                f"recorded level verdict repeated candidate {candidate_id!r}"
            )
        decided[candidate_id] = str(decision.get("tier") or "")
    if set(decided) != set(expected) or len(decided) != len(expected):
        raise ValueError(
            "recorded level verdict coverage mismatch: "
            f"expected={expected!r}, decided={sorted(decided)!r}"
        )
    return decided


def generate(
    db: Session,
    session_id: int,
    *,
    owner_sub: str = auth.LOCAL_OWNER_SUB,
) -> dict:
    """Generate questions for every concept x batch x (skill,difficulty,category) cell."""
    session = get_session(db, session_id, owner_sub=owner_sub)
    with _exclusive_session_generation(session.id):
        db.refresh(session)
        if session.status == "generated":
            raise ValueError(
                "this assessment session has already been generated; "
                "create a new session"
            )
        return _generate_session(db, session)


def _generate_session(
    db: Session,
    session: models.AssessmentSession,
) -> dict:
    session_id = session.id
    owner_sub = session.owner_sub
    if not session.batches:
        raise ValueError("add at least one blueprint batch before generating")

    concepts = directory.resolve_scope_concepts(db, session.scope_type, session.scope_ids)
    # Compile the stacked batches into explicit, validated blueprint cells
    # BEFORE any generation spend (spec Stage 3). The complete obligation
    # list exists up front, its totals are exact, and every created question
    # is stamped with the cell it fulfills — the old implicit Cartesian
    # product resolved inside this loop is retired.
    cells = assessment_blueprint.compile_cells_from_batches(
        session.batches,
        concepts=concepts,
    )
    prompt_concepts_by_id = {concept.id: concept for concept in concepts}
    session_envelope_sha256 = assessment_release.sha256_json({
        "kind": "legacy-assessment-session",
        "session_id": session_id,
        "owner_sub": owner_sub,
        "scope_type": session.scope_type,
        "scope_ids": list(session.scope_ids or []),
        "blueprint_cells": cells,
        "concepts": [_legacy_concept(concept) for concept in concepts],
    })
    decision_store = _legacy_decision_store("sessions", session_id)
    cell_contract_by_id = _recorded_cell_marks(
        cells,
        concepts_by_id=prompt_concepts_by_id,
        meta=_legacy_level_metadata(concepts),
        envelope_sha256=session_envelope_sha256,
        store=decision_store,
    )
    for cell in cells:
        contract = cell_contract_by_id[cell["cell_id"]]
        cell["marks"] = contract["marks"]
        cell["question_duration"] = contract["question_duration"]
        cell["math_keyboard"] = contract["math_keyboard"]
    assessment_blueprint.validate_cells(cells)
    cell_totals = assessment_blueprint.totals(cells)
    progress.log(
        f"Compiled {cell_totals['cells']} explicit blueprint cell(s): "
        f"{cell_totals['total_questions']} question obligation(s), "
        f"{cell_totals['total_marks']:g} total marks across "
        f"{len(concepts)} concept(s).")

    # Generation can call an external model and therefore stays outside the
    # process-wide workbook lock. These indices are prompt seeds only; final
    # labels are reserved from freshly loaded database state under the shared
    # finalization lock below.
    prompt_indices: dict[int, int] = {c.id: 1 for c in concepts}
    generated_batches: list[tuple[int, str, list[tuple[str, dict]]]] = []
    for done, cell in enumerate(cells):
        concept = prompt_concepts_by_id[cell["concept_id"]]
        progress.step(
            f"{concept.concept_title}: {cell['difficulty']}/"
            f"{cell['cognitive_skill']}/{cell['question_category']}",
            value=done / max(len(cells), 1))
        records = generation.generate_questions_for_concept(
            concept,
            question_type=cell["sheet_kind"],
            cognitive_skill=cell["cognitive_skill"],
            difficulty=cell["difficulty"],
            category=cell["question_category"],
            count=cell["count"],
            marks=cell["marks"],
            question_duration=cell["question_duration"],
            math_keyboard=cell["math_keyboard"],
            start_index=prompt_indices[concept.id],
            appears_in=", ".join(cell["appears_in"]),
        )
        if len(records) != int(cell["count"]):
            raise RuntimeError(
                f"blueprint cell {cell['cell_id']} required {cell['count']} "
                f"questions but its recorded author returned {len(records)}; "
                "refusing partial persistence"
            )
        prompt_indices[concept.id] += len(records)
        identified_records = [
            (
                f"LEGACY-SESSION-{session_id}-{cell['cell_id']}-{index:04d}",
                record,
            )
            for index, record in enumerate(records, start=1)
        ]
        generated_batches.append(
            (cell["concept_id"], cell["cell_id"], identified_records))

    level_units = [
        (candidate_id, record, prompt_concepts_by_id[concept_id])
        for concept_id, _cell_id, identified_records in generated_batches
        for candidate_id, record in identified_records
    ]
    recorded_tier_by_candidate = _recorded_tiers(
        level_units,
        envelope_sha256=session_envelope_sha256,
        store=decision_store,
    )

    progress.step("Tagging & column mapping", value=0.9)
    # Refresh counter state and reserve final labels while holding the same
    # re-entrant lock used by the workbook writer. This keeps concurrent
    # sessions from allocating the same labels and prevents a database/workbook
    # divergence where the second workbook row would otherwise be skipped.
    with workbook_sync.output_workbook_lock():
        # End the read transaction used for prompt generation so this session
        # observes labels committed by an earlier finalizer before allocating.
        db.rollback()
        db.expire_all()
        session = get_session(db, session_id, owner_sub=owner_sub)
        if session.status == "generated":
            raise ValueError(
                "this assessment session has already been generated; "
                "create a new session"
            )
        refreshed_concepts = directory.resolve_scope_concepts(
            db, session.scope_type, session.scope_ids
        )
        concepts_by_id = {concept.id: concept for concept in refreshed_concepts}
        if any(
            concept_id not in concepts_by_id
            for concept_id, _cell_id, _records in generated_batches
        ):
            raise ValueError(
                "scope selection changed while questions were generated"
            )

        # Per-concept running index keeps question labels unique and ordered,
        # continuing after every question committed by earlier sessions.
        # A MAX-SCAN, not a count (T5-3): counting reissues the number of every
        # deleted question, and R5 says an uploaded label is never reassigned.
        # ``identity.next_label_index`` is the same helper the release lane
        # calls, keyed on the same persisted ``machine_id`` the label is minted
        # from, so the two lanes continue one family instead of two.
        label_bases: dict[int, str] = {
            concept.id: identity.machine_id_for_concept(concept)
            for concept in refreshed_concepts
        }
        # Keyed by BASE, not by concept id, and that is load-bearing: the label
        # family is the base, so two concepts that resolve to one base must
        # share one counter or they mint the same label inside a single run and
        # the partial unique index turns it into an IntegrityError that escapes
        # ``db.commit()`` below and loses the whole generated batch. Two
        # concepts DO share a base whenever both carry a blank ``machine_id``
        # (no resolvable chapter), and on a database that already carries a
        # duplicate persisted ``machine_id`` — the T9-1 B1 defect the
        # publication act blocks, which the generation run must survive rather
        # than crash on.
        counters: dict[str, int] = {
            base: identity.next_label_index(db, base)
            for base in set(label_bases.values())
        }
        label_family_notes = _legacy_label_family_notes(
            refreshed_concepts, label_bases)
        created_ids: list[int] = []
        for concept_id, cell_id, identified_records in generated_batches:
            concept = concepts_by_id[concept_id]
            for candidate_id, generated_record in identified_records:
                group = _group_for_recorded_tier(
                    db, concept, recorded_tier_by_candidate[candidate_id])
                record = dict(generated_record)
                base = label_bases[concept_id]
                record["question_label"] = generation.question_label(
                    concept, counters[base]
                )
                counters[base] += 1
                question = models.Question(
                    group_id=group.id,
                    blueprint_cell_id=cell_id,
                    **_question_kwargs(record)
                )
                db.add(question)
                db.flush()
                created_ids.append(question.id)
        db.commit()

        pipeline = post_generation.run(db, created_ids)
        session.status = "generated"
        session.generated_question_ids = created_ids
        db.commit()

    # Quality review summary: deterministic checks + anti-monotony report.
    from . import assessment_prompts as ap
    created = db.query(models.Question).filter(models.Question.id.in_(created_ids)).all()
    problems: list[str] = []
    for q in created:
        for p in ap.review_question({
            "sheet_kind": q.sheet_kind, "question": q.question,
            "question_text": q.question_text, "cognitive_skills": q.cognitive_skills,
            "level_of_difficulty": q.level_of_difficulty, "marks": q.marks,
            "answers": q.answers,
        }):
            problems.append(f"{q.question_label}: {p}")
    monotony = ap.stem_monotony_report([q.question for q in created])
    progress.set_progress(1.0, label="Done")
    progress.log(f"Created {len(created_ids)} questions.", level="success")
    return {
        "session_id": session_id, "created": len(created_ids),
        "question_ids": created_ids,
        "pipeline": pipeline,
        "notes": label_family_notes,
        "review": {"problems": problems[:50],
                   "monotony": {k: monotony[k] for k in
                                ("worst", "worst_count", "generic_ratio", "monotonous")}},
    }


def _legacy_label_family_notes(concepts, bases: dict[int, str]) -> list[dict]:
    """Say out loud that a concept carries two label conventions (T5/R5).

    A grandfathered label is never re-minted, so the max-scan legitimately
    ignores it — it belongs to a different family. Reporting both prefixes is
    the difference between "the scan is scoped" and "the scan missed rows".
    """
    notes: list[dict] = []
    for concept in concepts:
        note = identity.legacy_label_family_for_concept(
            concept, bases.get(concept.id, ""))
        if note is not None:
            notes.append(note)
    return notes


def _question_kwargs(rec: dict) -> dict:
    sheet_kind = str(rec.get("sheet_kind") or "").strip()
    if sheet_kind not in {"objective", "subjective", "descriptive"}:
        raise ValueError("question record has no valid recorded sheet_kind")
    category = str(rec.get("question_category") or "").strip()
    if not category:
        raise ValueError("question record has no recorded question_category")
    cognitive_skill = bi.normalize_cognitive_skills(
        rec.get("cognitive_skills") or "")
    if cognitive_skill not in bi.COGNITIVE_SKILLS:
        raise ValueError(
            "question record has no valid recorded cognitive_skills")
    difficulty = bi.normalize_difficulty(
        rec.get("level_of_difficulty") or "")
    if difficulty not in bi.DIFFICULTY_LEVELS:
        raise ValueError(
            "question record has no valid recorded level_of_difficulty")
    marks = generation._positive_recorded_number(rec.get("marks"), "marks")
    duration = generation._positive_recorded_number(
        rec.get("question_duration"), "question_duration")
    math_keyboard = generation._recorded_math_keyboard(
        rec.get("math_keyboard"), sheet_kind)
    return {
        "sheet_kind": sheet_kind,
        "question_label": rec.get("question_label", ""),
        "question_category": category,
        "cognitive_skills": cognitive_skill,
        "question_source": rec.get("question_source", ""),
        "level_of_difficulty": difficulty,
        "question_duration": duration,
        "math_keyboard": math_keyboard,
        "question": rec.get("question", ""),
        "question_text": rec.get("question_text", ""),
        "question_appears_in": rec.get("question_appears_in", ""),
        "marks": marks,
        "display_answer": rec.get("display_answer", ""),
        "answer_explanation": rec.get("answer_explanation", ""),
        "answers": rec.get("answers", []),
        "sub_questions": rec.get("sub_questions", []),
        "origin": rec.get("origin", "concept_mapping"),
    }


# --------------------------------------------------------------------------- #
# Path B — From Upload
# --------------------------------------------------------------------------- #

def _legacy_route_authorities() -> tuple[
    kernel.Provider | None, kernel.Critic | None, kernel.Provider | None,
]:
    """Production delegates every real routing choice to shared authorities."""

    return None, None, None


def _route_uploaded_questions(
    records: list[dict],
    concepts: list[models.Concept],
    *,
    candidate_ids: list[str],
    meta: dict,
    envelope_sha256: str,
    source_concept_release_sha256: str,
    store: kernel.DecisionStore,
    provider: kernel.Provider | None = None,
    critic: kernel.Critic | None = None,
    fixer: kernel.Provider | None = None,
) -> list[dict]:
    """Route every uploaded question through the shared decide-once pass."""

    if len(candidate_ids) != len(records):
        raise ValueError(
            "upload routing candidate identity coverage does not match records")
    if provider is None and critic is None and fixer is None:
        provider, critic, fixer = _legacy_route_authorities()
    candidates = [
        _legacy_candidate(candidate_id, record)
        for candidate_id, record in zip(candidate_ids, records)
    ]
    result = assessment_routing.route_candidates(
        candidates,
        [_legacy_concept(concept) for concept in concepts],
        meta=meta,
        envelope_sha256=envelope_sha256,
        source_concept_release_sha256=source_concept_release_sha256,
        provider=provider,
        critic=critic,
        store=store,
        fixer=fixer,
    )
    placements = list(result.get("placements") or [])
    known_keys = {_legacy_concept(concept)["concept_key"] for concept in concepts}
    for placement in placements:
        concept_key = placement.get("concept_key")
        if concept_key not in known_keys:
            raise ValueError(
                "upload routing returned no known home concept for candidate "
                f"{placement.get('candidate_id')!r}"
            )
    return placements


def create_upload_job(
    db: Session, *, upload_type: str, filename: str, raw_bytes: bytes,
    source_book: str = "",
    owner_sub: str | None = None,
) -> models.UploadJob:
    """Stage an uploaded file ONLY. Conversion to MMD is a separate step
    (``uploads.convert_job``) so a mistakenly-chosen file can be replaced first.
    """
    if upload_type not in mmd.UPLOAD_TYPES:
        raise ValueError(f"upload_type must be one of {mmd.UPLOAD_TYPES}")
    job = models.UploadJob(
        owner_sub=uploads.normalize_owner_sub(owner_sub),
        module="build_assessments", upload_type=upload_type,
        filename=Path(filename).name, mmd_text="", status="uploaded",
        source_book=source_book.strip(),
    )
    return uploads.persist_new_job(db, job, raw_bytes)


def set_textbook_mode(
    db: Session, job_id: int, mode: str, *,
    owner_sub: str | None = None,
) -> models.UploadJob:
    """For upload_type='textbook': extract existing Q&A, or create new questions."""
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_assessments")
    if mode not in {"extract", "create"}:
        raise ValueError("textbook mode must be extract | create")
    with uploads.exclusive_job_operation(job.id):
        db.refresh(job)
        if job.upload_type != "textbook":
            raise ValueError("textbook mode is only valid for textbook uploads")
        if job.status not in {"uploaded", "converted", "deposited"}:
            raise ValueError(
                "cannot change textbook mode after generation; "
                "start a new upload"
            )
        job.textbook_mode = mode
        db.commit()
        db.refresh(job)
    return job


def set_deposit(
    db: Session, job_id: int, scope_type: str, scope_ids: list[int], *,
    owner_sub: str | None = None,
) -> models.UploadJob:
    """Choose where uploaded questions are deposited (chapter / topics / concepts)."""
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_assessments")
    with uploads.exclusive_job_operation(job.id):
        db.refresh(job)
        if job.status not in {"converted", "deposited"} or not job.mmd_text:
            if job.status == "generated":
                raise ValueError(
                    "cannot change the deposit scope after generation; "
                    "start a new upload"
                )
            raise ValueError(
                "convert the uploaded document to MMD before setting a "
                "deposit scope"
            )
        if scope_type not in {"chapter", "topic", "concept"}:
            raise ValueError("scope_type must be chapter | topic | concept")
        if not directory.resolve_scope_concepts(db, scope_type, scope_ids):
            raise ValueError("deposit selection resolves to no concepts")
        job.deposit_scope_type = scope_type
        job.deposit_scope_ids = scope_ids
        job.status = "deposited"
        db.commit()
        db.refresh(job)
    return job


def generate_from_upload(
    db: Session,
    job_id: int,
    question_type: str = "auto",
    *,
    owner_sub: str | None = None,
) -> dict:
    """Identify questions from the uploaded MMD and deposit them in the chosen scope.

    ``question_type`` is ``auto`` (detect & absorb a mix of objective /
    subjective / descriptive) or one specific type to force.
    """
    if question_type not in {"auto", "objective", "subjective", "descriptive"}:
        raise ValueError(
            "question_type must be auto | objective | subjective | descriptive")
    job = uploads.get_job(
        db, job_id, owner_sub=owner_sub, module="build_assessments")
    if not job.mmd_text:
        raise ValueError("convert the uploaded document to MMD before generating")
    if job.status != "deposited":
        raise ValueError("set a deposit scope before generating")

    concepts = directory.resolve_scope_concepts(db, job.deposit_scope_type, job.deposit_scope_ids)
    progress.log(
        f"Generating questions from upload into {len(concepts)} concept(s).")
    records = generation.identify_questions_from_mmd(
        job.mmd_text, upload_type=job.upload_type, question_type=question_type,
        textbook_mode=job.textbook_mode,
    )
    if job.source_book:
        records = [
            {**record, "question_source": job.source_book}
            for record in records
        ]
    candidate_ids = [
        f"LEGACY-UPLOAD-{job.id}-{index:04d}"
        for index in range(1, len(records) + 1)
    ]
    candidate_payloads = [
        _legacy_candidate(candidate_id, record)
        for candidate_id, record in zip(candidate_ids, records)
    ]
    concept_payloads = [_legacy_concept(concept) for concept in concepts]
    source_concept_release_sha256 = assessment_release.sha256_json(
        concept_payloads)
    upload_envelope_sha256 = assessment_release.sha256_json({
        "kind": "legacy-assessment-upload",
        "job_id": job.id,
        "owner_sub": job.owner_sub,
        "upload_type": job.upload_type,
        "textbook_mode": job.textbook_mode,
        "question_type": question_type,
        "source_book": job.source_book,
        "source_mmd": job.mmd_text,
        "scope_type": job.deposit_scope_type,
        "scope_ids": list(job.deposit_scope_ids or []),
        "candidates": candidate_payloads,
        "concepts": concept_payloads,
    })
    decision_store = _legacy_decision_store("uploads", job.id)
    # Routing happens outside the workbook lock (it may call the model). Every
    # real multi-concept choice is one cached assessment.route verdict; a sole
    # concept is the shared router's mechanical no-choice case.
    placements = _route_uploaded_questions(
        records,
        concepts,
        candidate_ids=candidate_ids,
        meta=_legacy_level_metadata(concepts),
        envelope_sha256=upload_envelope_sha256,
        source_concept_release_sha256=source_concept_release_sha256,
        store=decision_store,
    )
    concept_by_key = {
        payload["concept_key"]: concept
        for payload, concept in zip(concept_payloads, concepts)
    }
    routed_concepts = [
        concept_by_key[str(placement["concept_key"])]
        for placement in placements
    ]
    routed_concept_ids = [concept.id for concept in routed_concepts]
    placement_by_candidate = {
        str(placement["candidate_id"]): placement
        for placement in placements
    }
    level_units = [
        (candidate_id, record, concept)
        for candidate_id, record, concept in zip(
            candidate_ids, records, routed_concepts)
    ]
    recorded_tier_by_candidate = _recorded_tiers(
        level_units,
        envelope_sha256=upload_envelope_sha256,
        store=decision_store,
    )
    progress.step("Depositing & tagging questions", value=0.85)

    # The duplicate query, database writes, and workbook publication are one
    # serialized transaction boundary.  Without this lock, two uploads can both
    # observe a question as absent and append duplicate rows to the database and
    # shared workbook.
    with workbook_sync.output_workbook_lock():
        db.expire_all()
        job = uploads.get_job(
            db, job_id, owner_sub=owner_sub, module="build_assessments"
        )
        if job.status != "deposited":
            raise ValueError("set a deposit scope before generating")
        concepts = directory.resolve_scope_concepts(
            db, job.deposit_scope_type, job.deposit_scope_ids
        )
        if not concepts:
            raise ValueError("deposit selection resolves to no concepts")
        concepts_by_id = {c.id: c for c in concepts}
        if any(cid not in concepts_by_id for cid in routed_concept_ids):
            raise ValueError(
                "deposit selection changed while questions were generated")

        # Cross-book duplicate check: existing question texts in the deposit
        # chapters. A duplicate is not re-added; its sources are merged instead.
        chapter_ids = {c.topic.chapter_id for c in concepts}
        existing_by_text: dict[str, models.Question] = {}
        for qq in (
            db.query(models.Question)
            .join(models.Group).join(models.Concept).join(models.Topic)
            .filter(models.Topic.chapter_id.in_(chapter_ids))
        ):
            norm = bi.normalize_question_text(qq.question)
            if norm:
                existing_by_text.setdefault(norm, qq)

        created_ids: list[int] = []
        merged_ids: list[int] = []
        # The same max-scan as the generation path and the release lane
        # (T5-3/R5): a count reissues a deleted question's number.
        label_bases: dict[int, str] = {
            c.id: identity.machine_id_for_concept(c) for c in concepts
        }
        # Keyed by BASE for the same reason as ``_generate_session``: one
        # family, one counter, or a shared base mints one label twice inside a
        # single run.
        counters: dict[str, int] = {
            base: identity.next_label_index(db, base)
            for base in set(label_bases.values())
        }
        label_family_notes = _legacy_label_family_notes(concepts, label_bases)
        # Deposit each question into its routed home concept. Placement was
        # decided above (model judgment in live mode; mechanical only for a
        # sole-concept scope) — never round-robin arithmetic on a live path.
        for i, rec in enumerate(records):
            candidate_id = candidate_ids[i]
            concept = concepts_by_id[routed_concept_ids[i]]
            norm = bi.normalize_question_text(rec.get("question", ""))
            dup = existing_by_text.get(norm) if norm else None
            if dup is not None:
                if dup.group.concept_id != concept.id:
                    raise ValueError(
                        "an uploaded duplicate is already persisted under a "
                        "different routed home concept; refusing to relocate "
                        "or silently overwrite its semantic placement"
                    )
                dup.question_source = bi.merge_sources(
                    dup.question_source, rec.get("question_source", "")
                )
                merged_ids.append(dup.id)
                continue
            base = label_bases[concept.id]
            rec.setdefault(
                "question_label",
                generation.question_label(concept, counters[base]),
            )
            counters[base] += 1
            group = _group_for_recorded_tier(
                db, concept, recorded_tier_by_candidate[candidate_id]
            )
            placement = placement_by_candidate[candidate_id]
            q = models.Question(
                group_id=group.id,
                route_audit={
                    "concept_key": placement["concept_key"],
                    "basis": placement["basis"],
                    "evidence": placement["evidence"],
                    "rationale": placement["rationale"],
                    "flags": list(placement.get("flags") or []),
                    "authority": dict(placement.get("authority") or {}),
                },
                **_question_kwargs(rec),
            )
            db.add(q)
            db.flush()
            if norm:
                existing_by_text[norm] = q
            created_ids.append(q.id)
        db.commit()

        # Run the pipeline over new questions AND source-merged duplicates so
        # the output workbook's question_source cells refresh in place.
        pipeline = post_generation.run(db, created_ids + merged_ids)
        job.status = "generated"
        job.result_ids = created_ids
        job.detail = (
            f"identified {len(records)} questions from {job.upload_type} upload "
            f"({len(created_ids)} new, {len(merged_ids)} duplicates "
            "source-merged)"
        )
        db.commit()

    progress.set_progress(1.0, label="Done")
    progress.log(
        f"Created {len(created_ids)} new questions "
        f"({len(merged_ids)} duplicates source-merged).", level="success")
    return {
        "job_id": job_id, "created": len(created_ids),
        "duplicates_merged": len(merged_ids),
        "question_ids": created_ids + merged_ids,
        "pipeline": pipeline,
        "notes": label_family_notes,
    }
