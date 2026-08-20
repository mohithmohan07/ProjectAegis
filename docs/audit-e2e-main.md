# End-to-end adversarial audit — `main@865915b`

Audit date: 2026-08-19

Audited commit: `865915b2a55661da5a27e3033b5c2859895b0849`

Disposition: **NO-GO for a live deployment**

This is the missing post-sprint adversarial review. It treats every existing
specification, map, test name, docstring, and residue entry as a claim to check
against the merged code. No test suite, test file, or product command was run.
All findings below are static traces against the named commit. `CONFIRMED`
means the complete producer/caller/consumer path was read; `SUSPECTED` would
mean a runtime check is still needed. There are no suspected findings in this
report.

## Executive result

The merge itself is intact. The deploy block is in the merged behavior, not in
lost merge hunks. Five defects meet the project's own BLOCKER definition:

1. a QX membership change writes mutually inconsistent Phase-2.1 seals and the
   next load deterministically fails;
2. the language plan can influence later concept decisions but cannot create
   or rename the stanza/story topics it records;
3. QX records task-context and explicit visual dependencies without carrying
   them into the task a learner receives;
4. normalized visible titles are still used as identity, silently deleting or
   misrouting distinct teaching; and
5. a hash-shaped asset is served without checking that its bytes have that
   hash, under a one-year immutable cache header.

The remaining HIGH findings include evidence-starved QX review, a paid-work
discard path, stale plan replay, mutable publication-blocking authority,
post-spend PDF crop halts, and unauthenticated “sealed” PDF content. These are
not style findings. Each has a concrete state/input below.

## Pass 1 — merge integrity

### Result: clean

The merged graph is the expected lane union:

| Lane | PR head | Main merge |
|---|---:|---:|
| Step 10 / PR #230 | `1455072` | first parent of `11d1a7d` |
| Step 8 / PR #229 | `902536a` | `11d1a7d` |
| Step 12 / PR #231 | `aa43f9e` | `e2e64ca` |
| Review docs / PR #232 | `11333a9` | `90b6164` |
| Frontend rename / PR #225 | `4bff1f8` | `830d107` |
| Environment documentation | — | `865915b` |

All five PR heads are ancestors of `865915b`. Re-merge comparison found no
manual merge delta, and the final commit changes only `.env.example`.
Path-by-path comparison of each lane's introduced files produced these results:

- Step 8 introduced 29 paths. All are present; only
  `docs/residue-ledger.md` differs from the PR head, because Step 12 appended
  its R-S12 rows.
- Step 10's four introduced paths, Step 12's twelve, and PR #232's five are
  byte-identical to their PR heads.
- PR #225 modified `frontend/src/api/client.ts` and
  `frontend/src/pages/BuildAssessments.tsx`. The latter is byte-identical to the
  PR head. The former retains the school-agnostic rename and additionally
  carries Step 8's required `lane` argument and converged-flow removal of the
  separate Pre routes. That is the intended union, not a lost rename hunk.

`.gitignore` retains the Step-10 durable-store and PDF-cache exclusions. The
missing new QX/language cache exclusions are a merged product omission,
recorded as Finding 20, not a lost merge hunk.

Commands used:

```bash
git log --merges -6 --oneline 865915b
git show --no-patch --format='%H %P %s' 11d1a7d e2e64ca 90b6164 830d107 865915b
git merge-base --is-ancestor 902536a 865915b
git merge-base --is-ancestor 1455072 865915b
git merge-base --is-ancestor aa43f9e 865915b
git merge-base --is-ancestor 11333a9 865915b
git merge-base --is-ancestor 4bff1f8 865915b
git show --remerge-diff --stat 11d1a7d e2e64ca 90b6164 830d107
git diff --name-status 865915b^ 865915b
git diff --diff-filter=A --name-only 11d1a7d^1 11d1a7d
git diff 902536a 865915b -- $(git diff --diff-filter=A --name-only 11d1a7d^1 11d1a7d)
git diff 1455072 865915b -- backend/app/services/source_asset_store.py backend/tests/test_data_reset_durable_assets.py docs/map-step10.md docs/spec-step10.md
git diff aa43f9e 865915b -- backend/tests/acceptance_corpus backend/tests/test_acceptance_corpus.py backend/tests/test_acceptance_corpus_pdf.py backend/tests/test_fault_injection.py docs/map-step12.md docs/spec-step12.md
git diff 11333a9 865915b -- docs/map-step11.md docs/review-notes-s11-qc-checklist.md docs/spec-question-extraction.md docs/spec-step11.md docs/spec-step8-s11-corrections.md
git diff 4bff1f8 865915b -- frontend/src/api/client.ts frontend/src/pages/BuildAssessments.tsx
```

## Findings

### Finding 1 [BLOCKER] [CONFIRMED] — count-changing QX output is not loadable

**Where:**

- `backend/app/services/canonical_source_phase212.py:861-864`
- `backend/app/services/canonical_source_phase21_structure.py:639-657`
- `backend/app/services/canonical_source_phase212_contract.py:145-168`
- `backend/app/services/canonical_source_phase21.py:83-94`
- `backend/app/services/canonical_source_phase21_contract.py:71-98`

**Defect:** QX rematerializes the canonical task ledger and updates the
canonical Phase-2.1 hardening counts, but its compile wrapper never refreshes
the matching `report["phase21_hardening"]` seal. The persisted-artifact reader
requires those two markers to match, recompiles once, produces the same
mismatch, and raises.

**Failure scenario:** the parser finds no task in a Marathi block; QX correctly
recovers one missed ask. `parent_task_count` changes from 0 to 1 in the
canonical marker while the report remains 0. Conversion can write the artifact,
but the next `prepare_job_context`/generation load raises “Phase 2.1 source
hardening did not produce a complete leaf inventory artifact.” Rejecting a
parser candidate can trigger the same fault in the opposite direction.

The suite-wide QX echo author at `backend/tests/conftest.py:36-89` preserves the
parser's membership and therefore masks this exact count-changing path.

**Suggested fix:** after QX reconciliation, call one shared Phase-2.1
recalculation that rebuilds the canonical marker, the report marker, issue
counts, source-contract counts, summary counts, and readiness from the same
post-QX ledger. Add create-one/reject-one persisted-artifact reload tests; do
not patch only one count field.

**How to confirm:**

```bash
git show 865915b:backend/app/services/canonical_source_phase212_contract.py | nl -ba | sed -n '145,168p'
git show 865915b:backend/app/services/canonical_source_phase21.py | nl -ba | sed -n '83,94p'
git show 865915b:backend/app/services/canonical_source_phase21_contract.py | nl -ba | sed -n '71,98p'
```

### Finding 2 [BLOCKER] [CONFIRMED] — the language plan cannot realize its topic topology

**Where:**

- `backend/app/services/build_concepts.py:4358-4399`
- `backend/app/services/generation.py:2503-2539`
- `backend/app/services/canonical_source_phase3_contract.py:160-175`
- `backend/app/services/phase3/prompts.py:584-663`
- `backend/app/services/phase3/settle.py:85-90,654-715,790-808`
- `backend/tests/test_language_topology.py:281-297`

**Defect:** the serialized plan reaches a late Phase-3 instruction suffix and
can influence keep/refine/split wording, but the semantic graph and pre-freeze
skeleton never receive its plan topics or stable plan IDs. Settle iterates only
already-existing graph topics and preserves their `topic_id`; it cannot create
or rename the topics recorded by the language plan. No production consumer
reads `plan_topic_id`, `plan_concept_id`, `semantic_role`, or
`threaded_components` structurally.

This is narrower than “the plan does nothing”: the prompt suffix is live. It is
still a blocker because Step 11's core output is the topic topology, not merely
advice about concept wording.

**Failure scenario:** a poem's general graph has one chapter-level topic. The
language author records `Stanza 1`, `Stanza 2`, and `Detailed Analysis of 'The
Brook'`. Later concept decisions can read those words, but every row remains
under the one pre-existing graph topic. The learner never receives the
model-authored stanza topology that the sealed plan and composed hash claim.

**Suggested fix:** apply the plan before graph/skeleton freeze. Materialize plan
topics and concepts with stable IDs and evidence; carry those IDs and semantic
roles through graph, skeleton, Settle, Host, release, and publication. Add an
end-to-end production-wrapper test whose source graph headings deliberately
differ from the plan topics and assert the final topic/ID sequence, not merely
that plan JSON appears in a prompt.

**How to confirm:**

```bash
git grep -n -E 'plan_topic_id|plan_concept_id|semantic_role|threaded_components' 865915b -- backend/app
git show 865915b:backend/app/services/canonical_source_phase3_contract.py | nl -ba | sed -n '160,175p'
git show 865915b:backend/app/services/phase3/settle.py | nl -ba | sed -n '654,715p;790,808p'
```

### Finding 3 [BLOCKER] [CONFIRMED] — QX drops required task context and visual ownership

**Where:**

- `backend/app/services/canonical_source_phase212.py:239-241,633-693,826-837,915-926,1394-1402`
- `backend/app/services/canonical_source_phase2.py:386-407`

**Defect:** `task_context` is reduced to a dead accounting entry containing
only IDs; its block text, tables, objects, and visuals are never attached to
the target task. A newly recovered task also extracts explicit figure IDs but
sets `requires_visual=False`, clears all unresolved references, and records the
omission only in a ledger `recorded_limits` string that no production reader
consumes.

**Failure scenarios:**

1. A data table is ruled `task_context` for a separate “Calculate …” task. The
   question enters the inventory without the table it depends on.
2. QX recovers “Using Figure 3, explain …”. The task carries the explicit
   reference ID but no URL, no required/unresolved-visual state, and no release
   warning, so the learner receives an incomplete ask.

An empty `context_for_task_refs` is also accepted for a `task_context` verdict.
`git grep task_context_links` and `git grep
qx_created_tasks_skip_visual_ownership_pass` each find only their producer.

**Suggested fix:** resolve each context reference to a task occurrence and
mechanically attach verbatim context text, content objects, figures, and visual
relationships. Require at least one resolvable target for `task_context`.
Re-run the Phase-2.1 visual ownership pass over all QX-created tasks, then carry
any unresolved relationship as a per-QID release flag with a real consumer.

**How to confirm:**

```bash
git grep -n 'task_context_links\|qx_created_tasks_skip_visual_ownership_pass' 865915b -- backend/app
git show 865915b:backend/app/services/canonical_source_phase212.py | nl -ba | sed -n '633,693p;826,837p;1394,1402p'
```

### Finding 4 [BLOCKER] [CONFIRMED] — visible title shape still deletes and misroutes teaching

**Where:**

- `backend/app/services/generation.py:16677-16751`
- `backend/app/services/generation.py:11415-11440,18397-18412,18651-18671,18817-18829`
- `backend/app/services/generation.py:12743-12755,12834-12858`
- `backend/app/services/concept_validator.py:1640-1649`
- `backend/tests/test_chapter_topic_quality.py:768-784`
- `backend/tests/test_concept_mapping_reviews.py:144-156`

**Defect:** normalized `(topic, visible title)` is treated as semantic identity.
Two production dedupers silently discard one row, carrying only review flags
rather than content, source evidence, QIDs, or plan identity. Other recovery
and restructure paths still key titles chapter-wide: a legitimate cross-topic
addition is suppressed, or duplicate titles become last-write-wins and both
rows are assigned to one topic. The validator independently declares a
topic/title pair to be one identity.

**Failure scenarios:**

- Two positionally distinct `Stanza 1 / Courage` concepts teach private fear
  from lines 1–2 and collective resistance from lines 3–4. The second row's
  learner-visible explanation and evidence disappear.
- `Stanza 1 / Courage` and `Stanza 2 / Courage` both reach topic restructure.
  `topic_by_title` retains only the last response; both input rows receive that
  topic and the subsequent topic-scoped dedupe deletes one.
- A recovery model correctly authors an opening `Courage` concept, but a later
  stanza already has that title; the chapter-wide `existing_titles` set drops
  the model verdict.

The tests conflict: `test_chapter_topic_quality.py:768-784` explicitly expects
same-topic content to disappear, while
`test_concept_mapping_reviews.py:144-156` says exact duplicates are never
silently dropped but exercises only the non-mutating validator.

**Suggested fix:** remove every visible-title deletion and title-only response
join. Carry stable plan/concept IDs through model payloads. Mechanical dedupe
may use only a proven stable identity. Suspected semantic duplication goes to
a model verdict; any merge must be lossless over details, evidence, QIDs,
flags, and provenance. A legacy response without an ID should produce an
explicit unresolved mapping, not a last-write-wins update.

**How to confirm:**

```bash
git show 865915b:backend/app/services/generation.py | nl -ba | sed -n '16677,16751p;18397,18412p;18817,18829p'
git show 865915b:backend/tests/test_chapter_topic_quality.py | nl -ba | sed -n '768,784p'
```

### Finding 5 [BLOCKER] [CONFIRMED] — content-addressed assets are served without content verification

**Where:**

- `backend/app/api/source_assets.py:29-82`
- `backend/app/services/source_asset_store.py:94-119`
- `backend/app/services/canonical_source_phase221_fallback.py:2230-2240`
- `backend/tests/test_data_reset_durable_assets.py:377-391`

**Defect:** the public route checks only filename shape. It prefers any
job-local file and returns it without hashing; when the store is absent it can
notice a mismatch during opportunistic pinning but still returns those wrong
bytes. When the store exists it does not hash the job copy at all. Store
fallback bytes are also returned without hashing, and boot skips an asset when
the asset and sidecar merely exist. The response adds
`Cache-Control: public, max-age=31536000, immutable`.

**Failure scenario:** a partial restore or volume corruption places `evil` or
damaged JPEG bytes at `<sha-of-good>.jpg`. The route serves those bytes under
the good content identity and instructs clients to cache them for a year. A
correct durable copy can be shadowed by the corrupt job copy. The committed
test explicitly creates a mismatched file and asserts HTTP 200.

**Suggested fix:** hash candidate job and store bytes before every serve and
require equality with the filename stem. Prefer a verified job copy; if the
store is verified and job is not, heal or quarantine the job copy and serve the
store. If neither verifies, log a named integrity loss and return 404. The boot
sweep must digest-check before its existence shortcut. Keep the one canonical
filename regex; that part is sound.

**How to confirm:**

```bash
git show 865915b:backend/app/api/source_assets.py | nl -ba | sed -n '26,82p'
git show 865915b:backend/tests/test_data_reset_durable_assets.py | nl -ba | sed -n '377,391p'
```

### Finding 6 [HIGH] [CONFIRMED] — QX review is evidence-starved and its item flags die downstream

**Where:**

- `backend/app/services/canonical_source_phase212.py:253-267,1037-1059,1085-1115,1248-1294`
- `backend/app/services/canonical_source_phase2.py:937-945`
- `backend/app/services/build_concepts_release.py:1230-1275`

**Defect:** the critic is instructed to catch asks wrongly ruled `not_task`,
but receives verdict objects, accounting, and surviving task prompts—not the
source blocks. The Fixer is promised the block and its neighbours, but receives
only the block and candidates whose overlap list contains it; a defect
attributed through `nearest_block_id` excludes that candidate from the Fixer
payload. Finally, per-task `_acsd_review_flags` have no production reader, yet
release prose says affected occurrences carry flags. Aggregate QX provenance
does survive and produces aggregate release issues at
`build_concepts_release.py:1230-1275`; what is missing is the claimed
occurrence-level attribution.

**Failure scenarios:**

- The author wrongly marks an uncued Marathi imperative `not_task`; the critic
  sees no words and cannot dissent.
- A standalone “Why?” continuation reaches the Fixer without its preceding
  stem; it records `not_task` from incomplete evidence.
- A Fixer rejects a candidate, so no task occurrence survives on which to put
  `qx_fixer_decided`; release records only a count and falsely says the affected
  occurrence is flagged.

**Suggested fix:** give the critic every source block/candidate payload, and
give the Fixer previous/next blocks, nearest candidates, and declared task
references/verdicts. Add a real release-QC transcription for per-QID flags and
for rejected block/candidate decisions. Use truthful conditional receipt text.

**How to confirm:**

```bash
git show 865915b:backend/app/services/canonical_source_phase212.py | nl -ba | sed -n '253,267p;1037,1059p;1085,1115p'
git grep -n _acsd_review_flags 865915b -- backend/app
```

### Finding 7 [HIGH] [CONFIRMED] — one QX defect discards successful batches through an unbounded correction

**Where:** `backend/app/services/canonical_source_phase212.py:1191-1232`

**Defect:** block authoring is batched, but any mechanical defect constructs
one correction request containing every source block, every candidate, and a
strict schema enumerating the whole chapter. If that request exceeds provider
context/output capacity, the function immediately returns `unadjudicated`;
none of the successful batch verdicts reaches the Fixer.

**Failure scenario:** two 40-block calls succeed and only one verdict is
omitted. The combined correction is larger than either bounded author call and
exceeds the provider window. All already-paid work is discarded and the active
run blocks pre-spend on its next stage rather than repairing the one block.

**Suggested fix:** create bounded correction windows from the defect's block
and candidate identities, merge only those patches, and send each remaining
unresolved block to the Fixer with complete local context. Persist successful
batch decisions before correction.

**How to confirm:**

```bash
git show 865915b:backend/app/services/canonical_source_phase212.py | nl -ba | sed -n '1191,1232p'
```

### Finding 8 [HIGH] [CONFIRMED] — language-plan replay is not bound to current meaning and one cache seal is decorative

**Where:**

- `backend/app/services/language_topology.py:321-445,452-462,573-582,716-746`
- `backend/app/services/build_concepts.py:4393-4399`
- `backend/app/services/canonical_source_phase212_contract.py:145-150`

**Defect:** the decision key binds raw source, original Architect hash, mode,
adapter/model/prompt identity, but omits work name, canonical compiler/contract
identity, block inventory identity, and adjudicated task inventory identity.
The job artifact path checks key plus self-hash but never validates the plan
against current blocks/tasks/work name. The global cache path runs shape checks
but never recomputes `plan_sha256` or validates the embedded identity fields.

**Failure scenarios:**

- A QX policy change recovers a new QID from unchanged bytes. The prior plan
  artifact replays without the task, and its hash authenticates the new run.
- If work metadata changes while the original instruction hash remains
  reusable, an old `Detailed Analysis of 'The Brook'` can replay for the new
  target because `work_name` is not part of the plan key or artifact check.
- Valid JSON under the right cache filename has edited plan text but retains
  the old `plan_sha256`; the edited slot text is used while the composed run
  hash stays unchanged.

**Suggested fix:** build one `validate_sealed_plan()` used by both cache and
artifact replay. Bind normalized work metadata, canonical compiler/contract,
lossless ordered block identity, and current task/QID identity; recompute every
embedded hash; then run `plan_defects` against the current bundle and work
name. Invalid material is a cache miss/re-author, never a replay.

**How to confirm:**

```bash
git show 865915b:backend/app/services/language_topology.py | nl -ba | sed -n '431,462p;573,582p;716,746p'
```

### Finding 9 [HIGH] [CONFIRMED] — frozen publication does not seal its blocking authority

**Where:**

- `backend/app/services/assessment_release_snapshot.py:70-93`
- `backend/app/services/build_concepts_release.py:963-992`
- `backend/app/services/build_concepts_release_publication.py:177-204`

**Defect:** the source-release seal omits both `issues` and
`qc_blocking_defects`. `structural_defects` recomputes T9 identity issues only
when the `issues` key is absent; a present empty/stale list wins. It trusts
`qc_blocking_defects` verbatim. Publication reads those mutable fields and then
checks the incomplete seal. After one successful publication, the
`summary.database_uploaded` latch returns even earlier, before either gate or
the seal.

**Failure scenarios:**

- Freeze a release whose sealed inventory/type material implies
  `duplicate_qid_assignment`, replace `issues` with `[]`, and retain all sealed
  fields. The identity gate and seal both pass.
- Freeze `coverage_unaccounted`, clear only `qc_blocking_defects`, and publish.
- After a successful upload, mutate records or strip lineage and retry; the
  early latch returns a success receipt for the current staged payload without
  validating or applying it.

Publication independently rechecks row projection shape, so this does not
reopen the old `staged_row_defects` row-drop seam. It still voids the new S11
identity/QC authorities.

**Suggested fix:** unconditionally union pure recomputed T9 defects. Recompute
pure QC blockers at publication or include a canonical gate-authority section
in the seal. Keep deliberately post-freeze advisory issues outside that
section. Move seal/gate validation before idempotency return and bind the
durable publication receipt to the validated canonical payload hash.

**How to confirm:**

```bash
git show 865915b:backend/app/services/assessment_release_snapshot.py | nl -ba | sed -n '70,93p'
git show 865915b:backend/app/services/build_concepts_release.py | nl -ba | sed -n '963,992p'
git show 865915b:backend/app/services/build_concepts_release_publication.py | nl -ba | sed -n '177,204p'
```

### Finding 10 [HIGH] [CONFIRMED] — one degenerate crop still aborts and poisons a PDF retry

**Where:**

- `backend/app/services/canonical_source_phase221_fallback.py:2116-2189,3528-3560`
- `backend/tests/test_fault_injection.py:254-305`

**Defect:** `_clip_bbox` raises when a crop is under 8×8 points and
`materialize_visual_assets` has no per-figure boundary around crop/rendering.
The call occurs after the paid, verified page bundle. The sealed bundle replays
on retry and deterministically reaches the same exception. Only the later
durable-store pin has flag-and-continue handling.

**Failure scenario:** the page model returns one slightly degenerate figure
bbox among otherwise valid pages/tasks. Conversion aborts after full model
spend; the retry reuses the bundle and aborts identically; no evidence release
or per-figure flag is produced. The fault-injection test explicitly pins this
violation as current behavior.

**Suggested fix:** catch failures per figure. Preserve figure identity, bbox,
caption, page evidence, and a named materialization flag; continue materializing
all other assets and the source ledger. Do not invent an asset URL. Flip the
test to assert completed conversion plus visible defect.

### Finding 11 [HIGH] [CONFIRMED] — a well-formed edit to the sealed PDF cache becomes verified source

**Where:**

- `backend/app/services/canonical_source_phase221_fallback.py:1917-1945,2092-2112`
- `backend/tests/test_fault_injection.py:358-407`

**Defect:** sealed-bundle reuse checks only the embedded PDF hash and page
count. The cached result has no verified body digest, and the reader does not
re-run schema/source verification. The test edits block text while preserving
those two scalars and asserts that the altered source is replayed verbatim.

**Failure scenario:** a partial restore, disk bit error that leaves valid JSON,
or local cache tamper changes a source paragraph without changing page count or
the decorative `pdf_sha256`. The altered words bypass both author and verifier
and become canonical textbook source.

**Suggested fix:** store and verify a canonical result digest (or authenticated
seal if hostile local writes are in scope), validate the full page/block schema,
IDs, ordering, and PDF identity before replay, and treat any mismatch as a
cache miss requiring re-extraction. The test should assert rejection/rebill,
not trusted tamper.

### Finding 12 [MEDIUM] [CONFIRMED] — failure-release topology selection counts rows, not authored allotments

**Where:** `backend/app/services/build_concepts_release.py:1807-1827,1830-1892`

**Defect:** `_analysis_allotment_count` counts rows carrying at least one
allotment marker, then selects an entire cached topology by the larger row
count. It does not count distinct authored allotment identities or use the
artifact's recorded completeness. Shape of row partitioning therefore decides
which learner topology ships.

**Failure scenario:** the current validated topology has one merged row carrying
`[LA-1, LA-2]` and scores 1. A stale split cache has two rows, each carrying one
ID, and scores 2. A failure release replaces the current topology with the
stale one solely because it is split into more dicts.

**Suggested fix:** compare exact sets of recorded allotment identities plus an
artifact-authored completeness receipt under the same source/decision
contract. If neither proves strict completeness, keep the current rows and
record the ambiguity; never use row count as a semantic preference.

### Finding 13 [MEDIUM] [CONFIRMED] — the PDF lane runs QX before installing its exemption

**Where:**

- `backend/app/services/canonical_source_phase221_fallback.py:3540-3560`
- `backend/app/services/canonical_source_phase212_contract.py:31-34,97-115`
- `backend/app/services/canonical_source_phase2.py:714-726`

**Defect:** PDF reconstruction compiles rendered MMD before attaching the
already-derived chapter outline. QX exemption requires that outline, so an
uncached PDF runs a redundant QX author/critic pass. Page-ACSD relationships
then replace task relationships, while QX ledger/provenance stamps remain and
can be reported as the membership authority.

**Failure scenario:** a fresh PDF spends on page authorship/verification and
then again on text-lane QX. The later page ledger owns the final tasks, but the
release can say QX authored membership and retain irrelevant QX decisions.

**Suggested fix:** attach page-ledger authority before any Phase-2 compile that
can enter QX, or pass an explicit source-authority token to the compile
contract. Clear intermediate QX stamps if a page ledger supersedes them.

### Finding 14 [MEDIUM] [CONFIRMED] — language-plan receipts record decisions that did not occur

**Where:** `backend/app/services/language_topology.py:608-678`

**Defect:** after a correction, the Fixer receives the newly recomputed defect
list, but its durable `reason` records the author's initial defects. A critic
provider failure is serialized as `verdict: concur` even while an adjacent flag
says that review was unavailable.

**Failure scenario:** correction changes “unknown block” into “missing
mastery”; the Fixer handles missing mastery, but the durable reason says unknown
block. A provider outage then leaves a stored critic concurrence for a critic
decision that never happened.

**Suggested fix:** append each validation round, record the exact defects sent
to the Fixer, and use an `unavailable`/null critic verdict rather than
`concur`. Whether the plan must cover every current QID, and which parent/topic
evidence overlap is legitimate, are contract questions rather than confirmed
defects: `docs/spec-step11.md:93` says `task_qids` are tasks relevant to a
concept and leaves final placement downstream.

### Finding 15 [MEDIUM] [CONFIRMED] — QX cache identity omits actual author inputs

**Where:** `backend/app/services/canonical_source_phase212.py:368-424,941-955,1156-1179`

**Defect:** the sealed-ledger key covers source/block/candidate text identities
and selected systems, but omits block kind, section association/title,
candidate provenance/mapping, the batch-layout policy that assigns neighbouring
blocks as before/after context, Phase-2 compiler/schema identity, rendered
instruction text, and the Fixer schema. Those values affect the provider's
actual evidence and response contract.

**Failure scenario:** a compiler upgrade changes a block's section association
or kind without changing raw source/block hashes. A mechanically valid old
`not_task` ruling replays even though the author prompt and semantic context
would now differ.

**Suggested fix:** hash the exact ordered provider payloads and every system,
schema, batching policy, and canonical compiler/contract version used to
produce them. Validate the embedded identity on read.

### Finding 16 [MEDIUM] [CONFIRMED] — identical repeated evidence cannot identify two task occurrences

**Where:**

- `backend/app/services/canonical_source_phase212.py:270-305,431-435,648-672`
- `backend/app/services/canonical_source_phase21_structure.py:1174-1179`

**Defect:** the author schema permits two missed asks with distinct refs but
identical evidence text. `_locate_evidence` always selects the first occurrence,
so both tasks receive the same source span. Renumbering hashes section, start,
and prompt, producing duplicate identities that only a later structural gate
detects.

**Failure scenario:** one block prints `Explain. … Explain.` as two separate
asks and the author returns both exact quotes. The valid chapter is blocked for
duplicate task identity.

**Suggested fix:** require a source occurrence offset or an enclosing unique
evidence span in the model result, and validate it against the exact source
slice before minting identity.

### Finding 17 [MEDIUM] [CONFIRMED] — Step-12 tests do not pin several claims in their own docstrings/spec

**Where:**

- `backend/tests/test_fault_injection.py:151-188,218-247`
- `backend/tests/test_acceptance_corpus.py:162-234`
- `backend/tests/acceptance_corpus/sources/icse10_english_poem.mmd:33-46`
- `backend/tests/acceptance_corpus/sources/cbse7_english_prose.mmd:44-57`
- `backend/tests/test_acceptance_corpus_pdf.py:82-124`
- `backend/tests/conftest.py:36-89`
- `docs/spec-step12.md:27-30,104-124`

**Defect:** the quota-containment test says the checkpoint is retained but
never asserts a checkpoint. Its “free resume after quota death” first completes
and seals the whole bundle, then proves only ordinary full-bundle replay; no
partial batch ever dies. Both English fixtures print six asks but the QX
demonstration requires only four. The cross-page figure task is scripted with
`linked_visual_orders=[]` and the test separately checks task text and asset
existence, never the task-to-figure relationship. Finally, a suite-wide echo
author patches every test despite the spec's “zero patching” claim.

**Failure scenario:** partial-batch retry re-bills completed work, two of six
English asks disappear, or the physics question loses its required figure; all
named Step-12 tests still pass.

**Suggested fix:** make at least two PDF batches, succeed then quota-fail, assert
the persisted partial checkpoint and provider calls on retry; assert exact six
occurrences; script and assert the canonical figure link by stable ID; and
state the suite-wide authority honestly or scope it away from acceptance tests.

### Finding 18 [MEDIUM] [CONFIRMED] — repeated-question comparison is a lossy semantic classifier

**Where:** `backend/app/services/build_concepts_release.py:1594-1661`

**Defect:** punctuation/marker stripping plus casefolded `\w` shape is treated as
proof that two QIDs are the same learner question. Python `\w` excludes Unicode
combining marks, so Devanagari vowel signs and virama are deleted. The result is
advisory rather than blocking, but it is still a false model-free semantic
claim and can obscure real release issues.

**Failure scenario:** `की मात्रा क्या है?` and `क मात्रा क्या है?` normalize to
the same key and produce a false `repeated_question_text` warning. The existing
Devanagari test repeats the exact same string and cannot expose the collision.

**Suggested fix:** use exact byte/codepoint identity only for a mechanical
duplicate warning. Any “same wording despite punctuation/case/numbering” claim
is a model verdict and must remain advisory with source strings preserved.

### Finding 19 [MEDIUM] [CONFIRMED] — the volume-derived metadata purge leaves persisted outputs authoritative

**Where:**

- `backend/app/services/build_concepts.py:1052-1084,1226-1248`
- `backend/app/services/generation.py:18855-18880`

**Defect:** the formula producers are gone, but any existing string containing
a decimal digit is parsed as a finalized duration. That value wins over the
curated lookup and over a fresh provider duration, then is normalized and
persisted again. An old formula-generated description also survives whenever
no fresh model description arrives. The code therefore calls the class purged
while shipping its pre-cutover output with no provenance.

**Failure scenario:** an old five-concept chapter retains `60 minutes` and the
formula description. A new provider authors 95 minutes, but `finalized=60`
overrides it. Dry/provider-failure paths preserve both retired outputs.

**Suggested fix:** make the owner decision in `docs/owner-decisions-open.md` and
record a migration. Under Rule 1, do not infer provenance from a regex/formula
match. Use proven historical provenance, explicit owner review, or live
re-authoring while preserving before/after evidence.

### Finding 20 [LOW] [CONFIRMED] — operational documentation, code contracts, and runtime exclusions drifted

**Where:**

- `.env.example:6-28,159-165`
- `backend/app/config.py:101-110`
- `backend/aegis_pipeline/openai_policy.py:84-105`
- `.gitignore:42-71`
- `backend/app/services/canonical_source_phase212.py:62`
- `backend/app/services/language_topology.py:56`
- `backend/app/services/release_qc.py:3-8`
- `backend/app/services/build_concepts_release.py:2137-2142,2224-2227`

**Defect:** `.env.example` says QX/Step 11 require `OPENAI_API_KEY`, while code
also accepts `GEMINI_API_KEY` and reads `AEGIS_GEMINI_MODEL` and Gemini pricing
variables that the example omits. It says every request asks for `max`
reasoning, while the policy intentionally uses low/medium/high for several
purposes. `.gitignore` omits the new `qx-task-verdict-cache/` and
`language-plan-cache/` runtime directories (and the older
`source-adjudication-cache/`). The S11 QC module and staging comment still tell
callers that QC blockers ride `snapshot_defects`; production correctly writes
and reads the separate `qc_blocking_defects` field. Following the stale contract
would recreate the false “input snapshot unreadable” provenance S11 repaired.

**Failure scenario:** a valid Gemini-only operator follows the example and
believes the deployment is unsupported, or accidentally commits model-verdict
caches from a local run. Operational expectations for reasoning/cost are also
wrong.

**Suggested fix:** document both providers and their model/pricing variables,
describe purpose-specific reasoning truthfully, and ignore all runtime cache
directories under `backend/data/` by exact names. Correct the QC module/staging
comments to name `qc_blocking_defects` and reserve `snapshot_defects` for
genuinely unreadable input snapshots.

## Doctrine sweep summary

- **Rule 1 violation:** Findings 4 and 18 use normalized visible wording as a
  semantic identity/equivalence verdict. Finding 12 uses row partition count to
  choose a learner topology.
- **Q10:** no new critic was found gating a run. QX's critic is advisory, but
  Finding 6 shows that it lacks the evidence needed to perform the advertised
  audit and its occurrence flags have no consumer.
- **Q13:** Findings 7 and 10 discard paid work or halt after a recoverable local
  defect rather than record one Fixer/flagged decision and complete.
- **R4:** Findings 3 and 4 are direct silent learner-visible loss; Findings 2
  and 6 create dead coverage/provenance receipts. Finding 14 is false decision
  provenance rather than a demonstrated learner-visible drop.
- **Mechanical thresholds:** the 8×8 crop check is a physical renderability
  test, not a meaning judgment; the defect is its whole-run consequence. QX
  batch/token limits are transport controls; the defect is the unbounded
  correction that defeats them.

## Pass 4 — residue-ledger verification

| Row | Verdict at `865915b` |
|---|---|
| R-QX1 | Accurate: the legacy API body remains production-rebound; direct/offline callers can still reach it. |
| R-QX2 | Description accurate; “flagged, downstream safe” false. The flag is a dead `recorded_limits` string and the visual can be absent from the learner task (Finding 3). |
| R-QX3 | Accurate: no committed real-book/live-provider acceptance record. It remains a production-signoff block. |
| R-QX4 | Limitation accurate; safety overstated. The task kind affects routing and `_acsd_review_flags` has no consumer (Finding 6). |
| R-QX5 | Accurate naming debt. |
| R-QX6 | Accurate, and more consequential than stated: the echo author masks the count-changing reload blocker and is active in acceptance tests. |
| R-QX7 | Accurate comment debt. |
| R-QX8 | Inaccurate consequence. A first author transport failure returns before correction/Fixer, and one defect can create an unbounded whole-chapter correction (Finding 7). |
| R-S11a | Core limitation accurate; “downstream safe” false. Roles have no consumer and plan topics are not materialized (Finding 2). |
| R-S11b | **Closed/stale.** Outputs 02/04 now compose titles through `identity.titled`, carry machine IDs, validate/read back by identity, and have a production snapshot→both workbooks→re-import regression. The remaining direct-deposit title join is elsewhere (`build_concepts.py:552-567,995-1035`). |
| R-S11c | Whole-chapter/no-batching part accurate. Its “grammar-threading exact-once is schema-checked” claim is not established by the validator, but the intended multiplicity and allowed topic-summary/child overlap remain contract-ambiguous; this audit does not promote that ambiguity to a defect. |
| R-S11d | Accurate: scripted mechanics only; no committed live literary acceptance. |
| R-S12a | Accurate: Phase-2.2 cache-corruption injection remains omitted. |
| R-S12b | Accurate: the autonomous missing-asset flag twin is not separately pinned. |
| R-S12c | Accurate: synthetic corpus, no real-book semantic signoff. |
| R-S12d | **Closed/obsolete.** The merge order has happened and main contains both lanes. |

Material residue-class defects missing from the ledger include Findings 1, 2,
3's dead context transport, 4, 5, 7, 8, 9, 10, 11, 12, and 13. The ledger's
opening claim that none of its rows is foundation-false is itself no longer
safe for R-QX2/R-QX4/R-S11a; R-S11c needs narrower, owner-ratified wording.

## Pass 5 — deploy-and-run readiness

### Decision: NO-GO

The immediate no-go reasons are Findings 1–5. A text chapter whose QX verdict
changes membership can become permanently unloadable; literary mode cannot
realize its required topics; QX can ship questions without their stimulus or
figure; title normalization deletes content; and the public asset endpoint can
serve wrong immutable bytes.

The deployment scaffolding itself was otherwise coherent:

- `fly.toml` mounts `/data`; its DB URL, data directory, and public origin align
  with `config.py` and the Docker runtime override.
- The 30-day snapshot setting correctly warns that existing Fly volumes need
  an out-of-band `fly volumes update`.
- `Dockerfile` includes the required Open/Specific registry artifacts and
  serves the frontend from the same origin.
- authentication validation runs before DB/bootstrap work;
- contract installation order is Phase 2 → 2.1 → 2.1.1 → 2.1.2 → 2.2 and is
  fail-closed for unadjudicated text runs; and
- the boot asset backfill now isolates failures per crop/job and continues.

The boot backfill is not an integrity verifier, however: it skips an existing
asset+manifest pair without hashing (Finding 5). `.env.example` and ignore rules
need the low-severity corrections in Finding 20. A fresh production deployment
also still needs the normal out-of-repo secrets (`AEGIS_ADMIN_PASSWORD`, Google
client/session configuration, model-provider key, and preferably a dedicated
asset secret); that is configuration, not a code finding.

## Areas swept and found sound

- Main is the intended merge union; no PR-head file or merge hunk was found
  missing.
- QX corrected entries override originals; candidate contradictions, unruled
  candidates, and continuation references have mechanical checks; raw source
  byte changes invalidate the QX cache.
- The active text readiness chain carries `task_membership_unadjudicated` to a
  pre-spend refusal; parser-era artifacts are not silently accepted.
- QX added no regex/keyword/length rule that directly decides task membership.
- The composed Architect⊕language-plan instruction hash propagates consistently
  through checkpoint fingerprint/reuse, semantic graph context, envelope seal,
  kernel decision keys, and the pre-spend decision identities. No consumer was
  found re-deriving the old hash from the Architect artifact.
- The Architect's recorded mode is the language-adapter authority; no local
  rhyme/keyword/filename heuristic selects poem/prose.
- Chapter-wide `duplicate_title` production in `concept_validator` is retired;
  the remaining defect is title-keyed behavior elsewhere.
- Converged publication resolves concept identity in the intended order:
  carried machine ID, otherwise exactly one unclaimed normalized-title match
  inside the resolved topic, otherwise restore/create/mint.
- Output 02/04 identity composition and topic-scoped read-backs are repaired;
  R-S11b is not reproducible at main.
- The source-asset filename shape has one owner and no alternate public route
  bypasses it. Fresh minting derives names from bytes, and `pin_asset` heals a
  corrupt stored file when it is actually called.
- Step-10 boot backfill catches failures per crop and does not abandon later
  assets.
- No Step-8 lane was found minting or serving an alternate non-store asset URL.
- Critic dissent remains advisory in QX, language topology, and release QC.
