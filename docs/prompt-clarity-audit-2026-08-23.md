# Prompt clarity audit — 2026-08-23

## Purpose

This audit reviews the instructions that can reach an Aegis model call after
the owner-feedback release and the uniform GPT-5.6 Luna `xhigh` change. The
goal is not to make every prompt longer. It is to make each decision boundary,
evidence source, success condition, and output contract unambiguous while
keeping stable prefixes lean enough for prompt caching.

The review follows the current OpenAI GPT-5.6 guidance: state the goal,
relevant context, hard constraints, required evidence, success criteria, and
output format once; avoid repeated generic reasoning instructions; ask for
clarification only when an interactive choice is actually available. Aegis is
unattended after its pre-spend gates, so run-time ambiguity is resolved by the
least-distorting evidence-bound decision and recorded rationale, not by a
mid-run question to the operator.

Reference: <https://developers.openai.com/api/docs/guides/latest-model>

## Review standard

Every executable prompt was assessed against these requirements:

1. **One owner and one decision.** The prompt names the semantic decision it
   owns and does not pre-empt a later pass.
2. **Evidence boundary.** It says which request evidence is authoritative and
   forbids inference from position, volume, familiar templates, or omitted
   evidence where those shortcuts would distort the decision.
3. **Exact coverage.** Any input collection that must survive is returned once
   and only once; legitimate empty outcomes are named where they are allowed.
4. **Success criteria.** The model can tell what a complete, high-quality
   answer means for this pass, not merely what fields to emit.
5. **Output contract.** The provider receives either a strict transport schema
   or a valid JSON example. Prompt prose does not present commented or
   Python-style pseudo-JSON as strict JSON.
6. **Formatting boundary.** Rich-text fields and type-declared cells remain
   distinct: Equation cells are full raw LaTeX with no `[Katex]`; Phrases are
   wholly plain text; rich display fields may use `[Katex]`; tabular/array
   KaTeX and Markdown pipe tables are unsupported.
7. **Role separation.** Authors decide; critics independently audit and remain
   advisory; The Fixer repairs named defects without dropping valid content;
   Refiners polish only their whitelisted rendered fields.
8. **Durable identity.** A changed prompt either participates in the frozen
   instruction-set hash or advances its explicit `policy_version`.

## Prompt-surface inventory and disposition

| Surface | Principal modules | Audit disposition |
|---|---|---|
| Source reading and canonicalization | `chapter_reading.py`, `canonical_source_phase212.py`, `canonical_source_phase3.py`, Phase 3.4 structured-output contracts, `language_topology.py` | Existing prompts already bind complete source evidence, exact block/section accounting, separate author/correction/Fixer/critic roles, and strict provider schemas. Kept lean; no duplicated JSON schema added where transport already enforces it. |
| Concept topology and content | `generation.py`, `instruction_architect.py`, `phase3/prompts.py`, `concept_example_ownership.py`, `placement_policy.py`, `question_polishing.py` | Stage ownership was already strong. The common Phase-3 boundary now explicitly forbids imported patterns and requires schema/exact-coverage verification. The content author now states that Types/Cases/Examples and routed questions arrive in later passes, preventing the intermediate description-only response from being mistaken for the final assembled row. |
| Pre-Learning | `phase3/prelearn.py`, `phase3/premap.py`, `phase3/preanalyse.py`, `phase3/prequestions.py`, their prompt constants | Existing prompts distinguish assumed prior knowledge from chapter teaching, permit evidence-supported empty captures, require complete answers and genuine—not number/name-only—question variety, and carry the ~5-per-concept calibration as a target rather than a quota. The audit did not duplicate those already-clear rules in the system prefix. |
| Assessment generation and extraction | `assessment_prompts.py`, `generation.py`, `assessment_materialization.py`, `assessment_cells.py` | Added an explicit evidence boundary, a valid comment-free JSON example, and the lowercase paper-option contract at both authoring seams. Materialization advances to `assessment-materialize-9`. |
| Assessment routing, answer restriction, marking, grouping, deduplication, QA | `assessment_routing.py`, `assessment_answer_restriction.py`, `assessment_marking.py`, `assessment_grouping.py`, `assessment_dedup.py`, `assessment_quality.py` | Existing prompts already carry complete semantic evidence, forbid quota/default shortcuts, separate axes, and give critics advisory-only scope. The Q21 rules remain explicit: four-mark Descriptive items need at least two rubric blocks and every typed block is medium-pure. No wording was added where it would merely duplicate a checked contract. |
| Assessment Master Refiner | `assessment_master_refiner.py` | Replaced a Python-style critic example with strict JSON and advanced candidate/umbrella identities to `assessment-master-refiner-candidate-3` / `assessment-master-refiner-3`. The field whitelist and medium-purity contract remain unchanged. |
| Concept release review and Refiner | `release_review.py`, `release_refiner.py`, Phase-3 Refiner prompt | Existing response schemas and mechanical whitelists already define the output boundary. No redundant schema prose added. |
| Workbook authoring | `workbook_prompts.py`, `aegis_pipeline/create_workbooks/src/subject_prompts.py` | Active planner/builder prompts already use a plan-before-authoring split, exact inventory coverage, subject-specific representation rules, and JSON-only output. Subject guides are deliberately fragments appended to the builder contract; they do not repeat its output schema. |
| Retired standalone CLIs | `aegis_pipeline/bulk_upload_ultimate.py`, `aegis_pipeline/mmd_to_concepts_excel.py` | Reviewed for reachability but not treated as production authorities. `docs/aegis-restructure.md` explicitly retires these scripts in favour of `backend/app`; changing their obsolete semantic contracts would create a second standard rather than improve the deployed API path. |

The unused `assessment_prompts.REVIEW_PROMPT` constant was removed. No caller
ever sent it to the API; leaving it beside the active prompt registry made the
surface look reviewed twice when the actual live path uses its record-contract
checks and the newer assessment decision critics. Removing dead prompt prose
changes no run and adds no model call.

## Owner-format rules now pinned in provider instructions

- Paper options render as lowercase `a), b), c), d)`; option content does not
  duplicate the label that the workbook writer adds.
- Rich question/display/explanation math may use `[Katex]...[/Katex]` because
  those fields render rich text automatically.
- A type-declared `Equation` answer/rubric/keyword cell is full raw LaTeX with
  no `[Katex]`; a `Phrases` cell is full plain text with no TeX. The two are
  never mixed in one cell.
- A four-mark Descriptive item has at least two distinct rubric blocks; a
  single block weighted four is invalid.
- KaTeX `tabular`/`array` and Markdown pipe tables are not emitted. A
  source-associated table image is preserved; otherwise every cell is retained
  as explicitly labelled coordinate text without semantic reconstruction.
- Description authoring is an intermediate pass. The final Concept workbook is
  assembled later with Types, Cases, complete source-question Examples,
  learner analysis, hubs, and routed QIDs; the description prompt must not
  duplicate those passes.
- Pre-Learning is generated only from captured prerequisite evidence. Its
  questions are fresh prerequisite checks, calibrated to board/grade/context,
  with complete expected answers and real—not cosmetic—variety.

## Identity and replay impact

- `assessment.materialize`: `assessment-materialize-7` →
  `assessment-materialize-9`.
- `assessment.master_refiner.candidate`:
  `assessment-master-refiner-candidate-2` →
  `assessment-master-refiner-candidate-3`.
- Assessment Master Refiner umbrella:
  `assessment-master-refiner-2` → `assessment-master-refiner-3`.
- Phase-3 constant changes are already part of The Architect's frozen-core
  hash, so affected Concept/Pre-Learning decisions cannot replay under the old
  prompt text.
- Registry-backed prompt defaults participate in the same frozen prompt
  material or the generation request/cache identity used by their caller.

This re-keying is intentional: the first run after deployment may re-author
affected durable units. Subsequent resumes reuse the new content-addressed
decisions normally.

## Admin-override deployment check

The Admin prompt registry intentionally lets an operator override a shipped
default. An existing override wins over this branch's revised default. Before
deploying, inspect `/admin/prompts` for overrides on these changed keys:

- `assessment.base`
- `assessment.type.objective`
- `assessment.output`
- `identify.type_hint.objective`

Do not erase authored overrides blindly. Compare each override with the new
default, carry forward any intentional local instruction, and add the evidence,
lowercase-label, valid-JSON, and typed-cell rules where applicable. Reset the
key only when the shipped default is now the intended authority. Phase-3 and
materialization prompts changed here are code constants, not Admin overrides.

## Cost discipline

Clarity was added only where it closes an observed ambiguity. Existing
structured-output schemas were not copied into source prompts, critic prompts
were not expanded into second author prompts, and stage-specific instructions
remain in their owning pass. This keeps the stable shared prefix cacheable and
avoids paying repeatedly for duplicated prose. Cost reduction remains subject
to the same release-quality gates; fewer tokens are not accepted as a win if
coverage, grounding, or workbook integrity regresses.

## Verification

- Focused prompt/materialization/release gate: 57 passed.
- Complete backend gate: 2,900 passed, 7 expected xfails, zero failures.
- Frontend gate: TypeScript clean, 88 tests passed, production build green.
- Changed Python modules compile; `git diff --check` is clean.
- Provider credentials were removed and live generation was disabled for the
  test runs. No paid model call, database publication, deployment, or merge was
  performed.
