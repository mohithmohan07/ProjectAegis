# Step 10 map — image durability (Q8)

Measured on `main` @ 3aea81e, 2026-08-19. Baseline suite: **2314 passed, 6 xfailed**
(fresh isolation dir `/tmp/tmp.1kVXvcwX2K`, 128.52s, command from the brief).
Everything below was read from the code or executed; docstrings were not trusted.
Where a claim rests on inference rather than direct evidence it is labeled.

---

## 1. Where an asset URL enters a published artifact

### Minting — exactly one generator, two call sites

- `canonical_source_phase221_fallback.py:2126-2163` `materialize_visual_assets`:
  crops each model-identified `figure` block (`page.get_pixmap(matrix=fitz.Matrix(2.0,2.0),
  clip=…, alpha=False)`, `tobytes("jpeg", jpg_quality=88)`), names the file by its
  content — `filename = f"{_sha256_bytes(data)}.jpg"` (:2153) — writes it atomically and
  idempotently (`if not destination.exists()` :2154-2156), then stamps the block:
  `block["asset_filename"]` (:2157), `block["asset_url"] = asset_url(job_id, filename)` (:2158).
- `asset_url()` (:216-218) = `{public_base}/source-assets/{job_id}/{sha256}.jpg?sig={40hex}`;
  `asset_signature()` (:211-213) = HMAC-SHA256(`"{job_id}:{filename}"`, secret)[:40].
- Only two callers in the tree (grep-verified, exactly two):
  `canonical_source_phase221_fallback.py:3426` (`reconstruct_pdf_to_acsd`, full fallback lane)
  and `canonical_source_phase3.py:5815` (`load_page_evidence`, gated by `needs_assets`
  :5809-5813 — any figure block missing `asset_url`).
- `grep -rn "source-assets" backend/ --include=*.py | grep -v tests` → exactly 2 hits:
  the mint (:218) and the route prefix (`api/source_assets.py:9`). No third producer/consumer.

### Rendering into content

- `katex_rules.py:59-70` `kr.image(src, alt)` → `[img src="https://…" alt="…"]`, https
  required (:63, `_public_http_url` :43-57). This is the canonical rich-text image grammar
  (§5, aegis-restructure.md:498-499).
- MMD: `render_page_acsd_to_mmd` embeds `![caption](url)` markdown images
  (phase221_fallback.py:2398-2403 via `_markdown_image` :2200-2203).
- Phase 3 verified-page repair: `_render_page_visual_marker` (phase3.py:2459-2475) renders
  `kr.image(figure["asset_url"], caption)` into table cells (:2478-2483), replacement block
  text (:2555-2571), and `source_override.resolved_text` (:2574-2617, persisted in the
  semantic graph artifact).
- Model-authored content re-embeds the tags verbatim on instruction
  (generation.py:1055, :1618, :2178, :2311-2314; `_inventory_task_text` :8403-8439);
  `concept_validator.py:1574-1583` enforces "Every Figure reference must match a canonical
  [img]" — so `concept_details`, `digicards`, and question text carry the URL verbatim.

### Persistence of the minted (signed) URL

On disk, per job (`/data/uploads/{job_id}/source-shadow/`; layout: `canonical_source_contract.py:21-26`
installed as `uploads.source_artifact_directory` at :263 — a monkey-patch, not a def in uploads.py;
`ASSET_DIRNAME="assets"` phase221_fallback.py:64):

| File | URL-bearing? | Evidence |
|---|---|---|
| `assets/{sha256}.jpg` | bytes themselves | :2153-2156 |
| `source.gpt-page-acsd.json` | yes, per figure block | `_persist_bundle` :3191-3203 runs after materialize (:3426) |
| `source.phase3-page-acsd.json` | yes | phase3.py:5821, re-written by `write_artifacts` :5837-5838 |
| `source.raw.mmd`, `source.aegis.mmd` | yes (markdown/canonical img) | :3192, :3196; canonical_source.py:754 |
| `source.canonical.json` | yes (figure relationships keyed by asset_url, task `image_urls`) | phase221_fallback.py:2994; canonical_source.py:556, :710 |
| `source.semantic-graph.json`, `source.semantic.mmd` | yes (`resolved_text` overrides) | phase3.py:50-52, :5834-5836; :1999-2009 |

In the DB (models.py): `UploadJob.mmd_text` (:300; set at canonical_source_phase221_contract.py:104),
`UploadJob.question_inventory` (:310 — inventory items carry `image_urls`; the staged release
payload under `_aegis_release_output` / `_aegis_pre_release_output` holds `concept_details`
with `[img]` tags), `UploadJob.generation_checkpoint` (:313), `Concept.concept_details` /
`digicards` (:70,:72), `Question.question` / `question_text` / `answers` / `image_manifest`
(:133-141, :161 — `[{"url","alt","sha256","order",…}]`), `AssessmentRelease.concept_snapshot`
and `.payload` (:422,:431).

Off-box (URLs leave the machine): portable checkpoint bundles `*.aegis-checkpoint.json`
(checkpoints.py:1612-1629 embed `mmd_text` + full `question_inventory`), optionally mirrored
to Google Drive (drive_checkpoints.py); every xlsx/json/zip download below.

### The four outputs — all four carry image references

aegis-restructure.md:416-421: **01 Post-Learning Concept Review, 02 Post-Learning Master
File, 03 Pre-Learning Concept Review, 04 Pre-Learning Master File** (lane mapping:
build_concepts_release_files.py:57-58 — post→01, pre→03; assessment_workbook.py:176-196).

- 01/03 carry `[img]` inside `concept_details`/`digicards` cells
  (assessment_release_snapshot.py:152-168 → assessment_workbook.py:162-191;
  bulk_import/writer.py:617-622 — verbatim, no stripping).
- 02/04 additionally carry question rows with `[img]` in `question`/`question_text`/answers
  (assessment_workbook.py:229-259) plus `assets`/`image_urls`/`image_manifest` in the payload
  (assessment_materialization.py:409-411), HTTPS-gated at assessment_release.py:153-158.
- Also shipped: `release.xlsx` / `release-bulk-import.xlsx` / `release.json` /
  `diagnostics.zip` / `inventory.csv` (api/build_concepts.py:142,:288,:319,:349,:377),
  `releases/{id}/concepts.xlsx` + `master.xlsx` (api/build_assessments.py:376-397),
  legacy `/data/export*` (api/data.py:52-121), `UploadJobOut.mmd_text` (schemas.py:107).
- Lane inconsistency (residue): `bulk_import/__init__.py:335-355` `to_plain_text` documents
  that `question_text` is the stripped plain projection (`[img …]` → `(Image: alt)`), and the
  legacy lanes obey (db.py:131, post_generation.py:109, reader.py:496); the MES lane instead
  sets `question_text = question` — the rich value — at assessment_materialization.py:385-386
  (verified verbatim), and ships/persists it (assessment_workbook.py:230,
  assessment_release_service.py:628-629).

## 2. What the signature protects, and who the attacker is

- `source_assets` is the ONLY data-serving router mounted without auth (main.py:91; every
  other data router gets `Depends(auth_svc.require_user)` :92-100). Intentional: the URL
  consumers are the external Bulk Import platform and learners' browsers, with no Aegis
  session (config.py:59-61 — "Bulk Import rich text accepts only public images").
- The filename is already a 256-bit content-hash capability token (`_BBOX_RE =
  ^[0-9a-f]{64}\.jpg$` :89, enforced at :222 and :231, traversal guard :233-236). There is
  no listing endpoint. To fetch a crop without the sig you must already know its sha256 —
  which you can only compute from the image bytes you supposedly don't have.
- What the sig adds beyond that: (a) binding to job_id — near-value-free, since the same
  hash under another job is the same bytes; (b) a revocation knob — which is precisely the
  anti-feature Q8 orders removed for published assets; (c) in a bare deployment, nothing:
  the secret chain degrades to `"admin"` (next section), so the sig is forgeable from the
  repo. `test_source_asset_signature_is_unforgeable` (test_canonical_source_phase221_fallback.py:155)
  overstates — it monkeypatches a strong secret; unforgeability holds only when one is set.
- No rate limiting anywhere (only OpenAI-client RateLimitError handling); response
  hardening is nosniff + forced image/jpeg + inline disposition (source_assets.py:25-29).
- Conclusion: for a published asset the signature protects nothing that matters — the
  content-hash filename already carries the unguessability, and the sig's one real power
  (revocation via rotation) is exactly what Q8 forbids for published links. What the sig
  requirement does do today is create the durability failure below.

## 3. Secret rotation: what breaks, and how anyone finds out

- The effective secret chain (all verified, including by execution):
  `AEGIS_SOURCE_ASSET_SECRET` env → `config.SOURCE_ASSET_SECRET` (config.py:63-67 =
  env → `SESSION_SECRET` → `ADMIN_PASSWORD`, default `"admin"` :35) → literal
  `"aegis-local-source-asset-secret"` (phase221_fallback.py:202-208, reachable only if
  ADMIN_PASSWORD is explicitly empty). Executed probe in a clean env:
  `_asset_secret()` → `b'admin'`. fly.toml sets neither `AEGIS_SOURCE_ASSET_SECRET` nor a
  session secret in `[env]` (full file read); `.env.example` documents neither knob
  (only `AEGIS_ADMIN_PASSWORD` :107, `AEGIS_SESSION_SECRET` :119). So unless a dedicated
  secret was set out-of-band via `fly secrets`, **rotating the admin password or session
  secret silently rotates asset signatures**.
- Validation recomputes with the CURRENT secret (:221-225). Rotation therefore 404s every
  URL already frozen into: both page-acsd JSONs, mmd/canonical artifacts, `UploadJob.mmd_text`,
  question/concept rows, staged release payloads, checkpoint export bundles (off-box,
  unreachable forever), and every downloaded xlsx a school holds.
- Nothing re-signs: `asset_url()` is called only from `materialize_visual_assets`;
  `rehydrate_verified_fallback` (:3322-3408) rebuilds artifacts from the stored JSON without
  re-materializing (verified: no materialize/asset_url call in the range) — old sigs are
  copied forward into "fresh" artifacts. One narrow self-heal exists: deleting the
  `asset_url` fields (or the phase3-page-acsd artifact) makes `needs_assets` true and
  re-mints with the current secret, model-free (phase3.py:5809-5820) — but every downstream
  frozen copy (DB rows, workbooks in the wild) stays dead.
- How anyone finds out: they don't, structurally. The endpoint returns the same bare 404
  for bad sig, bad name, and missing file (source_assets.py:16,:20,:22), logs nothing
  (module imports no logging — 32 lines), and successful responses carry
  `Cache-Control: public, max-age=31536000, immutable` (:27) — so pre-rotation viewers keep
  seeing cached images up to a year while fresh viewers get 404s: inconsistent, delayed,
  hard-to-diagnose breakage. The frontend never renders these URLs (grep over frontend/src:
  zero matches), so no in-app surface would show it. R4's spirit ("the SILENCE is the
  defect") is violated by design here: a learner-facing image dies with no record anywhere.

## 4. The content hash: where it is, and whether it is stable

- The filename IS the content hash: sha256 of the JPEG bytes (:2153). There is no separate
  per-asset manifest today — the figure-block records (`asset_filename` + `asset_url`
  embedded in the ACSD JSONs) are the de-facto manifest. The Q8 "manifest entry" per asset
  does not yet exist as a discrete artifact.
- Determinism, probed empirically (two processes, same env, PyMuPDF 1.28.2): identical
  sha256 for the same (PDF, bbox, matrix 2.0, quality 88). No timestamp/random input in the
  path. BUT: `requirements.txt:12` floats `PyMuPDF>=1.24.0` (every other core dep is
  `==`-pinned) — a MuPDF upgrade may change rasterization/encoder bytes → different hash
  for the same figure. And bboxes are MODEL verdicts (normalized 0-1000, prompt at :462,:470;
  sanitizer only clamps :1571-1583) — a re-extraction (new pdf bytes, new model, cache
  cleared) can move crops → new hashes.
- Re-run behavior differs by lane (both verified):
  - Phase 3 lane: cached `source.phase3-page-acsd.json` (gate :5788-5794 checks pdf_sha +
    compiler + schema only) short-circuits materialization when `asset_url` is present —
    old URLs reused verbatim, hashes cannot shift. Caveat: the gate checks JSON field
    presence, never that the file still exists in `assets/`.
  - Fallback lane: `reconstruct_pdf_to_acsd` re-materializes EVERY run into a staging dir,
    and `_commit_staged_bundle` (:3206-3250) `os.replace`s the whole `assets/` dir
    (:3230-3236, `ASSET_DIRNAME` in managed_names :3218). **A re-conversion under a changed
    renderer/bboxes deletes the old hashed files** — previously published URLs dangle even
    with no rotation and no reset.
- Step-8-analogue found (name claims content identity, value is run-derived):
  `page_acsd_sha256` (:3291-3293) hashes the bundle AFTER materialize stamped absolute
  URLs into it, so it transitively encodes job_id, secret, and base URL. Same book, other
  job/deployment → different "content" hash. Today it has no consumer beyond its write site
  (grep), so it is display metadata — but any future comparer will misfire. Same
  contamination: `derived_mmd_sha256` (:3450,:3377-3379), `report["source_sha256"]`
  (:3457,:3386). Also `"raw_pdf_changed": False` is hardcoded (:3290), never computed.
- Global caches are clean: the sealed bundle cache (`/data/pdf-acsd-cache/`, key includes
  FALLBACK_VERSION/COMPILER/OPENAI_MODEL + pdf_sha :602-611) is written at :2099-2106 with
  a deepcopy BEFORE any caller materializes — cached bundles carry no asset fields
  (read first-hand; deepcopy insulates from later mutation). Batch and outline caches same.

## 5. Volume backup today

- One Fly volume `aegis_data` → `/data` (fly.toml:27-29) holds EVERYTHING learner-facing:
  `aegis.db` (sqlite + WAL), `bulk_import_database.xlsx` ("IS the database", config.py:69-70),
  `bulk_import_output.xlsx` (append-only), `uploads/{job}/…` (PDFs + source-shadow artifacts
  + assets), `assessment_releases/{uid}/v{n}` (assessment_release_service.py:67,:71),
  `assessment-decisions/`, `workbooks/`, `syllabus/`, `pdf-acsd-cache/`,
  `source-adjudication-cache/`, `prompt_overrides.json`, `model_provider.json`.
- No backup tooling exists in the tree: no object storage, no litestream, no snapshot cron,
  no fly.toml backup config (greps over repo for backup|snapshot|s3|tigris|litestream:
  only the Drive checkpoint mirror). fly.toml has no `snapshot_retention` (Fly default:
  5 daily snapshots).
- The one existing mechanism: optional, env-gated (`AEGIS_DRIVE_CHECKPOINT_BACKUP_ENABLED`,
  default off per .env.example:141) Google Drive mirror of build_concepts checkpoint
  bundles only (drive_checkpoints.py:1-7; scheduled from api/build_concepts.py:610;
  lifecycle in main.py:53-57). It covers job fields + mmd_text + checkpoint + inventory —
  NOT the DB, not the workbooks, not the PDFs, **not the asset files**.
- Redeploy: image is rebuilt, `/data` survives. But `Dockerfile:30` defaults
  `AEGIS_DATA_DIR=/app/data` — a deployment that lost fly.toml's `[env]` override would
  silently write to the ephemeral filesystem.
- Machine topology: `min_machines_running=0`, no count pinning; single-machine is operator
  discipline only (README.md:233-246 — `--ha=false`; volumes do not replicate).
- If the volume is lost, unrecoverable today: the whole relational store, both Bulk Import
  workbooks, all uploaded PDFs, all published asset crops (every distributed URL 404s
  forever), assessment release files, decision stores, prompt overrides. Recoverable:
  code/image, bundled syllabus; checkpoint state only if the Drive mirror was on or a human
  downloaded bundles (README.md:135-136 overstates: "administrator-only backup" exists only
  when the mirror is configured).
- Two in-app actions already produce the orphaned-URL failure without any disaster:
  `data_reset.reset_all` clears `UPLOAD_DIR` (data_reset.py:44), and `replace_file` rmtree's
  the job's `source-shadow` dir (canonical_source_contract.py:252-259 →
  canonical_source.py:1214-1215). Signatures still validate; files are gone; published
  images 404 silently.

## 6. What a content-addressed, non-expiring URL looks like here, and what breaks

- The register's design (aegis-restructure.md:211-218, :878-886): keep Fly hosting; links
  in published content must be durable non-expiring public URLs; volume backed up; every
  asset carries a content hash and manifest entry so a later publication-time rewrite can
  migrate to UpSchool hosting (that migration itself: out of scope, named three times).
  The "manifest-driven URL rewrite" design in the docs is exactly those two sentences —
  no schema or mechanics exist anywhere (verified by the docs sweep).
- The content hash is already in the URL path. What makes today's URL non-durable is
  (a) the `?sig=` tied to a rotatable, badly-defaulted secret; (b) the job-scoped physical
  path that dies on reset/replace/re-conversion; (c) the origin pinned to
  `https://projectaegis.fly.dev` (fly.toml:17) — a domain move breaks every URL
  independently of everything else (that is the manifest-rewrite's later job).
- What breaks if the endpoint stops requiring a valid sig: **no existing test** — the
  HTTP-layer rejection path is completely unpinned (verified: the only TestClient GET on
  the route is the happy path, test_canonical_source_phase221_fallback.py:301; the only
  direct endpoint call passes a valid sig, :388-410). The unit test
  `test_source_asset_signature_is_unforgeable` (:155-162) pins `validate_asset_signature`
  itself and survives as long as the function remains.
- What breaks if the URL *shape* changes (dropping job_id): :137's hard-coded
  `/source-assets/42/` prefix assert, the positional endpoint signature (:388-410), and —
  decisively — every URL already frozen in the corpus. A shape change is therefore the
  wrong move; the existing shape must keep resolving. Golden fixtures are unaffected either
  way (backend/tests/golden/ image URLs are all cdn.mathpix.com; zero `/source-assets/`
  or `sig=` matches).
- Old signed links must keep working after any change (they are in schools' hands); new
  links may simply carry no sig, or a sig that is accepted-but-not-required. `kr.image`
  only requires https (katex_rules.py:63) — it does not constrain the query string.

## 7. Untraced lanes checked and closed

- `backend/scripts/chapter_authoring/run_chapter.py:26-29` (and resume_chapter.py:24) —
  local authoring runs stamp `https://aegis.local/source-assets/…?sig=…` (secret
  `local-run-secret`) into canonical artifacts and release workbooks intended for bulk
  import into production: permanently dead URLs entering the corpus with no rewrite path.
  Residue for this step's PR body.
- `backend/aegis_pipeline/` (incl. create_workbooks) and `frontend/src`: zero asset-URL
  handling (greps: no `[img`/`source-assets`/`asset_url` consumers). workbooks.py is a
  separate PDF product (out of this restructure's scope per aegis-restructure.md:700).
- `question_polishing.py:238` reads only `bool(item.get("image_urls"))` — no URL emission.

## 8. Doctrine positioning (Rule 1 / Q13 / Q10 / R4)

Image durability is mechanics end-to-end: hashing, path resolution, HTTP serving, manifest
writing, backup copying — no content judgment anywhere. The gates involved (filename regex,
path containment, existence checks, hash verification on backup) are "gates that refuse to
accept a broken artifact", explicitly allowed. Two doctrine edges to respect when building:

- Serving a missing asset is not a mid-run event — there is no run. It must not guess, and
  today it also does not record (silent 404). Any new not-found handling stays a mechanical
  gate; per R4's spirit the loss should become visible (a record/counter), never a guess.
- Publication-time asset verification belongs to the model-free "publication hardening"
  contract (aegis-restructure.md:475-478), not to The Fixer (fixer.py:27-28 explicitly
  defers writer/publication seams F43-F45); a missing asset detected MID-RUN (during
  generation) would route through the Fixer per Q13 — no new `raise` on generation paths.
