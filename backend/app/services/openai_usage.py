"""Per-generation OpenAI token accounting and cost estimates.

The active accumulator is context-local so simultaneous streamed jobs cannot
mix their usage. Only usage returned by OpenAI is recorded; provider errors
without a response are deliberately not guessed.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterator


PRICING_AS_OF = "2026-07-22"
DEFAULT_PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"


@dataclass(frozen=True)
class Pricing:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    source: str = DEFAULT_PRICING_SOURCE


# Standard text-token prices, snapshotted on PRICING_AS_OF. Prefix matching
# covers both aliases and dated snapshots (for example gpt-5.4-mini-2026-03-17).
_PRICING: tuple[tuple[str, Pricing], ...] = (
    (
        "gpt-5.4-mini",
        Pricing(
            Decimal("0.75"),
            Decimal("0.075"),
            Decimal("4.50"),
            "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
        ),
    ),
    (
        "gpt-5.6-luna",
        Pricing(Decimal("1.00"), Decimal("0.10"), Decimal("6.00")),
    ),
)


@dataclass
class ModelUsage:
    model: str
    request_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    def add(
        self,
        *,
        request_count: int,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        total_tokens: int,
    ) -> None:
        self.request_count += max(0, int(request_count))
        self.input_tokens += max(0, int(input_tokens))
        self.cached_input_tokens += max(0, int(cached_input_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.reasoning_tokens += max(0, int(reasoning_tokens))
        self.total_tokens += max(0, int(total_tokens))


@dataclass
class UsageAccumulator:
    models: dict[str, ModelUsage] = field(default_factory=dict)

    def add(
        self,
        *,
        model: str,
        request_count: int = 1,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        total_tokens: int | None = None,
    ) -> None:
        model = (model or "unknown").strip() or "unknown"
        input_tokens = max(0, int(input_tokens))
        cached_input_tokens = min(input_tokens, max(0, int(cached_input_tokens)))
        output_tokens = max(0, int(output_tokens))
        reasoning_tokens = min(output_tokens, max(0, int(reasoning_tokens)))
        total = input_tokens + output_tokens if total_tokens is None else max(
            0, int(total_tokens)
        )
        item = self.models.setdefault(model, ModelUsage(model=model))
        item.add(
            request_count=request_count,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total,
        )

    def summary(self) -> dict[str, Any]:
        model_rows = [_model_summary(item) for item in self.models.values()]
        model_rows.sort(key=lambda row: row["model"])
        request_count = sum(row["request_count"] for row in model_rows)
        input_tokens = sum(row["input_tokens"] for row in model_rows)
        cached_input_tokens = sum(row["cached_input_tokens"] for row in model_rows)
        output_tokens = sum(row["output_tokens"] for row in model_rows)
        reasoning_tokens = sum(row["reasoning_tokens"] for row in model_rows)
        total_tokens = sum(row["total_tokens"] for row in model_rows)
        pricing_complete = all(row["pricing_complete"] for row in model_rows)
        known_costs = [row["estimated_cost_usd"] for row in model_rows]
        cost = (
            round(sum(known_costs), 12)
            if pricing_complete and all(value is not None for value in known_costs)
            else None
        )
        if not model_rows:
            pricing_complete = True
            cost = 0.0
        return {
            "model": (
                model_rows[0]["model"]
                if len(model_rows) == 1
                else "multiple" if model_rows else ""
            ),
            "models": model_rows,
            "request_count": request_count,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "uncached_input_tokens": max(input_tokens - cached_input_tokens, 0),
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": cost,
            "currency": "USD",
            "pricing_complete": pricing_complete,
            "pricing_as_of": PRICING_AS_OF,
            "pricing_source": _pricing_source(model_rows),
        }


_active: contextvars.ContextVar[UsageAccumulator | None] = contextvars.ContextVar(
    "aegis_openai_usage", default=None
)


@contextmanager
def track() -> Iterator[UsageAccumulator]:
    """Start an isolated usage accumulator for one logical generation run."""
    accumulator = UsageAccumulator()
    token = _active.set(accumulator)
    try:
        yield accumulator
    finally:
        _active.reset(token)


def start_tracking() -> contextvars.Token[UsageAccumulator | None]:
    """Start tracking when a surrounding callback cannot use ``with``."""
    return _active.set(UsageAccumulator())


def stop_tracking(token: contextvars.Token[UsageAccumulator | None]) -> None:
    _active.reset(token)


def is_tracking() -> bool:
    return _active.get() is not None


def current_summary() -> dict[str, Any]:
    accumulator = _active.get()
    return (accumulator or UsageAccumulator()).summary()


def record_response(response: Any, *, requested_model: str = "") -> dict[str, Any]:
    """Record one billable Chat Completions response, if tracking is active.

    Existing tests and third-party compatible endpoints may omit ``usage``;
    those responses remain valid and are not assigned invented token counts.
    """
    accumulator = _active.get()
    usage = _get(response, "usage")
    if accumulator is None or usage is None:
        return current_summary()

    input_tokens = _int(_get(usage, "prompt_tokens", _get(usage, "input_tokens")))
    output_tokens = _int(
        _get(usage, "completion_tokens", _get(usage, "output_tokens"))
    )
    prompt_details = _get(
        usage, "prompt_tokens_details", _get(usage, "input_tokens_details")
    )
    completion_details = _get(
        usage, "completion_tokens_details", _get(usage, "output_tokens_details")
    )
    cached_tokens = _int(_get(prompt_details, "cached_tokens"))
    reasoning_tokens = _int(_get(completion_details, "reasoning_tokens"))
    raw_total = _get(usage, "total_tokens")
    total_tokens = None if raw_total is None else _int(raw_total)
    model = str(_get(response, "model") or requested_model or "unknown")
    accumulator.add(
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
    )
    summary = accumulator.summary()

    # Local import avoids a module cycle. Outside streamed requests this is a
    # cheap no-op, while the web UI receives updated aggregate usage live.
    try:
        from . import progress

        progress.usage(summary)
    except Exception:  # pragma: no cover - accounting must never break generation
        pass
    return summary


def merge_summaries(*summaries: dict[str, Any] | None) -> dict[str, Any]:
    """Merge persisted/run summaries without repricing historical usage."""
    accumulator = UsageAccumulator()
    saved_cost = Decimal("0")
    pricing_complete = True
    saw_usage = False
    pricing_dates: set[str] = set()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        if _int(summary.get("request_count")) > 0:
            saw_usage = True
            value = summary.get("estimated_cost_usd")
            if value is None or summary.get("pricing_complete") is False:
                pricing_complete = False
            else:
                try:
                    saved_cost += Decimal(str(value))
                except (ValueError, TypeError):
                    pricing_complete = False
            if summary.get("pricing_as_of"):
                pricing_dates.add(str(summary["pricing_as_of"]))
        rows = summary.get("models")
        if not isinstance(rows, list) or not rows:
            rows = [summary] if summary.get("request_count") else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            accumulator.add(
                model=str(row.get("model") or summary.get("model") or "unknown"),
                request_count=_int(row.get("request_count")),
                input_tokens=_int(row.get("input_tokens")),
                cached_input_tokens=_int(row.get("cached_input_tokens")),
                output_tokens=_int(row.get("output_tokens")),
                reasoning_tokens=_int(row.get("reasoning_tokens")),
                total_tokens=_int(row.get("total_tokens")),
            )
    merged = accumulator.summary()
    if saw_usage:
        merged["pricing_complete"] = pricing_complete
        merged["estimated_cost_usd"] = (
            float(saved_cost.quantize(Decimal("0.000000000001")))
            if pricing_complete
            else None
        )
        if len(pricing_dates) == 1:
            merged["pricing_as_of"] = next(iter(pricing_dates))
        elif len(pricing_dates) > 1:
            merged["pricing_as_of"] = "multiple"
    return merged


def _pricing_for(model: str) -> Pricing | None:
    lowered = model.lower()
    for prefix, pricing in _PRICING:
        if lowered.startswith(prefix):
            return pricing
    return None


def _model_summary(item: ModelUsage) -> dict[str, Any]:
    pricing = _pricing_for(item.model)
    cost: float | None = None
    if pricing is not None:
        uncached = max(item.input_tokens - item.cached_input_tokens, 0)
        value = (
            Decimal(uncached) * pricing.input_per_million
            + Decimal(item.cached_input_tokens) * pricing.cached_input_per_million
            + Decimal(item.output_tokens) * pricing.output_per_million
        ) / Decimal(1_000_000)
        cost = float(value.quantize(Decimal("0.000000000001")))
    return {
        "model": item.model,
        "request_count": item.request_count,
        "input_tokens": item.input_tokens,
        "cached_input_tokens": item.cached_input_tokens,
        "uncached_input_tokens": max(
            item.input_tokens - item.cached_input_tokens, 0
        ),
        "output_tokens": item.output_tokens,
        "reasoning_tokens": item.reasoning_tokens,
        "total_tokens": item.total_tokens,
        "estimated_cost_usd": cost,
        "pricing_complete": pricing is not None,
        "pricing_source": pricing.source if pricing else DEFAULT_PRICING_SOURCE,
    }


def _pricing_source(rows: list[dict[str, Any]]) -> str:
    sources = {str(row.get("pricing_source") or "") for row in rows}
    sources.discard("")
    return sources.pop() if len(sources) == 1 else DEFAULT_PRICING_SOURCE


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default) if obj is not None else default


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0
