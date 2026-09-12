# AGENTS.md

## Cursor Cloud specific instructions

### Product

**Aegis** is a monorepo: Python FastAPI backend (`backend/`) + React/Vite frontend (`frontend/`). Canonical data lives in Bulk Import Excel workbooks under `backend/data/`; SQLite (`backend/aegis.db`) caches normalized rows for the API. Default **dry mode** needs no API keys.

### System dependency (fresh Ubuntu VMs)

If `python3 -m venv` fails with “ensurepip is not available”, install once (not in the update script):

```bash
sudo apt-get install -y python3.12-venv
```

### Services (local dev)

| Service | Port | Start |
|---------|------|--------|
| Backend (Uvicorn) | 8000 | `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000` |
| Frontend (Vite) | 5173 | `cd frontend && npm run dev` |

Run `python backend/scripts/generate_dummy_data.py` once (or after wiping `backend/data/`) before meaningful E2E; CI runs it before `pytest`.

Frontend API base URL: `frontend/.env.development` → `VITE_API_BASE=http://localhost:8000`.

### Lint / test / build (matches CI)

See root `README.md` and `.github/workflows/ci.yml`:

- **Backend:** `cd backend && source .venv/bin/activate && pytest -q`
- **Frontend:** `cd frontend && npm run build && npm test -- --run`

There is no separate ESLint/ruff job in CI; TypeScript compile is via `npm run build`.

### Gotchas

- Backend `bootstrap()` imports the workbook into SQLite only when the DB has zero chapters; tests use `aegis_test.db` via `conftest.py`.
- Uvicorn without `npm run build` does not serve the UI; use Vite on :5173 for browser dev.
- `AEGIS_USE_LIVE=true` plus OpenAI/Mathpix keys enable live hooks, but live paths are largely stubbed; dry stubs are the default.

### Docker (optional)

`docker compose up --build` runs both services; copy `.env.example` to `.env` if needed.
