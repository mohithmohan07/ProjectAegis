"""The Fixer must be able to satisfy a strict caller contract (Q58).

``prompts.FIXER_SYSTEM`` requires a top-level ``rationale`` — and the kernel
reads it for the recorded review flag — while a caller's checker is typically a
strict ``extra="forbid"`` schema that has no such field. The Fixer was therefore
instructed to return an artifact its own checker had to reject, so it could
never succeed on a strict-schema stage: three attempts, three identical
"Extra inputs are not permitted" defects, then ContractError. Job 130's Step 2
died that way after the reviewed file had already been read.
"""
import pytest
from pydantic import BaseModel, ConfigDict

from app.services.phase3 import kernel


class StrictArtifact(BaseModel):
    """A caller contract shaped like the real ones: closed, no rationale."""

    model_config = ConfigDict(extra="forbid", strict=True)
    verdict: str


def _strict_checker(value):
    try:
        StrictArtifact.model_validate(value)
    except Exception as exc:  # noqa: BLE001 — the caller's own message
        return ["Invalid schema: " + str(exc)]
    return []


def _always_blocked(_request):
    """An author that never satisfies the checker, forcing the Fixer."""
    return {"verdict": "draft", "unexpected": True}


def _fixer_following_its_prompt(_request):
    """What FIXER_SYSTEM actually asks for: the artifact PLUS a rationale."""
    return {"verdict": "resolved", "rationale": "Blocked on schema; chose the supported verdict."}


def test_the_fixer_can_satisfy_a_strict_contract_while_still_explaining(tmp_path):
    decision = kernel.decide(
        kind="test.strict",
        unit_id="u1",
        envelope_sha256="e" * 64,
        payload={"stage": "test.strict"},
        provider=_always_blocked,
        checker=_strict_checker,
        critic=lambda request: {"verdict": "verified", "confidence": 1.0, "issues": []},
        fixer=_fixer_following_its_prompt,
        store=kernel.DecisionStore(tmp_path / "decisions"),
        policy_version="test-1",
    )

    # The artifact stored is the one the caller's contract accepts...
    assert decision["response"] == {"verdict": "resolved"}
    assert "rationale" not in decision["response"]
    # ...and the Fixer's explanation is not lost: it rides the review flag,
    # and the decision is recorded as a Fixer decision (Q13: never silent).
    assert decision["fixer"] is True
    flags = " ".join(decision.get("review_flags") or [])
    assert "fixer:" in flags
    assert "chose the supported verdict" in flags


def test_a_caller_whose_contract_wants_rationale_keeps_it(tmp_path):
    """The protocol field is only separated when the checker refuses it."""

    class WithRationale(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        verdict: str
        rationale: str

    def checker(value):
        try:
            WithRationale.model_validate(value)
        except Exception as exc:  # noqa: BLE001
            return ["Invalid schema: " + str(exc)]
        return []

    decision = kernel.decide(
        kind="test.rationale",
        unit_id="u1",
        envelope_sha256="f" * 64,
        payload={"stage": "test.rationale"},
        provider=lambda _r: {"verdict": "draft"},
        checker=checker,
        critic=lambda request: {"verdict": "verified", "confidence": 1.0, "issues": []},
        fixer=_fixer_following_its_prompt,
        store=kernel.DecisionStore(tmp_path / "decisions"),
        policy_version="test-1",
    )

    assert decision["response"]["rationale"] == (
        "Blocked on schema; chose the supported verdict."
    )


def test_a_genuinely_unfixable_block_still_raises(tmp_path):
    """Separating the protocol field must not make the Fixer look successful."""

    def hopeless_fixer(_request):
        return {"verdict": 123, "rationale": "still wrong"}

    with pytest.raises(kernel.ContractError):
        kernel.decide(
            kind="test.hopeless",
            unit_id="u1",
            envelope_sha256="a" * 64,
            payload={"stage": "test.hopeless"},
            provider=_always_blocked,
            checker=_strict_checker,
            critic=lambda request: {"verdict": "verified", "confidence": 1.0, "issues": []},
            fixer=hopeless_fixer,
            store=kernel.DecisionStore(tmp_path / "decisions"),
            policy_version="test-1",
        )
