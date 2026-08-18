"""The coverage ledger: "everything is covered well", made checkable.

The completion test from ``docs/build-concepts-manual-process.md``: at the
end of a run, every item the source contained is accounted for — placed
somewhere, or flagged with a reason — and every shipped concept carries its
learner analysis. This module computes that accounting as a pure function of
the durable job state (question inventory, Type/Case placement ledger,
released records, and — when available — the persisted container
projections and Phase 2.2 placement snapshot), so it can be rebuilt for any
finished or stopped run and shipped in the diagnostics export beside the
run report.

The ledger reports; it does not block. Mid-run gates are a separate concern
(and the process document says what should become of them). What this makes
impossible is *silent* incompleteness: a question, hub, figure or fragment
that reached no output row is named here — canonical figure blocks
included, with their placement / attachment / recorded-disposition state —
as is a concept shipped without its Achieving Mastery line or
Misconception/ Error Analysis section, and every dropped furniture line is
listed with what it said (R4), never reduced to a count.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from . import containers

LEDGER_VERSION = 3

_TYPE_CASE_LEDGER_KEY = "_type_case_qid_placement_ledger"
# Q1 allotment marker stamped by phase3/assemble.py and, for the Pre
# lane, by phase3/preanalyse.py (kept in lockstep with
# concept_validator.ANALYSIS_ALLOTMENTS_FIELD).
_ANALYSIS_ALLOTMENTS_FIELD = "_aegis_analysis_allotments"
# The Pre lane's own row markers (phase3/premap.py). A row on the
# prerequisite-capture grounding contract is a PRE-LEARNING row: it has
# no source question, no Type/Case and no figure by construction (the
# no-extraction steer), so charging it a POST obligation would report a
# debt it can never owe. Marker accounting, not a reading of content.
_PRE_GROUNDING_CONTRACT = "derived-from-prerequisite-capture"
_PRE_CONCEPT_ID_FIELD = "_pre_concept_id"
_PRE_PREREQUISITES_FIELD = "_aegis_pre_prerequisites"
_PRE_NEEDED_FOR_FIELD = "_aegis_needed_for"
# The pooled hub kind vocabulary lives in ``containers`` (single source);
# this name is a consumer alias, pinned by the lockstep test.
_HUB_KINDS = containers.HUB_INVENTORY_KINDS
_MASTERY_MARK = "Achieving Mastery:"
_ANALYSIS_MARK = "Misconception/ Error Analysis"
_CULMINATION_MARK = "culmination"


def _qid_present(qid: str, text: str) -> bool:
    """Whole-qid match: ``QINV-0002`` never matches inside ``QINV-0002.1``."""
    return bool(re.search(re.escape(qid) + r"(?![.\d])", text))


def _item_rows(
    items: list[dict[str, Any]],
    placed_qids: set[str],
    records_text: str,
    hub_placements: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid:
            continue
        kind = str(item.get("source_kind") or "").strip().lower()
        channel = "hub" if kind in _HUB_KINDS else "question"
        if channel == "hub":
            # Hub notes carry private qid markers in the released rows; the
            # Phase 2.2 placement snapshot is a second recorded authority.
            placed = _qid_present(qid, records_text) or (
                qid in hub_placements
            )
        else:
            placed = qid in placed_qids or _qid_present(qid, records_text)
        rows.append({
            "qid": qid,
            "channel": channel,
            "source_kind": kind,
            "status": "placed" if placed else "unaccounted",
            "flag": str(item.get("polish_flag") or ""),
            "parent_qid": str(item.get("parent_qid") or ""),
        })
    return rows


def _figure_rows(
    items: list[dict[str, Any]], records_text: str
) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for url in item.get("image_urls") or []:
            value = str(url or "").strip()
            if value and value not in seen:
                seen[value] = str(item.get("qid") or "")
    return [
        {
            "image_url": url,
            "first_qid": qid,
            "status": "placed" if url in records_text else "unaccounted",
        }
        for url, qid in seen.items()
    ]


def _figure_block_rows(
    figure_blocks: list[Mapping[str, Any]],
    records_text: str,
    figure_placements: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Account for EVERY canonical figure block — the blind spot closes.

    States: ``placed`` (the Phase 2.2 pass placed it on a concept — the
    stamped row marker or the recorded placement says so),
    ``disposition_recorded`` (the pass recorded a disposition instead —
    never silently dropped), ``attached_to_item`` (an inventory item
    already carries its image), ``no_image_evidence`` (the block carries
    no extractable image URL, so no embed is possible), ``unaccounted``.
    """
    rows: list[dict[str, Any]] = []
    for block in figure_blocks:
        if not isinstance(block, Mapping):
            continue
        block_id = str(block.get("block_id") or "")
        if not block_id:
            continue
        urls = [str(url) for url in block.get("image_urls") or [] if url]
        claimed = [
            str(qid) for qid in block.get("claimed_by_qids") or [] if qid
        ]
        verdict = figure_placements.get(block_id)
        if verdict == containers.FIGURE_DISPOSITION:
            status = "disposition_recorded"
        elif verdict or _qid_present(block_id, records_text):
            status = "placed"
        elif claimed:
            status = "attached_to_item"
        elif not urls:
            status = "no_image_evidence"
        else:
            status = "unaccounted"
        rows.append({
            "block_id": block_id,
            "status": status,
            "image_urls": urls,
            "caption": str(block.get("caption") or "")[:300],
            "claimed_by_qids": claimed,
            "disposition": (
                verdict
                if status == "disposition_recorded"
                else ""
            ),
        })
    return rows


def _learner_analysis_rows(
    records: list[Mapping[str, Any]],
    *,
    allotment_aware: bool = False,
    exclude_pre_learning: bool = False,
    mark_culmination: bool = True,
) -> list[dict[str, Any]]:
    """Rows missing their Mastery line or their OWED analysis section.

    ``allotment_aware`` is the Q1 contract: the analysis section is
    owed only by rows carrying the ``_aegis_analysis_allotments``
    marker (the chapter inventory allotted them an item); an unallotted
    row without a section is complete. Legacy jobs (no allotment ledger
    anywhere) keep the every-row accounting. Achieving Mastery stays
    owed by every row either way.

    ``exclude_pre_learning`` drops Pre rows from the POST accounting;
    they are accounted separately, always allotment-aware, because the
    Pre lane's only analysis mechanism is its own Q1 inventory and it
    has no legacy every-row contract to fall back to. Row indexes stay
    positional in the list handed in, so a skipped row does not
    renumber its neighbours.

    ``mark_culmination`` is the POST lane's marker accounting: that lane
    mints its culmination rows and their titles, so reading the title
    back is reading an identity this pipeline created. The PRE lane
    mints none (``premap.PRE_VALIDATOR_FLAGS`` records
    ``require_culmination=False``), so there the same read would be a
    keyword classifying model-authored free text — and would excuse a
    real Pre row from the Achieving Mastery every row owes. Pre rows are
    therefore reported with ``culmination`` false and nothing is exempt.
    """
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            continue
        if exclude_pre_learning and is_pre_learning_row(record):
            continue
        text = json.dumps(dict(record), ensure_ascii=False, default=str)
        title = str(
            record.get("concept_title") or record.get("title") or ""
        )
        analysis_owed = (
            bool(record.get(_ANALYSIS_ALLOTMENTS_FIELD))
            if allotment_aware
            else True
        )
        missing = [
            label for label, mark, owed in (
                ("achieving_mastery", _MASTERY_MARK, True),
                (
                    "misconception_error_analysis",
                    _ANALYSIS_MARK,
                    analysis_owed,
                ),
            )
            if owed and mark not in text
        ]
        if not missing:
            continue
        rows.append({
            "row_index": index,
            "concept_title": title[:160],
            "missing": missing,
            "culmination": (
                mark_culmination and _CULMINATION_MARK in title.casefold()
            ),
        })
    return rows


def is_pre_learning_row(record: Mapping[str, Any]) -> bool:
    """Whether a released row belongs to the PRE-LEARNING lane.

    Pure marker accounting over identities this pipeline itself mints
    (``phase3/premap.py``): the prerequisite-capture grounding contract,
    or the row-private pre-concept id. It reads nothing out of the row's
    content, so it can never classify a row by what it says.
    """
    if not isinstance(record, Mapping):
        return False
    contract = str(record.get("_source_grounding_contract") or "").strip()
    if contract == _PRE_GROUNDING_CONTRACT:
        return True
    return bool(str(record.get(_PRE_CONCEPT_ID_FIELD) or "").strip())


def _pre_learning_rows(
    records: list[Mapping[str, Any]],
    pre_map: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Every Pre row this ledger can see: released, or from the snapshot.

    A Pre row may reach the ledger two ways — inside ``records`` (a Pre
    release payload) or only through the run's recorded Pre map snapshot
    (a Post release, where the Pre lane rode its own key). Both are
    accounted; a row present in both is counted once, by its
    ``_pre_concept_id``.
    """
    rows: list[Mapping[str, Any]] = [
        row for row in records if is_pre_learning_row(row)
    ]
    seen = {
        str(row.get(_PRE_CONCEPT_ID_FIELD) or "")
        for row in rows
        if str(row.get(_PRE_CONCEPT_ID_FIELD) or "")
    }
    for row in pre_map.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        pre_id = str(row.get(_PRE_CONCEPT_ID_FIELD) or "")
        if pre_id and pre_id in seen:
            continue
        rows.append(row)
    return rows


def _pre_prerequisite_rows(
    pre_rows: list[Mapping[str, Any]],
    prelearn_snapshot: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """R4 over the capture: every merged prerequisite mapped exactly once.

    ``carried`` is what the Pre map's rows actually claim; the merged
    capture (``source.phase3-prelearn-capture.json``) is the authority
    on what there was to carry. A prerequisite named by no row is a
    learner's prerequisite lost; one named twice is a double-count. Both
    are named here, never reduced to a count.

    When the Pre lane carried prerequisites but that authority is absent
    — the snapshot was never written (``runner._snapshot_prelearn``
    writes best-effort), or the artifact directory was partly cleaned —
    nothing here can confirm the claims, and an absent authority is not
    a clean bill of health. Each claim is reported
    ``capture_unavailable`` rather than ``mapped``, so the ledger says
    "cannot account" instead of silently passing. A run with no Pre lane
    claims nothing and still gets an empty accounting, not a false debt.
    """
    carried: dict[str, list[str]] = {}
    for row in pre_rows:
        pre_id = str(row.get(_PRE_CONCEPT_ID_FIELD) or "")
        for entry in row.get(_PRE_PREREQUISITES_FIELD) or []:
            if not isinstance(entry, Mapping):
                continue
            ref = str(entry.get("prerequisite_id") or "")
            if ref:
                carried.setdefault(ref, []).append(pre_id)
    snapshot = (
        prelearn_snapshot if isinstance(prelearn_snapshot, Mapping) else None
    )
    # The authority is present when the capture snapshot is, even if it
    # captured nothing: an empty capture beside a claiming row is an
    # ``unknown_prerequisite``, a MISSING capture is unverifiable.
    authority = (
        snapshot is not None
        and snapshot.get("prerequisites") is not None
    )
    captured: list[dict[str, Any]] = [
        dict(row)
        for row in (snapshot or {}).get("prerequisites") or []
        if isinstance(row, Mapping)
        and str(row.get("prerequisite_id") or "").strip()
    ]
    known = {str(row.get("prerequisite_id") or "") for row in captured}
    rows: list[dict[str, Any]] = []
    for ref in sorted(known | set(carried)):
        holders = carried.get(ref, [])
        if not holders:
            status = "unmapped"
        elif len(holders) > 1:
            status = "mapped_more_than_once"
        elif not authority:
            status = "capture_unavailable"
        elif ref not in known:
            status = "unknown_prerequisite"
        else:
            status = "mapped"
        text = ""
        for row in captured:
            if str(row.get("prerequisite_id") or "") == ref:
                text = str(row.get("text") or "")[:300]
                break
        rows.append({
            "prerequisite_id": ref,
            "status": status,
            "text": text,
            "pre_concept_ids": holders,
        })
    return rows


def _pre_needed_for_rows(
    pre_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Every needed-for link, resolved or not.

    A link resolves when it names a Post concept id AND carries that
    concept's title (``premap.build`` fills the title only from the ids
    the run actually has). A pre-concept linked to nothing is NOT an
    accounting gap — necessity is the critic's advisory dimension (Q10)
    — so it is reported as ``no_links`` and never counts as incomplete.
    """
    rows: list[dict[str, Any]] = []
    for row in pre_rows:
        pre_id = str(row.get(_PRE_CONCEPT_ID_FIELD) or "")
        links = [
            link for link in row.get(_PRE_NEEDED_FOR_FIELD) or []
            if isinstance(link, Mapping)
        ]
        if not links:
            rows.append({
                "pre_concept_id": pre_id,
                "post_concept_id": "",
                "post_concept_title": "",
                "status": "no_links",
            })
            continue
        for link in links:
            concept_id = str(link.get("post_concept_id") or "")
            title = str(link.get("post_concept_title") or "")
            rows.append({
                "pre_concept_id": pre_id,
                "post_concept_id": concept_id,
                "post_concept_title": title[:160],
                "status": "resolved" if concept_id and title else "unresolved",
            })
    return rows


def _pre_generated_question_counts(
    pre_rows: list[Mapping[str, Any]],
    pre_questions: Mapping[str, Any],
) -> dict[str, Any]:
    """What Output 04 actually authored, per pre-concept (R4).

    Counting only, and deliberately no expectation: Q4's adaptive target
    is a target and never a quota, so a pre-concept with fewer authored
    questions than its own plan asked for is REPORTED, never charged as
    incompleteness — the variance carries the authored rationale the
    generation pass already recorded. Nothing here derives a number from
    volume; every number is read back from what the model decided.
    """

    authored = pre_questions.get("questions")
    authored = authored if isinstance(authored, Mapping) else {}
    plans = pre_questions.get("plans")
    plans = plans if isinstance(plans, Mapping) else {}
    blocked = pre_questions.get("blocked")
    blocked = blocked if isinstance(blocked, Mapping) else {}
    per_concept: list[dict[str, Any]] = []
    for row in pre_rows:
        pre_id = str(row.get(_PRE_CONCEPT_ID_FIELD) or "")
        plan = plans.get(pre_id)
        plan = plan if isinstance(plan, Mapping) else {}
        planned = plan.get("total")
        per_concept.append({
            "pre_concept_id": pre_id,
            "concept_title": str(row.get("concept_title") or "")[:160],
            "planned": int(planned) if isinstance(planned, int) else None,
            "authored": len([
                item for item in authored.get(pre_id) or []
                if isinstance(item, Mapping)
            ]),
            "blocked": str(blocked.get(pre_id) or ""),
        })
    return {
        "total": sum(row["authored"] for row in per_concept),
        "pre_concepts_with_questions": len([
            row for row in per_concept if row["authored"]
        ]),
        "pre_concepts_blocked": len([
            row for row in per_concept if row["blocked"]
        ]),
        "refused": str(pre_questions.get("refused") or ""),
        "per_concept": per_concept,
    }


def _pre_release_counts(
    pre_release: Mapping[str, Any],
) -> dict[str, Any]:
    """Whether Outputs 03/04 were STAGED, and in which state.

    Distinct from what the run built: a map can exist while the release
    that carries it does not, and the reviewer needs to see the
    difference without opening any artifact JSON.
    """

    if not pre_release:
        return {"staged": False}
    from . import build_concepts_release as release

    summary = pre_release.get("summary") or {}
    return {
        "staged": True,
        "release_state": release.release_state(pre_release),
        "structural_defects": release.structural_defects(pre_release),
        "rows": int(summary.get("row_count") or 0),
        "rows_with_issues": int(summary.get("affected_row_count") or 0),
        "issues": int(summary.get("issue_count") or 0),
        "generated_questions": len(
            pre_release.get("generated_questions") or []
        ),
        "database_uploaded": bool(summary.get("database_uploaded")),
    }


def _analysis_item_rows(
    analysis_snapshot: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Account every Phase 2.4 inventory item (R4: allotted exactly once).

    The allotments map carries at most one concept per item by
    construction, so the checkable facts are: every LA-id received an
    allotment, and no allotment names an item outside the inventory.
    """
    snapshot = dict(analysis_snapshot or {})
    allotments = {
        str(item_id): str(concept_id)
        for item_id, concept_id in (snapshot.get("allotments") or {}).items()
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in snapshot.get("inventory") or []:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("item_id") or "")
        if not item_id:
            continue
        seen.add(item_id)
        concept_id = allotments.get(item_id, "")
        rows.append({
            "item_id": item_id,
            "kind": str(item.get("kind") or ""),
            "text": str(item.get("text") or "")[:300],
            "status": "allotted" if concept_id else "unaccounted",
            "concept_id": concept_id,
        })
    for item_id in sorted(set(allotments) - seen):
        rows.append({
            "item_id": item_id,
            "kind": "",
            "text": "",
            "status": "unknown_item",
            "concept_id": allotments[item_id],
        })
    return rows


def build_coverage_ledger(
    *,
    question_inventory: Mapping[str, Any] | None,
    records: list[Mapping[str, Any]] | None,
    chapter_reading: Mapping[str, Any] | None = None,
    container_projections: Mapping[str, Any] | None = None,
    place_snapshot: Mapping[str, Any] | None = None,
    analysis_snapshot: Mapping[str, Any] | None = None,
    pre_map_snapshot: Mapping[str, Any] | None = None,
    prelearn_snapshot: Mapping[str, Any] | None = None,
    pre_questions_snapshot: Mapping[str, Any] | None = None,
    pre_release_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Account for every source item and every shipped concept.

    ``container_projections`` is the persisted ``source.containers.json``
    (canonical figure-block evidence + verbatim furniture, both lanes);
    ``place_snapshot`` is the Phase 2.2 placement pass's recorded output
    (``source.phase3-place.json``); ``analysis_snapshot`` the Phase
    2.4/4.3 chapter analysis inventory
    (``source.phase3-analysis.json``, Q1); ``pre_map_snapshot`` the
    Phase 03 Pre-Learning map (``source.phase3-prelearn-map.json``) and
    ``prelearn_snapshot`` the merged prerequisite capture it was built
    from (``source.phase3-prelearn-capture.json``);
    ``pre_questions_snapshot`` the Q4 coverage plan and the GENERATED
    questions authored to it
    (``source.phase3-prelearn-questions.json``); and
    ``pre_release_payload`` the STAGED Output-03 release, so the ledger
    can say what actually shipped rather than only what the run built.
    All are optional — a
    legacy job without them keeps the item/URL accounting (and the
    legacy every-row analysis expectation), and a run with no Pre lane
    gets an empty Pre accounting rather than a false debt.

    **Lane awareness (§6.6).** The two lanes owe different things and
    this ledger charges each only its own. A PRE row has no source
    question, no Type/Case and no figure by construction — the
    no-extraction steer guarantees it — so it is never counted into the
    question/hub/figure accounting or into the POST analysis
    expectation. What the Pre lane DOES owe is accounted in full: every
    merged prerequisite carried into the map exactly once, every Pre
    inventory item allotted exactly once, every needed-for link
    resolved, and Achieving Mastery on every Pre row. All of it is
    counting identities; none of it judges whether the Pre map is good.
    """
    inventory = dict(question_inventory or {})
    rows = [row for row in (records or []) if isinstance(row, Mapping)]
    items = [
        item for item in inventory.get("items") or []
        if isinstance(item, dict)
    ]
    pre_map = dict(pre_map_snapshot or {})
    pre_questions = dict(pre_questions_snapshot or {})
    pre_release = dict(pre_release_payload or {})
    # A Pre row can never carry a source qid, a Type/Case route or a
    # figure url (premap's guard fails closed on the first, and the lane
    # mints neither of the others), so it can only ever ADD false
    # "placed" evidence to the POST scans. Keeping it out of
    # ``records_text`` makes that structural rather than incidental.
    post_rows = [row for row in rows if not is_pre_learning_row(row)]
    records_text = json.dumps(post_rows, ensure_ascii=False, default=str)

    ledger = inventory.get(_TYPE_CASE_LEDGER_KEY)
    placements = (
        ledger.get("placements") if isinstance(ledger, Mapping) else None
    )
    # An unplaced_pending_certification row is a flagged gap the ownership
    # stage recorded on purpose — it must never count as placed.
    placed_qids = {
        str(qid).strip()
        for qid, contract in (placements or {}).items()
        if str(qid or "").strip()
        and not (
            isinstance(contract, Mapping)
            and str(contract.get("basis") or "")
            == "unplaced_pending_certification"
        )
    }

    place = dict(place_snapshot or {})
    hub_placements = dict(place.get("hub_placements") or {})
    figure_placements = dict(place.get("figure_placements") or {})
    projections = dict(container_projections or {})
    figure_blocks = [
        block
        for block in projections.get("figure_blocks") or []
        if isinstance(block, Mapping)
    ]

    item_rows = _item_rows(items, placed_qids, records_text, hub_placements)
    figure_rows = _figure_rows(items, records_text)
    figure_block_rows = _figure_block_rows(
        figure_blocks, records_text, figure_placements
    )
    # Q1: with an allotment ledger (the snapshot, or markers riding the
    # rows themselves), the analysis section is owed only by allotted
    # rows; a legacy job keeps the every-row expectation.
    allotment_aware = analysis_snapshot is not None or any(
        row.get(_ANALYSIS_ALLOTMENTS_FIELD) for row in post_rows
    )
    analysis_rows = _learner_analysis_rows(
        rows, allotment_aware=allotment_aware, exclude_pre_learning=True
    )
    analysis_item_rows = _analysis_item_rows(analysis_snapshot)

    # ---- the PRE lane's own obligations (§6.6) -----------------------
    pre_rows = _pre_learning_rows(rows, pre_map)
    # Always allotment-aware: the Pre lane's Q1 inventory is its ONLY
    # analysis mechanism (the retired every-concept writer went with the
    # lane), so there is no legacy every-row contract to fall back to and an
    # unallotted Pre row owing nothing is complete, not incomplete.
    pre_analysis_rows = _learner_analysis_rows(
        pre_rows, allotment_aware=True, mark_culmination=False
    )
    pre_analysis_item_rows = _analysis_item_rows(pre_map.get("analysis"))
    pre_prerequisite_rows = _pre_prerequisite_rows(
        pre_rows, prelearn_snapshot
    )
    pre_needed_for_rows = _pre_needed_for_rows(pre_rows)

    furniture = projections.get("furniture")
    furniture = dict(furniture) if isinstance(furniture, Mapping) else {}
    dropped_furniture = {
        "chapter_reading": [
            str(line)
            for line in (
                furniture.get("chapter_reading")
                or (chapter_reading or {}).get("dropped_furniture")
                or []
            )
            if str(line or "").strip()
        ],
        "acsd": [
            str(line)
            for line in furniture.get("acsd") or []
            if str(line or "").strip()
        ],
    }

    def channel_counts(channel: str) -> dict[str, int]:
        subset = [row for row in item_rows if row["channel"] == channel]
        placed = sum(1 for row in subset if row["status"] == "placed")
        return {"total": len(subset), "placed": placed,
                "unaccounted": len(subset) - placed}

    figures_placed = sum(
        1 for row in figure_rows if row["status"] == "placed"
    )
    block_status_counts: dict[str, int] = {}
    for row in figure_block_rows:
        block_status_counts[row["status"]] = (
            block_status_counts.get(row["status"], 0) + 1
        )
    normal_missing = [
        row for row in analysis_rows if not row["culmination"]
    ]
    analysis_items_unaccounted = [
        row for row in analysis_item_rows if row["status"] != "allotted"
    ]
    # No culmination exemption in the PRE lane: it mints no culmination
    # row, so every Pre row missing an obligation it actually owes —
    # Achieving Mastery always, the analysis section where its own
    # inventory allotted an item — is incompleteness, never excused by
    # what the model happened to call the concept.
    pre_normal_missing = list(pre_analysis_rows)
    pre_items_unaccounted = [
        row for row in pre_analysis_item_rows if row["status"] != "allotted"
    ]
    pre_prerequisites_unaccounted = [
        row for row in pre_prerequisite_rows if row["status"] != "mapped"
    ]
    pre_links_unresolved = [
        row for row in pre_needed_for_rows if row["status"] == "unresolved"
    ]
    pre_learning = {
        "rows": len(pre_rows),
        "topics": len([
            topic for topic in pre_map.get("topics") or []
            if isinstance(topic, Mapping)
        ]),
        "refused": str(pre_map.get("refused") or ""),
        "prerequisites": {
            "total": len(pre_prerequisite_rows),
            "mapped": len(pre_prerequisite_rows)
            - len(pre_prerequisites_unaccounted),
            "unaccounted": len(pre_prerequisites_unaccounted),
        },
        # Q1 in the Pre lane: every item allotted to exactly one
        # pre-concept, and NOT every pre-concept receives one — the
        # count of rows carrying a section is reported, never owed.
        "analysis_items": {
            "total": len(pre_analysis_item_rows),
            "allotted": len(pre_analysis_item_rows)
            - len(pre_items_unaccounted),
            "unaccounted": len(pre_items_unaccounted),
        },
        "rows_with_analysis_section": sum(
            1 for row in pre_rows if row.get(_ANALYSIS_ALLOTMENTS_FIELD)
        ),
        "needed_for_links": {
            "total": len([
                row for row in pre_needed_for_rows
                if row["status"] != "no_links"
            ]),
            "resolved": len([
                row for row in pre_needed_for_rows
                if row["status"] == "resolved"
            ]),
            "unresolved": len(pre_links_unresolved),
            # Advisory, never incomplete (Q10): necessity is a critic
            # dimension, so a pre-concept nothing requires still ships.
            "pre_concepts_without_links": len([
                row for row in pre_needed_for_rows
                if row["status"] == "no_links"
            ]),
        },
        "rows_missing_learner_analysis": len(pre_analysis_rows),
        # WHAT SHIPPED (Outputs 03/04), so a reviewer never has to open
        # artifact JSON to see it. Counting and identities only: how many
        # GENERATED questions each pre-concept received against the plan
        # it authored, which pre-concepts were blocked and why, and
        # whether the Pre release was staged at all. Nothing here judges
        # a question; the flags it counts are already recorded verdicts.
        "generated_questions": _pre_generated_question_counts(
            pre_rows, pre_questions
        ),
        "released": _pre_release_counts(pre_release),
    }
    summary = {
        "questions": channel_counts("question"),
        "hubs": channel_counts("hub"),
        "figures": {
            "total": len(figure_rows),
            "placed": figures_placed,
            "unaccounted": len(figure_rows) - figures_placed,
        },
        "figure_blocks": {
            "total": len(figure_block_rows),
            **block_status_counts,
        },
        "released_rows": len(rows),
        "rows_missing_learner_analysis": len(analysis_rows),
        "normal_rows_missing_learner_analysis": len(normal_missing),
        # Q1 (R4): every LA-item accounted — allotted to exactly one
        # concept; an unallotted item is visible incompleteness.
        "learner_analysis_items": {
            "total": len(analysis_item_rows),
            "allotted": len(analysis_item_rows)
            - len(analysis_items_unaccounted),
            "unaccounted": len(analysis_items_unaccounted),
        },
        "flagged_for_review": sum(1 for row in item_rows if row["flag"]),
        "pre_learning": pre_learning,
    }
    complete = (
        summary["questions"]["unaccounted"] == 0
        and summary["hubs"]["unaccounted"] == 0
        and summary["figures"]["unaccounted"] == 0
        and block_status_counts.get("unaccounted", 0) == 0
        and not normal_missing
        and not analysis_items_unaccounted
        # The Pre lane's own obligations, and only its own. A run with
        # no Pre lane has none of these, so this reads exactly as it did
        # before the lane existed.
        and not pre_prerequisites_unaccounted
        and not pre_items_unaccounted
        and not pre_links_unresolved
        and not pre_normal_missing
    )
    reading = dict(chapter_reading or {})
    return {
        "version": LEDGER_VERSION,
        "complete": complete,
        "summary": summary,
        "items": item_rows,
        "figures": figure_rows,
        "figure_blocks": figure_block_rows,
        # R4: the lines themselves, never a bare count. Job-state caps
        # (400 lines / 300 chars) apply upstream; the persisted container
        # projections carry the uncapped lines and win when present.
        "dropped_furniture": dropped_furniture,
        "rows_missing_learner_analysis": analysis_rows,
        "learner_analysis_items": analysis_item_rows,
        # The PRE lane's per-identity accounting (§6.6), never a bare
        # count: every prerequisite with the pre-concept(s) that claim
        # it, every Pre inventory item with its destination, every
        # needed-for link with what it resolved to.
        "pre_learning": {
            "prerequisites": pre_prerequisite_rows,
            "analysis_items": pre_analysis_item_rows,
            "needed_for": pre_needed_for_rows,
            "rows_missing_learner_analysis": pre_analysis_rows,
        },
        "chapter_reading": {
            "provenance": dict(reading.get("provenance") or {}),
            "census_rows": reading.get("census_rows"),
            "dropped_furniture_lines": len(
                reading.get("dropped_furniture") or []
            ),
        } if reading else {},
    }


def _render_pre_learning(ledger: Mapping[str, Any]) -> list[str]:
    """The PRE lane's accounting, in the reviewer's first file.

    Silent when the run had no Pre lane, and ONLY then. The gate is
    whether the Pre accounting has anything to say, never whether rows
    reached the ledger: a run whose map snapshot is missing or empty
    while the capture holds prerequisites is exactly the state in which
    prerequisites are lost, and gating on ``rows`` would make the
    reviewer's first file say ``INCOMPLETE`` with no reason and name
    nothing (R4: silent incompleteness is impossible). Counts and
    identities only — the ledger says what the Pre lane produced and
    what it owes, never whether the map is any good.
    """
    summary = (ledger.get("summary") or {}).get("pre_learning") or {}
    detail = ledger.get("pre_learning") or {}
    accounted = any(
        detail.get(key)
        for key in (
            "prerequisites",
            "analysis_items",
            "needed_for",
            "rows_missing_learner_analysis",
        )
    )
    if (
        not summary.get("rows")
        and not summary.get("refused")
        and not accounted
    ):
        return []
    prerequisites = summary.get("prerequisites") or {}
    analysis_items = summary.get("analysis_items") or {}
    links = summary.get("needed_for_links") or {}
    lines = ["", "  PRE-LEARNING (Phase 03)"]
    if summary.get("refused"):
        lines.append(
            "    REFUSED and not shipped: " + summary["refused"][:300]
        )
    elif not summary.get("rows"):
        lines.append(
            "    no Pre-Learning map is recorded for this run, so nothing "
            "below could reach a pre-concept"
        )
    lines.append(
        f"    pre-concepts: {summary.get('rows', 0)} in "
        f"{summary.get('topics', 0)} pre-topic(s)"
    )
    lines.append(
        f"    prerequisites: {prerequisites.get('mapped', 0)}/"
        f"{prerequisites.get('total', 0)} mapped exactly once"
        + (
            f", {prerequisites['unaccounted']} unaccounted"
            if prerequisites.get("unaccounted") else ""
        )
    )
    for row in detail.get("prerequisites") or []:
        if row.get("status") == "mapped":
            continue
        lines.append(
            f"      {row.get('status')} {row.get('prerequisite_id')}: "
            f"{str(row.get('text'))[:120]!r}"
            + (
                " claimed by " + ", ".join(row.get("pre_concept_ids") or [])
                if row.get("pre_concept_ids") else ""
            )
        )
    lines.append(
        "    analysis inventory items: "
        f"{analysis_items.get('allotted', 0)}/"
        f"{analysis_items.get('total', 0)} allotted, on "
        f"{summary.get('rows_with_analysis_section', 0)} of "
        f"{summary.get('rows', 0)} pre-concept(s) — not every "
        "pre-concept receives one (Q1)"
        + (
            f", {analysis_items['unaccounted']} unaccounted"
            if analysis_items.get("unaccounted") else ""
        )
    )
    for row in detail.get("analysis_items") or []:
        if row.get("status") != "allotted":
            lines.append(
                f"      unaccounted analysis item {row.get('item_id')} "
                f"[{row.get('kind')}]: {str(row.get('text'))[:120]!r}"
            )
    lines.append(
        f"    needed-for links: {links.get('resolved', 0)}/"
        f"{links.get('total', 0)} resolved"
        + (
            f", {links['unresolved']} unresolved"
            if links.get("unresolved") else ""
        )
        + (
            f"; {links['pre_concepts_without_links']} pre-concept(s) "
            "linked to nothing (advisory, Q10 — they ship)"
            if links.get("pre_concepts_without_links") else ""
        )
    )
    generated = summary.get("generated_questions") or {}
    released = summary.get("released") or {}
    if generated.get("refused"):
        lines.append(
            "    generated questions REFUSED and not shipped: "
            + str(generated["refused"])[:300]
        )
    else:
        lines.append(
            f"    generated questions: {generated.get('total', 0)} authored "
            f"on {generated.get('pre_concepts_with_questions', 0)} of "
            f"{summary.get('rows', 0)} pre-concept(s) — GENERATED for the "
            "prerequisite, never lifted from the source"
            + (
                f"; {generated['pre_concepts_blocked']} pre-concept(s) "
                "blocked (each named below)"
                if generated.get("pre_concepts_blocked") else ""
            )
        )
    for row in generated.get("per_concept") or []:
        if row.get("blocked"):
            lines.append(
                f"      blocked {row.get('pre_concept_id')} "
                f"{str(row.get('concept_title'))!r}: "
                f"{str(row.get('blocked'))[:160]}"
            )
        elif (
            isinstance(row.get("planned"), int)
            and row["planned"] != row.get("authored")
        ):
            # Q4's target is a target, never a quota: variance is reported
            # with the plan beside it and is not incompleteness.
            lines.append(
                f"      {row.get('pre_concept_id')} authored "
                f"{row.get('authored')} of the {row['planned']} its own "
                "coverage plan asked for (an adaptive target, not a quota)"
            )
    if released.get("staged"):
        lines.append(
            f"    staged Pre release: {released.get('release_state')} — "
            f"{released.get('rows', 0)} row(s), "
            f"{released.get('generated_questions', 0)} generated question(s), "
            f"{released.get('issues', 0)} issue(s); "
            f"database_uploaded={released.get('database_uploaded')}"
        )
        for defect in released.get("structural_defects") or []:
            lines.append(
                f"      database upload BLOCKED: {str(defect)[:200]}"
            )
    else:
        lines.append(
            "    no Pre release is staged for this run, so Outputs 03/04 "
            "are not downloadable from it"
        )
    missing = detail.get("rows_missing_learner_analysis") or []
    if missing:
        lines.append(
            f"    pre-concepts missing an owed section: {len(missing)}"
        )
        for row in list(missing)[:10]:
            lines.append(
                f"      row {row.get('row_index')} "
                f"{str(row.get('concept_title'))!r} missing "
                f"{', '.join(row.get('missing') or [])}"
            )
    else:
        lines.append(
            "    every pre-concept carries its Achieving Mastery line, and "
            "its analysis section where one was allotted"
        )
    return lines


def render_coverage(ledger: Mapping[str, Any]) -> str:
    """Human-readable COVERAGE section appended to RUN_REPORT.txt."""
    summary = ledger.get("summary") or {}
    lines = ["", "COVERAGE"]
    lines.append(
        "  complete: everything accounted for"
        if ledger.get("complete")
        else "  INCOMPLETE: some source items reached no output row"
    )
    for label, key in (
        ("questions", "questions"), ("hubs", "hubs"), ("figures", "figures"),
    ):
        channel = summary.get(key) or {}
        lines.append(
            f"  {label}: {channel.get('placed', 0)}/{channel.get('total', 0)}"
            " placed"
            + (
                f", {channel['unaccounted']} unaccounted"
                if channel.get("unaccounted") else ""
            )
        )
    blocks = summary.get("figure_blocks") or {}
    if blocks.get("total"):
        lines.append(
            "  figure blocks: "
            + ", ".join(
                f"{blocks.get(status, 0)} {status}"
                for status in (
                    "placed",
                    "attached_to_item",
                    "disposition_recorded",
                    "no_image_evidence",
                    "unaccounted",
                )
                if blocks.get(status)
            )
            + f" of {blocks['total']}"
        )
    lines.append(
        f"  flagged for review: {summary.get('flagged_for_review', 0)}"
    )
    missing = ledger.get("rows_missing_learner_analysis") or []
    if missing:
        lines.append(
            f"  rows missing learner analysis: {len(missing)}"
        )
        for row in list(missing)[:10]:
            suffix = " (culmination)" if row.get("culmination") else ""
            lines.append(
                f"    row {row.get('row_index')} "
                f"{str(row.get('concept_title'))!r} missing "
                f"{', '.join(row.get('missing') or [])}{suffix}"
            )
    else:
        lines.append(
            "  learner analysis: every owed section present "
            "(mastery on every row; analysis on every allotted row)"
        )
    analysis_items = summary.get("learner_analysis_items") or {}
    if analysis_items.get("total"):
        lines.append(
            "  analysis inventory items: "
            f"{analysis_items.get('allotted', 0)}/"
            f"{analysis_items.get('total', 0)} allotted"
            + (
                f", {analysis_items['unaccounted']} unaccounted"
                if analysis_items.get("unaccounted") else ""
            )
        )
        for row in list(ledger.get("learner_analysis_items") or [])[:20]:
            if row.get("status") != "allotted":
                lines.append(
                    f"    unaccounted analysis item {row.get('item_id')} "
                    f"[{row.get('kind')}]: {str(row.get('text'))[:120]!r}"
                )
    unaccounted = [
        row for row in ledger.get("items") or []
        if row.get("status") != "placed"
    ]
    for row in unaccounted[:20]:
        lines.append(
            f"    unaccounted {row.get('channel')}: {row.get('qid')}"
            + (f" [{row['flag']}]" if row.get("flag") else "")
        )
    for row in list(ledger.get("figure_blocks") or [])[:40]:
        if row.get("status") in {"unaccounted", "disposition_recorded"}:
            lines.append(
                f"    figure block {row.get('block_id')}: {row.get('status')}"
                + (
                    f" ({row['disposition']})"
                    if row.get("disposition") else ""
                )
            )
    lines.extend(_render_pre_learning(ledger))
    furniture = ledger.get("dropped_furniture") or {}
    furniture_lines = [
        (lane, line)
        for lane in ("chapter_reading", "acsd")
        for line in furniture.get(lane) or []
    ]
    if furniture_lines:
        lines.append(
            f"  dropped furniture ({len(furniture_lines)} line(s), verbatim):"
        )
        for lane, line in furniture_lines[:40]:
            lines.append(f"    [{lane}] {line}")
        if len(furniture_lines) > 40:
            lines.append(
                f"    … {len(furniture_lines) - 40} more line(s) in "
                "context/coverage_ledger.json"
            )
    return "\n".join(lines) + "\n"
