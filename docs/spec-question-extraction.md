# Question extraction: semantic inventory specification

Status: specification only; no implementation is included here.

Evidence basis: PR #229 at `d7d2e2f`, checked 19 August 2026. The
previously referenced `docs/map-question-extraction.md` was not present on
`main` or on PR #229 at the time of review, so every central claim below was
re-checked against production code and tests.

## 1. Decision

Question/task membership for `.mmd`, `.md`, and `.txt` inputs must become a
model verdict over the complete source-block ledger. Deterministic parsing may
continue to recover spans, labels, ordering, figures, and candidate boundaries,
but it must not decide whether a block is a learner task and must not be the
only way a task enters the inventory.

The repair belongs to a dedicated **Step 2 follow-up slice**: semantic
question-inventory extraction for text sources. It is not Step 8 release work,
and it must not be hidden inside Step 11's language adapter. It should land
before Step 11 because language chapters and non-English editions are among the
inputs most exposed to the current finite English cue vocabulary.

The recommended design is a closed-world, block-by-block author verdict plus
an advisory critic:

1. Present every canonical source block, in reading order, to the inventory
   author with stable block IDs and bounded neighbouring context.
2. Require a verdict for every block: `task`, `task_context`, `not_task`, or
   `part_of_task`, with the referenced task occurrence and source evidence.
3. Reconcile the verdicts mechanically into exact source spans. Assign stable
   QIDs, order, labels, and figure references only after the verdicts exist.
4. Run an independent critic over the complete accounting. Critic dissent is a
   review flag, never a gate (Q10).
5. If a semantic decision remains unresolved after the bounded correction,
   invoke the real Fixer with the complete block and decision context. Record
   its verdict, flag the affected item, and complete the run (Q13).
6. Prove exact-once accounting: every source block is either represented in an
   inventory item, explicitly attached as context, or explicitly ruled
   `not_task`. No parser miss may disappear without a model verdict (R4).

## 2. Verified current mechanism

The production call graph for text-shaped uploads is:

```mermaid
flowchart TD
    A[".mmd / .md / .txt"] --> B["mmd.to_mmd"]
    B --> C["Phase 2 ACSD compile"]
    C --> D["generation._source_task_anchors: initial tasks"]
    D --> E["initial ACSD task ledger"]
    E --> R["Phase 2.1 regex/cue recovery: added tasks and follow-ups"]
    R --> L["final canonical text-task ledger"]
    L --> F["inventory_from_canonical: one row per final text parent"]
    F --> G["optional API topic placement for chapter-wide items"]
```

The central facts are not inferred from names or docstrings. Re-measurement at
`d7d2e2f` preserves the doctrine finding but narrows two earlier absolutes:
the active wrapper can call a model for **topic placement** after membership
already exists, and the final inventory is not 1:1 with the **initial anchor
list** because Phase 2.1 can append parents that `_source_task_anchors` missed.
That recovery is itself driven by finite cue regexes rather than a closed-world
model verdict. On direct text, the final inventory does remain one row per
final canonical parent; model-ruled multi-row boundaries are PDF-only today.

| Claim | Code evidence | Consequence |
|---|---|---|
| Text uploads are read as text, without the PDF reader | `backend/app/services/mmd.py:28-47,51-75` | `.mmd`, `.md`, and `.txt` enter the ordinary MMD compiler. |
| Initial ACSD tasks originate in the deterministic anchor parser | `canonical_source.py:588-650`; `canonical_source_phase2.py:114-132` | The Phase 2 ledger starts from `generation._source_task_anchors`, but this is not its only deterministic membership pass. |
| Phase 2.1 can append tasks and follow-ups through more vocabularies | `canonical_source_phase21_structure.py:16-29,74-96,279-390,944-1039`; active calls at `canonical_source_phase21.py:97-116` | `_CUE_RE` can append a new parent task, while an English cue-heading plus `?`/imperative test can attach additional prompts. These are recorded and sometimes flagged, but no model made their task/not-task decision. |
| The production inventory extractor is rebound at import | `canonical_source_phase2_contract.py:27-44,134-192,258-267`; install order in `services/__init__.py:97-110` | During an active Phase 2 run, `_extract_question_task_inventory_via_api` calls `inventory_from_canonical` rather than its original extraction author. Lines `154-163` can still call the API to assign already-existing chapter-wide items to topics. |
| The no-author-call test is deliberately scoped | `backend/tests/test_canonical_source_phase2.py:260-288` | Its fixture has no chapter-wide item and forbids `_openai_json`; it proves the extraction-author bypass on that path, not that every inventory-shaped input makes zero provider calls. |
| Direct-text follow-up leaves do not create extra inventory rows | `_never_split_questions` at `canonical_source_phase2.py:660-664`; merge at `:717-807`; the only `gpt_boundary_parts` writer is `canonical_source_phase221_fallback.py:3102-3104` | Deterministic direct-text follow-ups are merged back into their parent row. Multiple model-ruled rows exist only on the PDF page-ledger lane at this commit. |
| Phase 3 only annotates that result | `canonical_source_phase3_contract.py:41-45,348-356,428-435` | The semantic graph does not discover parent tasks absent from the Phase 2 ledger. |
| PDF has a different recovery authority | `canonical_source_phase221_fallback.py:2790-2875,3330-3367` | A verified GPT page task can be created even when the parser found no candidate. |

The existing API extractor in `generation.py` still performs a model
extraction and completeness review, then merges deterministic anchors
(`generation.py:7370-7542`). Under the active Phase 2 contract, however, that
function body is not the production inventory author for a text upload. The
precise claim is therefore **no live model is the closed-world task-membership
author on the active text path**, not “the rebound function can never call an
API” and not “every final item is 1:1 with the **initial**
`_source_task_anchors` result.” Its
chapter-wide call at `canonical_source_phase2_contract.py:154-163` assigns a
topic to a task the ledger already supplied; Phase 2.1's extra membership
comes from deterministic cue recovery, not that API call.

The central rebind correction is reproducible without importing the service:

```bash
git show d7d2e2f:backend/app/services/canonical_source_phase2_contract.py | nl -ba | sed -n '27,44p;134,192p;258,267p'; git show d7d2e2f:backend/app/services/canonical_source_phase21.py | nl -ba | sed -n '97,120p'; git show d7d2e2f:backend/app/services/canonical_source_phase21_structure.py | nl -ba | sed -n '1,29p;74,96p;279,390p;944,1039p'; git show d7d2e2f:backend/app/services/generation.py | nl -ba | sed -n '7370,7542p'
git grep -n 'gpt_boundary_parts' d7d2e2f -- backend/app/services; git show d7d2e2f:backend/app/services/canonical_source_phase2.py | nl -ba | sed -n '660,664p;717,807p'
```

### 2.1 The semantic decisions currently made by patterns

The parser does more than recognize syntax:

- `_QUESTION_SENTENCE_RE` admits only named English interrogatives and
  auxiliary verbs and imposes `{8,800}` character bounds
  (`generation.py:4414-4421`).
- `_STANDALONE_CHECKPOINT_DIRECTIVE_RE` is an English imperative vocabulary
  (`generation.py:4434-4439`).
- `_CHECKPOINT_CONTAINER_HEADING_RE`, `_TASK_LIST_CONTAINER_RE`, and
  `_CHAPTER_WIDE_TASK_HEADING_RE` decide where task parsing is active from
  finite English heading vocabularies (`generation.py:4388-4413`).
- The headingless recovery lane adds more English phrases, stop words,
  content-specific token sets, and numeric text bounds
  (`generation.py:4593-4675,4757-4850`).
- `_source_task_anchors` uses those results to create the only tasks that reach
  the **initial** text-lane ACSD ledger (`generation.py:5259-5842`). It also
  contains English-only branches for activity/project headings and task
  scoping.
- Phase 2.1 then applies another English cue vocabulary. `_CUE_RE` appends a
  new task directly; `_TASK_CUE_HEADING_RE`, `_IMPERATIVE_RE`, and the presence
  of `?` decide whether sibling blocks become task follow-ups
  (`canonical_source_phase21_structure.py:16-29,74-96,279-390,944-1039`).

These are content-membership decisions under Rule 1, even where comments call
them structural gates. They decide whether a learner-visible ask exists at all.
For direct text, `inventory_from_canonical` merges deterministic follow-up
`leaf_cases` back into one row per final parent. Phase 2.1 can nevertheless
append cue-matched parents, so “final inventory is 1:1 with the initial anchor
list” is false. The narrower and load-bearing statement is that a source block
missed by **both deterministic membership passes** has no later closed-world
model authority that can add it. The only current `gpt_boundary_parts` writer
that can produce multiple model-ruled rows is the PDF fallback
(`canonical_source_phase221_fallback.py:3102-3104`).

Concrete loss cases include:

- `# विचार करा\nपाण्याचे महत्त्व स्पष्ट करा.` — an unnumbered Marathi task
  with neither an English container heading nor an English interrogative cue;
- `Compare the two accounts.` in an otherwise ordinary unlabelled paragraph —
  a single imperative ask outside a recognized container;
- a legitimate question outside all recognized container/cue recovery paths
  whose printed wording falls below 8 or above 800 characters after its cue;
  and
- a publisher-specific callout with no `?`, English task-list heading, or
  recognized `Activity`/`Project` label.

For PDFs, the code itself records the asymmetry: the fallback says the legacy
parser “recognises a finite cue vocabulary,” then creates a task directly from
the verified page ledger (`canonical_source_phase221_fallback.py:2845-2874`).
There is no equivalent authority for direct text uploads.

## 3. Invariants

The replacement must preserve these contracts:

1. **Model-owned membership.** Whether source content asks the learner to do
   something is always an author-model verdict. No regex, keyword list,
   punctuation check, text length, heading spelling, or count may include or
   exclude it.
2. **Closed-world evidence.** Every canonical block receives a verdict. A
   sampled or parser-nominated subset is insufficient because the parser's
   omissions are the defect being repaired.
3. **Source fidelity.** Inventory display text is recoverable from source spans
   and may be normalized only by already-approved lossless presentation rules.
4. **Whole questions.** A printed question with subparts remains one question,
   unless a model verdict explicitly identifies independent asks under the
   binding never-split policy.
5. **Exact once.** Each assessable source occurrence has one inventory identity.
   Repeated wording at two source locations remains two occurrences.
6. **Stable mechanics.** QID minting, source ordering, span addressing, figure
   attachment, caching, schema validation, and replay remain deterministic.
7. **No silent fallback.** Provider failure is genuine impossibility. A model
   failure must not silently reinstate parser ownership.
8. **Decide once.** Author, critic, correction, and Fixer records are
   content-addressed by source contract, source/block hashes, prompts,
   instruction-set hash, provider/model identity, and schema version.
9. **Critic advises.** A critic rejection adds a review flag and cannot erase
   or block the author's inventory.
10. **Lane parity.** PDF and text sources satisfy the same semantic inventory
    contract even if their evidence acquisition differs.

## 4. Options and costs

| Option | Provider cost | What it fixes | What still breaks | Verdict |
|---|---:|---|---|---|
| A. Keep the deterministic text parser | None for inventory | Nothing | Finite English cues and character bounds remain the sole membership authority; direct text and PDF disagree | Reject |
| B. Restore the legacy API extractor only | One or more author calls plus completeness calls per chapter | A model can discover asks outside parser cues | The existing chunk path still merges parser anchors as mandatory inclusions; its completeness flow can halt after spend, and it does not account for every block | Transitional only |
| C. Parser candidates plus a model “gap scan” | Author call over only unmatched prose | Recovers some misses cheaply | The parser still decides which source deserves review; false-negative regions remain invisible, so R4 is not proven | Reject as final design |
| D. Closed-world block verdicts, deterministic reconciliation | One author pass, one critic pass, bounded correction/Fixer only when needed; cacheable by block/source hash | Removes cue vocabulary and bounds from membership while retaining stable spans and IDs | Requires new decision artifacts, prompt/schema work, checkpoint invalidation, and live acceptance runs | **Adopt** |
| E. Reuse the PDF page-ledger implementation literally for every text file | Similar to D, with extra conversion abstractions | Creates one nominal reader | Text has no pages, bounding boxes, or OCR uncertainty; pretending it does increases surface area and loses native character spans | Reject; share the semantic contract, not the evidence format |

Option D can batch blocks to control cost. Batching is transport only: batch
size must not decide how many questions exist or where task boundaries fall.
Oversized blocks must be losslessly windowed with overlap/evidence addressing,
then reconciled by stable block/span identity.

## 5. Required re-authoring

This is a contract replacement, not a single call-site edit.

### 5.1 Source and inventory artifacts

- Re-author Phase 2 task construction so `_source_task_anchors` yields
  **candidates and mechanical source locations**, not the canonical task set.
  The same change must demote Phase 2.1's `recover_plain_task_cues`,
  `recover_followup_task_prompts`, and `is_task_like` from membership authority
  to candidate/span evidence; fixing only the first parser leaves the wider
  deterministic recovery vocabulary in charge.
- Add a text-source task-verdict ledger parallel in authority to the verified
  PDF page ledger. Each verdict must retain block IDs, source spans, task/context
  relationships, rationale, review flags, prompt/schema versions, and decision
  hash.
- Build `canonical["tasks"]` from verdicts plus deterministic reconciliation.
  `inventory_from_canonical` may remain a renderer after that change.
- Record explicit `not_task` verdicts or a compact content-addressed accounting
  artifact so “no questions here” is distinguishable from “not read.”
- Make extraction provenance state the actual author and critic mode; the
  current “deterministic ACSD task ledger” wording must retire for text runs.

### 5.2 Runtime wiring

- Replace the import-time Phase 2 rebinding contract that bypasses the
  extraction-author API. The production function name must describe what it
  does; keeping `_via_api` while the wrapper builds membership without that API
  is actively misleading even though chapter-wide topic placement may still
  make a later API call.
- Ensure Phase 3 consumes the adjudicated task ledger and cannot annotate a
  parser-only fallback as semantic extraction.
- Thread the Architect instruction identity and relevant language/board slots
  into decision keys and requests.
- Re-key question-inventory checkpoints. A checkpoint created under parser
  authority cannot be reused as if a model had judged it.
- Preserve Question Polishing as a later wording pass. It cannot repair an ask
  that never entered the inventory.
- Replace mid-run raises in extraction-completeness handling with the Fixer
  contract. Pre-spend source unreadability may still fail closed.

### 5.3 Tests and fixtures

Tests that treat `_source_task_anchors` as the oracle must be re-authored around
recorded model verdicts. In particular,
`test_active_generation_inventory_bypasses_model_extraction` must invert: a
live text-source run must prove the semantic author was called, while a replay
must prove the stored verdict is reused without a call.

Parser tests remain useful only for span recovery, list-label parsing, figure
attachment, and candidate provenance. They must not assert that the absence of
a regex match means the absence of a question. Phase 2.1 tests for
`recover_plain_task_cues` and `recover_followup_task_prompts` require the same
migration: cue matches may nominate exact spans, but cannot author membership.

## 6. Validation and live-provider requirement

A live provider is **required for acceptance**. Unit tests with scripted
providers can validate schema, accounting, QID stability, cache replay,
critic polarity, and Fixer transport, but they cannot establish that the new
semantic author understands real publisher variation.

The acceptance set must contain complete, real chapters rather than synthetic
one-line prompts:

- one `.mmd`, one `.md`, and one `.txt` source;
- English, Marathi, and Hindi learner asks;
- interrogatives, imperatives, activity instructions, reflective prompts,
  table/figure-dependent tasks, and publisher-specific callouts;
- asks without `?`, asks outside familiar headings, and long multipart asks;
- rhetorical questions that must be ruled `not_task`;
- identical wording at two source locations; and
- the same PDF and text transcription where available, compared for inventory
  occurrence parity rather than byte-identical representation.

For every run, reviewers must be able to inspect:

- all source-block verdicts;
- inventory items and their exact source occurrences;
- critic dissent and Fixer interventions;
- zero unaccounted blocks; and
- a no-spend replay producing the same inventory identities.

The validation report must name any claim that requires the broader staging
corpus. The full test suite is not evidence for semantic extraction quality.

## 7. Slice plan

1. **QX1 — contract and artifacts.** Define the block-verdict schema, decision
   keys, closed-world accounting, and text/PDF parity fields. Add scripted
   provider tests; do not change publication.
2. **QX2 — text author and critic.** Build the live text-source author, advisory
   critic, bounded correction, and real Fixer route. Keep parser output as
   evidence only.
3. **QX3 — cutover and checkpoint migration.** Replace the Phase 2 rebinding,
   re-key stale inventories, update provenance, and prove no silent fallback.
4. **QX4 — live acceptance.** Run the multilingual, multi-format chapter set,
   inspect artifacts manually, and record provider/model/prompt identities and
   costs.

QX1-QX3 are the missed Step 2 work. QX4 must finish before Step 11 is accepted.
Step 11 may consume the adjudicated task ledger but must not own or duplicate
question extraction.

## 8. Definition of done

- No production path uses a vocabulary, regex, punctuation form, or character
  bound to decide task membership.
- Direct text and PDF sources expose the same author/critic/accounting contract.
- Every source block is accounted for by a recorded verdict.
- Marathi and Hindi task prompts enter the inventory without adding their words
  to a vocabulary.
- Rhetorical questions can be excluded only by a recorded model verdict.
- A provider outage never causes parser-only generation; it stops only as a
  named genuine impossibility before semantic output is claimed.
- A resumed run replays the same decisions and QIDs without provider spend.
- Critic dissent and Fixer decisions reach the release review surface.
- Live acceptance on real chapters is recorded and reviewed.
