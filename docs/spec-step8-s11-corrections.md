# Step 8 S11 changes-list corrections

Status: measured repair log; specification only. No pipeline or test code is
included here.

Evidence basis: `claude/step-8-four-output-schema` at `d7d2e2f`, checked
19 August 2026. This log re-verifies every symbol and coordinate in the S11
**Changes** bullet of `docs/spec-step8.md:3524-3545`. It distinguishes a
pending change from a claim that is no longer executable as written.

## 1. The two proposed files are still new

**CLAIM**

S11 adds `backend/app/services/release_qc.py` and
`docs/release-qc-checklist.md`.

**MEASURED TRUTH**

Neither path exists at `d7d2e2f`. The word **NEW** remains accurate.

**HOW TO CONFIRM**

```bash
git ls-tree -r --name-only d7d2e2f -- backend/app/services docs | grep -E '(^|/)release_qc.py$|release-qc-checklist.md$' || true
```

## 2. `audit(payload)` cannot run before the current payload literals exist

**CLAIM**

`release_qc.audit(payload, ...)` runs in both staging functions “immediately
before the payload dict is assembled,” and its issues are merged into that
payload.

**MEASURED TRUTH**

Neither function has a `payload` value at the proposed point. Post first
assigns the literal at `build_concepts_release.py:1911`; Pre first assigns it at
`:2684`. Their current issue fields are `:1941` and `:2708`. S11 must either
name a provisional-payload construction that precedes the audit or place the
audit after assembly and before persistence. The present wording asks the new
function to inspect a variable that does not yet exist.

The old call-site coordinates are also stale: Post `stage_release` begins at
`:1781`, and Pre `stage_pre_release` begins at `:2580`.

**HOW TO CONFIRM**

```bash
git show d7d2e2f:backend/app/services/build_concepts_release.py | nl -ba | sed -n '1781,1796p;1840,1915p;1940,1952p;2580,2592p;2658,2712p'
```

## 3. Only Pre currently carries `snapshot_defects`

**CLAIM**

Both staging payloads can carry the audit's blocking set; the Post function
gains the matching parameter and payload key.

**MEASURED TRUTH**

At `d7d2e2f`, the proposed Post change has not landed. `stage_release` declares
no `snapshot_defects` parameter and its payload at `:1911-1965` has no such
key. `stage_pre_release` declares the parameter at `:2580-2591` and writes the
key at `:2736-2738`. `stage_pre_release_from_run:2146` merely computes the list
at `:2178` and passes it at `:2205`; it does not own another payload.

The Post key inventory has moved and already includes earlier-slice additions:
S6's `STAGED_VERSION_FIELD` at `:1915` and S9's
`STAGED_ROW_DEFECTS_FIELD` at `:1948`.
The exact-key test is now `_POST_PAYLOAD_KEYS` at
`test_pre_release_lane_wiring.py:588`, with the S9 key at `:597`. The pending
Post transport and test migration remain necessary, but the old coordinates
must not be used.

**HOW TO CONFIRM**

```bash
git show d7d2e2f:backend/app/services/build_concepts_release.py | nl -ba | sed -n '1781,1796p;1911,1966p;2146,2206p;2580,2592p;2684,2740p'; git show d7d2e2f:backend/tests/test_pre_release_lane_wiring.py | nl -ba | sed -n '580,605p'
```

## 4. `snapshot_defects` is not a neutral QC transport

**CLAIM**

The audit's arbitrary `blocking` strings may be appended to
`payload["snapshot_defects"]`, which `structural_defects` already reads.

**MEASURED TRUTH**

`structural_defects:854-859` gives every entry in that key the fixed provenance
“an input snapshot could not be read.” A duplicate QID, identity mismatch,
arithmetic error, or layout error transported there would therefore be
recorded as a snapshot-read failure the run did not observe. The proposed gate
could still refuse the database write, but its receipt would be false.

S11 needs a semantically neutral, structured QC transport and must name how
that transport reaches `structural_defects`, manifests, diagnostics, state,
and publication refusal. Reusing `snapshot_defects` without changing its
reader is not a valid wiring shortcut.

**HOW TO CONFIRM**

```bash
git show d7d2e2f:backend/app/services/build_concepts_release.py | nl -ba | sed -n '794,870p'
```

## 5. The generation gate symbols and coordinates moved

**CLAIM**

S11 adds `_BLOCKING_CODES`, retains `_FATAL_CODES`, shrinks
`_FIXER_UNACCEPTABLE_CODES`, and changes the final gate at the listed
`:15706/:15723/:15742` sites.

**MEASURED TRUTH**

The policy change remains pending. `_BLOCKING_CODES` does not exist.
`_FATAL_CODES` is still the 52-code set at `generation.py:12863`.
`_FIXER_UNACCEPTABLE_CODES` remains the four-code set at `:12899-12904`:
`duplicate_title`, `duplicate_topic_concept`, `required`, and
`required_parent`. Current final-gate consumers call `_fatal_errors(report)`
at `:15698` and `:15715`; the resulting raise is at `:15734-15738`.

The changes list must update these coordinates while preserving the explicit
requirement that no existing Phase 3 decision identity or policy version moves.

**HOW TO CONFIRM**

```bash
git grep -n -E '_FATAL_CODES|_BLOCKING_CODES|_FIXER_UNACCEPTABLE_CODES|_fatal_errors\(report\)' d7d2e2f -- backend/app/services/generation.py
```

## 6. The deposit gate moved with the shared consumer

**CLAIM**

`build_concepts.py:915-918` changes the deposit gate to filter on
`_BLOCKING_CODES`.

**MEASURED TRUTH**

The deposit path currently starts its validation handling at
`build_concepts.py:902`, applies recorded Fixer accept-with-flag decisions at
`:916-926`, and refuses on the surviving fatal set at `:930-933`. It still
consumes `generation._fatal_errors`, so the shared-polarity change remains
pending at new coordinates.

**HOW TO CONFIRM**

```bash
git show d7d2e2f:backend/app/services/build_concepts.py | nl -ba | sed -n '895,938p'
```

## 7. The writer judgments moved, and there are two read-back callers

**CLAIM**

Delete the named writer ranges and route “the surviving read-back” to the
Fixer instead of raising.

**MEASURED TRUTH**

The content judgments now live at:

- `_REGULAR_TYPE_NUMBER_RE:530-533`;
- `_HUB_PREFIX_RE:534-538`;
- `_api_question_placement_active:541-543`;
- Type-host accumulation at `:637-645`;
- hub judgment at `:646-652`;
- the split-Type branch at `:666-678`; and
- the culmination classifier and threshold at `:680-696`.

The mechanical comparisons remain at `:618-635` and `:659-665`, with
`ConceptWorkbookValidationError` raised at `:698-701`.

There are two live callers of `_validate_concepts_workbook_bytes`, not one:
`append_concepts:1410` and `write_concepts_workbook:1576`. Only
`append_concepts` currently has `issues` and `fixer_decisions` receipt
channels; `write_concepts_workbook` returns bytes. S11 must name the second
caller's non-raising record transport or narrow the claim to the path it
actually repairs.

Finally, the retained comparison is not persisted-identity keyed. It strips
tags and compares normalized visible chapter/concept/topic/kind text at
`:557-564` and `:608-613`. Calling it an “identity check” conceals the already
documented same-topic/same-title collapse residue.

**HOW TO CONFIRM**

```bash
git grep -n -E '^_REGULAR_TYPE_NUMBER_RE|^_HUB_PREFIX_RE|^def _api_question_placement_active|type_hosts|split_types|startswith\("culmination"\)|culmination_title|_validate_concepts_workbook_bytes|ConceptWorkbookValidationError|def append_concepts|def write_concepts_workbook|fixer_decisions' d7d2e2f -- backend/app/bulk_import/writer.py
```

## 8. Repeated-question work moved; the defects remain

**CLAIM**

`build_concepts_release.py:951-1000` loses the threshold, becomes
case-insensitive and Unicode-aware, and produces one grouped collision issue.

**MEASURED TRUTH**

The relevant implementation moved to `:1404-1456`. The ASCII-only noise
regex remains at `:1404`; no `casefold()` is applied before key construction at
`:1414-1416`; and `len(key) < 25` still decides whether a collision counts at
`:1434`. One grouped issue per normalized collision key is already true at
`:1442-1455`. S11 must delete the content threshold and Latin-only assumption
at the measured sites without weakening that grouping behavior.

**HOW TO CONFIRM**

```bash
git show d7d2e2f:backend/app/services/build_concepts_release.py | nl -ba | sed -n '1400,1457p'
```

## 9. `_learner_analysis_count` moved and remains live

**CLAIM**

The learner-analysis counter is purged at the two listed old ranges.

**MEASURED TRUTH**

`_learner_analysis_count` remains at `:1585-1591` and still selects between
artifacts at `:1648-1655`. `stage_release:1818-1822` reaches that selection.
The Rule 1 purge is still required; only its coordinates were stale.

**HOW TO CONFIRM**

```bash
git grep -n '_learner_analysis_count\|_validated_artifact_topology' d7d2e2f -- backend/app/services/build_concepts_release.py
```

## 10. `structural_defects` moved and has no T9 QC set yet

**CLAIM**

The T9 identity set feeds `structural_defects:457-499`.

**MEASURED TRUTH**

S9 expanded the function to `:794-909`. It now also handles the assessment
payload, non-list `records`, staged-row defects, and the Pre verdict. No T9 QC
issue-code set is embedded there. The only proposed feed named by S11 is the
semantically scoped `snapshot_defects` read at `:854`, which cannot truthfully
carry arbitrary QC findings as described in correction 4.

The changes list must name the actual structured transport and preserve all
S9 branches while adding the new identity checks.

**HOW TO CONFIRM**

```bash
git show d7d2e2f:backend/app/services/build_concepts_release.py | nl -ba | sed -n '794,910p'
```

## 11. The malformed-catalog swallow moved and remains live

**CLAIM**

A catalog that will not parse becomes a named defect at the old
`:1021-1027` range.

**MEASURED TRUTH**

The same silent fallback remains in `_case_uniqueness_issues:1477-1483`:
the exception still becomes `cases = {}` with no named defect. The intended
S11 repair is still necessary at the new site.

**HOW TO CONFIRM**

```bash
git show d7d2e2f:backend/app/services/build_concepts_release.py | nl -ba | sed -n '1459,1506p'
```

## 12. The coverage ledger is still zip-local and cannot reach staging

**CLAIM**

Changing `build_concepts_release_files.py:666` makes the coverage ledger leave
the diagnostics zip and reach the staging audit.

**MEASURED TRUTH**

The only production `build_coverage_ledger` call is now inside
`build_diagnostics_zip:1079`, after that function has loaded an already-staged
payload at `:1014`. No current producer makes the ledger available to either
earlier staging function, and the S11 Changes bullet names no bridge. Moving
or returning the ledger is therefore an additional required contract change,
not a coordinate-only edit.

**HOW TO CONFIRM**

```bash
git grep -n 'build_coverage_ledger' d7d2e2f -- backend/app
```

## 13. `question_source` profile plumbing already landed

**CLAIM**

S11 changes `assessment_workbook.py:224` so `question_source` comes from the
profile.

**MEASURED TRUTH**

This work already landed in S8. Current
`assessment_workbook.py:509-510` falls back through
`assessment_profile.question_source(profile)`, and blame assigns both lines to
`76c84fb`. Remove this item from S11's pending Changes list rather than moving
its coordinate.

This does not resolve the separate profile-default question: the measured
default remains an empty string in `assessment_profile.py`. That is an S8
residue, not work created by S11's call-site list.

**HOW TO CONFIRM**

```bash
git blame d7d2e2f -L 509,510 -- backend/app/bulk_import/assessment_workbook.py; git grep -n 'question_source' d7d2e2f -- backend/app/services/assessment_profile.py
```

## Verified claims that do not need correction

- `_FATAL_CODES` still keeps its name and currently contains 52 codes.
- `_FIXER_UNACCEPTABLE_CODES` still contains exactly
  `duplicate_title`, `duplicate_topic_concept`, `required`, and
  `required_parent`.
- Both the final and deposit gates still consume `_fatal_errors`; neither uses
  a `_BLOCKING_CODES` symbol yet.
- Every writer content judgment named by S11 remains present.
- `ConceptWorkbookValidationError` still has one definition and one raise,
  with no catch or test occurrence.
- The repeated-question threshold and Latin-only normalization, the
  learner-analysis counter, the malformed-catalog swallow, and the zip-local
  coverage producer all remain live.

No tests were run for this verification. Every measurement above is a
read-only `git show`, `git grep`, `git blame`, or tree query against the exact
commit.
