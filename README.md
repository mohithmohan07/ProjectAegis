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
  tests/              pytest suite (25 tests)
frontend/             React + Vite + TypeScript UI (the two modules + Database)
```

## Dry vs live mode

Every generation step has a **dry** path (deterministic, realistic stub content,
no API keys — used for the MVP and tests) and a **live** hook that delegates to
the vendored scripts. Live mode activates when the relevant environment
variables are set:

```bash
export OPENAI_API_KEY=...                 # question / concept generation
export MATHPIX_APP_ID=... MATHPIX_APP_KEY=...   # PDF/image → MMD
```

The `_live_*` hooks in the service layer mark exactly where inputs must be wired.

## Run locally

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

### Docker

```bash
docker compose up --build
```

## Tests

```bash
cd backend && pytest                 # 25 tests
cd frontend && npm test -- --run
```

## Connecting the real workbook

Replace `backend/data/bulk_import_database.xlsx` with the real Clarius Bulk
Import workbook (or import one from the Database tab). Board / Grade / Subject
are parsed from the ID prefixes (`10CBMA_…`) by `services/directory.py`; nothing
else needs to change.
