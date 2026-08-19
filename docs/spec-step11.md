# Step 11 specification: poem/prose language adapter

Status: specification only; no implementation is included here.

Depends on:

- Step 8 S10's machine-ID publication join;
- correction of Step 8 S8's Output-02/04 identity projection and read-backs,
  which currently strip the machine ID from concept-title cells and collapse
  same-titled questionless concepts to one visible-title set;
- the Step 11 removal of chapter-wide title deduplication and title-based
  duplicate validation; and
- the Step 2 question-extraction follow-up in
  `docs/spec-question-extraction.md` before live acceptance.

## 1. Outcome

Step 11 turns the Architect's recorded `language_mode` selection into a
model-authored topology contract inside Phase 2.1. It remains one pipeline:
the adapter changes the semantic instructions and intermediate topology for a
language chapter, while Settle, Host, Polish, Assemble, the four outputs, and
explicit publication remain shared.

The adapter must produce a source-grounded, replayable topology plan before
concept skeleton authoring. It does not infer poem/prose locally and does not
rewrite the Architect's decision.

## 2. Governing rules

1. **The Architect is the mode authority.** The adapter consumes the selected
   `poem` or `prose` mode and its rationale. No code branch examines source
   keywords, rhyme, line lengths, punctuation, filenames, or subject names to
   choose a mode.
2. **All boundaries are model verdicts.** Stanzas, meaning-carrying line units,
   sizeable prose breaks, episodes, and analytical facets are judgments about
   the work.
3. **The source is closed-world.** Every literary source block and every
   end-of-chapter language component is represented in the plan or explicitly
   ruled non-teaching/context with evidence.
4. **Identity is not wording.** Topic and concept identity is positional and
   topic-scoped. Equal visible names in different topics are allowed and remain
   distinct.
5. **Culmination is a role.** It is an explicit model-authored role field, not
   an English prefix test. The visible title may be localized or reviewer-edited.
6. **No volume-derived structure.** Page count, line count, stanza length,
   character count, source length, chunk count, and desired concept count never
   determine topology.
7. **Decide once.** Author decisions are content-addressed and replayed. The
   independent critic is advisory (Q10). A mid-run unresolved contract goes to
   the real Fixer once, is recorded and flagged, and the run completes (Q13).
8. **Nothing is silently lost.** Grammar, listening, writing, figures, tasks,
   repeated names, and awkward source fragments are placed or visibly flagged
   (R4).
9. **Achieving Mastery remains universal.** Every ordinary, culmination, and
   Detailed Analysis concept ends with its model-authored mastery statement.

## 3. Adapter input and recorded plan

### 3.1 Inputs

The author receives:

- the sealed Architect instruction set, including mode, rationale, subject
  topology, grade vocabulary, board/publication conventions, and cautions;
- canonical blocks with stable IDs, source spans, reading order, figures, and
  semantic source text;
- the semantic graph and its source-contract hash;
- the adjudicated Question/Task Inventory;
- chapter title and publication metadata; and
- any durable prior adapter decision for the same identity.

The Phase 3 graph-preparation wrapper must pass the relevant instruction slots,
not only their hash. The selected mode must be visible as evidence to the
adapter while the hash continues to key cache invalidation.

### 3.2 Plan schema

The exact serialized schema is an implementation choice, but it must express
at least:

| Field | Meaning |
|---|---|
| `adapter_version` | Cache/schema identity |
| `instruction_set_sha256` | Architect decision identity |
| `mode` and `mode_rationale` | Replayed Architect selection, not a new verdict |
| `source_contract_hash` | Exact canonical source identity |
| `topics[]` | Ordered language topics with stable plan IDs and evidence block/span IDs |
| `concepts[]` | Ordered concepts within one topic, each with a stable plan ID |
| `semantic_role` | `ordinary`, `stanza_culmination`, `detailed_analysis`, or `chapter_culmination` |
| `source_units[]` | Exact block/span evidence and their relationship to the concept |
| `facets[]` | Meaning, line analysis, episode, theme, device, vocabulary, grammar/listening/writing, etc. |
| `threaded_components[]` | End component occurrence, destination concept, and rationale |
| `task_qids[]` | Source task occurrences relevant to the concept; placement remains governed downstream |
| `review_flags[]` | Author uncertainty, critic dissent, and Fixer interventions |
| `decision_provenance` | Prompt/schema/provider/model hashes and content-addressed key |

The schema must represent two equal display titles under different topic plan
IDs without collision.

## 4. Poem mode

### 4.1 Topics

The author identifies stanzas from the work's source evidence and creates one
topic per stanza in reading order. Printed numbering and blank lines are
evidence, never rules. A work with irregular typography, refrain blocks, or a
stanza split across pages must still be judged semantically.

Topic display names should be model-authored, brief, and reviewable. Their
machine identity is positional and persists independently of that wording.

### 4.2 Meaning units and concepts

Within each stanza, the author identifies each pair of lines that jointly
conveys one meaning and authors a concept for that semantic unit. “Pair” means
the work's meaning-bearing two-line unit; implementation must never iterate
over physical lines two at a time. Where OCR line breaks or the poem's form
make a literal pair ambiguous, the model records the evidenced unit or the
Fixer chooses the best flagged placement.

For the stanza as a whole, the plan accounts for three non-overlapping bodies
of teaching:

1. **Meaning unit:** literal and metaphorical reading, line analysis, and the
   setup needed to understand those lines.
2. **Language craft:** poetic devices and vocabulary grounded in those lines.
3. **Stanza culmination:** rhyme/form where applicable and how the stanza's
   elements work together.

“Non-overlapping” is a semantic contract checked by an author/critic verdict.
It is not token-overlap arithmetic. A device may be named in a line concept and
synthesized in the culmination when the model explains the distinct teaching
purpose of each occurrence.

Every stanza receives one explicit `stanza_culmination` plan role. The visible
title need not begin with `Culmination` and may repeat a title used under a
different stanza topic.

## 5. Prose mode

The author divides the story or narrative at sizeable teaching breaks. A break
is a coherent change in scene, episode, conflict, perspective, or development
that a teacher would plan separately; it is not a page interval, paragraph
count, token budget, or fixed number of breaks.

Within a topic, the author identifies significant plot episodes. Each episode
is a concept with a dramatic, source-grounded display title and a recorded
rationale for why it is independently teachable. Minor actions, ornamental
details, and adjacent events serving one objective remain inside one episode.
No target episode count is supplied or inferred from source volume.

Literary analysis that is necessary to understand an episode may be taught
there. Chapter-wide synthesis is reserved for the final Detailed Analysis
topic rather than duplicated mechanically under every episode.

## 6. Final Detailed Analysis topic

For both poem and prose modes, the final topic display name is exactly:

`Detailed Analysis of '<Name>'`

`<Name>` is the recorded work name from chapter metadata/source resolution; it
is not extracted with a title regex at this stage.

The author creates the applicable standard concepts in this order:

1. Theme / Central Idea
2. Plot / Development of Ideas
3. Characterisation / Speaker
4. Setting & Atmosphere
5. Language & Literary Devices
6. Culmination

Applicability and content are model judgments. The plan must not invent a
character cast for a speakerless poem or a conventional plot for a non-narrative
work merely to fill a shape. If a standard heading is not literally applicable,
the author records the work-appropriate interpretation under that analytical
slot; unresolved cases reach the Fixer and ship flagged. The final culmination
has an explicit role distinct from each stanza culmination.

## 7. Grammar, listening, and writing threading

The author inventories every printed grammar, listening, and writing component
as source-owned material. For each occurrence it decides:

- which earlier poem/prose concept supplies the content context;
- what language skill the component teaches or practices;
- whether the component is learner activity, assessable task, explanatory
  content, or more than one of those; and
- why that destination is the best teaching home.

Threading is exact-once. It may create an Activity/Info Hub item, attach QIDs,
or enrich a concept facet according to the existing container contracts, but
it never copies the same source occurrence into several concepts without an
explicit multi-placement verdict. No tense vocabulary, heading name, or
end-of-chapter position decides placement.

## 8. Integration and required retirements

### 8.1 Phase 2.1 and graph integration

- Add a language-adapter author/critic/Fixer pass after the Architect decision
  is available and before the skeleton topology is frozen.
- Record the plan with the canonical source artifacts and carry its stable
  topic/concept plan IDs into the semantic graph and Phase 3 envelope.
- Re-key semantic graph, skeleton, and downstream decision caches with adapter
  version and plan hash.
- Make skeleton/recovery prompts consume the plan as source-grounded topology,
  not merely the words `Language mode: poem` or `prose`.
- Keep general subject behavior unchanged for `expository` mode.

### 8.2 Same-title survival

The following changes are one end-to-end contract:

- retire `_dedupe_titles_chapter_wide` and its call sites;
- remove `duplicate_title` as a chapter-wide structural error;
- retain duplicate detection only for the same topic/plan identity where it
  proves two rows claim one identity, and route semantic ambiguity to a model;
- publish and update by persisted `machine_id`, never chapter-wide title;
- key question routing, related-concept links, QC aggregates, and workbook
  placement by machine/plan identity; and
- make both assessment snapshot builders carry `concept_machine_id`, and make
  Outputs 02/04 compose every concept-title cell through the shared
  `identity.titled` function so `topic_concept_labels` is an exact-text roster
  of the cells it names;
- validate concept presence and multiplicity by `concept_key`/persisted machine
  ID, not a set of visible titles, at both the Master validator and Refiner
  read-backs; questionless tail rows are part of that identity ledger; and
- prove that workbook re-import restores the original persisted IDs instead of
  recording `imported_without_machine_id` and re-minting them;
- add a regression with two stanza topics, each containing a concept named
  `Courage`, and prove both survive authoring, validation, staging, workbook
  rendering, publication, re-download, and re-import. Include a questionless
  pair so the OD5 tail-row path and both read-backs are load-bearing.

This preserves Step 8's actual topic-scoped positional identity instead of
merely asserting that the minter is correct in isolation.

### 8.3 Culmination role

Replace title-prefix or generic-word recognition wherever it controls
behavior. Consumers must read the recorded `semantic_role` (or its persisted
equivalent). Human-facing titles stay editable without changing role or
identity.

Affected surfaces include at least:

- generic skeleton-family filtering;
- `concept_refiner.is_culmination` consumers;
- coverage ledger exemptions;
- culmination count/order validation;
- Pre needed-for exclusions; and
- writer/QC checks.

Mechanical gates may verify that required role fields and one-per-topic role
identities exist. They may not infer the role from English text.

### 8.4 Prompt and cache governance

- Register or directly enumerate every new author, critic, correction, and
  Fixer prompt in the frozen core.
- Add `language_mode` and any board/chapter slots actually read by a pass to
  that pass's rule payload and decision key.
- Record prompt text/hash, model/provider, source contract, instruction hash,
  adapter version, and plan hash.
- A changed adapter prompt or Architect mode must invalidate affected semantic
  decisions, never silently replay them.

## 9. Failure behavior

- Invalid response shape before model spend may fail closed as configuration.
- Once language topology authoring has started, an exhausted correction or a
  semantic non-decision invokes the real Fixer. The Fixer receives source
  blocks, Architect mode/rationale, proposed plan, critic issues, active
  prompts, and the failed contract.
- The Fixer chooses one best plan, which is cached, attached to affected rows,
  and flagged. It never drops a stanza, episode, component, task, or figure.
- Provider unavailability or exhausted quota is genuine impossibility and may
  halt with a named recoverable state. It must not fall back to line pairing,
  regex mode detection, generic expository topology, or empty slots.

## 10. Verification

### 10.1 Scripted-provider tests

Tests must prove:

- the Architect's mode and rationale reach the adapter request;
- `expository` bypasses language topology without changing existing output;
- critic rejection records flags and does not gate or retry indefinitely;
- bounded correction exhaustion invokes the real Fixer and completes;
- decision replay performs zero provider calls;
- prompt, instruction, source, or adapter-version changes re-key decisions;
- no production code counts lines/pages/characters to choose topology;
- no production regex/keyword list chooses poem/prose, stanza, episode,
  culmination, or component placement;
- same-titled concepts under different stanza topics survive end to end;
- both question-bearing and questionless same-titled concepts retain their
  original machine IDs through Output-02/04 render, read-back, download, and
  re-import;
- localized/edited culmination titles retain their recorded roles;
- every source block and language component is accounted for; and
- no question is lost between the adjudicated source inventory and its final
  concept placement.

### 10.2 Live-provider acceptance

A live provider and real complete chapters are required. Scripted responses
cannot validate “sizeable,” “significant,” metaphorical reading, episode grain,
or appropriate threading.

Minimum corpus:

- *The Elevator* as the required prose chapter;
- one poem with a refrain or irregular stanza typography;
- one poem where the same visible concept name legitimately appears in two
  stanzas;
- one chapter with grammar, listening, and writing components at the end;
- one Devanagari language chapter or edition; and
- one deliberately misselected-mode fixture to verify the Architect decision
  is visible and reviewable rather than silently overridden.

For every chapter, review the adapter plan, source evidence, critic flags,
Fixer records, rendered Concept File and Master File, publication result, and
no-spend replay. The live report must state provider/model, prompt hashes,
instruction hash, costs, elapsed calls, and any claims deferred to the staging
acceptance corpus.

No full-suite run is a substitute for this chapter review.

## 11. Slice ownership

1. **L1 — plan contract and transport.** Define the adapter artifact, thread
   instruction slots into graph preparation, seal plan IDs/hashes into the
   graph/envelope, and add scripted replay/governance tests.
2. **L2 — poem author.** Implement stanza and meaning-unit topology,
   culmination roles, critic, correction, and Fixer path.
3. **L3 — prose author.** Implement sizeable-break and episode topology under
   the same contract.
4. **L4 — analysis and threading.** Build the final Detailed Analysis topic and
   exact-once grammar/listening/writing placements for both modes.
5. **L5 — identity and residue retirement.** Remove chapter-wide title
   deletion/validation, finish machine-ID publication, repair Output-02/04
   identity composition and identity-keyed read-backs, and replace English
   culmination inference with explicit role transport.
6. **L6 — live acceptance.** Validate the required corpus and record the
   evidence and costs.

L1 depends on the Architect and may land before the question-extraction repair.
L2-L5 may use scripted inventories while developing. L6 cannot pass until the
text-source semantic question inventory is live, because an adapter cannot
prove exact coverage over tasks that vanished upstream.

## 12. Definition of done

- The selected mode changes an explicit, recorded Phase 2.1 topology plan.
- No semantic boundary or role is derived from text shape, vocabulary, or
  volume.
- Poems, prose, and expository chapters still share the downstream pipeline.
- Every language concept has an Achieving Mastery statement.
- The final Detailed Analysis topic and its six analytical slots are present
  with source-grounded, work-appropriate content.
- Grammar, listening, and writing material is accounted for and threaded by
  recorded verdict.
- Two equal concept names under different stanza topics — including a
  questionless OD5 pair — survive through publication, re-download, and
  re-import with their original different persisted IDs.
- Culmination behavior survives localization and title edits because it is
  keyed by role, not English wording.
- Critic dissent is advisory; mid-run blocks become one recorded Fixer
  decision and the release completes.
- Live acceptance on *The Elevator*, a repeated-name poem, and a Devanagari
  language source is recorded and reviewed.

