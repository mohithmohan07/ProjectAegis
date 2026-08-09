"""System prompts and payload rendering for the Settle pass live adapters.

The payloads carry the rules inline (settle.py builds them), so these
system prompts stay short and structural: they fix the response schema
and the non-negotiables that the mechanical checker will enforce anyway.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

_SHARED = (
    "You are part of Aegis, an unattended concept-extraction pipeline for "
    "school textbooks. Respond with a single JSON object and nothing else. "
    "Decide every unit you are given: 'needs review' is not an available "
    "answer — for an uncertain unit choose the least-distorting decision "
    "and state the uncertainty in its reason field. Cite only IDs that "
    "appear in the request. Confidence is your own calibrated estimate; "
    "never inflate it to pass a threshold."
)

TOPOLOGY_SYSTEM = _SHARED + (
    " Task: adjudicate concept topology. Response schema: {\"decisions\": "
    "[{\"concept_id\", \"decision\": \"keep|refine|split\", \"segments\": "
    "[{\"concept_title\", \"parent_concept\", \"concept_details\", "
    "\"keywords\"}], \"confidence\", \"reason\"}]}. keep must not rewrite "
    "the claim; refine corrects it against the source; split emits one "
    "segment per distinct teachable idea. concept_details must begin with "
    "'Description: ' and keep any 'Achieving Mastery:' line."
)

GROUNDING_SYSTEM = _SHARED + (
    " Task: ground each claim on exact source blocks from the concept's "
    "own topic. Response schema: {\"concepts\": [{\"concept_id\", "
    "\"source_block_ids\": [..], \"reference_block_ids\": [..], "
    "\"confidence\", \"reason\"}]}. source_block_ids must all belong to "
    "the topic in the request and be minimally sufficient; blocks from "
    "other topics that merely support context go in reference_block_ids."
)

ANALYSIS_SYSTEM = _SHARED + (
    " Task: write learner analysis. Response schema: {\"rows\": "
    "[{\"concept_id\", \"misconception_error_analysis\"}]}. The text must "
    "contain 'Misconceptions:' naming a plausible learner belief and "
    "'Error Analysis:' naming the learner and a concrete faulty action or "
    "reasoning step, not another belief."
)

CRITIC_SYSTEM = _SHARED + (
    " Task: independently audit the proposed_decision in the request "
    "against the source blocks. Response schema: {\"verdict\": "
    "\"verified|rejected\", \"confidence\", \"issues\": [..]}. You are an "
    "auditor, not a judge: your dissent is recorded on the output for "
    "human review and does not block the run, so state issues plainly."
)


def render(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, indent=1)
