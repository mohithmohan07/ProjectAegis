"""Opt-in structured response contracts for already fixed API envelopes.

The schema and local validator come from the same strict Pydantic model;
there is no second, partial JSON Schema interpreter. This first rollout
covers advisory reviews only, leaving authored content and dynamic task
structures under their existing full mechanical and semantic contracts.
Empty issue arrays and arbitrarily detailed issue strings remain valid.
The caller's identity/range/cross-field checks and independent review
authority still run after this transport boundary.

OpenAI GPT-5.6 accepts the closed schema on the wire. Other providers and
models keep their existing JSON-object request, with the exact same local
response validation. This avoids probing unknown capabilities by spending
requests. Add a model family here only after verifying its wire support.

Wire reference: https://developers.openai.com/api/docs/guides/structured-outputs
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AdvisoryCriticResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)

    verdict: Literal["verified", "dissent"]
    confidence: float = Field(ge=0, le=1)
    issues: list[str]


class ItemReviewResponse(AdvisoryCriticResponse):
    candidate_id: str


@dataclass(frozen=True)
class ResponseSchema:
    """One versioned schema and its matching local validation model."""

    name: str
    model: type[BaseModel]

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "strict": True,
            "schema": self.model.model_json_schema(),
        }

    def identity(self) -> dict[str, str]:
        """Serializable evidence for a caller's persisted decision identity."""
        encoded = json.dumps(
            self.json_schema(), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return {"name": self.name, "sha256": hashlib.sha256(encoded).hexdigest()}

    def validate_response(self, response: object) -> None:
        # Validation only: never replace strings, reorder content, or populate
        # absent fields through defaults/coercion. Callers keep the original.
        self.model.model_validate(response, strict=True)


def advisory_critic_schema() -> ResponseSchema:
    return ResponseSchema("aegis_advisory_critic_v1", AdvisoryCriticResponse)


def item_review_schema() -> ResponseSchema:
    return ResponseSchema("aegis_item_review_v1", ItemReviewResponse)


def provider_response_format(
    response_schema: ResponseSchema | None, *, provider: str, model: str,
) -> dict[str, Any]:
    if (
        response_schema is not None
        and (
            (provider == "openai" and str(model).lower().startswith(("gpt-5.6", "gpt-5.4-mini")))
            or (provider == "gemini" and str(model).lower().startswith("gemini-3.8-flash"))
        )
    ):
        return {"type": "json_schema", "json_schema": response_schema.json_schema()}
    return {"type": "json_object"}


class PreQuestionDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    question_id: str
    question_text: str
    answer: str
    rationale: str
    tier: Literal["Basic", "Intermediate", "Advanced"]


class PreQuestionAuthorResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    questions: list[PreQuestionDraft]


def pre_question_author_schema() -> ResponseSchema:
    return ResponseSchema("aegis_pre_question_author_v1", PreQuestionAuthorResponse)
