import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .bulk_import import reader
from .db import SessionLocal, init_db
from .api import (
    directory as directory_api,
    build_assessments as build_assessments_api,
    build_concepts as build_concepts_api,
    data as data_api,
    tagging as tagging_api,
)


def bootstrap() -> None:
    """Load the Bulk Import database workbook into the normalized DB on first run."""
    init_db()
    db = SessionLocal()
    try:
        from . import models
        if db.query(models.Chapter).count() == 0 and config.BULK_IMPORT_DB.exists():
            reader.import_workbook(db, config.BULK_IMPORT_DB)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    yield


app = FastAPI(
    title="Aegis — Integrated Content Management Tool",
    description=(
        "Build Assessments and Build Concepts over a Bulk Import workbook "
        "database. All output is written in the canonical Bulk Import format, "
        "append-only."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(directory_api.router)
app.include_router(build_assessments_api.router)
app.include_router(build_concepts_api.router)
app.include_router(data_api.router)
app.include_router(tagging_api.router)


# Serve the built frontend from the same origin when available. In dev
# (uvicorn --reload, no `npm run build`) this directory won't exist and
# the block is skipped — Vite's dev server handles the UI on :5173.
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST_DIR", "/app/frontend_dist"))

if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
