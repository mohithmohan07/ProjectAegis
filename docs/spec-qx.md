# QX — model-verdict question extraction for text sources (implementation spec)

Status: IMPLEMENTED (build-sprint mode, owner-directed: no audit rounds; one debug pass at sprint end). QX1+QX2 landed as specced; QX3 landed lean (records + provenance consumers; the legacy-extractor re-polarization and naming-truth renames are residues R-QX1/R-QX5 in docs/residue-ledger.md). Live acceptance (§6) remains owner-gated (R-QX3). Evidence head:
`d5cfafe` (Step-8 S11). Inputs: the Surface-1 code map (session artifact,
re-verified line by line at d5cfafe) and the review stream's reconciled
`docs/spec-question-extraction.md` (`origin/review/specs-and-audit` @
`11333a9`) — read as CLAIMS; every claim this spec builds on was confirmed
against the branch code, and the map's empirical repro (a Marathi cue task
plus an unlabelled English imperative compile to a 0-task ledger with zero
blocking issues, no model call, no record) is the defect being repaired.

## 1. The defect, in one sentence

On the `.mmd`/`.md`/`.txt` lane, task membership — whether a learner-visible
ask exists at all — is decided by finite English regex vocabularies and
numeric bounds (`generation.py:4359-4876,5259-5843`;
`canonical_source_phase21_structure.py:16-29,74-96,279-392,944-1039`), a
direct Rule-1 violation with a demonstrated silent-loss class (24→0, PR
208/211; reproduced at d5cfafe), while the PDF lane already holds the correct
authority shape (verified per-block model verdicts,
`canonical_source_phase221_fallback.py`).

## 2. The decision

Task membership for text sources becomes a **closed-world, per-block model
verdict** adjudicated at the Phase-2 compile seam, recorded in a durable
content-addressed ledger, reconciled deterministically into
`canonical["tasks"]`. Deterministic parsing (anchors, Phase-2.1 cue recovery)
is demoted to **candidate and span evidence** presented to the author — it
nominates, it never decides. `inventory_from_canonical` stays a renderer.

The seam is the compile chain, not the mid-run wrapper, because the ledger
(`canonical["tasks"]`) is the source of truth every downstream contract
reads (the compat guard requires the inventory to equal a fresh render of
the ledger — `canonical_source_phase2_compat.py:246-274`; resume refresh
re-renders from the ledger — `canonical_source_phase2_contract.py:194-226`).
Membership must therefore be decided where the ledger is built.

New layer, following the house contract convention:

* `canonical_source_phase212.py` — Phase 2.1.2, semantic task-membership
  adjudication (author, critic, Fixer, validation, reconciliation,
  accounting, cache).
* `canonical_source_phase212_contract.py` — installs in
  `services/__init__.py` between `_install_canonical_source_phase211_contract`
  and `_install_canonical_source_phase22_contract`, so at runtime its
  compile wrapper runs inside phase22's and outside phase21's: it receives
  the hardened candidate ledger and returns the adjudicated one.

## 3. Activation matrix (who spends, when)

| Situation | Behaviour |
|---|---|
| Shadow compile (`consumer_module != "build_concepts"`) | No adjudication, no spend. Authority recorded as `shadow_unadjudicated`. |
| Active compile, PDF-lane bundle (a model membership authority already present: `chapter_outline` version / page-ledger provenance) | No re-adjudication. Authority recorded as `gpt_page_ledger`. Lane parity is contract parity, not double spend. |
| Active compile, text lane, cache hit | Replay the sealed ledger; zero provider calls; identical tasks. |
| Active compile, text lane, no cache, provider ready | Live adjudication at compile (conversion-time spend, exactly the PDF lane's timing). Ledger sealed to the durable cache. |
| Active compile, text lane, no cache, provider unavailable | Compile SUCCEEDS but the bundle is marked unadjudicated: parser/phase-2.1 tasks remain visible as candidates, `phase2_inventory_ready` is False, and a blocking issue `task_membership_unadjudicated` (severity error, message naming the provider) joins `phase2_inventory_issues`. Generation fails closed pre-spend (existing `prepare_job_context` validation). This is the named genuine impossibility — never a silent parser fallback. |
| Resume / `_load_or_refresh_for_job` on a parser-era artifact | The load wrapper treats an active text-lane canonical without a valid verdict ledger as stale and recompiles (adjudicating live or from cache). This is the checkpoint re-key: a bundle authored under parser authority is never reused as if a model had judged it. |

Conversion (`convert_job`) already tolerates a failed Phase-2 preparation
with the "blocked pending source review" warning path — an upload with no
provider converts fine and blocks at the pre-spend gate, which is the
existing owner-approved pause posture.

## 4. The decision contract

### 4.1 Evidence given to the author

For every non-`layout` block of `canonical["blocks"]` (`BLK-#####`, kind,
`source_start/source_end`, `raw_text` — `canonical_source.py:320-335`):
block id, kind, verbatim text, section title, and reading-order neighbours.
Plus the full candidate set: every task in the hardened ledger (parser +
Phase-2.1 recovery), each with a candidate id (`CAND-####` in ledger order),
its span, prompt text, and provenance (`source_kind`, recovery flags).
`layout` blocks are whitespace-only; they are excluded mechanically and
listed in the accounting as `layout_no_content` — an exclusion of no
content is parsing, not judgment.

### 4.2 Author verdict schema (strict, per block)

Every listed block MUST receive exactly one verdict:

* `contains_tasks` — with `confirmed_candidate_ids` (subset of candidates
  overlapping this block), `rejected_candidate_ids`, and `missed_asks`
  (asks no candidate covers: verbatim `evidence_text` quoted from the
  block, plus a stable `task_ref` so a multi-block ask groups).
* `task_continuation` — this block continues an ask begun earlier;
  carries the `task_ref`/candidate it extends (the model-ruled analogue of
  Phase-2.1's follow-up recovery).
* `task_context` — narrative/stimulus a task depends on; carries the refs
  it supports. Recorded; never an inventory row.
* `not_task` — exposition; the explicit "no questions here" record.
* `uncertain` — the honesty valve; routes to the Fixer (§4.4).

A candidate confirmed by no block and rejected by no block is a coverage
defect of the payload (mechanical), not an implicit verdict.

### 4.3 Passes and polarity

1. **Author** (batched strict-schema calls; batching is transport only and
   is windowed with overlapping neighbour context — batch size never
   decides membership). Transport: `phase22._openai_multimodal_json` with
   `pages=[]` (the existing bounded, gated, usage-tracked strict-schema
   call — `canonical_source_phase22.py:613`).
2. **Deterministic validation** (mechanics): every block covered exactly
   once, verdicts in-enum, refs resolvable, `evidence_text` locatable
   verbatim in its block, candidate rulings complete. Invalid → **one
   bounded correction** re-ask carrying the defect list (the PDF lane's
   correction pattern, `canonical_source_phase221_fallback.py:1791-1821`).
   Still invalid after correction → each unresolved block/candidate goes
   to the **Fixer** (§4.4).
3. **Critic** (independent pass over the complete accounting: all
   verdicts, the reconciled task list, the not-task record). Dissent is
   transcribed to durable review flags on the affected occurrences and to
   the ledger — one flag, no retry, no gate, no halt (Q10).
4. **Fixer** — for any block or candidate still without an applicable
   verdict (author returned `uncertain`, or validation could not resolve
   it after the bounded correction): one recorded model call with the full
   block + candidate context; its best-judgment verdict is recorded,
   content-addressed, and the occurrence ships flagged
   (`qx_fixer_decided`). The run completes (Q13). If the Fixer call itself
   fails on provider grounds, the compile ends unadjudicated exactly as in
   the activation matrix — a named impossibility, pre-spend for
   generation.

Semantic doubt therefore NEVER halts and NEVER silently restores parser
authority; only provider unavailability stops, and it stops loudly at a
pre-spend boundary.

### 4.4 Reconciliation (deterministic, mechanics only)

* Confirmed candidates → tasks, granularity preserved, provenance kept,
  `membership_authority: "model_verdict"`, ruling refs recorded.
* `missed_asks` → tasks created directly from the block evidence (span =
  located evidence; the PDF lane's direct-creation precedent,
  `fallback:2845-2874`), `origin: "qx_model_missed_ask"`.
* `task_continuation` → the referenced task's wording/span extended in
  block order (never a separate inventory row — the never-split rule is
  unchanged).
* Rejected candidates → removed from the ledger and recorded as
  `candidate_ruled_not_task` with their ruling (the text-lane analogue of
  `gpt_pdf_acsd_discarded_parser_tasks` — R4: dropped and listed as
  dropped, with what they said).
* Ordering, `TASK-#####`/`QINV-####` renumbering, identity keys: the
  existing mechanics (`structure.renumber_tasks`,
  `_augment_canonical_tasks` invariants) — qid shape and the
  source-ordered-sequence gate (`phase2.py:336-343`) are unchanged.
* `aegis_mmd`, `statistics.tasks`, `source_contract.task_count`, report
  summary: recomputed exactly as phase21's hardening does.

### 4.5 The ledger and the cache (decide once)

`canonical["task_verdict_ledger"]`: version, provider/model identity,
prompt+schema hashes, per-block verdicts, candidate rulings, correction
history, critic report, Fixer decisions, review flags, accounting summary
(counts by verdict kind, `layout_no_content` list, zero-unaccounted
proof). Durable cache keyed by
`QX_VERSION ⊕ compiler ⊕ model ⊕ source_sha256 ⊕ block-id+sha sequence ⊕
prompt/schema hashes` (the `_batch_cache_key` pattern,
`fallback:591-611`), sealed only when adjudication completed; replay
requires zero provider calls and yields byte-identical tasks. A lost
cache re-adjudicates; a changed item set on resume is already handled by
the existing paid reconciliation + certification reset
(`generation.py:19930-19949`). Architect-instruction identity slots join
the key when step 11 lands (recorded as future work, not silently
omitted).

### 4.6 Identity boundaries (S10)

Source QIDs stay occurrence identities; concept `machine_id`s stay
persisted-home identities. QX never derives one from the other, never
reuses S10's title fallback for membership, and never remints an existing
concept because a source QID changed. Frozen releases are never rewritten;
the new contract scopes to new/re-staged runs through the normal
supersede path.

## 5. Slices

### QX1 — the adjudication module (no wiring)

`canonical_source_phase212.py` + `backend/tests/test_canonical_source_phase212.py`.
Scripted-provider regressions (each proven red first by neutralising):

1. closed-world coverage — a scripted author omitting one block id fails
   validation, is corrected once, and an still-unruled block reaches the
   Fixer, never silence;
2. the Marathi + unlabelled-imperative repro compiles to a 2-task ledger
   under a scripted author that confirms no candidates and reports both
   as missed asks — the 24→0 class is dead;
3. rejected candidate → `candidate_ruled_not_task` record, task gone,
   nothing silent;
4. critic dissent → exactly one durable flag, no second author call, no
   halt;
5. `uncertain` → one Fixer record, flagged occurrence, completed
   adjudication;
6. provider failure (author or Fixer) → named unadjudicated result, no
   parser-authored ledger claimed as semantic;
7. cache replay → zero calls, byte-identical tasks and ledger;
8. batching cannot change membership (same verdicts split across batches
   reconcile identically);
9. evidence_text not locatable → correction → Fixer, never a guessed
   span;
10. `layout` exclusion is recorded, and every non-layout block appears in
    the accounting exactly once.

### QX2 — the compile-seam cutover

`canonical_source_phase212_contract.py`, installed per §2; issue-reader
wrapper adding `task_membership_unadjudicated`; load wrapper re-keying
parser-era artifacts; provenance truth (`extraction_provenance` gains
`task_membership_authority`, ledger counts; the wrapper's misleading
"deterministically…no inventory-extraction model call is required" log
and `_refresh_inventory_from_source_anchors` naming corrected to say what
they now render from); test migration:

* `test_canonical_source_phase2.py:260-288` re-authored: the render step
  still makes no inventory call (true and correct), and new tests prove
  the author IS called on a live active text compile and NOT called on
  replay;
* compile-path tests that assert `phase2_inventory_ready` gain the
  scripted author (adopt-all-candidates double) or pin the unadjudicated
  posture explicitly;
* direct `_source_task_anchors` pins (corpus counts, ~30 anchor tests)
  survive as candidate-surface pins with comments saying so — they assert
  what the parser NOMINATES, no longer what the book MEANS.

### QX3 — polarity and record closure behind the cutover

* The legacy extractor's post-spend semantic raises split by mechanism
  (`generation.py:6935-6939,7458-7466,7516-7535`): semantic doubt →
  recorded flag/Fixer continuation; mechanical corruption → truthful
  structural-defect refusal (a gate refusing a broken artifact stays
  deterministic and allowed).
* `_store_inventory`'s empty-items early return
  (`build_concepts.py:1255-1273`) no longer discards the accounting: a
  zero-item adjudicated chapter stores its provenance ("no questions
  here" is now distinguishable from "not read" all the way to the job
  record).
* Release staging provenance: `chapter_outline_not_applied`
  (`build_concepts_release.py:1171-1183`) stands down when the verdict
  ledger is the recorded authority; the ledger's flags surface through
  the existing release-QC advisory transcription.

## 6. Validation limits, stated honestly

Everything above is provable with scripted providers EXCEPT semantic
quality on real books. Live acceptance (GPT-spec QX4: real multilingual
chapters, occurrence-parity PDF/text comparison, reviewer inspection of
every verdict) requires a live provider this environment does not have —
it is recorded as owed, blocking step-11 acceptance, and no claim of
real-book validation is made by these slices.

## 7. Open with the owner

* Author/critic/Fixer prompt wording sign-off before first live spend.
* Whether shadow-lane compiles should ever adjudicate (this spec says
  never — no spend outside build_concepts).
* Cutover for already-persisted parser-era jobs beyond the automatic
  re-key on next resume (bulk re-adjudication is a paid operation the
  owner schedules).
