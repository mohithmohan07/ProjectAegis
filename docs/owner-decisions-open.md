# Owner decisions still open

> **Decision sheet, not implementation.** This register contains the owner
> choices that remain open after Step 8 S11 at PR #229 commit `d5cfafe`. It
> proposes no answer. Each option is intentionally unranked, and selecting an
> option authorises only the named contract change—not pipeline code in this
> review branch.

Evidence was re-read at `d5cfafe` from `docs/release-qc-checklist.md`,
`docs/spec-step8.md` Rounds 6–9, `docs/aegis-restructure.md` §12, the review
input at `docs/review-notes-s11-qc-checklist.md`, and the two standing legacy
residues named below. The §12 register expressly says Q1–Q13 are decided.
Accordingly, its provisional or revisit-triggered rulings (Q4, Q8 and Q11)
are not reopened here. Accepted cosmetic residues and work already assigned
to a later implementation step are also excluded unless an owner choice is
still required.

**Open count: 32 decision entries.** A response may cite the IDs below, for
example `SOP-04: B` or `REL-01: C`.

## A. Missing source-SOP checklist items (10)

The repository preserves a polarity for most of these item numbers but not
their original wording, fields or evidence scope. The current checklist says
so explicitly at `docs/release-qc-checklist.md:35-69,147-152`; the surviving
rulings come from `docs/spec-step8.md:1827-1844`. Supplying new wording is
valid, but it must be labelled an owner-authored reconstruction rather than
quoted as the missing SOP.

### SOP-04 — profile-scoped advisory item 4

**Context.** Only “mechanical, profile-scoped, flagging” survives for item 4.
No source property, profile field or evidence field is recoverable, so the
current placeholder cannot be implemented or reviewed faithfully.

**Exact question.** What property and profile/evidence fields did SOP item 4
cover?

**Options.**

- **A — Supply the original wording.** Restore it verbatim and retain the
  recorded advisory polarity.
- **B — Author replacement wording.** Record it as a new owner reconstruction,
  with advisory polarity.
- **C — Retire item 4.** Remove the placeholder and record that its source
  requirement could not be recovered.

**Where the answer lands.** `docs/release-qc-checklist.md` item 4,
`docs/spec-step8.md` T10-7/repair log, and a new entry in
`docs/aegis-restructure.md` §12.

### SOP-06 — profile-scoped advisory item 6

**Context.** Item 6 has the same surviving class as item 4, but no surviving
wording identifies its property or fields. It cannot safely inherit item 4's
meaning merely because the polarity matches.

**Exact question.** What property and profile/evidence fields did SOP item 6
cover?

**Options.**

- **A — Supply the original wording.** Restore it verbatim as advisory.
- **B — Author replacement wording.** Mark it as owner-authored and advisory.
- **C — Retire item 6.** Remove the unimplementable placeholder and preserve
  the provenance gap in the register.

**Where the answer lands.** `docs/release-qc-checklist.md` item 6,
`docs/spec-step8.md` T10-7/repair log, and `docs/aegis-restructure.md` §12.

### SOP-07 — item 7, whose wording and polarity are both absent

**Context.** Item 7 is the only numbered item for which neither wording nor a
polarity ruling survives. Inferring either would silently manufacture a source
contract.

**Exact question.** What was item 7, and was it blocking, advisory, pure model
judgment, or intentionally not a checklist item?

**Options.**

- **A — Supply the original wording and ruling.** Restore both verbatim.
- **B — Author a replacement property and ruling.** Label both as a new owner
  reconstruction.
- **C — Retire item 7.** Record that no canonical requirement survives.

**Where the answer lands.** `docs/release-qc-checklist.md` item 7,
`docs/spec-step8.md` T10-7, and `docs/aegis-restructure.md` §12.

### SOP-08 — universal blocking item 8

**Context.** The register places item 8 in the mechanical, universal,
database-write-blocking class, but its actual property and evidence are absent.
A blocking placeholder with no predicate cannot be implemented safely.

**Exact question.** What mechanical property, fields and evidence did item 8
check?

**Options.**

- **A — Supply the original wording.** Restore it under the recorded blocking
  polarity.
- **B — Author a replacement predicate.** Label it as reconstruction and keep
  the blocking polarity.
- **C — Retire item 8.** Remove the unsupported blocker and record the gap.

**Where the answer lands.** `docs/release-qc-checklist.md` item 8 and
`docs/spec-step8.md` T10-7; if the supplied property introduces a concrete
blocker, also T9's closed blocking set; plus `docs/aegis-restructure.md` §12.

### SOP-09 — profile-scoped advisory item 9

**Context.** Item 9 is known only to be a mechanical check governed by the
active profile and to flag rather than block. Its property and fields are not
recoverable.

**Exact question.** What property and profile/evidence fields did item 9
cover?

**Options.**

- **A — Supply the original wording.** Restore it verbatim as advisory.
- **B — Author replacement wording.** Mark it as an owner reconstruction and
  keep it advisory.
- **C — Retire item 9.** Remove the placeholder and record the source gap.

**Where the answer lands.** `docs/release-qc-checklist.md` item 9,
`docs/spec-step8.md` T10-7/repair log, and `docs/aegis-restructure.md` §12.

### SOP-11 — universal blocking item 11

**Context.** Only the mechanical, universal, blocking classification survives.
The checked property, fields and evidence do not.

**Exact question.** What mechanical property, fields and evidence did item 11
check?

**Options.**

- **A — Supply the original wording.** Restore it under the recorded blocking
  polarity.
- **B — Author a replacement predicate.** Identify it as reconstruction and
  keep it blocking.
- **C — Retire item 11.** Remove the ungrounded blocker and record the gap.

**Where the answer lands.** `docs/release-qc-checklist.md` item 11 and
`docs/spec-step8.md` T10-7; if the supplied property introduces a concrete
blocker, also T9's closed blocking set; plus `docs/aegis-restructure.md` §12.

### SOP-14 — universal blocking item 14

**Context.** Item 14 survives only as an unnamed mechanical blocker. No field
matrix or failure evidence is present.

**Exact question.** What mechanical property, fields and evidence did item 14
check?

**Options.**

- **A — Supply the original wording.** Restore it under the recorded blocking
  polarity.
- **B — Author a replacement predicate.** Label it as reconstruction and keep
  it blocking.
- **C — Retire item 14.** Remove the unsupported blocker and retain the gap as
  provenance.

**Where the answer lands.** `docs/release-qc-checklist.md` item 14 and
`docs/spec-step8.md` T10-7; if the supplied property introduces a concrete
blocker, also T9's closed blocking set; plus `docs/aegis-restructure.md` §12.

### SOP-15 — universal blocking item 15

**Context.** The repository retains item 15's blocking class but none of its
substance. It must not be filled by analogy with neighbouring items.

**Exact question.** What mechanical property, fields and evidence did item 15
check?

**Options.**

- **A — Supply the original wording.** Restore it under the recorded blocking
  polarity.
- **B — Author a replacement predicate.** Label it as reconstruction and keep
  it blocking.
- **C — Retire item 15.** Remove the placeholder and record the missing source.

**Where the answer lands.** `docs/release-qc-checklist.md` item 15 and
`docs/spec-step8.md` T10-7; if the supplied property introduces a concrete
blocker, also T9's closed blocking set; plus `docs/aegis-restructure.md` §12.

### SOP-16 — advisory item 16

**Context.** The surviving ruling says only that item 16 flags. The adjacent
text separately leaves substantive image durability with Step 10, but does not
prove that durability was item 16's missing subject.

**Exact question.** What property and profile/evidence fields did item 16
cover?

**Options.**

- **A — Supply the original wording.** Restore it verbatim as advisory.
- **B — Author replacement wording.** Label it as reconstruction and retain
  advisory polarity.
- **C — Retire item 16.** Remove the placeholder without weakening Step 10's
  independent durability contract.

**Where the answer lands.** `docs/release-qc-checklist.md` item 16,
`docs/spec-step8.md` T10-7/repair log and `docs/aegis-restructure.md` §12. Add
the Step-10 durability spec only if the supplied wording proves item 16 is
asset-related.

### SOP-21 — universal blocking item 21

**Context.** Item 21 is recorded as mechanical, universal and blocking, but no
source wording or predicate survives.

**Exact question.** What mechanical property, fields and evidence did item 21
check?

**Options.**

- **A — Supply the original wording.** Restore it under the recorded blocking
  polarity.
- **B — Author a replacement predicate.** Label it as reconstruction and keep
  it blocking.
- **C — Retire item 21.** Remove the unsupported blocker and record the gap.

**Where the answer lands.** `docs/release-qc-checklist.md` item 21 and
`docs/spec-step8.md` T10-7; if the supplied property introduces a concrete
blocker, also T9's closed blocking set; plus `docs/aegis-restructure.md` §12.

## B. Identity of the three unnamed extras (3)

T10-7 says the checklist contains “23 items plus three extras” but never names
the extras. S11 proposed E1–E3 at `docs/release-qc-checklist.md:71-90` and
explicitly marked them for confirmation (`docs/spec-step8.md:4956-4961`). Each
is useful independently; the open question is whether it may claim source-SOP
provenance.

### EXTRA-01 — repeated-question collision audit

**Context.** E1 is the Unicode/casefold repeated-question collision warning. It
is advisory and is currently S11's own reconstruction.

**Exact question.** Is this one of the SOP's three extras?

**Options.**

- **A — Confirm it.** Keep E1's identity and advisory polarity as a canonical
  extra.
- **B — Replace it.** Supply the correct extra and move this audit to an
  explicitly local S11 addition.
- **C — Decline source provenance.** Keep the audit, but state that the missing
  SOP extras remain unknown.

**Where the answer lands.** The “three extras” section of
`docs/release-qc-checklist.md`, `docs/spec-step8.md` T10-7/Rounds 8–9, and
`docs/aegis-restructure.md` §12.

### EXTRA-02 — Type-catalog parseability

**Context.** E2 makes an unreadable Type catalog a named structural blocker
instead of allowing a swallowed exception to disable the identity audit.

**Exact question.** Is Type-catalog parseability one of the SOP's three extras?

**Options.**

- **A — Confirm it.** Keep E2 as a canonical blocking extra.
- **B — Replace it.** Supply the correct extra; retain catalog parseability only
  as a locally justified T9 safeguard.
- **C — Decline source provenance.** Keep the safeguard but leave the SOP extra
  unidentified.

**Where the answer lands.** `docs/release-qc-checklist.md` E2,
`docs/spec-step8.md` T9-3/T10-7, and `docs/aegis-restructure.md` §12.

### EXTRA-03 — unresolved Pre needed-for links

**Context.** E3 turns each recorded but unresolved Pre-to-Post needed-for link
into an advisory issue. It closed a producer-with-no-reader seam, but its status
as an SOP extra is reconstructed rather than known.

**Exact question.** Is the unresolved-needed-for reader one of the SOP's three
extras?

**Options.**

- **A — Confirm it.** Keep E3 as a canonical advisory extra.
- **B — Replace it.** Supply the correct extra; retain this reader as an S11
  local addition.
- **C — Decline source provenance.** Keep the reader while leaving the third SOP
  extra unidentified.

**Where the answer lands.** `docs/release-qc-checklist.md` E3,
`docs/spec-step8.md` T10-7/Rounds 8–9, and `docs/aegis-restructure.md` §12.

## C. Release consequence and record durability (4)

### REL-01 — what “four outputs” requires of a Diagnostic release

**Context.** The product contract describes one snapshot and four projections,
while the release-state contract says a Diagnostic release ships evidence and
blocks the database write. S11 proves four download endpoints for the Post
duplicate-QID case and, separately, for injected QC in each lane; those
endpoints include diagnostics and JSON and are not the four contracted
Concept/Master projections. It does not prove every structurally damaged case
can yield four complete learner-format workbooks
(`docs/aegis-restructure.md:461-474`; `docs/release-qc-checklist.md:153-157`;
`docs/review-notes-s11-qc-checklist.md:287-292`).

**Exact question.** When structural corruption makes a projection incomplete,
must a Diagnostic release still contain four complete workbooks?

**Options.**

- **A — Four complete workbooks are mandatory.** The public contract stays
  simple, but the system needs a representation that neither invents nor drops
  the broken data.
- **B — Four contracted projections are mandatory.** An affected projection
  may be an explicitly labelled diagnostic shell or partial workbook with a
  manifest; “present” no longer implies learner-ready.
- **C — An affected projection or whole lane may be absent.** Sound workbooks
  ship and the absence is represented in the manifest and diagnostics. This
  extends T15-2's existing missing-Master exception to structurally Diagnostic
  releases and amends OD1/T14 to that extent.

**Where the answer lands.** `docs/aegis-restructure.md` §4 and §12,
`docs/release-qc-checklist.md`, and the S11 acceptance wording in
`docs/spec-step8.md`, including T14/T15.

### GATE-01 — final schema gate: run halt or publication block

**Context.** S11 narrows the final/deposit blocking codes to `required` and
`required_parent`, but the final consumer still raises after bounded correction.
`required_parent` currently has no caller and cannot fire, so the live choice is
about `required` and any future `required_parent` producer. Q13 says completed
model work reaches a release unless genuine impossibility prevents it
(`docs/spec-step8.md:4893-4897`;
`docs/review-notes-s11-qc-checklist.md:278-285`;
`docs/release-qc-checklist.md:153-157`; `docs/aegis-restructure.md:917-927`).

**Exact question.** If a row still lacks a required field or parent after its
bounded correction, may the final gate end the run, or must it stage a
Diagnostic release whose artifacts ship while the database write refuses?

**Options.**

- **A — Keep the run halt.** This is compatible with Q13 only if the missing
  schema field is ruled a genuine impossibility; otherwise it amends Q13's
  ruling that the halt is retired, and finished evidence may not ship.
- **B — Convert it to a Diagnostic database-write block.** Evidence ships, but
  the projection must carry an unusable row without pretending it is valid.
- **C — Quarantine the row.** Sound rows render and the missing row becomes an
  explicit Diagnostic defect. Omitting it from a learner projection requires an
  explicit R4/Q13 exception or an output-visible flagged representation, as well
  as the corresponding REL-01 completeness ruling.

**Where the answer lands.** `docs/aegis-restructure.md` Q13/§12,
`docs/spec-step8.md` T10-2 and repair log, and
`docs/release-qc-checklist.md`.

### DEP-01 — durable record for direct-deposit-only advisory findings

**Context.** A finding first created during direct-deposit cleanup currently
survives only as a per-finding run log; the copied row flags die at the database
boundary. Round 9 deliberately records this as owner-owed rather than solved
(`docs/spec-step8.md:5031-5041`; `docs/release-qc-checklist.md:141-145,158-160`).

**Exact question.** What durable record, if any, must a direct-deposit-only
advisory finding leave?

**Options.**

- **A — Persist a structured job/deposit issue ledger.** Internal deposits gain
  release-grade auditability, with new storage and lifecycle rules.
- **B — Accept per-finding logs.** Record a deliberate exception for this
  non-release tool; no durable artifact is promised.
- **C — Route or retire direct deposit in favour of the release path.** The
  special record contract disappears, but the internal workflow changes.

**Where the answer lands.** `docs/aegis-restructure.md` §12 and the “Recorded
limits/Open with the owner” sections of `docs/release-qc-checklist.md`.

### ID-01 — machine-ID uniqueness scope and enforcement boundary (T9-4)

**Context.** T9-4 specifies a mint-time persisted-column check scoped to
`(board, grade, subject)`. S11 delivered a chapter-and-lane scan at publication
and calls the broader scope undelivered and owner-visible
(`docs/spec-step8.md:1622-1625,5002-5011`).

**Exact question.** What is the authoritative scope and boundary for concept
machine-ID uniqueness?

**Options.**

- **A — `(board, grade, subject)` at mint.** Cross-chapter collisions refuse
  before persistence; legacy collisions need a migration policy.
- **B — Chapter and lane at publication.** Keep the current gate and amend
  T9-4; equal IDs outside one released lane remain possible.
- **C — Database-global uniqueness.** Establish one namespace and remediate
  every legacy collision; valid reuse across otherwise separate scopes is also
  prohibited.

**Where the answer lands.** `docs/spec-step8.md` T9-4/Round 9,
`docs/aegis-restructure.md` §12, and the blocking-owner description in
`docs/release-qc-checklist.md`.

## D. QC ownership and visibility (6)

### QC-01 — whether a flagged whole task block satisfies R4

**Context.** `task_blocks_left_unruled` says a whole task block shipped but
independent questions inside may be absent. R4 requires each learner-visible
question to end Placed or Flagged. The open seam is whether the whole-block flag
accounts for unknown internal identities (`docs/release-qc-checklist.md:162-175`;
`docs/review-notes-s11-qc-checklist.md:300-305`).

**Exact question.** Is flagging the whole task block sufficient R4 accounting,
or must a model first identify and account for each internal question?

**Options.**

- **A — Whole-block flag is sufficient.** Keep the current warning; internal
  questions remain unnamed but the source block is visible.
- **B — Require an internal-question verdict.** Each found question gains an
  identity and placement/flag state; this adds model work and durable ledger
  entries.
- **C — Move the obligation to question extraction.** Declare this not a
  release-QC item and make the extraction cutover own the internal inventory.

**Where the answer lands.** `docs/release-qc-checklist.md`,
`docs/spec-question-extraction.md`, and `docs/aegis-restructure.md` §12/R4.

### ASSET-01 — structural asset checks versus operational durability

**Context.** Q8 already decides current hosting and requires durable public
links, backup, content hashes and manifests. What remains open is which missing
facts are structural publication blockers now and which are Step-10 operational
advice (`docs/aegis-restructure.md:878-886`;
`docs/release-qc-checklist.md:162-175`).

**Exact question.** Which asset properties belong to release QC, and which stay
exclusively with Step 10?

**Options.**

- **A — Split by integrity versus operations.** Release QC blocks missing hash,
  manifest or referenced bytes; Step 10 owns retention, backup and future URL
  durability.
- **B — Receipt-only release QC.** S11 verifies that Step 10 produced a receipt;
  Step 10 owns all asset enforcement.
- **C — Exclude assets from S11.** All asset reporting and refusal remain in
  Step 10, and the release checklist contains no asset item.

**Where the answer lands.** `docs/release-qc-checklist.md`, the Step-10 asset
spec, and a Q8 amendment in `docs/aegis-restructure.md` §12.

### LANG-01 — language-mode receipt ownership

**Context.** Pipeline Step 11 owns the poem/prose adapter and semantic
conformance; Step-8 S11 release QC must not recreate it with deterministic
content rules. The remaining boundary is whether release QC verifies only the
mode/adapter receipt or also reports a model-authored conformance verdict
(`docs/release-qc-checklist.md:166-167`;
`docs/review-notes-s11-qc-checklist.md:313-317`).

**Exact question.** What must release QC verify for a language-mode release?

**Options.**

- **A — Receipt-only QC.** Release QC would verify the recorded mode and adapter
  execution; pipeline Step 11 would own all semantic conformance and its flags.
- **B — Receipt plus semantic-verdict visibility.** Require pipeline Step 11 to
  record an explicit conformance verdict; release QC verifies and carries its
  receipt and any dissent without re-deciding it.
- **C — Exclude language conformance from release QC.** Pipeline Step 11 alone
  owns both execution and review evidence; Step-8 S11 has no language-specific
  item.

**Where the answer lands.** `docs/aegis-restructure.md:265-282` and §12,
`docs/spec-step11.md`, and the language row in
`docs/release-qc-checklist.md`.

### LANG-02 — referent of the poem “non-overlap across the three” rule

**Context.** §4 says poem “elements covered must not coincide across the
three,” but does not identify three stable objects for stanzas of arbitrary
length. The Step-11 draft interprets the phrase as three teaching bodies—the
meaning unit, language-craft unit and stanza culmination—but no owner ruling
ratifies that interpretation (`docs/review-notes-s11-qc-checklist.md:371-374`;
`docs/spec-step11.md:121-133`).

**Exact question.** What are the “three” bodies whose teaching content must not
overlap?

**Options.**

- **A — Meaning, craft and culmination.** Ratify the Step-11 draft's
  interpretation and leave non-overlap as a model-authored verdict.
- **B — Supply a different referent.** Replace the draft's interpretation with
  the owner's three named bodies; the model, never a count rule, judges it.
- **C — Delete or replace the sentence.** Remove the ambiguous obligation and
  state the intended semantic distinction directly.

**Where the answer lands.** `docs/aegis-restructure.md:267-271` and §12, plus
`docs/spec-step11.md:121-133`.

### VAL-01 — visibility of validator codes outside `_FATAL_CODES`

**Context.** Error-severity validator findings such as `forbidden_name`,
`placeholder` and `repeated_sibling_opener` can drive repair yet are not in the
fatal family and do not automatically reach a row flag or release issue.
Severity cannot determine their publication polarity
(`docs/release-qc-checklist.md:168-175`;
`docs/review-notes-s11-qc-checklist.md:366-369`).

**Exact question.** Which non-fatal-family validator findings must be durable
and reviewer-visible after repair?

**Options.**

- **A — Carry every validator finding.** Visibility is comprehensive, at the
  cost of a larger and potentially noisy issue ledger.
- **B — Define a literal visibility allow-list.** The review surface is stable,
  but each addition requires an explicit contract change.
- **C — Keep them repair/run-log-only.** Preserve current behaviour and record
  that unresolved findings in this family have no release receipt.

**Where the answer lands.** The validator table in
`docs/release-qc-checklist.md`, T10/S11 in `docs/spec-step8.md`, and
`docs/aegis-restructure.md` §12.

### METH-01 — durable record for uncovered worked-method anchors

**Context.** Generation says an uncovered worked-method anchor “ships as a
review item,” but the live record is only a progress log; no row flag or release
issue consumes it. This is a producer-with-no-durable-reader seam
(`docs/release-qc-checklist.md:168-175`;
`docs/review-notes-s11-qc-checklist.md:84-87`).

**Exact question.** What durable reviewer record must an uncovered worked-method
anchor create?

**Options.**

- **A — Row review flag.** Attach the anchor to its resolved concept; unresolved
  ownership still needs a release-level fallback.
- **B — Release issue keyed to the anchor.** Preserve the finding even when no
  concept home resolves.
- **C — Accept the progress log.** Amend the “ships as a review item” claim and
  make no durable-review promise.

**Where the answer lands.** `docs/release-qc-checklist.md`, the Q13/R4 register
in `docs/aegis-restructure.md` §12, and the worked-method receipt clause in
`docs/spec-step8.md`.

## E. Open publication polarities (6)

Display severity is not a gate. These six families are explicitly left open at
`docs/release-qc-checklist.md:162-175` and `docs/spec-step8.md:5068-5073`.

### POL-01 — `example_qid_missing`

**Context.** A Type/Case Example can render without a QID and therefore has no
identity route. The T9 closed set does not currently classify that shape.

**Exact question.** What publication consequence applies to an Example with no
QID?

**Options.**

- **A — Block.** Treat every such Example as unaddressable identity corruption.
- **B — Flag.** Ship the visibly unaddressed Example for semantic repair.
- **C — Split by obligation.** Block when a known inventory identity was lost;
  otherwise flag an authored/generated shell.

**Where the answer lands.** The T9 closed set in `docs/spec-step8.md`, the code
polarity table in `docs/release-qc-checklist.md`, and
`docs/aegis-restructure.md` §12.

### POL-02 — `type_title_missing` and `type_cases_missing`

**Context.** Both conditions produce issues but no structural reader. A missing
title can affect addressability; a missing Case collection can also represent
authored taxonomy incompleteness or a legitimate Type-only shape.

**Exact question.** Which, if either, of a missing Type title and a missing Case
collection blocks publication?

**Options.**

- **A — Both block.** Incomplete Type structure makes the release Diagnostic.
- **B — Both flag.** Publish with taxonomy-review issues.
- **C — Split the pair.** State separately which condition is structural and
  which is advisory, including the valid Type-only representation.

**Where the answer lands.** T9/T10 in `docs/spec-step8.md`, the Type/Case table
in `docs/release-qc-checklist.md`, and `docs/aegis-restructure.md` §12.

### POL-03 — `pre_learning_questions_refused`

**Context.** This issue records that the generated Pre question artifact was
refused, but it does not enter the Pre payload's structural `refused` field.
The Concept release can therefore remain publishable while its question output
is unavailable.

**Exact question.** May the Pre Concept release publish when its generated
question artifact was refused?

**Options.**

- **A — Block the Pre database write.** The lane remains Diagnostic until its
  question artifact is available.
- **B — Keep it advisory.** Publish the concepts with a visible missing-question
  record.
- **C — Split the outputs.** Mark only the Pre question/Master lane unavailable
  while preserving the separate Concept lane and an explicit manifest receipt.

**Where the answer lands.** The Pre release policy in `docs/spec-step8.md`, the
Pre rows in `docs/release-qc-checklist.md`, and
`docs/aegis-restructure.md` §12.

### POL-04 — duplicate learner-analysis item IDs

**Context.** Misconception and Error Analysis items use stable IDs. Duplicate
IDs can collapse before exact-once accounting, but T9's closed identity set does
not currently name them.

**Exact question.** What publication consequence applies when two learner-
analysis items share one ID?

**Options.**

- **A — Block.** Treat the collision as identity corruption; downloads remain
  available.
- **B — Flag while preserving both.** Permit publication only if both colliding
  items remain visibly distinct in evidence.
- **C — Block only proven loss.** Let B3 exact-once accounting decide when a
  collision actually erases an item; otherwise flag.

**Where the answer lands.** T9 in `docs/spec-step8.md`, the learner-analysis row
in `docs/release-qc-checklist.md`, and Q1/§12 in
`docs/aegis-restructure.md`.

### POL-05 — internal group-key form

**Context.** §4 specifies `(<ConceptID>) BG##/IG##/AG##`, while live lanes carry
multiple key bases. Blank, duplicate and broken-home keys already block; textual
format alone has no settled polarity.

**Exact question.** Is one internal group-key form canonical across all lanes?

**Options.**

- **A — Enforce the §4 form for new releases.** Version and migrate/alias legacy
  keys; noncanonical new keys become structural.
- **B — Bless opaque persisted keys.** Amend §4; only blank, duplicate or broken
  joins block.
- **C — Permit lane/version-specific grammars.** Each manifest declares its
  grammar, and format alone never blocks.

**Where the answer lands.** The group identity section and T9 in
`docs/spec-step8.md`, Q12/§12 in `docs/aegis-restructure.md`, and the group row in
`docs/release-qc-checklist.md`.

### POL-06 — missing furniture-drop evidence

**Context.** R4 permits furniture to be dropped only when the ledger retains its
identity and verbatim content. No dedicated gate currently states what happens
when that evidence is missing.

**Exact question.** What is the publication consequence when furniture was
dropped but its required drop evidence is absent?

**Options.**

- **A — Block.** Treat missing verbatim drop evidence as unaccounted source loss.
- **B — Flag.** Publish with a provenance warning.
- **C — Use a conditional gate.** Block when a known source identity is
  unaccounted; otherwise flag the missing ledger evidence.

**Where the answer lands.** R4/§12 in `docs/aegis-restructure.md`, the coverage
contract in `docs/spec-step8.md`, and the furniture item in
`docs/release-qc-checklist.md`.

## F. Standing legacy-data decisions (3)

### R5-01 — durable authority for retired question labels

**Context.** The live allocator scans the highest label still present. Deleting
the highest label in a family therefore lets the next mint reuse it, contrary to
R5's rule that an uploaded label is never reassigned. The case is deliberately
and strictly xfailed at `backend/tests/test_label_collision_surface.py:763-799`;
the limit is documented at `backend/app/services/identity.py:489-507`.

**Exact question.** What durable authority remembers every uploaded question
label and its later retirement?

**Options.**

- **A — Append-only DB ledger/tombstones.** Record upload and retirement
  transactionally. Initialization and historical cutover are decided by
  R5-02.
- **B — Immutable release snapshots as ledger.** Scan live rows plus published
  releases; correctness depends on snapshot retention/completeness and scan
  cost.
- **C — Soft-delete Questions.** Preserve labels in the existing indexed table;
  every deletion path must enforce soft deletion.

**Where the answer lands.** R5 and a new §12 entry in
`docs/aegis-restructure.md`, T5-3's residue in `docs/spec-step8.md`, and the
chosen Step-9 audit or Step-10 publication ownership record. The strict xfail
remains the acceptance pin until the ruling is implemented.

### R5-02 — historical label backfill and cutover

**Context.** Selecting a durable authority for future uploads does not recover
labels already deleted from the top of a family. Live Questions and retained
release history can prove some prior labels, but missing historical evidence
cannot prove that a number was never used. R5's effective start therefore needs
its own ruling.

**Exact question.** How is the retired-label authority initialised, and from
what point is the no-reassignment guarantee absolute?

**Options.**

- **A — Evidence-complete backfill.** Import live Questions, retained releases
  and backups; prevent new labels in any unresolved family until its history is
  reviewed. This seeks an absolute historical guarantee but can block minting.
- **B — Named-date cutover.** Seed each family from the best evidence available
  and guarantee R5 only for labels uploaded on or after a recorded date; older
  unknown reuse is grandfathered.
- **C — Owner-approved family migration.** Export each legacy family's known
  history and require an approved initial high-water/tombstone record before it
  can mint again. This avoids a global cutover but requires manual decisions.

**Where the answer lands.** The same R5/§12 and T5-3 registers as R5-01, plus a
dedicated migration record naming the evidence sources, unresolved families and
effective guarantee date. The strict xfail remains until both R5 decisions are
implemented.

### LEGACY-01 — cleanup of persisted volume-derived chapter metadata

**Context.** S8 removed formulas that generated chapter descriptions and
durations from concept counts, but pre-S8 values remain stored. The parser takes
the first digit run from any stored duration string containing a decimal digit;
that value is then treated as final and wins over both the curated lookup and a
new provider duration. An old description remains whenever no fresh description
arrives. There is no durable provenance distinguishing a coincident
human-authored duration from the retired formula (`docs/spec-step8.md:2005-2015`;
`backend/app/services/build_concepts.py:1052-1084,1226-1248`;
`backend/app/services/generation.py:18857-18868`).

**Exact question.** How should pre-S8 chapter descriptions/durations that may
have come from volume formulas be identified and cleaned without silently
deleting genuine authored metadata?

**Options.**

- **A — Grandfather all persisted values.** No legitimate edit is erased, but
  potentially Rule-1-derived metadata remains learner-visible.
- **B — Proof-only cleanup.** Clear or re-author only rows whose origin can be
  demonstrated from history; indistinguishable residue remains.
- **C — Signature/formula detection, only with a Rule-1 amendment.** Clean
  broadly by value shape; this option is unavailable under the current doctrine
  because a regex/threshold would stand in for provenance and may erase
  legitimate values.
- **D — Re-author every pre-cutover chapter.** Use a live provider for
  descriptions and a live provider or curated authority for durations, with
  before/after records; this costs model spend and may overwrite human edits.
- **E — Owner-reviewed migration list.** Export candidates and migrate only
  approved rows; this minimizes automated deletion risk but requires manual
  review.

**Where the answer lands.** A Rule-1/legacy-data entry in
`docs/aegis-restructure.md` §12, the S8 repair log in `docs/spec-step8.md`, and a
dedicated migration record if cleanup is authorised.

## Close protocol

An entry closes only when the owner selects an option or supplies replacement
wording, the answer is copied into the listed canonical register(s), and any
affected acceptance pin is named. Implementing code without that recorded
answer does not close the decision.
