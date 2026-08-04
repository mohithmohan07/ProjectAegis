"""Public model projections for terminal Build Concepts releases."""
from __future__ import annotations

from .. import models


def install() -> None:
    current = getattr(models.UploadJob, "checkpoint_available", None)
    if not isinstance(current, property):
        return
    getter = current.fget
    if getter is None or getattr(getter, "_aegis_release_terminal", False):
        return

    def checkpoint_available(job: models.UploadJob) -> bool:
        # The durable checkpoint is retained inside a released job so it can be
        # downloaded with the diagnostic context. It is not, however, a pending
        # run to resume: generation has already produced its terminal release.
        if (
            str(getattr(job, "module", "")) == "build_concepts"
            and str(getattr(job, "status", "")) == "released"
        ):
            return False
        return bool(getter(job))

    checkpoint_available._aegis_release_terminal = True
    models.UploadJob.checkpoint_available = property(checkpoint_available)
