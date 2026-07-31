"""Phase 3.5.2 compatibility for explicit MMD chunk overrides.

Provider-capacity mode retains the section-focused production default. A caller
or test that explicitly changes ``_MMD_CHUNK_CHARS`` still receives that exact
lossless chunk boundary.
"""
from __future__ import annotations

from functools import wraps

from .. import config
from . import generation

_CONTRACT_VERSION = 1


def install() -> None:
    if getattr(generation, "_PHASE352_CHUNK_OVERRIDE_VERSION", 0) >= _CONTRACT_VERSION:
        return

    original_split = generation._split_mmd_into_chunks
    installed_default = int(generation._MMD_CHUNK_CHARS)
    generation._PHASE352_ORIGINAL_SPLIT_MMD = original_split
    generation._PHASE352_INSTALLED_CHUNK_DEFAULT = installed_default

    @wraps(original_split)
    def split_with_explicit_override(
        mmd_text: str,
        max_chars: int | None = None,
    ) -> list[str]:
        if (
            max_chars is None
            and config.OPENAI_PROVIDER_MAX_TOKENS
            and config.use_live_generation()
            and int(generation._MMD_CHUNK_CHARS) != installed_default
        ):
            max_chars = int(generation._MMD_CHUNK_CHARS)
        return generation._PHASE352_ORIGINAL_SPLIT_MMD(
            mmd_text,
            max_chars=max_chars,
        )

    generation._split_mmd_into_chunks = split_with_explicit_override
    generation._PHASE352_CHUNK_OVERRIDE_VERSION = _CONTRACT_VERSION
