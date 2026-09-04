"""Explicit database publication for a staged Build Concepts release."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.orm import Session

from .. import bulk_import as bi
from .. import config, models
from ..bulk_import import workbook_sync
from . import (
    assessment_release_snapshot,
    build_concepts,
    concept_cleanup,
    generation_recovery,
    identity,
    uploads,
)
from .build_concepts_release import (
    LANE_POST,
    LANE_PRE,
    PRE_LANE_VERDICT_FIELD,
    STAGED_RELEASE_UID_FIELD,
    ReleaseUnavailableError,
    _lift_resolved_related_concepts,
    _strip_release_fields,
    normalize_lane,
    nothing_to_publish,
    release_key_for_lane,
    release_payload,
    row_projection_defect,
    staged_version,
    structural_defects,
)


def _refuse_a_moved_seal(
    db: Session,
    job: models.UploadJob,
    payload: Mapping[str, Any],
    lane: str,
) -> None:
    """Refuse publication when the staged draft moved under its frozen Master.

    S10-g, the concept lane's only tamper gate: before this, a semantically
    edited but well-shaped payload published without complaint — the lane
    had no verification of any kind, while the Master lane re-hashes both
    artifacts and its snapshot. The comparison keys on the draft's LINEAGE
    (Round 7): a frozen same-job, same-lane ``AssessmentRelease`` that
    records the ``staged_release_uid`` this payload carries NOW froze THIS
    draft, so the same draft must still hash to what was sealed — a
    mismatch is an in-place edit nobody recorded. It used to key on
    ``staged_version``, and [measured] that counter restarts at 1 on a
    legitimate inventory reset (``replace_file``, ``convert_job``, the
    source-review checkpoint), so a fresh run's v1 collided with an old
    frozen v1 and the gate accused a clean draft of tampering. A legitimate
    re-stage mints a new uid, so it can never trip this.

    One anomaly IS still provable without a uid match: a frozen row of this
    job+lane RECORDS a non-empty uid — so this job's payload carried one at
    freeze time and staging has stamped one ever since — while the payload
    now carries NONE. That is the field stripped in place, and it refuses.
    Recorded limits, stated rather than implied: a payload and frozen rows
    that both predate the uid leave nothing to compare (the gate is dormant
    for them, exactly like its Master-lane counterpart before hashes were
    recorded), and an edit that swaps in a fresh uuid is indistinguishable
    from a legitimate re-stage BY THE PAYLOAD ALONE — the frozen Master's
    own artifacts stay hash-sealed regardless, and the machine-id
    resolution surfaces the drift loudly at the Master upload. The seal
    excludes ``issues`` and ``summary`` by construction, so this
    publication's own summary writes and the ``assessment_lane_unavailable``
    recorder never move it. Mechanics: it compares recorded strings and
    judges no content.
    """

    current_uid = str(
        payload.get(STAGED_RELEASE_UID_FIELD) or ""
    ).strip()
    rows = (
        db.query(models.AssessmentRelease)
        .filter(
            models.AssessmentRelease.job_id == job.id,
            models.AssessmentRelease.lane == lane,
        )
        .order_by(models.AssessmentRelease.id.desc())
        .all()
    )
    recomputed = ""
    for row in rows:
        recorded_uid = str(
            (row.provider_identity or {}).get("staged_release_uid") or ""
        ).strip()
        if not recorded_uid:
            continue
        if not current_uid:
            raise ValueError(
                f"the frozen Master (release {row.release_uid} "
                f"v{row.version}) records the staged draft lineage it froze, "
                "but this staged payload carries no lineage at all; the "
                "field was removed in place, so this publication is refused "
                "— re-stage the release to record a new draft"
            )
        if recorded_uid != current_uid:
            continue
        sealed = str(
            (row.concept_snapshot or {}).get("source_concept_release_sha256")
            or ""
        ).strip()
        if not sealed:
            continue
        if not recomputed:
            recomputed = assessment_release_snapshot.source_release_sha256(
                payload
            )
        if recomputed != sealed:
            raise ValueError(
                f"staged release v{staged_version(payload)} no longer "
                f"matches the content its frozen Master (release "
                f"{row.release_uid} v{row.version}) sealed; the staged "
                "draft was edited in place after the freeze, so this "
                "publication is refused — re-stage the release to record "
                "the change"
            )


def upload_release_to_database(
    db: Session,
    job_id: int,
    *,
    owner_sub: str | None = None,
    lane: object = LANE_POST,
) -> dict[str, Any]:
    """Publish one lane's released rows after an explicit authenticated action.

    Flagged rows are not silently removed. Generation, semantic review and
    publication stay separate: this action performs the deterministic concept
    upsert and shared-workbook publication, but starts no model request.

    ``lane`` selects the staged slot — Output 03 (post) or Output 01 (pre),
    OD4's numbering. Rule G is untouched by that: each lane is still ONE
    separate, explicit, authenticated act, and publishing one never
    publishes the other. The body below was already lane-aware in its
    topic/concept identity (``pre_post`` scopes ``_find_or_create_topic``
    and the slot resolution), so two sequential single-lane publications
    compose; what was NOT lane-aware was the slot it read and wrote back,
    which is what this parameter fixes.

    ORDERING, recorded because it is load-bearing for the Master files:
    ``assessment_release_service._resolve_snapshot_concept_ids`` resolves a
    staged concept only against an exactly matching PUBLISHED concept in the
    same lane, so each lane's concept output must be uploaded before its
    Master can publish — Output 01 before Output 02, Output 03 before
    Output 04. It fails closed and loudly when it is not.
    """

    resolved = normalize_lane(lane)
    job = uploads.get_job(
        db,
        job_id,
        owner_sub=owner_sub,
        module="build_concepts",
    )
    db.refresh(job)
    generation_recovery.require_mutation_allowed(
        job, operation=f"publish the {resolved} Concept release"
    )
    release_key = release_key_for_lane(resolved)
    # Restructure A: publication judges the verdict RECORDED at staging.
    # A legacy payload staged before the explicit field existed is
    # backfilled once from durable evidence here, so a checkpoint echo
    # that lags the finished run ([measured] job 'Patterns') cannot
    # refuse a healthy release's database upload. Local import: the
    # terminal contract module imports this one.
    from . import build_concepts_terminal_release_contract as terminal_release

    terminal_release.ensure_explicit_terminal_verdict(
        db, job, lane=resolved,
    )
    payload = release_payload(job, lane=resolved)
    if payload is None:
        raise ReleaseUnavailableError("this upload has no staged release")
    summary = copy.deepcopy(payload.get("summary") or {})

    def _published_ids() -> list[int]:
        return copy.deepcopy(
            (summary.get("concept_ids") or [])
            if resolved == LANE_PRE
            else (job.result_ids or [])
        )

    # "Semantic doubt flags; structural corruption blocks" (§4). A
    # Diagnostic release keeps every download open and refuses only the
    # database write. The defect list is the same one the upload already
    # refused on before the Pre lane existed, so the Post lane's behaviour
    # and its message are unchanged.
    defects = structural_defects(payload)
    if defects:
        raise ValueError("; ".join(defects))
    # S10-g: a tampered payload refuses BEFORE the zero-row branch — an
    # in-place edit that emptied the records is still an in-place edit.
    _refuse_a_moved_seal(db, job, payload, resolved)
    # Round 10 (audit finding 9c): the ``database_uploaded`` idempotency
    # latch runs AFTER the structural gate and the seal, never before.
    # [measured] with the latch first, one successful publication turned
    # this function into an unconditional receipt printer: a staged
    # payload mutated in place AFTER the upload re-earned a success
    # response without any gate ever reading it. An already-uploaded
    # release re-validates and only then repeats its receipt — idempotent
    # for the honest payload, refused for the tampered one.
    if summary.get("database_uploaded"):
        return {
            "job_id": job.id,
            "status": "generated",
            "database_uploaded": True,
            "created_concept_ids": _published_ids(),
            "updated_concept_ids": [],
            "issue_count": int(summary.get("issue_count") or 0),
            "publication_status": str(
                summary.get("publication_status") or "published"
            ),
        }

    chapter_id = int(payload.get("target_chapter_id") or 0)
    chapter = db.get(models.Chapter, chapter_id)
    if chapter is None:
        raise ValueError("the release target chapter no longer exists")
    # T3.3b ordering with D4 semantics (owner decision 2026-08-29): the
    # ``_aegis_*`` marker rides the staged rows and ``_strip_release_fields``
    # drops every ``_RELEASE_AUDIT_FIELDS`` key BY CONSTRUCTION — so the
    # staging decision about ``related_concepts`` must be stamped onto the
    # public key here, first. Under D4 that decision is always EMPTY for a
    # Pre row (lane- or marker-keyed), never the resolved Post ids the
    # pre-D4 lift used to carry.
    #
    # S10: no row is dropped in silence on the DB-write path. This used to
    # be a filter that ate any row without a topic or a concept title —
    # normally shadowed by the ``structural_defects`` gate above, but
    # [measured] a payload whose RECORDED ``staged_row_defects`` key is
    # stale (empty while its records disagree) sailed past that gate's
    # recorded-key-wins branch and published one of two rows with no trace.
    # An unusable row now refuses the publication, named through the ONE
    # existing vocabulary (``staged_row_unusable``) — the same transport as
    # the gate above, judging nothing about content. It runs BEFORE the
    # zero-row branch below (Round 7): [measured] with EVERY row unusable,
    # ``nothing_to_publish`` read the payload as empty and the zero-row
    # branch published a latched success whose receipt said "nothing was
    # removed" — the silent drop this gate exists to end, wearing D8.2's
    # words.
    records = []
    for position, row in enumerate(payload.get("records") or [], start=1):
        defect = row_projection_defect(row, position)
        if defect is not None:
            raise ValueError(str(defect.get("message") or ""))
        records.append(
            _strip_release_fields(_lift_resolved_related_concepts(row)))
    if nothing_to_publish(payload):
        # D8.2 — PUBLISHING NOTHING IS AN IDEMPOTENT ZERO-ROW SUCCESS.
        #
        # §4:475-478 requires publication to be "idempotent, model-free,
        # never drops a highlighted row"; writing nothing when there is
        # nothing IS the idempotent answer, and it is reached only after
        # ``structural_defects`` above found no corruption — which, for the
        # Pre lane, includes the recorded ``assumes_nothing`` verdict
        # (D8.3). An empty capture nobody decided never gets here.
        #
        # This is the only refusal S9 removes from this function. The other
        # five stay, and stay for one reason: Rule E blocks the DATABASE
        # WRITE on a defect, it does not make the write unconditional.
        # ``ReleaseUnavailableError`` (no staged release) and the three
        # missing-chapter checks all describe a write that has no target,
        # and the bare ``raise`` after ``db.rollback()`` is what stops a
        # half-written transaction reporting success.
        summary.update({
            "database_uploaded": True,
            "database_uploaded_at": datetime.now(timezone.utc).isoformat(),
            "created_count": 0,
            "merged_count": 0,
            "publication_status": "published",
        })
        if resolved == LANE_PRE:
            summary["concept_ids"] = []
        payload["summary"] = summary
        inventory = copy.deepcopy(dict(job.question_inventory or {}))
        inventory[release_key] = copy.deepcopy(payload)
        job.question_inventory = inventory
        if resolved == LANE_POST:
            job.status = "generated"
            job.result_ids = []
        lane_label = "Pre-Learning " if resolved == LANE_PRE else ""
        # THE RECEIPT MAY NOT CLAIM A DECISION NOBODY MADE. [measured] this
        # sentence read "the run recorded that its emptiness is correct" on
        # BOTH lanes — but D8.3's verdict is Pre-lane only
        # (``_pre_lane_verdict_defects`` returns ``[]`` for every other
        # lane), so on the Post lane it asserted, as a fact in the job's own
        # record, exactly the shape-inference D8.3 spends a model call to
        # abolish. A receipt is the one place a reviewer reads what was
        # decided; the difference between "a verdict says so" and "nothing
        # here said otherwise" is the whole of R4. So it names the recorded
        # verdict when there is one, and says plainly that there is none
        # when there is not.
        verdict_row = payload.get(PRE_LANE_VERDICT_FIELD)
        verdict_row = verdict_row if isinstance(verdict_row, Mapping) else {}
        verdict = str(verdict_row.get("verdict") or "").strip()
        rationale = str(verdict_row.get("rationale") or "").strip()
        if verdict:
            accounted = (
                f"the run recorded the verdict {verdict!r} on that "
                "emptiness"
                + (f": {rationale}" if rationale else "")
            )
        else:
            accounted = (
                "this lane records no verdict on whether that emptiness is "
                "correct, so it is reported as measured rather than as "
                "decided"
            )
        job.detail = (
            f"Explicit {lane_label}database upload completed with no row to "
            f"write: this release is empty and {accounted}. Nothing was "
            "created and nothing was removed."
        )
        db.commit()
        db.refresh(job)
        return {
            "job_id": job.id,
            "status": "generated",
            "database_uploaded": True,
            "created_concept_ids": [],
            "updated_concept_ids": [],
            "issue_count": int(summary.get("issue_count") or 0),
            "publication_status": "published",
            "publication": {"written": 0, "sources_updated": 0},
        }
    pre_post = "Pre" if resolved == LANE_PRE else "Post"
    # Contract v2.0 §18: the publication is the run's frozen source book;
    # a filename is never borrowed as one (an unknown publication stays
    # blank and is a recorded release blocker, never a nearby value).
    source_book = str(payload.get("source_book") or "").strip()
    created_ids: list[int] = []
    merged_ids: list[int] = []
    # T4-2: a grade label the normaliser could not parse keeps its raw token
    # (KG and Nursery must never collapse into one ``00`` family) and the
    # fallback is RECORDED here rather than stamped silently.
    review_flags: list[str] = []
    topic_positions: dict[str, int] = {}
    concept_positions: dict[str, int] = {}
    # Round 7: rows this publication has already resolved or created. A
    # persisted row may be merged into by AT MOST one staged row per
    # publication — the second same-titled row under one topic creates
    # beside it instead of overwriting the first row's just-written content
    # (last-wins would lose the first staged row, R4).
    claimed_ids: set[int] = set()

    try:
        db.expire_all()
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is None:
            raise ValueError("the release target chapter no longer exists")
        grade_flag = identity.grade_review_flag(chapter)
        if grade_flag:
            review_flags.append(grade_flag)
        for position, raw in enumerate(records, start=1):
            # S10: the row IS what the reviewer approved in the staged
            # workbook, byte for byte. ``clean_concept_record`` ran here for
            # years and was [measured] a rewriter at this boundary — the
            # staged title "pH and its meaning" persisted as "pH and Its
            # Meaning", and a "Fig. 2.1" reference plus its sentence head
            # were destroyed on the reviewer's way to the database. Staging
            # already cleaned what generation produced; a reviewer's edit
            # after that is a decision, not an artifact.
            rec = dict(raw)
            # T7.2's surviving half (Round 7): the cleaner no longer runs
            # here, but where it WOULD have changed the row the divergence
            # is RECORDED — the reviewer's text is preserved and the diff
            # from house normalization rides the receipt instead of dying
            # in silence. Rows that staging already normalized diverge on
            # nothing (the cleaner is idempotent), so the note appears only
            # where a human's edit actually won.
            would_be = concept_cleanup.clean_concept_record(
                copy.deepcopy(rec))
            diverged = [
                field
                for field in (
                    "topic", "parent_concept", "concept_title",
                    "concept_details",
                )
                if str(rec.get(field) or "") != str(would_be.get(field) or "")
            ]
            if diverged:
                review_flags.append(
                    f"staged row {position} ({rec.get('concept_title')!r}) "
                    "was published byte-exactly where house normalization "
                    f"would have rewritten {', '.join(diverged)}; the "
                    "reviewer's text won (T7.2)"
                )
            # T4-6: the second of four live topic identities, converged on the
            # one shared normaliser.
            topic_key = identity.topic_identity(rec["topic"])
            topic_positions.setdefault(topic_key, len(topic_positions) + 1)
            concept_positions[topic_key] = concept_positions.get(topic_key, 0) + 1
            source_order = concept_positions[topic_key]
            topic = build_concepts._find_or_create_topic(
                db,
                chapter,
                rec["topic"],
                pre_post,
            )
            topic.source_order = topic_positions[topic_key]
            # T4-4: the id is stamped HERE, where ``source_order`` is already
            # assigned, and only when the row does not already carry one —
            # §6:523's "stable forever" is a property of storage (P-C1), so a
            # republication never re-keys a published topic.
            #
            # Through the ONE minter, never composed inline — with the
            # RELEASE position passed explicitly (Round 7): the minter's own
            # sibling-sort guess counts blank-id legacy rows and unstable
            # ``source_order`` ties, so [measured] it drifted from the id
            # the transient export stamped for the same topic, and every id
            # a reviewer saw in the workbook pointed at nothing persisted.
            # The export predicts with the same inputs, so the two agree.
            identity.machine_id_for_topic(
                topic, position=topic_positions[topic_key])
            # Round 7 resolution, replacing both the d7d2e2f chapter-wide
            # title merge and the audited-and-withdrawn slot-composition
            # lookup ([measured] the slot lookup overwrote a removed row's
            # neighbour on an ordinary shrinking republication and misfiled
            # every Master question after a topic prepend). Per record, in
            # order:
            #
            # 1. A record CARRYING ``machine_id`` resolves by the persisted
            #    column, chapter+lane wide — recorded identity wins, however
            #    every title reads (P-C1). No record carries one today; the
            #    workbook round-trip (B4) is the intended carrier.
            # 2. Otherwise, EXACTLY ONE unclaimed persisted concept under
            #    THIS resolved topic whose title matches through the one
            #    normaliser is the same row re-staged: update it in place,
            #    id kept. Scoping to the topic is what kills T11.3 — the
            #    chapter-wide match [measured] re-parented and overwrote a
            #    same-titled concept under a DIFFERENT topic, so re-parenting
            #    and the ``db.delete(tag)`` repair loop it needed are gone.
            # 3. Zero candidates, or two-plus (ambiguous): CREATE. An
            #    identity that cannot be proven unique is never guessed.
            #
            # Queried against the DATABASE, never the loaded relationship —
            # [measured] the stale in-session collection is exactly what hid
            # a same-titled row from the old lookup.
            carried_machine_id = str(rec.get("machine_id") or "").strip()
            existing = None
            if carried_machine_id:
                matches = (
                    db.query(models.Concept)
                    .join(models.Topic)
                    .filter(
                        models.Topic.chapter_id == chapter.id,
                        models.Topic.pre_post_learning == pre_post,
                        models.Concept.machine_id == carried_machine_id,
                    )
                    .all()
                )
                if len(matches) > 1:
                    raise ValueError(
                        f"published concept identity {carried_machine_id!r} "
                        "is held by more than one database row; refusing to "
                        "guess which row this release means"
                    )
                existing = matches[0] if matches else None
                if existing is not None and existing.id in claimed_ids:
                    raise ValueError(
                        f"staged row {position} carries the machine id "
                        f"{carried_machine_id!r} that an earlier row of this "
                        "release already resolved; two rows cannot share one "
                        "identity"
                    )
                if existing is not None and existing.topic_id != topic.id:
                    # Recorded, never silently re-parented: the carried
                    # identity says one topic, the staged row says another.
                    # The row's fields update in place under its persisted
                    # topic; the disagreement rides the receipt.
                    review_flags.append(
                        f"staged row {position} ({rec['concept_title']!r}) "
                        f"carries identity {carried_machine_id!r}, which "
                        "lives under a different topic than the row was "
                        "staged under; the persisted topic was kept and "
                        "nothing was re-parented — review which topic is "
                        "right"
                    )
            else:
                norm = bi.normalize_question_text(rec["concept_title"])
                candidates = [
                    row
                    for row in (
                        db.query(models.Concept)
                        .filter(models.Concept.topic_id == topic.id)
                        .all()
                    )
                    if row.id not in claimed_ids
                    and bi.normalize_question_text(row.concept_title) == norm
                ]
                existing = candidates[0] if len(candidates) == 1 else None
            if existing is None:
                concept = build_concepts._add_concept(
                    db,
                    topic,
                    rec,
                    source_book,
                    clean=False,
                )
                concept.source_order = source_order
                if carried_machine_id:
                    # A recorded identity restored verbatim, exactly as the
                    # workbook reader restores one (B4) — the row it names
                    # no longer exists, so the identity comes back with the
                    # content rather than being re-minted.
                    concept.machine_id = carried_machine_id
                else:
                    identity.machine_id_for_concept(
                        concept, position=source_order)
                if not str(concept.machine_id or "").strip():
                    # T9-1 B1 (Round 9): a row still blank after the mint
                    # is unaddressable — no question can ever name it as
                    # its home — and this act is where machine ids exist,
                    # so this is where the blank blocks the write.
                    raise ValueError(
                        f"staged row {position} "
                        f"({rec['concept_title']!r}) could not be minted a "
                        "machine identity, so no question could ever name "
                        "it; the publication is refused rather than "
                        "persisting an unaddressable row"
                    )
                # Identity is settled (carried or minted): the group
                # shells now take their SOP §6.1 names (Q16).
                build_concepts.stamp_group_shells(concept)
                db.flush()
                created_ids.append(concept.id)
                claimed_ids.add(concept.id)
                continue

            claimed_ids.add(existing.id)
            existing.concept_title = rec["concept_title"]
            existing.concept_display_name = rec["concept_title"]
            existing.parent_concept = rec.get("parent_concept", "")
            existing.concept_details = rec.get("concept_details", "")
            existing.keywords = rec.get("keywords", "")
            # OD3/T3.3b — the same two columns the create branch persists.
            existing.related_concepts = rec.get("related_concepts", "")
            existing.digicards = rec.get("digicards", "")
            existing.source_order = source_order
            # Mints only when the adopted row is a legacy blank (the mint
            # the export predicted); a row with an id keeps it verbatim.
            identity.machine_id_for_concept(existing, position=source_order)
            if not str(existing.machine_id or "").strip():
                # T9-1 B1 (Round 9): same refusal as the create branch —
                # an adopted row the mint could not identify stays out of
                # the published set rather than shipping unaddressable.
                raise ValueError(
                    f"staged row {position} ({rec['concept_title']!r}) "
                    "could not be minted a machine identity, so no "
                    "question could ever name it; the publication is "
                    "refused rather than persisting an unaddressable row"
                )
            existing.sources = bi.merge_sources(existing.sources, source_book)
            # An adopted row's shells follow the same SOP naming; legacy
            # blank-key shells gain their key from the settled identity.
            build_concepts.stamp_group_shells(existing)
            db.flush()
            merged_ids.append(existing.id)

        # T9-1 B1 (Round 9): a duplicate persisted machine id in this
        # chapter+lane is two rows one identity — every ``group_key`` and
        # ``question_label`` built off it collides, and
        # ``assessment_release_service`` then skips questions silently
        # (R4). Checked HERE, at the publication act, because the payload
        # never carries ids (S10) so ``structural_defects`` cannot see
        # them. Pure identity counting over persisted rows.
        minted_rows = (
            db.query(models.Concept)
            .join(models.Topic)
            .filter(
                models.Topic.chapter_id == chapter.id,
                models.Topic.pre_post_learning == pre_post,
                models.Concept.machine_id != "",
            )
            .all()
        )
        rows_by_machine_id: dict[str, list[int]] = {}
        for row in minted_rows:
            rows_by_machine_id.setdefault(
                str(row.machine_id).strip(), []).append(row.id)
        duplicated = sorted(
            machine_id
            for machine_id, ids in rows_by_machine_id.items()
            if len(ids) > 1
        )
        if duplicated:
            raise ValueError(
                "duplicate persisted machine identity in this chapter's "
                f"{pre_post} lane: {', '.join(duplicated)}; two rows "
                "sharing one identity is data corruption, so the "
                "publication is refused"
            )

        active_ids = sorted(set(created_ids + merged_ids))
        # Freshly inserted concepts are not in the chapter's already-loaded
        # relationship collections, so the summary counted 0 concepts and
        # skipped every fallback (reviewers saw "develops 0 concept(s)",
        # an empty topic description, and no duration). Reload from the
        # flushed state, and apply the metadata authored at release time.
        db.flush()
        db.expire_all()
        chapter = db.get(models.Chapter, chapter_id)
        if chapter is None:
            raise ValueError("the release target chapter no longer exists")
        build_concepts._sync_chapter_topic_summary(
            chapter,
            copy.deepcopy(payload.get("chapter_meta") or {}),
            active_concept_ids=set(active_ids),
            pre_post=pre_post,
        )
        summary.update({
            "database_uploaded": True,
            "database_uploaded_at": datetime.now(timezone.utc).isoformat(),
            "created_count": len(created_ids),
            "merged_count": len(merged_ids),
            "publication_status": "publishing",
        })
        if review_flags:
            summary["identity_review_flags"] = list(
                dict.fromkeys(
                    list(summary.get("identity_review_flags") or [])
                    + review_flags
                )
            )
        if resolved == LANE_PRE:
            # The Pre lane records its OWN published ids inside its own
            # payload. ``job.result_ids`` stays the Post lane's, because
            # every existing reader of that column (the Bulk Import
            # shortcut, the "already uploaded" response) means Output 01
            # by it, and a Pre publication must not silently redefine it.
            summary["concept_ids"] = copy.deepcopy(active_ids)
        payload["summary"] = summary
        inventory = copy.deepcopy(dict(job.question_inventory or {}))
        inventory[release_key] = copy.deepcopy(payload)
        job.question_inventory = inventory
        if resolved == LANE_POST:
            job.status = "generated"
            job.result_ids = active_ids
        job.deposit_scope_type = "chapter"
        job.deposit_scope_ids = [chapter_id]
        lane_label = "Pre-Learning " if resolved == LANE_PRE else ""
        job.detail = (
            f"Explicit database upload accepted {len(records)} released "
            f"{lane_label}row(s): "
            f"{len(created_ids)} created and {len(merged_ids)} updated. "
            f"The release audit retains {summary.get('issue_count', 0)} issue(s)."
        )

        try:
            publication = build_concepts._commit_and_publish_concept_workbook(
                db,
                config.BULK_IMPORT_OUTPUT,
                active_ids,
            )
            publication_status = "published"
        except workbook_sync.WorkbookPublicationPending as exc:
            # The helper raises only after the database commit. Its durable
            # outbox retains the staged workbook, so report the queued state
            # instead of inviting the user to upload the rows again.
            publication = {
                "written": len(records),
                "sources_updated": 0,
                "queued_reason": str(exc),
            }
            publication_status = "queued"

        db.refresh(job)
        durable_payload = release_payload(job, lane=resolved) or payload
        durable_summary = copy.deepcopy(durable_payload.get("summary") or summary)
        durable_summary["publication_status"] = publication_status
        durable_payload["summary"] = durable_summary
        durable_inventory = copy.deepcopy(dict(job.question_inventory or {}))
        durable_inventory[release_key] = durable_payload
        job.question_inventory = durable_inventory
        job.detail = (
            f"Explicit {lane_label}database upload completed: "
            f"{len(created_ids)} created, {len(merged_ids)} updated; "
            f"workbook publication is {publication_status}."
        )
        db.commit()
        db.refresh(job)
    except Exception:
        db.rollback()
        raise

    return {
        "job_id": job.id,
        "status": "generated",
        "database_uploaded": True,
        "created_concept_ids": created_ids,
        "updated_concept_ids": merged_ids,
        "issue_count": int(summary.get("issue_count") or 0),
        "publication_status": publication_status,
        "publication": publication,
    }
