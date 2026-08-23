# Assessment Master Refiner — Slice 5 implementation contract

Status: **decided before implementation**
Governing sources: `CLAUDE.md` Rule 1,
`docs/aegis-restructure.md` §8.3/Q10/Q13, and
`docs/restructure-handoff.md` §5.

This document resolves the implementation choices surfaced by the Slice 5
read-only map. It is an implementation contract, not a new semantic policy.
Where it conflicts with the governing sources above, those sources win.

## 1. Scope and insertion point

Slice 5 adds one `assessment_master` dispatch at the existing
`release_refiner.refine_release(..., output_kind=...)` seam. Assessment logic
lives in a new `assessment_master_refiner.py` module. The existing
`concepts_release` branch keeps the same rules, provider/critic defaults,
payload, checker, decision kind/key, policy, fallback text and shape, terminal
validation, resealing, diff ordering, and summary behavior.

The assessment pass runs after:

1. materialization;
2. answer restriction;
3. marking;
4. routing;
5. level and variant-family decisions;
6. group description and advisory group quality review; and
7. question-label minting.

It runs before `assessment_release_service.create_release`, because that call
freezes and persists the immutable release snapshot. The caller assembles the
complete payload first and sends `records=[payload]` through the seam. The
assessment module returns exactly one complete payload record, an ordered
release diff, and review flags.

Generated `NA` group shells participate in transient render/read-back
validation but are not model units. Only authored groups in the payload are
eligible for refinement.

## 2. Decision units and evidence

The pass uses two distinct kernel kinds because candidate prose and group
descriptions have different response contracts:

| Unit | Decision kind | Policy | Unit ID |
|---|---|---|---|
| Candidate | `assessment.master_refiner.candidate` | `assessment-master-refiner-candidate-3` | `candidate_id` |
| Group | `assessment.master_refiner.group` | `assessment-master-refiner-group-1` | `group_key` |

Each unit receives:

- the full type-preserving domain record, including all immutable fields;
- the actual rendered Master row or rows produced by the production renderer
  and read back from the generated workbook;
- the relevant concept/group/member context;
- board, grade, subject, chapter, and profile context; and
- the complete written author and critic contracts.

Printer/page/row/bounding-box/source-position fields are removed recursively
from model evidence. The complete unstripped domain records remain available
to local identity mechanics and are bound indirectly through the proposal
comparison; the content-addressed model payload contains every non-positional
identity, asset, marking, membership, and prose value relevant to replay.

Every unit uses `phase3.kernel.decide`. There is no bespoke retry or
adjudicator. The independent critic is advisory: dissent, low confidence,
malformed output, or critic failure can only add a review flag. The existing
run-level Fixer is supplied to the assessment dispatch and any Fixer response
must pass the same unit checker. Replay of an unchanged unit calls no author,
critic, or Fixer.

## 3. Editable whitelist

Only these candidate prose leaves are editable:

- `display_answer` for a Descriptive candidate;
- `answer_explanation`;
- `answers[*].answer_content` for Objective and Descriptive candidates; and
- `sub_questions[*].keywords[*].keyword` (wording only).

Only this group prose leaf is editable:

- `semantic_description`.

Keyword wording is treated as rubric prose. Its row identity, order,
`answer_type`, and `weightage` remain immutable. No sub-question stem, mark,
cardinality, ordering, or other decomposition field is editable.

Q21 narrows the editable answer leaf by its already-declared medium. An
`Equation` proposal remains one full raw-LaTeX cell without `[Katex]` and any
prose stays inside a TeX text atom; a `Phrases` proposal remains wholly plain
text. The checker also rejects tabular/array/Markdown-table syntax. These are
lexical mechanics only; the Refiner's model remains responsible for semantic
prose preservation.

Everything outside the whitelist is immutable and type-stable, including:

- `question` and `question_text`;
- candidate/group cardinality and order;
- QIDs, source atom IDs, candidate IDs, cell IDs, labels, concept homes,
  group keys, group membership/order, family, tier/type, sequence and visible
  names;
- answer restriction and its reason;
- marks, duration, keyboard mode, correct-option markers, all weights,
  sub-question marks, answer/sub-question/keyword order and cardinality;
- sheet/category/cognitive/difficulty/source/appears-in fields;
- assets, URLs, image manifests, tables, rich content objects and visual
  requirements;
- the Output-01 hierarchy and source-release seal; and
- all pre-existing flags, audits, authority and provenance.

Mechanics compare canonical, type-preserving locked projections rather than
Python equality (`1` and `1.0` are not interchangeable). A proposal that
combines one allowed edit with any locked drift is discarded in full; the
allowed half is never projected out and applied.

The ordered inventory of QID tokens, HTTPS URLs, image tags and KaTeX blocks
inside editable prose must also remain exact **at each editable field path**.
The Refiner may polish around a token, never alter, remove, duplicate, reorder,
or migrate it to a different answer/rubric field. Previously visible prose
must also remain mechanically visible: whitespace, Unicode controls/format
marks, nonspacing marks, and known blank filler glyphs cannot erase a field.

The orchestrator additionally snapshots learner text before the pass and
calls the existing `_assert_learner_text_unchanged` guard afterwards.

## 4. Validation and rollback

The assessment module establishes a baseline before provider spend:

- candidate mechanics and exact arithmetic via
  `assessment_release.validate_candidate`;
- payload mechanics via `assessment_release.freeze_payload`; and
- the exact production dual-output render, parse and read-back path via
  `assessment_workbook.build_dual_output`, using
  `assessment_release_service.snapshot_from_staged_release` to complete the
  transient snapshot exactly as publication will.

If the baseline cannot be constructed or validated, the whole original
payload is returned with stable review warnings; the release still proceeds.

Proposals are applied one unit at a time to a full trial payload. After a
candidate proposal, candidate arithmetic, payload mechanics and full workbook
read-back run again. After a group proposal, payload mechanics and full
workbook read-back run again. Exact read-back additionally proves every
editable value survived workbook serialization unchanged. A new error,
renderer exception, value truncation, escaping difference, or read-back
mismatch rolls back only that unit.

After all units, the complete final payload is validated again. If an
unexpected combined regression appears, accepted units are replayed from the
last known-valid state one at a time so the offending unit can be rolled back.
If attribution is impossible, every refinement is rolled back and the
unrefined release ships flagged. Invalid marking arithmetic or read-back state
can never ship as a Refiner result.

## 5. Never-block behavior

- Provider, contract, Fixer, identity, or validation failure for one unit:
  retain that candidate/group's authored values byte-stable, append
  `assessment_master_refiner_review`, record the reason, and continue.
- Critic dissent/failure: retain a mechanically valid author refinement,
  append the same warning, and record the advisory evidence.
- Global setup/render/final-validation failure: return the entire original
  payload, annotate every authored candidate/group with the warning, and
  record the release-level reason.

The warning is present on candidate/group `flags`, not only in the top-level
diff, so the existing readiness calculation exposes the release as released
with warnings. The Refiner never blocks publication.

## 6. Diff, audit, and snapshot

The immutable assessment payload carries `refinements`, shaped as:

- umbrella policy `assessment-master-refiner-3`;
- `output_kind="assessment_master"`;
- the two decision-kind/policy identities;
- ordered changes with `unit_kind`, `unit_id`, precise `field_path`, `before`,
  `after`, and `reason`;
- ordered review flags; and
- a stable summary.

Discarded or rolled-back proposals produce no change entry.

Candidates/groups receive the shared private audit field
`_aegis_assessment_master_refinement`, containing unit kind, stable decision
authority, changed paths, rationale, review flags, Fixer presence and status.
It is registered in `build_concepts_release._RELEASE_AUDIT_FIELDS`, remains
outside visible workbook slots, and is stripped at the existing publication
boundary.

The run writes `source.phase3-assessment-master-refinements.json` beside the
other assessment decision snapshots. It binds the sealed envelope, ordered
stable row audits, release diff and rows hash, with no timestamps or provider
names.

No new workbook sheet or public schema field is added in Slice 5.

## 7. Marking-decomposition carryover

The recorded blueprint cell is permanently authoritative for total marks.
The model's per-item `assessment.marking` verdict permanently owns the mark
decomposition. No external marking-rubric document is consulted, expected,
pending or missing; the Question-Paper Blueprint & Analysis document is not
runtime evidence.

Slice 5 removes stale pending/missing-dependency wording from marking prompts,
audits, comments and the assessment decision registry. Because this changes
the model evidence contract, the marking policy advances to
`assessment-marking-3`; immutable v2 decisions remain in the store and the new
policy records a deliberate re-decision.

## 8. Regression and golden contract

Required regression families:

- positive Objective/Descriptive answer prose and occupied-group description
  refinements, exact rendered read-back, release diff and audit;
- attempted `question`/`question_text` change;
- every named identity, marking, restriction, membership, asset and nested
  structure drift, including type-only numeric drift;
- QID/URL/image/KaTeX token drift inside editable prose;
- per-unit provider/contract/Fixer failure and global fallback;
- critic dissent/malformed/sub-floor/failure as advisory flags;
- arithmetic and workbook-read-back rollback isolated to one unit;
- exact coverage/order and unchanged Output-01/source/cell/placement data;
- replay with zero author/critic/Fixer calls and re-keying on policy,
  envelope, marking, asset or membership changes;
- deterministic snapshot and timestamp/provider-free authority;
- end-to-end release readiness and published Master prose; and
- default versus explicit `concepts_release` equality of records, diff,
  flags, provider payloads and decision keys.

Existing Phase-3 goldens, `rne_envelope.json`, accepted reference workbooks,
and `assessment_workbook_template.json` must not change. Any new recorded
fixture must be additive and explained; no existing recorded verdict is
hand-edited.
