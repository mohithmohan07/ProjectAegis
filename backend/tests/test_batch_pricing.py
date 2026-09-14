"""A batched receipt is priced at the batch rate; a fallback is not.

The owner's instruction of 14 September 2026 is that a cohort run must be
priced through the Batch API rather than per chapter. A run that mixes a
batched wave with a synchronous fallback has to report each at its own
rate, or the number the console shows is not the bill.
"""
from __future__ import annotations

from decimal import Decimal

from app.services import openai_usage


class _Usage:
    def __init__(self, prompt: int, completion: int):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion
        self.prompt_tokens_details = None
        self.completion_tokens_details = None


class _Response:
    def __init__(self, model: str, prompt: int, completion: int, tier: str = ""):
        self.model = model
        self.usage = _Usage(prompt, completion)
        self.service_tier = tier


def _cost_of(*, batched: bool, tier: str = "") -> float | None:
    with openai_usage.track() as tracked:
        with openai_usage.request_attempt(requested_model="gpt-5.6-luna"):
            if batched:
                openai_usage.record_batched_attempt()
            openai_usage.record_response(
                _Response("gpt-5.6-luna", 100_000, 10_000, tier),
                requested_model="gpt-5.6-luna",
            )
        summary = tracked.summary()
    return summary.get("estimated_cost_usd")


def test_a_batched_receipt_is_priced_at_half_the_synchronous_receipt():
    synchronous = _cost_of(batched=False)
    batched = _cost_of(batched=True)
    assert synchronous and batched
    assert abs(batched - synchronous / 2) < 1e-12, (synchronous, batched)


def test_the_batch_tier_is_priced_rather_than_reported_as_unknown():
    """A batch line comes back on the provider's batch tier. Without this the
    whole cohort would read as unpriced usage — a floor, not a bill."""
    assert _cost_of(batched=True, tier="batch") is not None


def test_an_unmarked_receipt_on_an_unknown_tier_is_still_not_priced():
    """The existing protection stands: usage Aegis cannot price is reported
    as unpriced, never as zero."""
    assert _cost_of(batched=False, tier="scale") is None


def test_the_multiplier_is_the_only_thing_the_flag_changes():
    priced = openai_usage._request_cost(
        model="gpt-5.6-luna", input_tokens=50_000, cached_input_tokens=0,
        cache_write_tokens=0, output_tokens=5_000,
    )
    batched = openai_usage._request_cost(
        model="gpt-5.6-luna", input_tokens=50_000, cached_input_tokens=0,
        cache_write_tokens=0, output_tokens=5_000, batched=True,
    )
    assert priced is not None and batched is not None
    assert batched == priced * openai_usage.BATCH_RATE_MULTIPLIER
    assert openai_usage.BATCH_RATE_MULTIPLIER == Decimal("0.5")
