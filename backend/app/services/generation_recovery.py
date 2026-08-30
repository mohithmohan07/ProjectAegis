"""Exact lifecycle gate for mutations on non-resumable Concept runs.

Only the durable typed verdict ``resume_allowed is False`` closes a mutation.
Missing metadata and every other value retain the historical behavior; callers
must never infer this state from exception prose, filenames, or source content.
"""
from __future__ import annotations

from typing import Any

from .. import models


class NonResumableRunError(RuntimeError):
    """A mutation would bypass the run's recorded recovery route."""

    def __init__(
        self,
        recovery: dict[str, Any],
        *,
        operation: str = "continue this run",
    ) -> None:
        self.recovery = dict(recovery)
        self.resume_allowed = False
        self.recovery_action = str(
            recovery.get("recovery_action") or "reconvert_new_upload"
        )
        self.recovery_message = str(
            recovery.get("recovery")
            or recovery.get("message")
            or (
                "This saved checkpoint cannot complete by resuming. Start a "
                "new upload and conversion."
            )
        )
        self.operation = str(operation or "continue this run")
        super().__init__(self.recovery_message)


def blocked_recovery(job: models.UploadJob) -> dict[str, Any] | None:
    """Return the durable block only for an explicit false verdict."""

    recovery = job.generation_recovery
    if isinstance(recovery, dict) and recovery.get("resume_allowed") is False:
        return dict(recovery)
    return None


def require_mutation_allowed(
    job: models.UploadJob,
    *,
    operation: str = "continue this run",
) -> None:
    """Refuse a mutation that would contradict a non-resumable verdict."""

    recovery = blocked_recovery(job)
    if recovery is not None:
        raise NonResumableRunError(recovery, operation=operation)
