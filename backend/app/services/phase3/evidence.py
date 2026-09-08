"""Lossless projection of a named canonical source block for model evidence."""
from __future__ import annotations

import copy
from typing import Any, Mapping


def block_text(block: Mapping[str, Any]) -> str:
    """Keep the complete visual table rendering when the source provided one."""
    return str(
        block.get("display_text_with_visuals")
        or block.get("display_text")
        or block.get("text")
        or block.get("raw_text")
        or ""
    )


def block_context(block: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve explicit relationships; this performs no semantic selection."""
    # Graph ownership and the complete display text are projected separately.
    # Preserve every other recorded relationship/asset field rather than a
    # fragile allowlist that loses future page, cell or content-object refs.
    return {
        str(key): copy.deepcopy(value)
        for key, value in block.items()
        if key not in {
            "block_id", "topic_id", "subtopic_id", "kind", "text",
            "display_text", "display_text_with_visuals",
        }
    }



def decide_with_visual_evidence(*, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Bind the figures already carried by this decision, then decide once.

    The owning pass selected its evidence before this boundary. This helper
    attaches only those explicit image references and preserves their bytes in
    the decision identity; it never searches nearby pages or chooses pictures.
    """
    from . import kernel
    from .. import assessment_visual_evidence

    bound = assessment_visual_evidence.bind(copy.deepcopy(payload), payload)
    decision = copy.deepcopy(kernel.decide(payload=bound, **kwargs))
    flags = decision.setdefault("review_flags", [])
    for flag in assessment_visual_evidence.review_flags(bound):
        flag = flag.replace("assessment_visual_evidence_unavailable", "concept_visual_evidence_unavailable", 1)
        if flag not in flags:
            flags.append(flag)
    return decision


def image_inputs(payload: Mapping[str, Any]) -> list[str]:
    from .. import assessment_visual_evidence

    return assessment_visual_evidence.image_inputs(payload)
