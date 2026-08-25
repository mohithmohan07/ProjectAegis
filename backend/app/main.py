import logging
import mimetypes
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
from .services import build_concepts_terminal_release_contract
from .services import type_coverage_fixer_contract
from .services import language_topology_grade_contract
from .services import four_output_release_contract
from .services import assessment_release_service
from .services import storage_capacity
from .api import (
    admin as admin_api,
    auth as auth_api,
    directory as directory_api,
    build_assessments as build_assessments_api,
    build_concepts as build_concepts_api,
    data as data_api,
    native_auth as native_auth_api,
    source_artifacts as source_artifacts_api,
    source_assets as source_assets_api,
    tagging as tagging_api,
    workbooks as workbooks_api,
)


def bootstrap() -> None:
    """Initialize the database schema and preload syllabus structure if empty."""
    auth_svc.validate_configuration()
    # Re-apply the persisted model-provider selection (OpenAI/Gemini) so a
    # restart keeps pointing every model call at the chosen provider.
    from .services import model_provider

    model_provider.restore()
    # Install source/content decision contracts before any run can reach their
    # model prompts. The literary contract deliberately re-keys language-plan
    # decisions so a stanza-count-authored plan can never replay under the new
    # grade/source-calibrated instructions.
    language_topology_grade_contract.install()
    build_concepts_release_manifest.install()
    build_concepts_release_contract.install()
    build_concepts_terminal_release_contract.install()
    type_coverage_fixer_contract.install()
    # The Job-79 literary contracts transform the Phase-3 envelope and author
    # the Pre sibling inside the rewritten runner. Install the mechanical
    # handoff last so the durable envelope and release capture are the exact
    # artifacts those contracts produced, not their pre-transformation inputs.
    four_output_release_contract.install()
    # A crashed publisher leaves only an unpublished ``vN.staging`` tree.
    # Sweep those exact trees before database/bootstrap writes so stale debris
    # can return capacity to an otherwise full volume. Recovery is best-effort
    # and never converts an operational warning into a failed boot.
    try:
        recovered = assessment_release_service.recover_incomplete_publications()
        if recovered:
            logging.getLogger(__name__).warning(
                "recovered %d incomplete Master publication(s)",
                len(recovered),
            )
    except Exception:
        logging.getLogger(__name__).warning(
            "incomplete Master publication recovery failed", exc_info=True,
        )
    init_db()
    db = SessionLocal()
    try:
        # If atomic rename completed but the following SQLite commit returned
        # an uncertain failure, preserve the complete target and finish only
        # the matching unpublished row after byte/hash verification (without
        # reviving a row that a later retry already superseded).
        try:
            reconciled = (
                assessment_release_service.reconcile_complete_publications(db)
            )
            if reconciled:
                logging.getLogger(__name__).warning(
                    "reconciled %d complete Master publication(s)",
                    len(reconciled),
                )
        except Exception:
            db.rollback()
            logging.getLogger(__name__).warning(
                "complete Master publication reconciliation failed",
                exc_info=True,
            )
        syllabus_svc.bootstrap_syllabus(db)
    finally:
        db.close()
    # Backfill the durable asset store from crops minted before it existed,
    # so URLs already embedded in published content survive job-dir loss.
    # Best-effort: a failed sweep is recorded, never a failed boot.
    try:
        from .services import canonical_source_phase221_fallback as page_acsd

        pinned = page_acsd.pin_existing_job_assets()
        if pinned:
            logging.getLogger(__name__).info(
                "durable asset store backfilled %d crop(s)", pinned
            )
    except Exception:
        logging.getLogger(__name__).warning(
            "durable asset store backfill sweep failed", exc_info=True
        )


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
    # Liveness stays HTTP 200 even when storage is critical; returning 503
    # would make the platform restart-loop the same full volume. The payload
    # gives operators the capacity state while Master preflight refuses safely.
    return {
        "status": "ok",
        "storage": storage_capacity.health_status(),
    }


# Patch only the user-facing Build Concepts upload routes before FastAPI copies
# them into the application. Internal generation services keep their original
# contracts for recovery tooling and programmatic callers.
build_concepts_release_api_contract.install(build_concepts_api.router)

app.include_router(auth_api.router)
# Native app sign-in routes self-gate (404 until AEGIS_GOOGLE_CLIENT_SECRET
# is set) and must be reachable before a session exists.
app.include_router(native_auth_api.router)
app.include_router(native_auth_api.wellknown_router)
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

# Python's mimetypes table predates the PWA manifest extension; without
# this the SPA route serves it as application/octet-stream.
mimetypes.add_type("application/manifest+json", ".webmanifest")


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
