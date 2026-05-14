from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import DATA_DIR
from .db import SessionLocal, init_db
from .api import (
    concepts as concepts_api,
    questions as questions_api,
    pipeline as pipeline_api,
    tags as tags_api,
    export as export_api,
    stats as stats_api,
)


def bootstrap() -> None:
    init_db()
    db = SessionLocal()
    try:
        from . import models
        from .services import concepts as concept_svc
        from .services import questions as question_svc

        if db.query(models.Concept).count() == 0:
            for fname, is_pl in [("concepts.xlsx", False), ("pre_learning.xlsx", True)]:
                p = DATA_DIR / fname
                if p.exists():
                    concept_svc.import_excel(db, p, is_pre_learning=is_pl)

        if db.query(models.Question).count() == 0:
            p = DATA_DIR / "bulk_upload.xlsx"
            if p.exists():
                question_svc.import_excel(db, p)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    yield


app = FastAPI(
    title="Aegis",
    description="Content intelligence and assessment-building engine for Clarius.",
    version="0.1.0",
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


app.include_router(concepts_api.router)
app.include_router(questions_api.router)
app.include_router(pipeline_api.router)
app.include_router(tags_api.router)
app.include_router(export_api.router)
app.include_router(stats_api.router)
