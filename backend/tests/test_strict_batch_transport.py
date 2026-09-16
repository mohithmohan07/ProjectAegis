"""The selected pricing lane cannot silently change at the provider seam."""
from types import SimpleNamespace
import pytest
from app.services import batch_broker, generation, model_routing_run, openai_usage, run_control


def test_pending_batch_suspends_with_durable_receipt_identity(monkeypatch):
    def pending(*_args, **_kwargs):
        raise batch_broker.BatchPending('still running', batch_id='batch-1', request_sha256='sha-1', wave_id='wave-1')
    monkeypatch.setattr(batch_broker, 'bound', lambda: SimpleNamespace(call_result=pending))
    with openai_usage.track():
        with pytest.raises(run_control.RunDeferred):
            with openai_usage.request_attempt(requested_model='gpt-5.6-luna', provider='openai'):
                generation.batched_completion({'model': 'gpt-5.6-luna'}, provider='openai')
        summary = openai_usage.current_summary()
    attempt = summary['request_attempts'][0]
    assert attempt['delivery_mode'] == 'batch' and attempt['outcome'] == 'batch_pending'
    assert attempt['batch_id'] == 'batch-1'
    assert summary['pending_request_count'] == 1
    assert summary['unresolved_usage_request_count'] == 0
    assert summary['request_count'] == 0


def test_rejected_batch_never_returns_synchronous_fallback(monkeypatch):
    def rejected(*_args, **_kwargs):
        raise batch_broker.BatchUnavailable('provider rejected request')
    monkeypatch.setattr(batch_broker, 'bound', lambda: SimpleNamespace(call_result=rejected))
    with pytest.raises(batch_broker.BatchUnavailable):
        generation.batched_completion({}, provider='openai')
    with pytest.raises(batch_broker.BatchUnavailable):
        generation.batched_completion({}, provider='gemini')


def test_a_different_waiter_cannot_steal_original_paid_receipt_attribution(monkeypatch):
    body = {'id': 'response-1', 'model': 'gpt-5.6-luna', 'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}
    result = batch_broker.BatchResult(body, 'batch-1', 'sha-1', False, 41)
    monkeypatch.setattr(batch_broker, 'bound', lambda: SimpleNamespace(call_result=lambda *_a, **_kw: result))
    monkeypatch.setattr(model_routing_run, 'current_job_id', lambda: 42)
    monkeypatch.setattr(generation, '_chat_completion_from_body', lambda value: value)
    with openai_usage.track():
        with openai_usage.request_attempt(requested_model='gpt-5.6-luna', provider='openai'):
            response = generation.batched_completion({}, provider='openai')
            openai_usage.record_response(response)
        summary = openai_usage.current_summary()
    assert summary['request_count'] == 0 and summary['estimated_cost_usd'] == 0
    assert summary['request_attempts'][0]['reused'] is True
    assert summary['request_attempts'][0]['receipt_id'] == 'batch-1:sha-1'
