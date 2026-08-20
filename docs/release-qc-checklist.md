# The release QC checklist — reconstructed (spec-step8 T10, S11)

**Provenance, stated first.** The original SOP §7 pre-upload QC checklist is
not in this repository in any form ([verified], T10). This document is its
RECONSTRUCTION from the §12 register's per-item rulings (T10-7, T9, Q19/D6),
authored with `backend/app/services/release_qc.py` in S11. Where T10-7
glosses an item, the gloss is quoted; where it records only a ruling class
for an item number, that is said plainly — **the original wording for those
items is not in-repo and only the owner can supply it**. Review input: the
review stream's independent candidate inventory and ambiguity register
(`docs/review-notes-s11-qc-checklist.md` **on the `review/specs-and-audit`
branch, PR #232** — deliberately not merged here to avoid a cross-branch
path collision), whose open questions are folded into the "Open with the
owner" section below.

**The polarity rule (T9, one sentence).** *A defect blocks the DATABASE
WRITE when it corrupts an identity; a defect flags when it concerns what the
source means. Nothing ever blocks a download.* The audit itself never
raises, never runs inside an artifact builder, and a blocking finding makes
the release *Diagnostic* — every download still ships, the write refuses.

**Where the audit runs.** `release_qc.audit(payload, *, artifacts=None,
ledger=None)` at STAGING, both lanes, immediately before each staging
function assembles its payload (T10-0). Its `issues` join the release's
existing ledger; its `blocking` strings ride
`payload["qc_blocking_defects"]` — their OWN key with their own honest
reader (Round 9: V2's original `snapshot_defects` routing [measured]
stamped every finding with a false "input snapshot could not be read"
preamble and minted a spurious snapshot-corruption issue on the Pre lane).
`structural_defects` reads it into `release_state`; `snapshot_defects`
keeps its original input-artifact meaning on both lanes, with the Post
lane's V2 key/parameter kept for lane parity (dormant until a Post caller
records one).

## The 23 SOP items, per T10-7's rulings

**Mechanical, universal, blocking per T9** — enforced by the named owner,
never re-decided by the audit:

| # | Reconstruction | Owner today |
|---|---|---|
| 1 | The five-level join (chapter→topic→concept→group→question) composed cells agree — fixed by T3.7; compared as a FLAG until comma-escaping exists | workbook read-back twins (`assessment_workbook`) |
| 2 | title / display-name / machine-ID pairs agree — fixed by T4 | identity module + read-back twins |
| 3 | `question_label` uniqueness — fixed by T5 | `assessment_release_service` (T5-1 transport) |
| 8 | ruling recorded (blocking); original wording not in-repo | — owner to supply wording |
| 11 | ruling recorded (blocking); original wording not in-repo | — owner to supply wording |
| 13 | a candidate that reaches no data row blocks the ASSESSMENT-lane publication act — verdict computed at staging by `unresolved_question_homes`, enforced via `diagnostics["payload_errors"]` → `_readiness` BLOCKED; never anything the renderer discovers | assessment lane (T7.5/D8.5b transport) |
| 14 | ruling recorded (blocking); original wording not in-repo | — owner to supply wording |
| 15 | ruling recorded (blocking); original wording not in-repo | — owner to supply wording |
| 19 | the duplicate-QID half blocks: `duplicate_qid_assignment` is in `T9_IDENTITY_DEFECT_CODES` and `structural_defects` reads it (S11) | concept lane, closed identity set |
| 20 | the absent-from-output half blocks: `unknown_type_case_qid`, same closed set (S11) | concept lane, closed identity set |
| 21 | ruling recorded (blocking); original wording not in-repo | — owner to supply wording |
| 22 | **an inventory item that ends the run neither Placed nor Flagged** (B3, R4). The coverage ledger gains its first issue-producing consumer: the audit builds/receives it, every unaccounted item becomes a named issue, and an unaccounted item with NO flag and NO recorded issue naming its QID is a blocking finding. Stated honestly: in the wired staging flow this net is normally SHIELDED — `unassigned_inventory_qid` names every unplaced item and rendered examples count as placements — so it fires on stale, stripped or legacy payloads and on future producers, the same gap-net class as the row gate's recorded-key case | `release_qc._coverage_findings` (S11) |

**Mechanical, profile-scoped, flagging:** items 4, 6, 9 — rulings recorded
(flag class); original wording not in-repo. Item 10 blocks as B4
(schema/layout — the header gate, the reader's layout gate, the
manifest-union field gate; owners unchanged).

**Fixed in code rather than checked:** item 5 — `question_source` names the
ORIGIN SYSTEM, not a school; the profile carries the "UpSchool DB" default
(S11) and the renderer reads it when a candidate declares none.

**Pure judgment, no mechanical check ever** (writing a regex here would be
the defect): item 17 (alt-text neutrality — the only expression stays the
prompt contract in `assessment_materialization`), item 18's fit half,
item 23. Item 16 stays a flag (ruling recorded; wording not in-repo).
Item 12 is already correct — the enum blocks, the verdict never gates.
Item 7: **no ruling and no wording survive in-repo; open with the owner.**

## The three extras

T10-7 says "the 23 items plus three extras" and never enumerates them.
The reconstruction names its own three, **flagged for owner confirmation**:

* **E1 — the repeated-question collision audit** (T10-5, tightened by
  audit finding 18): lossless, casefolded, threshold-free; every collision
  one grouped warning carrying the QIDs and the shared wording. The key is
  NFC + whitespace-run collapse + casefold and NOTHING else — the former
  punctuation/`\w` noise class deleted Unicode combining marks (two
  distinct Devanagari strings collided into one false warning), and the
  leading item-marker strip was a shape judgment that could not be
  verified lossless; both are gone, so the warning compares exact wording
  and classifies nothing.
* **E2 — Type-catalog parseability** (T9-3): a catalog that will not parse
  is the named defect `type_catalog_unreadable` (blocking via the closed
  identity set), never a swallowed exception that silently disables the
  uniqueness gate.
* **E3 — unresolved Pre needed-for links**: the resolver's recorded
  non-decisions (`_aegis_pre_related_concepts_unresolved`) had
  [measured] no reader anywhere; the audit reports each as an advisory
  issue (`pre_related_concept_unresolved`). Never blocking — an
  unresolved link is a review question, not corruption.

## What the audit reports but does not own

* The T9 identity codes (`duplicate_qid_assignment`,
  `unknown_type_case_qid`, `case_uniqueness_duplicate_case_identity`,
  `case_uniqueness_duplicate_qid_route`, `type_catalog_unreadable`) are
  produced once by `audit_type_cases` / `_case_uniqueness_issues` and
  block through `structural_defects`' closed-set read — one
  implementation, one vocabulary; the audit does not recompute them.
* T7.5's seven assessment-lane codes (`unresolved_question_home`,
  `sheet_kind_not_renderable`, `group_home_disagreement`,
  `group_concept_home_unknown`, `group_home_unnamed`,
  `group_visible_name_mismatch`, `render_shape_overflow`) travel by
  `diagnostics["payload_errors"]` → `_readiness`; the audit may report
  them when a caller supplies artifacts, and never owns their refusal.
* T10-2's advisory validation findings ride rows as `validation: …` review
  flags; the audit transcribes them to issues
  (`flagged_validation_finding`) so the Post lane's Issues sheet shows
  them — the recorded asymmetry that `models.Concept` has no
  `review_flags` column still drops row flags at the direct DB-deposit
  boundary.

## Deliberately not blocking (T9-2 — the negative controls)

`unassigned_inventory_qid` (a coverage verdict; R4 says Placed OR Flagged,
and this issue IS the flag), `qid_render_count_mismatch` and
`example_less_case_shell` (both text-derived — a reviewer reword of
`concept_details` must never refuse the upload on a deterministic prose
comparison, §7:577).

## Recorded limits of the staging audit

* On the PRE lane the item accounting is skipped unless a caller supplies
  a ledger: the chapter's question inventory is the Post lane's debt, and
  charging it to the Pre lane would block a lane that owes nothing (§6.6).
* The staging-built ledger is minimal (inventory + records +
  `type_case_rows` placements). The full-evidence ledger — figures,
  containers, placement snapshots, Pre accounting — still ships in the
  diagnostics zip; findings only its extra evidence can prove are the
  zip's, not the audit's, until an owner asks otherwise. The inverse
  direction is also recorded: the minimal ledger cannot see the Phase-2.2
  `hub_placements` snapshot, so a hub item placed only there COULD read
  unaccounted at staging — today the issue-flag shield covers it
  (`unassigned_inventory_qid` names any unassigned item), and the risk is
  named here rather than assumed away.
* A failed audit PASS is the named issue `release_qc_unavailable` AND a
  blocking finding (Round 9): each pass runs isolated, so a later pass's
  failure can never discard an earlier pass's finding, and a net that
  could not run to completion never certifies the release — the same
  polarity `type_catalog_unreadable` carries, for the same T9-3 reason.
* Advisory validation flags at the DIRECT DB-deposit boundary travel only
  as far as the deposit's own record copies plus a per-finding run-log
  warning (`models.Concept` has no `review_flags` column). A finding that
  first arises from deposit-only cleanup therefore has no durable
  artifact record — a named residue owed to the owner, below.

## Open with the owner

* Item 7 (no ruling, no wording) and the un-glossed item wordings
  (4, 6, 8, 9, 11, 14, 15, 16, 21).
* Confirmation of the three extras' identities (E1-E3 above are the
  reconstruction's own naming).
* Whether every structural Diagnostic release must contain all four
  complete workbooks (the S11 regressions require the four downloads to
  RETURN; completeness beyond that is unpinned), and whether the final
  gate's remaining `{required, required_parent}` halt should some day
  become a release block instead of a run halt.
* A durable record for deposit-only advisory findings (see the recorded
  limit above): the direct-deposit lane is non-release internal tooling,
  but "logged, not stored" is below the bar the release lanes hold.
* From the review stream's inventory (its file lives on the
  `review/specs-and-audit` branch), neither covered nor consciously
  ruled here — each needs an owner polarity or an owner "not an item":
  `task_blocks_left_unruled` vs R4 (whole-block flag vs internal QIDs);
  the asset split (which asset properties are structural now vs step 10's
  operational half); language-mode ownership (mode-verdict and
  adapter-receipt checking vs step 11); observability of error-severity
  validator codes outside the fatal family (`forbidden_name`,
  `placeholder`, `repeated_sibling_opener` drive repair but reach no flag
  and no issue); the open-polarity codes `example_qid_missing`,
  `type_title_missing`/`type_cases_missing`,
  `pre_learning_questions_refused`, duplicate learner-analysis item ids,
  the group internal-key-form discrepancy, and furniture-drop evidence;
  and the worked-method-anchor "ships flagged" path, whose only record
  today is a progress log line — the same producer-with-no-consumer shape
  E3 closed.
