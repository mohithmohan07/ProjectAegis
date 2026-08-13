# Aegis — Integrated Content Management Tool

Aegis is the content intelligence and assessment-building engine inside Clarius.
It is a single integrated tool over a **Bulk Import workbook database**: every
chapter, topic, concept, group and question lives in the canonical Bulk Import
format, and everything the tool generates is written back to it — **append-only**.

## The two modules

### 1 · Build Assessments

**a · From Concept Mapping** — drill the directory (Board → Class → Subject →
Unit → Chapter), scope to the whole chapter, specific topics, or specific
concepts. Question content always comes from the concept level, so chapter and
topic scopes fan out to their concepts. Stack one or more **Blueprint settings**
(Cognitive Skill × Difficulty × Category × Question Type × count) in a single
session, then Generate.

**b · From Upload** — upload a PDF / text / handwritten image, convert it to MMD
(KaTeX), pick the upload type (Textbook / Questions / Questions & Answers /
Handwritten). For textbooks, choose to *extract* existing Q&A or *create* new
questions. Choose where to deposit in the directory (chapter / topics /
concepts), then identify and generate — Bulk Import columns are filled from the
directory selection.

### 2 · Build Concepts

**Post Learning** — upload a document (any format) → convert to MMD → parse
concepts → deposit under a chapter.

**Pre Learning** — either upload a document, or **use existing Post Learning**:
pick one or more chapters and derive prerequisite concepts from their existing
post-learning concepts.

### Post-generation pipeline

After every generation: **assessment tagging** (cluster questions, build group
descriptions) → **column mapping** (fill remaining canonical columns) →
**append** to the Bulk Import output workbook. Existing `question_label`s are
never overwritten.

## Canonical Bulk Import format

The workbook has three content sheets — Objective, Subjective, Descriptive —
each with two header rows (section bands + field names) and the hierarchical
column blocks Chapter → Topic → Concept → Group → Question → Answers. Exact
field orders live in `backend/app/bulk_import/__init__.py` (65 / 92 / 374
columns, including `concept_source` and the trailing `question_text`).
`backend/app/bulk_import/reader.py` and `writer.py` round-trip it; the reader
auto-detects older templates without the newer columns.

## Layout

```
backend/
  app/
    bulk_import/      canonical schema + reader + append-only writer
    services/         directory, mmd, generation, build_assessments,
                      build_concepts, post_generation
    api/              directory, build_assessments, build_concepts, data
    models.py         normalized Chapter/Topic/Concept/Group/Question + jobs
  aegis_pipeline/     vendored prior scripts (live-mode reference impls)
  data/               user workbooks, uploads, and generated output
  scripts/            generate_dummy_data.py (optional dev fixture)
  tests/              pytest suite (including review-feedback regressions)
frontend/             React + Vite + TypeScript UI (the two modules + Database)
```

## Dry vs live mode

Every generation step has a **dry** path (deterministic, realistic stub content,
no API keys — used for the MVP and tests) and a **live** hook that delegates to
the vendored scripts. Live mode activates when the relevant environment
variable is set:

```bash
export OPENAI_API_KEY=...                 # question / concept generation,
                                          # and PDF → canonical source
```

PDFs are read by the GPT PDF-to-ACSD reader, so `OPENAI_API_KEY` is the only
credential a conversion needs. There is no separate OCR service.

The `_live_*` hooks in the service layer mark exactly where inputs must be wired.

## Run locally

On Windows, keep the Git checkout and its `.venv` / `node_modules` outside a
OneDrive-synced folder (for example, `C:\Projects\ProjectAegis`). Git already
provides source history, while OneDrive can interpret normal environment
rebuilds as thousands of file deletions. If OneDrive presents a large,
unexpected deletion prompt, keep the files and inspect the target folder first.

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The app starts with an **empty database** unless syllabus workbooks are present
in `backend/data/syllabus/` (see that folder's README). On first startup, unit
and chapter shells are loaded automatically so you can deposit concept mapping
into the right place. Import a full Bulk Import workbook from the Database tab
when you need questions and concepts too.

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: http://localhost:5173 — Home, Build Assessments, Build Concepts, Database.
The development server forwards Aegis API requests to the local backend on
port 8000, so local mode needs no hosted service or Google/Drive connection.

### Docker

```bash
docker compose up --build
```

## Durable generation checkpoints

Build Concepts automatically commits each completed generation stage to the
server before continuing. After a refresh or sign-in from another laptop, Aegis
offers the signed-in user the newest compatible unfinished run with three
choices: **Resume**, **Keep for later**, or **Discard**. Resume restores the
document and exact directory target; the user still confirms the destination
and starts the billable generation request explicitly. A second request for a
job that is already running is rejected instead of duplicating OpenAI work.
Discard removes the resumable stage from the server and overwrites the Drive
mirror with a non-resumable bundle; the converted source remains in that
administrator-only backup for disaster recovery.

Each converted concept job can also be downloaded as an
`*.aegis-checkpoint.json` bundle. The bundle contains the converted MMD,
compatible pipeline state, inventory, usage totals, and the latest diagnostic
log. Store this file in private durable storage such as Google Drive, then use
**Restore checkpoint** after a deployment or in another compatible Aegis
installation. Do not commit live checkpoint bundles, uploads, databases, or
generated workbooks to Git; Git remains the source of truth for code,
migrations, prompts, and sanitized regression fixtures.

### Hosted access for UpSchool

The checked-in Fly configuration enables Google sign-in and limits hosted
access to verified `@up.school` Google Workspace accounts. The backend verifies
the Google ID token, its audience, the hosted-domain claim, and the verified
email domain, then isolates uploads and resumable jobs by Google's stable user
ID. It fails closed if hosted authentication is incomplete.

Create an **Internal** OAuth consent screen and a Web application OAuth client
in Google Cloud. Add `https://projectaegis.fly.dev` as an authorized JavaScript
origin; add `http://localhost:5173` only if Google sign-in is also needed during
local development. Store these values as Fly secrets:

```powershell
$sessionSecret = python -c "import secrets; print(secrets.token_urlsafe(48))"
$adminPassword = python -c "import secrets; print(secrets.token_urlsafe(32))"
fly secrets set AEGIS_GOOGLE_CLIENT_ID="CLIENT_ID.apps.googleusercontent.com" `
  AEGIS_SESSION_SECRET="$sessionSecret" `
  AEGIS_ADMIN_PASSWORD="$adminPassword" --app projectaegis
```

Save the generated admin password in the team's password manager. It protects
global prompt editing and the irreversible **Clear all data** action.

If the Fly volume already contains runs created before authentication was
added, explicitly name the one verified user who may adopt those legacy runs:

```powershell
fly secrets set AEGIS_LEGACY_OWNER_EMAIL="owner@up.school" --app projectaegis
```

On that user's first successful Google sign-in, Aegis assigns legacy uploads,
saved checkpoints, and assessment sessions to their stable Google identity.
Remove the
bridge afterward with
`fly secrets unset AEGIS_LEGACY_OWNER_EMAIL --app projectaegis`. If the value
is omitted or another user signs in, legacy runs remain inaccessible rather
than being exposed to the domain.

Do not deploy the Google-authenticated configuration until all three values are
set. Local development remains fully usable without Google or Fly: leave
`AEGIS_AUTH_MODE=local` (the default in `.env.example`) and run the normal
backend and frontend processes. This local mode is intentionally a
single-user, offline-capable workspace.

### Optional automatic Google Drive mirror

The Fly volume remains the primary checkpoint store. Aegis can additionally
queue a background Drive backup after every successful server checkpoint; a
Drive failure is logged but never interrupts generation. The manual
**Download checkpoint** and **Restore checkpoint** controls remain available
as portable fallback.

For unattended service-account uploads, use a folder inside a Google
**Shared Drive**. A service account has no personal Drive storage quota and
cannot own files in a normal My Drive folder. Create a dedicated Shared Drive
folder, add the service account only to that location, enable the Drive API,
and configure:

```powershell
$driveJsonB64 = [Convert]::ToBase64String(
  [IO.File]::ReadAllBytes("C:\secure\aegis-drive.service-account.json")
)
fly secrets set AEGIS_DRIVE_SERVICE_ACCOUNT_JSON_B64="$driveJsonB64" `
  --app projectaegis
fly secrets set AEGIS_DRIVE_CHECKPOINT_BACKUP_ENABLED=1 `
  AEGIS_DRIVE_CHECKPOINT_FOLDER_ID="SHARED_DRIVE_FOLDER_ID" `
  --app projectaegis
```

Workspace administrators may instead configure domain-wide delegation and
`AEGIS_DRIVE_IMPERSONATE_USER` for a dedicated human backup account. Keep the
automatic-backup location limited to the service account and designated
administrators; sharing it with the whole domain would let users inspect one
another's source bundles. Do not use “Anyone with the link can edit.” A normal
My Drive folder can still be used for deliberate manual bundle
upload/download, but not for plain service-account backup.
Set `VITE_CHECKPOINT_DRIVE_FOLDER_URL` at frontend build time if the UI should
link to a different backup folder.

The checked-in Fly configuration mounts the `aegis_data` volume at `/data` and
stores both runtime files and SQLite there. Create the encrypted volume once in
the app's primary region before deploying this configuration:

```bash
fly volumes create aegis_data --app projectaegis --region ams
fly deploy --app projectaegis --ha=false
```

A Fly volume is the practical **single-machine** bridge for this app. Keep
exactly one active Aegis machine while SQLite and checkpoints live on that
volume; two volumes are independent and do not replicate one another. Fly's
generic high-availability volume recommendation assumes the application has a
replicated data layer, which this version intentionally does not. Before
removing a machine or volume from an existing multi-volume app, first identify
and back up the volume containing the current `/data` directory.

A multi-machine production deployment should move run metadata and events to
managed PostgreSQL and large checkpoint/upload artifacts to private object
storage; the portable bundle remains the human-controlled backup.

## Tests

```bash
cd backend && pytest                 # full backend and review-regression suite
cd frontend && npm test -- --run
```

The review-to-implementation traceability assessment is maintained in
[`REVIEW_AUDIT.md`](REVIEW_AUDIT.md). Update that assessment whenever a new
review version changes the generation contract.

## Connecting the real workbook

Replace `backend/data/bulk_import_database.xlsx` with the real Clarius Bulk
Import workbook (or import one from the Database tab). Board / Grade / Subject
are parsed from the ID prefixes (`10CBMA_…`) by `services/directory.py`; nothing
else needs to change.

## Canonical source recovery

Build Concepts reads PDFs with verified GPT PDF-to-ACSD extraction and uses evidence-backed PDF adjudication for bounded omissions. The reader preserves page order, exact source wording, mathematical structure and source-owned visual crops before compiling through the same deterministic ACSD gates. See `docs/aegis-canonical-source-phase-2-2-1.md`.
