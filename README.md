# Aegis

Aegis is the content intelligence and assessment-building engine inside Clarius. It converts curriculum maps, uploaded academic material, and question sources into structured, tagged, bulk-import-ready learning assets.

This repository wraps the team's existing pipeline scripts (vendored under `backend/aegis_pipeline/`) in a FastAPI service and a React UI, so the whole flow can be driven, inspected, and exported from one app.

## The pipeline

| # | Stage | Vendored script | Output |
|---|-------|-----------------|--------|
| 1 | Extract PDFs | `extract_pdfs.py` | PDF manifest + files |
| 2 | PDF → MMD (Mathpix OCR) | `extract_mmds.py` | `.mmd` files |
| 3 | MMD → Concepts Excel | `mmd_to_concepts_excel.py` | `concepts.xlsx` |
| 4a | Excel → Pre-Learning Concepts | `excel_to_concepts_prelearning.py` | `pre_learning_concepts.xlsx` |
| 4b | Concept Mapping → Pre-Learning | `concept_mapping_to_prelearning.py` | `pre_learning_concepts.xlsx` |
| 5 | Bulk Question Upload | `bulk_upload_ultimate.py` / `bulk_upload_mathpix.py` | 3-sheet `final_output.xlsx` |
| 6 | Assessment Tagging | `aegis_pipeline/assessment_tagging/` (Apps Script) | tagging decisions |

Every stage runs in two modes:

- **dry** — produces realistic dummy artifacts, no API keys needed. Used for the MVP, demos, CI.
- **live** — delegates to the vendored script. Requires `MATHPIX_APP_ID`, `MATHPIX_APP_KEY`, and/or `OPENAI_API_KEY` depending on the stage. The stage runner reports which keys are missing.

## Canonical schema

**Concept** (mirrors `mmd_to_concepts_excel.py` output): Board, Book, Grade, Subject, Chapter No, Chapter Code, Chapter Title, Topic, Parent Concept, Concept, Concept Description, Concept ID, MMD Path, PDF Path.

**Question** — 3 sheets (`objective` / `subjective` / `descriptive`) with Question Label, Question Category, Cognitive Skills, Question Source, Level of Difficulty, Question, Marks, Answer columns, Answer Explanation.

The dummy fixtures in `backend/data/*.csv` use this exact schema. When the real Clarius sheets are ready, drop them into `backend/data/` and point the loaders at them — no code change needed.

## Layout

```
backend/
  app/                FastAPI service (models, schemas, services, api)
  aegis_pipeline/     vendored pipeline scripts (kept as-is)
  data/               dummy fixtures (CSV committed; XLSX generated)
  scripts/            generate_dummy_data.py
  tests/              pytest suite (26 tests)
frontend/             React + Vite + TypeScript UI
.github/workflows/    CI
docker-compose.yml
```

## Run locally

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_dummy_data.py
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: http://localhost:5173 — Dashboard, Pipeline runner, Concepts browser, Questions browser, Assessment Tagging.

### Docker

```bash
docker compose up --build
```

## Tests

```bash
cd backend && pytest          # 26 tests
cd frontend && npm test -- --run
```

## Enabling live mode

Set the relevant environment variables before starting the backend:

```bash
export MATHPIX_APP_ID=...
export MATHPIX_APP_KEY=...
export OPENAI_API_KEY=...
```

The Pipeline page will show stages as "live ready" once their keys are present. The vendored scripts still contain some hard-coded paths from prior runs; the `_live_*` wrappers in `backend/app/services/pipeline.py` mark exactly where inputs need to be wired before live mode is fully enabled.
