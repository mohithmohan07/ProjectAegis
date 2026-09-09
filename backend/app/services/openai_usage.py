"""Per-generation OpenAI token accounting and cost estimates.

The active accumulator is context-local so simultaneous streamed jobs cannot
mix their usage. Only usage returned by OpenAI is recorded; provider errors
without a response are deliberately not guessed.
"""
from __future__ import annotations

import copy
import contextvars
import functools
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

from .usage_schema import SCHEMA_VERSION


PRICING_AS_OF = "2026-08-28"
DEFAULT_PRICING_SOURCE = "https://developers.openai.com/api/docs/pricing"


@dataclass(frozen=True)
class Pricing:
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    source: str = DEFAULT_PRICING_SOURCE
    cache_write_multiplier: Decimal = Decimal("1")
    long_context_threshold: int | None = None
    long_input_multiplier: Decimal = Decimal("1")
    long_output_multiplier: Decimal = Decimal("1")


# Standard text-token prices, snapshotted on PRICING_AS_OF. Prefix matching
# covers both aliases and dated snapshots (for example gpt-5.4-mini-2026-03-17).
# A model Aegis does not select has no row here. Unpriced usage is reported as
# ``pricing_complete: False`` with a null cost rather than being priced at zero,
# so an unrecognized model is visible in the run summary instead of silently
# understating spend.
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
        Pricing(
            Decimal("0.20"),
            Decimal("0.02"),
            Decimal("1.20"),
            "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
            cache_write_multiplier=Decimal("1.25"),
            long_context_threshold=272_000,
            long_input_multiplier=Decimal("2"),
            long_output_multiplier=Decimal("1.5"),
        ),
    ),
)


def _decimal_env(name: str, default: str) -> Decimal:
    try:
        return Decimal(os.environ.get(name, "").strip() or default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _gemini_pricing() -> Pricing:
    """Gemini rates, configurable to match the actual billing plan.

    Gemini models ride the provider-selection seam, and their prices change
    per model and tier, so the defaults here are flash-class ballpark rates.
    Set the AEGIS_GEMINI_*_PRICE_PER_M variables to your billed rates so the
    run estimate matches your invoice; either way the token counts are exact.
    """
    return Pricing(
        _decimal_env("AEGIS_GEMINI_INPUT_PRICE_PER_M", "0.30"),
        _decimal_env("AEGIS_GEMINI_CACHED_INPUT_PRICE_PER_M", "0.03"),
        _decimal_env("AEGIS_GEMINI_OUTPUT_PRICE_PER_M", "2.50"),
        "https://ai.google.dev/pricing — flash-class defaults; override "
        "with AEGIS_GEMINI_INPUT_PRICE_PER_M / "
        "AEGIS_GEMINI_CACHED_INPUT_PRICE_PER_M / "
        "AEGIS_GEMINI_OUTPUT_PRICE_PER_M",
    )


_PRICING = (*_PRICING, ("gemini", _gemini_pricing()))

_COST_UNSET = object()


@dataclass
class ModelUsage:
    model: str
    request_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    pricing_complete: bool = True

    def add(
        self,
        *,
        request_count: int,
        input_tokens: int,
        cached_input_tokens: int,
        cache_write_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        total_tokens: int,
        estimated_cost_usd: Decimal | None,
    ) -> None:
        self.request_count += max(0, int(request_count))
        self.input_tokens += max(0, int(input_tokens))
        self.cached_input_tokens += max(0, int(cached_input_tokens))
        self.cache_write_tokens += max(0, int(cache_write_tokens))
        self.output_tokens += max(0, int(output_tokens))
        self.reasoning_tokens += max(0, int(reasoning_tokens))
        self.total_tokens += max(0, int(total_tokens))
        if estimated_cost_usd is None:
            self.pricing_complete = False
        else:
            self.estimated_cost_usd += estimated_cost_usd


@dataclass
class StageUsage:
    """Per-(stage, lane) usage for the run's time/cost table.

    ``stage`` is the last ``progress.step`` label seen by the context that
    made the call (worker pools inherit the stage that spawned them —
    the stage that paid for them); ``lane`` is the composed worker-label
    scope, which is how the parallel tracks (the early inventory track,
    the Phase-3 lanes, the two Masters) show up as separate rails.
    Stage rows are part of every summary and are merged cumulatively
    across run segments (parse + each generation attempt) by
    ``merge_summaries``, so the live console, the terminal event, and the
    persisted job record all describe the SAME ledger (owner request,
    2026-08-28: before-parsing and after-parsing numbers must agree).
    """

    stage: str
    lane: str
    request_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: Decimal = Decimal("0")
    pricing_complete: bool = True
    first_ts: float = 0.0
    last_ts: float = 0.0


@dataclass
class UsageAccumulator:
    models: dict[str, ModelUsage] = field(default_factory=dict)
    stages: dict[tuple[str, str], StageUsage] = field(default_factory=dict)
    stage_models: dict[tuple[str, str, str], ModelUsage] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    mechanical_spans: list[dict[str, Any]] = field(default_factory=list)
    missing_usage_responses: int = 0
    untracked_response_count: int = 0
    # One immutable durable baseline per persisted artifact. Recomputing
    # ``baseline + current run`` at every checkpoint makes repeated saves
    # idempotent while still including newly billed responses.
    persistence_baselines: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        repr=False,
    )
    visible_persistence_key: str = field(default="", repr=False)
    # Wall-clock for this run segment: the accumulator opens when the
    # streamed run's worker starts, so ``elapsed_seconds`` is the segment's
    # duration so far; cumulative time across segments comes from
    # ``merge_summaries`` summing the persisted values.
    started_monotonic: float = field(default_factory=time.monotonic, repr=False)
    # Per-STAGE wall-clock windows fed by ``mark_stage`` at each
    # ``progress.step`` boundary: accumulated closed seconds per stage
    # label, plus the currently open stage. Lanes within one stage share
    # the stage's window — they run inside its wall time.
    stage_windows: dict[str, float] = field(default_factory=dict, repr=False)
    open_stage: str = field(default="", repr=False)
    open_stage_started: float = field(default=0.0, repr=False)

    def _close_open_stage_locked(self) -> None:
        if self.open_stage_started:
            self.stage_windows[self.open_stage] = (
                self.stage_windows.get(self.open_stage, 0.0)
                + (time.monotonic() - self.open_stage_started)
            )
            self.open_stage_started = 0.0

    def mark_stage(self, stage: str) -> None:
        """Record a stage boundary: close the open window, open the next."""
        with _MUTATION_LOCK:
            self._close_open_stage_locked()
            self.open_stage = str(stage)
            self.open_stage_started = time.monotonic()

    def _stage_elapsed(self, row: StageUsage) -> float:
        elapsed = self.stage_windows.get(row.stage)
        extra = (
            time.monotonic() - self.open_stage_started
            if self.open_stage == row.stage and self.open_stage_started
            else 0.0
        )
        if elapsed is None and not extra:
            # Never marked (a caller without step events): the billable
            # window is the honest fallback.
            return round(max(row.last_ts - row.first_ts, 0.0), 3)
        return round((elapsed or 0.0) + extra, 3)

    def add(
        self,
        *,
        model: str,
        request_count: int = 1,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        total_tokens: int | None = None,
        estimated_cost_usd: Decimal | float | str | None | object = _COST_UNSET,
        stage: str = "",
        lane: str = "",
    ) -> None:
        model = (model or "unknown").strip() or "unknown"
        input_tokens = max(0, int(input_tokens))
        cached_input_tokens = min(input_tokens, max(0, int(cached_input_tokens)))
        cache_write_tokens = min(
            max(input_tokens - cached_input_tokens, 0),
            max(0, int(cache_write_tokens)),
        )
        output_tokens = max(0, int(output_tokens))
        reasoning_tokens = min(output_tokens, max(0, int(reasoning_tokens)))
        total = input_tokens + output_tokens if total_tokens is None else max(
            0, int(total_tokens)
        )
        request_cost: Decimal | None
        if estimated_cost_usd is _COST_UNSET:
            request_cost = _request_cost(
                model=model,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_write_tokens=cache_write_tokens,
                output_tokens=output_tokens,
            )
        elif estimated_cost_usd is None:
            request_cost = None
        else:
            try:
                request_cost = Decimal(str(estimated_cost_usd))
            except (ValueError, TypeError):
                request_cost = None
        with _MUTATION_LOCK:
            item = self.models.setdefault(model, ModelUsage(model=model))
            item.add(
            request_count=request_count,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total,
            estimated_cost_usd=request_cost,
            )
            matrix_row = self.stage_models.setdefault(
                (str(stage), str(lane), model), ModelUsage(model=model)
            )
            matrix_row.add(
                request_count=request_count, input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_write_tokens=cache_write_tokens,
                output_tokens=output_tokens, reasoning_tokens=reasoning_tokens,
                total_tokens=total, estimated_cost_usd=request_cost,
            )
            row = self.stages.setdefault(
                (str(stage), str(lane)),
                StageUsage(stage=str(stage), lane=str(lane)),
            )
            row.request_count += max(0, int(request_count))
            row.input_tokens += input_tokens
            row.cached_input_tokens += cached_input_tokens
            row.cache_write_tokens += cache_write_tokens
            row.output_tokens += output_tokens
            row.reasoning_tokens += reasoning_tokens
            row.total_tokens += total
            if request_cost is None:
                row.pricing_complete = False
            elif row.pricing_complete:
                row.estimated_cost_usd += request_cost
            now = time.time()
            if not row.first_ts:
                row.first_ts = now
            row.last_ts = now

    def stage_rows(self) -> list[dict[str, Any]]:
        """JSON-safe per-(stage, lane) rows, in first-seen order."""
        attempts_by_stage: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for attempt in self.attempts:
            attempts_by_stage.setdefault((attempt["stage"], attempt["lane"]), []).append(attempt)
        return [
            {
                "stage": row.stage,
                "lane": row.lane,
                "request_count": row.request_count,
                "input_tokens": row.input_tokens,
                "cached_input_tokens": row.cached_input_tokens,
                "cache_write_tokens": row.cache_write_tokens,
                "output_tokens": row.output_tokens,
                "reasoning_tokens": row.reasoning_tokens,
                "total_tokens": row.total_tokens,
                "estimated_cost_usd": (
                    round(float(row.estimated_cost_usd), 6)
                    if row.pricing_complete else None
                ),
                "pricing_complete": row.pricing_complete,
                "first_ts": row.first_ts,
                "last_ts": row.last_ts,
                "elapsed_seconds": self._stage_elapsed(row),
                "elapsed_scope": "shared_stage_window_do_not_sum_lanes",
                "active_request_seconds": _interval_seconds([
                    (attempt["queued_at"], attempt.get("ended_at") or time.time())
                    for attempt in attempts_by_stage.get((row.stage, row.lane), [])
                ]),
                **_stage_attempt_fields(row, attempts_by_stage.get((row.stage, row.lane), [])),
            }
            for row in self.stages.values()
        ]

    def summary(self, *, include_attempts: bool = True) -> dict[str, Any]:
        with _MUTATION_LOCK:
            return self._summary_locked(include_attempts=include_attempts)

    def _summary_locked(self, *, include_attempts: bool) -> dict[str, Any]:
        model_rows = [_model_summary(item) for item in self.models.values()]
        model_rows.sort(key=lambda row: row["model"])
        request_count = sum(row["request_count"] for row in model_rows)
        input_tokens = sum(row["input_tokens"] for row in model_rows)
        cached_input_tokens = sum(row["cached_input_tokens"] for row in model_rows)
        cache_write_tokens = sum(row["cache_write_tokens"] for row in model_rows)
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
        attempt_rows = self.attempts
        unknown_attempts = sum(
            bool(row.get("service_started_at")) and not row.get("usage_reported")
            for row in attempt_rows
        )
        usage_complete = not (self.missing_usage_responses or unknown_attempts)
        known_cost = round(sum(row["known_usage_estimated_cost_usd"] for row in model_rows), 12)
        if not usage_complete:
            pricing_complete = False
            cost = None
        stage_names = list(dict.fromkeys([
            *self.stage_windows, *([self.open_stage] if self.open_stage_started else []),
            *(row.stage for row in self.stages.values()),
        ]))
        spans = self.mechanical_spans
        span_lookup = {row["span_id"]: row for row in spans}
        mechanical_cpu = sum(
            row.get("thread_cpu_seconds") or 0.0
            for row in spans
            if span_lookup.get(row.get("parent_span_id"), {}).get("thread_id") != row["thread_id"]
        )
        return {
            "usage_schema_version": SCHEMA_VERSION,
            "model": (
                model_rows[0]["model"]
                if len(model_rows) == 1
                else "multiple" if model_rows else ""
            ),
            "models": model_rows,
            "request_count": request_count,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_write_tokens": cache_write_tokens,
            "uncached_input_tokens": max(input_tokens - cached_input_tokens, 0),
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": cost,
            "currency": "USD",
            "pricing_complete": pricing_complete,
            "usage_complete": usage_complete,
            "known_usage_estimated_cost_usd": known_cost,
            "attempt_count": len(attempt_rows),
            "provider_request_count": sum(bool(row.get("service_started_at")) for row in attempt_rows),
            "missing_usage_response_count": self.missing_usage_responses,
            "untracked_response_count": self.untracked_response_count,
            "attempt_coverage_complete": self.untracked_response_count == 0 and sum(bool(row.get("usage_reported")) for row in attempt_rows) == request_count,
            "cost_matrix_complete": usage_complete,
            "request_attempts": copy.deepcopy(attempt_rows) if include_attempts else [],
            "attempt_details_included": include_attempts,
            "mechanical_spans": copy.deepcopy(spans) if include_attempts else [],
            "mechanical_span_count": len(spans),
            "mechanical_wall_seconds": _interval_seconds([
                (row["started_at"], row.get("ended_at") or time.time()) for row in spans
            ]),
            "mechanical_thread_cpu_seconds": round(mechanical_cpu, 6),
            "mechanical_cpu_complete": all(row.get("ended_at") is not None for row in spans),
            "cost_by_stage_lane_model": [
                {"stage": stage, "lane": lane, **_model_summary(row)}
                for (stage, lane, _model), row in self.stage_models.items()
            ],
            "pricing_as_of": PRICING_AS_OF,
            "pricing_source": _pricing_source(model_rows),
            # Wall-clock of this run segment; cumulative across segments
            # once merged. Stage rows carry the same ledger stage-wise —
            # persisted with the summary so before/after-parsing views and
            # the durable record all agree (owner request, 2026-08-28).
            # Replayed decisions and workbook construction also consume time.
            # Monetary/request counters remain unchanged for a free replay.
            "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "stage_timings": [
                {"stage": stage, "elapsed_seconds": self._stage_elapsed(
                    self.stages.get((stage, ""), StageUsage(stage=stage, lane=""))
                )}
                for stage in stage_names
            ],
            "stages": self.stage_rows(),
        }


# Workers under the bounded decision pool share ONE accumulator object per
# run (contextvars copy the reference, not the value), and ``ModelUsage.add``
# is a read-modify-write. One process-wide lock keeps the cost report exact;
# contention is negligible next to a model call.
_MUTATION_LOCK = threading.RLock()


_active: contextvars.ContextVar[UsageAccumulator | None] = contextvars.ContextVar(
    "aegis_openai_usage", default=None
)
_active_attempt: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "aegis_provider_attempt", default=None
)
_active_mechanical_span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aegis_mechanical_span", default=None
)


@contextmanager
def mechanical_span(operation: str) -> Iterator[None]:
    """Measure one named mechanical operation, without billing an API call.

    Thread CPU excludes child Node/renderer processes. Parent identities let
    summaries avoid adding nested durations/CPU twice; concurrent wall time is
    the interval union, while each separate worker's CPU remains attributable.
    """
    accumulator = _active.get()
    if accumulator is None:
        yield
        return
    from . import progress

    row = {
        "span_id": uuid.uuid4().hex, "parent_span_id": _active_mechanical_span.get(),
        "operation": str(operation), "stage": progress.current_stage(),
        "lane": progress.current_lane(), "thread_id": threading.get_ident(),
        "started_at": time.time(), "ended_at": None, "elapsed_seconds": None,
        "thread_cpu_seconds": None, "cpu_scope": "current_thread_excludes_child_processes",
        "outcome": "running",
    }
    with _MUTATION_LOCK:
        accumulator.mechanical_spans.append(row)
    token = _active_mechanical_span.set(row["span_id"])
    started, cpu_started = time.monotonic(), time.thread_time()
    try:
        yield
        row["outcome"] = "success"
    except BaseException:
        row["outcome"] = "error"
        raise
    finally:
        with _MUTATION_LOCK:
            row["ended_at"] = time.time()
            row["elapsed_seconds"] = round(max(0.0, time.monotonic() - started), 6)
            row["thread_cpu_seconds"] = round(max(0.0, time.thread_time() - cpu_started), 6)
        _active_mechanical_span.reset(token)


def measure_mechanical(operation: str):
    """Transparent wrapper for existing workbook projection/parse functions."""
    def decorate(callback):
        @functools.wraps(callback)
        def measured(*args, **kwargs):
            with mechanical_span(operation):
                return callback(*args, **kwargs)
        return measured
    return decorate


def _interval_seconds(intervals: list[tuple[float, float]]) -> float:
    """Elapsed union, never the sum of overlapping requests."""
    total = 0.0
    end = 0.0
    for start, stop in sorted(intervals):
        total += max(0.0, stop - max(start, end))
        end = max(end, stop)
    return round(total, 3)


def _stage_attempt_fields(row: StageUsage, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    usage_complete = not any(
        item.get("service_started_at") and not item.get("usage_reported") for item in attempts
    )
    return {
        "attempt_count": len(attempts),
        "provider_request_count": sum(bool(item.get("service_started_at")) for item in attempts),
        "usage_complete": usage_complete,
        "attempt_coverage_complete": sum(bool(item.get("usage_reported")) for item in attempts) >= row.request_count,
        "pricing_complete": row.pricing_complete and usage_complete,
        "estimated_cost_usd": round(float(row.estimated_cost_usd), 6) if row.pricing_complete and usage_complete else None,
    }


@contextmanager
def request_attempt(*, requested_model: str, purpose: str = "", provider: str = "",
                    reasoning_effort: str = "", service_tier: str = "") -> Iterator[None]:
    """One scheduled transport attempt, including queue, error and backoff.

    Prompt text, credentials and URLs are deliberately absent. Response IDs
    join the provider ledger; a timeout with no usage is unknown expenditure,
    never an invented zero-token/zero-dollar request.
    """
    accumulator = _active.get()
    if accumulator is None:
        yield
        return
    from . import progress

    row = {
        "attempt_id": uuid.uuid4().hex, "stage": progress.current_stage(),
        "lane": progress.current_lane(), "purpose": str(purpose),
        "provider": str(provider), "requested_model": str(requested_model),
        "requested_reasoning_effort": str(reasoning_effort),
        "requested_service_tier": str(service_tier),
        "actual_model": None, "actual_reasoning_effort": None,
        "actual_service_tier": None, "request_id": None, "response_id": None,
        "queued_at": time.time(), "service_started_at": None,
        "service_ended_at": None, "ended_at": None,
        "queue_seconds": 0.0, "service_seconds": 0.0,
        "backoff_seconds": 0.0, "backoff_intervals": [],
        "outcome": "queued", "usage_reported": False,
    }
    with _MUTATION_LOCK:
        accumulator.attempts.append(row)
        accumulator.stages.setdefault(
            (row["stage"], row["lane"]), StageUsage(stage=row["stage"], lane=row["lane"])
        )
    token = _active_attempt.set(row)
    started = time.monotonic()
    try:
        yield
    except BaseException as exc:
        record_attempt_outcome("error", error=exc, only_if_unset=True)
        raise
    finally:
        with _MUTATION_LOCK:
            row["ended_at"] = time.time()
            row["elapsed_seconds"] = round(time.monotonic() - started, 6)
            if row["service_started_at"] is None:
                row["queue_seconds"] = row["elapsed_seconds"]
        _active_attempt.reset(token)


def record_service_started() -> None:
    row = _active_attempt.get()
    if row is not None:
        with _MUTATION_LOCK:
            row["service_started_at"] = time.time()
            row["queue_seconds"] = round(max(0.0, row["service_started_at"] - row["queued_at"]), 6)
            row["outcome"] = "in_flight"


def record_service_ended() -> None:
    row = _active_attempt.get()
    if row is not None:
        with _MUTATION_LOCK:
            row["service_ended_at"] = time.time()
            row["service_seconds"] = round(max(0.0, row["service_ended_at"] - (row["service_started_at"] or row["service_ended_at"])), 6)


def record_attempt_outcome(outcome: str, *, error: BaseException | None = None,
                           only_if_unset: bool = False) -> None:
    row = _active_attempt.get()
    if row is not None:
        with _MUTATION_LOCK:
            if only_if_unset and row["outcome"] not in {"queued", "in_flight", "response_received"}:
                return
            if outcome:
                row["outcome"] = str(outcome)
            if error is not None:
                row["error_type"] = type(error).__name__
                row["http_status"] = getattr(error, "status_code", None)
                row["request_id"] = getattr(error, "request_id", None) or row["request_id"]


def wait_for_retry(seconds: float) -> None:
    row = _active_attempt.get()
    started, wall_started = time.monotonic(), time.time()
    try:
        time.sleep(seconds)
    finally:
        if row is not None:
            with _MUTATION_LOCK:
                elapsed = max(0.0, time.monotonic() - started)
                row["backoff_seconds"] += round(elapsed, 6)
                row["backoff_intervals"].append({
                    "started_at": wall_started, "ended_at": time.time(),
                    "requested_seconds": float(seconds), "elapsed_seconds": round(elapsed, 6),
                })


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


def bind_persisted_summary(
    persistence_key: str,
    persisted_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind one durable baseline to the active logical generation run.

    The baseline is captured only once for a given artifact. Later checkpoint
    writes recompute ``baseline + all usage observed in this run`` instead of
    merging the same cumulative run summary into the database repeatedly.
    """
    accumulator = _active.get()
    persisted = (
        persisted_summary if isinstance(persisted_summary, dict) else {}
    )
    if accumulator is None:
        return merge_summaries(persisted)
    key = str(persistence_key)
    if key not in accumulator.persistence_baselines:
        accumulator.persistence_baselines[key] = copy.deepcopy(persisted)
    accumulator.visible_persistence_key = key
    return merge_summaries(
        accumulator.persistence_baselines[key],
        accumulator.summary(),
    )


def cumulative_summary(
    persisted_summary: dict[str, Any] | None,
    *,
    persistence_key: str,
) -> dict[str, Any]:
    """Return durable history plus this run exactly once."""
    return bind_persisted_summary(persistence_key, persisted_summary)


def visible_summary(*, include_attempts: bool = True) -> dict[str, Any]:
    """Return the cumulative summary shown for the active persisted artifact."""
    accumulator = _active.get()
    if accumulator is None:
        return UsageAccumulator().summary()
    key = accumulator.visible_persistence_key
    if key and key in accumulator.persistence_baselines:
        baseline = accumulator.persistence_baselines[key]
        if not include_attempts:
            baseline = {
                **baseline, "request_attempts": [], "mechanical_spans": [],
                "attempt_details_included": False,
            }
        return merge_summaries(
            baseline,
            accumulator.summary(include_attempts=include_attempts),
        )
    return accumulator.summary(include_attempts=include_attempts)


def mark_stage(stage: str) -> None:
    """Record a stage boundary on the active accumulator, if tracking.

    Called by ``progress.step`` so every stage owns a wall-clock window;
    the per-stage ``elapsed_seconds`` in stage rows comes from these
    windows (billable-response window as the fallback for callers that
    never emit steps).
    """
    accumulator = _active.get()
    if accumulator is not None:
        accumulator.mark_stage(stage)


def console_summary() -> dict[str, Any]:
    """The cumulative summary shown on the live console.

    One ledger everywhere (owner request, 2026-08-28): the summary — stage
    table and elapsed time included — is the same cumulative object the
    terminal event carries and ``job.openai_usage`` persists, so the
    numbers shown during parsing, during generation, and after completion
    always agree. ``visible_summary`` already merges the persisted
    baseline (earlier segments: the parse run, prior attempts) with this
    run's accumulator, stage rows and elapsed included.
    """
    # Keep identical totals without repeatedly streaming the entire growing
    # attempt ledger. Full immutable details accompany durable checkpoints
    # and the terminal summary; per-response UI updates remain compact.
    return visible_summary(include_attempts=False)


def record_response(response: Any, *, requested_model: str = "") -> dict[str, Any]:
    """Record one billable Chat Completions response, if tracking is active.

    Existing tests and third-party compatible endpoints may omit ``usage``;
    those responses remain valid and are not assigned invented token counts.
    """
    accumulator = _active.get()
    usage = _get(response, "usage")
    if accumulator is None:
        return current_summary()
    usage_status = "reported"
    if usage is None:
        usage_status = "missing"
    elif (
        _get(usage, "prompt_tokens", _get(usage, "input_tokens")) is None
        or _get(usage, "completion_tokens", _get(usage, "output_tokens")) is None
    ):
        usage_status = "incomplete"
        usage = None
    attempt = _active_attempt.get()
    with _MUTATION_LOCK:
        if attempt is None:
            accumulator.untracked_response_count += 1
        else:
            attempt.update({
                "actual_model": _get(response, "model") or None,
                "actual_reasoning_effort": _get(response, "reasoning_effort") or None,
                "actual_service_tier": _get(response, "service_tier") or None,
                "request_id": _get(response, "_request_id") or _get(response, "request_id") or None,
                "response_id": _get(response, "id") or None,
                "usage_reported": usage is not None,
                "usage_status": usage_status,
                "outcome": "response_received",
            })
        if usage is None:
            accumulator.missing_usage_responses += 1
            return accumulator.summary()

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
    cache_write_tokens = _int(_get(prompt_details, "cache_write_tokens"))
    reasoning_tokens = _int(_get(completion_details, "reasoning_tokens"))
    raw_total = _get(usage, "total_tokens")
    total_tokens = None if raw_total is None else _int(raw_total)
    model = str(_get(response, "model") or requested_model or "unknown")
    reported_tier = str(_get(response, "service_tier") or "").lower()
    cached_tokens = min(input_tokens, cached_tokens)
    cache_write_tokens = min(max(input_tokens - cached_tokens, 0), cache_write_tokens)
    response_cost = (
        _request_cost(
            model=model, input_tokens=input_tokens, cached_input_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens, output_tokens=output_tokens,
        )
        if reported_tier in {"", "default", "standard", "auto"} else None
    )
    try:
        from . import progress

        stage, lane = progress.current_stage(), progress.current_lane()
    except Exception:  # pragma: no cover - attribution must never break billing
        stage, lane = "", ""
    accumulator.add(
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        stage=stage,
        lane=lane,
        estimated_cost_usd=response_cost,
    )
    if attempt is not None:
        with _MUTATION_LOCK:
            attempt.update({
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_tokens,
                "cache_write_tokens": cache_write_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "estimated_cost_usd": float(response_cost) if response_cost is not None else None,
                "pricing_as_of": PRICING_AS_OF,
                "pricing_basis": "standard_text_token_rates" if response_cost is not None else "unpriced_model_or_service_tier",
            })
    summary = accumulator.summary(include_attempts=False)

    # Local import avoids a module cycle. Outside streamed requests this is a
    # cheap no-op, while the web UI receives updated aggregate usage live —
    # the cumulative summary (baseline + this run, stages and elapsed
    # included), identical in shape to what the job record persists.
    try:
        from . import progress

        progress.usage(console_summary())
    except Exception:  # pragma: no cover - accounting must never break generation
        pass
    return summary


def _merge_stage_rows(
    summaries: tuple[dict[str, Any] | None, ...],
) -> list[dict[str, Any]]:
    """Cumulative per-(stage, lane) rows across run segments, in first-seen
    order (earlier segments first, so a parse stage precedes generation)."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    counters = (
        "request_count", "input_tokens", "cached_input_tokens",
        "cache_write_tokens", "output_tokens", "reasoning_tokens",
        "total_tokens", "attempt_count", "provider_request_count",
    )
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for row in summary.get("stages") or []:
            if not isinstance(row, dict):
                continue
            key = (str(row.get("stage") or ""), str(row.get("lane") or ""))
            target = merged.setdefault(key, {
                "stage": key[0],
                "lane": key[1],
                **{name: 0 for name in counters},
                "estimated_cost_usd": 0.0,
                "pricing_complete": True,
                "usage_complete": True,
                "attempt_coverage_complete": True,
                "first_ts": 0.0,
                "last_ts": 0.0,
                "elapsed_seconds": 0.0,
                "elapsed_scope": "shared_stage_window_do_not_sum_lanes",
                "active_request_seconds": 0.0,
            })
            for name in counters:
                target[name] += _int(row.get(name))
            target["usage_complete"] = target["usage_complete"] and row.get("usage_complete") is not False
            target["attempt_coverage_complete"] = target["attempt_coverage_complete"] and bool(
                row.get("attempt_coverage_complete", not row.get("request_count"))
            )
            cost = row.get("estimated_cost_usd")
            if cost is None or row.get("pricing_complete") is False:
                target["pricing_complete"] = False
                target["estimated_cost_usd"] = None
            elif target["pricing_complete"]:
                try:
                    target["estimated_cost_usd"] = round(
                        target["estimated_cost_usd"] + float(cost), 6
                    )
                except (TypeError, ValueError):
                    target["pricing_complete"] = False
                    target["estimated_cost_usd"] = None
            first = _float(row.get("first_ts"))
            if first and (
                not target["first_ts"] or first < target["first_ts"]
            ):
                target["first_ts"] = first
            target["last_ts"] = max(
                target["last_ts"], _float(row.get("last_ts"))
            )
            target["elapsed_seconds"] = round(
                target["elapsed_seconds"]
                + max(0.0, _float(row.get("elapsed_seconds"))),
                3,
            )
            target["active_request_seconds"] = round(
                target["active_request_seconds"] + max(0.0, _float(row.get("active_request_seconds"))),
                3,
            )
    return list(merged.values())


def merge_summaries(*summaries: dict[str, Any] | None) -> dict[str, Any]:
    """Merge persisted/run summaries without repricing historical usage."""
    accumulator = UsageAccumulator()
    saved_cost = Decimal("0")
    pricing_complete = True
    saw_usage = False
    pricing_dates: set[str] = set()
    elapsed_total = 0.0
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        elapsed_total += max(0.0, _float(summary.get("elapsed_seconds")))
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
                cache_write_tokens=_int(row.get("cache_write_tokens")),
                output_tokens=_int(row.get("output_tokens")),
                reasoning_tokens=_int(row.get("reasoning_tokens")),
                total_tokens=_int(row.get("total_tokens")),
                estimated_cost_usd=(
                    None
                    if row.get("pricing_complete") is False
                    else row.get("estimated_cost_usd", _COST_UNSET)
                ),
            )
            if row.get("estimated_cost_usd") is None and row.get("known_usage_estimated_cost_usd") is not None:
                model = str(row.get("model") or summary.get("model") or "unknown")
                accumulator.models[model].estimated_cost_usd += Decimal(str(row["known_usage_estimated_cost_usd"]))
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
    # The fresh accumulator above knows nothing of the inputs' wall-clock
    # or stage attribution — carry both cumulatively.
    merged["elapsed_seconds"] = round(elapsed_total, 3)
    merged["stages"] = _merge_stage_rows(summaries)
    valid_summaries = [row for row in summaries if isinstance(row, dict)]
    attempts: dict[str, dict[str, Any]] = {}
    mechanical_spans: dict[str, dict[str, Any]] = {}
    matrix: dict[tuple[str, str, str], ModelUsage] = {}
    timings: dict[str, float] = {}
    for summary in valid_summaries:
        for row in summary.get("request_attempts") or []:
            if isinstance(row, dict) and row.get("attempt_id"):
                attempts[str(row["attempt_id"])] = copy.deepcopy(row)
        for row in summary.get("mechanical_spans") or []:
            if isinstance(row, dict) and row.get("span_id"):
                mechanical_spans[str(row["span_id"])] = copy.deepcopy(row)
        for row in summary.get("cost_by_stage_lane_model") or []:
            key = tuple(str(row.get(field) or "") for field in ("stage", "lane", "model"))
            item = matrix.setdefault(key, ModelUsage(model=key[2]))
            item.add(
                **{field: _int(row.get(field)) for field in (
                    "request_count", "input_tokens", "cached_input_tokens",
                    "cache_write_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
                )},
                estimated_cost_usd=(
                    Decimal(str(row["estimated_cost_usd"]))
                    if row.get("estimated_cost_usd") is not None and row.get("pricing_complete") is not False
                    else None
                ),
            )
            if row.get("estimated_cost_usd") is None and row.get("known_usage_estimated_cost_usd") is not None:
                item.estimated_cost_usd += Decimal(str(row["known_usage_estimated_cost_usd"]))
        for row in summary.get("stage_timings") or []:
            stage = str(row.get("stage") or "")
            timings[stage] = timings.get(stage, 0.0) + max(0.0, _float(row.get("elapsed_seconds")))
    merged["request_attempts"] = list(attempts.values())
    merged["mechanical_spans"] = list(mechanical_spans.values())
    merged["mechanical_span_count"] = sum(_int(row.get("mechanical_span_count")) for row in valid_summaries)
    merged["mechanical_wall_seconds"] = round(sum(_float(row.get("mechanical_wall_seconds")) for row in valid_summaries), 6)
    merged["mechanical_thread_cpu_seconds"] = round(sum(_float(row.get("mechanical_thread_cpu_seconds")) for row in valid_summaries), 6)
    merged["mechanical_cpu_complete"] = all(row.get("mechanical_cpu_complete") is not False for row in valid_summaries)
    details_included = all(row.get("attempt_details_included", True) for row in valid_summaries)
    merged["attempt_details_included"] = details_included
    merged["attempt_count"] = (
        len(attempts) if details_included
        else sum(_int(row.get("attempt_count")) for row in valid_summaries)
    )
    merged["provider_request_count"] = (
        sum(bool(row.get("service_started_at")) for row in attempts.values()) if details_included
        else sum(_int(row.get("provider_request_count")) for row in valid_summaries)
    )
    merged["missing_usage_response_count"] = sum(_int(row.get("missing_usage_response_count")) for row in valid_summaries)
    merged["untracked_response_count"] = sum(_int(row.get("untracked_response_count")) for row in valid_summaries)
    merged["usage_complete"] = all(row.get("usage_complete") is not False for row in valid_summaries)
    merged["known_usage_estimated_cost_usd"] = round(sum(
        _float(row.get("known_usage_estimated_cost_usd", row.get("estimated_cost_usd")))
        for row in valid_summaries
    ), 12)
    merged["attempt_coverage_complete"] = all(
        row.get("attempt_coverage_complete", not row.get("request_count"))
        for row in valid_summaries
    )
    merged["cost_matrix_complete"] = all(
        row.get("cost_matrix_complete", not row.get("request_count"))
        for row in valid_summaries
    )
    if not merged["usage_complete"]:
        merged["pricing_complete"] = False
        merged["estimated_cost_usd"] = None
    merged["cost_by_stage_lane_model"] = [
        {"stage": stage, "lane": lane, **_model_summary(item)}
        for (stage, lane, _model), item in matrix.items()
    ]
    merged["stage_timings"] = [
        {"stage": stage, "elapsed_seconds": round(seconds, 3)}
        for stage, seconds in timings.items()
    ]
    return merged


def _pricing_for(model: str) -> Pricing | None:
    lowered = model.lower()
    for prefix, pricing in _PRICING:
        if lowered.startswith(prefix):
            return pricing
    return None


def _request_cost(
    *,
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    cache_write_tokens: int,
    output_tokens: int,
) -> Decimal | None:
    """Price one response so per-request cache/long-context rules stay exact."""
    pricing = _pricing_for(model)
    if pricing is None:
        return None
    ordinary_input = max(
        input_tokens - cached_input_tokens - cache_write_tokens,
        0,
    )
    input_value = (
        Decimal(ordinary_input) * pricing.input_per_million
        + Decimal(cached_input_tokens) * pricing.cached_input_per_million
        + Decimal(cache_write_tokens)
        * pricing.input_per_million
        * pricing.cache_write_multiplier
    )
    output_value = Decimal(output_tokens) * pricing.output_per_million
    if (
        pricing.long_context_threshold is not None
        and input_tokens > pricing.long_context_threshold
    ):
        input_value *= pricing.long_input_multiplier
        output_value *= pricing.long_output_multiplier
    return (input_value + output_value) / Decimal(1_000_000)


def _model_summary(item: ModelUsage) -> dict[str, Any]:
    pricing = _pricing_for(item.model)
    cost = (
        float(item.estimated_cost_usd.quantize(Decimal("0.000000000001")))
        if item.pricing_complete
        else None
    )
    return {
        "model": item.model,
        "request_count": item.request_count,
        "input_tokens": item.input_tokens,
        "cached_input_tokens": item.cached_input_tokens,
        "cache_write_tokens": item.cache_write_tokens,
        "uncached_input_tokens": max(
            item.input_tokens - item.cached_input_tokens, 0
        ),
        "output_tokens": item.output_tokens,
        "reasoning_tokens": item.reasoning_tokens,
        "total_tokens": item.total_tokens,
        "estimated_cost_usd": cost,
        "known_usage_estimated_cost_usd": round(float(item.estimated_cost_usd), 12),
        "pricing_complete": item.pricing_complete,
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


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
