"""Closed, versioned checkpoint schema for runtime telemetry extensions.

Validation does not coerce, strip, reprice or rewrite imported records. Legacy
usage remains under the existing checkpoint schema; v2 remains readable and
new records declare v3 to distinguish live requests from unresolved usage.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 3
MIN_SCHEMA_VERSION = 2
Count = Annotated[int, Field(ge=0, le=10**15)]
Seconds = Annotated[float, Field(ge=0, le=366 * 24 * 60 * 60)]
Timestamp = Annotated[float, Field(ge=0, le=10**11)]
Cost = Annotated[float, Field(ge=0, le=10**9)]
Label = Annotated[str, Field(max_length=512)]
Identifier = Annotated[str, Field(max_length=512)]


class Closed(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class Backoff(Closed):
    started_at: Timestamp
    ended_at: Timestamp
    requested_seconds: Seconds
    elapsed_seconds: Seconds


class Attempt(Closed):
    attempt_id: Identifier
    stage: Label
    lane: Label
    purpose: Label
    provider: Label
    requested_model: Label
    requested_reasoning_effort: Label
    requested_service_tier: Label
    actual_model: Label | None
    actual_reasoning_effort: Label | None
    actual_service_tier: Label | None
    request_id: Identifier | None
    response_id: Identifier | None
    queued_at: Timestamp
    service_started_at: Timestamp | None
    service_ended_at: Timestamp | None
    ended_at: Timestamp | None
    queue_seconds: Seconds
    service_seconds: Seconds
    backoff_seconds: Seconds
    backoff_intervals: list[Backoff] = Field(max_length=1000)
    outcome: Literal[
        "queued", "in_flight", "response_received", "success", "provider_error",
        "queue_timeout", "truncated_response", "invalid_json", "invalid_schema",
        "refused_response", "error",
    ]
    usage_reported: bool
    elapsed_seconds: Seconds | None = None
    error_type: Label | None = None
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    usage_status: Literal["reported", "missing", "incomplete"] | None = None
    input_tokens: Count | None = None
    cached_input_tokens: Count | None = None
    cache_write_tokens: Count | None = None
    output_tokens: Count | None = None
    reasoning_tokens: Count | None = None
    estimated_cost_usd: Cost | None = None
    pricing_as_of: Label | None = None
    pricing_basis: Literal["standard_text_token_rates", "unpriced_model_or_service_tier"] | None = None


class StageTiming(Closed):
    stage: Label
    elapsed_seconds: Seconds


class CostMatrixRow(Closed):
    stage: Label
    lane: Label
    model: Label
    request_count: Count
    input_tokens: Count
    cached_input_tokens: Count
    cache_write_tokens: Count = 0
    uncached_input_tokens: Count
    output_tokens: Count
    reasoning_tokens: Count
    total_tokens: Count
    estimated_cost_usd: Cost | None
    known_usage_estimated_cost_usd: Cost
    pricing_complete: bool
    pricing_source: Annotated[str, Field(max_length=2048)]


class MechanicalSpan(Closed):
    span_id: Identifier
    parent_span_id: Identifier | None
    operation: Label
    stage: Label
    lane: Label
    thread_id: Annotated[int, Field(ge=0, le=2**64)]
    started_at: Timestamp
    ended_at: Timestamp | None
    elapsed_seconds: Seconds | None
    thread_cpu_seconds: Seconds | None
    cpu_scope: Literal["current_thread_excludes_child_processes"]
    outcome: Literal["running", "success", "error"]


class UsageExtensionsV2(Closed):
    usage_schema_version: Annotated[int, Field(ge=2, le=2)]
    attempt_count: Count
    provider_request_count: Count
    missing_usage_response_count: Count
    untracked_response_count: Count
    attempt_coverage_complete: bool
    attempt_details_included: bool
    cost_matrix_complete: bool
    usage_complete: bool
    known_usage_estimated_cost_usd: Cost
    request_attempts: list[Attempt] = Field(max_length=20_000)
    cost_by_stage_lane_model: list[CostMatrixRow] = Field(max_length=20_000)
    stage_timings: list[StageTiming] = Field(max_length=20_000)
    mechanical_spans: list[MechanicalSpan] = Field(max_length=20_000)
    mechanical_span_count: Count
    mechanical_wall_seconds: Seconds
    mechanical_thread_cpu_seconds: Seconds
    mechanical_cpu_complete: bool


class UsageExtensionsV3(UsageExtensionsV2):
    usage_schema_version: Annotated[int, Field(ge=3, le=3)]
    pending_request_count: Count
    unresolved_usage_request_count: Count


class StageExtensions(Closed):
    # Optional for imported legacy stage rows. Validation never inserts these
    # defaults into the caller's stored historical object.
    attempt_count: Count = 0
    provider_request_count: Count = 0
    usage_complete: bool = True
    attempt_coverage_complete: bool = False
    elapsed_scope: Literal["shared_stage_window_do_not_sum_lanes"] | None = None
    active_request_seconds: Seconds = 0.0
    known_usage_estimated_cost_usd: Cost | None = None
    pending_request_count: Count = 0
    unresolved_usage_request_count: Count = 0
    missing_usage_response_count: Count = 0


TOP_EXTENSION_FIELDS = frozenset(UsageExtensionsV3.model_fields)
STAGE_EXTENSION_FIELDS = frozenset(StageExtensions.model_fields)


def validate_extensions(value: dict, path: str) -> None:
    extensions = {key: value[key] for key in TOP_EXTENSION_FIELDS if key in value}
    if not extensions:
        return
    try:
        schema = UsageExtensionsV3 if value.get("usage_schema_version") == 3 else UsageExtensionsV2
        schema.model_validate(extensions, strict=True)
    except ValueError as exc:
        raise ValueError(f"{path} runtime telemetry schema is invalid: {exc}") from exc


def validate_stage_extensions(value: dict, path: str) -> None:
    extensions = {key: value[key] for key in STAGE_EXTENSION_FIELDS if key in value}
    try:
        StageExtensions.model_validate(extensions, strict=True)
    except ValueError as exc:
        raise ValueError(f"{path} runtime stage schema is invalid: {exc}") from exc
