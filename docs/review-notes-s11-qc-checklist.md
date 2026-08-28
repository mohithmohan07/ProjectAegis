# Review notes for the S11 QC checklist

> **Review input only.** This file is not the canonical release checklist and
> must not be renamed to `docs/release-qc-checklist.md`. The implementer owns
> that S11 deliverable on `claude/step-8-four-output-schema`; this separate path
> exists to avoid a cross-branch merge collision.

Status: candidate inventory and ambiguity register, not pipeline code.

Evidence basis: PR #229 head `d7d2e2f`, read through the GitHub raw view on
19 August 2026. The primary sources are the complete S11 section of
`docs/spec-step8.md:3522-3611`, the complete phase-by-phase account in
`docs/aegis-restructure.md:191-478`, and the issue vocabularies at
`backend/app/services/build_concepts_release.py:794-1506,2220-2373` and
`backend/app/services/generation.py:12863-12953`. The S11 section points back
to T9/T10, so those normative definitions are cited where they are the only
place that supplies a polarity or a literal blocking set.

## 1. Polarity and scope

For this inventory:

- **BLOCKING** means the T9/release-QC predicate refuses the matching
  **database write**. It does not, by itself, authorize a run halt. A
  Diagnostic release must retain evidence; whether every structural failure
  can still produce all four complete workbooks is open. The named S11 audit
  and duplicate-QID regressions do require all four downloads
  (`docs/spec-step8.md:3588-3591,3600-3604`), while the general three-state
  contract promises evidence rather than four valid workbooks
  (`docs/aegis-restructure.md:470-478`; `build_concepts_release.py:956-981`).
- **RUN HALT** names a pre-release raise or refusal. It is not interchangeable
  with BLOCKING. Marking arithmetic and the current final/deposit validators
  still contain run/deposit halt surfaces, and their relationship to Q13 is an
  explicit open question below (`docs/aegis-restructure.md:441-452`;
  `generation.py:15697-15738`).
- **ADVISORY** means semantic doubt, a critic dissent, a Fixer decision, or a
  quality concern becomes a visible issue or row flag. Downloads and explicit
  publication remain available (`docs/aegis-restructure.md:470-474`).
- An issue's display `severity` is not its gate polarity.
  `_issue` defaults to `severity="error"`, but `release_state` becomes
  Diagnostic only when `structural_defects` returns a finding
  (`build_concepts_release.py:794-909,1004-1026`). A checklist that treats every
  `error` issue as blocking would silently reverse S11.
- A semantic property below is checkable by verifying that the required author
  verdict, critic receipt, provenance and flag transport exist. The mechanical
  audit must not re-decide the content with a regex, vocabulary, threshold,
  count or shape heuristic (`CLAUDE.md:3-33,52-61`).

## 2. Candidate checklist items

### 2.1 Audit execution, state and observability

| ID | Checkable property | Polarity | Source and reason |
|---|---|---|---|
| EXEC-01 | `release_qc.audit(payload, *, artifacts=None, ledger=None)` runs for both Post and Pre staging, outside artifact builders, and no exception escapes it. | BLOCKING findings may result; the audit itself never halts | `docs/spec-step8.md:3524-3530,3566-3568,3600-3604`; T10 states that it never raises or runs inside a builder at `:1633-1641`. |
| EXEC-02 | The final and deposit validation gates consume the same literal `_BLOCKING_CODES` set, exactly `{required, required_parent}`. | RUN HALT/deposit refusal in the live topology; whether S11 must convert the final halt to a release BLOCK is OPEN | S11 names both consumers at `docs/spec-step8.md:3534-3537`; the literal ruling is `:1738-1744`. The live 52-code predecessor is `generation.py:12863-12953`. |
| EXEC-03 | Every other live `_FATAL_CODES` member still reaches the bounded repair/Fixer selection, then becomes a row review flag instead of entering the final halt selection. The repair selector and the halt selector are not the same set. | ADVISORY | `docs/spec-step8.md:1738-1749`; the live shared selection is `generation.py:15697-15738`, so replacing `_fatal_errors` too early would skip the Fixer. S11 regressions include `generic_misconception` at `docs/spec-step8.md:3578-3579`. |
| EXEC-04 | The named S11 injected blocker and duplicate-QID blocker change the release to Diagnostic, refuse the matching database write, and leave all four downloads available. Other structural findings must retain evidence, but four-workbook completeness is OPEN. | BLOCKING | `docs/spec-step8.md:3588-3591,3600-3604`; the general contract says only that Diagnostic evidence ships at `docs/aegis-restructure.md:470-474`. |
| EXEC-05 | An advisory audit finding included in a lane's summary changes an otherwise clean release to Ready with flags, never closes publication, and never triggers a fresh-author retry or adjudicator. Deliberately lane-external notices such as `assessment_lane_unavailable` need not change the surviving lane's state. | ADVISORY | `docs/aegis-restructure.md:311-315,400-402,423-440,470-474`; the lane-unavailable exception is `build_concepts_release.py:524-529`. |
| EXEC-06 | Detailed audit issues reach the staged payload, Release workbook Issues sheet, `context/source_evidence.json` and `release.json`; public state and terminal output receive correct aggregates. Assessment diagnostics transport is verified separately rather than assumed. | Same as originating property | `docs/spec-step8.md:1700-1705,3605-3607`; `release_result` carries aggregates at `build_concepts_release.py:2809-2848`. A record written with no reader is not a completed transport. |
| EXEC-07 | Audit output never mutates learner content, silently repairs a row, or derives a semantic verdict. | ADVISORY for semantic findings; structural detection may block | S11 says the audit is beside the builder and fails visibly at `docs/spec-step8.md:1613-1620`; deterministic code may validate shape but not meaning (`CLAUDE.md:52-61`). |
| EXEC-08 | Same-visible-title concepts do not fail merely because their text matches; persisted topic-scoped identity remains authoritative. | ADVISORY for title similarity; BLOCKING only for actual persisted-ID collision | `docs/spec-step8.md:1751-1759,3583`; the Step 8 identity projection is verified at `assessment_workbook.py:351-429,1108-1132,1259-1395`. |
| EXEC-09 | A reviewer edit to `concept_details` cannot turn a prose-derived comparison into a publication refusal. | ADVISORY | `qid_render_count_mismatch` and `example_less_case_shell` are explicitly corrected to flags at `docs/spec-step8.md:1602-1611,3592-3593`. |
| EXEC-10 | Audit findings feed both `_annotate_records` and `_release_summary` before persistence on each lane, whether by call order or explicit recomputation. QID/unit-scoped findings must reach row audit fields, and a summarized advisory issue must move an otherwise clean release to Ready with flags. | ADVISORY transport, but mandatory | Current Post ordering is annotation/summary at `build_concepts_release.py:1891-1892` before its payload at `:1911`; Pre is `:2671-2672` before `:2684`. `release_state` reads summary counts at `:971-980`, not the raw issue list. |
| EXEC-11 | The live `_learner_analysis_count` English-substring selector is removed, and no replacement prose-keyword or volume count chooses or discards a cached topology. A tie-break may count recorded `_aegis_analysis_allotments` verdict markers or use artifact-recorded completeness. | Mandatory doctrine correction; any resulting B3-unaccounted identity BLOCKS, while semantic selection doubt is ADVISORY | The live selector is `build_concepts_release.py:1585-1591,1648-1655`; S11 owns its purge and permits the recorded-marker tie-break at `docs/spec-step8.md:1815-1825,3540-3543`. |

### 2.2 Source, provenance and exact-once coverage

| ID | Checkable property | Polarity | Source and reason |
|---|---|---|---|
| SRC-01 | Original-source unreadability is distinguished from a staged release snapshot becoming unreadable after model work. | Original unreadability may be a genuine-impossibility RUN HALT; unreadable staged snapshots BLOCK the database write and retain evidence | Q13's genuine-impossibility exception is `docs/aegis-restructure.md:97-107,917-927`; unreadable release snapshots already block at `build_concepts_release.py:814-822,851-859`. Other `source_artifact` quality findings remain repair/advisory under S11 rather than being promoted by this row. |
| SRC-02 | Page extraction has an independently executed verifier call and durable receipt. Verifier dissent remains visible without gating, retrying the author or being mistaken for a missing verifier. | Receipt existence is OPEN; dissent is ADVISORY | Extraction and independent verification are required at `docs/aegis-restructure.md:203-209`; the universal critic rule is `:86-95,894-900` and governs the result, not permission to omit the critic. |
| SRC-03 | Every non-furniture block, figure, activity, info hub and question has exactly one coverage state, Placed or Flagged, with provenance; one identity may still have multiple non-exclusive projection edges. Every ledger `unaccounted` entry becomes a named release issue and refuses the database write. | BLOCKING | R4 is `docs/aegis-restructure.md:109-115`; the S11 coverage obligation is `docs/spec-step8.md:1590-1591,1846-1851,3594`. |
| SRC-04 | Furniture may be excluded only when the drop ledger retains its identity and what it said. | Missing drop evidence is OPEN unless the coverage ledger separately yields B3 `unaccounted`; role disagreement is ADVISORY | `docs/aegis-restructure.md:244-245` applies R4, but S11's closed blocking list does not separately name a furniture-drop code. |
| SRC-05 | Container projections preserve multi-role relationships: an activity can remain enrichment while its embedded task also has a question QID. No exclusivity assertion deletes either edge. | A ledger-proven unaccounted identity BLOCKS; edge/role disagreement is ADVISORY; other lost-edge transport is OPEN | `docs/aegis-restructure.md:238-242`. |
| SRC-06 | Every source question form has a stable QID, including checkpoints, discussion prompts, review points, projects and activity-embedded asks; a source role never grants exclusion power. | BLOCKING only when the coverage ledger proves a known inventory identity unaccounted; `example_qid_missing` polarity is OPEN; boundary disagreement is ADVISORY | `docs/aegis-restructure.md:293-300,316-319`; T9's B3 predicate is `docs/spec-step8.md:1590-1601`. |
| SRC-07 | Raw source, normalised source and published assessment remain distinguishable, with original wording, source-role review metadata and source QID retained beside derived polish. | BLOCKING only when a named B1/B3 identity predicate proves loss; wording/provenance doubt is ADVISORY; other missing transport is OPEN | `docs/aegis-restructure.md:293-319`. The S11 closed blocker list does not make every provenance omission a structural gate. |
| SRC-08 | A multi-part question retains every part and its order under one QID; cross-concept content is not mechanically split. | BLOCKING only if the whole known identity becomes B3-unaccounted; omitted-part/equivalence or whole-vs-part disagreement is ADVISORY | `docs/aegis-restructure.md:302-315`. |
| SRC-09 | Every polished item ships flagged for review, and the six polishing-equivalence verdicts are recorded: no omitted requirement, new requirement, answer reveal, changed expected response, detached context or changed visual dependency. The critic must execute and leave a receipt; failed checks add their own review flags. | Receipt existence is OPEN; universal/failure-specific flags and dissent are ADVISORY | `docs/aegis-restructure.md:305-315`. No local text comparison may decide equivalence. |
| SRC-10 | A required image/table retains a source-owned reference, content hash and manifest entry. S11 keeps `figure_reference_without_image` and `figure_reference_image_mismatch` repair failures visible rather than converting them into content gates. | Those validator findings are ADVISORY after repair; a separately proven B3-unaccounted identity or Step-10 publication hash/manifest failure may BLOCK | Source-image handling is `docs/aegis-restructure.md:203-218,300-301`; S11's old fatal-set inversion is `docs/spec-step8.md:1738-1749`, and checklist item 16 remains a Step-10 concern (`:1843`). |
| SRC-11 | `chapter_outline_not_applied`, `chapter_outline_topics_unusable` and `chapter_outline_review_flags` remain visible with provenance. `task_blocks_left_unruled` is not assigned polarity merely from its warning severity. | First three ADVISORY; `task_blocks_left_unruled` OPEN/conditional | Producers are `build_concepts_release.py:1035-1099`. A whole-block flag may be sufficient accounting, but an unaccounted internal QID/item would be B3 BLOCKING; §5 records the unresolved R4 seam. |
| SRC-12 | Pending semantic conflicts retain decision ID, context hash, evidence, candidates and source patch, and a generated exception retains its type and referenced IDs. | ADVISORY unless a separately named structural predicate is met | Dynamic issue producers are `build_concepts_release.py:1102-1155`; a dynamic exception class name must not silently enter a blocking allow-list. |
| SRC-13 | Upload, conversion and generation remain explicit, distinguishable actions; upload does not auto-match a chapter; PDF extraction has a hash-cache receipt. | Process/receipt conformance; gate polarity OPEN | `docs/aegis-restructure.md:193-209`. The same section ambiguously says image-PDF rasterisation occurs “internally on upload” while upload otherwise stores only, so the checklist must not invent the event boundary. |
| SRC-14 | C01, C02 and C03 carry model-authored projection verdicts; activity/“do you know?” headers do not themselves become topics or concepts, and multi-role edges survive. | Verdict/receipt existence OPEN; semantic dissent ADVISORY; proven B3 loss BLOCKING | `docs/aegis-restructure.md:220-242`. Header interpretation is model work, not a deterministic heading rule. |
| SRC-15 | Every polished item carries the required self-containment/phrasing receipt and retained source-role review metadata. | Receipt existence OPEN; content or critic doubt ADVISORY | `docs/aegis-restructure.md:293-319`. |
| SRC-16 | Every uncovered worked-method anchor that generation says “ships as a review item” has a durable row/release issue consumer; a progress-log line alone is not a receipt. | ADVISORY unless B3 separately proves the source identity unaccounted | The live path only logs at `generation.py:15804-15829`; no row flag or release issue is stamped. |
| SRC-17 | The canonical per-upload artifact bundle retains stable block, section, figure and task IDs and passes byte-exact reconstruction. | Malformed/unreadable artifact or broken mechanical reconstruction is structural; semantic source-quality findings remain ADVISORY after repair | `docs/aegis-restructure.md:199-201`; S11 leaves `source_artifact` content-quality findings outside the two-code halt set, so the checklist must name the mechanical predicate rather than promote that code by severity. |

### 2.3 Topology, enrichment and Pre-Learning receipts

| ID | Checkable property | Polarity | Source and reason |
|---|---|---|---|
| TOP-01 | Topic/concept topology carries an executed model-author verdict and independent critic receipt; no count, length or heading rule authored the division. | Receipt existence OPEN; critic dissent ADVISORY | `docs/aegis-restructure.md:247-258`; Q10 controls dissent but does not authorize skipping the critic, and deterministic classification is forbidden by `CLAUDE.md:3-33`. |
| TOP-02 | Blank topic/title/details or a missing required parent are distinguished from grade-level, well-worded detail quality and missing/weak Achieving Mastery prose; the author/critic evidence must also establish that the material is never pitched off the grade chart. | Schema absence maps to the `{required, required_parent}` RUN-HALT/open-Q13 seam; detail/Mastery quality and critic dissent are ADVISORY after bounded repair | `docs/aegis-restructure.md:260-263,282`; S11 retains only schema presence in the two-code set and moves Mastery validators to repair/flags (`docs/spec-step8.md:1738-1749`). |
| TOP-03 | A recorded language-mode choice reaches its adapter receipt. For poems, evidence covers stanza topics, meaning-bearing line-pair concepts, literal/metaphorical reading, line analysis, setup, devices, vocabulary and culmination; for prose, sizeable story breaks and significant plot episodes; the last language topic carries the named Detailed Analysis families, with grammar/listening/writing threaded through concepts. | Receipt existence OPEN; semantic conformance and critic dissent ADVISORY | `docs/aegis-restructure.md:265-282`. The “elements … must not coincide across the three” phrase has no stable referent for arbitrary stanza length and must not become a count/content gate. Step 11 owns the adapter; only the boundary between S11 receipt checking and Step 11 semantic conformance is open. |
| TOP-04 | Every activity, info hub and figure has a model-authored placement receipt based on meaning rather than print position, and its rendered information/image reference survives. | BLOCKING for an identity known to be unaccounted; ADVISORY for placement fitness | `docs/aegis-restructure.md:284-291`; exact-once consequence is SRC-03. |
| TOP-05 | Misconception and Error Analysis entries retain stable IDs and allotment receipts; no checklist demands an entry on every concept. | B3-unaccounted/lost identities BLOCK; duplicate-ID polarity is OPEN; meaning, distinctness and quality are ADVISORY | `docs/aegis-restructure.md:321-333`; T9's closed identity list does not explicitly classify duplicate learner-analysis IDs. The every-concept requirement is retired. |
| TOP-06 | The topology/placement receipt explicitly applies the model-governed source-meaning rules rather than print position, including the named Rules 2, 3, 4, 4a and 6. | Receipt existence OPEN; placement/quality dissent ADVISORY | `docs/aegis-restructure.md:256-258`; deterministic position or heading execution is forbidden by Rule 1. |
| PRE-01 | A Pre release distinguishes “no lane built,” “empty capture,” and “model decided this chapter assumes nothing.” An empty release is Ready only with a recorded `assumes_nothing` verdict. | BLOCKING when emptiness has no positive verdict or records `capture_incomplete`; critic dissent remains ADVISORY | `build_concepts_release.py:912-953,2600-2624`; §4's capture contract is `docs/aegis-restructure.md:335-348`. |
| PRE-02 | Every Pre concept retains explicit needed-for links to Post machine identities; unresolved necessity/grade/duplication concerns remain visible; no current-chapter source QID appears in a Pre artifact. | Current-chapter source-QID leakage and B3-unaccounted/broken persisted identity BLOCK; unresolved-link transport is OPEN; semantic necessity dissent is ADVISORY | `docs/aegis-restructure.md:349-351,357-358`; S11 explicitly places source-QID leakage in B1 at `docs/spec-step8.md:1582-1587`. The current unresolved-link private marker has no issue or coverage-ledger consumer. |
| PRE-03 | Pre question plans are model-authored and may vary from the stated target with a rationale; the audit enforces no 40/20/20 quota, maximum or padding rule. | ADVISORY | `docs/aegis-restructure.md:352-358`. The numbers are planning evidence, explicitly not a mandatory quota. |
| PRE-04 | One Build Concepts run carries Post and Pre evidence together; no separate Pre upload, derive flow or quota engine appears in the release receipt. A failed Master lane must have a disabled manifest entry plus `assessment_lane_unavailable`, without closing the completed Concept lane. | Required process/manifest evidence; the documented lane exception is ADVISORY to the surviving lane | `docs/aegis-restructure.md:359-362,461-469`; T15-2 specifies the lane exception at `docs/spec-step8.md:2399-2458`. |
| PRE-05 | An unreadable Pre snapshot, refused Pre map or refused source identity is named and refuses that database write without hiding evidence. `pre_learning_questions_refused` is not included in this structural predicate. | BLOCKING via `snapshot_defects` or payload `refused`; question-author refusal is OPEN/currently issue-only | `build_concepts_release.py:2220-2253,2319-2328,2460-2484,2739`; `structural_defects` reads `refused` and snapshot failures at `:851-859`. |
| PRE-06 | Empty-capture critic flags, Pre validation findings, row review flags and blocked/refused question-authoring reasons remain reviewer-visible. Nonempty unresolved needed-for links must obtain a named consumer rather than be stripped silently. | Existing critic/validation findings ADVISORY; question-author refusal and unresolved-link polarity/transport OPEN | Issue producers are `build_concepts_release.py:2291-2373`. `pre_learning_questions_refused` is issue-only; unresolved data is written at `:2661-2663` but `_pre_release_issues` never reads it. |
| PRE-07 | Prerequisites, vocabulary and required basics captured throughout earlier phases feed a complete Pre map at Post detailing standard; introductory/review source is only evidence, with a model verdict and critic receipt deciding whether it is genuinely prerequisite. | Receipt existence OPEN; semantic capture/fit dissent ADVISORY; a coverage-proven unaccounted identity BLOCKS | `docs/aegis-restructure.md:335-348`. |

### 2.4 Type, Case, Example and question identity

| ID | Checkable property | Polarity | Source and reason |
|---|---|---|---|
| QID-01 | Classification precedes embedding and allotment; every final Example has a stable QID route, Case identity where applicable, and one owner. | Named duplicate/unknown B1 identities BLOCK; `example_qid_missing` is OPEN; semantic classification is ADVISORY | `docs/aegis-restructure.md:364-385`; register Q14 supersedes the earlier multi-concept rendering rule. Case/QID placement verdicts remain evidence, but one Type-owner verdict must consolidate every Case and QID of a reusable Type under exactly one concept. |
| QID-02 | `duplicate_qid_assignment` and `unknown_type_case_qid` refuse publication; S11 rules that `unassigned_inventory_qid` ships flagged and remains publishable. | First two BLOCKING; last ADVISORY by S11 owner ruling, with a recorded §4 conflict | Producers are `build_concepts_release.py:1367-1399`; T9 names the blocking pair at `docs/spec-step8.md:1552-1554,1582-1587` and the negative control at `:1595-1601,3588-3591`. Section 4 also says every QID has exactly one final Type/Case assignment (`docs/aegis-restructure.md:378-385`), so the canonical checklist must cite S11's Placed-or-Flagged exception rather than presenting the sources as naturally consistent. |
| QID-03 | An Example with no QID is named rather than treated as an addressable source question. | OPEN: likely structural, but S11 does not rule | `example_qid_missing` is produced at `build_concepts_release.py:1349-1354`, and §4 requires a stable QID for every question (`docs/aegis-restructure.md:297-300`), but T9's closed B1 list does not name this code. The checklist must not infer polarity from default `severity="error"`. |
| QID-04 | `case_uniqueness_duplicate_case_identity` and `case_uniqueness_duplicate_qid_route` refuse the write. | BLOCKING | These are the two identity halves of the prefixed family produced at `build_concepts_release.py:1459-1506`; their underlying identities are named in `docs/spec-step8.md:1586-1587`. |
| QID-05 | `case_uniqueness_qid_render_count_mismatch` and `case_uniqueness_example_less_case_shell` are recorded without refusing publication. | ADVISORY | They depend on reviewer-editable prose and are explicitly removed from blocking at `docs/spec-step8.md:1602-1611,3592-3593`. |
| QID-06 | A malformed Type catalog produces its own named structural issue instead of silently replacing the catalog with `{}` and disabling the uniqueness audit. | BLOCKING | S11 requires the named defect at `docs/spec-step8.md:3599`; the live swallow is `build_concepts_release.py:1477-1483`. |
| QID-07 | Type/Case definitions, Case examples and semantic fit concerns are visible, but the mechanical audit does not author or judge them. | ADVISORY for definition/example quality; OPEN for `type_title_missing` and `type_cases_missing` | Live codes are produced at `build_concepts_release.py:1245-1321`; meaning and fit are model work under §4 (`docs/aegis-restructure.md:368-376`). S11 does not say whether a missing Type title is unaddressable identity or only missing authored content, and “Case where one applies” leaves a legitimate Type-only representation underspecified. |
| QID-08 | Repeated-question collisions are one grouped warning per normalised wording, including short, case-varied and Devanagari questions. No length threshold or Latin-only vocabulary suppresses them. | ADVISORY | S11 regressions are `docs/spec-step8.md:3594-3598`; the live threshold/ASCII implementation is `build_concepts_release.py:1404-1456`. |
| QID-09 | `_QUESTION_ITEM_MARKER_RE` is disclosed as a surviving shape residue; the result is not labelled “exact wording.” | ADVISORY | `docs/spec-step8.md:1809-1813`. |

### 2.5 Groups and assessment homes

| ID | Checkable property | Polarity | Source and reason |
|---|---|---|---|
| GRP-01 | Every question selected for placement has one addressable home concept/group and a sheet kind the active profile permits and this renderer can emit; an inventory QID left unassigned follows S11's separate visible-flag ruling. | BLOCKING for a placed candidate with unresolved/unrenderable home; unassigned inventory QID ADVISORY by S11 ruling | The explicit structural codes are `unresolved_question_home` and `sheet_kind_not_renderable` (`docs/spec-step8.md:1552-1569`); `unassigned_inventory_qid` is the negative control at `:1595-1601`. |
| GRP-02 | Group keys are nonblank and unique; the tier belongs to the renderer-supported enum; each group names an existing concept; candidate and group concept homes agree. | BLOCKING for enum/addressability mechanics; semantic level fitness is ADVISORY | These are identity/addressability properties (`docs/spec-step8.md:1554-1580`); semantic level/clustering is model work (`docs/aegis-restructure.md:387-402`). |
| GRP-03 | `group_name` and `group_display_name` carry Q12's global friendly `Concept name — Tier` form while the internal machine key remains separate. | BLOCKING when the stored/display identity pair is internally inconsistent; no content judgment | `docs/aegis-restructure.md:395-400`; staged codes include `group_visible_name_mismatch` (`docs/spec-step8.md:1560-1562`). This naming rule is not a profile field. |
| GRP-04 | Renderer capacity/shape checks (`render_shape_overflow`) and manifest/header dimensions are treated as physical schema constraints, never as content-size judgments. | BLOCKING | `docs/spec-step8.md:1562-1569`; deterministic schema gates are allowed by `CLAUDE.md:52-61`. |
| GRP-05 | Level and variant clustering carry executed author/critic receipts; critic dissent flags, and unresolved clustering ships as a flagged singleton. | Receipt existence OPEN; dissent and singleton flag ADVISORY | `docs/aegis-restructure.md:387-402`; Q10 governs critic dissent, not permission to omit the critic. |
| GRP-06 | Group-description quality concerns—capability, construction, placeholder/count/label prose—are reviewed by the model, not by a keyword or count check. | ADVISORY | `docs/aegis-restructure.md:404-406`; Rule 1 forbids a local classifier. |
| GRP-07 | Section 4's target internal-key form is `(<ConceptID>) BG##`, `IG##` or `AG##`, with visible names separate. The checklist records the discrepancy with the live alternative key bases rather than installing a regex shape gate. | Blank/duplicate key and broken join BLOCK under B1; format-only polarity OPEN | `docs/aegis-restructure.md:395-400`; the live key-base discrepancy is recorded in `docs/spec-step8.md:4340-4346`, while S11's closed B1 set names addressability rather than a global key regex (`:1552-1580`). |

### 2.6 Workbook, marking, assets and publication

| ID | Checkable property | Polarity | Source and reason |
|---|---|---|---|
| OUT-01 | All available outputs are projections of one immutable accepted snapshot; paired Concept/Master hierarchy content and identity hashes match, and no projection re-runs semantics. The manifest explicitly records any documented unavailable Master lane. | Identity/hash mismatch BLOCKING; complete four-output release status OPEN subject to T15-2 lane exception | `docs/aegis-restructure.md:408-421,461-469`; the unavailable-Master exception is `docs/spec-step8.md:2399-2458`. |
| OUT-02 | The release uses the SOP/MES schema family, required headers/fields and profile, while the reader still detects supported older layouts. | BLOCKING for unreadable or structurally incompatible layout | `docs/aegis-restructure.md:408-414`; S11's B4 family is `docs/spec-step8.md:1592-1593`. |
| OUT-03 | Five-level Chapter → Topic → Concept → Group → Question joins remain addressable; title/display/machine-ID pairs and labels round-trip without collapsing same-visible-title identities. | BLOCKING for actual identity loss; aggregate comparison remains ADVISORY until comma escaping exists | Checklist-item rulings are `docs/spec-step8.md:1827-1837`; the fixed Step 8 read-backs are `assessment_workbook.py:1108-1132,1259-1395`. |
| OUT-04 | Every concept, including a questionless concept, reaches its Concept File and, when that Master lane exists, its Master projection. Every source QID reaches its answer-style sheet with required group/marking identity or the explicit S11 `unassigned_inventory_qid` flagged state. | A B3-unaccounted identity in an existing artifact BLOCKS; S11's named unassigned state and T15-2 unavailable-Master exception remain ADVISORY to the surviving lane | Output contents are `docs/aegis-restructure.md:416-421`; exact-once consequence is SRC-03, the unassigned exception is `docs/spec-step8.md:1595-1601`, and T15-2 is `:2399-2458`. |
| OUT-05 | `answer_restriction` is one schema-supported enum; the author/critic verdict, registry version, Math/Physics method-equivalence exceptions and complete evidence payload (question, context, image/table, expected answer, rubric, modality, subject and grade) are retained. No deterministic registry lookup or invented third value occurs. | Invalid enum/schema BLOCKING; receipt existence OPEN; classification or critic doubt ADVISORY | `docs/aegis-restructure.md:423-440`; the live enum check is `assessment_workbook.py:1358-1361`. |
| OUT-06 | Option weights, rubric totals, sub-question totals and keyword weights are finite and reconcile exactly to the recorded canonical marks total; step, diagram and sub-question marks mirror the stem; redundant steps carry zero; deterministic code never rewrites the model's decomposition. The recorded blueprint cell is canonical, the API authors the per-item breakdown, and QC neither requires an external marking-rubric document nor treats “Question Paper Blueprint & Analysis” as runtime evidence. | Arithmetic may BLOCK publication and currently can RUN HALT; semantic decomposition dissent is ADVISORY | `docs/aegis-restructure.md:441-459`; the publication blocking family is `docs/spec-step8.md:1588-1589`, while §5 records the unresolved Q13/run-halt seam. |
| OUT-07 | Source-owned image references resolve to bytes named by their content hash and manifest; public URLs are non-expiring; the learner-facing volume has backup/retention evidence; explicit publication verifies the structural asset identity. | Hash/manifest/reference failure at publication or B3-unaccounted identity may BLOCK; retention, URL longevity and the S11 item-16 check are ADVISORY/Step-10-owned | `docs/aegis-restructure.md:211-218,475-478`; checklist item 16 is scoped to Step 10 at `docs/spec-step8.md:1843`. |
| OUT-08 | Explicit publication verifies the selected release's hashes, schema, source assets and placement identities, writes transactionally and idempotently, records a durable receipt, and drops no highlighted row. | BLOCKING | `docs/aegis-restructure.md:475-478`. |
| OUT-09 | `no_materialized_concept_rows` does not by itself mislabel a deliberately empty, positively decided release as corrupt. | ADVISORY unless a separate structural predicate proves loss | The live issue is at `build_concepts_release.py:1853-1862`; `structural_defects` explicitly separates emptiness from corruption at `:806-812`. |
| OUT-10 | `release_rows_upgraded_from_validated_cache` remains an informational provenance record, not a gate. | ADVISORY | `build_concepts_release.py:1878-1889`. |
| OUT-11 | `assessment_lane_unavailable` records the missing Master lane, disables its manifest entry, and does not change the completed Concept lane's state or close its separate database write. | ADVISORY to the surviving Concept lane; not a claim that the absent Master exists | `build_concepts_release.py:81-86,475-574`; T15-2 fixes this behavior at `docs/spec-step8.md:2399-2458`. |
| OUT-12 | `question_source` is populated from the active profile with the specified default `UpSchool DB`; a present blank row value must not defeat that contract silently. | Expected value settled; gate consequence OPEN | T10 fixes the profile value at `docs/spec-step8.md:1837-1840` and `docs/aegis-restructure.md:527-529`. The renderer reads the profile at `assessment_workbook.py:509-510`, while the `d7d2e2f` profile default remains blank. |
| OUT-13 | The append-only Bulk-Import workbook remains the database of record; explicit publication is a separate, transactional, idempotent, receipt-bearing action and never mutates the accepted release snapshot. | BLOCKING for transactional/hash/identity failure; process receipt mandatory | `docs/aegis-restructure.md:463-478`. |

## 3. Live gate and issue vocabularies with S11 target polarity

### 3.1 `generation.py` fatal subset

At `d7d2e2f`, `_FATAL_CODES` contains 52 codes
(`generation.py:12863-12891`). S11's target is not “the old set minus some
judgments.” It is the closed allow-list below at both final and deposit gates:

| Target | Codes | Consequence |
|---|---|---|
| Two-code gate set | `required`, `required_parent` | Mandatory row or parent schema is absent. The Fixer cannot accept these with a flag. These codes feed a live RUN HALT/deposit refusal; whether S11 must convert the final halt to a Diagnostic-release BLOCK remains open. |
| ADVISORY after bounded repair | `description_prefix`, `duplicate_title`, `duplicate_topic_concept`, `source_artifact`, `types_too_early`, `culmination_too_early`, `types_format`, `case_without_type`, `type_without_case`, `culmination_description`, `culmination_count`, `culmination_order`, `section_number`, `empty_types`, `merged_description`, `rich_text_format`, `empty_misconception`, `empty_error_analysis`, `duplicate_misconception`, `duplicate_error_analysis`, `missing_misconception_or_error_analysis`, `issue_section_order`, `noncanonical_issue_label`, `generic_misconception`, `misconception_framing`, `generic_error_analysis`, `error_analysis_framing`, `issue_section_overlap`, `analysis_section_format`, `missing_learner_analysis`, `unallotted_analysis_section`, `figure_reference_without_image`, `figure_reference_image_mismatch`, `generic_case_definition`, `missing_case_definition`, `case_without_example`, `case_question_not_definition`, `example_numbering`, `section_number_in_description`, `case_example_semantic_mismatch`, `description_truncated_clause`, `verbatim_source_description`, `missing_type_definition`, `generic_type_definition`, `duplicate_type_definition`, `missing_mastery_statement`, `mastery_statement_format`, `mastery_statement_not_substantive`, `duplicate_mastery_statement`, `mastery_marker_outside_description` | These remain repair inputs. If unresolved, the row and code ship as a review flag; they do not halt the run merely because `_FATAL_CODES` historically contained them. Actual machine-ID, QID, arithmetic, coverage and layout corruption is checked separately at publication. |

`_FIXER_UNACCEPTABLE_CODES` therefore shrinks from the live four-code set at
`generation.py:12899-12904` to the same two schema codes. In particular,
`duplicate_title` and `duplicate_topic_concept` cannot stand in for persisted
identity after Step 8's machine-ID migration (`docs/spec-step8.md:1751-1759`).

This 52-code table is only the live **fatal subset**, not the complete validator
vocabulary. `concept_validator.py` also emits error-severity codes such as
`forbidden_name`, `placeholder` and `repeated_sibling_opener`, plus warning
codes including `forbidden_topic`, `generic_opener`, `description_length`,
`thin_description`, `description_image_url`, `empty_image_alt` and
`textbook_dump`. `_fatal_errors` drops them at
`generation.py:12945-12953`, and the final report is not automatically carried
into release issues. S11 needs an explicit observability decision; display
severity and omission from `_FATAL_CODES` cannot decide whether a reviewer sees
them.

### 3.2 `build_concepts_release.py`

The following table records the companion machine predicate and target
polarity, not merely an issue code or its display `severity`. A bare issue with
a “blocking” name does not gate anything.

| Code or family | S11 checklist treatment | Evidence |
|---|---|---|
| `staged_row_unusable` | BLOCKING through companion `staged_row_defects` | A non-object row, blank topic or blank concept cannot reach an output (`build_concepts_release.py:635-692,874-908`). |
| `pre_learning_snapshot_unreadable` | BLOCKING through companion `snapshot_defects` | An unreadable on-disk input is incomplete, not an empty chapter (`:814-822,2238-2253`). |
| `pre_learning_map_refused`, `pre_learning_source_identity_refused` | BLOCKING through companion payload `refused` | The former is copied to `refused`; the latter writes that field directly, and `structural_defects` consumes it (`:851-853,2319-2328,2460-2484,2739`). |
| `pre_learning_questions_refused` | OPEN; currently ADVISORY by transport | It is emitted only as an issue at `:2329-2338`; the staged payload's `refused` value comes from the map at `:2739`, so `structural_defects` does not consume this code. |
| `pre_learning_empty_capture_verdict` | The issue code is non-gating by itself; the companion predicate `(Pre lane && nothing_to_publish && verdict != assumes_nothing)` consumes the underlying author verdict and BLOCKS | `_pre_lane_verdict_defects` returns early when a publishable row exists and otherwise reads the author verdict mechanically (`:912-953`); only critic review flags are ADVISORY (`:2291-2318`). Zero rows with no verdict can block even though this issue code is absent. |
| `pre_learning_empty_capture_review_flag`, dynamic Pre validation codes/`pre_learning_validation`, `pre_learning_review_flag`, `pre_learning_questions_blocked` | ADVISORY | Producers explicitly describe critic/Fixer/unresolved warnings (`:2291-2373`). |
| `chapter_outline_not_applied`, `chapter_outline_topics_unusable` | ADVISORY under the live contract | Warning issues at `:1035-1072`. |
| `task_blocks_left_unruled` | OPEN/conditional | The warning at `:1073-1086` may be sufficient whole-block accounting, but a coverage-proven unaccounted QID/item is B3 BLOCKING; see open R4 question below. |
| `chapter_outline_review_flags`, `release_rows_upgraded_from_validated_cache` | ADVISORY/informational | `:1087-1098,1878-1889`. |
| Dynamic pending-decision kind/`semantic_conflict` | ADVISORY | The producer says it is a semantic conflict and carries evidence (`:1102-1142`). |
| Dynamic exception class name | No code-only polarity; classify by a separately measured structural predicate | `_exception_issue` uses `type(exc).__name__` (`:1145-1155`). Adding arbitrary exception names to a blocker vocabulary would make the set open-ended. |
| `type_definition_missing`, `case_definition_missing`, `case_examples_missing` | ADVISORY | These concern authored taxonomy content; producers are `:1253-1258,1305-1321`. |
| `type_title_missing`, `type_cases_missing` | OPEN | Producers are `:1245-1251,1264-1270`, but S11 names neither in its closed identity list and §4 does not define the valid Type-only representation. |
| `example_qid_missing` | OPEN | A missing QID appears structural (`:1349-1354`; `docs/aegis-restructure.md:297-300`), but S11's closed list does not name it. |
| `duplicate_qid_assignment`, `unknown_type_case_qid` | BLOCKING | Duplicate or unknown QID identity; producers are `:1367-1399` and T9 names both. |
| `unassigned_inventory_qid` | ADVISORY and publishable by S11 ruling; §4 conflict must be cited | S11's negative control is `docs/spec-step8.md:3590-3591`, while `docs/aegis-restructure.md:378-385` says every QID receives one final assignment. |
| `repeated_question_text` | ADVISORY, grouped, Unicode/casefold-aware, no length floor | S11 regressions are `docs/spec-step8.md:3594-3598`; current producer is `build_concepts_release.py:1419-1456`. |
| `case_uniqueness_duplicate_case_identity`, `case_uniqueness_duplicate_qid_route` | BLOCKING | Machine identity repeats (`docs/spec-step8.md:1586-1587`). |
| `case_uniqueness_qid_render_count_mismatch`, `case_uniqueness_example_less_case_shell` | ADVISORY | Reviewer-editable prose cannot gate (`docs/spec-step8.md:1602-1611`). |
| Malformed-type-catalog code (not yet named) | BLOCKING | S11 requires a named structural defect in place of the current swallow (`docs/spec-step8.md:3599`; code `:1477-1483`). |
| `no_materialized_concept_rows` | ADVISORY unless another check proves corruption | Correct emptiness is not structural corruption (`build_concepts_release.py:806-812,1853-1862`). |
| `assessment_lane_unavailable` | ADVISORY to the separate Concept lane | The code deliberately preserves that lane's upload (`:81-86,475-574`). |

## 4. The missing 23-item source checklist

S11 says the SOP is absent from the repository and reconstructs its checklist
(`docs/spec-step8.md:1635-1641`). T10-7 then refers to “23 items plus three
extras” without reproducing all of them (`:1827-1844`). The following is the
maximum recoverable map. An unnamed item is not safe to implement from its
number or polarity alone.

| Source item | Recoverable property | Recoverable polarity | Missing information / contradiction |
|---|---|---|---|
| 1 | Five-level join | ADVISORY for now | The sentence first groups it under blocking, then says it is compared as a flag until comma escaping exists (`:1828-1829`). |
| 2 | Title/display/machine-ID pair | BLOCKING | Property is named but its exact field matrix is not reproduced (`:1829-1830`). |
| 3 | Label uniqueness | BLOCKING | Scope of “label” is not enumerated (`:1830`). |
| 4 | Unnamed profile-scoped mechanical check | ADVISORY | Name and fields absent (`:1837`). |
| 5 | `question_source` profile value | Not stated | Described as fixed, but no audit consequence is given (`:1837-1840`). |
| 6 | Unnamed profile-scoped mechanical check | ADVISORY | Name and fields absent (`:1837`). |
| 7 | Not mentioned | UNKNOWN | No recoverable property or polarity. |
| 8 | Unnamed mechanical check | BLOCKING | Property absent (`:1830-1831`). |
| 9 | Unnamed profile-scoped mechanical check | ADVISORY | Property absent (`:1837`). |
| 10 | Unnamed profile-scoped check | AMBIGUOUS | It is grouped as flagging and immediately followed by “blocking as B4” (`:1837`). |
| 11 | Unnamed mechanical check | BLOCKING | Property absent (`:1830-1831`). |
| 12 | Enum half blocks; verdict half does not | SPLIT | The enum and underlying field are not named (`:1843-1844`). |
| 13 | Candidate reaches no output data row | BLOCKING, assessment write | Transport is named at `:1832-1836`. |
| 14 | Unnamed mechanical check | BLOCKING | Property absent (`:1830-1831`). |
| 15 | Unnamed mechanical check | BLOCKING | Property absent (`:1830-1831`). |
| 16 | Durability concern | ADVISORY in S11 | Substantive durability belongs to Step 10 (`:1843`). |
| 17 | Alt-text neutrality | ADVISORY; no mechanical content check | Only the model prompt may judge it (`:1841-1842`). |
| 18 | “Fit half” | ADVISORY; no mechanical content check | Neither “fit” nor the other half is defined (`:1842-1843`). |
| 19 | Duplicate-QID half | BLOCKING | The other half is not defined (`:1831`). |
| 20 | Absent-from-output half | BLOCKING | The other half is not defined (`:1831`). |
| 21 | Unnamed “keep verbatim” check | BLOCKING | Property absent (`:1830-1831`). |
| 22 | Inventory identity neither Placed nor Flagged | BLOCKING | Fully recoverable at `:1846-1851`. |
| 23 | Unnamed pure judgment | ADVISORY; no mechanical content check | Property absent (`:1841-1843`). |
| Extras 1-3 | Not named | UNKNOWN | No enumeration exists in S11 or T10. |

The canonical checklist must supply the missing names, fields, evidence scope
and polarity. Otherwise `release_qc.py` would necessarily invent the content
of item 7, the three extras, and multiple half-items.

## 5. Open questions the implementation must not answer silently

1. **Neutral blocking transport.** S11 says to append arbitrary QC strings to
   `snapshot_defects`, but `structural_defects` prefixes every entry as “an
   input snapshot could not be read” (`build_concepts_release.py:854-859`).
   That would create false provenance. What new structured field carries QC
   findings, and which consumer turns it into state and publication refusal?

2. **Call order.** S11 places `audit(payload)` immediately before each payload
   is assembled (`docs/spec-step8.md:3524-3530`). At `d7d2e2f`, the first Post
   and Pre payload values do not exist until
   `build_concepts_release.py:1911` and `:2684`. Does S11 build a provisional
   payload, or audit after assembly but before persistence? Merely merging after
   assembly is insufficient: Post `_annotate_records`/`_release_summary` run at
   `:1891-1892`, Pre at `:2671-2672`, and `release_state` reads their summary
   counts. The chosen order must let a QID-scoped audit issue annotate its row
   and let an advisory-only issue yield Ready with flags.

3. **Coverage-ledger bridge.** The only production coverage-ledger call remains
   inside the post-staging diagnostics-zip builder. S11 says the ledger leaves
   the zip (`docs/spec-step8.md:3544,3594`) but does not name how staging obtains
   it. What function builds or returns it before both audit calls?

4. **Post transport.** Post `stage_release` has no `snapshot_defects`
   parameter/key at `d7d2e2f`; Pre has both. S11 correctly requires parity,
   but the neutral-transport issue above means copying the Pre field is not a
   truthful solution.

5. **Run gate versus publication gate.** S11 puts `{required,
   required_parent}` into final/deposit consumers that currently raise, while
   Q13 says mid-run structural detections route through the Fixer and finished
   work ships. Is this two-code set permitted to halt the run, or must it yield
   a Diagnostic release whose database write is blocked? Separately, the live
   `fatal` value both selects Fixer inputs and selects the raise
   (`generation.py:15697-15738`). S11 must keep all repair-eligible errors in
   the former selection while applying `_BLOCKING_CODES` only to the latter.

6. **Meaning of “complete” Diagnostic release.** §4 says Diagnostic evidence
   ships and the database write blocks (`docs/aegis-restructure.md:470-474`),
   while marking arithmetic says corrupt marking never ships (`:441-452`) and
   S11's regression expects all four downloads on an audit block
   (`docs/spec-step8.md:3600-3604`). Must every structural finding still produce
   all four workbooks, or may one artifact be absent with evidence?

7. **The second writer read-back caller.** The S11 Changes bullet says the
   surviving read-back routes to the Fixer (`docs/spec-step8.md:3538-3539`),
   but `writer.py` has two callers at `:1410` and `:1576`; only the first has
   an issues/Fixer receipt channel. Where does the second caller record a
   mismatch without raising or losing it?

8. **Unruled task blocks and R4.** `task_blocks_left_unruled` is a warning whose
   message says independent questions may be absent
   (`build_concepts_release.py:1073-1086`), while R4 says an unaccounted
   question blocks (`docs/aegis-restructure.md:109-115`). Is the whole task
   block's flagged placement sufficient accounting, or must a model verdict
   first establish its internal question identities?

9. **Asset split.** S11 says durability stays advisory and belongs to Step 10,
   while §4 requires publication to verify source-owned assets
   (`docs/aegis-restructure.md:475-478`). The canonical checklist should name
   which asset properties are structural now (hash, manifest, referenced bytes)
   and which are operational advice (retention and future URL durability).

10. **Language-mode ownership.** §4 contains detailed poem/prose content rules,
    but S11 excludes the Step 11 adapter and Rule 1 forbids reproducing those
    rules as local content checks. Should S11 check only for the Architect's
    mode verdict/adapter receipt and surface critic dissent, leaving semantic
    conformance to Step 11?

11. **`question_source` consequence.** T10 settles the profile default as
    `UpSchool DB` (`docs/spec-step8.md:1837-1840`), but the live profile default
    at `assessment_profile.py:34-36` remains blank. The renderer reads the
    profile at `assessment_workbook.py:509-510`; a present blank candidate
    value can also defeat a nonblank `.get(..., default)` fallback. Does a
    mismatch block, flag, or become a named repair? The expected value is not
    open; only its gate/repair consequence is.

12. **Numbered source items.** Item 7, all three extras, several whole items and
    three half-items cannot be recovered from this repository. The implementer
    needs the original SOP checklist or an owner ruling; an inferred list would
    be false provenance.

13. **Other halt surfaces.** `_BLOCKING_CODES` does not govern the independent
    invalid-inventory and exact-coverage raises at
    `generation.py:15739-15795`, nor the earlier exact-coverage raises reached
    from Phase 3 assembly. Some are identity mechanics; others include a
    semantically empty task. Which become recorded publication blocks, and
    which are genuine-impossibility run stops? S11 cannot claim a global
    polarity inversion without naming them.

14. **Unnamed code polarities.** `type_title_missing`, `type_cases_missing` and
    `example_qid_missing` default to error issues but have no structural
    consumer and no explicit S11 ruling. The malformed-catalog structural code
    is not named either. The canonical checklist and its literal-set regression
    must resolve these names and polarities; prefix matching or severity
    inference is unsafe.

15. **Two Pre advisory transports.** `pre_learning_questions_refused` is only
    an issue; unlike a map or source-identity refusal, it never reaches payload
    `refused`. Separately, `_aegis_pre_related_concepts_unresolved` is written at
    `build_concepts_release.py:2661-2663` and stripped as an audit field, but no
    application reader turns it into the promised row review flag. Should the
    first refusal block, and which issue/row channel preserves the second?

16. **Missing critic execution versus dissent.** Section 4 requires independent
    critic passes, but Q10 specifies only the polarity of their dissent. Which
    named structural or advisory record represents a critic that never ran or
    produced no receipt? Treating absence as ordinary dissent would let a
    “verified” release contain no verification command or verdict.

17. **Assessment diagnostics bridge.** T10 names the assessment diagnostics
    store as an audit surface, but `assessment_release_service.py:400-408`
    writes `AssessmentRelease.diagnostics` from the assessment frozen errors,
    and publication at `:519-525` uses the assessment renderer manifest. What
    explicit bridge carries a concept `release_qc` issue into that column?

18. **Nonfatal validator observability.** Validator codes omitted from the live
    `_FATAL_CODES` subset are neither final-halt inputs nor automatically release
    issues. Which ones must be visible after S11, and through what row/release
    consumer? The answer cannot be inferred from `severity`.

19. **Poem non-overlap phrase.** “Elements covered must not coincide across the
    three” (`docs/aegis-restructure.md:267-271`) has no stable referent for a
    stanza with a noncanonical number of line-pair concepts. It needs an owner
    interpretation and model-authored receipt, not a numeric/content rule.

## 6. Explicit S11 exclusions

The checklist must not smuggle these into S11:

- no `_dedupe_titles_chapter_wide` rewrite;
- no model-verdict replacement for the eleven out-of-scope judgment codes;
- no `_GENERIC_SKELETON_FAMILY_RE` change;
- no hub-marker vocabulary or Culmination title/length/count classifier moved
  from `writer.py` into QC;
- no `_learner_analysis_count` replacement based on another prose keyword or
  volume count; the S11-permitted count of recorded allotment markers remains
  available;
- no alt-text neutrality, semantic “fit,” misconception quality, grouping
  quality or language-mode meaning decided by deterministic code.

Sources: `docs/spec-step8.md:1761-1769,1815-1825,1841-1844,3609-3611` and
`CLAUDE.md:3-33`.

No tests were run. All implementation evidence was read-only against the exact
PR #229 head named above.
