# Step 12 spec — staging acceptance corpus + fault injection (4 of 6)

Scope is exactly §10 item 12 minus the two interrupted-path cases (S9/S10 are
rewriting those seams; they are residues owned by a follow-up step). Everything
built here is tests and test fixtures — no product code changes. All corpus
assertions are mechanics: identity locks on frozen inputs, exact-once closure,
flags-surface-as-flags, run-completes. No assertion decides what a source means;
the scripted providers ARE the model verdicts, and the tests verify the pipeline
honors them without loss (R4) and without gating on dissent (Q10).

## D1 — Corpus home and shape

`backend/tests/acceptance_corpus/`: a package holding checked-in `.mmd` sources,
fitz PDF builders, scripted provider transcripts, and an identity-locked
manifest. Test drivers live beside the other suites as
`backend/tests/test_acceptance_corpus.py` (corpus) and
`backend/tests/test_fault_injection.py` (faults) so the standard
`pytest tests/` collection picks them up unchanged.

Losing argument: extend `backend/data/Testing/` and its manifest — that
directory is owner-frozen for this step, and its manifest deliberately asserts
`len == 3` so coverage cannot be overstated silently. A new home keeps the
owner's corpus and the staging corpus separately accountable.

## D2 — Source medium per case

- **MMD cases**: checked-in UTF-8 `.mmd` files, byte-identity-locked (sha256 in
  the manifest), driven through the real HTTP lane (upload → convert → generate).
  One suite-wide authority stand-in applies (stated honestly, audit F17): the
  conftest QX echo author supplies the task-membership verdicts the compile
  seam now requires, since tests run with no live provider. Beyond that the
  drive is unpatched — the dry path exercises Phase 2/Phase 3 source
  compilation, subject adapters, deposit, culmination, and workbook append.
- **PDF and scanned cases**: built at test time with fitz (text-layer pages for
  "text", rasterized-image-only pages for "scanned") and driven through
  `reconstruct_pdf_to_acsd(provider=<scripted>)` — the established offline
  pattern. PDF cases are structure-locked, not byte-locked.

Losing argument (byte-locking PDFs): PyMuPDF is version-floating and its output
bytes are environment-sensitive (step-10 map §4 measured this), so a checked-in
PDF hash would rot on a dependency bump while proving nothing the structural
locks don't. Losing argument (driving PDFs over HTTP): the contract exposes no
provider seam, so HTTP-lane PDF drive requires patching `use_live_generation`
plus wrapping a module attr per test; adding a seam would touch conversion
plumbing concurrently being reworked by step 8. The direct-call pattern tests
the same reader with less scaffolding.

## D3 — Corpus cases (the variety §10 item 12 names, lower grades emphasized)

| Case | Board / grade / subject | Medium | What it carries |
|---|---|---|---|
| `mh3_evs_thin` | MSBSHSE / 3 / EVS | MMD | Lower-grade thin chapter: 1 topic, 2 concepts' worth of text, 3 short tasks — the volume-pressure case CLAUDE.md names |
| `mh4_maths_short` | MSBSHSE / 4 / Mathematics | MMD | Lower-grade maths, worked examples, tiny exercise set |
| `cbse6_science_activities` | CBSE / 6 / Science | MMD | Activities, "do you know?" info hubs, checkpoint questions mid-chapter |
| `cbse8_maths_katex` | CBSE / 8 / Mathematics | MMD | Dense KaTeX/LaTeX, tabular content, numericals |
| `icse10_english_poem` | ICSE / 10 / English | MMD | Poetry: stanzas, no topic headings, poem-question tasks |
| `cbse7_english_prose` | CBSE / 7 / English | MMD | Prose: story chapter, dialogue, minimal structure |
| `kseeb9_socsci_hubs` | Karnataka / 9 / Social Science | MMD | Map/figure references, info hubs, source-quote blocks |
| `cbse10_physics_figures` | CBSE / 10 / Physics | PDF (text) | Image-dependent tasks: figure crops, cross-page figure reference, "describe Fig." tasks |
| `mh6_maths_scanned` | MSBSHSE / 6 / Mathematics | PDF (scanned) | Image-only pages, figure + tasks — the no-text-layer route |
| `cbse5_evs_mixed` | CBSE / 5 / EVS | PDF (text) | Lower-grade PDF: sparse pages, one figure, activity-embedded question |

Ten cases; each MMD case ≤ ~120 lines, each PDF case ≤ 3 pages, so the corpus
stays reviewable. Metadata (board/grade/subject/chapter title) rides each case
and reaches the pipeline through the same fields production uses.

Losing argument (real textbook excerpts): copyright and owner sourcing make
checked-in real pages the owner's call, not this step's; the register's own
live-acceptance intent (restructure-handoff step 12) needs real books AND live
API, neither available here. Synthetic sources shaped on the named axes exercise
the mechanics now; live runs over real books are recorded as the operator
residue.

## D4 — What the corpus asserts (per case, all mechanical)

1. **Identity locks**: MMD bytes sha256; per-case recorded `source_contract_hash`,
   topic/task/block counts, graph status, and error-issue map — drift detection
   exactly like the existing template. PDF cases lock structural counts
   (pages, figure assets materialized, tasks) rather than byte hashes.
2. **Exact-once closure (R4)**: block-id set equality canonical↔graph; task-id
   closure; contiguous QINV numbering; every scripted-provider task lands in the
   inventory placed-or-flagged — counted by identity, never by threshold.
3. **Flags surface, never gate (Q10)**: cases whose scripted verdicts carry
   review flags/sub-floor confidence complete with the flags visible in the
   output, not dropped, not blocking.
4. **Run completion**: the HTTP drive returns a result (not a stream error) for
   every case; the dry generation deposits and the chapter meta fills.

Recorded counts are identity pins on frozen synthetic inputs, reviewed when a
deliberate compiler change moves them — the same contract the existing manifest
test already established. Losing argument (closure-only, no counts): silent
structural drift (a topic split disappearing) would pass closure checks;
the existing template locks counts for exactly this reason.

## D5 — Fault injection (the four directed cases)

All injected at the real seams via the established fixtures; no product code
edited; frozen files exercised, never modified.

- **API dissent** (`fallback.py:2040-2078`): scripted provider returns
  `review_required` WITH candidate pages after bounded corrections → assert the
  batch ships `accepted_with_review_flags`, per-page flags carry the dissent
  reason, the run completes, and nothing from the candidate pages is lost. This
  is the first test ever on that branch. Companion: the no-candidate variant
  raises (pinning the intentionally fail-closed comment at :2044-2046). Phase 2.2
  and kernel dissent are already pinned by existing tests — not duplicated.
- **Quota failure** (`generation.py:2778-2789` via the `test_multi_user_safety`
  RateLimitError fixture): (a) quota death inside generation → failure release:
  `status == "released"`, the quota text attached as a release issue, checkpoint
  retained; (b) quota death inside `prepare_job_context` → true halt: stream
  error, `job.detail` prefixed "Generation failed:", status unchanged, no
  release; (c) post-quota retry replays verified batch caches without
  re-invoking the provider for them (the free-resume property).
- **Asset failure** (`_clip_bbox` :2121-2122 via a scripted degenerate bbox):
  pin CURRENT behavior — the run fails, and the retry fails identically because
  the sealed bundle replays without asset_urls. Both pins are labeled in-test as
  documentation of a pre-existing Q13 tension owned elsewhere; the test names
  the mechanism so the eventual fix has a red test to flip. Companion: the
  autonomous repair paths downgrade a missing-asset render to flags
  (phase3.py:3116-3160) — pinned as the correct twin.
- **Cache alteration**: (a) corrupted JSON in the batch cache → silent miss →
  re-extraction (provider invoked again), never served; (b) wrong status →
  same; (c) tampered-but-well-formed sealed bundle with preserved page count is
  accepted verbatim — pinned and labeled as the documented tamper-trust
  asymmetry (residue), with the corrupt-refusal side asserted as the safety
  property; (d) phase-2.2 adjudication cache: corrupt → re-adjudicated, not
  applied.

Losing argument (fixing the Q13 violations the injections expose): the seams
live in files frozen for this step (step-8 concurrency) or in `phase3/`
(frozen absolutely). Pinning current behavior with named mechanisms gives the
owning step a failing-on-fix test to flip, which is this step's whole value.

## D6 — Fail-before/pass-after discipline for a tests-only step

There is no product fix to neutralise, so each test's non-vacuity is proved by
breaking the property it pins (monkeypatching the seam to simulate the silent
loss, the gate, or the served-corruption) and watching the test go red; the PR
lists which tests were proved that way and by what breakage.

## Slices

1. Corpus package: sources + builders + manifest + `test_acceptance_corpus.py`
   (MMD cases through HTTP; identity + closure + flags + completion).
2. PDF/scanned corpus cases with scripted providers, same assertion set.
3. `test_fault_injection.py`: the four seams.
4. Audits (doctrine + integrity lenses), repair, verify, PR report.

## Residues (owned elsewhere; recorded in the PR)

- Interrupted release / interrupted publication injections — follow-up step
  after S9/S10 land.
- Live-API acceptance runs over real textbooks + human review budget — operator
  action (no keys in this environment; the corpus's case metadata and sources
  are shaped to be reusable for that run).
- The Q13 tension inventory (map §8) — owned by the steps that own those files.
- The cache tamper-trust asymmetry (map §7) — integrity hardening step.
- A second golden envelope (non-History subject) for offline rewritten-Phase-3
  coverage — needs a live recording run.
