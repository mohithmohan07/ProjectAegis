# Phase 3.8: Boundary-aware exact grounding

Phase 3.8 closes a source-order edge case exposed by the RNE acceptance run.
A concept may be correctly assigned to one academic topic while a continuation
paragraph or Figure is placed in the immediately adjacent semantic topic because
of converter or page reading-order drift.

## Contract

- Native topic blocks remain authoritative.
- Exact grounding may inspect a bounded, source-ordered window from only the
  immediately previous and next topic.
- Every adjacent block is labelled with its source topic, target topic, and
  boundary relation.
- An adjacent block may be selected only when an independent critic verifies that
  it visibly continues the target topic.
- Boundary evidence may not be used to preserve a genuinely cross-topic or
  over-merged concept. Such rows return to topology adjudication for move,
  refine, split, or retirement.
- Relevant verified original-PDF pages remain available for visual claims.
- Only the original concept that produced an exact-grounding rejection is retried.
- Repeated ineffective decisions receive a cycle-breaking instruction and may
  not return the same effective title, topic, and Description.

## Resume behaviour

Existing PDF evidence, semantic hierarchy, QIDs, mined Types, and compatible
per-concept topology decisions are reused. Cached final topology rows grounded
under an earlier contract are rejected once and rebuilt under Phase 3.8.
