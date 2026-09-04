"""The reconstructed pre-upload QC audit (spec-step8 T10, S11).

One pass over the staged payload (and, when supplied, the rendered
artifacts and the coverage ledger), returning ``(issues, blocking)``.
``issues`` merge into the release's existing ledger; ``blocking`` rides
``payload["qc_blocking_defects"]`` (its OWN key with its own honest
reader in ``structural_defects`` — Round 9 replaced the original
``snapshot_defects`` routing, whose reader stamped every finding with a
false "input snapshot could not be read" provenance) — so a blocking
finding makes the release *Diagnostic* and refuses the DATABASE WRITE
while every download ships (T9, Rule E).

The checklist this implements is reconstructed in
``docs/release-qc-checklist.md`` with per-item provenance. The polarity is
T9's: identity corruption blocks, meaning flags, and the audit itself
NEVER RAISES and is never called from inside an artifact builder — a
failed audit is a named issue, not a lost download.

Doctrine (CLAUDE.md Rule 1): everything here counts identities, compares
recorded values, or transcribes recorded flags. Nothing judges what the
source means.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import coverage_ledger

# The one blocking family this audit OWNS (T9 B3, checklist item 22):
# an inventory item that ends the run neither Placed nor Flagged. R4 says
# every question ends Placed OR Flagged; an unaccounted-and-unflagged item
# is the silent loss the doctrine calls unrecoverable. An unaccounted item
# that CARRIES a flag satisfies R4 — it is reported, never blocking.
COVERAGE_UNACCOUNTED = "coverage_unaccounted"
# The R4 closure for the Pre lane's needed-for links: the resolver records
# what it could not resolve on the row, and [measured] nothing read that
# record anywhere — a producer with no consumer, the exact shape S8/S9
# closed for ``unplaced`` and ``staged_row_defects``. Advisory, never
# blocking: an unresolved link is a review question, not corruption.
PRE_RELATED_UNRESOLVED = "pre_related_concept_unresolved"
# T10-2's advisory validation findings ride the rows as review flags; on
# the Post lane no transcription made them a release issue, so a reviewer
# reading only the Issues sheet never saw them. Transcribed here, one
# issue per (row, flag).
FLAGGED_VALIDATION_FINDING = "flagged_validation_finding"
# The audit itself failed. Named instead of raised: T10's contract is that
# the audit never costs a run or a download, and a swallowed exception is
# the anti-pattern T9-3 bans — so the failure is a visible issue.
AUDIT_UNAVAILABLE = "release_qc_unavailable"
# Master Governing Contract v2.0 §32.1 (NUM-001): the chapter duration is
# frozen once per chapter from an accepted registry row, explicit source
# periods, or the explicit upload variable — never estimated. A release
# whose chapter has none is blocked from the database write (every
# download still ships) until one is supplied.
CHAPTER_DURATION_UNREGISTERED = "chapter_duration_unregistered"
PUBLICATION_UNKNOWN = "publication_unknown"

_VALIDATION_FLAG_PREFIX = "validation: "


def _issue(**kwargs: Any) -> dict[str, Any]:
    from . import build_concepts_release as rel

    return rel._issue(**kwargs)


def _records(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in payload.get("records") or []
        if isinstance(row, Mapping)
    ]


def _is_pre_lane(payload: Mapping[str, Any]) -> bool:
    lane = str(payload.get("release_lane") or "").strip().lower()
    if lane:
        return lane == "pre"
    return str(payload.get("learning_kind") or "").strip().lower() == "pre"


def _coverage_findings(
    payload: Mapping[str, Any],
    ledger: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Checklist item 22 (T9 B3): the coverage ledger stops being inert.

    [measured] ``build_coverage_ledger`` had exactly one production caller
    — the diagnostics zip — producing no issue, no flag, no state: R4's
    own enforcement was inert. Here each unaccounted item becomes a named
    issue, and each unaccounted item WITHOUT a flag becomes a blocking
    finding. On the PRE lane the item accounting is skipped unless a
    caller supplies its own ledger: the chapter's question inventory is
    the Post lane's debt, and charging it to the Pre lane would block a
    lane that owes nothing (§6.6).
    """

    if ledger is None:
        if _is_pre_lane(payload):
            return [], []
        ledger = coverage_ledger.build_coverage_ledger(
            question_inventory=(
                dict(payload.get("question_task_inventory") or {})
            ),
            records=[dict(row) for row in _records(payload)],
        )
    # R4's own words: every item ends Placed OR FLAGGED. An item is
    # flagged when the ledger row carries a flag — or when ANY recorded
    # release issue names its QID (``unassigned_inventory_qid`` is
    # literally that record, and T9-2 keeps it a flag). Blocking is
    # reserved for the item NOTHING accounts for: no placement, no ledger
    # flag, no issue — the silent loss with no record at all.
    issue_flagged_qids = {
        str(qid).strip()
        for issue in payload.get("issues") or []
        if isinstance(issue, Mapping)
        for qid in issue.get("qids") or []
        if str(qid or "").strip()
    }
    # The staged payload's OWN recorded placements: a QID rendered as a
    # Type/Case Example row IS placed, and the minimal staging ledger
    # (built from inventory + concept records only) cannot see it.
    type_case_qids = {
        str(row.get("example_qid") or "").strip()
        for row in payload.get("type_case_rows") or []
        if isinstance(row, Mapping)
        and str(row.get("row_kind") or "") == "example"
        and str(row.get("example_qid") or "").strip()
    }
    issues: list[dict[str, Any]] = []
    blocking: list[str] = []
    for row in ledger.get("items") or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "") != "unaccounted":
            continue
        qid = str(row.get("qid") or "")
        flag = str(row.get("flag") or "")
        if qid in type_case_qids:
            # Placed — the staged Type/Case rows carry it.
            continue
        if qid in issue_flagged_qids:
            # A recorded issue already names this item — it ends Flagged,
            # and repeating it here would be a second vocabulary (whether
            # or not the ledger row carries its own flag too).
            continue
        if flag:
            issues.append(_issue(
                code=COVERAGE_UNACCOUNTED,
                message=(
                    f"{qid} is neither placed in the released rows nor "
                    "resolved by a placement record, and carries the "
                    f"recorded flag {flag!r} — Placed or Flagged holds, "
                    "so it ships for review rather than blocking"
                ),
                severity="warning",
                phase="release_qc",
                qids=[qid],
            ))
            continue
        message = (
            f"{qid} ends this release neither Placed nor Flagged — the "
            "exactly-once accounting (R4) has no record of where this "
            "source item went"
        )
        issues.append(_issue(
            code=COVERAGE_UNACCOUNTED,
            message=message,
            severity="error",
            phase="release_qc",
            qids=[qid],
        ))
        blocking.append(f"{COVERAGE_UNACCOUNTED}: {message}")
    return issues, blocking


def _chapter_duration_findings(
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Contract v2.0 §32.1: the frozen chapter duration must exist.

    Reads the two places the staged payload records it — the frozen
    directory metadata (``chapter_duration``, the value the Chapter row
    carried at staging) and the authored chapter metadata
    (``chapter_duration_minutes``, the registry/upload value the metadata
    pass was handed). Either being a positive number satisfies the gate;
    neither is a blocking finding. Mechanics: a numeric presence check,
    never a judgment about the source.
    """
    from .. import bulk_import as bi

    directory = payload.get("directory_metadata")
    directory = directory if isinstance(directory, Mapping) else {}
    chapter_meta = payload.get("chapter_meta")
    chapter_meta = chapter_meta if isinstance(chapter_meta, Mapping) else {}
    if bi.duration_minutes_cell(directory.get("chapter_duration")) != "":
        return [], []
    if bi.duration_minutes_cell(
        chapter_meta.get("chapter_duration_minutes")
    ) != "":
        return [], []
    message = (
        "the chapter has no frozen duration: the accepted duration registry "
        "has no row for this board/grade/subject/chapter and no explicit "
        "chapter duration was supplied with the upload; the workbooks ship "
        "the cell blank and the database write is blocked until one is "
        "supplied (contract v2.0 §32.1) — a duration is never estimated"
    )
    issue = _issue(
        code=CHAPTER_DURATION_UNREGISTERED,
        message=message,
        severity="error",
        phase="release_qc",
    )
    return [issue], [f"{CHAPTER_DURATION_UNREGISTERED}: {message}"]


def _publication_findings(
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Contract v2.0 §18: the run's publication is a frozen run variable.

    ``concept_source``/``question_source`` name it on every populated band,
    so a release whose ``source_book`` is blank cannot be written to the
    database: nothing borrows a filename or an origin-system constant for
    it. A presence check, never a judgment about the source.
    """
    if str(payload.get("source_book") or "").strip():
        return [], []
    message = (
        "the run has no publication: source_book is blank, so "
        "concept_source and question_source cannot name it; supply the "
        "publication on the upload and stage again (contract v2.0 §18) — "
        "a filename or a default is never borrowed for it"
    )
    issue = _issue(
        code=PUBLICATION_UNKNOWN,
        message=message,
        severity="error",
        phase="release_qc",
    )
    return [issue], [f"{PUBLICATION_UNKNOWN}: {message}"]


def _unresolved_link_findings(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    from . import build_concepts_release as rel

    issues: list[dict[str, Any]] = []
    for position, row in enumerate(_records(payload), start=1):
        for entry in row.get(rel.PRE_ROW_RELATED_UNRESOLVED_FIELD) or []:
            if not isinstance(entry, Mapping):
                continue
            issues.append(_issue(
                code=PRE_RELATED_UNRESOLVED,
                message=(
                    f"row {position} "
                    f"({str(row.get('concept_title') or '')!r}) records an "
                    "unresolved needed-for link to "
                    f"{str(entry.get('post_concept_id') or '')!r} "
                    f"({str(entry.get('post_concept_title') or '')!r}): "
                    f"{str(entry.get('reason') or '')}"
                ),
                severity="warning",
                phase="release_qc",
            ))
    return issues


def _validation_flag_findings(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for position, row in enumerate(_records(payload), start=1):
        for flag in row.get("review_flags") or []:
            text = str(flag or "")
            if not text.startswith(_VALIDATION_FLAG_PREFIX):
                continue
            issues.append(_issue(
                code=FLAGGED_VALIDATION_FINDING,
                message=(
                    f"row {position} "
                    f"({str(row.get('concept_title') or '')!r}) carries a "
                    f"recorded validation flag: {text}"
                ),
                severity="warning",
                phase="release_qc",
            ))
    return issues


QX_ITEM_REVIEW_FLAG = "qx_item_review_flag"


def _qx_item_flag_findings(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Per-occurrence QX flags become visible release issues (audit F6).

    The QX adjudication records critic dissent, Fixer decisions, and
    kind-pending notes on inventory items as ``_acsd_review_flags``; without
    this transcription those flags had no production reader — a recorded
    defect nobody reads is silent loss.
    """
    issues: list[dict[str, Any]] = []
    inventory = payload.get("question_task_inventory")
    items = (
        inventory.get("items") if isinstance(inventory, Mapping) else None
    ) or []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for flag in item.get("_acsd_review_flags") or []:
            text = str(flag or "").strip()
            if not text:
                continue
            issues.append(_issue(
                code=QX_ITEM_REVIEW_FLAG,
                message=(
                    f"inventory item {str(item.get('qid') or '?')} carries "
                    f"a recorded QX review flag: {text}"
                ),
                severity="warning",
                phase="release_qc",
            ))
    return issues


def audit(
    payload: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Any] | None = None,
    ledger: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One QC pass over the staged payload. NEVER raises.

    Returns ``(issues, blocking)``. Every blocking finding also appears as
    an issue, so the reviewer reads one list. ``artifacts`` is RESERVED
    and currently always ``None`` — both call sites run at staging, before
    any rendering exists, so no caller can supply rendered-artifact
    findings today; the parameter exists for the checklist items other
    owners hold (the Master lane's transports, the workbook read-back),
    which the audit may some day REPORT and will never own the refusal of
    (T10-0).

    Round 9: each pass runs ISOLATED. One try around all three [measured]
    let a later pass's failure discard a blocking finding an earlier pass
    had already computed — the voidable-gate shape T9-3 bans — and a
    failed pass now contributes its own BLOCKING finding: a net that
    could not run to completion must not certify the release (the same
    polarity ``type_catalog_unreadable`` got, for the same reason).

    What this audit deliberately does NOT recompute: the T9 identity codes
    (``duplicate_qid_assignment``, ``unknown_type_case_qid``, the
    ``case_uniqueness_*`` pair, ``type_catalog_unreadable``) are produced
    once by ``audit_type_cases`` / ``_case_uniqueness_issues`` and block
    through ``structural_defects``'s closed-set read — one implementation,
    one vocabulary.
    """

    del artifacts  # reserved; see the docstring
    issues: list[dict[str, Any]] = []
    blocking: list[str] = []

    def _pass(name: str, run) -> None:
        try:
            run()
        except Exception as exc:  # noqa: BLE001 — named AND blocking
            message = (
                f"the release QC audit's {name} pass failed "
                f"({type(exc).__name__}: {exc}); this release cannot be "
                "certified by a net that did not run to completion"
            )
            issues.append(_issue(
                code=AUDIT_UNAVAILABLE,
                message=message,
                severity="error",
                phase="release_qc",
            ))
            blocking.append(f"{AUDIT_UNAVAILABLE}: {message}")

    def _run_coverage() -> None:
        coverage_issues, coverage_blocking = _coverage_findings(
            payload, ledger)
        issues.extend(coverage_issues)
        blocking.extend(coverage_blocking)

    _pass("coverage", _run_coverage)

    def _run_chapter_duration() -> None:
        duration_issues, duration_blocking = _chapter_duration_findings(
            payload)
        issues.extend(duration_issues)
        blocking.extend(duration_blocking)

    _pass("chapter-duration", _run_chapter_duration)

    def _run_publication() -> None:
        publication_issues, publication_blocking = _publication_findings(
            payload)
        issues.extend(publication_issues)
        blocking.extend(publication_blocking)

    _pass("publication", _run_publication)
    _pass(
        "needed-for link",
        lambda: issues.extend(_unresolved_link_findings(payload)),
    )
    _pass(
        "validation-flag",
        lambda: issues.extend(_validation_flag_findings(payload)),
    )
    _pass(
        "qx-item-flag",
        lambda: issues.extend(_qx_item_flag_findings(payload)),
    )
    return issues, blocking
