from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .bulk_import import reader
from .db import SessionLocal, init_db
from .api import (
    directory as directory_api,
    build_assessments as build_assessments_api,
    build_concepts as build_concepts_api,
    data as data_api,
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
