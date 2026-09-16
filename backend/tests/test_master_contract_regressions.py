"""Owner audit: immutable column policies, geometry and batch reuse receipts."""
import copy
from datetime import datetime, timezone

import pytest

from app.bulk_import import assessment_workbook as aw, writer
from app.services import assessment_profile as profiles, column_spec, katex_rules as kr
from app.services import build_concepts_release as release, assessment_release_snapshot as snapshot
from app.services import openai_usage
from app.services import checkpoints
from tests.test_assessment_release_run import _chapter_with_concepts
from tests.test_independent_reviewed_files import setup_job
from tests.test_batch_pricing import _Response


@pytest.mark.parametrize("equation", [
    "AC", r"\angle BAC", r"\triangle ABC \sim \triangle DEF", r"\frac{AB}{DE}",
    r"\sin x + \cos y", r"\text{Area} = ab", r"AB^2 = AC^2 + BC^2",
])
def test_geometry_labels_are_neither_prose_nor_escaped_commands(equation):
    assert kr.answer_cell_issues("Equation", equation) == []
    assert kr.raw_answer_cell("Equation", equation) == equation
    assert kr.rich_answer_display("Equation", equation) == f"[Katex] {equation} [/Katex]"


def test_concept_download_and_master_profile_keep_same_recorded_delimiter(db):
    chapter = _chapter_with_concepts(db)
    concepts = [concept for topic in chapter.topics for concept in topic.concepts]
    policy = column_spec.for_metadata({"subject": chapter.subject})
    policy.update(version="owner-column-spec-2026-09-08-v2", keywords_separator=", ")
    for concept in concepts:
        concept.keywords = "length | angle | triangle"
    db.flush()
    data = writer.write_concepts_workbook(
        db, [concept.id for concept in concepts], layout_id=writer.CONCEPT_FILE_LAYOUT_ID,
        column_policy=policy)
    rows = aw.parse_workbook(data)["sheets"]["Objective"]["rows"]
    assert {row["keywords"] for row in rows} == {"length, angle, triangle"}
    profile = profiles.resolve_for_metadata(None, {
        "subject": chapter.subject, column_spec.POLICY_KEY: policy})
    assert profile[column_spec.POLICY_KEY] == policy
    assert column_spec.keyword_cell(concepts[0].keywords, column_spec.from_profile(profile)) == rows[0]["keywords"]


def test_new_staged_lanes_share_policy_and_snapshot_carries_it(db):
    job = setup_job(db)
    post = release.release_payload(job, lane="post")
    pre = release.release_payload(job, lane="pre")
    assert post[column_spec.POLICY_KEY] == pre[column_spec.POLICY_KEY]
    assert post[column_spec.POLICY_KEY]["keywords_separator"] == " | "
    bridge = snapshot.build(db, job, post)
    assert bridge["metadata"][column_spec.POLICY_KEY] == post[column_spec.POLICY_KEY]


def test_a_new_stage_preserves_the_stamp_of_its_existing_envelope():
    old = {"version": "owner-column-spec-2026-09-08-v2", "keywords_separator": ", "}
    envelope = {"metadata": {column_spec.POLICY_KEY: old}}
    frozen = column_spec.freeze_for_release({"subject": "Mathematics"}, envelope)
    assert frozen == old
    frozen["keywords_separator"] = "changed"
    assert envelope["metadata"][column_spec.POLICY_KEY] == old


@pytest.mark.parametrize("producer_reused", [True, False])
def test_same_batch_receipt_only_contributes_one_provider_charge(producer_reused):
    with openai_usage.track() as tracked:
        for reused in (False, producer_reused):
            with openai_usage.request_attempt(requested_model="gpt-5.6-luna", provider="openai"):
                openai_usage.record_service_started()
                openai_usage.record_batched_attempt(reused=reused, batch_id="batch-one", request_sha256="abc")
                openai_usage.record_response(_Response("gpt-5.6-luna", 100_000, 10_000, "batch"))
        summary = tracked.summary()
    assert summary["request_count"] == summary["provider_request_count"] == 1
    assert summary["input_tokens"] == 100_000
    assert summary["attempt_coverage_complete"]
    first, second = summary["request_attempts"]
    assert second["receipt_id"] == first["receipt_id"] == "batch-one:abc"
    assert second["reused"] and second["usage_status"] == "reused"
    assert second["reported_input_tokens"] == 100_000
    assert second["input_tokens"] == second["estimated_cost_usd"] == second["estimated_cost_inr"] == 0
    assert summary["estimated_cost_usd"] == first["estimated_cost_usd"]
    assert all(attempt["delivery_mode"] == "batch" for attempt in summary["request_attempts"])


def test_reuse_in_a_later_run_is_zero_new_cost_but_keeps_receipt_reference():
    with openai_usage.track() as tracked:
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            openai_usage.record_batched_attempt(reused=True, receipt_id="existing-provider-receipt")
            openai_usage.record_response(_Response("gpt-5.6-luna", 100, 20, "batch"))
        summary = tracked.summary()
    assert summary["request_count"] == summary["total_tokens"] == summary["estimated_cost_usd"] == 0
    assert summary["request_attempts"][0]["receipt_id"] == "existing-provider-receipt"
    assert summary["usage_complete"] and summary["attempt_coverage_complete"]


@pytest.mark.parametrize("compact", [True, False])
def test_persisted_receipt_identity_deduplicates_even_if_replay_flag_is_missing(compact):
    with openai_usage.track() as tracked:
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna", provider="openai"):
            openai_usage.record_batched_attempt(receipt_id="batch-existing:request-one")
            openai_usage.record_response(_Response("gpt-5.6-luna", 100, 20, "batch"))
        saved = tracked.summary(include_attempts=not compact)
    checkpoints._validate_usage(saved, "usage")
    with openai_usage.track():
        openai_usage.bind_persisted_summary("job:9", saved)
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna", provider="openai"):
            openai_usage.record_batched_attempt(receipt_id="batch-existing:request-one")
            openai_usage.record_response(_Response("gpt-5.6-luna", 100, 20, "batch"))
        resumed = openai_usage.visible_summary()
    checkpoints._validate_usage(resumed, "usage")
    assert resumed["request_count"] == 1
    assert resumed["input_tokens"] == saved["input_tokens"]
    assert resumed["estimated_cost_usd"] == saved["estimated_cost_usd"]
    assert resumed["billed_receipt_ids"] == ["batch-existing:request-one"]
    assert resumed["request_attempts"][-1]["reused"]


def test_broker_price_is_frozen_once_and_dashboard_read_does_no_rate_lookup(monkeypatch):
    from app.services import usage_currency
    receipt = {"receipt_id": "batch:paid", "owner_job_id": 42, "model": "gpt-5.6-luna",
               "usage": {"prompt_tokens": 100_000, "completion_tokens": 10_000,
                         "total_tokens": 110_000}, "service_tier": "batch",
               "recorded_at": datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp()}
    frozen = openai_usage.estimate_batch_receipt(receipt, for_storage=True)
    assert frozen["estimated_cost_usd"] == 0.016
    assert frozen["owner_job_id"] == 42
    receipt["cost_estimate"] = frozen
    monkeypatch.setattr(openai_usage, "_pricing_for", lambda *a, **k: pytest.fail("GET must not reprice"))
    monkeypatch.setattr(usage_currency, "snapshot", lambda: pytest.fail("GET must not look up FX"))
    assert openai_usage.estimate_batch_receipt(receipt) == frozen
    unknown = openai_usage.estimate_batch_receipt({key: value for key, value in receipt.items() if key != "cost_estimate"})
    assert unknown["estimated_cost_usd"] is None
    assert unknown["accounting_basis"] == "missing_frozen_batch_price"


def test_historical_receipt_without_a_matching_rate_snapshot_stays_unpriced():
    receipt = {"model": "gpt-5.6-luna", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
               "recorded_at": datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()}
    estimate = openai_usage.estimate_batch_receipt(receipt, for_storage=True)
    assert estimate["usage_complete"]
    assert estimate["estimated_cost_usd"] is None
    assert not estimate["pricing_complete"]


@pytest.mark.parametrize("compact", [True, False])
def test_durable_batch_wait_survives_resume_and_exact_receipt_clears_pending(compact):
    with openai_usage.track() as tracked:
        with pytest.raises(KeyboardInterrupt):
            with openai_usage.request_attempt(requested_model="gpt-5.6-luna", provider="openai"):
                openai_usage.record_service_started()
                openai_usage.record_batch_pending(batch_id="batch-wait", request_sha256="same", wave_id="wave")
                raise KeyboardInterrupt("suspend")
        saved = tracked.summary(include_attempts=not compact)
    assert saved["pending_request_count"] == 1
    assert saved["unresolved_usage_request_count"] == 0
    checkpoints._validate_usage(saved, "usage")
    with openai_usage.track():
        bound = openai_usage.bind_persisted_summary("job:9", saved)
        assert bound["pending_request_count"] == 1
        assert bound["unresolved_usage_request_count"] == 0
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna", provider="openai"):
            openai_usage.record_batched_attempt(reused=True, batch_id="batch-wait", request_sha256="same")
            openai_usage.record_response(_Response("gpt-5.6-luna", 100, 20, "batch"))
        resumed = openai_usage.visible_summary(include_attempts=not compact)
    checkpoints._validate_usage(resumed, "usage")
    assert resumed["pending_request_count"] == resumed["unresolved_usage_request_count"] == 0
    assert resumed["pending_batch_attempts"] == []
    assert resumed["completed_batch_receipt_ids"] == ["batch-wait:same"]
    # The provider ledger owns the original charge; reuse invents no new cost.
    assert resumed["request_count"] == resumed["estimated_cost_usd"] == 0


def test_same_body_from_an_older_batch_cannot_settle_a_durable_pending_retry():
    with openai_usage.track() as tracked:
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna", provider="openai"):
            openai_usage.record_service_started()
            openai_usage.record_batch_pending(batch_id="new-batch", request_sha256="same", wave_id="new-wave")
        saved = tracked.summary(include_attempts=False)
    untouched = copy.deepcopy(saved)
    assert openai_usage.settle_batch_pending(saved, {"old-batch:same"}) == untouched
    settled = openai_usage.settle_batch_pending(saved, {"new-batch:same"})
    assert saved == untouched
    assert settled["pending_request_count"] == 0
    assert settled["estimated_cost_usd"] == saved["estimated_cost_usd"]
