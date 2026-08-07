"""Wire Pass 1 (Chapter Reading) into the Build Concepts run boundary.

Two patches, mirroring how Phase 2.2 reconciles a derived semantic source
with the immutable ``UploadJob.mmd_text`` audit copy:

* ``generation.concepts_from_mmd`` — a live run reads the chapter first and
  generates from the normalized MMD. The raw upload text, and the checkpoint
  fingerprint computed from it, never change.
* ``canonical_source_phase22.active_semantic_source`` — deposit validation
  and semantic recovery hand this the raw audit copy; resolving it through
  the cached reading first means they see exactly the source the run
  compiled, with Phase 2.2 overlays applied on top as before.

A run resumed across the deployment that introduced this pass keeps its raw
source: its saved stages were compiled from raw text, and silently switching
sources would invalidate every one of them. Fresh runs read; a resumed run
that already read reuses the cached reading byte for byte.
"""
from __future__ import annotations

from types import ModuleType

from .. import config
from . import canonical_source_phase22 as phase22
from . import chapter_reading, progress


def install(generation: ModuleType | None = None) -> None:
    if generation is None:
        from . import generation as generation_module

        generation = generation_module

    if not getattr(
        generation.concepts_from_mmd, "_chapter_reading_installed", False
    ):
        original_concepts_from_mmd = generation.concepts_from_mmd

        def concepts_from_mmd(mmd_text: str, *args, **kwargs):
            live = kwargs.get("live")
            use_live = (
                config.use_live_generation() if live is None else bool(live)
            )
            raw = str(mmd_text or "")
            if use_live and raw:
                reading = _reading_for_run(generation, raw, kwargs)
                if reading is not None:
                    artifacts = kwargs.get("artifacts")
                    if isinstance(artifacts, dict):
                        artifacts["chapter_reading"] = {
                            "census": reading.get("census") or [],
                            "dropped_furniture": (
                                reading.get("dropped_furniture") or []
                            ),
                            "provenance": reading.get("provenance") or {},
                        }
                    if reading.get("mode") == "live":
                        mmd_text = (
                            str(reading.get("normalized_mmd") or "") or raw
                        )
            return original_concepts_from_mmd(mmd_text, *args, **kwargs)

        concepts_from_mmd._chapter_reading_installed = True
        generation.concepts_from_mmd = concepts_from_mmd

    if not getattr(
        phase22.active_semantic_source, "_chapter_reading_installed", False
    ):
        original_active_semantic_source = phase22.active_semantic_source

        def active_semantic_source(raw_source: str) -> str:
            return original_active_semantic_source(
                chapter_reading.normalized_for(raw_source)
            )

        active_semantic_source._chapter_reading_installed = True
        phase22.active_semantic_source = active_semantic_source


def _reading_for_run(
    generation: ModuleType, raw: str, kwargs: dict
) -> dict | None:
    """The reading this run should use, or ``None`` to keep the raw source."""
    prior = None
    try:
        prior = generation._newest_compatible_concept_checkpoint(
            kwargs.get("resume_checkpoint")
        )
    except Exception:  # noqa: BLE001 — an unreadable envelope is no prior
        prior = None
    if prior and chapter_reading.cached_reading(raw) is None:
        progress.log(
            "This run's checkpoints were compiled before chapter reading "
            "was deployed; keeping the raw source so they stay coherent. "
            "The next fresh run will read the chapter first.",
            level="warning",
        )
        return None
    return chapter_reading.read_chapter(
        raw,
        source_filename=str(kwargs.get("chapter_title") or ""),
    )
