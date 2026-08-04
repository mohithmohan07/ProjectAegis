import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import SessionLocal, init_db
from .services import syllabus_import as syllabus_svc
from .services import auth as auth_svc
from .services import drive_checkpoints
from .services import build_concepts_release_api_contract
from .services import build_concepts_release_contract
from .services import build_concepts_release_manifest
from .api import (
    admin as admin_api,
    auth as auth_api,
    directory as directory_api,
    build_assessments as build_assessments_api,
    build_concepts as build_concepts_api,
    data as data_api,
    source_artifacts as source_artifacts_api,
    source_assets as source_assets_api,
    tagging as tagging_api,
    workbooks as workbooks_api,
)


def bootstrap() -> None:
    """Initialize the database schema and preload syllabus structure if empty."""
    auth_svc.validate_configuration()
    build_concepts_release_manifest.install()
    build_concepts_release_contract.install()
    init_db()
    db = SessionLocal()
    try:
        syllabus_svc.bootstrap_syllabus(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    drive_checkpoints.initialize_checkpoint_backup(SessionLocal)
    try:
        yield
    finally:
        drive_checkpoints.shutdown_checkpoint_backup()


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
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


# Patch only the user-facing Build Concepts upload routes before FastAPI copies
# them into the application. Internal generation services keep their original
# contracts for recovery tooling and programmatic callers.
build_concepts_release_api_contract.install(build_concepts_api.router)

app.include_router(auth_api.router)
app.include_router(source_assets_api.router)
_authenticated = [Depends(auth_svc.require_user)]
app.include_router(directory_api.router, dependencies=_authenticated)
app.include_router(build_assessments_api.router, dependencies=_authenticated)
app.include_router(build_concepts_api.router, dependencies=_authenticated)
app.include_router(source_artifacts_api.router, dependencies=_authenticated)
app.include_router(data_api.router, dependencies=_authenticated)
app.include_router(tagging_api.router, dependencies=_authenticated)
app.include_router(workbooks_api.router, dependencies=_authenticated)
app.include_router(admin_api.router, dependencies=_authenticated)


# Serve the built frontend from the same origin when available. In dev
# (uvicorn --reload, no `npm run build`) this directory won't exist and
# the block is skipped — Vite's dev server handles the UI on :5173.
FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST_DIR", "/app/frontend_dist"))


def _safe_frontend_file(root: Path, request_path: str) -> Path | None:
    """Resolve a SPA asset path without allowing traversal outside ``root``."""
    resolved_root = root.resolve()
    candidate = (resolved_root / request_path).resolve()
    if (
        candidate != resolved_root
        and resolved_root not in candidate.parents
    ):
        return None
    return candidate if request_path and candidate.is_file() else None


if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = _safe_frontend_file(FRONTEND_DIST, full_path)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
