# Assessment decision and audit registry

This is the reviewer-facing registry for Step 6's recorded assessment
decisions. It records identities and ownership only; it is never executable
classification policy. The implementation authority remains each named
module's kernel contract and the adopted architecture/handoff.

## Decision kinds

| Kind | Current policy | Semantic owner | Stable row audit |
|---|---|---|---|
| `assessment.cell` | `assessment-cell-1` | Source-atom blueprint axes and marks | `_aegis_assessment_cell_verdict` |
| `assessment.materialize` | `assessment-materialize-2` | Question and complete unweighted semantic answer/rubric content | `_aegis_assessment_materialization` |
| `assessment.answer_restriction` | `assessment-answer-restriction-2` plus both v2.0 registry hashes | Open/Specific answer-space verdict | `_aegis_assessment_answer_restriction` |
| `assessment.marking` | `assessment-marking-2` | Weight decomposition, duration, and keyboard mode | `_aegis_assessment_marking` |
| `assessment.route` | `assessment-route-1` | One released concept home | `_aegis_assessment_route` |
| `assessment.level` | `assessment-level-1` | Basic/Intermediate/Advanced verdict | `_aegis_assessment_level_verdict` |
| `assessment.variant_cluster` | `assessment-variant-cluster-1` | Same-tier variant families | `_aegis_assessment_variant_cluster` |
| `assessment.group_description` | `assessment-group-description-1` | Visible semantic group description | `_aegis_assessment_group_description` |
| `assessment.group_quality` | `assessment-group-quality-1` | Advisory touched-group QA | `_aegis_assessment_group_quality` |
| `assessment.legacy_cell_contract` | `assessment-legacy-cell-contract-2` | Legacy session marks, duration, and keyboard contract | Legacy decision store only |

The legacy cell contract deliberately has its own kind. Before Slice 4 it
shared `assessment.cell` with the MES cell decision despite having a different
schema. The kind rename and policy bump intentionally produce a recorded
re-decision; old immutable records remain readable but unreachable by the new
key.

## Ordering and ownership

The Output-02 order is:

1. cell;
2. semantic materialization;
3. answer restriction over the complete unweighted answer space;
4. marking without changing that answer space;
5. route;
6. level, variant cluster, description, and advisory group QA.

`assessment.materialize` cannot publish an answer-restriction or marking
allocation. `assessment.answer_restriction` receives the complete unparsed
v2.0 registry and has no local Objective, subject, keyword, Policy-ID, family,
or command-word rule. `assessment.marking` receives the explicit blueprint
cell as total-marks authority and records that the full owner-supplied
Question-Paper Blueprint artifact is not currently tracked; it never
fabricates that source.

## Fail-closed mechanics and The Fixer

There is intentionally no `_FIXER_UNACCEPTABLE_CODES` set. `kernel.decide`
passes every Fixer response through the same checker that rejected the author
response and raises `ContractError` when any structural defect remains. This
re-validate-or-raise guarantee is stronger than a maintained deny-list:
identity, coverage, finite-number, weightage-sum, submark, keyword-weight,
group-key, and immutable-text defects cannot ship as acceptable-with-flag.

Critic dissent is different: it is semantic review evidence, remains advisory,
and never retries or vetoes a mechanically valid recorded decision.

## Replay and release projection

Every decision key binds kind, unit identity, sealed envelope, complete payload
hash, and policy version. A changed registry or policy produces a new key and
recorded re-decision; immutable prior records are not edited. Batch APIs refuse
duplicate identities and enforce exact ordered coverage.

The stable audits above carry decision key, policy version, review flags and
Fixer presence without provider names or timestamps. They live in
`build_concepts_release._RELEASE_AUDIT_FIELDS`, ride the release for review,
and are stripped before concept-row database publication. Durable assessment
snapshots use the same stage order and contain their own ordered-row SHA-256.
