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
    "the claim; refine corrects it against the source; split is rare — "
    "only for a row conflating ideas a teacher would lesson-plan apart, "
    "never for carving one coherent explanation into aspects or steps, "
    "and every segment must stand as a substantial concept with a full "
    "Description and its own distinct 'Achieving Mastery:' line. "
    "concept_details must begin with 'Description: ' and keep any "
    "'Achieving Mastery:' line."
)

GROUNDING_SYSTEM = _SHARED + (
    " Task: ground each claim on exact source blocks from the concept's "
    "own topic. Response schema: {\"concepts\": [{\"concept_id\", "
    "\"source_block_ids\": [..], \"reference_block_ids\": [..], "
    "\"confidence\", \"reason\"}]}. source_block_ids should belong to "
    "the topic in the request and be minimally sufficient; blocks from "
    "other topics that merely support context go in reference_block_ids. "
    "One exception: when the concept's own topic does not teach the "
    "claim at all, ground on the chapter blocks that DO teach it (from "
    "other_topic_blocks) and say so in reason — that ships flagged for "
    "review. Never return an empty source_block_ids."
)

ANALYSIS_SYSTEM = _SHARED + (
    " Task: author each concept's learner-facing content in one pass. "
    "Response schema: {\"rows\": [{\"concept_id\", "
    "\"concept_description\", \"achieving_mastery\", "
    "\"misconception_error_analysis\"}]}. concept_description is the "
    "full teaching paragraph, grounded only on the concept's "
    "source_blocks, in original language (never a copied source "
    "passage) — it is the basis for books, worksheets, notes, slides, "
    "and interactive content, so it must teach: define the idea "
    "precisely, state the key rule or method and what each term means, "
    "give when and why it applies, and make it concrete with the "
    "source's own facts, figures, or a compact worked cue; carry no "
    "'Description:' label and no other section inside it. "
    "achieving_mastery is one sentence naming what a learner can do "
    "once the concept is mastered — distinct for every concept, never "
    "shared or paraphrased between concepts. "
    "misconception_error_analysis starts directly with "
    "'Misconceptions:' (a plausible learner belief) or 'Error "
    "Analysis:' (the learner's concrete faulty action or reasoning "
    "step, not another belief); state it concretely with the specific "
    "quantity, step, or claim named — never a vague 'confuses X with "
    "Y'; default to the ONE section carrying "
    "the sharpest insight for the concept, and add the second ONLY "
    "when it contributes genuinely different insight — never one "
    "paraphrasing the other, and never reuse one concept's analysis "
    "for another. In every field wrap EVERY mathematical expression "
    "exactly as [Katex] valid LaTeX [/Katex]; never emit raw TeX, $ "
    "delimiters, bare sub/superscripts, or bare equations outside "
    "those tags. When the request carries a culminations array, also "
    "return {\"culminations\": [{\"concept_id\", \"consolidation\"}]}: "
    "for each culmination a 2-4 sentence consolidation paragraph tying "
    "the topic's member concepts together — what the learner can now "
    "do with them combined — never a list of concept names."
)

HOST_SYSTEM = _SHARED + (
    " Task: certify one host concept per assignment unit, and place "
    "every question (qid) under its correct concept. Response schema: "
    "{\"assignments\": [{\"unit_id\", \"decision\": "
    "\"existing|create_new\", \"host_concept_title\", \"confidence\", "
    "\"reason\", \"qid_placements\": {\"<qid>\": {\"falls_under\": "
    "[\"<settled concept title>\", ...], \"destination_concept_title\": "
    "\"<settled concept title>\", \"reason\"}}, \"new_concept\": "
    "{\"concept_title\", \"parent_concept\", \"concept_details\", "
    "\"keywords\", \"source_block_ids\", \"_semantic_topic_id\"}}]}. "
    "For decision existing, host_concept_title must be the EXACT title "
    "of a settled concept from the request. qid_placements must cover "
    "EVERY qid of the unit. Placement rules for each question — apply "
    "them by understanding the question against the settled concepts: "
    "(1) If a question falls under a specific concept alone, place it "
    "under that same concept. (2) If a question falls under two or more "
    "concepts within the same topic, place it under the Culmination "
    "concept of that same topic. (3) If a question falls under two "
    "different concepts across different topics, place it under the "
    "involved concept that belongs to the LATER topic (teaching order). "
    "(4) If a question falls under more than one concept in a topic AND "
    "another concept in another topic, place it in the Culmination "
    "concept of the later topic. Placement is MOST-LIKELY, not "
    "sure-shot: when a question is close to what a concept teaches, "
    "place it there — never leave a question unplaced and never drop "
    "one. A case can hold several questions; place every one of them. "
    "Never break a question apart: a question with sub-questions is "
    "placed whole, exactly as it is. Weigh each question's own wording, "
    "kind, and printed position: in-text/checkpoint questions belong "
    "with the material they are printed inside; end-of-chapter "
    "exercises place purely by what they ask. Surface similarity is "
    "not ownership — an image/caricature/table question belongs to the "
    "concept teaching that content, not to another concept that also "
    "involves images. A definitional/recall question about one "
    "quantity, device, or term belongs to the concept that teaches "
    "that quantity; a question interpreting a specific source "
    "account/print belongs to the concept dedicated to that source "
    "when one exists; an application/appliance-context question "
    "belongs to the chapter's application concept when one exists; a "
    "research/find-out-more activity extending one concept's material "
    "belongs to that concept, never to a Culmination. Exactly one "
    "destination per question; ties "
    "resolve to the later topic. falls_under lists every teaching "
    "concept the whole question genuinely requires; "
    "destination_concept_title is your placement under these rules and "
    "must be the EXACT title of a settled concept row from the request "
    "(a Culmination row's title when the rules call for it). "
    "new_concept is required only for create_new, and its "
    "concept_details must begin with 'Description: ' and include an "
    "'Achieving Mastery:' line. new_concept.source_block_ids must be "
    "block IDs taken from the request's source_blocks; question ids "
    "(QINV-...) are never source blocks."
)

POLISH_SYSTEM = _SHARED + (
    " Task: repair concept rows that failed the terminal content gate. "
    "Response schema: {\"rows\": [{\"row_ref\", \"concept_title\", "
    "\"concept_details\", \"keywords\"}]}. Echo each row_ref exactly as "
    "given and return every requested row. Fix ONLY what the row's "
    "validation_errors name: a Description must teach in original "
    "language (never repeat a long contiguous source passage), "
    "Misconceptions must name a concept-specific incorrect belief, "
    "Error Analysis must name the learner and a concrete faulty action "
    "or reasoning step, and no sentence may end truncated. The learner "
    "analysis needs at least ONE genuine section — Misconceptions or "
    "Error Analysis; both only when they carry different insight, and "
    "never one restating the other. Keep every other section — the "
    "'Achieving Mastery:' line, Types — intact and in place, keep the "
    "concept's meaning, and never rename it."
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
