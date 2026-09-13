"""Two blocking defects the shipped batch console carried (Q64).

Both were found by auditing the code against `docs/chapter-batch-console-contract.md`
and both were reproduced against the real app before being fixed.

1. `_run_step01` omitted `pause_for_concept_review=True`. Contract section 5
   spells the call out with it and the interactive route passes it; without it
   the flag defaults to False, the pause branch is skipped, and step01 falls
   through to `_build_master_siblings`. `reviewed_file_input.prepare` then takes
   its unchanged-file branch, RENDERS the job's own staged Concept workbook,
   records it as an accepted reviewed input and spends the whole of Step 02 on
   content no person ever saw. `initialize_concept_review` — whose only caller
   is that skipped branch — never runs, so the row ends markerless and the
   console derives `blocked/no_review_marker` with only "upload source" left.

2. `GET /chapter-batches/{chapter_id}` declared no `response_model`, so FastAPI
   encoded the ORM object's loaded COLUMNS. `source_artifacts` is a property
   installed at import time, not a column, so the drawer's Concept and Master
   download links resolved to nothing in every state, forever — while the same
   `files.find(kind)` code worked on Build Concepts, which declares
   `UploadJobOut`.
"""
from __future__ import annotations

import contextlib

from app import models, schemas
from app.api import chapter_batches as chapter_batches_api
from app.services import build_concepts_release_contract as release_contract
from app.services import chapter_queue_worker, openai_usage, progress, uploads


def test_queued_step01_asks_for_the_concept_review_pause(db, monkeypatch):
    """The kwarg the frozen contract names, reached through the real body."""
    recorded: dict = {}

    @contextlib.contextmanager
    def _journal(*args, **kwargs):
        class _Capture:
            def set_result(self, value):
                recorded["result"] = value
        yield _Capture()

    monkeypatch.setattr(progress, "capture_to_journal", _journal)
    monkeypatch.setattr(openai_usage, "track", contextlib.nullcontext)
    monkeypatch.setattr(
        uploads, "run_with_openai_usage",
        lambda db, job_id, call, **kwargs: call(),
    )
    monkeypatch.setattr(
        release_contract, "generate_post_learning",
        lambda db, job_id, chapter_id, **kwargs: recorded.setdefault(
            "kwargs", dict(kwargs)),
    )
    monkeypatch.setattr(
        chapter_queue_worker, "_after_generation",
        lambda db, job_id, result: {"outcome": "done"},
    )
    monkeypatch.setattr(chapter_queue_worker, "_job_owner", lambda db, job_id: "owner-1")

    row = models.ChapterBatchRow(chapter_id=7, job_id=11)
    chapter_queue_worker._run_step01(db, row, task=None)

    assert recorded["kwargs"].get("pause_for_concept_review") is True


def test_the_row_detail_route_carries_the_download_links():
    """``source_artifacts`` is a property; only a response_model exposes it."""
    assert "source_artifacts" in schemas.UploadJobOut.model_fields

    route = next(
        candidate for candidate in chapter_batches_api.router.routes
        if getattr(candidate, "path", "").endswith("/{chapter_id}")
        and "GET" in getattr(candidate, "methods", set())
    )

    assert route.response_model is chapter_batches_api.ChapterBatchDetailOut
    assert (
        chapter_batches_api.ChapterBatchDetailOut.model_fields["job"]
        .annotation.__args__[0] is schemas.UploadJobOut
    )
