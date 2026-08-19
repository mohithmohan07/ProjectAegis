# Step 10 spec — image durability (Q8)

Everything in this step is mechanics — hashing, path resolution, HTTP serving, atomic
copies, config — none of it judges content. Gates below are of the allowed kind: they
refuse broken artifacts (bad filename shape, path escape, missing file); they never decide
what the source means. No new failure path is reachable during a generation run except as
a recorded warning (Q13-safe); nothing learner-visible is dropped without a record (R4).

## The problem, from the map

A published workbook's image URL `{origin}/source-assets/{job_id}/{sha256}.jpg?sig=…` dies
today in four independent ways, all silent:

1. Secret rotation (worse: the effective default secret chain ends at `"admin"`, so
   rotating the admin password or session secret rotates asset sigs too).
2. `data_reset.reset_all` clears `UPLOAD_DIR` — every asset file gone, sigs still valid.
3. `replace_file` rmtree's the job's `source-shadow/` dir.
4. Fallback-lane re-conversion `os.replace`s the whole `assets/` dir — old hashed crops
   deleted whenever bboxes or the renderer moved.

The filename is already a 256-bit content hash; the sig adds no protection that matters
for a published asset and is the direct cause of failure mode 1. There is no per-asset
manifest and no backup of any kind.

## Decisions

### D1 — The signature becomes advisory at serve time; minting is unchanged

`GET /source-assets/{job_id}/{filename}` serves any EXISTING file whose name passes the
content-hash gate (`^[0-9a-f]{64}\.jpg$`) and path containment — whether or not `sig`
validates. `asset_url()` keeps minting exactly today's shape (sig included), so no
generator, artifact, cell, or test changes, and every URL already in the wild keeps
resolving under any future secret.

Losing argument: *keep enforcement and version the secrets (accept any historical
secret).* Rejected: a forever-growing secret ring is operational fragility, still breaks
when a secret is lost rather than rotated, and defends nothing the content-hash filename
doesn't already defend. Second losing argument: *drop the sig from minted URLs too.*
Rejected: it would churn the URL string for identical content across deployments and adds
nothing — serve-side behavior is the entire fix.

Security note (from map §2): the route stays public by design (external Bulk Import
platform + learner browsers). Post-change, an attacker can confirm that a *known* image
exists under a *known* job — value-free, since knowing the hash requires the bytes. The
filename gate and containment gate stay.

### D2 — A global content-addressed asset store, pinned at mint time

New service `backend/app/services/source_asset_store.py`; store directory
`DATA_DIR/source-asset-store/`. At mint (`materialize_visual_assets`), each crop's bytes
are atomically copied to `source-asset-store/{sha256}.jpg` (idempotent — existence check)
with a manifest sidecar `source-asset-store/{sha256}.json` (first mint wins) carrying:
`{"sha256", "bytes", "media_type", "job_id", "asset_url", "public_base_url", "created_at"}`
— the Q8 "content hash and manifest entry" that the later UpSchool publication-time URL
rewrite will walk; the hash→bytes mapping plus minted-URL provenance is exactly what a
corpus migration needs.

Serving falls back to the store when the job path misses. That single fallback repairs
failure modes 2, 3, and 4 for every URL whose asset is pinned, and also heals the
checkpoint-import wrinkle (imported bundles referencing an old job_id).

Mint-time pinning alone would leave the entire pre-change corpus unpinned (the audit's
one HIGH finding: the phase-3 lane short-circuits materialization whenever `asset_url`
is already stamped, so existing jobs never re-mint). Two mechanical repairs close it:
a boot-time backfill sweep (`pin_existing_job_assets`, called from `bootstrap()`,
best-effort and idempotent — a failed sweep is a logged warning, never a failed boot;
a crop whose bytes no longer match its content-hash name is recorded, not silently
pinned under a name it doesn't own), and an opportunistic serve-time pin when a request
hits the job-dir copy of a crop the store lacks.

The manifest's identity is the content hash; the sidecar's job/URL fields are first-mint
provenance, not identity. The later corpus rewrite keys on the hash in the URL filename
and may ignore the job segment — later jobs referencing the same bytes deliberately get
no second entry.

A store write failure during generation is a recorded warning (`progress.log`), never a
raise — the job-dir asset still exists, the run completes, durability degradation is
visible (Q13).

Losing argument: *pin at publication time so the store holds exactly the published
corpus.* Rejected: the map found ≥8 diffuse publication surfaces (4 outputs, release.json,
diagnostics.zip, DB upload, mmd via API, checkpoint export); missing one silently loses
durability for that lane — the exact class of defect this step exists to kill. Mint-time
is a strict superset at negligible cost (deduplicated ~50-200KB jpegs). A
published-vs-unpublished distinction can be layered onto the manifest later without
moving bytes.

### D3 — `reset_all` preserves the store

No code change needed (the store lives outside `UPLOAD_DIR`); the behavior is PINNED by a
regression so a future reset "cleanup" can't silently break the published corpus.

Losing argument: *reset means reset — the owner expects a blank slate.* Rejected: Q8 names
published links learner-facing infrastructure; a pipeline reset must not 404 a school's
workbook. A true full wipe stays possible by deleting the directory deliberately
(documented in README).

### D4 — Volume backup: Fly snapshot retention + admin export of the store

- `fly.toml`: `snapshot_retention = 30` under `[[mounts]]` (Fly daily snapshots, month of
  restore points instead of the 5-day default). Operational config, not content judgment.
- `GET /admin/source-asset-store/export` (existing admin auth: router-level
  `require_user` + `require_admin` header token): streams a `.tar.gz` of the store —
  crops + manifest sidecars — giving an off-box recovery/migration package for exactly
  the learner-facing corpus.
- README: a Backups section — what the volume holds, snapshot restore, store export,
  and the existing Drive checkpoint mirror's actual coverage.

Losing argument: *extend the Drive checkpoint mirror to stream every asset off-box
continuously.* Rejected for scope: per-asset upload lifecycle, quota and credential
handling; snapshots + on-demand export cover the recovery paths, and continuous off-box
asset mirroring rides better on the designed UpSchool migration.

### D5 — A serve-time miss becomes visible

On 404 (file in neither location) the endpoint logs a structured warning (job_id,
filename, whether the sig validated). No DB writes on an unauthenticated route (abuse
surface). The silence, not the miss, is the defect (R4 spirit; a serve-time miss is not a
run event, so no Fixer involvement — map §8). A shape-invalid name (not a content hash)
is refused without a log line — that is a malformed request, not a lost asset. The
pin-failure record during generation is emitted through BOTH `progress.log` (streamed
runs) and the module logger (authoring scripts and recovery tooling run without a
progress stream, where `progress.log` is a documented no-op).

Losing argument: *a durable DB miss-ledger auditable in-app.* Rejected: write
amplification on a public route; logs already surface the event operationally.

## Slices

1. **Store service + durable serving** — `source_asset_store.py` (paths, containment,
   `pin(bytes)->sha`, `store_path(filename)`, sidecar write); rewrite
   `api/source_assets.py` per D1/D2/D5.
2. **Mint-time pinning** — `materialize_visual_assets` pins each crop (idempotent,
   non-fatal per Q13); regression for the non-gating failure mode.
3. **Reset/replace survival pins** — regressions proving old URLs serve after
   `reset_all` and after job-artifact deletion.
4. **Backup** — fly.toml retention, admin export endpoint, README section.

New tests live in `backend/tests/test_data_reset_durable_assets.py` (named to sort
directly after `test_data_reset.py`: the suite tolerates a mid-session DB reset only at
that point in the alphabet — a pre-existing order fragility, see the PR residues). The
regressions are proved
fail-without-fix by neutralising the fix (list in the PR body). Frozen paths untouched:
no `backend/app/services/phase3/`, no `backend/tests/golden/`, no frontend/, acceptance
test verbatim, `backend/data/Testing/` untouched.

## Out of scope (residues for the PR body)

- The UpSchool-environment migration and the URL rewrite itself (designed later step).
- `https://aegis.local` URLs minted by `scripts/chapter_authoring` local runs.
- Checkpoint bundles not carrying asset bytes (cross-machine import gap).
- The MES `question_text` rich-vs-plain lane inconsistency.
- `page_acsd_sha256` run-derived contamination; hardcoded `"raw_pdf_changed": False`.
- Dockerfile `AEGIS_DATA_DIR=/app/data` fallback footgun.
- Unpinned PyMuPDF version.
