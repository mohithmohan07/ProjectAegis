import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine, init_db
from app.main import app, bootstrap
from app.services import pipeline as pipeline_svc


@pytest.fixture(scope="session", autouse=True)
def _prepare_db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    pipeline_svc.cleanup_artifacts()
    bootstrap()
    yield
    Base.metadata.drop_all(bind=engine)
    pipeline_svc.cleanup_artifacts()


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
