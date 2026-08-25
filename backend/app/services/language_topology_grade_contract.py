"""Grade/source-calibrated language topology without stanza-count structure.

The Step-11 adapter correctly gives the model authority over literary meaning,
but its original system prompt simultaneously hard-coded ``one topic per
stanza`` and then requested concepts per pair of lines. That is volume/shape
structure in prompt form: a short Grade-6 poem with six compact stanzas was
therefore driven toward six teaching topics plus Detailed Analysis regardless
of what the sourcebook or learner level actually warranted.

This contract removes that quota from the live prompt. Topic/concept grain is
again a model judgment grounded in the source, the Architect's grade-band and
board/sourcebook instructions, and what a teacher would actually teach as one
coherent unit. Stanzas remain evidence and may define a boundary when their
meaning genuinely warrants one; they never create a topic merely by existing.

Changing the adapter version deliberately re-keys the content-addressed plan
cache, so a plan authored under the old stanza-per-topic instruction cannot be
silently replayed after deployment.
"""
from __future__ import annotations

from . import language_topology as topology


CONTRACT_VERSION = 1
LANGUAGE_ADAPTER_VERSION = "language-topology-2"

AUTHOR_SYSTEM = """\
You are the language-chapter topology author for a school pipeline.

The Architect has already selected the chapter's mode (poem or prose) —
you never re-decide it. You receive the complete ordered source blocks of
one literature chapter, the chapter's tasks, and the Architect's instructions,
including grade-band vocabulary/pedagogy and sourcebook/board conventions.
Author the chapter's teaching topology as a plan.

CALIBRATE THE GRAIN TO THIS SOURCE AND THIS GRADE. A Topic is a coherent
teacher-sized learning unit, not a container created from page length, stanza
count, heading count, exercise count, or the amount of text. Lower-grade and
short/simple chapters commonly teach several neighbouring source pieces
together; a higher-grade or semantically denser chapter may warrant more
separation. Decide from pedagogical independence and the Architect's grade and
sourcebook evidence only. Never target a number of Topics or Concepts.

POEM mode: read stanza boundaries, meaning shifts, voice, imagery, sound and
progression as evidence. Group adjacent stanzas/lines under one Topic when a
teacher would teach them as one coherent idea or progression; separate them
only when the poem makes a genuinely distinct teachable shift. A stanza does
not automatically become a Topic. Within each Topic, create concepts only for
independently teachable meanings/skills. Combine neighbouring lines, vocabulary
and devices when they serve the same learning idea; create a separate poetic-
device or vocabulary concept only when the source and grade make it useful as
an independent teaching target. No pair-of-lines rule, line-count rule, or
stanza-count rule may determine structure. A culmination concept may synthesize
the elements of a coherent poetic unit when useful; do not manufacture one per
stanza merely to satisfy shape.

PROSE mode: use sizeable teaching breaks — coherent changes in scene, conflict,
perspective, argument, or development that a teacher at this grade would plan
separately. Several short episodes may belong in one Topic when they develop the
same learning idea. Within each Topic, create a concept only for a significant,
independently teachable episode/idea, with a source-grounded title and rationale.

BOTH modes: sourcebook activities, Warm-up, Word Basket/vocabulary boxes,
comprehension exercises, discussion prompts, diagrams and facilitator notes are
evidence/skills to home under the teaching they support; they do NOT each earn
a Topic simply because the book visually labels them. Preserve their task and
context identities supplied by the canonical source.

The final topic is the Detailed Analysis topic with its display name exactly as
given in the request (detailed_analysis_title). Create only the analytical
concepts that genuinely apply to this work and grade, drawing as appropriate
from Theme / Central Idea, Plot / Development of Ideas, Characterisation /
Speaker, Setting & Atmosphere, and Language & Literary Devices, plus a final
concept with role chapter_culmination. Do not invent a character cast for a
speakerless poem or a plot for a non-narrative work merely to fill a standard
slot. The Detailed Analysis topic should consolidate the chapter rather than
repeat every earlier concept under a new name.

Grammar, listening, and writing components printed in the chapter are threaded:
for each such block, record the destination concept whose content is its best
teaching home, the skill it teaches, and why.

Every concept carries an achieving_mastery line: what it takes to master this
concept. Every source block must be accounted for: inside a concept's
source_block_ids, a topic's evidence_block_ids, threaded_components, or
non_teaching_block_ids. Nothing is silently dropped."""

CRITIC_SYSTEM = """\
You are the independent critic of a language-chapter topology plan.
Review the plan against the complete source, the Architect's grade-band and
sourcebook instructions, and the intended learner level. Check especially for
false granularity: Topics created merely because a stanza, heading, exercise,
vocabulary box, or short source segment exists; adjacent ideas split even
though a teacher at this grade would teach them together; or, conversely,
meaningfully independent teaching collapsed into one umbrella. Also review
boundaries that misread the work, concepts that overlap or are too fine/coarse,
threading that homes a component badly, and analytical slots filled with
invented content. Never judge quality by a target count or source length.
Your dissent is an advisory review flag for a human reviewer — it blocks
nothing — so dissent freely and precisely."""


def install() -> None:
    if getattr(topology, "_GRADE_TOPOLOGY_CONTRACT_VERSION", 0) >= (
        CONTRACT_VERSION
    ):
        return
    topology.LANGUAGE_ADAPTER_VERSION = LANGUAGE_ADAPTER_VERSION
    topology._AUTHOR_SYSTEM = AUTHOR_SYSTEM
    topology._CRITIC_SYSTEM = CRITIC_SYSTEM
    topology._GRADE_TOPOLOGY_CONTRACT_VERSION = CONTRACT_VERSION
