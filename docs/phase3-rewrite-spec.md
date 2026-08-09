# Phase 3 rewrite: four passes from the 81% boundary

**Status: approved direction, pre-implementation.** This document is the
contract for rewriting everything that runs after the `pre_type_assignment`
(81%) checkpoint. It replaces the Phase 3.1–3.11 sub-phase architecture
entirely. Nothing before 81% changes.

Companion documents: `build-concepts-manual-process.md` (the five-pass manual
mirror and the "Decide once" doctrine), `concept-placement-rules.md` (the
written placement rules), `concept-release-and-type-case-routing-rules.md`.

---

## 1. Why a rewrite, in one paragraph

The post-81% pipeline is 26,224 lines across 22 modules (`phase31`,
`phase32`, `phase33`, `phase331`, `phase332`, `phase333`, `phase341`,
`phase342`, `phase351`, `phase352`, `phase36`–`phase311`, …), wired together
by 20 `install()` monkeypatch hooks and 17 differently-shaped cache files.
Every production failure of the last month — the 17-replay loop of job 16,
the eight re-asks of TOPOLOGY-CONCEPT-0027 in job 20, the single-citation
instant death of job 23 — came from this half of the codebase, and each fix
found another copy of a validator or another unbounded raise because the same
interaction is hand-rolled a dozen times. Meanwhile everything before 81%
has reached its checkpoint cleanly on every run. The rewrite keeps the good
half, seals a formal boundary against it, and rebuilds the bad half as four
passes around a single decision kernel.

## 2. Scope

**Rewritten (deleted and replaced):** every model-facing step after the 81%
checkpoint — topology adjudication (3.2), source grounding (3.1), learner
analysis, Type/Case/QID host certification (3.3 + 3.31/3.32/3.33), Type
allocation and transport (3.4/3.41/3.42), provider-capacity and workbook
contracts (3.5/3.51/3.52), source-critical turnover (3.6), visual topology
convergence (3.7/3.71), boundary grounding turnover (3.8), post-freeze hub
convergence (3.9), terminal figure inventory (3.10), ACSD visual projection
(3.11), and the semantic-recovery replay machinery that services them.

**Untouched:** chapter reading, ACSD compilation, Phase 2.1/2.2 evidence
adjudication, semantic graph construction (hierarchy classification and
critic), skeleton extraction, canonicalization, descriptions, mastery lines,
deterministic question/task inventory, Type mining, and the three pre-81%
human-pause sites (source review, source-topic recovery, Type granularity).
Those pauses are the correct kind of human involvement: they fire before
money is spent, for problems only a human can fix (a broken source file).
`placement_policy` survives nearly as-is — it is already the written rulebook
the kernel enforces.

## 3. The boundary contract (the 81% envelope)

The new code consumes exactly one input: a sealed envelope materialized at
the 81% checkpoint. It never reaches back into live session state, active
graph contextvars, or generation internals.

```jsonc
{
  "envelope_version": 1,
  "source_contract_hash": "…",        // seals source identity
  "semantic_topology_sha256": "…",    // seals the graph
  "policy_version": "…",              // placement_policy.POLICY_VERSION
  "metadata": { subject, board, grade, unit, chapter, chapter_id, learning_kind },
  "graph":     { topics, subtopics, blocks },   // the verified semantic graph
  "canonical": { blocks },                      // display text + offsets
  "acsd_ledger": { qids, figures, hubs, tasks },// deterministic source inventory
  "skeleton_rows": [ … ],             // the 81% concept rows (title, parent,
                                      // details, keywords, topic)
  "inventory": { items, stats },      // QINV items incl. fragments (QINV-n.m)
  "mined_types": { types, cases }     // the granularity-audited taxonomy
}
```

Every field already exists at 81% — job 23's diagnostics zip *is* this
envelope in scattered form. Sealing it has two payoffs: the rewrite is
testable offline against recorded envelopes, and the boundary can never
drift silently (the envelope hash is part of every decision key).

## 4. The decision kernel

There is exactly one way the new code talks to a model. It is written once,
in one module, and every pass uses it.

```
decide(unit, payload_builder, checker, critic, applier):
    key = sha256(unit.identity + source_contract_hash + policy_version
                 + payload content hash)
    if key in decision_store:                    # resume = free
        return decision_store[key]
    feedback = []
    for attempt in 1..3:
        response = provider(payload_builder(unit, feedback))
        defects  = checker(response)             # MECHANICAL only:
                                                 #  schema, opaque IDs,
                                                 #  confidence >= 0.920,
                                                 #  protected QIDs survive,
                                                 #  evidence topic-locality
        if not defects: break
        feedback = defects
    else:
        raise ContractError(defects)             # run fails closed; release
                                                 # still ships the store
    flags = advisory(critic(response))           # NEVER blocking
    decision = Decision(response, review_flags=flags)
    decision_store[key] = decision               # immutable once written
    return applier(decision)
```

Doctrine (from `build-concepts-manual-process.md`, "Decide once"):

- **A decision is made exactly once.** Critic dissent, review-band
  confidence (0.900–0.919), unhonoured saved directives, prior deferrals —
  all become `review_flags` on the decided row. No escalation, no replay, no
  human, no resolution agent. The words `HumanDecisionRequired` and
  `semantic recovery` do not appear in the new code.
- **Mechanical defects get bounded corrections; nothing gets zero and
  nothing gets infinity.** Three attempts (two when replaying a saved
  pre-81% directive), then the run fails closed with the exact defect list.
  A run may fail; it may never wait.
- **The critic is an auditor, not a judge.** Its output can only annotate.
- **Deterministic math is the placement authority.** `compute_placement`
  over the teaching order decides where a unit lands; models supply
  relationships and claims, rules decide placement.

Preserved fail-closed invariants (these are checker rules, not critic
opinions): the fixed 0.920 confidence floor (env cannot lower it), unknown
or fabricated block IDs, relationship evidence must cite blocks from the
relationship's own topic (with bounded correction — the job 23 lesson),
lossy splits that drop protected QIDs, keep-decisions that rewrite the
claim, and Rule 4a — print position is provenance, never grounding: a
same-topic block grounds, a cross-topic block is a `reference_block_id`.

## 5. The decision store

One content-addressed JSON store per job replaces all 17 cache files:

```
decision_store/
  <key>.json   { unit, kind, decision, review_flags, confidence,
                 provider, model, created_at, envelope_hash }
```

- **Resume** is a cache walk: identical envelope → identical keys → free
  replay. A network drop costs nothing and can never re-litigate.
- **Release** reads the store directly. The always-complete-workbook rule
  from the job 23 fix generalizes: the release is a projection of the store
  plus the envelope, so a run that dies at any point still ships every
  decision made so far, flags included.
- Store entries are immutable. A changed envelope or policy version yields
  new keys; stale entries are simply unreachable, never "reactivated".

## 6. The four passes

### Pass 1 — Settle
Topology and grounding for every skeleton row, then learner analysis.
Per topic, through the kernel: (a) keep/refine/split/move against every
canonical topic, with split segments inheriting protected QIDs; (b) exact
source-block grounding for each surviving claim; (c) Misconception/Error
Analysis authored per concept (culminations excluded by design). Output:
the settled row set — the same artifact today's
`phase31-final-topology-cache` holds, now a first-class product.

### Pass 2 — Host
One certified host concept per Type/Case unit and per QID, through the
kernel. A new concept may be created only when no existing row can host a
distinct durable source idea; new-host claims carry placement contracts from
`placement_policy` exactly as today. Output: host map + qid map.

### Pass 3 — Assemble (deterministic, zero model calls)
Types onto settled rows is ID mapping through the host map. QIDs route
through the qid map. Figures, hubs, and chapter-review tasks come from the
ACSD ledger's deterministic anchors. Culmination rows derive from their
topic's settled concepts. **Because no model runs here, the 5,800-line
re-verification tail (3.4–3.11) has nothing to verify and is deleted, not
ported.** Any assembly inconsistency is a bug in our code and raises
immediately — there is no provider to correct.

**Types are embedded in the concept detailing.** Assemble renders each
hosted Type/Case into the concept's `concept_details` as the house
`// Types:` section, exactly as today's output format:

```
Description: … 
Achieving Mastery: … // Types: Type 01: <type_title> — <type_definition>
Case 01: <case_definition> Example: <example prompt> Type 02: … //
Misconception/ Error Analysis: …
```

Every field in that section — Type titles, definitions, Case definitions,
example prompts — already exists in the envelope's `mined_types` and
`inventory`, and which concept carries which Type comes from the Pass 2
host map. Rendering it is therefore pure string assembly with no model
call, and the golden gate compares the rendered section against the
reference output.

### Pass 4 — Release
Projects store + envelope into the review workbook: Released Concepts (with
a Review Flags column), Type/Case Routing, Issues, Manifest. Identical
shape whether the run completed or failed; the only difference is the
attached failure record.

**Terminal output format is the house bulk-import format.** Explicit
publication ("Upload to Database") continues to write concepts into the
platform database and to append them to
`backend/data/bulk_import_database.xlsx` via `app/bulk_import/writer
.append_concepts` — the Objective-sheet Chapter/Topic/Concept bands, one
row per concept placement, idempotent refresh, merged `concept_source`.
Pass 4's released rows MUST remain consumable by that exact chain
(`clean_concept_record` → `_find_or_create_topic`/`_add_concept` →
`writer.append_concepts`) unchanged: `topic`, `concept_title`,
`parent_concept`, `concept_details` (Description / Achieving Mastery //
Misconception–Error Analysis), `keywords`. Group/Question columns of the
bulk format stay owned by the question-generation pipeline, which consumes
the released QID→concept routing.

## 7. What the new tree looks like

```
backend/app/services/phase3/
  envelope.py    # build + seal + load the 81% envelope        (~200 lines)
  kernel.py      # decide(), decision store, flags             (~250 lines)
  settle.py      # pass 1 payloads/checkers/appliers           (~400 lines)
  host.py        # pass 2                                      (~500 lines)
  assemble.py    # pass 3, deterministic                       (~400 lines)
  release.py     # pass 4 projection                           (~250 lines)
```

`placement_policy.py` stays. The 22 `canonical_source_phase3*` modules are
deleted at the end of the migration (the pre-81% functions that live inside
`canonical_source_phase3.py` — graph build, source review — move out or
stay behind a trimmed module; nothing pre-81% is rewritten).

## 8. Acceptance: the golden envelope

Job 23 produced the project's first fully validated topology (53 rows, 47
with complete learner analysis, certified host and qid maps). Its artifacts
are the recorded golden envelope, checked into `backend/tests/golden/`.
The rewrite's gate, enforced in CI:

1. `settle(golden_envelope)` with recorded provider/critic responses
   reproduces the 53 settled rows byte-for-byte (minus timestamps).
2. `host(...)` reproduces the certified host map and qid map.
3. `assemble(...) + release(...)` reproduces the rescued workbook's
   Released Concepts and Type/Case Routing sheets.
4. Kernel property tests: every fail-closed invariant from §4, dissent
   always flags and never blocks, store immutability, resume-is-free.

A second golden envelope is recorded from the first fully completed
production run and added to the same gate.

## 9. Migration sequence

- **PR 1 (this document)** — spec + boundary contract, no code changes.
- **PR 2 — Envelope + kernel + Settle.** New tree lands alongside the old
  code; a feature flag routes Settle through it; golden gate (1) passes.
- **PR 3 — Host.** Golden gate (2).
- **PR 4 — Assemble + Release, old modules deleted.** Golden gates (3)–(4);
  the flag is removed and the old post-81% path ceases to exist.
- **PR 5 (optional, recommended) — detached runner.** The run executes
  server-side; the browser stream becomes a viewer. Network drops stop
  meaning anything. Separable from the rewrite but this is the moment.

Old per-phase caches are not migrated: the first run after PR 4 re-decides
from the envelope (one full-price chapter run), and the decision store owns
everything thereafter.

## 10. Boundary audit: pre-81% influence and API-only guarantee

Audited 2026-08-09 against the current code.

**What legitimately crosses the boundary** is exactly the envelope's
contents (§3) — data, independently verified before 81%. Upstream quality
propagates as content, never as behavior.

**Everything after 81% is decided by the model over the API** — topology,
grounding, learner analysis, hosting, Type embedding, and every critic
pass — gated by `semantic_api_enabled()` (true whenever an OpenAI or
Gemini credential is present and live mode is not disabled). The only
non-API steps are derivations of already-API-verified material:
culmination recaps today, and the whole of Assemble in the rewrite.

**Seam closures the rewrite MUST implement** (silent-degrade paths found
in the current code):

1. Today, if `semantic_api_enabled()` is false (missing/misconfigured
   credential), Phase 3.1 silently falls back to
   `topic-bounded-deterministic` grounding and the run "succeeds" with
   weaker output. The rewrite fails closed at the envelope gate instead:
   no live API, no run, before any spend.
2. Today, a missing active-graph contextvar makes `ground_concepts`
   return rows unchanged (grounding silently skipped). The rewrite has no
   ambient state to miss — passes take the envelope explicitly, and a
   malformed envelope is a hard error.
3. The dry/stub mode (`AEGIS_ALLOW_DRY`) stays test-only and is refused
   by the new runner when a job id is present.

## 11. Out of scope

Phases 1–2 and the semantic graph, the pre-81% pause sites, the autonomous
resolution agent for pre-81% decisions, provider selection (GPT/Gemini) and
cost accounting, database publication. All continue unchanged.
