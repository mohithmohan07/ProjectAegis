# Session handoff — continue from the 2026-08-29 sessions

This file is the complete state transfer for a fresh Claude Code session.
Read it first, then `docs/aegis-restructure-c-plan.md`, then
`docs/aegis-restructure.md` (§12 decision register). CLAUDE.md Rule 1 is
loaded automatically and governs everything.

## Mission

Make the Aegis pipeline (deployed at projectaegis.fly.dev, repo
`mohithmohan07/ProjectAegis`) reliably produce all four outputs per chapter
(01 Pre Concept, 02 Pre Master, 03 Post Concept, 04 Post Master) with content
matching the owner's manually corrected files in the "Concept Mapping Audit"
corpus. Work happens on branch `claude/error-progress-reporting-x924ql`.

## Where things stand (all merged to main and deployed; deploys 1–8 green)

- PR #265–268: auto-deploy workflow; Fixer hub-routing; \mathrm ingestion
  canonicalization; named-predicate terminal diagnosis.
- PR #269: audit decisions D1 (KaTeX support list: only \mathrm banned;
  \hspace/\phantom/\boxed and \\[dim] row spacing allowed), P10 (canonical
  table house style), D3 (is_update_* defaults "No"), D4 (Pre rows'
  related_concepts cleared), Question Duration Matrix + marks_matrix +
  MH-board subject policies with grade-6 English layering, run-diagnostics
  zip export for any run state (+ UI button).
- PR #270: P1–P9 (plain numerals, whole-set type merge, pre-spend source
  dedup verdict, authored Case titles, figure placement rules, entity-ID
  uniqueness gate, transport-encoding rewording, one-decimal number
  formats, uniform-1.0 rubric default).
- PR #271: D8 — a Culmination concept exists only for a topic with ≥2
  concepts (authoring-time enforcement; legacy attested rows never dropped;
  `culmination_single_concept` validator warning).
- PR #272: Restructure A (the run's terminal verdict is decided ONCE at
  staging, recorded as `terminal_generation_complete` on the staged payload
  + summary; Master eligibility and DB publication read the recorded fact;
  legacy payloads backfilled once — this retroactively unblocked the
  'Patterns' job class) and Restructure B (all upload parameters chosen
  before upload with a one-shot upload→convert→generate chain; full
  converted-text viewer `MmdViewer` replacing the 800-char preview).
- `docs/aegis-restructure-c-plan.md`: the approved-for-drafting Restructure
  C plan (block JSON as single authority; MMD demoted to a generated view),
  with the full seam survey (file:line). NOT yet implemented.

Baseline: backend suite 3401 passed; frontend tsc clean, 103 tests passed.

## The immediate task

Implement Restructure C per `docs/aegis-restructure-c-plan.md`, in order:

1. **C1** — compile the canonical ACSD directly from the reader's page/block
   JSON; MMD becomes a projection. SHADOW MODE FIRST: run both compilers,
   record divergences as defect flags, present the shadow-diff results to
   the owner BEFORE cutting over.
2. **C2** — carry page identity end-to-end (fill dead `page_hint`, populate
   the Phase 3 envelope's `acsd_ledger`, delete both character-offset
   page-guessers). Independent of C1; may be done first as a cheap win if
   the owner prefers.
3. **C3** — chunk generation from block sequences on the PDF lane.
4. **C4** — non-PDF parity. Do NOT start without a separate owner go/no-go
   with cost numbers.

Approval protocol the owner has used all along: present findings/proposals
and wait for explicit approval before implementing anything new; once a
phase is approved, ship it end-to-end without re-asking.

## Validation requirement (every C phase)

Full backend suite PLUS reconversion of both audit chapters (Maths and
English source PDFs) and a machine diff of the canonical inventory and all
four outputs against the corrected files. **The audit corpus is NOT in the
repo** — ask the owner to re-upload the "Concept Mapping Audit" zip.
Known data-integrity facts about that zip (verified, owner informed):
- The English master originals are cross-named; the log is authoritative:
  file dee7655a = Post, e324a5a1 = Pre.
- The Maths folder 03 PostConcept original/corrected files are swapped
  between the 01/02 folders (proven by is_update columns and the
  362-vs-480 duration values).
- ~25 of 30 logged corrections were already fixed by PRs #269–271; the
  correctors also introduced defects (ID collapse in all four EN files,
  header corruption, half-applied edits) — do not treat corrected files as
  byte-perfect ground truth; treat the Logs & Errors documents as intent.

## Parked items (owner-aware; defaults standing; do not do unasked)

- D5 chapter_duration one-value-everywhere stays as is.
- D6 "Words"→Phrases fold stays as is.
- D7 capitalized answer_restriction stays as is.
- A6 widening beyond EN-Post's 30 slots: not done.
- Fixer spend observation in a live Luna run summary: still unobserved.
- Post-restructure validation rerun of both audit chapters end-to-end
  (fresh runs through the deployed app) is still owed once C stabilizes.

## Working conventions (hard-won; follow exactly)

- NEVER run two pytest processes concurrently on the SAME database — it
  once produced 300+ phantom sqlalchemy failures. The default test database
  is `backend/aegis_test.db` (set with `setdefault` in `backend/conftest.py`),
  so a per-process override isolates parallel runs:
  `cd backend && AEGIS_DB_URL=sqlite:///./aegis_test_<tag>.db python3 -m pytest tests/test_x.py -q`
  (delete the `aegis_test_<tag>.db*` files afterwards; they are not
  ignored). Full suite, one process:
  `cd backend && python3 -m pytest tests/ -q` (~3–5 min, ~3500 tests).
- In background shell commands the cwd resets to the repo root; `cd` inside
  the command.
- Ship pattern per change set: commit → push to
  `claude/error-progress-reporting-x924ql` → create PR → merge → reset the
  branch onto origin/main (`git checkout -B <branch> origin/main` +
  force-with-lease push) → verify the `deploy.yml` Actions run turns green
  (~60 s).
- Commit footer: `Co-Authored-By: Claude <noreply@anthropic.com>` plus the
  Claude-Session line FOR YOUR OWN SESSION (not the previous session's URL).
  PR bodies end with the Claude Code attribution and your session URL.
- Never put model identifiers in commits, PR text, code comments, or any
  pushed artifact.
- Frontend checks: `cd frontend && npx tsc -b && npx vitest run`.
- Rule 1 (CLAUDE.md) is absolute: no deterministic content judgment; model
  decides, an independent second pass advises (never gates), The Fixer
  resolves mid-run blocks; finished work always ships; nothing is lost
  silently. Restructure C is mechanics (IDs, joins, transport) and must not
  add any content-judging regex/threshold.
- For big diffs, an adversarial review pass (independent find→verify) has
  repeatedly caught real bugs before shipping; use one before merging C1.

## Fresh diagnostics

Any run, complete or not, can be exported as a diagnostics zip from the UI
("Download run diagnostics") or `GET
/build-concepts/uploads/{job_id}/diagnostics.zip` — use it when the owner
reports a misbehaving run.
