# Parallel workstreams — knowledge transfer and delegation plan

Written for two additional agent accounts joining this repository alongside the
session that has been driving §10 step 8. Read §0 and §1 before writing any code.

---

## 0. What is achievable, stated honestly

The remaining §10 work is **36–52 hours of wall clock**, measured from this
session's own slice times (S3 ≈ 35 min, S5 ≈ 61 min, S4 ≈ 114 min, the step-8
spec ≈ 2 h 14 m). Three hours with three agents does not close it, and the
reason is structural rather than a matter of effort:

* **Step 8's remaining slices are a dependency chain.** S8 needs S7's layout;
  S9, S10 and S11 need S8's renderer. Adding agents does not parallelise a
  chain.
* **The backend test suite is a global lock.** Two `pytest` runs against one
  clone produce phantom failures in the DB-state suites (`test_directory`,
  `test_tagging`, `test_sources`, `test_data_reset`,
  `test_deposit_checkpoint_recovery`, `test_grounding_certificate`,
  `test_chapter_reading_outline_aware`). Every slice ends with a full run.
* **This container has 4 cores**, so `min(16, nproc-2)` = **2** concurrent
  agents per workflow. A second account on the same container contends for the
  same cores.

**What three hours of three parallel streams realistically closes:** S8 lands,
step 10's map and spec land, step 11's map lands. That is real progress and it
is roughly 6–8 hours of work compressed into 3. It is not the end of the
programme.

The largest single speedup available is **more cores**, not more accounts —
see §1a.

---

## 1a. Cores: what to provision, and what each step buys

The harness caps concurrent sub-agents per workflow at `min(16, nproc - 2)`.
That single line explains most of this session's wall clock.

| Cores | Agents/workflow | Effect on a slice |
|---|---|---|
| **4** (today) | **2** | The step-8 spec's 14 agents ran as **7 sequential pairs → 2 h 14 m**. A 3-audit slice runs 2 audits, then the third alone. |
| 8 | 6 | All three audits of a slice run at once. Fan-out phases roughly halve. |
| **16** | **14** | The same 14-agent spec workflow becomes **one batch, ~20–25 min**. This is the knee of the curve. |
| 18+ | 16 (hard cap) | No further gain; the cap binds, not the CPU. |

**Provision 16 cores for the step-8 stream.** It has the largest fan-out —
three adversarial audits plus implement and repair on every slice. 8 cores is
enough for stream B, whose slices are smaller. Stream C (documents) is not
CPU-bound at all.

RAM is not the constraint: this container has 15 GB and has never been near it.

**What more cores does NOT compress**, so the expectation stays honest:

* the full suite — ~2–3.5 min, single-threaded by necessity, and it is a
  **global lock** (see §0);
* the dependency chain S7 → S8 → S9 → S10 → S11;
* the orchestrator's own verification and commit, which is serial by design and
  is what has caught the defects the audits missed.

Realistic effect of 4 → 16 cores on the remaining 36–52 h: **roughly 30–40 %
off**, not 4×. The fan-out compresses; the serial spine does not.

---

## 1. The doctrine. Non-negotiable, and invisible to a fresh agent

`CLAUDE.md` is the constraint this codebase is built on. Read it in full before
writing a line. The short form:

**No deterministic judgment about what the source MEANS.** No regexes or
keyword vocabularies that classify content. No numeric thresholds that decide
meaning. No volume-derived structure (counts scaled from length, chunk count,
page count). No shape-matching standing in for comprehension.

**Mechanics ARE allowed**: parsing, ID assignment, caching, ordering, atomic
writes, schema validation, and gates that refuse a broken artifact.

**Q13** — a mid-run block becomes one recorded, flagged decision and the run
completes. Only three pre-spend pauses and genuine impossibility stop a run.
**Q10** — a critic advises, never gates. **R4** — nothing a learner would see
is silently lost.

### Why a competent coding agent is the specific risk here

A strong agent optimises for *make the test pass*. That instinct has already
shipped a disaster in this repo: a bold-vs-heading rule took questions from
**24 to 0** (PR 208/211). The live proof is current — `_source_task_anchors`
in `generation.py` is regex question extraction that has passed every test in
this repository for months (see §7).

**Green tests do not mean correct here.** In this session alone:

* three slices where the repair pass found defects **every** audit missed;
* two spec rounds where a repair opened a **wider** hole than the one it closed;
* three occasions where an auditor reported a real failure with the **wrong
  mechanism**;
* two audits proposing **opposite** fixes for the same behaviour.

Every one passed the suite. They were caught by adversarial review against the
doctrine.

---

## 2. The working method every stream must follow

    map → spec → implement → independent adversarial audits → repair → verify → commit

Not optional. The audits must be *independent* (they do not see each other) and
*adversarial* (told to refute, defaulting to REFUTED when they cannot confirm).
Two to three lenses per slice; at least one purely on doctrine, one on whatever
the slice could silently break.

The orchestrator (not a sub-agent) runs the full suite and commits. Sub-agents
are forbidden `git commit`, `push`, `checkout`, `reset`, `stash`, `rebase`,
`clean`.

**Verification commands, exactly:**

```bash
# full suite — ALONE, from a FRESH dir never reused
cd backend && S=$(mktemp -d) && mkdir -p $S/data && \
  AEGIS_DB_URL="sqlite:////$S/x.db" AEGIS_DATA_DIR="$S/data" \
  python3 -m pytest tests/ -q -p no:cacheprovider

# frozen artifacts — every one must be empty / unchanged
sha256sum backend/tests/golden/rne_envelope.json
#   e27cdcf02ed8579b1210c1d55d484cf20d604b2f08cb379c814d3d4ba1e42c79
git diff --name-only -- backend/app/services/phase3/ backend/tests/golden/
git diff --name-only -- frontend/
git diff --name-only -- backend/tests/test_assessment_reference_acceptance.py
git diff --name-only -- backend/data/Testing/reference_bulk_import/
```

**Baseline at the time of writing: 2459 passed, 7 xfailed.** A close below that
is deleted coverage and must be accounted for test-by-test by name.

The 7th xfail is deliberate and `strict=True`: it pins the open R5
top-of-range residue in `tests/test_label_collision_surface.py`. It must stay
XFAIL. An XPASS fails the suite, which is the point.

---

## 3. Current state

* Branch `claude/step-8-four-output-schema`, draft **PR #229**, based on `main`.
* Slices **S1–S6 committed**; **S7 in flight** in the working tree.
* Steps 1–7 of §10 are merged to `main` (PRs #227, #228).

| Slice | State |
|---|---|
| S1 frozen-core listing | ✅ `6c6ebc9` |
| S2 layout registry + fail-closed reader gate | ✅ `9b90d5f`, `11b6b9c` |
| S3 release_result symmetry, four manifest blocks, explicit publish lane | ✅ `efadf77` |
| S4 persisted identity | ✅ `2cc988c` |
| S5 label-collision surface | ✅ `ccf447f` |
| S6 release core | ✅ `260c0d0` |
| S7 layout migration (atomic) | 🔶 in flight |
| S8–S11 | ⬜ pending |

---

## 4. Owner rulings — settled, do not re-litigate

| # | Ruling |
|---|---|
| **OD1** | One Build Concepts run produces **all four** outputs. |
| **OD2** | `bulk_import_format.xlsx` (committed, owner-supplied) is the layout authority. `bulk_import_database.xlsx` is a **CI-regenerated fixture**, not the accumulator; the real append target is the gitignored `bulk_import_output.xlsx`. |
| **OD3** | `keywords` and `related_concepts` ship **filled**. |
| **OD4** | Output **01 = Pre Concept, 02 = Pre Master, 03 = Post Concept, 04 = Post Master**. This reverses the original doc. |
| **OD5** | A questionless concept gets **one** Master tail row, stopping at the concept columns. |
| **OD6** | The Concept File is filled from the first column through the concept columns only. |

**Standing ruling: NO FRONTEND WORK.** A complete UI/UX makeover is planned;
any display change now is thrown away. `git diff --name-only -- frontend/` must
be empty in every stream. Record frontend needs as residues.

---

## 5. The partition — by FILE, not by task

This is what keeps three streams from corrupting each other. Verified by
inspection of the remaining spec slices.

| Stream | Scope | Files | Collides with |
|---|---|---|---|
| **A** (this session) | Step 8 S7–S11 | `bulk_import/*`, `assessment_*`, `build_concepts_release*`, `release_qc.py`, `generation.py` (S11 only) | — |
| **B** | Step 10 — image durability | `app/api/source_assets.py`, `app/services/auth.py`, `app/api/admin.py`, `app/main.py`, `canonical_source_phase221_fallback.py` | **none** |
| **C** | Step 11 map + spec (documents only) | `docs/` | **none** |

**Known collision, do not schedule concurrently:** the question-extraction fix
(§7) and step 8's **S11** both touch `generation.py`. One or the other, never
both.

### Merge protocol

1. Each stream gets its **own environment, own clone, own branch, own PR**.
2. **Never two streams on one working tree**, and never two `pytest` runs on one
   clone — that is how you get an audit result you cannot trust.
3. Merge to `main` independently. Rebase on `main` after each merge, never onto
   another stream's in-flight branch.
4. Do **not** push to a shared branch or a shared PR.

---

## 6. Stream briefs

### Stream B — step 10, image durability

**Why this one is safe:** zero file overlap with step 8, and it is designed but
not built.

Published assets currently carry **HMAC-signed, expiring** URLs
(`api/source_assets.py`). Q8 requires non-expiring public links for published
assets plus volume backup. Every asset already carries a content hash, so the
manifest-driven URL rewrite is a design to implement, not to invent. The
environment migration stays a deliberately later step.

Deliverable: map → spec → slices, same loop as §2. Estimate 5–8 h.

### Stream C — step 11, language mode (map and spec only)

Poem/prose topology as a subject adapter inside Phase 2.1. The Architect
already selects and records the mode; this step builds what that selection
drives. Validation needs a live provider and a real chapter, so the **map and
spec** are the deliverable here — implementation waits.

Critical constraint the map must respect: **a poem chapter can legitimately
teach the same-named idea in two stanzas.** Step 8's identity was written not
to foreclose this (per-topic, positional, no chapter-wide unique-title
assumption). Do not introduce one.

### Which stream suits which account

Assign on three things that are checkable, not on general claims about model
quality:

**1. Does the work need the harness?** This project's method is
map → spec → N independent adversarial audits → repair, run as parallel
sub-agents against one working tree. Claude Code has that harness natively
(`Workflow`, sub-agents, per-agent tool scoping). An agent without it can
still do the work, but the loop has to be driven by hand, which is where
adversarial independence quietly degrades into one agent reviewing itself.
→ **Work that writes pipeline code goes to an account with the harness.**

**2. Can the output be verified by reading it?** A map, a spec, a census or a
review is checkable in minutes and costs nothing when wrong. Pipeline code
that passes tests but violates Rule 1 costs a slice and may not be caught for
weeks.
→ **Read-verifiable work is the safe place to start a new account.**

**3. Would model diversity help?** This is the one place a second *family* of
model is worth more than a second instance of the same one. Every audit in
this session has been Claude auditing Claude, and the failure pattern is
visible in the record: twice, two audits agreed with each other and both were
wrong about the mechanism; three times, all audits missed a defect the repair
found. Those are correlated blind spots, which is exactly what a different
training lineage breaks up.
→ **A GPT account is most valuable as an independent adversarial reviewer of
this session's slices**, not as a second implementer racing alongside.

**Concrete assignment:**

| Account | Primary | Why |
|---|---|---|
| **Claude Max #2** | Step 10 — image durability, full loop, own PR | Writes pipeline code; needs the harness; file-isolated so a mistake cannot reach step 8 |
| **GPT Pro** | (a) Adversarial review of each step-8 slice PR before merge; (b) step 11 map + spec; (c) the question-extraction spec from the existing map | Read-verifiable output; different blind spots from the Claude audits; zero merge risk |
| **This session** | Step 8 S7 → S11, then the extraction fix | Holds the spec, the residues and the verification loop |

Give each new account **one** stream first and read its first PR closely before
widening. Both still run the loop in §2.

---

## 7. Open items a new agent will otherwise rediscover

**The largest, and it is not in any slice.** On `.mmd`/`.md`/`.txt` uploads the
question inventory is produced by **regex, with no model verdict at all**.
`_extract_question_task_inventory_via_api` does not call the API in production —
it is rebound at import by `canonical_source_phase2_contract.py:135-148`, whose
own log line says *"Building Question / Task Inventory deterministically from
the Phase 2 ACSD task ledger; no inventory-extraction model call is required."*
That branch is live for every `build_concepts` run, and the ACSD ledger is built
1:1 from `generation._source_task_anchors`.

The vocabularies deciding what a question is are English-only
(`what|why|how|...`, `can|could|do|does|...`) with `{8,800}` character
thresholds. The PDF lane is different — the GPT page ledger overrides the
parser there, and its authors left a comment acknowledging the parser
*"recognises a finite cue vocabulary"*. They fixed one lane.

Owner ruling: **map now, fix after step 8.** The map is written. It collides
with S11.

**Other tracked residues:** R5 holds for interior deletions but not the top of a
label range (strict xfail, owner S10); `legacy_label_family` has no durable
home (step 9); `question_label_duplicate` has no app-side consumer (step 9);
non-SQLite deployments get no unique index; OD4 numbering drift in
`assessment_release_run.py:1305-1306, :1378-1379` now reaches reviewers through
`disabled_reason` (S8); `_PEDAGOGY_TOPIC_RE`, `_GENERIC_SKELETON_FAMILY_RE`,
`PLACEHOLDERS` containing `"none"`, and the writer's culmination checks remain
on the §3 residue list.

---

## 8. Things that look like defects and are not

* `AssessmentRelease.__tablename__` is `"assessment_releases"` and the name is
  now a misnomer. **Do not rename it** — `_ensure_columns` emits only
  `ADD COLUMN`, so a renamed table is minted empty by `create_all` and every
  published release is orphaned. Pinned by test.
* `layouts.py`'s three `canonical-*` entries are **frozen literal
  transcriptions**, deliberately not derived from `FIELDS_BY_KIND`. Deriving
  them un-registers the canonical layout the moment S7 moves the constants, and
  every older workbook 422s.
* `assessment_release_service._readiness`'s read of
  `manifest["issues"]["unplaced"]` is the **only** thing refusing the
  assessment-lane database write until S8 lands its replacement. It is deleted
  in S8, in the same commit as `unresolved_question_homes`. Deleting it earlier
  opens a window in which a candidate appearing in no data row of any of the
  four outputs publishes to the database.
* `except Exception` in `_build_master_siblings` deliberately does not catch
  `BaseException`.
* `summary.issue_count` and the issue ledger disagree by one on a failed
  assessment-lane run, deliberately — bumping it would downgrade the Concept
  File's public state for a fault in the other lane.

---

## 9. Reference documents

| Document | What it holds |
|---|---|
| `CLAUDE.md` | Rule 1. Read first, in full. |
| `docs/aegis-restructure.md` | The architecture and the §12 decision register (Q1–Q13, Rules A–G, R1–R7). |
| `docs/spec-step8.md` | 4,606 lines: the thirteen tensions resolved with evidence, eleven slices, tests, doc amendments. |
| `docs/restructure-handoff.md` | Working method, conventions, per-step briefs 6–12. |
