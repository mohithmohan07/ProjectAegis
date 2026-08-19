# Step 12 map — staging acceptance corpus

Measured on `main` @ 3aea81e, 2026-08-19, in a dedicated worktree. Baseline suite:
**2314 passed, 6 xfailed** (fresh isolation dir `/tmp/tmp.ePuwdjwU0p`, 129.76s, the
brief's command). All claims below were verified against code (docstrings not
trusted); the four most load-bearing were re-read line-by-line after the sweep.

## 1. How a source is driven offline today

- Tests force dry mode at import: `tests/conftest.py:5-8` (`AEGIS_ALLOW_DRY=1`,
  `AEGIS_USE_LIVE=0`); honored via `config.use_live_generation()` /
  `require_generation_live()` (config.py:113-154). Production deliberately has no
  offline semantic fallback — test verdicts are injected at documented authority
  seams (conftest.py:63-183 for the assessments lane).
- **MMD/text entry** (`.txt/.md/.mmd`): upload → convert → generate through HTTP with
  ZERO patching. `mmd.to_mmd` wraps text as `# {stem}\n\n{body}` (mmd.py:51-75);
  PDFs and images on this path raise clean ConversionErrors — **there is no OCR path
  in the tree at all** (mmd.py:59-71).
- **PDF entry**: only for `module="build_concepts"` with the fallback enabled —
  `canonical_source_phase221_contract.py:139-167` routes `.pdf` to the GPT reader;
  there is NO `provider=` seam on the HTTP path, so offline tests wrap the module
  attr `fallback.reconstruct_pdf_to_acsd` and patch `config.use_live_generation`
  (pattern: test_canonical_source_phase221_fallback.py:255-304). Direct-call
  pattern: `reconstruct_pdf_to_acsd(pdf, job_id=…, artifact_dir=…,
  fallback_reason=["pdf_source"], provider=_verified_provider)` with
  `AEGIS_PUBLIC_BASE_URL`/`AEGIS_SOURCE_ASSET_SECRET` set and `fallback._CACHE_DIR`
  pointed at tmp (:112-126).
- **Scanned sources** are mechanically the PDF lane with image-only pages: the
  reader renders page images regardless of text layer (`collect_pdf_pages`), so a
  synthetic scanned fixture is a fitz PDF whose pages contain only a rasterized
  image. No other scanned/handwritten path exists (`upload_type=handwritten` is an
  enum value whose only test proves a `.jpg` fails, test_mmd.py:111-144).
- **Dry generation** (`generation.py:20860`, dry branch :21333-21361) is a
  line-per-concept stub (## lines set topics). So MMD corpus cases exercise Phase
  2/Phase 3 SOURCE COMPILATION (deterministic offline —
  `semantic_api_enabled()` is False, phase3.py:333-339), subject adapters, deposit,
  culmination enforcement, workbook append — not semantic quality.
- **The rewritten Phase 3** (Settle→…→Assemble) is reachable offline only via
  `phase3.runner.run(env, providers=…)` against a sealed envelope
  (runner.py:197-216); exactly ONE envelope exists (golden job 23,
  CBSE/10/History). New envelopes per corpus case = a full replay-provider suite
  each (test_phase3_runner_golden.py:78-165 is the pattern).

## 2. The corpus template that already exists

- `tests/test_offline_source_corpus_manifest.py` — an identity-locked manifest
  (sha256 per source, exact-set membership, `len == 3`) plus
  `test_corpus_rebuilds_exact_grounding_preconditions`: compiles each source
  through `phase2.compile_phase2_source` → `phase3.compile_semantic_graph` with no
  providers and asserts `source_contract_hash`, topic/task/block counts, block-id
  set equality canonical↔graph, task-id closure, and the exact error-severity
  issue map. Its docstring says extension is the intended path — and that adding a
  `.mmd` under `data/Testing/` FAILS the manifest until updated. The owner dir is
  frozen for us → the step-12 corpus lives in a NEW directory.
- Siblings: `test_canonical_source_phase2_sources.py` ("Representative real-source
  acceptance gates": QINV numbering contiguous, identity keys unique, zero
  phase2_issues), `test_review_corpus_contracts.py` (per-source topic-heading and
  source_kind Counter ledgers).
- `test_assessment_reference_acceptance.py` (frozen): parse gold xlsx → rebuild
  snapshot → render Master → field-by-field diff. Pure workbook-layer, no HTTP.

## 3. Variety in fixtures today vs what §10 item 12 demands

Covered: CBSE-dominant, ICSE seed tree, MSBSHSE only via gold workbooks/assertion
strings; grades 06/09/10 only; real source text for History, Mathematics, Physics
(the three locked `.mmd`s); 4 of 13 subject adapters exercised.

Absent (grep-verified zero): **poetry/poem/stanza** fixtures; **prose as a genre**;
**scanned** anything; grades 1-5/7/8/11/12; regional-language text; adapters
computer_science/electronics/environmental_science/commerce/health_PE/arts; any
`.pdf` source fixture (every PDF test builds one with fitz); any second golden
envelope. Lower grades — the population CLAUDE.md names as hardest-bitten — have
NO source fixture at all below grade 6, and grade 6 exists only as gold xlsx.

## 4. Fault seam A — API dissent

- GPT PDF reader: the fork is `fallback.py:2040-2051` — dissent ships under
  per-page review_flags with `decisions[].status = "accepted_with_review_flags"`
  (:2052-2077) **iff the provider result carries `pages`**; a `review_required`
  result with no candidate pages raises (the comment at :2044-2046 declares that
  intentional: "Only a genuinely unusable transcription … stops the source
  pipeline"). Verifier dissent always ships pages (:1851-1861); deterministic
  validation exhaustion never does (:1792-1799). Sub-floor confidence flags, never
  rejects (:1834-1844).
- **No test exercises the accepted_with_review_flags branch** (:2040-2078). The
  nearest, test_canonical_source_phase221_fallback.py:165-183, hits the raise
  branch (stub returns no pages).
- Phase 2.2 adjudication: every dissent form flags and returns the author's
  candidate (phase22.py:852-925); only a missing evidence page fails closed
  (:866-868); dissent never raises from `adjudicate_job_source`.
- Phase 3 kernel: critic dissent → advisory flags (kernel.py:167-194, :334-345; a
  crashing critic ships "decision stands unaudited"); confidence-only defects ship
  flagged (:253-263); structural defects go to the Fixer (:264-309) which itself
  gets `attempts` tries (:283 — the docstring's "one decision" wording overstates);
  halts only at :310-318 (Fixer failed — sanctioned) and :319-325 (no fixer
  wired — see §8). Well-tested (test_phase3_kernel.py, test_phase3_fixer.py).

## 5. Fault seam B — quota failure

- The one distinguished stop: `generation.py:2778-2789` — `insufficient_quota` →
  no retry → `RuntimeError("… quota exhausted (insufficient_quota) …")`. Mirrors:
  phase22.py:701-714, phase34 contract :536-545. Everything else transient retries
  (default 10, config.py:230-233). **No typed quota exception exists** — detection
  downstream is string-matching (semantic_recovery.py:171-178), and the
  `\bopenai quota\b` pattern would miss a Gemini-labeled message
  (`\binsufficient_quota\b` catches it).
- Blast radius depends on WHERE it dies (layering verified):
  (a) inside `generate_post_learning` → swallowed by
  `build_concepts_release_contract.py:288` → **failure release**: `job.status =
  "released"` (there is NO "failed" status anywhere), detail names the release,
  the quota text becomes a release issue, checkpointed work is retained;
  (b) inside `prepare_job_context` (phase 2/2.2/2.2.1/3.6 turnover — runs OUTSIDE
  the release catch, canonical_source_phase2_contract.py:116-121) → **true halt**:
  stream `{"type":"error"}`, `job.detail = "Generation failed: …"`, status stays
  "converted", no release.
- Checkpoint durability on quota death: `build_concepts.py:4359-4430`
  save_checkpoint commits + schedules Drive backup; a quota-killed run releases
  the newest checkpoint (path (a)).
- The exact reusable fixture: `test_multi_user_safety.py:14-22` builds a real
  `openai.RateLimitError` with `body={"error":{"code":"insufficient_quota"}}`;
  :108-124 pins fail-immediately-no-retry (verified verbatim). A resume-after-
  quota case must pin: verified batch caches written before death (:1983-1992)
  make the retry replay free.

## 6. Fault seam C — asset failure

- `_render_page_visual_marker` raises ValueError on a figure without asset_url
  (phase3.py:2469-2473). Three consumers: two autonomous paths downgrade to
  flags/skip (:2650-2656; :3116-3160 — "never blocks the chapter"), the
  human-decision path (:4203-4206) does not guard → propagates → failure release.
- `materialize_visual_assets` raises on: page outside PDF (:2142-2143), bbox < 8pt
  after clipping (`_clip_bbox` :2121-2122), any PyMuPDF error. **No caller
  catches.** Via `reconstruct_pdf_to_acsd` (conversion or 3.6 turnover → true
  halt); via phase3 `load_page_evidence` :5814-5818 (mid-generation → failure
  release). Worse: the sealed bundle replays WITHOUT asset_urls, so
  `needs_assets` is true again on retry → **the same degenerate bbox fails every
  retry deterministically** (the sharpest Q13 tension in scope — a single
  unusable crop is a place-or-flag decision under R4).
- Deleted-asset blind spot: :5809-5813 checks only field presence, never file
  existence — a bundle with asset_urls whose files are gone completes and ships
  404-bound URLs (step 10's store fallback now covers the serve side for pinned
  crops; the generation-side blind spot remains).
- **No missing-asset test exists anywhere** (grep-verified).

## 7. Fault seam D — cache alteration

- Batch cache `_read_verified_batch_cache` (fallback.py:623-633): corrupt JSON /
  wrong status → silent miss → re-extract (re-bill). But valid JSON with
  `status:"verified"` and ARBITRARY `result` is trusted verbatim (:1978, logged
  "success") — no schema recheck, no re-verification, no checksum. The stored
  `pdf_sha256` in the file is decorative; only the filename key binds it.
- Sealed-bundle gate (:1916-1939) checks `result["pdf_sha256"] == pdf_sha` and
  page COUNT only — edited page content with preserved count is undetectable and
  bypasses the whole verification lane.
- Phase 3 artifact gate (phase3.py:5781-5797): three scalar stamps, no page count
  (unlike the sealed gate), no digest. Stale-schema re-extraction is tested
  (test_pdf_acsd_lane_reuse.py:162-204); content-tamper acceptance is not.
- Phase 2.2 adjudication cache (phase22.py:1017-1026, gate at :1571): corrupt →
  silent re-bill; well-formed tampered `decision` is **applied verbatim to the
  canonical ledger** (`apply_verified_decision` :1611-1619) — the sharpest
  integrity asymmetry: corruption costs money, tampering injects fabricated
  source text.
- Checkpoints: exported bundles ARE checksummed (checkpoints.py:1672-1677,
  :1754-1766 — corruption detection, not tamper-proof); DB-resident
  `generation_checkpoint` fingerprint EXCLUDES the records themselves
  (build_concepts.py:1359-1383), with per-row grounding certificates as the late
  backstop (grounding_certificate.verify_row, enforced generation.py:20740+);
  `phase3/kernel.DecisionStore.get` (:93-104) has no integrity check — a
  well-formed tampered decision replays as authoritative; the settled-rows
  snapshot IS digest-gated on read (build_concepts_release.py:1176-1177, silent
  skip on mismatch).
- Envelope tampering is detected (envelope.py:54-60 seal; EnvelopeError) but
  silently absorbed by rebuild (concept_topology_contract.py:165-166).
- Asset bytes: content-addressed filenames but served bytes are never re-hashed
  (step 10 posture; in-place file swap serves altered bytes under immutable
  headers). Existing cache tests cover presence/absence, none write tampered
  bytes into `fallback._CACHE_DIR`, `phase22._CACHE_DIR`, or `phase3-decisions`.

## 8. Q13 tension inventory (pre-existing; documented, not fixed here)

Reachable mid-run raises that are not sanctioned carve-outs (fixer.py:13-38):
degenerate-bbox abort with deterministic retry-failure (fallback.py:2121-2122 /
:2142-2143 — the sharpest); page-coverage mismatch (:2082-2085); fatal gate issue
in the accept-with-flags function (:2295-2302, reached via 3.6 turnover, which
also wraps everything into a no-release halt at :302-306); the human-decision
asset path (phase3.py:4203-4206 vs the guarded autonomous twins at :2655/:3121);
kernel ContractError with no fixer wired (kernel.py:319-325 — only 3 generation
paths supply `default_provider()`), amplified by fail-fast parallel_map
(:158-163); `runner.run` called bare (concept_topology_contract.py:191) while
prequestions.py:618/:722 shows the flag-and-continue pattern the other passes
lack. Sanctioned and correctly so: quota (generation.py:2786-2789),
LiveApiUnavailable, Fixer-tried-and-failed (kernel.py:310-318), and the
no-candidate transcription raise (fallback.py:2044-2051, explicit comment).

## 9. Constraints on this step

- Frozen for me: `build_concepts_release*.py`, `build_concepts.py`,
  `assessment_workbook.py`, `bulk_import/writer.py`, `generation.py`,
  `assessment_release*.py` (step-8 concurrency), plus phase3/ dir, golden/,
  acceptance test, `data/Testing/`, frontend/. Fault tests EXERCISE these, never
  edit them; a needed change is a stop-and-report.
- Interrupted release / interrupted publication: NOT built (S9/S10 rewrite those
  paths); residues.
- No API keys in this environment → live acceptance runs are operator actions;
  the corpus ships with an offline harness plus a thin live runner script.
