"""Phase 3.4.1 strict-schema completeness guard.

Reasoning models can consume the whole completion budget before emitting useful
visible JSON.  In that edge case some compatible clients expose an empty or
partially closed JSON object together with ``finish_reason=length``.  Phase 3.4
already escalates malformed/truncated text, but a syntactically valid partial
object must also be rejected before a caller mistakes it for a complete schema.

This final wrapper checks required keys, required nested object fields, and array
min/max cardinality from the supplied strict schema.  An incomplete object is
retried with a larger completion budget and a compact-output instruction.  It
never manufactures missing fields or relaxes caller validation.
"""
from __future__ import annotations

import copy
import os
from typing import Any

from . import canonical_source_phase22 as phase22
from . import progress

_CONTRACT_VERSION = 1


class _IncompleteStrictSchemaError(RuntimeError):
    pass


def _matches_schema_shape(value: object, schema: object) -> bool:
    if not isinstance(schema, dict):
        return True
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            return False
        required = [str(item) for item in schema.get("required") or []]
        if any(key not in value for key in required):
            return False
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and not _matches_schema_shape(
                    value[key], child_schema
                ):
                    return False
        return True
    if schema_type == "array":
        if not isinstance(value, list):
            return False
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < int(minimum):
            return False
        if maximum is not None and len(value) > int(maximum):
            return False
        item_schema = schema.get("items")
        return all(_matches_schema_shape(item, item_schema) for item in value)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def _completion_cap(initial: int) -> int:
    configured = max(
        4000,
        int(os.environ.get(
            "AEGIS_STRUCTURED_JSON_MAX_COMPLETION_TOKENS", "65536"
        )),
    )
    return max(int(initial), min(131072, configured))


def _max_retries() -> int:
    return max(
        1,
        min(
            5,
            int(os.environ.get(
                "AEGIS_STRUCTURED_JSON_SCHEMA_COMPLETENESS_RETRIES", "3"
            )),
        ),
    )


def install() -> None:
    if (
        getattr(phase22, "_PHASE341_SCHEMA_COMPLETENESS_VERSION", 0)
        >= _CONTRACT_VERSION
    ):
        return

    original = phase22._openai_multimodal_json
    phase22._PHASE341_ORIGINAL_OPENAI_MULTIMODAL_JSON = original

    def shape_checked_openai_multimodal_json(**kwargs):
        response_schema = copy.deepcopy(kwargs.get("response_schema") or {})
        strict_schema = response_schema.get("schema") or {}
        schema_name = str(response_schema.get("name") or "unnamed_json_schema")
        purpose = str(kwargs.get("purpose") or "source_adjudication").replace(
            "_", " "
        )
        current_budget = max(1000, int(kwargs.get("max_tokens") or 0))
        cap = _completion_cap(current_budget)
        attempts = _max_retries()
        base_system = str(kwargs.get("system") or "")

        for attempt in range(1, attempts + 2):
            call_kwargs = dict(kwargs)
            call_kwargs["max_tokens"] = current_budget
            if attempt > 1:
                call_kwargs["system"] = (
                    base_system
                    + "\n\nSTRICT-SCHEMA COMPLETENESS RECOVERY: The previous "
                    "response was syntactically valid but omitted required fields "
                    "or required array members. Return every required field and "
                    "opaque ID exactly once. Keep evidence and reason strings "
                    "short; do not omit rows to save tokens."
                )
            result = original(**call_kwargs)
            if _matches_schema_shape(result, strict_schema):
                return result
            if attempt > attempts or current_budget >= cap:
                raise _IncompleteStrictSchemaError(
                    f"OpenAI {purpose} schema {schema_name} remained incomplete "
                    f"after {attempt} bounded response(s) at {current_budget} tokens"
                )
            previous = current_budget
            current_budget = min(cap, max(previous * 2, previous + 4000))
            progress.log(
                f"OpenAI {purpose} schema {schema_name} returned an incomplete "
                f"strict object at {previous} tokens; retrying with "
                f"{current_budget} tokens ({attempt}/{attempts}).",
                level="warning",
            )
        raise _IncompleteStrictSchemaError(
            f"OpenAI {purpose} schema {schema_name} did not converge"
        )

    phase22._openai_multimodal_json = shape_checked_openai_multimodal_json
    phase22._PHASE341_SCHEMA_COMPLETENESS_VERSION = _CONTRACT_VERSION
