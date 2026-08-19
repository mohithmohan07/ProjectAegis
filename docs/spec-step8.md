# spec-step8 — Four-output release on the SOP/MES schema (§10 step 8)

Branch `claude/step-8-four-output-schema`, HEAD **7f71b16** ("Commit the Bulk Import Format
workbook as the layout authority"; the repair rounds before it were measured at `6335fe6`
and every coordinate has been re-checked where a repair touched it). Every file:line below
was opened or executed by me on this tree. Measurements marked **[measured]** were run;
where a cluster resolution or a reviewer asserted something I could check, I checked it,
and where the code disagreed with them I say so.

**THE OUTPUT NUMBERING USED THROUGHOUT THIS SPEC IS THE OWNER'S, AND IT IS THE REVERSE OF
`docs/aegis-restructure.md:416-421`:** Output 01 = **Pre**-Learning Concept File, Output 02
= **Pre**-Learning Master File, Output 03 = **Post**-Learning Concept File, Output 04 =
**Post**-Learning Master File. "Four separate files serving four separate purposes." The
doc table is corrected by the §12 register entry D9/Q22 in §6 below. Nothing about any
artifact changes — only its number and its position in the reviewer's list. Note that the
two CONCEPT files are `{01, 03}` under both numberings and the two MASTER files are
`{02, 04}` under both, so every paired citation in this spec ("Outputs 01/03", "Outputs
02/04") is numbering-invariant and reads correctly either way; only the SINGLE citations
moved, and they were swept (see D9/Q22).

**Baseline to hold — [measured] by me, fresh isolation dir:**

```
cd backend
D=<fresh>; AEGIS_DB_URL="sqlite:///$D/step.db" AEGIS_DATA_DIR=$D/data \
  python3 -m pytest tests/ -q -p no:cacheprovider
→ 2324 passed, 6 xfailed, 7 warnings in 142.42s, exit 0
```

The map records 2314/6 at `19ed7e4`; that is stale. **A step-8 close below 2324 is
deleted coverage and must be accounted for test-by-test by name.**

---

## 1. What step 8 ships

Step 8 makes the four outputs (§4 Phase 05) one immutable release with one layout, one
identity, and one publication. It (a) lists the six Post-lane prompts in the frozen core
so an edit to them can never replay stale verdicts; (b) closes the live silent-corruption
hole on `POST /data/import` by identifying a workbook's layout from its header row and
refusing an unrecognised one — today an accepted reference workbook imports with one
sheet dropped and 342/344 question columns misaligned, with zero issues logged
(`bulk_import/reader.py:246`, `:250`); (c) mints and **persists** a per-topic and
per-concept machine identity that is script-safe, rename-stable and never re-derived,
replacing the release-hash-derived `REL<sha12>C###`
(`assessment_release_snapshot.py:137`) that re-mints every id on any content edit;
(d) migrates the schema constants, the writer, the reader and the acceptance tests to the
reference layout, derived from `app/bulk_import/assessment_workbook_template.json` as the
single positional authority so the repo does not acquire a *second* hard-coded copy of
that layout; (e) converges the two release systems onto `models.AssessmentRelease` as the
immutable row of record with A's issue ledger, diagnostics and three §4 state names kept,
and routes one hardened publication through it; and (f) reconstructs the SOP §7 QC
checklist as `release_qc.py`, inverting the polarity that today blocks a finished run on
keyword lists and character thresholds while letting a duplicate QID publish. Two live R4
silent-loss holes found during this work are closed with it: publication drops any staged
row missing a topic or concept title with no issue recorded (`_publication.py:100-106`,
**[measured]**: `structural_defects → []`, `release_state → "ready"`, one of two rows
gone), and `concept_cleanup.filter_review_violations` deletes rows whose topic is named
"Summary" or "General" on the release path (**[measured]**: 5 in, 2 out — and the Marathi
equivalent survives, so the deletion is language-asymmetric).

**Six owner rulings arrived after the second repair round and are folded in as DECISIONS,
not options.** OD1: the Build Concepts run builds **all four** outputs, so every chapter
pays the assessment-lane spend (**T15**; §7's OR1 is gone). OD2: the migration subject is
`config.BULK_IMPORT_OUTPUT`, not the committed fixture `bulk_import_database.xlsx` — the
question's premise was false (**T7.6a**; OR2 is gone). OD3: the `keywords` and
`related_concepts` columns ship as **content, not as forced blanks** (**T3.3**; OR3 is
gone) — stated precisely, because §1 previously overclaimed it and T3.3 does not deliver
what "both ship filled" implies: `keywords` ships wherever the record carries one
([measured] 47 of 53 golden records do); `related_concepts` ships **filled on Pre rows**
from the Post `machine_id`s resolved at Pre staging, and **blank on Post rows** until a
concept↔concept relations pass exists, which is a later step with its own kind and critic
(T3.3, and the test `test_post_related_concepts_is_blank_until_a_relations_pass_exists`
that fails when that changes). Neither column is ever a `_FORCED_BLANK`, and
`forced_blank_fields` (T12/M4) is the one-line lever if the reference school's importer
refuses them. OD4: the outputs are numbered
**Pre first** — 01/02 Pre, 03/04 Post (**D9/Q22**). OD5: a questionless concept is **one
tail row stopping at the concept columns** (**T16**). OD6: the Concept Files are filled to
the concept band and no further, stated explicitly for both of them and bound to the
builders that actually make them (**T17**). §7 is now empty of questions.

Step 8 is deliberately a large PR: §10 line 726 defines it as "Four-output release on the
SOP/MES schema (Q5) — **including** per-topic/per-concept ID minting and the QC-checklist
audit", and all three are in the eleven slices of §3 below. The doctrine-lens reviewer's
R14 ("one PR, six commits does not contain step 8") is **upheld against Cluster A's
sequencing**: Cluster A's six commits carried none of the three named deliverables.

---

## 2. THE DECISIONS

### T1 · Which release system survives

**Decision.** Convergence option (c): a shared release core; **`models.AssessmentRelease`
is the immutable release row of record for all four outputs**; `build_concepts_release*`
stays the staging + evidence layer and keeps the issue ledger, the `_aegis_*` audit
registry, the diagnostics archive, the two-lane sibling staging and the reviewer API;
**A's three §4 names are the only public release vocabulary**
(`build_concepts_release.py:436-438` — `:433-435` is the comment banner), B's 7-state FSM
stays the internal lifecycle and
its 3-value readiness becomes an input to the projection, not a second public truth.

**Evidence.** A has no immutable object: `stage_release` overwrites the slot —
`durable_inventory[RELEASE_KEY] = copy.deepcopy(payload)` at
`build_concepts_release.py:1488`, Pre twin `:2083`. B has `release_uid` + `version` +
`supersedes` under `UniqueConstraint("release_uid","version")` (`models.py:447`),
per-artifact `workbook_hashes`, a lease-guarded atomic publication and a durable receipt.
§7:551 ("a new immutable release version per applied round") is unexpressible on a mutable
dict slot.

**The argument against that lost.** "Absorb B into A — A owns the run and the reviewer
surface." It loses because §4:463-466's "one immutable snapshot, four projections" is not
a feature you add cheaply to a job blob: you need identity, version, supersede, freeze,
artifact hashing, a lease and a receipt, i.e. you rewrite B inside A while B's 18
collected lifecycle tests go dark.

**Three corrections to Cluster A's version of this decision, each measured.**

1. **"All four RENDERERS are B's … so Q5 for Outputs 01/03 is a ROUTING change, not a
   second writer migration" is FALSE.** [measured] I fed
   `assessment_workbook.render_concept_file` (`assessment_workbook.py:180`) a snapshot
   carrying `chapter_duration="45"` and rendered it back:

   ```
   concept_title        'Growth and reproduction as living characteristics'   ← NO (tag)
   topic_concept_labels None                                                  ← BLANK
   chapter_duration     None                                                  ← _FORCED_BLANK
   keywords 'k1, k2'   related_concepts 'REL-OTHER'   concept_source 'Balbharti'
   validate_concept_file(...) == []                                           ← and nothing notices
   ```

   The `"Name (tag)"` composition exists **only** in `bulk_import/writer.py:614` /
   `_front_bands`; both snapshot builders emit raw titles
   (`assessment_release_snapshot.py:137`, `assessment_release_service.py:114-124`), and
   both hard-code `"topic_concept_labels": ""`
   (`assessment_release_snapshot.py:199`, `assessment_release_service.py:118`) while the
   gold fills that column on **23 of 23** populated rows [measured]. Routing Outputs 01/03
   to B as-is therefore ships a workbook whose §6:522 identity pair and §6:509-512
   five-level join are blank. It is a **writer migration**, and specifically the
   "per-topic/per-concept ID minting" §10:726 names as step 8's own content. The
   portability lens wins; the map recommended routing, this spec migrates the renderer.
2. **`release_state()` cannot be handed to `_release_summary` as Cluster A's C3 listed
   it.** [measured] `release_state({"source_atoms":…,"candidates":…,"groups":…})` returns
   `diagnostic_release`, because `structural_defects`
   (`build_concepts_release.py:457-499`) looks for A's `records` key. Landed as listed,
   every healthy assessment release is labelled structurally corrupt.
3. **B is two-sheet by construction and A is three-sheet.** `assessment_release.py:103`
   `SHEET_KINDS = ("objective","descriptive")` with the message "MES never uses
   Subjective" at `:169`; `assessment_workbook.py:407` `sheet_for_kind` has two entries
   and diverts anything else to `unplaced` (`:412-420`: the lookup at `:411`, the append at
   `:414-419`, the `continue` at `:420`), which `_readiness:326-330` turns
   into `BLOCKED` — and, because that `continue` precedes `_append_group_row:384-393`,
   writes the candidate to no sheet at all (T7.5). `bi.SHEET_BY_KIND` carries three and `writer.py:1178` writes all three.
   Migrating 01/03 onto B unmodified is a capability regression for any source with
   genuinely subjective questions — Q9's language mode, §4's Output-**04** contract naming
   three sheets (the Post Master File; §4's table row still reads "02" until D9/Q22 lands),
   and the owner steer all forbid it. This is sweep item B2, ranked #1 and
   assigned to "**Step 8** — it is in the file step 8 is rewriting"; no Cluster-A commit
   carried it. It is carried here, in slice S8.

**What breaks if this is wrong.** If the core lands on A's blob, §7's per-round immutable
version has nowhere to live and step 9 keeps editing published DB rows, which
`concept_revisions.apply_instruction` already does.

**MC4 stands and matters:** the map's "(b) discards the only publication path that is
actually hardened" is FALSE. A has a transactional outbox at
`build_concepts.py:1788-1830` (shared workbook lock → `recover_pending_publication` →
`record_publication_intent` **before** `db.commit()` → atomic publish → clear intent).
A lacks artifact-hash verification and a receipt; B lacks the append-only workbook outbox.
**Keep both.** An implementer acting on the map's sentence would delete A's outbox as
redundant and lose R5's crash-safety.

### T2 · One release object, or two stores

**Decision.** **One immutable row per LANE per run — two rows, four projections — on the
existing `assessment_releases` table, NOT renamed.** A's job-blob slot survives, demoted
from "the release" to "the staging draft": mutable before freeze, a cache of the frozen
payload after it.

**Why no rename — measured, not argued.** `db.py:35-39 init_db` = `create_all` +
`_ensure_columns`, which returns early for non-SQLite (`db.py:43-45`) and emits only
`ALTER TABLE … ADD COLUMN` plus two `CREATE INDEX IF NOT EXISTS`. A table rename is not
expressible: every existing database keeps `assessment_releases` while `create_all` mints
a new empty table, orphaning every published release and its receipt. Adding COLUMNS is
expressible and free. `fly.toml:16` pins `AEGIS_DB_URL = 'sqlite:////data/aegis.db'`, so
the additive path is live.

**Why two rows.** The two lanes have separate authenticated publications (Rule G),
separate readiness, and a load-bearing ORDER that
`assessment_release_service._resolve_snapshot_concept_ids:683-741` enforces byte-exactly.
One row carrying both lanes needs a lane-keyed sub-structure inside `payload` — the shape
`build_concepts_release.py:15-25` rejected and `tests/test_pre_release_lane_wiring.py:583`
pins against.

**New columns.** `assessment_releases.lane VARCHAR(16) DEFAULT ''` and
`assessment_releases.layout_id VARCHAR(64) DEFAULT ''`, added to `db.py`'s `additions`
list in exactly the shape `("groups","group_key","VARCHAR(255) DEFAULT ''")` uses. The
run's **profile name, layout manifest sha256, and provider/model/prompt identity** go into
the already-declared, never-written `AssessmentRelease.provider_identity` JSON
(`models.py:435` — [measured] `grep -rn provider_identity app/ --include=*.py` returns
exactly one hit, the declaration), which is §3 guard 2's run-context pinning and costs no
migration. `build_dual_output` already accepts a profile and stamps
`manifest["profile"]` (`assessment_workbook.py:974-989`); the defect is the call site
(`assessment_release_service.py:356` passes none) and `create_release` never persisting it
— [measured] `grep -c profile app/services/assessment_release_service.py` = **0**.

**Known limit, recorded rather than papered over:** `_ensure_columns` returns early for
non-SQLite (`db.py:43-45`), so on any Postgres deployment neither the new columns nor
S5's unique index appear. "Adding columns is free" is a property of the current Fly
deployment, not of the code. Record it in the §12 amendment (D5 below); do not let the
`String(128)` argument in T4 pretend otherwise.

**The concrete hazard this closes** [verified in code]: publish Output 03 (the Post Concept
File), then
`force_release` (`build_concepts_release.py:2157`) re-stages — it refuses only when
`job.status == "generated"` — the payload changes, `source_release_sha256` changes, every
`machine_id` re-mints, and the already-published Output 04 can never be matched again
(`create_release` raises "staged concept snapshot and assessment release seal disagree").

### T3 · Which concept band ships, and what fills the restored columns

**T3.1 — the manifest is the single positional authority for BOTH renderers.**
`app/bulk_import/__init__.py` stops hard-coding `OBJECTIVE_FIELDS`/`SUBJECTIVE_FIELDS`/
`DESCRIPTIVE_FIELDS`, `SECTION_BANDS` and `SHEET_BY_KIND` and derives them from
`assessment_workbook_template.json` at import, keeping `LEGACY_CONCEPT_FIELDS`
(`__init__.py:61-68`) and a frozen legacy copy for reading old workbooks.
`assessment_workbook.py:46-58` already reads that manifest, so the import-time-read risk
is already taken; the alternative is sweep finding B4 — step 8 minting a *second*
hard-coded copy of the reference layout in the commit meant to converge on one.

[measured] The target band geometry cannot be expressed by today's `(label, span)` list:

| sheet | Chapter | Topic | Concept | Group | Question | unbanded |
|---|---|---|---|---|---|---|
| Objective (67) | 1-6 | 7-12 | 13-22 | 24-30 | 31-31 | col 23 (`concept_source`) + 32-67 |
| Descriptive (374) | 1-6 | 7-12 | 13-22 | 23-29 | 30-70 | 71-374 |
| Subjective (144) | 1-6 | 7-12 | 13-23 | 24-30 | 31-144 | — |

Band labels differ per sheet by trailing whitespace (`'Chapter '` vs `'Chapter  '`,
`'Concept  '` vs `'Concept '`, `'Topic'` / `'Topic '` / `'Topic  '`) and Descriptive's
Group band field ORDER differs from Objective's. `_write_headers` (`writer.py:1117`) and
`SECTION_BANDS` adopt the `{label,start,end}` dict shape; the whitespace variants are
preserved byte-exactly, never normalised.

The target band is **this repo's own `LEGACY_CONCEPT_FIELDS`** plus
`concept_question_labels` and `concept_source`. `bulk_import/__init__.py:43-48` records
why we diverged: *"parent_concept is a first-class column (team request) … The keywords
and related_concepts columns were dropped from the canonical layout (also a team
request)."* Q5 supersedes that recorded team request, and the commit must say so.

**T3.1b — NEW, and it changes the trust chain: the `sop-mes-1` registry entry derives from
the COMMITTED FORMAT WORKBOOK, not from the template JSON.** The owner has supplied the
format workbook and it is committed at
`backend/data/Testing/reference_bulk_import/bulk_import_format.xlsx` (commit `7f71b16`,
[verified] `git ls-files backend/data/Testing/reference_bulk_import/`). [measured] I opened
both and compared field-for-field:

```
sheet order         workbook ['Objective','Descriptive','Subjective']
                    manifest ['Objective','Descriptive','Subjective']   equal
row-2 field names   Objective   67 fields, list(row2) == manifest fields → True
                    Descriptive 374 fields → True
                    Subjective  144 fields → True
data rows below row 2   Objective 0   Descriptive 0   Subjective 0
row-1 band cells (non-empty)  5 per sheet
no "Doc Link" sheet; no trailing-space field names
```

So the two agree today, byte-for-byte, on all three sheets — which is exactly why the
switch is safe to make now and expensive to postpone. **Decision:
`layouts.py`'s `sop-mes-1` entry is read at import time from the committed
`bulk_import_format.xlsx`, and `assessment_workbook_template.json` becomes a derived
convenience cache regenerated from that workbook, never an independent authority.** The
reason is the one T3.1 already gives for not hard-coding a second copy, applied one level
up: the JSON is a *transcription* of a workbook the owner supplied, and a transcription
sitting in the trust chain is precisely the second-copy defect this spec forbids elsewhere
(sweep B4, T6's frozen-literal ruling, T3.1 itself). A transcription that silently drifts
from its source is the same failure shape as `SHEET_OBJECTIVE = "Objective "`.

* **If the two ever disagree, THE WORKBOOK WINS.** The mismatch is a named structural
  defect, `layout_manifest_drift`, carrying the sheet, the first divergent column index and
  both values. It is detected mechanically (tuple inequality — it judges nothing about
  content) and it is a **gate on an artifact**, so under CLAUDE.md:52-61 it may refuse the
  registry entry rather than guess. Concretely: at import of `layouts.py` the entry is
  built from the workbook and compared with the JSON; disagreement records the defect on
  every release staged in that process and is surfaced in the release audit. It never halts
  a run (Q13): the workbook's layout is used, the JSON's is not, and the run completes.
* **A regression pins the equality directly** (`test_the_committed_format_workbook_and_the_
  template_json_agree_field_for_field`), so a hand-edit to either one fails loudly in CI
  instead of shipping a mis-banded workbook.
* **Landing slice: S2**, with the registry itself.

**T3.2 — `keywords` ships filled on all four outputs.** [measured] 47 of 53 records in
`tests/golden/rne_settled_rows.json` carry non-empty `keywords`; the value already rides
the record and `render_concept_file` already emits it once the column exists (proved in
T1's render above). No new pass, no new decision kind. Pure gain.

**T3.3 — `related_concepts` ships FILLED on Pre rows, resolved at STAGING, not at
render.** Both reviewers refuted Cluster B's version of this and both were right about the
mechanism; they were wrong that it is not implementable in step 8.

* Refuted, correctly: Pre and Post are **two payloads and two snapshots**, not one.
  `build_concepts_release.py` stages `RELEASE_KEY` and `PRE_RELEASE_KEY` as separate
  slots (`:1488`, `:2083`); `transient_release_hierarchy` sets one `pre_post` per
  hierarchy (`build_concepts_release_files.py:159-163`); §4:466 pairs "the Concept and
  Master pair **for each phase**". So "the Post projection's own concept_title cell"
  does not exist inside a Pre render.
* Refuted, correctly: `_aegis_needed_for` carries `post_concept_id` = `CONCEPT-%04d`
  from `phase3/place.py:43-50`, and [measured] `grep -rn "mint_concept_ids" app/`
  returns four call sites, none of which persists the id. So there is no durable Post
  identity to join on at render time, and a title join is forbidden by T11.2.
* **The resolution that survives both:** `_stage_pre_sibling`
  (`build_concepts_release_contract.py:145-180`) runs **after** the Post release is
  staged — its own docstring says so — and both slots hang off the same `UploadJob`. So
  Pre staging resolves each `_aegis_needed_for[].post_concept_id` against the Post
  payload staged in the same run, stamps the resolved **persisted Post concept
  `machine_id`** (T4) onto the Pre record as a registered `_aegis_*` field, and the
  renderer reads that stamped field. Newline-joined (concept titles legitimately contain
  commas; `topic_concept_labels`' comma convention is recorded as a pre-existing hazard,
  not fixed here). A link that does not resolve is a **recorded review flag on the row**,
  never a blank and never a block.
* `_aegis_pre_prerequisites` does **not** go in this column: it is `{prerequisite_id,
  text}` — copied provenance prose, not a label. The comment at
  `build_concepts_release.py:98-105` naming both fields' column home as
  `related_concepts` is half right and is corrected in the same commit.
* Post rows ship `related_concepts` blank because nothing authors a concept↔concept
  relation; the test that pins this is named so it fails when that changes.
  `_placement_contract.reference_edges` / `prerequisite_topic_ids` exist on **4** of 53
  golden records [measured — Cluster B said 2], are TOPIC-level and carry opaque
  `TOPIC-000n` ids, so their home is `related_topics`, not this column, and turning them
  into concept relations is a new model verdict with its own kind and critic. Later step.
* "The gold leaves it blank" is **not** a reason to ship it blank (owner steer; Q5
  settles layout, not content). **OWNER RULING OD3, now folded in as a decision: fill
  `keywords` and `related_concepts`.** The gold leaving them blank is the reference
  school's fill practice, not a rule of the format — the committed
  `backend/data/Testing/reference_bulk_import/bulk_import_format.xlsx` carries both
  columns and no data rows at all [measured, see T3.1b], so the format itself asserts
  nothing about them. It remains a reversible decision: the `forced_blank_fields` profile
  key introduced in T12/M4 is the one-line lever if that ever changes, and it stays in the
  spec for exactly that reason. There is no open question here; §7 no longer carries one.

**T3.3b — the resolved value must be PERSISTED, or it silently empties at exactly the
moment the reviewer publishes.** This was missing and it recreated the R4 hole the step
exists to close. [measured] three facts compose into a loss:

1. `build_concepts._add_concept:598-613` persists `concept_title`,
   `concept_display_name`, `parent_concept`, `concept_details`, `keywords` and `sources`
   — and **not** `related_concepts`, **not** `digicards`, although both are real columns
   on `models.Concept` (`related_concepts` `models.py:73`, `digicards` `:72`). The merge
   branch at
   `build_concepts_release_publication.py:164-170` writes the same six and omits the same
   two.
2. The resolved Post `machine_id`s ride a registered `_aegis_*` marker, and
   `_strip_release_fields` (`build_concepts_release.py:2183-2188`) drops every key in
   `_RELEASE_AUDIT_FIELDS` (`:74`) **by construction** — so the marker cannot reach
   `_add_concept`'s `rec` at all. It is applied at `_publication.py:100-106`, before the
   loop.
3. After the upload, Output 01 (the Pre Concept File) is no longer projected from the
   payload:
   `build_release_bulk_import_workbook:78-79` returns
   `bi_writer.write_concepts_workbook(db, result_ids)` as soon as
   `summary["database_uploaded"]` is true. The renderer then reads
   `concept.related_concepts` (`writer._concept_field_value:622`) — which is `""`.

So today the column would be filled before publication and empty after it, for the same
release. **Decision: publication lifts the resolved marker into an explicit
`related_concepts` key on the record BEFORE `_strip_release_fields` runs
(`_publication.py:100-106`), and `_add_concept` and the merge branch both persist
`related_concepts` and `digicards`.** The registry stays the transport; the DB column is
the destination.

**The argument against that lost:** "record the loss plainly and leave it." It loses on
§4:463-466 — "one immutable snapshot, four projections" is untrue if Output 01 changes
shape when a reviewer clicks Upload, and R4 does not distinguish "lost" from "lost after
publication". **Residual, recorded not papered over:** `models.Concept.related_concepts`
is a `Text` column with no referential integrity, so a stored Post `machine_id` dangles if
that concept is later deleted. That is a review-flag condition for step 9's edit surface,
not a step-8 gate, and no step-8 code may re-derive the column from titles to "repair" it
(T11.2).

**T3.4 — `parent_concept`: the COLUMN goes, the FIELD stays.** `writer.py:610` sets
`parent = ""` unconditionally, so the column has always shipped blank and removing it
costs no exported content. The value is authored on 53/53 golden records [measured], is
live evidence in the family-preservation accounting, and is part of the exact-identity
match at `assessment_release_service.py:727-728`, so it stays on `models.Concept`
(`models.py:69`), in the payload records, and in the release-side identity. Delete in the
same commit the now-unreachable `parent: X` fallback branch (`writer.py:623-631`), the
`parent_column_present` parameter, and the `"parent_column"` report key consumed at
`build_concepts.py:4570`.

**T3.5 — `concept_source` leaving the Descriptive sheet: accept the layout, no owner
ruling.** This corrects the map. Every concept CATALOGUE row is written to the Objective
sheet (`writer.py:1060`, `build_concepts_release_files.py:86`,
`assessment_workbook.py:182`), and target Objective keeps `concept_source` at col 23, so
Outputs 01/03 lose nothing. The only rows without the column are Master **Descriptive
question** rows, where the value today is not multi-book tagging at all:
`build_concepts_release_files.py:210-214` sets the transient concept's `sources` to
`release["source_book"] or release["filename"]`, a chapter-wide constant repeated on every
row. The merged value (`bi.merge_sources`, `build_concepts.py:994`) rides the Objective
catalogue row.

**T3.6 — the gate that makes "silently blank" structurally impossible, with the
allow-list corrected.** `assessment_workbook._row_values:89-96` iterates template fields,
so any record key with no column vanishes with no flag. Add one mechanical gate at
release-audit time: the union of every sheet's manifest fields ∪ registered `_aegis_*`
audit fields ∪ an explicit, commented NOT-IN-WORKBOOK allow-list; anything outside is a
structural defect naming the field.

Cluster B specified the allow-list as "exactly `parent_concept`". [measured] The
snapshot's concept-row keys minus the manifest union are
`['concept_key', 'concept_machine_id', 'parent_concept', 'release_row_identity']`, and
`_bands_record` (`assessment_workbook.py:162-171`) strips only `concept_key` — so
`concept_machine_id` and `release_row_identity` are silently discarded by `_row_values`
today, and the gate as specified would report two identity fields as structural defects on
every release. **The allow-list is `{parent_concept, concept_machine_id,
release_row_identity}`, each with a written reason**, and `_bands_record` strips the
identity trio the way it strips `concept_key`. Run the gate on the **snapshot** rows, not
the raw staged `records`: [measured] every golden record carries 23 further keys with no
column and no `_aegis_` prefix (`topic`, `source_evidence`, `_phase32_*`, `_semantic_*`,
`_source_grounding_*` …), which `_annotate_records` deep-copies into the payload.

**T3.7 — NEW, and the single most important restored-field decision neither cluster
made: `topic_concept_labels`.** [measured] the gold fills it on 23/23 populated rows and
it equals that row's concept-title cell; both snapshot builders hard-code `""`
(`assessment_release_snapshot.py:199`, `assessment_release_service.py:118`); the canonical
writer computes it (`writer.py:710`). §6:509-512 makes this column the join mechanism
("joined by exact text labels"). Moving Outputs 01/03 onto the manifest renderer without
it **deletes** the Topic→Concept join. T3.6's gate cannot see it — the field has a column
and has a value (`""`). Decision: **both snapshot builders compute the roster** — the
comma-joined tagged concept-title cells of the topic's concepts, matching
`writer._front_bands:710`'s declared semantics ("the concept titles taught under this
topic") — and `release_qc` compares the aggregate against the concept rows as a **flag**,
not a block, until a comma-escaping round-trip contract exists (`bi.split_multi`
documents COMMA as the only supported separator, and concept titles contain commas).

**T3.8 — NEW: the writer's silent row-width repair becomes loud.** The spec cited this
once, as an argument for S7's atomicity, and then never fixed it. [verified]
`writer._question_to_row` ends

```
    expected = len(FIELDS_BY_KIND[kind]) + (len(concept_fields) - len(CONCEPT_FIELDS))
    if len(row) < expected:
        row += [""] * (expected - len(row))
    return row[:expected]                                       # writer.py:808
```

and `_concept_to_row` does the same at `writer.py:830-832`. A row **wider** than its sheet
loses its tail with no record; a row **narrower** gains blanks with no record. Both are
silent, both are on the export path R5 calls the database of record, and after S7 changes
`expected` on every sheet they are the exact failure mode that would make a half-migrated
file look green. This is P13's other half.

**Decision: the pad and the truncation both become a recorded mismatch.** The width
comparison itself stays mechanical — `expected` is the layout's own column count, not a
judgment about content — and its consequence follows T10-4's pattern for the surviving
read-back check: a mismatch is **one recorded, flagged Fixer decision** naming the sheet
kind, the expected width, the actual width, and the concept or question identity of the
row, and **the run completes**. Never a raise (`append_concepts` is reached mid-run with
the model budget already spent — see S7), never a silent slice. Lands in **S7**, in the
same commit that moves `expected`.

### T4 · The tag shape, the separator, and what the machine id comes off

**P-C1 stands: identity is minted once and PERSISTED; it is never re-derived.** No formula
is stable under §7:577's permitted rename/merge/split/re-tag, so §6:523's "unique and
stable forever" is a property of *storage*. Every current mint recomputes on every read.

**But Cluster C's single "verified myself" reproduction is FALSE, and the mint formula was
built on it.** Cluster C claimed `d.concept_tag("MSBSHSE","06","Science","Charateristics
of Living Organisms","Growth and Reproduction")` byte-matches the gold. [measured]:

```
concept_tag : 06MSSC_Charateristics_of_Living_Organisms_PL_Growth_and_Reproduction
gold cell   : 06MSSC_CharateristicsOfLivingOrganisms_PL_Growth_and_Reproduction
byte-identical: False
```

The gold's chapter segment is CamelCase-concatenated, reproduced across all three
workbooks (`06MSEN_SelfHelpIsTheOnlyWay_PL`, `06MSMA_ThreeDimensionalShapes_PL`). I
reconstructed the real generator exactly: `code_prefix + "_" + generation._slug(chapter,
∞) + "_PL"[+ "_" + directory._underscore_slug(topic)]` — [measured] matches on two
independent chapter/subject pairs, while `directory.topic_tag` does not. **No live code
path in `app/` reproduces the accepted corpus's tags.** Byte-exact gold tag reproduction
is therefore not achievable and is not a design target; say so in the PR body and stop
citing the gold cell as verification of any minted shape.

**Three further facts that decide the shape, each measured.**

1. **The alphabet is constrained.** `bulk_import/__init__.py:233`
   `_TITLE_TAG_RE = \s*\([A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\)\s*$`, consumed by
   `strip_title_tag`/`strip_topic_title` at every import/deposit/read-back boundary.
   `strip_title_tag("Growth (06MSSC-Chapter_PL_T01_C02)")` returns the string
   **unchanged** — a hyphen breaks the round trip. The tag must be `[A-Za-z0-9_]` with
   ≥1 underscore.
2. **The slug is not script-safe and collides across chapters.**
   `directory._underscore_slug:294-297` keeps only `[A-Za-z0-9]+` and falls back to
   `"X"`. [measured] two *different* Marathi chapters both yield
   `chapter_code_full → 06MSSC_X` and `concept_tag → 06MSSC_X_PL_X`. Balbharati — the
   reference book's own publisher — prints in Marathi. A name-derived id is therefore
   not unique, and `_next_label_index` (`assessment_release_run.py:1253-1266`) is a
   **global** prefix scan, so chapter B's questions would continue chapter A's numbering
   inside chapter A's identity family.
3. **A name-bearing tag overruns the label column.** `models.Question.question_label` is
   `String(128)` (`models.py:123`). [measured] sqlite silently ignores VARCHAR length
   (`VARCHAR(8)` stored 16 chars), so an overrun is invisible today and fatal on any
   Postgres move. The map's option (c) (`…_T01_Types_of_Angles_C02_Complete_angle`)
   produces a 130-character label on the longest real gold row.

**Decision — the shape.** The map recommended (c) *position + name*; **this spec ships
position + content address, with the name beside the tag in the cell, not inside it**,
because (2) makes a name-derived id non-unique for non-Latin sources and (3) makes it
overrun the column. §6:522 requires the *cell* to carry "name + (machine ID)", which this
satisfies exactly.

```
chapter_key  = f"{code_prefix}_{slug(chapter_title, 12) or 'X'}_{h8}"
               h8 = sha256 of (board, grade, subject, chapter_title, chapter_id)[:8], hex
topic.machine_id   = f"{chapter_key}_{lane}_T{n:02d}"        lane ∈ {"PL","PrL"}
concept.machine_id = f"{topic.machine_id}_C{m:02d}"
question_label     = f"{concept.machine_id} Q{k:02d}"        ← SPACE separator, kept
```

Worst case ≈ 43 characters — inside `String(128)` and `String(255)`. Round-trips
`strip_title_tag` (alnum + underscore, ≥1 underscore) including behind a Devanagari name
[measured: `strip_title_tag("वाढ आणि प्रजनन (06MSSC_X_PL_T01_C01)") → 'वाढ आणि प्रजनन'`].
Unique for a non-Latin source because `h8` carries the uniqueness when the slug collapses
to `X`. Stable under rename because it is persisted.

**Consequence: Cluster C's RULING 2 ("is a stale name inside a persisted id acceptable?")
disappears** — there is no name inside the id. It is not an owner ruling.

**T4-1 · Persist it.** `models.Topic.machine_id` and `models.Concept.machine_id`
(`String(255), default=""`), added to `db.py`'s `additions` list. `n`/`m` come from the
ordering key that **moves to `identity.source_order_key` in S4** (today
`writer._source_order_key`, `writer.py:314-321`), which already falls back to `id` for
legacy rows. The move is not cosmetic: leaving the key in the writer while the writer calls
`identity` is a module cycle — see T4-9(b).

**T4-2 · Grade normalisation, corrected.** [measured] `code_prefix` today yields
`6CBSC` / `06CBSC` / `VICBSC` / `' 6 CBSC'` / `KGCBSC` / `'Class VICBSC'` for one
chapter. `text_normalize.normalize_grade` already exists and maps `6`/`06`/`VI`/` 6 `/`६`
→ `06`, **but returns `''` for `KG`, `Nursery` and `Class VI`**. So Cluster C's "normalise
to two digits (mechanics)" would collapse KG and Nursery into one `00` family — CLAUDE.md:
48-50's exact failure mode — while still minting two families for `VI` vs `Class VI`.
**Decision: normalise when the parse succeeds; retain the stripped raw token when it
returns `''`; record the fallback as a review flag on the release.** Never stamp `00`
silently.

**T4-3 · Read-through-persistence, with the group-key hole closed.** The mint becomes
lookup-then-mint: a question that already carries a label keeps it; only a genuinely new
question is minted. Cluster C claimed this "removes the entire re-mint cost". It does
not: [measured] `tagging.py:43` and `build_assessments.py:218` both compute
`generation.question_label(concept, 1).rsplit(" Q", 1)[0]` and feed the result to
`assessment_grouping.group_key_for:130-132` — neither reads a persisted value.
`Group.group_key` is persisted (`models.py:105`), so groups minted before and after the
change get different key families. **Both sites must read the persisted
`Concept.machine_id`.** The separator has four dependencies, not one:
`assessment_release_run.py:1255`, `:2155`, `tagging.py:43`, `build_assessments.py:218` —
which is also why the space stays.

**T4-4 · The machine id comes off the persisted column, not the release hash.**
`assessment_release_snapshot.py:133,137` read `Concept.machine_id`. For a
staged-but-unpublished concept, mint against `(topic, per-topic source_order)` and persist
at the publication site where `source_order` is already assigned
(`build_concepts_release_publication.py:126-129`). `source_release_sha256` then no longer
participates in identity at all.

**T4-5 · `source_order` reconciled to per-topic.** `build_concepts_release_files.py:217`
sets `concept.source_order = index` — the **chapter-wide** record index — while
`build_concepts_release_publication.py:126-129` uses the **per-topic** position. Any `C##`
read off `source_order` is chapter-wide in the export and per-topic in the database.

**T4-6 · One topic identity, and stop rewriting the stored title.** **FOUR** different topic
identities are live — the enumeration in the previous round said three and omitted the one
C8 added, which is the enumeration a reader would implement from
[re-verified, each line opened]:

| # | site | identity as written today |
|---|---|---|
| 1 | `build_concepts_release_files.py:185` | `key = topic_title.casefold()` |
| 2 | `build_concepts_release_publication.py:126` | `topic_key = bi.normalize_question_text(rec["topic"])` |
| 3 | `build_concepts._find_or_create_topic:569-585` | strip-then-normalise, per-lane |
| 4 | **`bulk_import/reader.py:308-310`** | `t_key = (chapter.id, t_title)` + `filter_by(chapter_id=…, topic_title=t_title)` — **raw title, no normalisation, no lane** (T6.4) |

Cluster C's T4-6
reconciled the counter but not the key, and neither cluster carried the reader at all.
**All four call one shared
`topic_identity(title) = normalize_question_text(strip_topic_title(title))`.** And
`_find_or_create_topic` must **stop rewriting `t.topic_title` to the incoming casing**
(`build_concepts.py:576`) — that rewrite is the reproducible cause of the Master-upload
refusals, which MC6 declared unfindable. [measured, real SQLite session]:

```
_find_or_create_topic(db, ch, "Growth and Reproduction", "Post"); concept deposited
_resolve_snapshot_concept_ids(...) → {'release:abc:0001': 1}
_find_or_create_topic(db, ch, "growth and reproduction", "Post")  → SAME Topic row reused,
                                                stored title now 'growth and reproduction'
_resolve_snapshot_concept_ids(...) → UploadRefused: "concept 'SI Unit of Length' under
   topic 'Growth and Reproduction' does not have one exact published Output-01 identity"
                    ↑ verbatim from assessment_release_service.py:738 as it reads TODAY;
                      under OD4 that message becomes "Output-03" — see §5's string table
```

MC6 was right that `clean_concept_record` is idempotent and therefore not the cause
[measured: `clean(clean(x)) == clean(x)`]; it was wrong that the cause "must be sought
elsewhere" and left unfound. It is here, and S10 replaces the five-field byte-exact text
match (`assessment_release_service.py:711-733`) with `machine_id` resolution, which kills
the class.

**T4-7 · THE QUESTION-LABEL MINTER. One minter, and it reads the persisted id.** This was
the largest hole in the first draft of this spec: D1/Q14 names `Question = <ConceptID>
Q##` and no slice touched the function that actually mints it, so half the identity would
have shipped. [verified] the surviving legacy minter is:

```
generation._topic_index      generation.py:77-104    # position among ALL chapter topics
generation.question_label    generation.py:107-114   # f"{prefix}_{slug(chapter,6)}_PL_
                                                     #   T{idx:02d}_{slug(title)} Q{n:02d}"
```

with five call sites: `generation.py:331`, `:449`, `build_assessments.py:218`
(`_legacy_machine_id`), `:728` and `:1106`.

**Decision.**

* `generation.question_label(concept, n)` becomes
  `f"{identity.machine_id_for_concept(concept)} Q{n:02d}"` — the lookup-then-mint
  helper of T4-3, so a concept that already carries an id keeps it and a concept that does
  not gets one minted and **persisted** on the spot. Nothing is re-derived from a title.
  **The signature does not change**: the helper resolves its own session with
  `sqlalchemy.orm.object_session(concept)`, so the five call sites keep their arguments.
  For a **transient** concept — the never-persisted copies
  `build_concepts_release_files.py:199-214` builds — `object_session` is `None`; the helper
  then returns the id already stamped on the copy and mints nothing, because there is
  nothing to persist to and a transient row must never invent an identity a persisted row
  will later contradict.
* `generation._topic_index` is **DELETED**. Its whole docstring argument
  (`generation.py:80-100`) is that lane-scoping the numbering would make a Pre label
  collide with a Post label, because "`question_label` carries a literal `_PL_` segment
  and no lane discriminator" — and it names this rebuild as the real fix. T4's shape puts
  `PL|PrL` in the id, so the reason to keep a chapter-wide topic index is gone with it.
* `build_assessments._legacy_machine_id:215-218` is deleted; its two consumers (`:252`,
  `:264`, `:299`) read `identity.machine_id_for_concept` directly — which is T4-3's
  requirement expressed at the site that actually calls it.
* **Why this closes two consequences the spec asserted but did not deliver.** (i) S5's
  shared `_next_label_index` (`assessment_release_run.py:1253-1266`) is a
  `question_label.startswith(f"{base} Q")` scan; with one minter the `base` IS the
  concept's persisted `machine_id`, so the scan is scoped to one concept's family instead
  of colliding across chapters through a 6-character chapter slug. (ii) T5-2's
  `UploadRefused` compares against a global `Question` query; with two live minters the
  same question can carry two label shapes and the comparison sees phantom collisions and
  misses real ones. One minter is the precondition for both.
* **The legacy family is grandfathered, not migrated** (T5's R5 ruling, unchanged). A
  concept whose published questions carry the old shape keeps them; the new family has a
  different prefix, so `_next_label_index` starts at 1 in the new family and **no label is
  ever reassigned**. The release audit records the `legacy_label_family` note naming both
  prefixes so the reviewer sees why one concept has two.
* **Landing slice: S4**, with the mint. `tests/test_chapter_topic_quality.py:822-846`
  calls `g._topic_index` and `g.question_label` directly, which is why S4's promised
  re-authoring of that test is only possible in the same commit — without T4-7 there is
  nothing to re-author it against and **S4 cannot close green**.

**T4-8 · How EXISTING rows acquire a `machine_id`, and what the importer does with the
tag.** T4-1 added the columns and said nothing about the rows already in the database.

* **The backfill is `db._backfill_and_normalize` (`db.py:113-164`), in S4.** It already
  runs at every start inside `init_db` (`db.py:35-39`) immediately after
  `_ensure_columns` (`:42`), it is already declared idempotent, and its stated contract is
  "existing non-empty values are never overwritten" — exactly the property a minted-once
  identity needs. It walks `models.Topic` then `models.Concept` and fills only blanks.
* **The ordering rule, including `source_order == 0`.** `n` and `m` are 1-based positions
  in `identity.source_order_key` order (T4-9(b); the body is `writer._source_order_key`'s,
  `writer.py:314-321`, moved not rewritten), reused verbatim:
  `(source_order if source_order > 0 else 10**9, id)`. A legacy row with `source_order ==
  0` therefore sorts **after** every positioned sibling, in creation-id order —
  deterministic, total, and derived from storage rather than from content. `n` is scoped
  to `(chapter, Topic.pre_post_learning)`; `m` to the topic. `lane` comes from
  `Topic.pre_post_learning`; grade from T4-2's normalisation, including its raw-token
  fallback and its flag.
* **T9-1 B1 is stated precisely so legacy rows are not blocked by an accident of
  timing.** B1 blocks on a **duplicate** persisted `machine_id`, and on a staged row whose
  concept still has no id **after `identity.machine_id_for_concept` has been given its
  chance to mint one**. It never blocks on "the column happens to be empty": every read
  path goes through the lookup-then-mint helper, so an empty column is a mint, not a
  defect. Only genuine impossibility (the concept has no topic, or the chapter carries no
  identity tuple at all) leaves it empty, and that is a real identity corruption.
* **`reader.import_workbook` RESTORES the id it currently strips.** [verified]
  `reader.py:284-286` calls `strip_title_tag` / `strip_topic_title` and throws the tag
  away; without this the one endpoint S2 hardens is also the one that erases the identity
  S4 mints. Decision: the reader keeps the stripped tag. For a **newly created** `Topic` /
  `Concept` it writes the tag into `machine_id` when the tag round-trips
  `bi._TITLE_TAG_RE`'s alphabet (`bulk_import/__init__.py:233`) and the sheet identified
  to a layout that carries the identity pair. For an **existing** row it compares: equal →
  nothing; different → a recorded `machine_id_conflict` issue naming both, and **the
  persisted row's id wins** — §6:523's "stable forever" is a property of storage (P-C1), so
  an uploaded file may never re-key a published row. A row whose title carries no tag, or
  a tag that does not round-trip, is created with an empty `machine_id`, gets an
  `imported_without_machine_id` note, and is minted on first use. A note, never a block:
  this is an import, not an identity corruption, and refusing it would lose the file's
  content.
* **Which slice.** S2 rewrites `import_workbook` but `machine_id` does not exist until S4,
  so the **restore lands in S4** and S2 states it as out of scope. `models.Chapter` gains
  no `machine_id` in step 8 — the chapter key is a derived input to the topic id (T4), not
  a stored identity — and the chapter tag the reader strips stays stripped.

**T4-9 · NEW — `app/services/identity.py`: what it contains, WHEN each part lands, and the
one import direction that keeps it acyclic.** Two independent verifiers found that the
previous round's module was unbuildable as sequenced. Both findings reproduce.

**(a) The forward dependency (verifier V3), confirmed.** S2's Changes said
`reader.py:308-310` "gains the lane and the shared `topic_identity()` per T6.4", while T4-6
put `topic_identity()` in a NEW `app/services/identity.py` created in **S4**. S2 is slice
two. As written, S2 either duplicates the normaliser — the second-copy defect T3.1 exists
to forbid — or cannot land.

**Resolution: split the module by what it needs, not by which slice thought of it first.**
`identity.py` is created in **S2** containing exactly one thing:

```
app/services/identity.py                                   # created in S2
def topic_identity(title: str) -> str:
    return bi.normalize_question_text(bi.strip_topic_title(title))
```

It is a pure string normaliser over two functions that already exist in
`bulk_import/__init__.py`. It needs no new column, no model change and no mint, so nothing
in it forward-depends on S4. **S4 then EXTENDS the same module** with
`machine_id_for_topic`, `machine_id_for_concept` (the lookup-then-mint of T4-3), the two
shared cell composers `titled(name, machine_id)` and `topic_concept_roster(...)` (T14), and
the ordering key of (b). One module from the first slice that needs it; one definition of
the normaliser; four call sites converging on it (T4-6's table) rather than a fifth copy.
The alternative considered and rejected — moving the reader's lane fix from S2 to S4 —
loses because T6.4's lane merge is a live silent-loss defect on the same authenticated POST
endpoint S2 exists to close, and deferring half of that close by two slices leaves a
bisect window where the reader is fail-closed on headers and still merges two lanes.

**(b) The module cycle (verifier V4), confirmed real, and the direction that breaks it.**
[verified by reading the import blocks]:

```
app/bulk_import/__init__.py    imports NOTHING from app.services   (only __future__, re)
app/bulk_import/writer.py:27   from ..services import directory
app/bulk_import/reader.py:25   from ..services import directory, katex_rules
app/bulk_import/assessment_workbook.py:42-43
                               from ..services import assessment_profile, assessment_release
app/services/directory.py:19   from .. import bulk_import as bi
app/services/generation.py:31  from .. import bulk_import as bi
```

So `app.services.* → app.bulk_import` and `app.bulk_import.<module> → app.services.<module>`
both already exist and are both fine, because `bulk_import/__init__` is a leaf. The cycle
C1+C9 would have created is a **direct two-module one**: C9 has `writer.py` CALL
`identity.titled` / `identity.topic_concept_roster` (so `bulk_import.writer → services.identity`),
while T4-1/T4-8 take the mint ordering from `writer._source_order_key:314-321` (so
`services.identity → bulk_import.writer`). That is a genuine import cycle and the spec never
named a direction.

**Decision: the ordering key MOVES into `identity.py` and the writer imports it — never the
reverse.** `writer._source_order_key` becomes `identity.source_order_key`, with the body
unchanged (`(source_order if source_order > 0 else 10**9, id)`). [measured]
`grep -rn "_source_order_key" app/ tests/` returns **11** sites: the definition
(`writer.py:314`), nine uses inside `writer.py` (`:340, :349, :560, :686, :1068, :1069,
:1230, :1231, :1236`) and one external caller, `build_concepts_release_files.py:92`
(`key=bi_writer._source_order_key`). All eleven move to `identity.source_order_key` in S4;
`writer.py` keeps no alias, because an alias is a second name for one identity and that is
how the four topic identities of T4-6 happened.

The resulting graph is acyclic and stays that way:

```
app.bulk_import                (leaf — imports no app.services module)
   ↑
app.services.identity          (imports bulk_import only)
   ↑                    ↑
app.bulk_import.writer   app.services.generation / build_assessments / …
app.bulk_import.reader
app.bulk_import.assessment_workbook
```

`identity.topic_concept_roster(topic, export_scope)` takes the scope **as a parameter and
never imports `writer.ConceptExportScope`** — it reads the object it is handed. That is what
lets one roster serve both renderers without either importing the other (T14).

**A pin, because this is the kind of thing a later refactor undoes silently:**
`test_identity_module_imports_no_bulk_import_writer` asserts
`"app.bulk_import.writer" not in sys.modules` after a fresh `import app.services.identity`
in a subprocess. Mechanics, not judgment.

### T5 · The silent duplicate-label skip

**Decision — five parts, in this order.**

**T5-1 · Make the collision visible where it is created. No schema change.** Add
`duplicate_question_labels(candidates)` to `assessment_release.py` beside
`duplicate_group_keys:419-435`, carrying its docstring doctrine verbatim in spirit
("*Reusing one key for a different home is structural corruption, not a semantic concern
that a later judgment pass may accept with a flag*"), and append its findings inside
`freeze_payload:490`. A duplicate inside one release becomes a `payload_error` →
`_readiness` BLOCKED (`assessment_release_service.py:318-324`) → every download still
ships, the DB write is refused.

**T5-2 · Replace the unflagged `continue` — with a key that actually exists.**
`assessment_release_service.py:600-613` builds `existing_labels` from a **global**
`db.query(models.Question).all()` and skips silently. Cluster C's replacement keyed on
"the existing row's source identity"; that is **undecidable for the Pre lane, Outputs
01/02**:
[verified] `assessment_release.py:184-190` permits `source_atom_ids == []` under
`source_policy == "generate"`, `assessment_release_run.py:1140-1145` states the generated
Pre lane's contract as exactly that, and `assessment_release_service.py:643-644` writes
`source_qid=", ".join(source_atom_ids)` — so every generated pre-learning question stores
`""`. **The idempotency key is the release identity the insert already writes**:
`route_audit = {"release_uid": …, "version": …}` (`assessment_release_service.py:648-651`).
If the existing row's `route_audit.release_uid` equals this release's uid → genuine
idempotent re-publication → skip **and record** a `question_label_reissued` release note.
Otherwise `raise UploadRefused(f"{label}: already published under different content")`.
Never an unflagged skip; Rule G's idempotency is preserved for both lanes.

**T5-3 · Fix the counter that burns numbers.** `build_assessments.py:716-719` seeds label
counters as `sum(len(group.questions) for group in concept.groups) + 1` — a count, not a
max-scan — and `:732` / `:1113` insert with **no label check at all**. Delete one question
and the next mint reuses a burned number. Promote `_next_label_index`
(`assessment_release_run.py:1253-1266`) to a shared helper both lanes call.

**T5-4 · Only then the index, gated on a scan that cannot halt the app.** Inside
`_ensure_columns`, after the ADD COLUMN loop, scan for duplicate **non-blank** labels. On
a clean scan:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_questions_question_label
  ON questions(question_label) WHERE question_label <> ''
```

[measured, sqlite 3.45.1] two `question_label = ''` rows fail a plain unique index
(`IntegrityError`) and are accepted by the partial one. Blank labels are reachable —
`reader.py:369-370` `has_question = bool(label or qd.get("question"))` creates a
`Question` with the `""` default. **Cluster C's gate contradicted its own test** ("do not
create the index if … ≥2 blanks exist" vs `test_blank_labels_do_not_block_the_partial_
index`): drop the blank clause; scan only for duplicate non-blank labels. On a dirty scan,
record the finding durably and **continue** — `init_db()` runs inside the FastAPI lifespan
(`app/main.py:42`), so an exception takes the product down for the very reviewer who must
repair the duplicates, and `_backfill_and_normalize` never runs either. CLAUDE.md:52-61
permits a gate that refuses a broken **artifact**; the artifact is the release, and
T5-1/T5-2 already refuse it. Refusing to start is not that gate. Never wrap a bare create
in `try/except` — that hides the duplicates.

**T5-5 · Repair is a reviewer path, not a migration script.** Recorded duplicates surface
as `question_label_duplicate` release-audit issues naming both rows; resolution goes
through the §7 surface (step 9). Until then the index stays absent on that database and
T5-1/T5-2 carry the guarantee.

**The argument that lost.** "Just add `UniqueConstraint` to `models.py:123`." [verified]
`Base.metadata.create_all` creates missing *tables* only, so every existing database keeps
the duplicate-tolerant schema while the suite asserts the invariant holds. A green suite
proving nothing about production is strictly worse than no constraint.

**Published labels are grandfathered.** R5 ("a `question_label`, once uploaded, is never
reassigned") settles this; a re-mint would suspend R5 for a migration. The corpus carries
two label conventions and the release audit reports a `legacy_label_family` note on old
chapters. This is doctrine-derivable and is **not** an owner ruling.

### T6 · The reader silently corrupts the Q5 schema of record

**Decision — a layout REGISTRY, identification by exact header signature, reading BY NAME,
failing closed on an unrecognised header. This lands EARLY (slice S2): it is a live R4
silent-corruption hole on an authenticated POST endpoint.**

[measured] `SHEET_OBJECTIVE = "Objective "` (`bulk_import/__init__.py:19`, trailing space)
vs the gold's `'Objective'`, and `reader.py:246` `if sheet_name not in wb.sheetnames:
continue` with **no `_flag()`** — the Objective sheet vanishes with no issue. Subjective
and Descriptive match and ARE read, mis-banded: Descriptive group slice `(21,30)`,
`q_start=30`, **342 of 344** question-band positions mismatch; Subjective **63 of 63**.
Value-level, first populated gold Descriptive row: stored `question_label ← 'Very Short
Answer Questions'` where the truth is `'06MSSC_Measur_PL_T02_LimitationsOfHandSpanM Q01'`.
`reader.py:249-250` reads row 2 **only** to call `_concept_fields`; it never validates it.
`reader.py:258` takes question-band NAMES from canonical `FIELDS_BY_KIND` while `:364`
takes VALUES from the detected slice — names and values decouple by construction.

**The scalar counts are all wrong, each by 2.** [measured] between `question_label`
inclusive and the first `answer_type_*`: reference Objective **12**, Subjective **13**,
Descriptive **14**; `reader.py:85/96/108` hard-code **10 / 11 / 12** (which are exactly
the canonical counts, so the reader is self-consistent only for canonical). And the
reference Subjective sheet carries **20** answer blocks against the reader's
`range(10)` [measured], so blocks 11-20 are unreadable by construction.

**What lands.**

* New `app/bulk_import/layouts.py`. Each entry declares `id`, `sheet_name_by_kind`,
  `fields_by_kind` (the exact ordered row-2 names), `bands_by_kind` as `{label,start,end}`,
  and `column[(kind, logical_name)] → index` where the logical name is **band-qualified**
  so canonical Descriptive's duplicate `question_label` resolves as
  `group.question_label` (idx 27) vs `question.question_label` (idx 30) explicitly instead
  of by scanning. [measured] the target has **zero** duplicate field names on all three
  sheets, which is what makes name-addressing possible for the first time.
* Registered entries. `sop-mes-1` is **imported at run time** from
  `assessment_workbook_template.json` — the manifest is the single positional authority
  for the target (T3.1) and step 8 must not mint a second hard-coded copy of it (sweep
  B4). The three `canonical-*` entries are the opposite: `canonical-current`,
  `canonical-no-question-text` (the 64-column Objective variant, which really does reach
  the reader) and `canonical-legacy-concept-band` are **FROZEN LITERAL transcriptions
  committed inside `layouts.py`**, including the 10/11/12 scalar counts and the
  `range(10)` answer-block count, each with a comment saying it is frozen and why.
* **This is not a style preference; it is the difference between S2 working and S2
  silently deleting itself.** S2 lands `canonical-current` while `FIELDS_BY_KIND` still
  holds the canonical layout, and S7 then redefines `OBJECTIVE_FIELDS` /
  `SUBJECTIVE_FIELDS` / `DESCRIPTIVE_FIELDS` / `FIELDS_BY_KIND` to the target. Registered
  as a live reference, `canonical-current` would silently BECOME the reference layout the
  instant S7 lands: the canonical layout would stop being registered at all, every older
  workbook would fail identification, and Q16's "remain readable AND appendable" would
  turn into a 422 on every one of them — with the S2 regression still green, because a
  fixture built from `writer._new_workbook` / `_write_headers` moves with the writer. So:
  **S2 derives the three canonical entries once and writes the result into the file as
  literals**, and every canonical-layout fixture in the suite is built from the frozen
  registry entry or from a committed `.xlsx`, **never** from the live writer. S7 changes
  `FIELDS_BY_KIND` and must not touch `layouts.py`'s canonical entries.
* `identify_sheet(sheet_name, header_names) → (layout_id, kind) | None` by exact tuple
  equality on stripped names. No prefix matching, no fuzzy matching, no scanning.
* `reader.py`: delete `_concept_fields` (:33-50), `_concept_len` (:53), `_group_slice`
  (:57); rewrite `_parse_answers` (:79-134) to read every field **by name** and to derive
  the block COUNT by counting matching names, losing `q_start` and every literal;
  `import_workbook` collects row 2 of every sheet and either proceeds on one matched
  layout or raises `WorkbookLayoutError` naming the sheet, its column count, the first
  divergent index with both values, and the closest registered layout — before any
  `db.add`, all-or-nothing (it already ends in a single `db.commit()`).
  `api/data.py` maps it to **422** beside the existing `_lossless_xlsx_or_422` pattern.
* `writer.scan_workbook` (`writer.py:244-251`) identifies through the same registry; its
  identical unflagged `continue` becomes a recorded mismatch. `_sheet_concept_fields`
  (`writer.py:56`) is deleted with its reader twin.

**T6.2 — NEW, found by the portability lens and named by no cluster: the reader silently
MERGES chapters.** `reader.py:288-304` keys chapters solely on
`directory.derive_chapter_meta(...)["chapter_code"]`, reuses on a hit and never compares
`chapter_title`, with no `_flag()`. [measured] the code truncates at 19 characters:

```
'The Rise of Nationalism in Europe' → 10CBSS_TheRiseOfNat
'The Rise of Nationalism in Asia'   → 10CBSS_TheRiseOfNat   COLLIDE
'Understanding Fractions'           → 06MSMA_Understandin
'Understanding Decimals'            → 06MSMA_Understandin   COLLIDE
```

The gold's own titles do not collide (`06MSSC_Charateristi` vs `06MSSC_Measurement`) —
which is exactly why a reference-anchored suite cannot see it. **The reader's chapter
identity gains the full title; a code collision with a different title is a recorded issue
and the row is not merged.** The gold sheets are multi-chapter (Descriptive carries up to
**3** chapters [measured]), so the regressions must assert the CHAPTER SET, not one cell.

**T6.3 — the reader's `appears_in` default does NOT come from the profile.** Cluster D's
D6.2f would take it from the active profile. [measured] `reader.py:506` defaults to
`"Pre-test, Post-test, Worksheet, Test"` (= `APPEARS_IN_ALL`) while
`assessment_profile.py:22` holds `"Pre/Post-Worksheet/Test"`, and
`bi._APPEARS_IN_LEGACY` exists precisely to map the latter to the former. Sourcing the
READER's default from the profile stamps one school's wire value onto every other
school's imported rows, silently, on import — the steer's harm, not its cure. **The
profile default belongs on the WRITER's emission.** The reader keeps the value its own
layout implies and routes it through `normalize_appears_in`.

**T6.4 — NEW, and the third silent merge in the same function: the reader has no LANE in
its topic identity.** [verified] `reader.py:308` keys topics `t_key = (chapter.id,
t_title)` and the DB fallback at `:309-310` is
`db.query(models.Topic).filter_by(chapter_id=chapter.id, topic_title=t_title).first()`.
Neither carries `pre_post_learning`, which the reader only ever writes **on create**
(`:317`). So a Pre topic and a Post topic that share a title merge into one row, the row
keeps whichever lane happened to be created first, and every concept from the other lane
is re-parented under it — with no `_flag()`. It is the same defect shape as T6.2's chapter
merge and T11.3's publication merge: an identity keyed on text, one dimension short.

Two facts make this reachable rather than theoretical. Q9/§4's language mode puts the
same structural title ("Culmination") under both lanes; and step 7's Pre lane authors
topics from the pre-map with no rule that their titles differ from the Post outline's —
nor should there be one, since that would be a judgment about what the source means.

**Decision: the reader's topic identity becomes `(chapter.id, topic_identity(t_title),
lane)`**, where `topic_identity` is T4-6's single shared normaliser and `lane` is the
row's `pre_post_learning` (defaulting as it does today). The DB fallback filters on
`chapter_id` **and** `pre_post_learning`, then matches in Python on
`topic_identity(t.topic_title)` — deliberately not on a byte-exact `topic_title`, because
T4-6 measured that exact-match-plus-lenient-write is precisely how the `UploadRefused`
class was created. A pre-existing row that matches the title but not the lane is **not**
reused: it becomes a recorded `topic_lane_conflict` issue and a second topic row. And, per
T4-6, the reader **never rewrites `t.topic_title`** to the incoming casing.

Landing slice: **S2**, which rewrites `import_workbook` anyway. The machine-id restore
(T4-8) rides the same key in S4.

**D6.6's prerequisite is real and must land in the same commit.** [verified]
`tests/test_sources.py:44-48` and `tests/test_question_text.py:122-126` write
`["chapter_title"]` as the entire header row on the Subjective and Descriptive sheets. A
fail-closed gate breaks them at `tests/conftest.py:31`, taking the whole suite down. They
are re-authored to write the real header on all three sheets. **Do not add a
"sheet-is-short-enough-to-be-a-stub" allowance** — that is a numeric threshold, and it is
unnecessary: a real header-only sheet carries the FULL header
(`assessment_workbook._new_workbook`), so it matches the registry with zero data rows.

**Why fail closed rather than route to The Fixer.** `import_workbook` is not mid-run: it
is a user-initiated POST (`api/data.py:35`) and a test fixture. Q13's carve-out protects a
RUN reaching a complete release; there is no run here, no spend in flight, and no learner
work to lose. Refusing an upload loses nothing and is instantly actionable; reading it
wrong destroys 342 of 344 identities per row. **This asymmetry does not extend to the
writer** — see T7's ruling on `migrate_workbook_layout`.

### T7 · Where the publication-hardening invariant lands

**Decision — B's mechanism, one user-facing act, A's outbox kept, and three additional
holes closed.**

One `release_publication` core that, before any write: (i) re-reads each artifact from
disk and re-hashes against `workbook_hashes` (`assessment_release_service.py:377-386`,
again at `:513-525`); (ii) verifies `concept_snapshot_sha256` has not drifted; (iii)
refuses on the closed blocking set (T9); (iv) writes ONE durable receipt keyed
`uid:vN:master_sha` covering all four projections; (v) exposes through the exclusive
staging-dir lease + `os.replace` + manifest-written-LAST with
`recover_incomplete_publications`. Ordering per lane, forced by
`_resolve_snapshot_concept_ids`: verify hashes → concept upsert (A's transaction) →
append-only workbook outbox (A, `build_concepts.py:1788-1830`, untouched) → Master upload
(B) → one receipt.

**T7.1 — the silent per-row drop, and it is the R4 hole this step exists to close.**
[measured] `build_concepts_release_publication.py:100-106` filters `records` to rows with
both a topic and a concept title and discards the rest with no flag, no issue, no name:

```
payload = 2 records, one with topic ''
structural_defects → []        release_state → "ready"
publication keeps ['pH Scale'], drops ['Neutralisation in Daily Life']
```

A release that will lose a concept is publicly labelled READY. **Every skipped row becomes
a named `staged_row_unusable` release issue AND a structural defect**, so the state is
truthful and the DB write is refused. This simultaneously falsifies T1's claim that the
three §4 names are a truthful public vocabulary and T7's claim that (i)-(v) is the
complete refusal set; both are corrected here.

**T7.2 — `clean_concept_record` removal must cover TWO sites, not one.** Cluster A
specified removing `build_concepts_release_publication.py:125` and asserted its own test
`test_publication_never_rewrites_a_reviewer_edited_title` would pass. [measured] it would
fail on any first publication: `build_concepts._add_concept` calls the same cleaner
unconditionally at `build_concepts.py:603`, and publication reaches it at
`_publication.py:143-148`. Removing `:125` changes the CREATE path by nothing; it changes
only the MERGE path. **Both sites are covered**: `_add_concept` gains an explicit
`clean=` switch that the publication path sets False. The cleaner is measurably a
rewriter, not a normaliser: `"pH and its meaning" → "pH and Its Meaning"`;
`"…runs 0-14 (see Fig. 1.2). Achieving Mastery…" → "…runs 0-14. Achieving Mastery…"`;
`"Ozymandias: 'Look on my Works, ye Mighty, and despair!'" → "…on My Works, Ye Mighty, and
Despair!"`; `"Meaning of the refrain in The Elevator" → "…in the Elevator"` — and a
measured NO-OP on Devanagari, so the same chapter in two mediums yields different label
text. §6:537 ("nothing silently fixes") and §7:546-547 ("applies it verbatim and records
it") both forbid it, and step 9's central guarantee is untrue on arrival if it stays. If
the cleaner would have changed anything, record a review flag on the row — and note the
flag rides the release payload only (`concept_cleanup.py:193-197` records this boundary
itself, and `models.Concept` has no flag column), so the diff is preserved on the release,
not on the DB row.

**T7.3 — `filter_review_violations` must leave the release path.** [measured, 5 records
in]: it deletes both concepts filed under topic `'Summary'` and the one under `'General'`
(`concept_cleanup.py:31-35` `_FORBIDDEN_TOPIC_NAMES`, deleted at `:253-256`), while the
Marathi `'सारांश'` survives — 5 in, **2 out**, with only a `progress.log` line as the
record. It runs at `release_refiner.py:271` and `phase3/assemble.py:900`, i.e. on the
release boundary this step redesigns, and its own docstring concedes `_PEDAGOGY_TOPIC_RE`
is "Still Rule 1 and still here". "Summary" is a real teaching topic in a poem or prose
chapter. **Step 8 converts it from a deletion to a flag**: the rows ship with a
`review_topic_name` flag naming what was suspected. Named by neither the map nor any
cluster; found by the portability lens; upheld.

**T7.4 — Rule-E, narrower than the map says and wider than Cluster A's version.** MC5 is
correct: [measured] `transient_release_hierarchy` has exactly two call sites
(`build_concepts_release_files.py:81`, `assessment_release_snapshot.py:115`);
`build_release_workbook:308`, `release_payload_bytes:503` and `build_diagnostics_zip:597`
do not call it, so the review workbook, the release JSON and the diagnostics archive all
still download. The map's "no download of any kind, for any of the four outputs" is FALSE.
What is lost is the bulk-import export and Outputs 02/04. But Cluster A scoped the fix to
the raise at `:158` only; there are **eight** raises inside that function (`:128, :133,
:136, :141, :145, :149, :153, :158`) and `:148`/`:150-155` fire on a SINGLE malformed row
— the exact rows publication silently drops in T7.1. One defect, two opposite behaviours.
**All eight become recorded defects**: the function returns `(chapter, concepts, records,
defects)`, a malformed row is skipped into `defects` naming its position and what was
missing, and the artifact is built from what survives with an issues note row. See T8.

**T7.5 — `unplaced` is NEVER a warning, because an `unplaced` candidate reaches no sheet
at all — AND the previous round's fix for that was itself an R4 hole. Rewritten twice; this
version is the one both verifiers converge on.** The first draft cited one producer, named
it wrongly, and split the two the wrong way round. The second draft named both producers
correctly and then routed them through a function that structurally cannot see them. Both
errors are corrected here, from re-measurement.

[verified] `render_master_file` has **three CANDIDATE-level** branches that cost a candidate
its data row — two that produce `unplaced` and one that raises:

```
assessment_workbook.py:357-368   reason "unresolved home concept/group placement"
                                 → appends to `unplaced`, continue at :368
assessment_workbook.py:371-375   raise WorkbookRenderError — candidate's concept_key does
                                 not match its group's home  (no workbook at all)
assessment_workbook.py:412-420   reason f"sheet kind {kind!r} has no MES data sheet"
                                 → appends to `unplaced`, continue at :420
```

`_append_group_row` is defined at `:384-393` and is the **only** thing that writes a data
row. All three end the candidate's journey before reaching it. **Such a candidate is
therefore written to no sheet at all — it exists in none of the four outputs.**

**Those three are not the whole surface, and B4 below is the correction:** the same function
carries **six more `WorkbookRenderError` raises** on the group→concept edge (`:315`,
`:317-318`, `:324-326`, `:331-333`, `:336-337`, `:343-345`) and calls `_question_record`,
which carries seven more. Every one of them costs not a candidate but *every row on every
sheet*. The rule below governs all of them.

That single fact decides the ruling. §4:470-474's "flags never block" governs *semantic
doubt on a row that ships*. A row that does not ship is not doubt; it is R4 exactly-once
loss, and T9's principle names it exactly — *a thing that must be addressable cannot be
addressed*. Downgrading the unresolved-home branch to `RELEASED_WITH_WARNINGS` would let
the authenticated DB upload proceed while a learner's question exists in none of the four
outputs. That is the precise harm CLAUDE.md:33 calls never recoverable.

**THE PREVIOUS ROUND'S REPAIR OPENED A WIDER R4 HOLE THAN IT CLOSED, both independent
verifiers found it, and I reproduced every measurement. It is corrected here, and the
correction is the most important edit in this round.**

What the previous round wrote: both producers "become named structural defects so the DB
write is refused for the same rows through one vocabulary", and S6 **removes** `_readiness`'s
read of `manifest["issues"]["unplaced"]` at `assessment_release_service.py:326-330`. Four
measurements together make that a silent-loss path:

```
$ grep -rn 'get("unplaced")\|\["unplaced"\]' app/ --include=*.py
app/services/assessment_release_service.py:326    ← the ONLY consumer in the whole tree

assessment_release_service.py:537   if readiness == BLOCKED: raise UploadRefused(...)
                                    ← the ONLY thing refusing the assessment-lane DB write

build_concepts_release.structural_defects:457-500
    reads payload["refused"] (:485), payload["snapshot_defects"] (:489),
    payload["records"] (:494)  — and NOTHING else.  A pure function of the STAGED payload.

`unplaced` is produced inside assessment_workbook.render_master_file, reachable only
through build_dual_output from assessment_release_service.py:356 — i.e. at PROJECTION
time, inside publish_release, AFTER the payload is frozen.
```

`structural_defects` therefore **cannot see `unplaced`, ever** — which is exactly what D8.5
already proves in this same spec, and which the previous round's C4 repair did not read.
Net effect if built as written: after S6 deletes `:326-330`, an `unresolved_question_home`
candidate is refused by **nothing**, and a learner question that appears in none of the four
outputs publishes to the database. That is the precise hole C4 existed to close,
reintroduced one level up. An R4 hole opened by a repair is worse than the one it closed.

**Decision — ONE rule, stated once, and the five other sections in this spec that touch it
are reconciled to this wording verbatim.**

> **A candidate the renderer cannot write to a data row is decided at STAGING, recorded
> once on the release, refused at the publication act by machinery that already exists, and
> merely REPORTED by the renderer.**

Five parts, each measured.

1. **The verdict is a pure function of the frozen snapshot, so it does not need the
   renderer.** New `assessment_release.unresolved_question_homes(snapshot, profile) ->
   list[dict]`, beside `duplicate_group_keys:419-435` and T5-1's
   `duplicate_question_labels`. It reads the snapshot's `candidates`, its `groups`, the
   `concept_key` set of `snapshot["topics"][*]["concepts"]`, and the profile's allowed sheet
   kinds — the identical inputs `render_master_file:306-425` uses, plus the one literal
   tuple B3 introduces. Its named codes:
   * `unresolved_question_home` — `concept_key` not in the concept set, or `group_key` not
     in `groups`, or an empty `question_label` (`assessment_workbook.py:357-368`).
   * `group_home_disagreement` — the candidate's `concept_key` disagrees with its group's
     `concept_key` (`:371-375`, today a **raise**).
   * `sheet_kind_not_renderable` — a `sheet_kind` the profile allows that **this step's
     renderer cannot write**, carrying the kind, the profile name and the renderable set,
     and **not** a reason string with a school's name in it (T12/M2). This code replaces
     the `sheet_kind_not_emittable` earlier drafts named; that one could never fire, and
     B3 below is the whole argument.
   * the **group→concept-edge** conditions `render_master_file` raises on today, all
     de-raised here. Three of the six already have a staging twin and need no code; three
     become `group_concept_home_unknown`, `group_home_unnamed` and
     `group_visible_name_mismatch`, and `_question_record`'s four count caps become
     `render_shape_overflow`. B4 below names every raise by line.
   It counts identities and compares key sets against literal tuples. It judges nothing
   about content, so it is a gate under CLAUDE.md:52-61, not a judgment under Rule 1.

   **B3 · `sheet_kind_not_emittable` is deleted, and this is ONE decision with the profile
   key.** Two measurements kill it, and they close it from both sides:
   * **A kind the profile ALLOWS always has a data sheet, so the branch is the empty set.**
     Under T12/M6 the allowed set becomes profile-derived, and the layout is `sop-mes-1`,
     whose Subjective sheet carries a full **144-field** header — [measured] against the
     committed `bulk_import_format.xlsx`, `list(row2)` on `Subjective` is 144 names and
     equals the manifest's (T3.1b). "Allowed by the profile, no data sheet in the layout"
     has no member.
   * **A kind the profile DISALLOWS never gets that far.** [verified]
     `freeze_payload:502-503` runs `validate_candidate` over every candidate, and
     `validate_candidate:191-195` appends `sheet_kind must be one of …` for any kind
     outside the allowed set → `frozen["errors"]` → `diagnostics["payload_errors"]` →
     `_readiness:318-324` `BLOCKED` → `assessment_release_service.py:537` refuses. That is
     already this section's transport. Nothing new is needed and nothing new may be added.

   **What replaces it is the defect T12/M2's own scope limit implies:
   `sheet_kind_not_renderable` — a kind the profile allows that THIS STEP cannot render.**
   [verified] the renderable set is a property of the CODE, not of the profile and not of
   the layout: `assessment_workbook._question_record:199-283` has exactly two branches,
   `if sheet == "Objective"` (`:237`) and `else:  # Descriptive` (`:248`), so a Subjective
   candidate falls into the Descriptive branch and **raises** at `:216-219`
   ("descriptive math_keyboard must be exactly Yes or No") — costing all four workbooks,
   which is the R3/§4 breach T12/M2 already forbids. So step 8 names the set:

   ```
   app/bulk_import/assessment_workbook.py
   RENDERABLE_SHEET_KINDS = ("objective", "descriptive")
   # What _question_record can write TODAY, not what any school allows and not what the
   # layout carries. Widened by the Output-04-lane step that teaches _question_record the
   # 144-column Subjective answer blocks (T12/M2's scope limit).
   ```

   `unresolved_question_homes` reports `sheet_kind_not_renderable` for any candidate whose
   kind is in the profile's allowed set and not in `RENDERABLE_SHEET_KINDS`. It is two
   literal tuples compared; it reads no content. `sheet_for_kind:407` is then derived from
   `RENDERABLE_SHEET_KINDS × the layout's sheet-name map` — **not** from the profile — its
   `sheet is None` branch (`:412-420`) becomes unreachable because every unrenderable kind
   was named at staging, and its `:413` comment and `:417` reason string stop naming a
   school.
2. **It is called at STAGING, once per lane, and it lands in the one durable place freeze
   findings already land.** [verified] `assessment_release_service.create_release:250-310`
   calls `rel.freeze_payload(payload)` at `:260`, builds the snapshot at `:277-287`, and
   writes `diagnostics={"payload_errors": frozen["errors"]}` at `:301`. The call goes
   **between the snapshot and the diagnostics dict** — one intra-function reordering,
   because the check needs the concept universe and `freeze_payload` sees only the payload
   (the `snapshot_from_chapter` branch has no `concept_snapshot` key at all, so
   `freeze_payload` could not reach the concepts even in principle). Its findings **append
   to `frozen["errors"]`**.
3. **The refusal then costs no new wiring, because T5-1 already proved this path.**
   [verified] `_readiness:318-324` returns `BLOCKED` on
   `(release.diagnostics or {}).get("payload_errors")`, and `:537` refuses the upload on
   `readiness == BLOCKED`. T5-1's duplicate `question_label` takes exactly this route
   (`freeze_payload` → `payload_errors` → BLOCKED → refused). The three home/sheet codes
   join it. **One vocabulary, one transport, one refusal** — which is what "one rule"
   was supposed to mean and did not.
4. **The renderer READS the staged verdict instead of discovering it, and it stops raising
   ENTIRELY.** `render_master_file` still skips the candidate and still records it in
   `issues["unplaced"]` — that is the EVIDENCE channel and it must stay (see below) — but it
   no longer *decides* anything. A raise inside `render_master_file`, called unguarded at
   `assessment_release_service.py:356`, means no workbook is written for any of the four
   outputs: an R4 breach traded for an R3/§4 breach (T12/M2's existing ruling, unchanged).

   **B4 · An earlier draft de-raised only `:371-375` and left the rest raising. There are
   SEVEN raises in this function, not two — [measured] by walking its AST, so the count is
   not a grep artefact — and the verifier that flagged this said "five more" and undercounted
   by one. Every one is named here by line, and every one is de-raised:**

   | line | condition today | where the same verdict already lives, or lands |
   |---|---|---|
   | `:315` | `group_key` must not be blank | **already refused at staging** — `validate_group:386-387` `_missing` over `_GROUP_REQUIRED:136-139`, which lists `group_key` |
   | `:317-318` | duplicate `group_key` | **already refused at staging** — `duplicate_group_keys:419-435`, wired into `freeze_payload:510-511` |
   | `:324-326` | group has unknown concept home | NEW, code `group_concept_home_unknown` — `group["concept_key"]` not in the snapshot's concept set |
   | `:331-333` | group's home concept has no `concept_display_name` | NEW, code `group_home_unnamed` |
   | `:336-337` | `group_type` not in `rel.GROUP_TYPES` | **already refused at staging** — `validate_group:388-391` |
   | `:343-345` | `group_name`/`group_display_name` must both equal `f"{concept} — {tier}"` | NEW, code `group_visible_name_mismatch` |
   | `:373` | candidate's `concept_key` ≠ its group's home | code `group_home_disagreement`, item 1 above |

   Three of the six already have a staging twin, so de-raising them costs nothing and adds
   no code — the release is ALREADY `BLOCKED` before the renderer runs, and the raise is a
   second copy of a decision that has been made. The three new codes join
   `unresolved_question_homes` and use its four inputs: they compare a `concept_key` against
   the snapshot's concept set, test one string for emptiness, and compare one composed string
   against two stored ones. Mechanics, not meaning — the same gate class as
   `duplicate_group_keys`, whose docstring at `assessment_release.py:420-425` already writes
   the doctrine for exactly this shape.

   **`_question_record:199-283` carries seven more, reachable from here through
   `_full_record:404`, and they are named rather than left implied.** [verified] `:207`
   (`question_duration` positive), `:213` (objective `math_keyboard` blank) and `:217`
   (descriptive `math_keyboard` Yes/No) each already have a staging twin in
   `validate_candidate` — `:236-238`, `:263-266`, `:293` respectively — so they de-raise for
   free, exactly like `:315`/`:317`/`:336`. The remaining four — `:239`
   (`MAX_OBJECTIVE_OPTIONS`), `:252` (`MAX_DESCRIPTIVE_ANSWERS`), `:265` (`MAX_SUBQUESTIONS`)
   and `:275` (`MAX_SUBQUESTION_KEYWORDS`) — have **no** staging twin. They compare a list
   length against a layout constant (`assessment_workbook.py:64-67`), which is the layout's
   own column count and not a threshold deciding meaning, so they join the staged check as
   `render_shape_overflow` carrying the candidate identity, the cap and the actual count.
   **After this, `render_master_file` and everything it calls raise nothing.** That is what
   makes the Rule-E guarantee total for the Master lane rather than a sample, and it is
   pinned in **S8** — the slice that owns the function — by
   `test_the_master_renderer_contains_no_raise`: walk the AST of `render_master_file` and
   `_question_record` and assert zero `ast.Raise`. The cheapest possible guard against the
   fifteenth raise somebody adds next year, and the Master-lane twin of S9's
   `test_no_new_raise_inside_transient_release_hierarchy`, which does the same job for the
   concept lane's shared builder.
5. **`_readiness:326-330` is deleted in S8, NOT in S6 — and only in the same commit that
   lands (1), (2) and (4).** This is the sequencing the previous round got wrong. S6 keeps
   the `unplaced` read verbatim; it is the only refusal that exists until the staged verdict
   does. S8 lands the staged check and removes the read in one commit, so there is no bisect
   point at which an unplaced candidate is refused by nothing. The reviewer's experience
   improves in the same commit: instead of an unexplained `BLOCKED`, they get a named defect
   carrying the candidate's `question_label`.

**The evidence still ships, which is what keeps R4 whole — and "no consumer" is stated
precisely here, because S8's pin and this item read as contradicting each other and do not.**
[measured] `grep -rn 'unplaced' app/ --include=*.py` returns exactly one site that READS
`manifest["issues"]["unplaced"]`: `assessment_release_service.py:326-327`, inside
`_readiness`. Everything else is either a producer (`assessment_workbook.py:351`, `:362`,
`:414`, `:445`) or an unrelated `unplaced_pending_certification` identifier in the coverage
lane. So after S8 deletes `:326-330`:

* **the key is still WRITTEN, and it is still durable.** `publish_release` puts the whole
  issues manifest on disk as `manifest.json` in the version directory
  (`assessment_release_service.py:35`, written at `:387-389`, read back at `:453` and
  `:511`), so every unplaced candidate survives with its `candidate_id`, `question_label`,
  reason and flags.
* **what is gone is the DECIDER, not the record.** No code in `app/` reads `unplaced` to
  compute readiness, state or a refusal. That is the whole content of S8's pin, and its
  name is corrected accordingly: `test_no_code_path_decides_on_unplaced_and_the_refusal_
  survives` — assert that `unplaced` has no reader outside `assessment_workbook`'s own
  producers, AND that the same candidate is still refused, in one test so neither half can
  land alone.
* **and the reviewer-facing surface is the NAMED defect, not `unplaced`.** [verified]
  `_release_summary` (`api/build_assessments.py:305-318`) exposes `state`, `readiness`,
  hashes and publication flags — and no issues at all — so `unplaced` never reached a human
  through the assessment API even before this change. What reaches one is
  `unresolved_question_homes`' finding: the release workbook's Issues sheet
  (`build_concepts_release_files.py:398-409`: Severity / Code / Phase / Unit ID / Topic /
  QIDs / BLKs / Message / Full Details), `context/source_evidence.json` in the diagnostics
  zip (`:771`), and S9's issues note row inside `build_release_bulk_import_workbook`. The
  candidate has no data row; it is not invisible, and it is now visible under a NAME instead
  of an unexplained `BLOCKED`.

**Which release state the reviewer sees.** The assessment release's public state comes from
`_release_summary` (`api/build_assessments.py:305-318`), which S6 gives `release_state` in
§4's three names. That state is computed for the assessment release from
`diagnostics["payload_errors"]` — the same list — so a candidate with no home yields
*Diagnostic release*: all four downloads 200, database write refused. It is **not** routed
through `build_concepts_release.structural_defects`, which is the CONCEPT lane's function
over the concept payload and has no candidates in it. Saying otherwise was the previous
round's error and it is corrected everywhere it appears (D8.5, S6, S8, T9-1 B1, T10-7).

**The argument that lost, restated so it is not re-litigated:** "an unresolved home is a
placement judgment, so Q10/Q13 make it a flag." It loses because the flag would have to ride
on a row and there is no row — `continue` fires before `_append_group_row:384-393`, the only
function that appends one. If the renderer could emit the candidate onto its sheet with its
home cells empty and a review flag attached, this would be a flag; it cannot, so it is a
defect. That redesign is a later step with its own placement work, not step 8.

**Six sections now state this one rule and were re-read after editing to confirm they
agree:** T7.5 (here), **T9-1 B1**, **T10-7 item 13**, **D8.5**, **S6** (keeps `:326-330`,
adds the reason) and **S8** (lands the staged check and deletes `:326-330`).

**T7.6 — `migrate_workbook_layout`: owner, signature, caller, lock — and its SUBJECT, which
the map and every earlier draft of this spec got wrong.** T6 forward-referenced
"T7's ruling on `migrate_workbook_layout`" and T7 did not make one; S7 then specified its
behaviour with no module, no signature and no call site. Named here.

**T7.6a — the subject is `config.BULK_IMPORT_OUTPUT`, runtime state on a deployed volume.
It is NOT `backend/data/bulk_import_database.xlsx`, and the premise of the old OR2 was
false.** The map and this spec called `bulk_import_database.xlsx` "the checked-in
append-only production workbook appended to under a process-wide lock by five call sites".
The owner's second verifier found that independently; I re-measured all of it:

```
$ grep -rn "BULK_IMPORT_DB" backend --include=*.py
app/config.py:70                     BULK_IMPORT_DB = DATA_DIR / "bulk_import_database.xlsx"
app/services/data_reset.py:38,39,40  .exists() / .unlink() / report            ← DELETES it
app/services/data_reset.py:68        "seed_workbook": ….exists()               ← reports it
scripts/generate_dummy_data.py:39    DEST = config.BULK_IMPORT_DB              ← WRITES it
tests/conftest.py:20,31              reads it as the suite fixture
tests/test_data_reset.py:11,22       reads it
```

**Nothing appends to it.** `.gitignore:57` states its role in the repo's own words —
*"data/bulk_import_database.xlsx IS committed — it is the database fixture."* And CI
**regenerates** it before every run: `.github/workflows/ci.yml:28` is
`python scripts/generate_dummy_data.py`, immediately before `:29` `pytest -q`, and that
script writes the file through `app.bulk_import.writer` (`generate_dummy_data.py:32`
`from app.bulk_import import writer`). So the instant S7 moves the writer to the target
layout, **this file is regenerated in the new layout automatically**. There is no
migration, no coordination window, no irreversible half, and no owner risk for it.

```
$ grep -rn "BULK_IMPORT_OUTPUT" backend/app --include=*.py
app/config.py:72                       "# Every generation appends here (append-only, never overwritten)."
app/services/build_concepts.py:1923    app/services/post_generation.py:118
app/services/tagging.py:183            app/api/data.py:66-70
app/services/build_concepts_release_publication.py:225
$ sed -n '46,47p' .gitignore
# The append-only generation output is runtime state, not source.
backend/data/bulk_import_output.xlsx
```

`BULK_IMPORT_OUTPUT` is the real append-only accumulator and it is **gitignored**. And the
"five call sites appending under a process-wide lock" were never appends at all: [measured]
`grep -rn "output_workbook_lock" app/` returns `build_concepts.py:1805`, `:1865`,
`build_assessments.py:691`, `:1049`, `api/data.py:63` — five **lock ACQUISITIONS**, plus the
`@synchronized_output_workbook` decorator at `tagging.py:174` — and the lock guards the
**output** file. The old OR2 attributed the output file's properties to the fixture file.

**Consequence, propagated everywhere the spec named the wrong file:** the layout migration
still exists and is still needed — a deployed volume carries a real
`bulk_import_output.xlsx` on the canonical layout and `append_concepts` must not write
new-layout values at old-layout positions into it. But its subject is **runtime state on a
deployed volume, not a committed artifact**. That removes the entire "coordination window /
outside consumer reading it by column position" risk class that OR2 was escalated for, and
it removes the fallback plumbing (`BULK_IMPORT_DB` as an ordered tuple, a sequence-of-paths
parameter) the previous round specified — none of which is built. Corrected in: this
section, **T12/M9**, **S7**, **D3/Q16**, §7 (OR2 removed — it is answered), §8 and the
REPAIR LOG. **V5 is also settled by it:** the previous round wired the fallback to
`BULK_IMPORT_DB`, the wrong constant, and that wiring is deleted rather than re-pointed.

```
app/bulk_import/layouts.py                       # beside the registry it reads
def migrate_workbook_layout(
    path: Path, *, target_layout_id: str,
) -> MigrationReceipt                            # dataclass, all mechanics:
    #   source_layout_id, target_layout_id, sha256_before, sha256_after,
    #   rows_by_sheet_before, rows_by_sheet_after, sibling_path,
    #   alias_entries_applied: list[str], unmappable: list[dict]
```

* **Owner: `layouts.py`, not `writer.py`.** The migration is a pure function of the two
  registry entries; putting it in the writer would give the writer a second job and would
  re-couple the layout knowledge S2 just centralised.
* **Caller: `writer.append_concepts` (`writer.py:1049`)**, at the top — after
  `scan_workbook` (`:1058`) has identified the file, before
  `openpyxl.load_workbook(path)` (`:1059`). One call site, on the one path that mutates an
  existing workbook.
* **It operates on the STAGED SIBLING of `config.BULK_IMPORT_OUTPUT`, not on the live
  file, and never on the committed fixture.** [verified]
  `build_concepts._stage_concept_workbook:1750-1776` copies `target → staged` (`:1767`,
  `shutil.copy2`) and hands `append_concepts` the staged path (`:1772`); publication is
  `os.replace` via `_publish_staged_workbook:1779-1781` → `workbook_sync.atomic_publish`;
  and [verified] the `target` it is given is `config.BULK_IMPORT_OUTPUT`
  (`build_concepts.py:1921-1924`, inside `_commit_and_publish_concept_workbook`). So the
  migration inherits A's transactional outbox unchanged (MC4/MC-E), a crash mid-migration
  leaves the live workbook untouched, and the file being migrated is gitignored runtime
  state on the deployed volume (T7.6a) — never a committed artifact.
* **Lock:** `workbook_sync.output_workbook_lock()`, which it takes itself. [verified]
  `_OUTPUT_WORKBOOK_LOCK` is an `RLock` (`workbook_sync.py:25`), so taking it inside is
  safe under every existing holder — `build_concepts.py:1805` and `:1865`,
  `build_assessments.py:691` and `:1049`, `api/data.py:63` — and correct for a caller that
  does not already hold it. Do not rely on the callers: an unlocked migration on the
  shared workbook is the one failure this design cannot recover from.
* **Pre-migration sibling** `<stem>.pre-<source_layout_id>.xlsx` is written beside the
  target before any migrated byte, per Q16's recorded receipt requirement.
* **It never raises on row content.** An unmappable non-blank value lands in the receipt's
  `unmappable` list and becomes a Fixer decision plus a recorded release issue (S7); the
  run completes. This is the writer half of T6's asymmetry: the reader may fail closed
  because no run is in flight, the writer may not because the model budget is already
  spent.
* **Landing slice: S7.**

**The argument that lost.** "(b) port hash verification and a receipt into A." It loses
because it duplicates the lease/atomic-exposure/crash-recovery machinery a second time,
and after T1 the four artifacts carry B's manifest hashes anyway.

### T8 · The fourth release state, and Rule E

**Decision — NO fourth state. §4's three names stand.**

**D8.1 · Split what `structural_defects` conflates.** `build_concepts_release.py:457-499`
today mixes genuine corruption (`refused`, `snapshot_defects`) with mere emptiness ("the
release contains no concept rows to upload"), and `release_state:503` reads any defect as
`DIAGNOSTIC_RELEASE`. Split into `structural_defects(payload)` (corruption only: refused,
snapshot_defects, the T7.4 skipped-row defects, and T9's closed identity set) and
`nothing_to_publish(payload) -> bool` (no `_publishable_record`,
`build_concepts_release.py:440-452`).

**D8.2 · Publishing nothing becomes an idempotent zero-row success**, returning
`database_uploaded: True` with empty id lists and a receipt. §4:475-478 requires
publication to be "idempotent, model-free, never drops a highlighted row"; writing nothing
when there is nothing IS the idempotent answer.

**D8.3 · The gate that keeps D8.2 safe — Cluster D's marker is REFUTED and replaced.**
Cluster D opened the zero-row success on `lane_content_decided`, written where
`stage_pre_release` returns None only when `pre_map is None`
(`build_concepts_release.py:1971-1972`). [verified] that records "Phase 03 ran", not "the
model decided this chapter assumes no prior knowledge": `phase3/premap.py:860-879` returns
the `empty` map when `captured == []` and its own docstring says it does so **"without
spending a decision"**, logging only to `progress`. An OCR-degraded scan, a thin
lower-grade chapter and a chapter that genuinely assumes nothing are indistinguishable
under that marker, and Cluster D's stated mitigation ("written at exactly one site")
cannot separate them because the two conditions are not the same condition.

**Replacement: step 8 makes premap spend the verdict.** When `captured == []`, premap takes
ONE `kernel.decide` verdict — *does this chapter assume no prior knowledge, or did the
capture fail to reach it?* — with the source evidence, an advisory critic, and a Fixer
seam, and stamps `pre_lane_verdict` onto the Pre payload. Ready + zero-row publish opens
only on `assumes_nothing`; `capture_incomplete` keeps the release Diagnostic. This is
Rule 1 applied literally: it replaces a silent shape-inference ("the list is empty,
therefore the chapter needs nothing") with a recorded model verdict, and it costs one call.
It also removes the need for a fourth state, so §4's three names are unamended.

**D8.4 · Rule E, enforced for the first time.** Per T7.4, `transient_release_hierarchy`
returns defects instead of raising on row content;
`build_release_bulk_import_workbook:44` always returns a workbook;
`assessment_release_snapshot.build:97` keeps `SnapshotError` only for a snapshot it
genuinely cannot build (no frozen source-document hash, `:104-110`), so Outputs 02/04
build from the sound rows; `api/build_concepts.py:304-306` keeps ValueError→404 only for
"this upload has no staged release".

**D8.5 · Cluster D's "every `defects` entry enters `structural_defects`" is structurally
impossible as specified, and this is why the defects are discovered at STAGING.** [verified]
`structural_defects` (`build_concepts_release.py:457-500`) is a **pure function of the
staged payload** — it reads `payload["refused"]` (`:485`), `payload["snapshot_defects"]`
(`:489`) and `payload["records"]` (`:494`) and nothing else — and neither of its call
sites runs inside `transient_release_hierarchy`, which itself is only reachable at
download time (`_files.py:81`) and at Output-**04** build time
(`assessment_release_snapshot.py:115`). A row skipped at projection time could reach
`structural_defects` only by mutating the staged payload during a GET — which breaks §4's
immutable-snapshot invariant and makes release state depend on whether anyone downloaded a
file. **The row-level validity check therefore runs at staging** (the proven shape:
`stage_pre_release`'s existing `snapshot_defects` parameter), the payload carries the
verdict, and the projection reads a decision the payload already holds.

**D8.5b · The same proof governs `unplaced`, and it is why T7.5 was rewritten this round.**
The paragraph above is exactly the reason a projection-time discovery cannot reach
`structural_defects` — and `unplaced` is produced at projection time, inside
`assessment_workbook.render_master_file`, reachable only through `build_dual_output` from
`assessment_release_service.py:356`, i.e. inside `publish_release`, after the payload is
frozen. So the previous round's "both `unplaced` producers become named structural defects
so the DB write is refused through one vocabulary" was refuted by D8.5 itself, and building
it as written would have left an `unresolved_question_home` candidate refused by nothing
once S6 deleted `_readiness:326-330` (T7.5). Two things follow, and both are now written
into T7.5:

* the home/group/sheet-kind verdict is computed at **staging** by
  `assessment_release.unresolved_question_homes(snapshot, profile)` — a pure function of the
  frozen snapshot's `candidates`, `groups` and `concept_key` set plus the profile's allowed
  sheet kinds, needing no renderer. **The same proof extends to every raise inside
  `render_master_file` (T7.5/B4), for the identical reason:** a raise at projection time
  cannot reach `structural_defects` either, and it is strictly worse than an `unplaced`
  append because it costs every row on every sheet rather than one candidate's;
* it is refused through `diagnostics["payload_errors"]` → `_readiness:318-324` `BLOCKED` →
  `assessment_release_service.py:537`, which is the **assessment** lane's publication act,
  and which is the route T5-1's duplicate `question_label` already takes. It is **not**
  routed through `build_concepts_release.structural_defects`, whose payload is the CONCEPT
  lane's `records` and contains no candidates at all.

**Two lanes, two publication acts, one principle.** Rule G already makes them two acts; T9's
principle governs both; their transports are not interchangeable, and this spec no longer
writes as if they were.

### T9 · Flag vs block for identity corruption

**The principle, stated once.** *A defect blocks the DATABASE WRITE when it corrupts an
identity — when two things that must be distinguishable are not, or when a thing that must
be addressable cannot be addressed. A defect flags when it concerns what the source means.
Nothing ever blocks a download.* This is the sentence `assessment_release.py:419-427`
already writes for `group_key` and the consequence
`assessment_release_service.py:327-329` already writes ("downloads stay available, the
database stays protected"); T9 stops exempting one identity. It does not contradict Rule C
(`docs/concept-release-and-type-case-routing-rules.md:66-67` forbids "deleting the
affected question or preventing output"; Rule E at :99-101 defines the output as the
released workbook; Rule G at :121-133 makes the DB write a separate act).

**T9-1 · The closed blocking set at the publication act. There are TWO publication acts —
Rule G's concept upload and Rule G's Master upload — and each item names the one it is
enforced at, because the previous round conflated their transports (T7.5, D8.5b):**

* **B1 IDENTITY** — duplicate `question_label` (T5-1); `duplicate_qid_assignment`
  (`build_concepts_release.py:917`); `unknown_type_case_qid` (`:942`); duplicate
  `group_key`; **a question with no home concept/group** — code
  `unresolved_question_home`; **a sheet kind the profile allows that THIS STEP's renderer
  cannot write** — code `sheet_kind_not_renderable` (T7.5/B3; it replaces the
  `sheet_kind_not_emittable` earlier drafts named, which could never fire); **a candidate
  whose `concept_key` disagrees with its group's home** — code `group_home_disagreement`,
  today a raise at `assessment_workbook.py:373` that costs all four outputs; **and the
  three group→concept-edge codes B4 de-raises** — `group_concept_home_unknown` (`:324-326`),
  `group_home_unnamed` (`:331-333`) and `group_visible_name_mismatch` (`:343-345`), plus
  `render_shape_overflow` for `_question_record`'s four count caps (`:239`, `:252`, `:265`,
  `:275`).

  **All of those are decided at STAGING by
  `assessment_release.unresolved_question_homes(snapshot, profile)`, not by the renderer**,
  and they are enforced at the **assessment-lane** publication act through the transport
  T5-1 already uses: `freeze`/`create_release` → `diagnostics["payload_errors"]` →
  `_readiness:318-324` `BLOCKED` → `assessment_release_service.py:537` refuses the upload.
  They block because the candidate reaches **no sheet at all** — both `unplaced` branches
  (`assessment_workbook.py:357-368`, `:412-420`) `continue` before `_append_group_row:384-393`,
  the only function that appends a data row (T7.5) — or, for the raising ones, because the
  raise costs every row on every sheet. The renderer keeps producing
  `issues["unplaced"]` as the EVIDENCE channel and decides nothing. They are **not** routed
  through `build_concepts_release.structural_defects`, which is a pure function of the
  concept lane's `records` (D8.5b). Three of B4's raise conditions (`:315` blank
  `group_key`, `:317-318` duplicate `group_key`, `:336-337` invalid `group_type`) need no
  new code at all: `validate_group:386-391` and `duplicate_group_keys:419-435` already put
  them on this exact transport through `freeze_payload:504-511`, so the renderer's raise is
  a second copy of a decision already made and is simply deleted;

  a current-chapter source QID in a Pre artefact
  (`_account_for_source_identity`, keep verbatim); a **duplicate** persisted `machine_id`,
  or a staged row whose concept still has no `machine_id` after
  `identity.machine_id_for_concept` has minted (T4-8 — an empty column is a mint, never by
  itself a defect); `duplicate_case_identity` and `duplicate_qid_route` from
  `phase3/assemble.py:363-402`.
* **B2 ARITHMETIC** — option weights, rubric sums, sub-question sums, keyword sums.
  §4:446-451 says this one never accepts a flag. Both existing passes stay unchanged.
* **B3 EXACTLY-ONCE (R4)** — an inventory item that ends the run neither Placed nor
  Flagged, from the coverage ledger (T10 item 22).
* **B4 SCHEMA / LAYOUT** — `assessment_workbook._header_errors:485` on a rendered
  artifact, the reader's layout gate (T6), and T3.6's manifest-union field gate.

**T9-2 · Deliberately NOT blocking**, and this is where Cluster C's list is corrected
twice:

* `unassigned_inventory_qid` (`build_concepts_release.py:935`) stays a **flag**. It is
  literally `inventory_qids - assigned_qids` — a coverage verdict — and R4 says every
  question ends Placed **or Flagged**. Cluster C got this right and it is the best call in
  that submission.
* **`qid_render_count_mismatch` and `example_less_case_shell` leave the blocking set.**
  Cluster C promoted "`case_uniqueness_*` findings" wholesale. [verified] two of the four
  codes that phrase reaches are text-derived: `qid_render_count_mismatch`
  (`phase3/assemble.py:404-439`) is decided by `generation._inventory_coverage_key`, a
  ~10-stage regex normalisation pipeline compared with `startswith`; `example_less_case_
  shell` (`:441-496`) matches `_RENDERED_CASE_SEGMENT_RE` against `concept_details` and
  compares casefolded prose. Both read `concept_details` — the field §7:577 explicitly
  lets the reviewer reword and `release_refiner.py:55` lists as editable. Under Cluster
  C's version, a reviewer reword flips the release to DIAGNOSTIC and refuses the upload —
  the blocking §7 forbids, on a deterministic prose comparison. They stay flags.

**T9-3 · The gate may not be voidable, and may not be a raise.** [verified]
`_case_uniqueness_issues` wraps `_type_catalog` in `except Exception: cases = {}` with the
comment "a malformed catalog never blocks release" (`build_concepts_release.py:1021-1027`)
— promoting those codes to blocking would let a malformed catalog silently disable the
gate, which is exactly where an unusual source lands. **A catalog that will not parse is
itself a named structural defect**, not a swallowed exception. And the audit is a
flag-producing pass *beside* the artifact builder, never a ninth raise inside
`transient_release_hierarchy`.

**T9-4 · Scope.** Machine-id uniqueness is checked at **mint** time against the persisted
column, scoped to (board, grade, subject) — not "within the release", because [measured]
every collision the slug produces is cross-chapter and a release is one chapter.

**The argument that lost.** "Q13 says The Fixer always passes the run through; a blocking
identity check reintroduces the halt." Q13 governs the **run**. This gate sits at the
publication act, which Rule G makes a separate, human-initiated, model-free step. The run
completes, all four outputs download, every flag is visible, and the reviewer chooses to
publish or repair. Without this, §4:474's "structural corruption blocks" describes nothing
that exists.

### T10 · The QC checklist — polarity, and where it lives

**Where it lives.** [verified] the SOP is not in this repository in any form; the only
hits for `checklist` are the doc sentences that refer to it
(`docs/aegis-restructure.md:32, 536-537, 727`). Step 8 **reconstructs** it as
`docs/release-qc-checklist.md` (the reconstruction and its provenance) plus
`backend/app/services/release_qc.py` — one pass over the staged payload, the rendered
artifacts and the coverage ledger, returning `(issues, blocking)`. **It never raises, and
it is never called from inside an artifact builder.**

**T10-0 · Its call site and the surfaces its output reaches — stated, because a named
deliverable with no caller is not buildable.** The first draft of this spec described the
audit's behaviour and its regressions and never named where it runs or where a reviewer
sees it.

```
app/services/release_qc.py
    audit(payload, *, artifacts=None, ledger=None) -> tuple[list[dict], list[str]]
```

* **Called from `build_concepts_release.stage_release` (`:1320`) and its Pre twin
  `stage_pre_release` (`:1918`)**, immediately before each assembles its payload dict —
  i.e. at STAGING, for both lanes, which is where D8.5 proved the row-level verdict has to
  live (`structural_defects` is a pure function of the staged payload and neither call
  site runs inside a projection). Never at download time, never inside
  `transient_release_hierarchy`, never at the publication act.
* **`issues` merges into `payload["issues"]`** — `:1464` for Post, `:2035` for Pre. That
  is the existing ledger, so no new transport is invented.
* **`blocking` appends to `payload["snapshot_defects"]` — and on the POST lane that key
  does not exist yet, so it is CREATED here. The previous round's "that is the whole
  wiring" was false and its own regression could not have passed.** [measured, and both
  verifiers found it independently]:

  ```
  $ grep -n '"snapshot_defects"' app/services/build_concepts_release.py
  489:    for defect in payload.get("snapshot_defects") or []:      ← the READ
  2053:        "snapshot_defects": _json_safe(                        ← the WRITE
  ```

  Exactly two hits. `:2053` is inside `stage_pre_release`, whose signature carries
  `snapshot_defects: Sequence[str] = ()` (`:1918-1929`). The **Post** payload dict built in
  `stage_release:1437-1481` has **no `snapshot_defects` key at all**, and `stage_release`
  takes no such parameter — [verified] I read its whole payload literal: `version`,
  `released_at`, `release_reason`, `job_id`, `learning_kind`, `source_book`, `filename`,
  `source_document_hash`, `target_chapter_id`, `directory_metadata`, `target_identity`,
  `checkpoint_stage`, `checkpoint_progress`, `records`, `issues`, `type_case_rows`,
  `question_task_inventory`, `extraction_provenance`, `mined_types`,
  `pending_decision_snapshot`, `final_grounding_certificate`, `chapter_meta`,
  `instruction_set`, `summary` (+ optional `refinements`). So S11's own regression —
  asserting the blocking finding appears in `payload["snapshot_defects"]` for **both**
  `stage_release` and `stage_pre_release` — could not pass as written.

  **Correction, now in S11's Changes:** `stage_release`'s payload dict **gains
  `"snapshot_defects": _json_safe([...])` in the same shape `stage_pre_release` uses at
  `:2053`** — a list of normalised strings, `_json_safe`-wrapped, defaulting to `[]`. It is
  a payload-shape change on the Post lane and therefore lands with the audit, in S11, and
  `tests/test_pre_release_lane_wiring.py:583`'s Post key set moves with it (it was already
  being rewritten in S6; S11 adds one key and the commit message names it). Only with that
  key present is the sentence below true. `structural_defects:489` then reads it on both
  lanes and the audit's blocking set reaches `release_state` through machinery that exists.
* **What this transport does NOT carry:** the candidate- and group-level codes of T7.5 —
  `unresolved_question_home`, `sheet_kind_not_renderable`, `group_home_disagreement`,
  `group_concept_home_unknown`, `group_home_unnamed`, `group_visible_name_mismatch` and
  `render_shape_overflow`.
  Those are assessment-lane identities and travel by `diagnostics["payload_errors"]` →
  `_readiness` → `:537` (D8.5b). `release_qc.audit` may still REPORT them as issues so the
  reviewer sees one list; it does not own their refusal.
* **The surfaces a human actually reaches, each verified:** the release workbook's
  **Issues sheet** (`build_concepts_release_files.py:398-409`, nine columns ending in
  Message / Full Details); the **diagnostics zip** at `context/source_evidence.json`
  (`:771`); **`release.json`** (`release_payload_bytes:503`); the **`release_state`** field
  S3 adds to the `database_upload` manifest entry and to the terminal `release_result`
  NDJSON event; and the `AssessmentRelease.diagnostics` column S6 begins writing.
* **Landing slice: S11**, which is also where the checklist document lands. The seam it
  plugs into is finished by S9 (the state split) — which is why S11 must land after S9,
  as its own "Must land after S9" note already says.

**T10-1 · The polarity inversion, with Cluster D's model of `_FATAL_CODES` corrected.**
Cluster D proposed renaming `_FATAL_CODES` → `_REPAIRABLE_CODES` on the theory that it
selects which errors the bounded repair pass sees. [verified] it does not:
`_repair_records_via_api` selects on `hard = [e for e in report["errors"] if e["severity"]
== "error"]` (`generation.py:12996`). `_FATAL_CODES` is a **halt set with four consumers**
— `generation.py:13006` (dead: no call site passes `strict=True`), `:15706`, `:15723`
(the final gate) and `build_concepts.py:887` (the deposit gate). Renaming it misnames a
halt set as a repair set and leaves the final-gate `raise RuntimeError` at
`generation.py:15742` reading "repairable errors". **The name stays; a new
`_BLOCKING_CODES` is introduced and used at BOTH gates** — Cluster D's edit list touched
only the deposit gate, so 41 codes would have kept halting the run at the final gate after
the "polarity inversion" shipped.

**T10-2 · `_BLOCKING_CODES` is an explicit ALLOW-list, not `_FATAL_CODES` minus eleven.**
[measured] `len(_FATAL_CODES) == 52`; removing the eleven judgment codes leaves **41**,
and the survivors include `issue_section_overlap` (whose entire verdict is `len(shared) >=
2 and len(shared)/shorter >= 0.8` over a hand-rolled English stemmer,
`concept_validator.py:820-830, 1347-1354`), `section_number` (a regex fired on the concept
title — [measured] it matches `'pi is approximately 3.14'` and `'a hand span is about 20.5
cm'`), `culmination_too_early` / `culmination_count` / `culmination_order` (all driven by
`concept_refiner.is_culmination`, `title.lower().startswith("culmination")` — the
**identical** classifier Cluster D deletes from `writer.py:524`), `source_artifact`,
`description_prefix`, `merged_description`, `textbook_dump`-family thresholds, and
`missing_learner_analysis` (stale since Q1 retired the every-concept contract). So Cluster
D's stated property — "all four blocking families count identities, sum numbers, or
compare column names; none reads meaning" — is FALSE as designed, and its `Q2` test would
pin a 41-code set that violates its own doctrine.

**Decision: `_BLOCKING_CODES` at the deposit and final gates contains exactly
`{required, required_parent}`** — schema presence, pure mechanics. Every other code in
`_FATAL_CODES` becomes a review flag carried on the row (`_carry_review_flags`, already
built by 6335fe6). The identity, arithmetic, exactly-once and schema blocking moves to the
publication act, where T9's closed B1-B4 set lives. Nothing is lost from the quality loop:
the repair selector (`severity == "error"`) is untouched, so every judgment code still
earns its one bounded model correction; only the destroy-on-second-failure behaviour goes.

**Consequence to state plainly, and the owner should expect it:** flagged rows increase,
deliberately and visibly. That is the trade CLAUDE.md and Q13 already made. The mitigation
is that `_BLOCKING_CODES` is a literal enumerated set pinned by a test, so "just add one
more code to the fatal set" is no longer a one-line change anyone can make quietly.

**T10-3 · `_FIXER_UNACCEPTABLE_CODES` shrinks to `{required, required_parent}`.**
[measured] it is `{duplicate_title, duplicate_topic_concept, required, required_parent}`.
`duplicate_topic_concept` today cannot be cleared by any legal Fixer move — the Fixer
refuses `accept_with_flag` for it and rows are sealed at the deposit boundary — so two
same-titled concepts under one topic, a shape a real chapter produces, kill the run. After
T4 identity no longer depends on title text, so both title codes leave the blocking set as
a consequence of T10-2's allow-list. `_dedupe_titles_chapter_wide` (`generation.py:16624`,
call sites `:16263, :16268, :16503`) is **step 11's** and step 8 does not touch it; step 8
adds no new chapter-wide title assumption anywhere, and a regression pins that.

**T10-4 · The writer's two content judgments, deleted.** In
`writer._validate_concepts_workbook_bytes` (`:386-542`): `_HUB_PREFIX_RE` (def at `:376`,
body `:376-379`, `{3,100}`/`{3,}` shape bounds) rendering "Activity/Info Hub repeats its
visible marker" (`:487-493`) → **DELETE**; `title.casefold().startswith("culmination")`
(`:524`) and `len(culmination_title) > 120` (`:532`) → **DELETE** with the whole
culmination block;
`_api_question_placement_active()` (`:383-384`) returns a constant `True`, making the
split-Type branch at `:511-519` unreachable → **DELETE**, and with it the now-dead
`_REGULAR_TYPE_NUMBER_RE` (`:372-375`) and `type_hosts` accumulation.

**KEEP the read-back identity checks** (`:458-475` exact `topic_title` /
`topic_concept_labels` / `topic_description` comparison against the DB record; `:499-505`
missing and duplicate placements) **where they are.** Cluster D proposed relocating them
from `append_concepts` to `release_qc`. That is refused: [verified] the call at
`writer.py:1105-1112` runs on the serialized bytes of `config.BULK_IMPORT_OUTPUT`
immediately before `atomic_save_workbook` — R5's "the workbook is the database" — and
`release_qc` inspects a different artifact built by a different function, so it cannot be
the same check. CLAUDE.md:52-61 explicitly protects "gates that refuse to accept a broken
artifact". What changes is the **consequence**, not the location: the surviving mechanical
half routes a mismatch to the Fixer as one recorded, flagged decision and the run
completes, instead of `raise ConceptWorkbookValidationError`. This matters because
[measured] `grep -rn ConceptWorkbookValidationError app/ tests/` returns exactly two hits
— its definition (`writer.py:368`) and its raise (`:540`) — nothing catches it and **no
test pins it**, in either lane, while it gates BOTH the mid-run deposit
(`build_concepts.py:4489 → :1921 → :1813 → :1776 → writer.py:1107`, re-raised pre-commit)
and the explicit publication (`build_concepts_release_publication.py:222` → the same
helper → `api/build_concepts.py:432-433` → HTTP 400).

**T10-5 · The `len(key) < 25` threshold in the audit itself.**
`build_concepts_release.py:981` — *"Very short prompts ('Why?', 'Explain.') legitimately
recur"* — is a character threshold deciding whether two questions are the same question,
inside the audit that is supposed to embody the doctrine. And its key builder is worse:
`_QUESTION_TEXT_NOISE_RE = r"[^0-9a-z ]+"` (`:951`) deletes every uppercase letter and
every non-Latin character. [measured]:

```
'Name the acid in curd.'                              → 'ame the acid in curd'   (len 20 → skipped)
'NAME THE ACID PRESENT IN CURD…'                      → ''
'दही में कौन-सा अम्ल पाया जाता है…'                      → ''
'Explain why Ramanujan and Hardy disagreed about 1729' → 'xplain why amanujan and ardy disagreed…'
```

So `repeated_question_text` — the audit that stops one learner meeting the same question
twice — is inert for every non-Latin source, for capitalised text, and for short
lower-grade questions. **Delete the threshold; casefold before the noise substitution;
make the noise class Unicode-aware (`\w` with `re.UNICODE`, keeping digits and
whitespace).** Report every collision as ONE grouped warning issue carrying the QIDs and
the shared wording, so a reviewer dismisses a "Why?" in one glance. Reporting a collision
is identity accounting; suppressing it was the judgment. Be honest about what survives:
`_QUESTION_ITEM_MARKER_RE` (`:954-956`) still decides that a leading "(iv)" is numbering
rather than part of the question — that is a shape judgment, it is recorded in the
checklist as a named residue with the step that owns it, and it is not called "exact
wording".

**T10-6 · `_learner_analysis_count` is purged.** [verified]
`build_concepts_release.py:1132-1138` counts rows whose `concept_details` contains the
English substring `"misconception"`, and `:1195-1203` uses that count to choose which
whole concept map ships, applied inside `stage_release` at `:1357-1360`. It is a keyword
vocabulary classifying content AND a count deciding meaning, and it scores against a
requirement Q1 explicitly retired ("not every concept needs one … the every-concept
authoring requirement and its missing-analysis flag are retired"), so a chapter that
correctly follows Q1 scores low and has its validated topology discarded. The tie-break
becomes the recorded `_aegis_analysis_allotments` marker count — allotments are a recorded
model verdict, not a substring — or, where no marker exists, the artifact's own recorded
completeness. Named by neither the map nor any cluster; found by the doctrine lens.

**T10-7 · Per-item rulings for the reconstructed checklist** (the 23 items plus three
extras). Mechanical, universal, blocking per T9: 1 (five-level join — **fixed** by T3.7,
compared as a flag until comma-escaping exists), 2 (title/display/machine-ID pair —
**fixed** by T4, newly checked), 3 (label uniqueness — **fixed** by T5), 8, 11, 13, 14,
15, 21 (keep verbatim), plus 19's duplicate-QID half, 20's absent-from-output half, and
22. **Item 13 is kept verbatim and is now stated with its transport, so it agrees with the
corrected T7.5 and D8.5b: a candidate that reaches no data row blocks the ASSESSMENT-lane
publication act through `diagnostics["payload_errors"]` → `_readiness` `BLOCKED` →
`assessment_release_service.py:537`, on a verdict computed at staging by
`unresolved_question_homes`, never on anything the renderer discovers.** Mechanical,
profile-scoped, flagging: 4, 6, 9, 10 (blocking as B4). **Item 5 is fixed
here**: [measured] `assessment_workbook.py:224` reads `question_source` and nothing in the
release lane sets it, while every gold row carries "UpSchool DB" — it names the ORIGIN
SYSTEM, not a school, so it goes in the profile with that default. Pure judgment, no
mechanical check ever: 17 (alt-text neutrality — the only expression stays the prompt
contract at `assessment_materialization.py:54`; writing a regex here would be the defect),
18's fit half, 23. Item 16 stays a flag and Q8 durability stays step 10's. Item 12 is
already correct — the enum blocks, the verdict never gates.

**Item 22 is the one step 8 must not skip.** [measured]
`coverage_ledger.build_coverage_ledger` has exactly one production caller —
`build_concepts_release_files.py:666`, inside the diagnostics zip. It produces no issue,
sets no flag, feeds no state: **R4's own enforcement is inert.** `release_qc.audit` calls
it, turns each `unaccounted` entry into a named issue, and puts "neither Placed nor
Flagged" into B3.

**T10-8 · The Pre lane's deposit Fixer stays dormant — declined deliberately.**
[verified] `build_concepts.py:790` sets `deposit_fixer = … if pre_post == "Post" else
None`, and the comment at `:890-892` that the non-Post branch has no production caller
since step 7 is true: the only `_deposit_concepts` chain passes `pre_post="Post"`.
Publication never imports `concept_validator`. Do **not** give publication the deposit
validation — `release_qc.audit` runs at STAGING for both lanes and its blocking set feeds
`structural_defects`, so a corrupt release cannot be published, with one gate at the right
boundary and no new Fixer requirement. Step 8 must not wake that dead branch.

### T11 · Chapter-wide concept-title uniqueness vs the §4 language mode

**T11.1 · Step 8's minted identity is topic-scoped and positional; it never relies on a
chapter-wide title.** Two concepts titled "Culmination" under two stanza-topics mint two
different `machine_id`s and two different `concept_title` cells (T4). This is the same
change as closing the CH01 gap recorded at
`backend/scripts/chapter_authoring/emit_canonical.py:66-79`.

**T11.2 · Step 8 adds NO new chapter-wide title uniqueness assumption anywhere** — not in
the QC audit (no chapter-wide duplicate-title issue code), not in the header-identified
reader, not in the release identity resolution, not in any label aggregate. Every
aggregate (`concept_question_labels`, `topic_concept_labels`, `related_concepts`) is keyed
on concept identity, never on title text. Forward-compatibility verified:
`writer._row_concept_placement_key:205-216` keys (concept, chapter, topic, lane);
`assessment_workbook` keys by `concept_key`.

**T11.3 · Step 8 fixes the publication join, because step 8 owns the converged publication
path.** [verified] the site that actually destroys content is
`build_concepts._find_concept_in_chapter:551-566` — a normalised-title match anywhere
under the chapter, lane-scoped only — used at
`build_concepts_release_publication.py:137`, followed by `existing.topic = topic` (`:154`),
`db.delete(tag)` on every cross-topic QuestionTag (`:155-163`) and a full content
overwrite (`:164-170`). `_publication.py:119-172` never invokes `concept_validator`, so
this path is reachable exactly when the upstream gates are retired. Two legitimately
same-titled concepts under two topics silently become ONE published row on the wrong
topic, with the first row's content gone and no issue recorded.

**Decision:** after T4, publication resolves each staged record to a DB concept by
**persisted `machine_id`**, not by title. A same-titled concept under a different topic
neither merges nor re-parents; the collision is recorded as a release issue at **staging**
— not at publication — because §7 puts the instruction box ("combine these two concepts
into one" is a worked example at `docs/aegis-restructure.md:571-575`) on the *staged*
release, and an issue raised at publication reaches nobody who can act on it. Cluster B
filed the remedy at the wrong boundary; corrected.

**The argument against, and why it loses.** `_find_concept_in_chapter`'s docstring defends
the chapter-wide search: *"Schools use different books; the same concept arriving from
another book must be reused (and its sources extended), never duplicated."* It loses
because the feature is a convenience built on a forbidden inference ("same title ⇒ same
concept" is a judgment about what the source means, answered deterministically, silently
and destructively), the reviewer can still merge explicitly, the collision is now NAMED,
and R4 outranks convenience: one silently deleted concept row is unrecoverable, one
unmerged `concept_source` is a visible, reviewer-fixable duplication. **The named
regression risk is not the second-book case but the re-run case** — re-generating the same
chapter with a renamed topic today re-parents and deletes the stale tags; with machine-id
resolution the row is matched by identity, not by topic, so Rule G's idempotency is
preserved and no duplicate set appears. That is why the fix must be machine-id resolution
and not merely "topic-scoping the title match", which Cluster B proposed and which would
have produced a full duplicate concept set on every re-run.

**T11.4 · Step 11 keeps the rest.** `_dedupe_titles_chapter_wide` retires there; step 8
leaves it possible and does not deepen the dependency. Flagged for step 11, found while
verifying: `generation.py:15857-15860` `_GENERIC_SKELETON_FAMILY_RE` lists "culmination"
among the English words that disqualify a parent family, and `premap.py:1049` excludes
culmination concepts from needed-for linking via `concept_refiner.is_culmination:79-80`
(`.startswith("culmination")`, 86 references). Under §4's language mode the culmination is
structural, so both are language-mode blockers. 6335fe6's own commit message already flags
`_GENERIC_SKELETON_FAMILY_RE` as deferred residue; step 8 inherits it **explicitly as out
of scope** rather than silently.

### T12 · Profile plumbing vs freezing the layout

**The test applied:** a sweep finding is step 8's MUST-FIX only if step 8 rewrites that
exact code, AND leaving the reference value in it either bakes the school in deeper at the
moment we were converging, or converts a today-unreachable defect into a reachable
silent-loss path once all four outputs move onto that renderer.

**M1 · Sheet names and the sheet set come from the layout registry**, not from
`bulk_import/__init__.py:19-28`'s `SHEET_OBJECTIVE = "Objective "` and
`SHEET_DOC_LINK`. `writer._new_workbook:1140-1154` stops creating the Doc Link sheet
(`:1146`, `SHEET_DOC_LINK = "Doc Link <> Each fields "`, `__init__.py:22`), which the
schema of record does not have, and takes the registry's sheet ORDER. [measured] today the
three builders disagree on both:

```
format workbook (the authority)   ['Objective', 'Descriptive', 'Subjective']
writer._new_workbook              ['Objective ', 'Subjective', 'Descriptive',
                                   'Doc Link <> Each fields ']   ← Outputs 01/03
assessment_workbook._new_workbook ['Objective', 'Descriptive', 'Subjective']
```

**That second line is the sheet set of the two Concept Files the owner ruled on**, which is
why T17 is bound to M1 and not to `assessment_workbook`. `scan_workbook:244` and
`append_concepts:1049` identify an existing file's sheets by header signature with a legacy
sheet-NAME alias (`"Objective " → Objective`) — mechanics for reading old files, not a
second layout.

**M2 · The sheet map is derived from what the RENDERER can write, an unrenderable kind is a
DEFECT, and the profile no longer answers this question twice.**
`assessment_workbook.py:407`'s two-entry `sheet_for_kind`, the `:413` comment naming a
school and the `:417` reason string that repeats it all go — replaced by the
`sheet_kind_not_renderable` defect of T7.5/B3, carrying the kind, the profile name and
`RENDERABLE_SHEET_KINDS`. **`sheet_for_kind` is derived from `RENDERABLE_SHEET_KINDS × the
layout's sheet-name map, NOT from the profile** — an earlier draft said "from the profile ×
the manifest", and that is the version B3 refutes: the profile's allowed set and the
layout's sheet set both contain Subjective the moment a profile widens, so a
profile-derived map has no `None` branch to take and the defect could never fire, while
`_question_record:199-283`'s two-branch body would raise instead. Cluster B/C's
"make it a LOUD render error" is refused: [verified] `WorkbookRenderError` is a
`ValueError` raised inside the renderers and `build_dual_output` is called unguarded at
`assessment_release_service.py:356` inside `publish_release`, before the staging lease — a
raise means NO workbook is written for ANY of the four outputs, trading an R4 breach for
an R3/§4 breach ("evidence still ships"). **A kind the profile allows that the renderer
cannot write is a recorded structural defect** → Diagnostic release → downloads open, DB
write blocked. Same correction applies to `assessment_materialization.py:120` and
`assessment_marking.py:321`, which raise long before any renderer.

**M3 · `render_concept_file:180` and `render_master_file:298` take the resolved profile.**
`build_dual_output:979` already resolves one; passing it two lines further is the enabling
change for M2 and M4.

**M4 · `_FORCED_BLANK` (`assessment_workbook.py:62`, applied `:93`) becomes the profile key
`forced_blank_fields`, and ALL THREE SITES MOVE IN S8. There is no "or none".** [measured]
neither snapshot builder carries `chapter_duration` at all
(`assessment_release_snapshot.py:208-216`, `assessment_release_service.py:135-141` both
emit a five-key chapter dict without it), and `validate_concept_file:718-737` lists
`chapter_duration` among the fields a Concept File "must keep blank", so un-blanking it
in one place either changes nothing or turns every release of the widened profile into a
Diagnostic release. The three sites are:

1. `assessment_workbook._FORCED_BLANK:62`, applied at `:93` → reads
   `profile["forced_blank_fields"]`.
2. `validate_concept_file:718-737`'s must-keep-blank list → reads the same key, so the
   read-back gate and the renderer can never disagree.
3. Both snapshot builders (`assessment_release_snapshot.py:208-216`,
   `assessment_release_service.py:135-141`) → the chapter dict **carries** every field the
   profile may un-blank, so there is something to un-blank.

**An earlier draft wrote "all three move together, or none does". That escape is
removed**, and its removal is the point of the item: "none" leaves
`forced_blank_fields` present in the profile and read by nothing, which is *exactly* the
declared-and-unread B-class defect this item exists to close — the same shape as
`AssessmentRelease.provider_identity`, declared at `models.py:435` and [measured] never
written by anything (MC-K). A key that no code reads is worse than no key: it reads as
plumbing that works.

For `reference-1` the key's value contains `chapter_duration`, so the shipped bytes are
byte-identical before and after — the plumbing is proved by M8's `reference-2`, which
un-blanks it. **The general form is pinned too:** every key in `DEFAULT_PROFILE` is named
in a test beside the module:function that reads it, and the test fails on a key with no
reader. That is the cheapest possible guard against the next declared-and-unread key, and
it is pure mechanics — it asserts that a reader exists, never what the value should be.

**Paired constraint, non-negotiable:** this may not be used to start shipping
`build_concepts.py:1209-1211`'s `f"{max(40, n_concepts * 12)} minutes"` — verified live at
HEAD, "Rough classroom estimate: ~12 minutes of instruction per concept", volume-derived
structure of exactly the shape CLAUDE.md:17-18 forbids. It is purged in the same PR
(separate commit), together with the adjacent
`build_concepts.py:1198-1201` `chapter_description = "This chapter develops {n_concepts}
concept(s) across {len(topics)} topic(s): …"` — code-composed recap text scaled from
counts, on §3's purge list, and [verified] it IS in the snapshot chapter dict
(`assessment_release_snapshot.py:216`), so the migration would carry it into all four
outputs. Cluster B called the blanked value "an authored `chapter_duration`"; that framing
promotes a computed number to authored content and is corrected here.

**M5 · `assessment_cells._allowed_sheet_kinds:99-103` reads the profile** instead of
taking `_profile` and returning `rel.SHEET_KINDS`, with its two mirrors at
`assessment_release_run.py:686, :887`. The value is published to the MODEL as evidence at
`assessment_cells.py:114`, so today the payload tells the model something the profile
denies. Cost check: for the DEFAULT profile the derived tuple is identical
`("objective","descriptive")`, so the payload stays byte-identical and **zero decisions
re-key**. Pin that.

**M5b · It reads through ONE accessor, and the reason is a golden that must not move.**
[verified] `decide_cells:226-245` deliberately does **not** call `assessment_profile.resolve`
— it type-checks the Mapping at `:242-243` and reads it with `.get` — and
[measured] `tests/golden/rne_assessment_candidates.json:16-20` records the profile it was run
with as `{name, appears_in, allow_subjective_rows}`, fed straight in at
`tests/test_mes_candidate_golden.py:86`. That golden is one of the eleven §4 forbids moving.
So the new key is read through

```
app/services/assessment_profile.py
def sheet_kinds(profile: Mapping) -> tuple[str, ...]:
    return tuple(profile.get("sheet_kinds") or DEFAULT_PROFILE["sheet_kinds"])
```

and **every** site goes through it: `assessment_cells._allowed_sheet_kinds:99-103`,
`assessment_release_run.py:686` and `:887`, `assessment_release.validate_blueprint_cell:166`
and `validate_candidate:192`, `assessment_materialization.py:120`,
`assessment_marking.py:321`, `assessment_blueprint.py:100`, and
`assessment_workbook.validate_master_file:751`. One accessor, one default, one place to
change — the same discipline T4-6 applies to `topic_identity` and T4-9(b) to
`source_order_key`. **The alternative that lost: make `resolve()` merge `DEFAULT_PROFILE`
under a partial dict.** It loses on measurement, not taste: `decide_cells` never calls
`resolve`, so the merge would not reach the golden anyway, and adding a `resolve` call there
would make `_profile_payload:107-111`'s own `appears_in` gate — pinned live by
`tests/test_assessment_cells.py:419-425` — unreachable, because the merge would always
supply an `appears_in`. A portability fix that silently disables an existing gate is the
exact defect class this section exists to close.

**M6 · `assessment_release.py:103 SHEET_KINDS` and its gates** (`:166-169` carrying the
universal-sounding "MES never uses Subjective", `:191-195`) read the profile through M5b's
accessor, and `freeze_payload:490` gains a profile parameter threaded from its two callers
(`assessment_release_service.py:261`, `assessment_master_refiner.py:598`). That also fixes
`validate_placement:375-382`'s hardcoded "secondary_placements must be empty for MES" by
reading the EXISTING `automatic_secondary_tags` key — do not add a second key; two keys
for one question is how that defect happened.

**M6b · `sheet_kinds` SUPERSEDES `allow_subjective_rows`; the boolean is DELETED, not kept
beside it. This is B3's other half and it is one decision with T7.5/B3, not a second one.**
[measured] `grep -rn "allow_subjective_rows" app/` returns three hits:
`assessment_profile.py:25` (the declaration), `assessment_workbook.py:751-753` (the only
live read, inside `validate_master_file`) and `assessment_cells.py:101` (a comment saying it
"remains a future profile capability; it cannot widen this wire contract yet"). So:

* `DEFAULT_PROFILE` gains `"sheet_kinds": ("objective", "descriptive")` and **loses**
  `"allow_subjective_rows"`.
* `validate_master_file:751-755` becomes
  `if "subjective" not in assessment_profile.sheet_kinds(profile) and parsed["sheets"]["Subjective"]["rows"]:` —
  same message, same behaviour, byte-identical for `reference-1`.
* `assessment_cells.py:100-102`'s comment goes with the constant it describes.

Two keys answering one question is what M6 already forbids one paragraph up, and this one is
worse than the `validate_placement` case it cites: a boolean cannot express a THREE-value
set, so `reference-2` — the profile M8's parametrised acceptance test exists to prove — is
inexpressible while it survives. And leaving both would let the derived tuple and the
boolean disagree, which is the declared-and-unread shape M4 exists to close, one worse.
**What this does NOT break, [measured] and worth stating because it is the cheap part:**
`tests/test_assessment_release_run.py:1176-1183` and
`tests/test_assessment_cells.py:428-445` both pass a profile carrying
`allow_subjective_rows: True` and assert the wire is NOT widened. Under M5b's accessor those
dicts carry no `sheet_kinds`, so they fall back to the default and **both still raise, green
unchanged**. The second one's NAME becomes untrue, so S8 re-authors it to
`test_a_widened_profile_widens_the_cells_wire_contract` — with `sheet_kinds` including
`"subjective"` the cells wire accepts it, and the refusal moves downstream to the named
`sheet_kind_not_renderable` defect, which is exactly where B3 puts it.

**Scope limit, stated so the implementer does not over-build, and now consistent with the
defect B3 names:** step 8 makes the profile REACH every site that hardcodes the reference
answer, and makes a mismatch **loud and NAMED**. It does NOT make Subjective work end to end
— that needs `_question_record:199-283` to render the 144-column Subjective answer blocks
(today it has exactly two branches, `:237` Objective and `:248` Descriptive), materialization
and marking support, and its own acceptance fixture. That is Output-**04** lane work (the
Post Master File) with its own step, and it is the step that widens
`assessment_workbook.RENDERABLE_SHEET_KINDS`. Until then a widened profile does not produce
Subjective rows — **it produces a `sheet_kind_not_renderable` defect with all four downloads
intact**, which is precisely what this scope limit means expressed as behaviour.

**M7 · Persist the run's profile — no migration.** `assessment_profile` name + layout
manifest sha256 into `AssessmentRelease.provider_identity` (`models.py:435`), read back at
`assessment_release_service.py:356` as `build_dual_output(release.concept_snapshot,
release.provider_identity.get("assessment_profile"))`.

**M8 · A second, TEST-ONLY registered profile plus a parametrised acceptance test.**
Without it nothing proves M2-M6 work and the next reader re-hardcodes. `reference-1`
behaviour is pinned unchanged; `reference-2` (differing on `sheet_kinds` and
`forced_blank_fields`) proves the plumbing. It lives in the test tree and must not become
a second pinned school. **What `reference-2` proves is stated exactly, because an earlier
draft left it as "either the rows or a NAMED defect" and that ambiguity is what B3
resolves:** `reference-2` widens `sheet_kinds` to include `"subjective"`, and the required
outcome is that a subjective candidate (i) **passes** `validate_candidate` — which is the
plumbing working, and which fails today — and (ii) yields a named
`sheet_kind_not_renderable` entry in `diagnostics["payload_errors"]` with **all four
workbooks written and all four downloads 200**. It must **not** produce Subjective rows:
`_question_record` cannot write them (M6b's scope limit), and a draft that allowed "either"
would let an implementer satisfy M8 by shipping a Subjective row through the Descriptive
branch. `forced_blank_fields` is the second axis and un-blanks `chapter_duration` (M4).

**M9 · The layout id is NOT a profile key.** Cluster B proposed `"layout": "reference-1"`
in `DEFAULT_PROFILE`. Refused on two measured grounds: `DEFAULT_PROFILE["name"]` is
already `"reference-1"` (`assessment_profile.py:19`), so the key collides with the
registry id; and **`config.py:72` makes `BULK_IMPORT_OUTPUT` one global append-only
file** — [corrected this round: the previous draft cited `config.py:70`'s `BULK_IMPORT_DB`,
which is the committed test fixture and is appended to by nothing, T7.6a] — so a
profile-switched emission layout would flip the append-only database of record on every
append. The layout id belongs on the **artifact** (the release row's `layout_id` column,
T2, and the workbook manifest); a profile may NAME a layout for its own deliverables. The
registered id is `sop-mes-1`, not `reference-1`.

**M10 · `reader.py:506`** — see T6.3. It does **not** take the profile's raw wire value.

**M11 · Re-cite the `spec §N` citations on the lines step 8 touches.** [verified] 15 files
under `backend/app` cite `spec §1`–`§14` and `§23`; `docs/` has no such document.
`_FORCED_BLANK`'s "MES §3.8" → the profile key plus Q5/§6; `validate_placement`'s "spec
§3.7" → the profile key; `validate_candidate`'s "spec §3.5" →
`docs/aegis-restructure.md:436-440`. An implementer cannot otherwise tell a Q5-settled
layout fact from an unsettled school-policy fact — the mechanism that produced the "40
questions per concept" prose defect.

**NOT step 8, named individually with the step that owns it:** `generation.py:4396-4447`
publisher banner regexes + English question grammar + the `>= 2` count at `:5834` (own
step; the fix is Architect-authored source conventions, not a bigger regex);
`concept_validator.py:820-830, 847, 850-854, 1398, 626-635` English stemming/ratio/char
floors (with T10's model-verdict replacement); `phase3/analyse.py:51-54 _PRACTICAL_KINDS`
and the "typically surfaces around practical/experimental work" prose at `:282-284` and
`phase3/prompts.py:90-92` (own small change, cheapest high-value item in the sweep);
`chapter_durations.py` and `generation.py:681`'s "Indian school boards (ICSE/CBSE)";
`frontend/src/pages/BuildAssessments.tsx:44, :484` "MES Release" labels (step 9);
`assessment_grouping.py:24 TIER_CODES` vs `assessment_release.py:93 GROUP_TYPES`
duplication (dedupe when the grouping lane is next opened; no `group_tiers` key until a
school actually differs); `source_language` / `source_conventions` Architect slots (step
11-adjacent).

**Settled, not escalated:** `group_name = "Concept — Tier"` is Q12, project-wide — re-cite
the comment to Q12 and move on. `COGNITIVE_SKILLS` / `DIFFICULTY_LEVELS` are **not** step
8: [measured] every gold row uses `Less`/`Moderate` and Bloom action verbs, so the enums
are the reference workbooks' own wire values. But Cluster B's *reason* is wrong and must
not be committed: [measured] `models.py:125` and `:132` are `String(64)` and `String(16)`
free-text columns with no enum and no constraint, so "they protect a DB enum" is false.
The correct reason is that nothing in the repo proves them universal **and** nothing
proves them school-specific, so widening them is unmotivated work — record that honestly.
Related and unnamed by anyone: `bulk_import/__init__.py:266-282` `_COGNITIVE_LEGACY` /
`_DIFFICULTY_LEGACY` map "average"→"Moderate", "analysing"→"Analyse" — a keyword
vocabulary reclassifying a content value. Recorded as residue with no step 8 action.

**Sweep H1 is ALREADY FIXED at HEAD** by 6335fe6 — [verified] `grep -n
"_CANONICALIZE_MIN\|structural_floor\|min_keep" app/services/generation.py` returns no
identifier. Do not re-open it; the sweep's rank-2 item is done.

### T13 · The six Post-lane prompts in the frozen core

**Decision — list all six, in a SEPARATE and FIRST commit, in DOC ORDER, recording the
resulting hash in the commit message.**

[measured, in-process, all three of the map's §9 values are stale]:

```
current  empty_set_sha256() = 88a685074cfea8403d5f968c2728a2635d5dfea0ece0ea10d7e7e2e583332eff  (42 entries)
doc order (ANALYSE_INVENTORY_SYSTEM, ANALYSE_ALLOT_SYSTEM, ANALYSE_CRITIC_SYSTEM,
           PLACE_SYSTEM, PLACE_CRITIC_SYSTEM, REFINER_SYSTEM)
         → 039bd4cae3e76623453537ed4226404740942dcc469147540a5a2bf3f8c7c930               (48 entries)
alphabetical
         → 9b5552cdd192536614d394ad449293c2f710d613e86e82eeafce3eab29bb784a               (48 entries)
hasattr(phase3.prompts, n) → True for all six
```

**Take DOC ORDER**: `_frozen_core_entries()` folds in declaration order and the module
comment at `instruction_architect.py:90` says "Names are appended, never reordered."

**Why now.** HEAD is 6335fe6, one commit ahead of the map's 19ed7e4, and that commit
ALREADY moved the frozen-core hash — its own message records `a5f280cb…676539 →
88a68507…332eff`. `origin/main` is `3aea81e`, so the re-key has **not** reached main and
every stored Post decision keyed to `a5f280cb` is already invalid on this branch with
nothing shipped. Listing the six now costs production ONE re-key event instead of two.
That settles what the map called "the actual decision input".

**Cost, measured.** No golden and no test hard-codes any value; the four pins call
`_frozen_core_entries()` live. `tests/golden/rne_envelope.json` does **not** move: its
metadata keys are exactly `[board, chapter_code, chapter_id, chapter_title, grade,
learning_kind, subject, subject_adapter, unit]` — no `instruction_set_sha256` — which is
why 6335fe6 could truthfully report it unmoved while moving the hash. [measured] its
sha256 is `e27cdcf02ed8579b1210c1d55d484cf20d604b2f08cb379c814d3d4ba1e42c79` and stays
so. Runtime effect is re-spend plus checkpoint-fingerprint invalidation, never a halt.

**Why a separate, first commit.** `docs/restructure-handoff.md:467` requires every golden
diff explained; bundled with the schema move, nothing distinguishes a re-key diff from a
schema diff. First, so the pre-spend canary suites (handoff:470) are diffed against a tree
where nothing else moved — **diff them deliberately, do not merely watch for green.**

**The argument that lost.** "Leave them out; the Post blast radius is real." It loses on
the branch evidence, and because the gap is a live cache-invalidation defect: editing
`PLACE_SYSTEM` today replays verdicts made under the old text, and `REFINER_SYSTEM` drives
both concept lanes and the assessment-master seam.

### T14 · THE FINAL ARTIFACT SET — named, because "four outputs" was never resolved to files

An earlier draft of this spec argued about outputs for two thousand lines without ever
saying which function produces which file. Named here, each builder opened.

**The numbering is the OWNER'S (OD4/D9-Q22), and the table is ordered by it:** 01 Pre
Concept, 02 Pre Master, 03 Post Concept, 04 Post Master. The manifest order follows this
table (T14's manifest paragraph and S3/S6 below).

| Output (§4 Phase 05) | Builder | File the reviewer gets | Route |
|---|---|---|---|
| **01 · Pre-Learning Concept File** | `build_concepts_release_files.build_release_bulk_import_workbook(db, job, lane="pre")` (`:44`) — transient hierarchy before upload, `writer.write_concepts_workbook` (`writer.py:1213`) after (`:78-79`) | `<stem>_pre_bulk_import.xlsx` | `GET /build-concepts/uploads/{job}/release-bulk-import.xlsx?lane=pre` (`api/build_concepts.py:288-299`) |
| **02 · Pre-Learning Master File** | `assessment_workbook.render_master_file` (`:298`) via `build_dual_output` (`:974`), against the **Pre** lane's release row (T2) | `aegis_master.xlsx` under that release's staging dir | `GET /build-assessments/releases/{id}/master.xlsx` (`api/build_assessments.py:388`), that release id |
| **03 · Post-Learning Concept File** | the same builder as 01, `lane="post"` | `<stem>_bulk_import.xlsx` | the same route, no `lane` parameter |
| **04 · Post-Learning Master File** | the same builder as 02, against the **Post** lane's release row | `aegis_master.xlsx` (`assessment_release_service.py:34`) | the same route, that release id |

**Nothing in that table is a new artifact, a new builder, a new filename or a new route.**
OD4 changes only which NUMBER names which file and the order the reviewer sees them in.
Step 7 shipped what its PR text called "Outputs 03-04" (the Pre lane); under this numbering
the same work is **Outputs 01-02**. The earlier PR text is not wrong — it is superseded, and
D9/Q22 records that so nobody reads the two numberings as two deliverables.

**What in the CODE encodes the old numbers, measured so the sweep is bounded:**

* **No manifest `kind` string does.** [measured] `grep -n '"kind":'` over both manifest
  modules returns eight lane-prefixed semantic names —
  `released_concepts` / `release_diagnostics` / `release_payload` / `database_upload` and
  their `pre_*` twins — and not one digit. The kinds S3 and S6 add (`release_bulk_import`,
  `pre_release_bulk_import`, `release_master`, `pre_release_master`) follow the same
  convention **deliberately**: a kind string that carried "01" would have to be renamed by
  this ruling, and renaming a manifest kind is a frontend break. Step 8 must not introduce
  one.
* **No frontend label does.** [measured] `grep -rn "Output 0\|Output-0" frontend/src/`
  returns exactly one hit and it is a code comment —
  `frontend/src/components/DocumentUpload.tsx:663` ("…while Output 03 stayed…"), which means
  the Pre Concept File and therefore now reads **Output 01**.
* **SIX live user-visible STRINGS do**, and they are the only code text this ruling forces.
  [measured] `grep -rn '"[^"]*Output[- ]0[0-9]' app/ --include=*.py`, filtered to strings
  that reach a human: `assessment_release_service.py:738`;
  `assessment_release_run.py:1309-1310`, `:1382-1387`, `:1454`, `:1459`; and
  `build_concepts_release.py:1895`, `:1908`. §5's table gives each one's old text, new text
  and slice — and every one of them sits in a function some slice already opens, so the
  renumbering costs no extra file. **Two existing tests assert on two of them**, including
  one line in `tests/test_mes_release_lifecycle.py`, which §4 otherwise keeps verbatim; §5
  amends that claim explicitly instead of letting it break.
* **Backend docstrings and comments carry the old numbering in ~25 places** across
  `build_concepts_release.py`, `build_concepts_release_publication.py`,
  `build_concepts_release_files.py`, `assessment_release_service.py`,
  `assessment_cells.py`, `assessment_answer_restriction.py` and `phase3/prequestions.py`.
  They are prose, not behaviour. **They are renumbered only in the files each slice already
  opens**, each renumbering called out in that slice's commit message, and a PR-body line
  states that a comment sweep of untouched files is deliberately NOT in step 8 — an
  unreviewable 25-file comment diff inside an eleven-commit PR is how a real change hides.

Three artifacts that are **evidence, not outputs**, and are never counted as one: the
review workbook `release.xlsx` (`build_release_workbook:308`, whose Issues sheet is the
QC audit's surface), `diagnostics.zip` (`build_diagnostics_zip:597`), and `release.json`
(`release_payload_bytes:503`).

**`concepts_xlsx` is ALIASED, not retired.** `build_dual_output` returns it
(`assessment_workbook.py:1003`), `assessment_release_service.py:369` writes it as
`aegis_concepts.xlsx`, `:379` seals its sha256 into `workbook_hashes` and `:516` re-reads
and re-verifies it at publication. Retiring it would delete one of the two artifact hashes
the T7 publication-hardening invariant checks, and `api/build_assessments.py:375` already
serves it. So it stays — **demoted from a reviewer-facing file to the sealed internal
Concept-file projection**: no manifest entry points at it, Outputs 01/03 are served by
`build_release_bulk_import_workbook`, and its only jobs are the hash and the receipt.
Recorded here so nobody "tidies up the duplicate concepts workbook" and silently removes
half of the hash verification.

**S8's "One renderer" is corrected: the composition is SHARED, not MOVED.** S8 said the
`"Name (machine ID)"` composition and the `topic_concept_labels` roster "move in from
`writer.py`". Read literally that strips the identity pair from `writer._front_bands`
(`writer.py:703-720`) and `writer._concept_field_value:611` — i.e. from the append-only
bulk-import database of record, and from Outputs 01/03, which are exactly the exports T1's
correction 1 argues must carry the pair. **Decision: both live in the shared
`app/services/identity.py` (created in **S2** with `topic_identity`, extended in **S4** with
these two — T4-9(a))** — `identity.titled(name, machine_id)` and
`identity.topic_concept_roster(topic, export_scope)` — and BOTH renderers call it.
[verified] the import direction already exists in both places:
`writer.py:27` does `from ..services import directory`,
`reader.py:25` does `from ..services import directory, katex_rules`, and
`assessment_workbook.py:42-43` does `from ..services import assessment_profile` /
`assessment_release as rel`. One definition, two callers, neither stripped.

**But that direction is only half the graph, and the other half is a real cycle — verifier
V4, reproduced.** C9 has `bulk_import.writer` import `services.identity`, while T4-1/T4-8
take the mint ordering from `writer._source_order_key`, which would have
`services.identity` import `bulk_import.writer`. **The direction that breaks it is named in
T4-9(b): the ordering key moves INTO `identity.py` (`identity.source_order_key`) and the
writer imports it, never the reverse**, and `topic_concept_roster` takes the export scope as
a parameter rather than importing `writer.ConceptExportScope`. [verified]
`app/bulk_import/__init__.py` imports nothing from `app.services` (its only module-level
imports are `__future__` and `re`), so `services.identity → bulk_import` is a leaf edge and
the graph stays acyclic. A subprocess-import pin enforces it.

**The manifest must show all four in one place, or map P16's root cause survives.**
[verified] today the concepts manifest carries four Post entries
(`build_concepts_release_manifest.release_artifact_entries:27-79`) and four Pre entries
(`_pre_entries:80-155`), and **none of the eight is an Output 01/02/03/04 artifact**: they
are the review workbook, the diagnostics zip, the release JSON and the upload action. The
reviewer never sees the four outputs enumerated anywhere. Closing it is split across two
slices for an honest reason:

* **S3 adds `release_bulk_import` / `pre_release_bulk_import`** (Outputs 01 and 03). These
  need only `job.id` and a route that already exists.
* **S6 adds `release_master` / `pre_release_master`** (Outputs 02 and 04). These need the
  lane's `AssessmentRelease` row id, resolved through `AssessmentRelease.job_id`
  (`models.py:413-414`, indexed) plus the `lane` column S6 introduces — so they cannot
  land before S6. Until a release row exists the entry is present and `disabled` with a
  stated reason, never absent, because an absent entry is the defect.

**And the manifest ORDER follows the owner's numbering (OD4).** The reviewer's list reads
01 Pre Concept → 02 Pre Master → 03 Post Concept → 04 Post Master, then the three evidence
artifacts. That is a change to the ORDER in which entries are appended inside the four
manifest blocks, not to any `kind` string (which carry no digits — T14's measurement
above) and not to any route. The lazy module monkeypatches the eager one
(`build_concepts_release_manifest.install():158-159`), so the order must be identical in
both or the two disagree the moment `install()` runs; a regression
(`test_the_manifest_lists_the_four_outputs_in_the_owner_numbering`) asserts the emitted
`kind` sequence by function reference across all four blocks.

By the end of S6 the manifest enumerates all four outputs plus the three evidence
artifacts, in both lanes, in the owner's order, and P16 is closed at its root rather than at
its symptom.

---

### T15 · The trigger — OWNER RULING OD1, folded in as a DECISION (this was OR1)

**Decision. The Build Concepts run builds ALL FOUR outputs.** §7:541 ("**One action: "Build
Concepts."** … One run produces all four outputs (Q3)") is taken **literally**, which is what
the owner has ruled and what Q3 already says. There is no option (ii), no fallback, and no
mailbox to wait on. **Every chapter pays the assessment-lane model spend** — Open/Specific,
marking, level verdicts, variant clustering, group descriptions and the Master Refiner —
**including chapters the reviewer discards after reading the Concept File.**

**What lands.** [verified] `api/build_assessments.py:436` and `:478` are the only two
external callers of `run_release_for_job` / `run_pre_release_for_job`. Under this ruling the
run itself calls them **after the freeze**, once per lane, and the two API routes stay as
the reviewer's explicit re-build path against the already-frozen release row — they are not
deleted, because a re-build against a frozen row is Rule G's idempotent second act, not a
second trigger. The S6 freeze contract is unchanged: the freeze still happens in the run,
and Outputs 02/04 are now built inside it rather than on a later click.

**T15-1 · THE CALL SITE, named — because "the run calls them" is not a call site and an
earlier draft left OD1 with no function to build it in.**

```
app/services/build_concepts_release_contract.py
def _run_generation_release(original, db, job_id, target_chapter_id, *args, **kwargs):
    owner_sub = kwargs.get("owner_sub")
    staged = _stage_generation_release(          # ← today's whole body, moved verbatim
        original, db, job_id, target_chapter_id, *args, **kwargs)
    _build_master_siblings(db, job_id, target_chapter_id, owner_sub=owner_sub)
    return staged
```

[verified] that module already owns the run's release lifecycle and has **four** exits, each
a `stage_release` / `_stage_pre_sibling` pair, each ending in `return staged`:

| exit | `stage_release` | `_stage_pre_sibling` | when |
|---|---|---|---|
| clean run, rows captured | `:209` | `:232` | `_release_after_result`, `captured` truthy |
| clean run, no capture | `:243` | `:254` | `_release_after_result`, checkpoint released |
| generation raised, rows captured | `:301` | `:323` | `_run_generation_release`'s `except` |
| generation raised, no capture | `:336` | `:347` | same `except` |

**The choice, with the reason.** The call goes in `_run_generation_release` (`:268-370`),
**once**, on the single tail all four exits converge on — not four times beside
`_stage_pre_sibling`. Three reasons, each checkable: (a) all four exits reach it, including
the two FAILURE exits, and those are exactly where "one run, four outputs" would otherwise
silently degrade to two on the runs that most need the evidence — `_stage_pre_sibling`'s own
docstring (`:153-165`) makes precisely this argument for the Pre lane and it applies
unchanged one lane further; (b) it is **outside** the `_RELEASE_MODE` / `_RELEASE_CAPTURE`
context vars, whose `finally` (`:365-367`) has already run, so the assessment lane cannot be
intercepted by the deposit interceptor `_install_deposit_interceptor:462-476` wires; (c) one site, so a
fifth exit added later inherits it rather than being forgotten. Today's body becomes
`_stage_generation_release` **verbatim** — no exit, no `stage_release` argument and no
`_stage_pre_sibling` call moves — which is what keeps this a trigger change and not a
redesign.

**T15-2 · CONTAINMENT — and this is a Q13 requirement, not a robustness nicety.** The two
concept outputs are finished and durable by the time `_build_master_siblings` runs: the
staged payload is written, `release-bulk-import.xlsx` already renders for both lanes. An
exception escaping the assessment-lane call would propagate out of `generate_post_learning`
and take them with it — a mid-run halt after the model budget is spent, which CLAUDE.md:25-33
and Q13 forbid. So:

* **`_build_master_siblings` never propagates.** It wraps each lane's call in
  `try/except Exception`. Deliberately broad, and the reason is stated rather than
  apologised for: an enumerated tuple is a list that goes stale, and the one thing that must
  never happen is a NEW exception type costing Outputs 01/03. [verified] the types it will
  actually see today are `assessment_release_run.ReleaseRunError` (`:106`) with its
  subclasses `GeneratedLaneError` (`:110`) and `SourceQuestionLeak` (`:114`);
  `assessment_release_service.UploadRefused` (`:62`);
  `assessment_release_snapshot.SnapshotError` (`:19`, already converted to `ReleaseRunError`
  at `assessment_release_run.py:1392-1393`); `assessment_workbook.WorkbookRenderError`
  (`:70`); and `phase3.premap.PreExtractionError` (`:256`).
* **It is caught, not swallowed.** Each failure becomes a named
  `assessment_lane_unavailable` **release issue on that lane's CONCEPT release**, appended to
  the staged payload's existing `issues` ledger (`build_concepts_release.py:1464` Post,
  `:2035` Pre — the transport T10-0 already uses), carrying the lane, the exception class
  name, its message and the staged release version. It rides the terminal `release_result`
  NDJSON event (S3), so the reviewer sees it in the console on the run that produced it.
* **And that append is legal, which needs saying because D8.5 forbids the neighbouring
  thing.** D8.5's prohibition is on a **projection-time** discovery mutating a staged payload
  during a GET; this append happens inside the run, after `_stage_generation_release` returned
  and before the terminal result is emitted — no download, no publication act. It is also
  seal-safe, [measured]: `assessment_release_snapshot.source_release_sha256:57-80` hashes
  exactly thirteen payload keys — `version`, `target_chapter_id`, `learning_kind`,
  `source_book`, `filename`, `source_document_hash`, `directory_metadata`, `records`,
  `question_task_inventory`, `chapter_meta`, `target_identity`, `mined_types`,
  `type_case_rows` — and **`issues` is not among them**, so appending moves no seal, no
  `machine_id` and no `concept_snapshot_sha256`. The helper touches `issues` and nothing
  else; it is not a re-stage and must never be implemented as one.
* **It is an ISSUE, not a structural defect and not a Fixer decision.** Not a defect,
  because the concept payload is not corrupt — a later lane simply did not produce, and
  naming it a defect would refuse the CONCEPT lane's database write for a fault in a
  different lane, which is Rule G's two-publication separation collapsed and the precise loss
  this item exists to prevent. Not a Fixer decision, because nothing here is a judgment about
  what the source means; it is a mechanical record that a later stage failed.
* **What the release state then reads.** The concept release's `release_state` is
  **unchanged** — it stays whatever the concept payload earns (Ready / Released-with-warnings
  / Diagnostic), and the concept-lane database upload stays open if it was open. The
  ASSESSMENT release for that lane does not exist, so `_release_summary`
  (`api/build_assessments.py:305-318`) has no row to summarise and the manifest's
  `release_master` / `pre_release_master` entries stay **present and `disabled`**, with the
  recorded issue's message as their disabled reason. That is the genuine transient case S6
  already reserves that shape for; T15-2 gives it a producer and a name.
* **The reviewer's recovery is the route that already exists.** `api/build_assessments.py:436`
  / `:478` re-run the lane against the frozen row — Rule G's idempotent second act — so the
  failure costs a click, never a chapter.

**Regression: `test_an_assessment_lane_failure_does_not_cost_the_concept_outputs`.**
Monkeypatch `assessment_release_run.run_release_for_job` to raise `ReleaseRunError`; run
Build Concepts; assert (i) no exception escapes `generate_post_learning`, (ii)
`release-bulk-import.xlsx` returns 200 for **both** lanes and carries the same rows as a
control run with the lane healthy, (iii) the concept release's `release_state` is identical
to the control's, (iv) an `assessment_lane_unavailable` issue names the lane and the
exception class, (v) the `release_master` / `pre_release_master` manifest entries are present
and `disabled` with that reason, and (vi) the concept-lane database upload is still open.
Parametrised over the four exits by forcing each one, so a later refactor that returns from a
fifth place fails here. Landing slice: **S6**, with the in-run call.

**Everything the previous round made conditional on OR1 is now unconditional.** The
"Blocked on OR1 only for the TRIGGER, and it has a fallback" bullet is removed from S6; the
S6 manifest entries for Outputs 02/04 are no longer "present-and-`disabled` until the lane
has a release row" *because no ruling arrived* — they keep that shape only for the genuine
transient case, which T15-2 now names, produces and tests.

**The consequence the owner should expect, stated plainly rather than softened:** provider
spend per chapter rises by the whole assessment lane, and it is spent **before** the
reviewer has read the Concept File, so the spend on a discarded chapter is not recoverable.
At R7's "thousands of chapters" that is the single largest cost term in step 8, and this
repo cannot measure it — the per-chapter assessment-lane call count depends on concept
count, group count and question count, none of which step 8 may derive from volume
(CLAUDE.md:17-18). It is the owner's ruling and it is implemented as given; what step 8 owes
in return is that the spend is **visible**: the run's `release_result` and the
`AssessmentRelease.provider_identity` written by S6 (T2) record the provider/model/prompt
identity of the assessment lane, so the cost per chapter is measurable from the first real
run instead of argued about.

### T16 · Questionless concepts in the Master — OWNER RULING OD5, folded in as a DECISION

**The owner's rule, verbatim:** the Master "should contain everything (including the
concepts that have no questions because some concepts will have no questions, so they should
appear in the end till concepts columns)".

**Decision: one tail row per questionless concept — concept columns filled, Group and
Question columns blank, placed at the END of the Objective sheet.**

**What the code does today, which is different, [verified] on this tree.**

1. `assessment_release_service._complete_required_shells:195-249` gives **every** concept
   `BG01/IG01/AG01` shells ("any exact required identity absent from the payload gets its NA
   shell so questionless concepts survive into the Master"), via
   `grouping.required_shells`, appending each to `snapshot["groups"]`.
2. `assessment_workbook.render_master_file`, after the question rows, emits *"every created
   group not otherwise represented by a question row — required empty shells included —
   … once on the Objective sheet (the catalogue carrier), Question band blank"*
   (`:430-442`), and each such row goes through `_group_record_fields`, so **the Group band
   is FILLED**.

So a questionless concept today produces **three rows with the Group columns populated**,
where the owner asks for one row stopping at the concept columns.

**The gold cannot settle this and must not be cited as if it could.** [measured] on
`backend/data/Testing/reference_bulk_import/grade6_science.xlsx`: Objective **2** populated
rows, Descriptive **6**, Subjective **0**, and **zero** rows with a blank `question_label` —
i.e. zero questionless concepts anywhere in the accepted corpus. The owner's rule is the
only authority here, which is exactly the situation the governing steer describes.

**The change.**

* `render_master_file`'s catalogue loop (`:430-442`) emits a group row only for a group
  whose concept has **at least one placed question row**. For a concept with none, it emits
  **exactly one** row built from `_bands_record(entry)` with `concept_question_labels`
  blank and **no `_group_record_fields` update at all** — so every column from the Group
  band onward is empty — and it is appended **after** every other row, in the snapshot's own
  concept order. Deterministic mechanics: it counts placed rows per `concept_key`; it judges
  nothing.

**What it costs, and how each cost is paid.**

* **`validate_master_file`'s "every created group appears in the Master" check breaks on
  every questionless concept, and must be scoped.** [verified] `expected_group_keys =
  set(groups_by_key)` (`assessment_workbook.py:767`) and
  `for group_key in sorted(expected_group_keys - seen_group_keys): errors.append(f"group
  missing from Master: {group_key!r}")` (`:947-948`). **Exactly this:** `expected_group_keys`
  becomes the subset of `groups_by_key` whose `concept_key` has ≥1 placed question row —
  the same count the renderer used — so the validator demands a Group row precisely where
  the renderer emits one. The adjacent concept check (`:941-946`, "every concept
  (questionless included) … appears") is **unchanged and now load-bearing**: the tail row is
  what satisfies it, so a regression that deletes the tail row fails there rather than
  silently.
* **The three shells STAY in the payload.** They are group structure
  (`group_key`, `group_type`, `group_sequence`, the friendly name) that the grouping lane,
  the release identity and step 9's edit surface read, and dropping them from the payload to
  match the workbook would delete structure to fix a rendering question. `_complete_required_shells`
  is **not** changed.
* **The payload and the workbook therefore differ, and that difference is RECORDED, never
  silent (R4).** `render_master_file`'s issues manifest gains
  `questionless_concepts: [{concept_key, concept_machine_id, concept_title,
  shell_group_keys}]` beside `unplaced` / `placed_questions` / `groups` /
  `group_provenance` (`:444-449`). It is a **record**, not a defect and not a flag: a concept
  with no questions is a legitimate outcome, so it blocks nothing, warns nothing, and
  changes no release state. It exists so that "the payload holds three groups the workbook
  does not show" is written down where the reviewer and the audit can both see it, and
  `release_qc` lists it as an informational item.
* **The Concept Files are unaffected** — they never emit Group columns at all (T17).

* **There is a SECOND read-back that enforces the same invariant, and it must be scoped in
  the same commit.** [verified] `assessment_master_refiner._release_readback` parses the
  rendered Master (`:611`), rebuilds `group_rows` from `group_provenance` (`:616-637`) and
  errors at **`:673-682`** — `"refiner-readback: group {group_key!r} has no rendered Master
  row"` — for every group in `payload["groups"]` with no rendered row. The three shells do
  not reach it (they live on `snapshot["groups"]`, not the payload), but a **real** payload
  group whose concept ends with zero placed rows does. `:673-682` takes the **same scoping**
  as `validate_master_file:767`: a group is expected in the Master only when its concept has
  at least one placed question row. Not "a group with no questions" — that would stop
  catching a genuinely missing catalogue row on a concept that does have questions.

**Slice, functions, regressions.** **S8**, the renderer slice, which already rewrites this
function. Changed: `assessment_workbook.render_master_file:298` (the catalogue loop
`:430-442` plus the new tail loop and the new issues key), `assessment_workbook.validate_master_file`
(`:767` and `:947-948`), **`assessment_master_refiner.py:673-682`**. Unchanged:
`assessment_release_service._complete_required_shells:195-249`,
`grouping.required_shells`. Regressions:
`test_a_questionless_concept_is_one_tail_row_stopping_at_the_concept_columns` (one row,
every Group- and Question-band cell empty, and it is the LAST row on Objective);
`test_the_master_validator_accepts_a_questionless_concept` (fails today —
`validate_master_file` reports three "group missing from Master" errors);
`test_the_questionless_shells_stay_in_the_payload_and_the_difference_is_recorded` (the three
`group_key`s are still in `snapshot["groups"]` and are named in
`issues["questionless_concepts"]`);
`test_a_concept_with_questions_still_emits_its_unrepresented_group_rows` (the negative
control that pins where the line is);
`test_the_master_refiner_readback_accepts_a_questionless_concept` (the second read-back —
`assessment_master_refiner.py:673-682` — reports no `refiner-readback` error, which it does
today whenever a real group's concept ends with no placed rows).

### T17 · The Concept File's shape — OWNER RULING OD6, made EXPLICIT

**The owner:** "In Concepts File, it should be filled till concepts related columns
(starting from first)."

**B1 · AN EARLIER DRAFT ANSWERED THIS RULING AGAINST THE WRONG FUNCTION, AND THE
CONSEQUENCE WAS A FALSE STATEMENT ABOUT THE OWNER'S OWN ARTIFACTS.** It verified
`assessment_workbook.render_concept_file:180-192` and `assessment_workbook._new_workbook:
116-121`. But this spec's own T14 table and D8/Q21 bind Outputs 01 and 03 to
`build_concepts_release_files.build_release_bulk_import_workbook`, and declare
`render_concept_file`'s product — `aegis_concepts.xlsx` — **"NOT an output: it is the sealed
Concept-file projection"**. There are two `_new_workbook`s and two concept-file writers, in
two modules, with the same names; the draft conflated them. Re-pointed here.

**The two builders, each opened and each executed.**

| | Outputs **01** and **03** — what the reviewer downloads | `aegis_concepts.xlsx` — the sealed projection |
|---|---|---|
| entry | `build_concepts_release_files.build_release_bulk_import_workbook:44-107` | `assessment_workbook.render_concept_file:180-192` via `build_dual_output:974` |
| after the DB upload | `bi_writer.write_concepts_workbook(db, result_ids)` — returned at `:79`, defined `writer.py:1213-1257` | n/a |
| before the DB upload | `bi_writer._new_workbook()` (`:85`) + `bi_writer._concept_to_row` (`:95`) over the transient hierarchy | n/a |
| row builder | `writer._concept_to_row:811-832` | `_row_values("Objective", record)` (`:191`) |
| sheet set **today** | `['Objective ', 'Subjective', 'Descriptive', 'Doc Link <> Each fields ']` | `['Objective', 'Descriptive', 'Subjective']` |
| status after step 8 | **an output** (T14, D8/Q21) | evidence — its sha256 is re-verified at `assessment_release_service.py:379`/`:516` |

**Both builders survive step 8** (T14: `concepts_xlsx` is aliased, not retired, because
retiring it deletes half the publication-hardening hash check), so the rule is stated for
each. **The owner's ruling governs the FOUR OUTPUTS**, so it binds the first column first
and the second only by the requirement that the two not diverge.

**Rule for Outputs 01 and 03 — the ones the owner named.** [verified by reading the call
chain] `_concept_to_row:811-832` builds `_front_bands(concept, topic,
include_group_columns=False, …)` — Chapter band (6) + Topic band (6) + `CONCEPT_FIELDS` —
then `row += [""] * (expected - len(row))` and `return row[:expected]` (`:830-832`). So
**every column from the Group band onward is blank by construction**, not by an explicit
blanking pass, and `_concept_field_value:632-640` returns `""` for `basic_groups` /
`intermediate_groups` / `advanced_groups` under `include_group_columns=False`.
`concept_question_labels` is not in `CONCEPT_FIELDS` at all — it is the first field of
`OBJECTIVE_GROUP_FIELDS` (`__init__.py:72-75`) — so it falls beyond `len(row)` and is blanked
by the pad. **The owner's shape is therefore already true of Outputs 01/03 today for the
COLUMNS, and false for the SHEETS.**

**And that is the connection the earlier draft never made: the sheet-set half of this rule is
delivered by M1, in S7, not by S8.** [measured, executed on this tree]

```
format workbook (the authority)   ['Objective', 'Descriptive', 'Subjective']
writer._new_workbook              ['Objective ', 'Subjective', 'Descriptive',
                                   'Doc Link <> Each fields ']   ← Outputs 01/03 TODAY
assessment_workbook._new_workbook ['Objective', 'Descriptive', 'Subjective']
```

Outputs 01 and 03 carry **FOUR** sheets, including a Doc Link sheet the schema of record does
not have (`writer._new_workbook:1140-1154`, the Doc Link created at `:1146`), and the other
two in the wrong order. The statement "the other two sheets exist and are empty — keep it"
is **false of the real Outputs 01/03 today**; it becomes true only when **T12/M1 in S7**
stops `writer._new_workbook` creating the Doc Link sheet and takes the registry's sheet
order. T17 is therefore not "a recorded contract with no code change": it is a recorded
contract whose sheet half **is** a code change, and it belongs to S7's commit.

**Rule for `aegis_concepts.xlsx` — the sealed projection.** It reaches the same shape by a
different mechanism, and it must be kept at the same shape. [verified]
`render_concept_file:180-192`:

* it writes **only the Objective sheet** (`ws = wb["Objective"]`, `:180`);
* for every concept row it blanks `basic_groups`, `intermediate_groups`, `advanced_groups`
  and `concept_question_labels` (`:186-190`) — explicitly, with the comment *"Group and
  Question bands stay blank … groups do not exist in Output A"*;
* it emits the **full 67-column** Objective row via `_row_values("Objective", record)`
  (`:191`), so the Group and Question bands are present as columns and empty as values;
* [verified] `assessment_workbook._new_workbook:116-121` creates every sheet in `SHEET_ORDER`
  (which is `MANIFEST["sheet_order"]`, `:49`) and calls `_write_headers` on each — so this
  file already has the three sheets in the authority's order and no Doc Link sheet.

**Why it must not be allowed to diverge:** its sha256 is sealed into `workbook_hashes` at
`assessment_release_service.py:379` and re-read and re-verified at `:516`, so it is the
half of the T7 publication-hardening invariant that covers concept content. A projection
whose shape drifts from the artifact it projects is a hash that proves nothing.

**The band geometry this "till concepts columns" refers to, measured from the committed
format workbook** (T3.1b), so nobody has to re-derive it:

| sheet | Chapter | Topic | **Concept (ends at)** | Group | Question |
|---|---|---|---|---|---|
| Objective (67) | 1-6 | 7-12 | 13-**22** | 24-30 | 31-31 |
| Descriptive (374) | 1-6 | 7-12 | 13-**22** | 23-29 | 30-70 |
| Subjective (144) | 1-6 | 7-12 | 13-**23** | 24-30 | 31-144 |

So "filled till concepts columns" is **columns 1-22 on Objective**, plus col 23
(`concept_source`, unbanded on Objective — T3.5), with 24-67 empty.

**The three explicit statements the spec was missing, each now decided:**

1. **Both Concept Files have this shape.** Output 01 (Pre) and Output 03 (Post) are the same
   builder — `build_release_bulk_import_workbook(db, job, lane=…)` (T14) — differing only in
   which staged slot they read (`:65-77`), so the rule is one rule for both. It is recorded
   because T1's correction 1 migrates the concept band and a migration is where an unstated
   shape silently changes.
2. **The workbook carries the authority's THREE sheets, in the authority's order, and no
   Doc Link sheet — and for Outputs 01/03 that is a CHANGE, delivered by T12/M1 in S7.**
   [measured] `writer._new_workbook` emits four sheets today (`'Objective '`, `'Subjective'`,
   `'Descriptive'`, `'Doc Link <> Each fields '`), against the committed format workbook's
   `['Objective', 'Descriptive', 'Subjective']`. **Decision: keep the two data-less sheets,
   delete the Doc Link sheet, adopt the authority's order.** Keeping the empty Descriptive
   and Subjective sheets makes the Concept File the same workbook shape as the format
   workbook and the Master File, which is what makes the reviewer's four files one family
   and what lets a single layout identification (`identify_sheet`, T6) read all four;
   deleting them would make the Concept File the one artifact whose sheet set differs, for
   no gain. The Doc Link sheet is the opposite case — it is in no layout the registry
   describes, so a workbook carrying it cannot identify to `sop-mes-1` at all. `aegis_
   concepts.xlsx` already satisfies this (`assessment_workbook._new_workbook:116-121` walks
   `SHEET_ORDER`); Outputs 01/03 do not, and S7 is where they do.
3. **The four blanked group-label columns stay blanked** (`basic_groups`,
   `intermediate_groups`, `advanced_groups`, `concept_question_labels`) — they are inside
   the concept band by position but they are group AGGREGATES by meaning, and Outputs 01/03
   have no groups. On Outputs 01/03 this needs no code: `include_group_columns=False`
   (`writer._concept_field_value:632-634`) blanks the first three and the pad at
   `_concept_to_row:830-832` blanks the fourth. `topic_concept_labels` is **not** in that
   list and is **filled**: on Outputs 01/03 `_front_bands:659-718` already computes it at `:688` from the
   topic's concepts, and on `aegis_concepts.xlsx` T3.7 is what makes the snapshot builders
   stop hard-coding `""`. `keywords` and `related_concepts` are content, not forced blanks
   (T3.2/T3.3, OD3 — `related_concepts` filled on Pre rows, blank on Post rows).
   `concept_source` is filled (`_concept_field_value:641-642` returns `concept.sources`
   unconditionally).

**Slices and pins — split, because the two halves fail today for different reasons in
different commits.** The earlier draft put one pin,
`test_the_concept_file_is_filled_to_the_concept_band_and_no_further`, in **S8** — the
renderer slice — asserting against a builder S8 does not touch. Corrected:

* **`test_the_concept_files_are_filled_to_the_concept_band_and_no_further` → S7**, the slice
  that owns `writer._new_workbook` and `_concept_to_row`. It runs
  `build_release_bulk_import_workbook` for **both** lanes and asserts: three sheets, in the
  format workbook's order, **no Doc Link sheet**; only Objective carries data rows; every
  column from the Group band onward empty on every row; `topic_concept_labels` and
  `concept_source` non-empty. It **fails today on the sheet set and the sheet order** — which
  is the point, and which is why it cannot live in S8.
* **`test_the_sealed_concept_projection_keeps_the_same_shape` → S8**, where the profile is
  threaded through `render_concept_file:180` and where T3.7 fills `topic_concept_labels` in
  the snapshot builders. It asserts the same shape on `aegis_concepts.xlsx` and, in the same
  test, that its sheet set equals Output 03's — the anti-divergence pin the sealed hash
  depends on. It **fails today on `topic_concept_labels`** (T3.7's defect).
* **`keywords` / `related_concepts` non-empty** is asserted by S8's existing
  `test_keywords_ship_filled_on_all_four_outputs` and
  `test_pre_related_concepts_carries_the_resolved_post_machine_ids`, not here: the COLUMNS
  arrive with the layout in S7 and the VALUES in S8, so asserting them in S7 would fail for
  the wrong reason.
* **S8 must not change the shape** while it threads the profile through
  `render_concept_file:180` — that prohibition is unchanged and is what the second pin
  enforces.

---

## 3. THE SLICES

Eleven commits, one PR. Each leaves the suite green. Nothing published moves before its
identity is stable. Baseline to hold: **2324 passed, 6 xfailed**. All 11 goldens stay
byte-identical throughout.

### S1 — Frozen-core listing. ALONE, FIRST.

* **Changes:** `app/services/instruction_architect.py` (:98-103 comment deleted, the
  `_FROZEN_CORE_PHASE3_CONSTANTS` tuple extended by six in doc order),
  `tests/test_instruction_architect.py` (+1 test).
* **Atomic with:** nothing. It must be bundled with nothing.
* **Regression:** `test_the_six_post_lane_prompts_are_frozen_core` — the six `phase3:`
  keys are in `{e["key"] for e in _frozen_core_entries()}`, the count is 48, and
  monkeypatching `phase3.prompts.PLACE_SYSTEM` changes `empty_set_sha256()`. Pin the
  BEHAVIOUR, not the literal hash. Commit message records
  `88a68507…332eff → 039bd4ca…7c930` and names the append order; the three pre-spend pause
  suites are diffed deliberately.
* **NOT in it:** any schema, identity, renderer or publication change.

### S2 — The reader's fail-closed header gate + the layout registry.

Placed second because it closes a live R4 silent-corruption hole on an authenticated POST
endpoint (`api/data.py:35`), and because every later slice that changes a layout depends on
identification existing first.

* **Changes:** NEW `app/bulk_import/layouts.py` — with `canonical-current`,
  `canonical-no-question-text` and `canonical-legacy-concept-band` written as **FROZEN
  LITERAL transcriptions** (T6, including the 10/11/12 scalars and the `range(10)` block
  count), and **`sop-mes-1` read at import time from the COMMITTED FORMAT WORKBOOK
  `backend/data/Testing/reference_bulk_import/bulk_import_format.xlsx`, with
  `assessment_workbook_template.json` compared against it and the workbook winning on any
  disagreement (`layout_manifest_drift`) — T3.1b**;
  **NEW `app/services/identity.py` carrying `topic_identity()` and nothing else** — see
  below;
  `app/bulk_import/reader.py` (:33-60 deleted, :79-134 rewritten name-addressed with
  derived block counts, :207-258 gated, :288-304 chapter identity, **:308-310 topic
  identity gains the lane and the shared `identity.topic_identity()` per T6.4**, :364-368,
  :506);
  `app/bulk_import/writer.py` (:56 deleted, :244-251 identify); `app/api/data.py`
  (`WorkbookLayoutError` → 422).
* **`app/services/identity.py` is CREATED HERE, not in S4 — verifier V3, and the previous
  round could not have landed.** S2's reader fix needs `topic_identity()`; T4-6 put it in a
  module S4 creates; S2 is slice two. As written, S2 either duplicated the normaliser — the
  second-copy defect T3.1 forbids — or could not land at all. **Resolution (T4-9(a)): S2
  creates `identity.py` containing only**

  ```
  def topic_identity(title: str) -> str:
      return bi.normalize_question_text(bi.strip_topic_title(title))
  ```

  a pure string normaliser over two functions that already exist, needing no column, no
  model change and no mint. **S4 EXTENDS the same module** with the mint, the two cell
  composers and `source_order_key`. [verified] `reader.py:25` already does
  `from ..services import directory, katex_rules`, and `app/bulk_import/__init__.py` imports
  nothing from `app.services`, so the new import adds no cycle (T4-9(b)).
* **Atomic with:** `tests/test_sources.py:44-48` and `tests/test_question_text.py:122-126`
  — their one-column stub headers fail the gate the instant it exists, at
  `tests/conftest.py:31`, taking the whole suite down. And `writer._sheet_concept_fields`
  must die with `reader._concept_fields`: they are twins deciding the same column geometry
  on the same file under the same `output_workbook_lock()`.
* **Regressions:** `test_reference_workbook_imports_by_name` — feed
  `grade6_science.xlsx` to `reader.import_workbook` and assert the first populated
  Descriptive row stores `question_label == "06MSSC_Measur_PL_T02_
  LimitationsOfHandSpanM Q01"` and `question_category == "Very Short Answer Questions"`
  (the exact two values that are wrong today); the same for the other two fixtures;
  `test_an_unrecognised_header_refuses_the_whole_import` (`models.Chapter` count
  unchanged, POST → 422); `test_a_trailing_space_sheet_name_is_a_different_layout_not_a_
  skip`; `test_canonical_current_and_legacy_workbooks_still_import`;
  `test_reader_answer_blocks_are_read_by_name` (a reference Subjective row with
  `answer_type_15` populated reads back 15 answers — the fixture is synthetic and the test
  says so, because [measured] all three gold Subjective sheets have zero data rows);
  `test_two_chapters_whose_codes_collide_are_not_merged` (the measured
  `10CBSS_TheRiseOfNat` pair, asserting the CHAPTER SET, not one cell);
  `test_a_pre_topic_and_a_post_topic_with_one_title_import_as_two_topics` (T6.4 — fails
  today: `reader.py:308`/`:309-310` carry no lane, so the second lane's concepts re-parent
  under the first lane's topic);
  `test_the_canonical_layout_entries_are_frozen_literals_not_live_constants` — the
  registry's canonical `fields_by_kind`/scalars equal tuples written literally in the test,
  and the test states that after S7 they must NOT equal `bi.FIELDS_BY_KIND`;
  **`test_the_committed_format_workbook_and_the_template_json_agree_field_for_field`**
  (T3.1b — sheet order, all three field lists, and zero data rows below row 2; it passes
  today, [measured], and it is what keeps a hand-edit to either copy from shipping a
  mis-banded workbook);
  **`test_the_registry_prefers_the_workbook_and_records_the_drift`** (monkeypatch one field
  name in the loaded JSON copy: the entry still equals the workbook and a
  `layout_manifest_drift` defect names the sheet, the index and both values);
  **`test_a_pre_topic_and_a_post_topic_with_one_title_import_as_two_topics` uses
  `identity.topic_identity`, not a local copy** — a pin that S2 did not fork the
  normaliser. **Every
  canonical-layout fixture in this slice is built from the frozen registry entry or a
  committed `.xlsx`, never from `writer._new_workbook`/`_write_headers`** — that writer
  moves in S7 and a fixture built from it would follow the target and prove nothing.
* **NOT in it:** the writer's emitted layout (S7), the runtime output workbook
  `config.BULK_IMPORT_OUTPUT` (S7), any
  profile threading, and the machine-id restore on import (T4-8 — `machine_id` does not
  exist until S4; S2 leaves the stripped tag available to it and nothing more).
  **`identity.py` gains nothing but `topic_identity` here** — no mint, no `titled`, no
  `topic_concept_roster`, no `source_order_key`; all four arrive in S4 (T4-9(a)).

### S3 — `release_result` symmetry + manifest surfacing + the frontend lane fix.

* **Changes:** `app/services/build_concepts_release.py:2090-2154` (collapse the two
  branches into one dict builder always emitting `lane`, `release_state`,
  `structural_defects`, `generated_question_count` and the lane-suffixed URLs; Post's
  suffix stays empty so no published URL moves); **all FOUR manifest blocks, named
  individually — the first draft named only the two Post ones and would have been a silent
  no-op on the Pre lane, the exact failure its own atomicity note warns about.** [verified]
  `grep -n '"kind":'`:

  | block | function | lines | lane |
  |---|---|---|---|
  | lazy (production) | `build_concepts_release_manifest.release_artifact_entries` | `:27-79` (entries at `:35, :46, :55, :64`) | Post |
  | lazy (production) | `build_concepts_release_manifest._pre_entries` | `:80-155` (entries at `:103, :117, :128, :139`) | **Pre** |
  | eager (pinned twin) | `build_concepts_release_files._pre_release_entries` | `:795-878` (entries at `:820, :836, :849, :862`) | **Pre** |
  | eager (pinned twin) | `build_concepts_release_files.release_artifact_entries` | `:881-935` (entries at `:890, :901, :910, :919`) | Post |

  All four gain `release_state` on their `database_upload` entry and all four gain the
  missing per-lane `release_bulk_import` entry — the two **Concept Files**, which are
  Outputs **01** (Pre, `pre_release_bulk_import`) and **03** (Post, `release_bulk_import`)
  under the owner's numbering (OD4/D9-Q22; the pair `{01, 03}` is the same under either
  numbering, so this citation did not move). The two **Master Files**, Outputs **02** (Pre)
  and **04** (Post), land in S6, which is where the release row that addresses them exists.
  **The order entries are appended in follows the owner's numbering** — Pre Concept, Pre
  Master, Post Concept, Post Master, then the three evidence artifacts — identically in the
  lazy and eager blocks, because `install():158-159` monkeypatches one over the other. No
  `kind` string changes: [measured] none of the eight existing kinds carries a digit, and
  the new ones must not either (T14).
  `frontend/src/api/client.ts:327-331`; `frontend/src/components/ConceptReviewPanel.tsx:70,
  :92`; `frontend/src/types.ts` `ActionableArtifact` + `DocumentUpload.tsx:729-749`.
* **Atomic with:** all four manifest blocks — the lazy module MONKEYPATCHES the eager one
  (`build_concepts_release_manifest.install():158-159`) and each file's own docstring says
  editing one alone is a silent no-op. Per-lane is the same trap one level down: S3's whole
  purpose is a per-lane entry, so touching only `release_artifact_entries` in each file
  changes nothing for Pre.
* **Correction to Cluster A, which scoped this too narrowly:** MC3 is right that the Post
  `release_result` dict IS the terminal `{"type":"result"}` NDJSON event
  (`build_concepts_release_api_contract.py:112` → `uploads.py:343-392` →
  `progress.py:146-160`, consumed at `RunConsole.tsx:97`) and that the Pre branch's five
  fields are the ones that reach nobody, because `_stage_pre_sibling`
  (`build_concepts_release_contract.py:145-180`) discards `stage_pre_release_from_run`'s
  return value. But `RunConsole.tsx:97-99` reads only `usageFromResult(evt.data)` and
  `DocumentUpload.tsx:729-749` renders only `kind/label/download_url/filename/size_bytes/
  action/disabled/requires_confirmation` — so adding the fields to the payload and the
  manifest **displays nothing**. [measured] `grep -rn "release_state\|structural_defects"
  frontend/src/` → 0 hits. The frontend type and renderer are in this slice or the stated
  defect is not closed.
* **Regressions:** `test_both_lanes_report_the_same_release_result_shape` (identical key
  sets); `test_the_post_release_result_carries_its_release_state`;
  `test_every_manifest_block_carries_the_bulk_import_entry_in_both_lanes` — parametrised
  over all four blocks by function reference, so a fifth block added later fails until it
  is listed; **`test_the_manifest_lists_the_four_outputs_in_the_owner_numbering`** (OD4 —
  the emitted `kind` sequence, asserted across all four blocks by function reference, is
  Pre Concept → Pre Master → Post Concept → Post Master, then the evidence artifacts; the
  Master entries are `disabled` placeholders until S6 fills them, and the ORDER is asserted
  from this slice on so S6 cannot quietly append them at the end);
  **`test_no_manifest_kind_string_carries_an_output_number`** (a pin: every `kind` emitted
  by all four blocks matches no digit, so the next ruling that renumbers outputs cannot
  break the frontend); and the proof that it
  is additive — `tests/test_pre_release_lane_wiring.py:583`, `:388` and `:463` stay green
  UNCHANGED, because `release_result` reads the payload and never writes it. Say that in
  the commit message; `:583` is the baseline that makes S6's deliberate payload change
  legible.
* **NOT in it:** any payload key change.

### S4 — Persisted identity: mint once, never re-derive.

* **Changes:** `app/services/identity.py` — **EXTENDED, not created; S2 created it with
  `topic_identity()` (T4-9(a))** — gains the minted id (`machine_id_for_topic`,
  `machine_id_for_concept`, lookup-then-mint per T4-3), the two shared cell composers
  `titled(name, machine_id)` and `topic_concept_roster(...)` that T14 keeps out of a single
  renderer, and **`source_order_key`, MOVED here from `writer._source_order_key:314-321`
  (T4-9(b) — leaving it in the writer while the writer imports `identity` is a module
  cycle).** [measured] all eleven sites move: the definition, nine uses inside `writer.py`
  (`:340, :349, :560, :686, :1068, :1069, :1230, :1231, :1236`) and the one external caller
  `build_concepts_release_files.py:92` (`key=bi_writer._source_order_key`). No alias is left
  behind in `writer.py`;
  `app/models.py` (`Topic.machine_id`,
  `Concept.machine_id`); `app/db.py:46-79` (two ADD COLUMN entries in `additions`) **and
  `db._backfill_and_normalize:113-164` (the T4-8 backfill for existing rows, in
  `writer._source_order_key` order, filling blanks only)**;
  `app/services/directory.py:325-330` (grade normalisation with the raw-token fallback and
  its flag); `app/bulk_import/writer.py:577-580, :600-602, :611, :680-682` (read the
  persisted id; stop calling `directory.topic_tag`/`concept_tag`; the composition itself
  moves to `identity.titled` and the writer CALLS it — it is not stripped, T14);
  **`app/services/generation.py:77-104` (`_topic_index` DELETED) and `:107-114`
  (`question_label` becomes `f"{identity.machine_id_for_concept(concept)} Q{n:02d}"` —
  same signature, the helper resolves its session with `object_session`),
  with its call sites `generation.py:331`, `:449`,
  `app/services/build_assessments.py:215-218` (`_legacy_machine_id` deleted), `:252`,
  `:264`, `:299`, `:728`, `:1106`** — T4-7, without which D1/Q14's Question pattern is
  half-built and this slice cannot close green;
  `app/bulk_import/reader.py:284-286, :308-345` (restore the minted id from the stripped
  tag per T4-8; `machine_id_conflict` / `imported_without_machine_id` notes);
  `app/services/build_concepts_release_publication.py:126-129, :149, :169` (stamp the id
  where `source_order` is already assigned); `build_concepts_release_files.py:217`
  (`source_order` reconciled to per-topic);
  `app/services/assessment_release_snapshot.py:133, :137` (read the persisted id);
  `app/services/tagging.py:43` (read the persisted `machine_id` instead of
  `question_label(concept,1).rsplit(" Q",1)[0]`);
  `app/services/build_concepts.py:576` (stop rewriting `t.topic_title`) and the shared
  `identity.topic_identity()` used by **all four** call sites — `build_concepts_release_files.py:185`,
  `build_concepts_release_publication.py:126`, `build_concepts._find_or_create_topic:569-585`
  and **`bulk_import/reader.py:308-310`**, which S2 already converted and which the previous
  round's three-site enumeration omitted (T4-6's table).
* **Atomic with:** the writer's exported tag, the publication's stored tag, **the
  question-label minter (T4-7) and the reader's restore (T4-8)** — they are the four legs
  of one round trip through `bi.strip_title_tag`. Half-landed, the workbook carries a tag
  the importer cannot match, or two live minters produce two label families for one
  concept and S5's max-scan and refuse branch both misfire against the wrong one.
  `directory.py:338-346` **stays** — it is still an input
  and `emit_canonical.py:86-95` depends on it; it loses its three writer call sites.
* **The ordering constraint the map and two clusters asserted is FALSE, and this is why S4
  can precede S7.** [measured] `assessment_release_snapshot.py:112` computes
  `release_sha = source_release_sha256(release)` **before** `transient_release_hierarchy`
  is called at `:115`, and `source_release_sha256:57-80` hashes payload keys only. Adding
  `related_concepts=`/`digicards=` to the transient `models.Concept` constructor
  (`build_concepts_release_files.py:199-214`) therefore **cannot** move `concept_key` or
  `machine_id`. The map's "ID minting must be rebuilt before or atomically with the column
  restoration" rested on a false premise; the correct reason for ordering S4 before S7 is
  that the tag must be stable before the layout carrying it moves, so the layout diff is
  attributable.
* **Regressions:** `test_identity_is_persisted_not_rederived` (publish, capture
  `Concept.machine_id`, rename `concept_title`, re-export, assert the id is byte-identical
  and the cell reads `"<new name> (<unchanged id>)"`);
  `test_release_hash_no_longer_reaches_identity` (flip one character of `concept_details`;
  assert `source_release_sha256` changed and every `concept_machine_id`, `group_key`,
  `question_label` unchanged); `test_two_chapters_whose_titles_share_no_ascii_mint_
  distinct_ids`; `test_every_minted_tag_round_trips_strip_title_tag` over a corpus
  including a Devanagari name; `test_two_topics_sharing_a_concept_name_get_distinct_ids`;
  `test_source_order_agrees_between_export_and_publication`;
  `test_grade_string_variants_mint_one_identity` **and**
  `test_kg_and_nursery_do_not_collapse_into_one_family`;
  `test_question_label_stays_within_its_column` (`len(label) <= 128` on the longest
  realistic chapter); `test_group_key_reads_the_persisted_machine_id`;
  `test_question_label_is_minted_by_exactly_one_function` (T4-7 — assert
  `generation.question_label(c, 3) == f"{c.machine_id} Q03"` and that `generation` no
  longer defines `_topic_index`);
  `test_a_legacy_row_with_source_order_zero_backfills_deterministically` (T4-8 — two
  topics with `source_order == 0` and ids 7 and 3: `T01` goes to id 3, `T02` to id 7, and
  a second `_backfill_and_normalize()` changes nothing);
  `test_a_positioned_row_sorts_before_every_source_order_zero_row`;
  **`test_identity_module_imports_no_bulk_import_writer`** (T4-9(b) — a fresh subprocess
  `import app.services.identity`, then assert `"app.bulk_import.writer" not in sys.modules`;
  the cheapest possible pin against the cycle being reintroduced);
  **`test_source_order_key_has_exactly_one_definition`** (`writer` exposes no
  `_source_order_key` after this slice, and `build_concepts_release_files` imports it from
  `identity`);
  `test_import_restores_the_minted_id_from_the_title_tag`;
  `test_an_imported_id_never_overwrites_a_persisted_one` (a `machine_id_conflict` issue is
  recorded and the DB value stands);
  `test_a_workbook_with_no_tag_imports_with_a_note_and_mints_on_first_use`.
  `tests/test_chapter_topic_quality.py:849-877` must stay green **UNCHANGED** — it is the
  real regression guarantee that published Post labels did not move; `:822-846` is
  **re-authored** (not deleted): it calls `g._topic_index` and `g.question_label`
  **directly**, so it can only be re-authored in the commit that replaces them, and it is
  re-authored to pin lane-scoped `T##` plus the `PL|PrL` token, keeping its docstring's
  reasoning — which named exactly this rebuild as the fix.
* **NOT in it:** the label-collision surface (S5), any column change.

### S5 — The label-collision surface.

* **Changes:** `app/services/assessment_release.py` (+`duplicate_question_labels` after
  `:435`, wired into `freeze_payload` beside `:508-513`);
  `app/services/assessment_release_service.py:612-613` (skip-and-record vs `UploadRefused`,
  keyed on `route_audit.release_uid`); `app/services/build_assessments.py:716-719`
  (max-scan); `app/services/assessment_release_run.py:1253-1266` (`_next_label_index`
  promoted to a shared helper); `app/db.py` (the duplicate scan + the partial unique index,
  never a bare create, never a startup halt).
* **Atomic with:** T5-2's skip-vs-refuse branch and T5-3's shared helper — split, the
  refuse branch fires on legitimate re-publications.
* **Must land after S4, and the reason is not ordering hygiene.** The shared
  `_next_label_index` scans `question_label.startswith(f"{base} Q")`, and the refuse branch
  compares against a global `Question` query. Both are correct only when `base` is the
  concept's persisted `machine_id` and there is **one** minter (T4-7). Landed before S4,
  the max-scan continues to key on a 6-character chapter slug that collides across
  chapters, and the refuse branch compares two label families against each other — phantom
  collisions on legitimate rows, real collisions missed. Grandfathered legacy labels are
  not a counterexample: they carry a different prefix, so they fall outside the family
  being scanned and R5 holds, with the `legacy_label_family` note naming both.
* **Regressions:** `test_two_candidates_sharing_a_label_block_the_upload_and_still_
  download` (readiness BLOCKED, both workbook downloads 200, upload refused);
  `test_republishing_the_same_release_skips_with_a_recorded_note`;
  `test_a_colliding_label_refuses_the_upload_instead_of_dropping_the_question` (question
  count unchanged); `test_a_generated_pre_question_with_no_source_qid_republishes_
  idempotently` (the case Cluster C's source-identity key could not express);
  `test_deleting_a_question_does_not_reissue_its_number`;
  `test_unique_index_is_created_on_a_clean_database`;
  `test_startup_survives_a_database_that_already_carries_duplicate_labels`;
  `test_blank_labels_do_not_block_the_partial_index`;
  `test_a_legacy_label_family_does_not_share_a_scan_with_the_minted_one` (a concept with
  one grandfathered label and one minted label: the max-scan returns 1 for the minted
  family, no label is reassigned, and a `legacy_label_family` note names both prefixes).
  `tests/test_data_io.py:166` stays
  green **UNCHANGED** — the reader turns a repeated label into a `QuestionTag` placement
  edge (`reader.py:410-436`) and a partial unique index does not break it.
* **NOT in it:** any change to the label FORMULA (S4's).

### S6 — The release core: one immutable row per lane per run.

* **Changes:** NEW `app/services/release_core.py`; `app/models.py` (`AssessmentRelease`
  gains `lane`, `layout_id`; `provider_identity` starts being written); `app/db.py`
  (`additions` gains the two columns); `app/services/assessment_release_service.py`
  (`create_release:251-310` persists lane + profile + provider identity; `_readiness:318`
  becomes an input to the projection and **KEEPS its read of
  `manifest["issues"]["unplaced"]` at `:326-330` verbatim**);
  `app/api/build_assessments.py:305-318` (`_release_summary` gains `release_state`, keeps
  `state`/`readiness` as diagnostics); `app/services/build_concepts_release.py`
  (`stage_release:1320`, `stage_pre_release:1918`, `force_release:2157` mints a version
  instead of overwriting); **the four manifest blocks named in S3 gain the
  `release_master` / `pre_release_master` entries (Outputs 02/04, T14), resolved through
  `AssessmentRelease.job_id` (`models.py:413-414`) plus the new `lane` column and served by
  `api/build_assessments.py:388`; present-and-`disabled` with a stated reason until the
  lane has a release row, never absent.**
* **Atomic:** a shared core with no caller is dead code, and a freeze seam with no core
  cannot compute state. Half-landed, `release_state` comes from two places at once — the
  two-vocabularies defect this slice exists to end.
* **S6 does NOT delete `_readiness:326-330`. The previous round said it did, and that was
  the R4 hole verifier V1 found.** [measured] that read is the **only** consumer of
  `manifest["issues"]["unplaced"]` in the whole tree
  (`grep -rn 'get("unplaced")\|\["unplaced"\]' app/ --include=*.py` → one hit), and `:537`
  `if readiness == BLOCKED` is the **only** thing refusing the assessment-lane database
  write. Deleting the read here — with the replacement verdict not landing until S8 —
  leaves a bisect window, and a merged PR state if S8 slipped, in which a candidate that
  appears in **no data row of any of the four outputs** publishes to the database. It is
  deleted in **S8**, in the same commit that lands
  `assessment_release.unresolved_question_homes` and wires it into `create_release`
  (T7.5, D8.5b). One commit, no window.
* **What S6 *does* change about readiness** is only that it stops being a second **public**
  truth (T1): `_release_summary` gains `release_state` in §4's three names, computed for the
  assessment release from `diagnostics["payload_errors"]`, and `state`/`readiness` stay as
  diagnostics beside it. The consequence of an unplaced candidate is unchanged in both
  directions through S6 and S8: DB write refused, every download available.
* **The trigger is RULED, not open (OD1/T15): the run builds all four outputs — and this
  slice names the FUNCTION, which the previous round did not.** The earlier "blocked on OR1,
  execute the fallback (ii)" bullet is **deleted** — there is no option, no fallback and no
  mailbox. The freeze still happens in this slice; after the freeze the run calls
  `run_release_for_job` / `run_pre_release_for_job` once per lane, which is the in-run half
  of what [verified] `api/build_assessments.py:436` and `:478` do today as the only two
  external callers. Those two routes stay as the reviewer's explicit re-build against the
  frozen row (Rule G's idempotent second act).

  **Changes, per T15-1/T15-2:** `app/services/build_concepts_release_contract.py` — today's
  `_run_generation_release` body (`:268-370`) is moved **verbatim** into a new
  `_stage_generation_release` (no exit, no `stage_release` argument and no
  `_stage_pre_sibling` call moves: the four pairs stay at `:209`/`:232`, `:243`/`:254`,
  `:301`/`:323`, `:336`/`:347`), and `_run_generation_release` becomes three lines — stage,
  then `_build_master_siblings(db, job_id, target_chapter_id, owner_sub=owner_sub)`, then
  return. **One call site, on the tail all four exits converge on, and outside the
  `_RELEASE_MODE`/`_RELEASE_CAPTURE` `finally` at `:365-367`** so the deposit interceptor
  (`install():462-477`) cannot see the assessment lane. NEW `_build_master_siblings`, which
  **never propagates**: `try/except Exception` per lane, each failure recorded as a named
  `assessment_lane_unavailable` issue on that lane's CONCEPT release through the existing
  `payload["issues"]` ledger (`build_concepts_release.py:1464` Post, `:2035` Pre).

  **Q13, stated as the reason and not as a nicety:** the concept outputs are finished and
  durable when this runs, so an uncontained `ReleaseRunError`
  (`assessment_release_run.py:106`), `SourceQuestionLeak` (`:114`), `UploadRefused`
  (`assessment_release_service.py:62`), `WorkbookRenderError`
  (`assessment_workbook.py:70`) or `premap.PreExtractionError` (`premap.py:256`) escaping
  here is a mid-run halt that loses finished work. The concept release's `release_state` is
  **unchanged** by an assessment-lane failure and its database upload stays open; only the
  `release_master` / `pre_release_master` manifest entries go `disabled`, carrying the
  recorded issue as their reason. An implementer who "simplifies" by letting the exception
  escape turns one Master-lane fault into all four outputs lost — the consequence the
  round-3 log warned about and this slice now BUILDS against rather than warns about.

  **Regressions:** `test_one_build_concepts_run_produces_all_four_outputs` (the positive
  pin) and **`test_an_assessment_lane_failure_does_not_cost_the_concept_outputs`** (T15-2's
  six assertions, parametrised over the four exits).
* **Must include, or the core is broken on arrival:** `structural_defects` must handle B's
  payload shape. [measured] today `release_state` on `{"source_atoms","candidates",
  "groups"}` returns `diagnostic_release`. Cluster A's C3 file list omitted
  `build_concepts_release.py`; it is here.
* **Regressions:** `tests/test_release_core.py::test_one_run_mints_one_release_per_lane_
  with_four_projections`; `::test_restaging_after_freeze_mints_version_2_and_leaves_
  version_1_superseded`; `::test_the_release_row_carries_its_lane_the_profile_and_the_
  layout_the_run_executed_under`; `::test_the_table_name_is_unchanged` (assert
  `models.AssessmentRelease.__tablename__ == "assessment_releases"` — a pin against a
  well-meant later rename); `::test_release_state_is_the_same_vocabulary_for_both_payload_
  shapes`;
  `::test_an_unplaced_candidate_still_blocks_the_upload_through_readiness` — the
  **unchanged-behaviour** pin that makes the S6→S8 hand-off safe: with `:326-330` intact, a
  candidate whose `concept_key` does not resolve leaves readiness `BLOCKED`, the DB upload
  is refused and all four downloads return 200. It is written here and stays green
  unchanged through S8, where the *reason* changes from the renderer's `unplaced` list to
  the staged `unresolved_question_home` verdict but the *consequence* does not. (The named
  -defect test itself, `test_a_candidate_with_no_resolved_home_is_a_named_defect_not_a_
  silent_warning`, lands in **S8** with the verdict — it cannot pass here, because nothing
  names anything yet.)
  `::test_the_manifest_enumerates_all_four_outputs_in_both_lanes` (T14/P16, in the owner's
  order).
  `tests/test_pre_release_lane_wiring.py:583` is **REWRITTEN** with the new key
  set named in the commit message. All 18 collected tests in
  `tests/test_mes_release_lifecycle.py` stay green unchanged.
* **NOT in it:** publication (S10), any renderer change.

### S7 — The layout migration. THE ATOMIC ONE.

* **Changes:** `app/bulk_import/__init__.py` (`SHEET_*` :19-28, `OBJECTIVE_FIELDS`/
  `SUBJECTIVE_FIELDS`/`DESCRIPTIVE_FIELDS` :99/:124/:164, `FIELDS_BY_KIND` :171,
  `SECTION_BANDS` :178 → `{label,start,end}`, `CONCEPT_FIELDS` → per-kind
  `concept_fields(kind)`, `_q_start` :47 — all derived from the layout registry;
  `LEGACY_CONCEPT_FIELDS` :61 kept frozen); `app/bulk_import/writer.py`
  (`_concept_field_value` :598 including the dead `parent: X` branch :623-631, the
  `parent_column_present` parameter and the `"parent_column"` report key at :1075 with its
  consumer `build_concepts.py:4570`; `_question_to_row` :722; `_concept_to_row` :811 and
  both `expected =` computations at :805/:830, **whose silent pad/truncate at `:806-808`
  and `:831-832` becomes a recorded Fixer decision per T3.8**; `_write_headers` :1117
  adopting the band
  dicts with gaps and per-sheet whitespace; **`_new_workbook` :1140-1154 — three sheets in
  the authority's order `['Objective','Descriptive','Subjective']`, and the Doc Link sheet
  created at `:1146` DELETED. This is the sheet half of OWNER RULING OD6 (T17/B1): it is
  `writer._new_workbook` that builds Outputs 01 and 03, and [measured] it emits four sheets
  today (`'Objective '`, `'Subjective'`, `'Descriptive'`, `'Doc Link <> Each fields '`)
  against the committed format workbook's three. Until this lands, "the other two sheets
  exist and are empty" is false of the two Concept Files the owner ruled on**;
  **`append_concepts:1049` calls `layouts.migrate_workbook_layout`
  after `scan_workbook:1058` and before `openpyxl.load_workbook:1059`**);
  NEW `layouts.migrate_workbook_layout` + `MigrationReceipt` in
  `app/bulk_import/layouts.py` (T7.6 — signature, lock and sibling there);
  `app/bulk_import/reader.py` (the registry entry for the target,
  including the 12/13/14 scalars and the 20 Subjective blocks); the 14 positionally-coupled
  test files.
* **`layouts.py`'s three `canonical-*` entries are NOT touched by this slice.** They are
  frozen literals by construction (T6/S2). If this slice edits them — or if they were ever
  written as live references to `FIELDS_BY_KIND` — the canonical layout stops being
  registered the moment `FIELDS_BY_KIND` moves, `canonical-current` silently becomes the
  reference layout, and Q16's "remain readable AND appendable" becomes a 422 on every older
  workbook. The regression below is the only thing standing between those two states.
* **Atomic:** with a shared `CONCEPT_FIELDS` gone and per-sheet bands in, any file left
  behind computes a wrong `expected`; today that **truncates rows silently** at
  `writer.py:808`/`:832` `return row[:expected]` instead of failing, which is why T3.8's
  loud replacement lands in this same commit rather than after it.
* **Also in it, and it is the reason this cannot be a pure constants swap:**
  `tests/test_bulk_import_schema.py:37-39` asserts
  `sum(span for _, span in bi.SECTION_BANDS[kind]) == len(bi.FIELDS_BY_KIND[kind])` — a
  **contiguity invariant the target cannot satisfy**: [measured] the reference Objective
  bands cover 30 of 67 columns. That file is rewritten from counts to manifest equality and
  becomes the anti-drift gate: for each kind,
  `bi.FIELDS_BY_KIND[kind] == aw.FIELDS[sheet]`, `bi.SECTION_BANDS[kind] ==
  aw.BANDS[sheet]`, `list(bi.SHEET_BY_KIND.values()) == aw.SHEET_ORDER`. Cluster B's
  atomic file list omitted it.
* **The workbook that migrates is `config.BULK_IMPORT_OUTPUT` — gitignored runtime state
  on the deployed volume — and NOT `backend/data/bulk_import_database.xlsx`. OWNER RULING
  OD2 corrects the premise; there is no OR2 and no fallback.** (T7.6a carries the
  measurements.) Two consequences for this slice, both simplifications:
  * **`backend/data/bulk_import_database.xlsx` needs no migration, no coordination window
    and no ruling.** [verified] nothing appends to it (`grep -rn "BULK_IMPORT_DB"` →
    `data_reset.py:38-40` deletes it, `:68` reports it, `scripts/generate_dummy_data.py:39`
    WRITES it, `tests/conftest.py:20,31` reads it), `.gitignore:57` records that it is the
    committed **database fixture**, and `.github/workflows/ci.yml:28` runs
    `scripts/generate_dummy_data.py` before `:29`'s `pytest -q` — and that script writes it
    through `app.bulk_import.writer`. So the moment this slice moves the writer, CI
    regenerates the fixture in the target layout automatically. **What this slice must do
    is exactly one thing: commit the regenerated fixture in the same commit as the writer
    move**, so the tree is never in a state where the committed fixture and the writer
    disagree, and say so in the commit message.
  * **The whole fallback-(b) plumbing is DELETED, not re-pointed.** `config.BULK_IMPORT_DB`
    does **not** become an ordered tuple; `scan_workbook:244` and `append_concepts:1049` do
    **not** take a sequence of paths; there is no second file and no split R5 ledger. The
    previous round wired that fallback to `BULK_IMPORT_DB` — the wrong constant (verifier
    V5) — and OD2 removes the fallback itself, so the correction is a deletion at every
    site: here, T7.6, T12/M9, D3/Q16, §7 and §8.
  * **What remains real:** a deployed volume carries a live `bulk_import_output.xlsx` on the
    canonical layout, and `append_concepts` must not write new-layout values at old-layout
    positions into it. That is what `migrate_workbook_layout` is for, it runs on the staged
    sibling under `output_workbook_lock()` (T7.6), and it never raises.
* **Cluster D's losslessness proof is measured on a different
  key space than its own migration rule, and on the repo's own file the two disagree:**
  [measured] canonical Descriptive carries `question_label` at idx 27 (Group band) AND idx
  30 (Question band), both non-blank on **56 of 56** rows with **identical values on all
  56**; the target carries it once. Under a band-qualified logical key
  `group.question_label` has no destination, so `migrate_workbook_layout` lists it in the
  receipt's `unmappable` on the repo's own artifact (it never raises — T7.6). The collapse
  is provably safe (the writer writes
  `q.question_label` into both), but it must be an **explicit, recorded alias entry in the
  layout registry**, not an inference. Cluster D's `parent_concept` / `concept_source`
  measurement is separately confirmed (0 non-blank of 56 on all three sheets) but is a
  property of `scripts/generate_dummy_data.py`, not of the pipeline: a real run sets
  `concept.sources` and `writer.py:641-642` returns it unconditionally, so a deployment
  that has run a real chapter has non-blank `concept_source` on Descriptive. The migration
  regressions must use a workbook the real writer produced with a non-empty `source_book`,
  never `bulk_import_database.xlsx` — which, per OD2, is the CI-regenerated fixture and is
  therefore doubly wrong as a migration subject: it is not the file that migrates and its
  56 rows are `generate_dummy_data.py`'s, not the pipeline's.
* **Regressions:** `test_appending_to_an_older_layout_migrates_once_and_loses_nothing`
  (copy a real-writer workbook, `append_concepts`, assert new layout, sibling
  `<stem>.pre-<layout_id>.xlsx` exists, every prior `question_label` survives, second
  append does not re-migrate); `test_a_migration_that_would_drop_a_non_blank_value_is_
  recorded_and_the_run_completes` (the receipt names it, a release issue names it, the
  Fixer decision is recorded, `append_concepts` returns — **no raise**);
  `test_the_duplicate_question_label_columns_collapse_by_recorded_alias`;
  `test_a_canonical_workbook_still_imports_and_appends_after_the_layout_move` (C5b's
  actual failure mode: open a committed canonical `.xlsx`, POST it, assert 200 and not
  422, then `append_concepts` onto it and assert one migration);
  `test_the_canonical_registry_entries_did_not_move_with_FIELDS_BY_KIND` (assert
  `layouts.CANONICAL_CURRENT.fields_by_kind["objective"] != bi.FIELDS_BY_KIND["objective"]`
  after this slice — the direct pin that the frozen literals stayed frozen);
  `test_a_row_wider_than_its_sheet_is_recorded_not_truncated` and
  `test_a_row_narrower_than_its_sheet_is_recorded_not_padded` (T3.8);
  **`test_the_committed_fixture_is_on_the_target_layout`** (OD2 — identify
  `backend/data/bulk_import_database.xlsx`'s three sheets through the registry and assert
  `sop-mes-1`; it fails until the regenerated fixture is committed in this same commit, and
  it is the cheapest guard against the tree carrying a writer and a fixture that disagree);
  **`test_the_concept_files_are_filled_to_the_concept_band_and_no_further`** (OD6/T17/B1 —
  run `build_concepts_release_files.build_release_bulk_import_workbook` for **both** lanes,
  i.e. the real Outputs 01 and 03, and assert: exactly three sheets in the format workbook's
  order, **no `'Doc Link <> Each fields '` sheet**, only Objective carrying data rows, every
  column from the Group band onward empty on every row, and `topic_concept_labels` /
  `concept_source` non-empty. [measured] it fails today on the sheet set and the sheet
  order, which is why it belongs in this slice and not in S8 — an earlier draft aimed it at
  `render_concept_file`, which builds the sealed projection and not an output);
  `tests/test_bulk_import_schema.py` (all 8 rewritten to manifest equality).
* **`migrate_workbook_layout` may NOT raise mid-run.** [verified] `append_concepts` is
  reached from `build_concepts.py:4489 → :1921 → :1813 → :1776`, i.e. inside a generation
  run that has already spent its entire model budget. Cluster D's specification put a
  `WorkbookLayoutMigrationRefused` there. That is the halt Q13 retired, and T6's
  "there is no run here" justification is true for the reader and false for the writer.
  An unmappable non-blank value is a **Fixer decision plus a recorded release issue**, the
  pre-migration sibling is retained, and the run completes.
* **NOT in it:** the tags, the joins and the restored-column CONTENT (S8).

### S8 — One renderer: tags, joins, restored columns, profile.

* **Changes:** **NEW `assessment_release.unresolved_question_homes(snapshot, profile)`**
  beside `duplicate_group_keys:419-435`, wired into
  `assessment_release_service.create_release` **between the snapshot build (`:277-287`) and
  the `diagnostics={"payload_errors": frozen["errors"]}` dict (`:301`)**, appending its
  findings to `frozen["errors"]` — the transport T5-1's duplicate `question_label` already
  uses, so `_readiness:318-324` `BLOCKED` and `:537`'s refusal need no new wiring (T7.5,
  D8.5b); **`assessment_release_service.py:326-330` (`_readiness`'s read of
  `manifest["issues"]["unplaced"]`) is DELETED HERE, in this same commit and no earlier** —
  S6 kept it deliberately, because until the staged verdict exists it is the only thing
  refusing the write;
  `app/bulk_import/assessment_workbook.py` (`render_concept_file:180` and
  `render_master_file:298` take the resolved profile; **NEW module constant
  `RENDERABLE_SHEET_KINDS = ("objective","descriptive")` and `sheet_for_kind:407` derived
  from IT × the layout's sheet-name map — NOT from the profile, per T7.5/B3 and T12/M2: a
  profile-derived map has no `None` branch to take, so the defect could never fire**; the
  three candidate branches at `:357-368`, `:371-375` and `:412-420` **READ the staged
  verdict instead of discovering it — they still record the candidate in
  `issues["unplaced"]` as the evidence channel**;
  **ALL SEVEN raises in `render_master_file` STOP RAISING — `:315`, `:317-318`, `:324-326`,
  `:331-333`, `:336-337`, `:343-345` and `:373` — and so do `_question_record`'s seven at
  `:207`, `:213`, `:217`, `:239`, `:252`, `:265`, `:275` (T7.5/B4). Three of the first six
  and three of the second seven already have a staging twin and simply go; the rest join
  `unresolved_question_homes` as `group_concept_home_unknown`, `group_home_unnamed`,
  `group_visible_name_mismatch` and `render_shape_overflow`. After this the Master renderer
  raises nothing at all**;
  **the questionless-concept change of OD5/T16: the catalogue loop at `:430-442` emits a
  Group row only for a group whose concept has ≥1 placed question row, and a concept with
  none gets exactly ONE tail row from `_bands_record(entry)` with no `_group_record_fields`
  update, appended last; the issues manifest at `:444-449` gains
  `questionless_concepts`**;
  **`validate_master_file`: `expected_group_keys` (`:767`) is scoped to concepts that have
  questions, so `:947-948`'s "group missing from Master" stops firing on every questionless
  concept, while `:941-946`'s "concept missing from Master" is unchanged and is now what the
  tail row satisfies** (OD5/T16);
  **`app/services/assessment_master_refiner.py:673-682` takes the SAME scoping in this same
  commit — it is the SECOND read-back enforcing "every group has a rendered Master row"
  (`"refiner-readback: group … has no rendered Master row"`), and leaving it unscoped turns
  every affected concept into an error** (T16);
  `_FORCED_BLANK:62` and its read-back mirrors →
  `forced_blank_fields` **at all three sites, C10: `:62`/`:93`, `validate_concept_file:718-737`,
  and the two snapshot builders' chapter dicts**; `_bands_record:162`
  strips the identity trio; **the renderers CALL `identity.titled` and
  `identity.topic_concept_roster` — the composition and the roster are SHARED with
  `writer._front_bands:703-720` and `writer._concept_field_value:611`, not moved out of
  them (T14); stripping them from the writer would delete the identity pair from Outputs
  01/03 and from the append-only database of record**);
  `app/services/assessment_release_snapshot.py:199` and
  `app/services/assessment_release_service.py:118` (compute `topic_concept_labels`);
  `assessment_release_service.py:356` (pass the persisted profile);
  `app/services/assessment_profile.py` — **`sheet_kinds` is ADDED and
  `allow_subjective_rows:25` is DELETED, not kept beside it (T12/M6b); `forced_blank_fields`,
  `question_source`, `group_status` keys; the accessor `sheet_kinds(profile)` of T12/M5b,
  which every reader goes through and which falls back to `DEFAULT_PROFILE`'s value so
  `tests/golden/rne_assessment_candidates.json:16-20`'s recorded profile still resolves and
  the golden does not move**; byte-identical in effect for `reference-1`; the test-only
  `reference-2`; `assessment_cells.py:99-103` (and its `:100-102` comment, which names the
  deleted key); `assessment_release_run.py:686, :887`;
  `assessment_release.py:103, :166-169, :191-195, :375-382, :490` and its two
  `freeze_payload` callers; `assessment_blueprint.py:100`;
  **`assessment_workbook.validate_master_file:751-753`, which reads
  `"subjective" not in assessment_profile.sheet_kinds(profile)` in place of
  `not profile["allow_subjective_rows"]` — [measured] the ONLY live read of the deleted
  key**; `assessment_materialization.py:120`;
  `assessment_marking.py:321`; `build_concepts_release_files.py:199-214` (the transient
  concept carries `related_concepts` and `digicards`); `build_concepts_release_contract.py`
  (Pre staging resolves `_aegis_needed_for` to persisted Post `machine_id`s per T3.3);
  `build_concepts_release.py:98-105` (the half-right comment corrected) and
  `_RELEASE_AUDIT_FIELDS` (+ the resolved needed-for marker).
* **Atomic with:** `build_concepts.py:1198-1201` and `:1209-1211` are purged in the SAME PR
  as a separate commit — un-blanking `chapter_duration` before the `max(40, n_concepts *
  12)` estimate goes would ship a volume-derived number to every school, a Rule-1
  regression introduced by a portability fix.
* **Atomic, and this is the one that must not be split:** `unresolved_question_homes` +
  its `create_release` wiring + the deletion of `_readiness:326-330`. Any two without the
  third is either a double refusal (harmless but two vocabularies, which T1 exists to end)
  or **no refusal at all** (the R4 hole verifier V1 found). The commit message states the
  three together.
* **Atomic for the same reason, one level down (T7.5/B4):** each renderer raise is deleted in
  the same commit as the staged verdict that replaces it. A raise removed without its
  replacement is the same hole in miniature — the release renders and the DB write is refused
  by nothing; a replacement added without the raise removed leaves the raise winning first,
  so the named defect never reaches a reviewer. For the six that already have a staging twin
  (`:315`, `:317-318`, `:336-337`, and `_question_record`'s `:207`, `:213`, `:217`) there is
  no replacement to add and the deletion stands alone safely — [verified] `validate_group`,
  `duplicate_group_keys` and `validate_candidate` already refuse them at freeze. Say which
  six in the commit message, so a reviewer can check the claim rather than trust it.
* **Atomic for B3:** `sheet_kinds` added + `allow_subjective_rows` deleted + the accessor +
  `validate_master_file:751-753`'s read. Landed half-way, either two keys answer one question
  (T12/M6's own prohibition) or the single live read of the deleted key raises `KeyError` on
  every Master validation.
* **Regressions:** `test_output_03_carries_the_machine_id_pair_in_every_title_cell` (the
  Post Concept File — renamed from `test_output_01_…` by OD4/D9-Q22; the artifact and the
  assertion are unchanged);
  **`test_a_candidate_with_no_resolved_home_is_a_named_defect_not_a_silent_warning`** — the
  T7.5 case, moved here from S6, and the one that proves the R4 hole is closed: a candidate
  whose `concept_key` does not resolve yields a named `unresolved_question_home` entry in
  `diagnostics["payload_errors"]`, `_readiness` is `BLOCKED`, the assessment release's
  `release_state` is `DIAGNOSTIC_RELEASE`, **all four output downloads return 200**, the DB
  upload is refused, and the candidate's `question_label` appears in the release workbook's
  Issues sheet. Assert explicitly that the label appears in **no data row of any of the
  four outputs** — that is the fact that makes it a defect rather than a flag;
  **`test_no_code_path_decides_on_unplaced_and_the_refusal_survives`** (renamed from
  `test_the_unplaced_readiness_read_is_gone_…`, because "has no consumer" overstated it and
  contradicted T7.5 item 4: the renderer still WRITES `issues["unplaced"]` and
  `publish_release:387-389` still puts the whole manifest on disk as `manifest.json`. What
  the test asserts is that no site outside `assessment_workbook`'s own producers READS it —
  [measured] `grep -rn 'unplaced' app/ --include=*.py` leaves only those producers plus the
  unrelated `unplaced_pending_certification` identifier in the coverage lane — together with
  the refusal still firing. Two halves in one test so neither can land alone);
  **`test_a_group_home_disagreement_is_a_defect_not_a_render_error`** (the `:373` raise:
  today it costs all four workbooks; after this slice all four are written and the DB write
  is refused);
  **`test_the_master_renderer_contains_no_raise`** and
  **`test_every_group_edge_raise_became_a_named_defect`** (T7.5/B4 — the first walks the AST
  of `render_master_file` and `_question_record` and asserts zero `ast.Raise`; the second
  parametrises the seven `render_master_file` conditions by line — `:315`, `:317-318`,
  `:324-326`, `:331-333`, `:336-337`, `:343-345`, `:373` — and asserts each one now yields a
  named entry in `diagnostics["payload_errors"]` with **all four workbooks written**, three
  of them through machinery that already existed (`validate_group:386-391`,
  `duplicate_group_keys:419-435`));
  **`test_a_questionless_concept_is_one_tail_row_stopping_at_the_concept_columns`**,
  **`test_the_master_validator_accepts_a_questionless_concept`**,
  **`test_the_questionless_shells_stay_in_the_payload_and_the_difference_is_recorded`** and
  **`test_a_concept_with_questions_still_emits_its_unrepresented_group_rows`** (OD5/T16 —
  the first three fail today: the renderer emits three Group-filled rows and
  `validate_master_file` demands them);
  **`test_the_sealed_concept_projection_keeps_the_same_shape`** (OD6/T17/B1 — on
  `aegis_concepts.xlsx`, the product of `render_concept_file:180-192`: every column from the
  Group band onward empty on every rendered row, and `topic_concept_labels` non-empty. It
  fails today on `topic_concept_labels` (T3.7). **In the same test, assert its sheet set
  equals Output 03's** — the anti-divergence pin, because this file's sha256 is what
  `assessment_release_service.py:379`/`:516` seals and re-verifies, and a projection whose
  shape drifts from the artifact it projects is a hash that proves nothing. The pin on the
  REAL Outputs 01/03 is `test_the_concept_files_are_filled_to_the_concept_band_and_no_
  further` and it lives in **S7**, with the builder — an earlier draft put it here, aimed at
  the wrong function);
  `test_topic_concept_labels_is_populated_in_every_projection`;
  `test_keywords_ship_filled_on_all_four_outputs` (assert ≥1 rendered row has non-empty
  keywords, so a future "blank it for parity with the gold" change fails);
  `test_pre_related_concepts_carries_the_resolved_post_machine_ids` (two needed-for links,
  newline-joined in model order; assert no `CONCEPT-`/`PRC-`/`PRT-` token appears anywhere
  in the rendered bytes); `test_post_related_concepts_is_blank_until_a_relations_pass_
  exists`; `test_an_unresolvable_needed_for_link_is_a_review_flag_not_a_blank`;
  **`test_a_subjective_candidate_a_widened_profile_allows_is_a_named_defect_not_a_render_
  error`** (T7.5/B3 — renamed and re-aimed from
  `test_a_subjective_candidate_the_profile_allows_is_a_recorded_defect_not_an_unplaced_row`,
  which asserted a defect on a candidate the renderer would happily place. Under
  `reference-2`: the candidate (i) **passes** `validate_candidate:191-195`, which is the
  plumbing working and which fails today; (ii) yields a named `sheet_kind_not_renderable`
  entry in `diagnostics["payload_errors"]`; (iii) leaves `_readiness` `BLOCKED` and the DB
  write refused; (iv) **all four workbooks are written and all four downloads return 200** —
  the fact that distinguishes it from today, where `_question_record:216-219` raises and
  costs every one of them; (v) no reason string anywhere contains "MES");
  **`test_the_reference_profile_still_refuses_a_subjective_candidate_at_freeze`** (the
  negative control: under `reference-1` the refusal is `validate_candidate`'s, exactly as
  today, and `sheet_kind_not_renderable` does **not** also appear — the two codes must not
  double-report);
  **`test_the_recorded_golden_profile_still_resolves`** (T12/M5b —
  `tests/golden/rne_assessment_candidates.json:16-20`'s profile, which carries
  `allow_subjective_rows` and no `sheet_kinds`, feeds `decide_cells` unchanged and yields
  `allowed_sheet_kinds == ["objective","descriptive"]`, so the golden does not move);
  `test_the_reference_profile_cells_payload_is_byte_identical_before_and_after_threading`
  (so `kernel.decision_key` does not move and zero decisions re-spend); the manifest-union
  gate test (a record carrying `made_up_field` yields a named structural issue; one
  carrying `parent_concept`, `concept_machine_id` or `release_row_identity` does not);
  a "the machinery is gone" pin that `"parent: "` appears nowhere under `app/bulk_import/`;
  `test_the_writer_still_composes_the_identity_pair` (C9/T14 — `writer.write_concepts_
  workbook` output still carries `"<name> (<machine id>)"` in every concept title cell and
  a populated `topic_concept_labels`, so "one renderer" did not strip the other one);
  `test_forced_blank_fields_is_read_at_all_three_sites` and
  `test_every_profile_key_has_a_named_reader` (C10 — the key-with-no-reader pin;
  parametrised over `DEFAULT_PROFILE`'s keys with the reading module:function named
  beside each. **It is also what enforces T12/M6b: with `allow_subjective_rows` deleted and
  `sheet_kinds` added, a tree that kept both would fail here on the one with no reader**);
  **`test_sheet_kinds_is_read_through_one_accessor`** (T12/M5b — no module outside
  `assessment_profile` indexes `profile["sheet_kinds"]` directly; all nine sites go through
  `assessment_profile.sheet_kinds`).
  **Re-authored, not new:** `tests/test_assessment_cells.py:428-445`
  `test_profile_cannot_enable_an_unsupported_subjective_wire` becomes
  `test_a_widened_profile_widens_the_cells_wire_contract` — with `sheet_kinds` including
  `"subjective"` the cells wire ACCEPTS it (M5's point), and the refusal it used to prove
  moves downstream to `sheet_kind_not_renderable`, which is where B3 puts it. [measured] it
  would otherwise stay green while proving nothing: its profile carries no `sheet_kinds`, so
  M5b's accessor falls back to the default and it still raises.
  `tests/test_assessment_release_run.py:1176-1183` stays green **UNCHANGED** for the same
  reason, and that is the honest reason to leave it alone rather than a claim that it still
  proves what its name says.
  The parametrised acceptance test of M8: `reference-1` reproduces
  `tests/test_assessment_reference_acceptance.py` byte-for-byte while `reference-2` with a
  widened `sheet_kinds` produces a NAMED `sheet_kind_not_renderable` defect with all four
  downloads intact — **not** rows, and never an `unplaced` row with a school name in its
  reason string (M8, corrected: the earlier "either the rows or a NAMED defect" let an
  implementer satisfy it by pushing a Subjective row through the Descriptive branch).
* **NOT in it:** publication (S10), the QC audit (S11).

### S9 — Rule E de-raising, the state split, and the Pre-lane verdict.

* **Changes:** `app/services/build_concepts_release_files.py:110-227` (all eight raises
  become recorded defects; the function returns `(chapter, concepts, records, defects)`);
  `:44-107` (always returns a workbook, with an issues note row);
  `app/services/assessment_release_snapshot.py:113-120` (`SnapshotError` only for a
  snapshot that genuinely cannot be built); `app/api/build_concepts.py:304-306`;
  `app/services/build_concepts_release.py:457-499` split into `structural_defects` +
  `nothing_to_publish`, `:503-528`, `:1918-1975` (the payload carries the Phase-03
  verdict); `app/services/phase3/premap.py:860-879` (spend one verdict when the capture is
  empty, with an advisory critic and a Fixer seam);
  `app/services/build_concepts_release_publication.py:97-99` (zero-row idempotent success).
* **Atomic with:** the state split and the row-level defect recording — a defect discovered
  at staging with no place in the state vocabulary is invisible.
* **Regressions:** `test_an_empty_but_decided_pre_release_is_ready` (state READY,
  `structural_defects == []`, **all four downloads 200** including
  `release-bulk-import.xlsx` which [measured] raises today, and
  `upload_release_to_database → database_uploaded True` with empty id lists) —
  this **replaces** `tests/test_pre_release_lane_wiring.py:632-673`;
  `test_an_empty_capture_without_a_positive_verdict_still_blocks`;
  `test_one_malformed_staged_row_does_not_kill_any_download` (row 2 missing `topic`: four
  downloads 200, the workbook carries the other rows, a `staged_row_unusable` issue names
  row 2, state DIAGNOSTIC_RELEASE, DB upload refused);
  `test_output_04_builds_from_the_sound_rows` (200, not 400 — the Post Master File;
  renamed from `test_output_02_…` by OD4/D9-Q22);
  `test_no_new_raise_inside_transient_release_hierarchy` (the Rule-E pin).
* **NOT in it:** a fourth release state. §4's three names are unamended.

### S10 — The converged publication.

* **Changes:** `app/services/build_concepts_release_publication.py` (:97-99 gate on the
  T9 closed set; :100-106 the silent row drop becomes a named defect **and the resolved
  needed-for marker is lifted into an explicit `related_concepts` key BEFORE
  `_strip_release_fields` runs, per T3.3b**; :125
  `clean_concept_record` REMOVED; :137-172 resolve by persisted `machine_id`, no merge, no
  re-parent, no `db.delete(tag)` (`:155-163`), **and :164-170 persists `related_concepts` and
  `digicards` on the merge path**); `app/services/build_concepts.py:598-613` (`_add_concept`
  gains `clean=` **and persists `related_concepts=` and `digicards=` — today it persists
  neither, so the Pre column T3.3 resolves empties the moment the reviewer publishes and
  Output 01 (the Pre Concept File) is regenerated from DB rows at
  `build_concepts_release_files.py:78-79`**); `app/services/assessment_release_service.py:344-433` (publish),
  `:487-682` (upload), `:683-741` (`_resolve_snapshot_concept_ids` → machine-id
  resolution); `app/services/concept_cleanup.py` call sites at `phase3/assemble.py:900` and
  `release_refiner.py:271` (`filter_review_violations` flags instead of deleting);
  `build_concepts.py:1788-1830` **untouched**.
* **Atomic with:** the `clean_concept_record` removal and the `_add_concept` switch — one
  without the other changes nothing on the create path.
* **Must NOT land before S4/S6:** hash verification needs a frozen object with recorded
  hashes, and machine-id resolution needs the persisted column.
* **Regressions:** `test_publication_refuses_a_tampered_artifact_for_every_one_of_the_four_
  outputs`; `test_publication_never_rewrites_a_reviewer_edited_title` — stage
  `concept_title="pH and its meaning"` and details containing "Fig. 2.1", publish a
  **newly created** concept (not a merge), assert the persisted row is byte-identical;
  `test_a_staged_row_with_no_topic_is_a_named_defect_not_a_silent_drop` (the measured
  two-row payload); `test_two_same_titled_concepts_under_two_topics_publish_as_two_rows`
  (fails today — the regression that proves T11.3);
  `test_a_topic_title_recased_between_publications_still_resolves` (the measured
  `UploadRefused` reproduction, now green); `test_a_concept_under_a_topic_named_Summary_is_
  flagged_not_deleted` (the measured 5-in-2-out case);
  `test_an_empty_pre_release_still_exports_and_still_publishes_zero_rows`;
  `test_pre_related_concepts_survives_publication` (T3.3b — publish, then re-download
  Output 01 through the published-concept shortcut at
  `build_concepts_release_files.py:78-79` and assert the same newline-joined Post
  `machine_id`s, i.e. that the column reads identically before and after the upload);
  `test_digicards_survive_publication` (the same omission, the same fix).
  `tests/test_mes_release_lifecycle.py:231` (crash recovery), `:241` (lease), `:262`
  (ordering) stay green unchanged.
* **NOT in it:** the QC audit's issue set (S11).

### S11 — The QC checklist and the polarity inversion.

* **Changes:** NEW `app/services/release_qc.py` (`audit(payload, *, artifacts=None,
  ledger=None) -> (issues, blocking)`) and `docs/release-qc-checklist.md`; **its call
  sites — `build_concepts_release.stage_release:1320` and `stage_pre_release:1918`, both
  immediately before the payload dict is assembled, with `issues` merged into
  `payload["issues"]` (`:1464` Post, `:2035` Pre) and `blocking` appended to
  `payload["snapshot_defects"]` (`:2053` Pre), which `structural_defects:489` already
  reads** (T10-0);
  **`build_concepts_release.stage_release:1437-1481` — its payload dict GAINS
  `"snapshot_defects"` and the function gains the matching parameter (see the next bullet;
  without it this slice's own regression cannot pass);**
  `app/services/generation.py` (NEW `_BLOCKING_CODES` allow-list; `_FATAL_CODES` keeps its
  name; `_FIXER_UNACCEPTABLE_CODES` :12907 shrinks to `{required, required_parent}`; the
  final gate at `:15706/:15723/:15742` filters on `_BLOCKING_CODES`);
  `app/services/build_concepts.py:915-918` (the deposit gate filters on `_BLOCKING_CODES`);
  `app/bulk_import/writer.py` (delete :372-379, :383-384, :487-493, :511-519, :520-537;
  the surviving read-back routes to the Fixer instead of raising);
  `app/services/build_concepts_release.py:951-1000` (the threshold deleted, the key
  casefolded and Unicode-aware, one grouped collision issue), `:1132-1138` and `:1195-1203`
  (`_learner_analysis_count` purged), `:457-499` (the T9 identity set feeds
  `structural_defects`), `:1021-1027` (a catalog that will not parse is a named defect);
  `app/services/build_concepts_release_files.py:666` (the coverage ledger leaves the zip);
  `app/bulk_import/assessment_workbook.py:224` (`question_source` from the profile).
* **THE POST LANE HAS NO TRANSPORT FOR THE AUDIT'S BLOCKING SET UNTIL THIS SLICE BUILDS
  ONE — verifier V2, reproduced, and the previous round's "that is the whole wiring" was
  false.** [measured]
  `grep -n '"snapshot_defects"' app/services/build_concepts_release.py` returns exactly two
  hits: `:489` (the read, inside `structural_defects`) and `:2053` (the write, inside
  `stage_pre_release`). `stage_pre_release` declares the parameter at `:1918-1929`;
  **`stage_release` declares no such parameter and its payload dict emits no such key** —
  [verified] by reading the whole literal at `:1437-1481` (`version`, `released_at`,
  `release_reason`, `job_id`, `learning_kind`, `source_book`, `filename`,
  `source_document_hash`, `target_chapter_id`, `directory_metadata`, `target_identity`,
  `checkpoint_stage`, `checkpoint_progress`, `records`, `issues`, `type_case_rows`,
  `question_task_inventory`, `extraction_provenance`, `mined_types`,
  `pending_decision_snapshot`, `final_grounding_certificate`, `chapter_meta`,
  `instruction_set`, `summary`, + optional `refinements`). **Add
  `"snapshot_defects": _json_safe([...])` to `stage_release`'s payload dict in the same
  shape `:2053` uses** — normalised non-empty strings, defaulting to `[]` — and the matching
  `snapshot_defects: Sequence[str] = ()` parameter. It is a Post-lane payload-shape change,
  so `tests/test_pre_release_lane_wiring.py:583`'s key set gains one key here and this
  slice's commit message names it (S6 rewrote that key set; S11 adds to it). Only with this
  present is
  `test_the_audit_runs_at_staging_for_both_lanes_and_never_raises` — which asserts the
  blocking finding in `payload["snapshot_defects"]` for **both** staging functions —
  passable at all.
* **Atomic with:** `_BLOCKING_CODES` and both gates' filters are one contract; split, there
  is a bisect point where every code blocks or none does. The writer deletions belong here
  because they are the same doctrine and the same PR-body claim. **And the
  `stage_release` payload key is atomic with the audit's call site**: an audit that returns
  a blocking finding with nowhere to put it on the Post lane silently drops it, which is the
  failure this slice exists to prevent.
* **Must land after S9:** every item here can produce a new defect, and under the pre-S9
  raise behaviour a new defect 404s `release-bulk-import.xlsx` and 400s Outputs 02/04 —
  the run's own audit would take the downloads away.
* **Regressions:** `test_a_generic_misconception_ships_flagged_not_blocked` (the deposit
  completes, the row carries a review flag naming the code, the release issue exists);
  `test_the_blocking_set_is_exactly_the_closed_list` (`_BLOCKING_CODES` equals a set
  written literally in the test); `test_the_final_gate_uses_the_same_blocking_set_as_the_
  deposit_gate` (the check Cluster D's edit list would have missed);
  `test_two_same_titled_concepts_in_one_topic_do_not_kill_the_run`;
  `test_the_writer_no_longer_judges_a_culmination_or_a_hub` (a 200-char "Culmination …"
  title and a hub whose gist repeats its marker; `append_concepts` succeeds and both rows
  are written — the first test ever to touch that function);
  `test_the_read_back_identity_check_routes_to_the_fixer_and_the_run_completes`;
  `test_a_duplicate_qid_assignment_blocks_the_upload_and_ships_every_download`
  (`release_state()` is DIAGNOSTIC_RELEASE, the bulk-import workbook and diagnostics zip
  both return 200, upload raises); `test_an_unassigned_inventory_qid_ships_flagged_and_
  publishes` (the negative control that pins where the line is);
  `test_a_reviewer_reword_of_concept_details_never_blocks_the_upload` (the
  `qid_render_count_mismatch` / `example_less_case_shell` correction);
  `test_the_coverage_ledger_produces_release_issues`;
  `test_a_short_shared_tail_is_reported_as_a_collision` (re-authoring
  `tests/test_build_concepts_release.py:871-884`);
  `test_a_devanagari_question_repeated_is_reported`;
  `test_repeated_question_detection_is_case_insensitive`;
  `test_a_malformed_type_catalog_is_a_named_defect_not_a_swallowed_exception`;
  `test_the_audit_runs_at_staging_for_both_lanes_and_never_raises` (T10-0 — patch one
  checklist item to report a blocking finding; assert it appears in `payload["issues"]`
  and `payload["snapshot_defects"]` for BOTH `stage_release` and `stage_pre_release`, that
  `release_state()` moves to `DIAGNOSTIC_RELEASE`, that all four downloads stay 200, and
  that no exception escapes either staging call);
  `test_audit_issues_reach_the_release_workbook_issues_sheet` (the row lands in
  `build_release_workbook`'s Issues sheet with its code and message, and in
  `context/source_evidence.json` inside the diagnostics zip).
  Re-author `tests/test_generation_validation_diagnostics.py:750-761`.
* **NOT in it:** `_dedupe_titles_chapter_wide` (step 11); the model-verdict replacement for
  the eleven judgment codes (scheduled, needs a live provider and its own critic pass);
  `_GENERIC_SKELETON_FAMILY_RE` (inherited from 6335fe6 as explicit out-of-scope residue).

---

## 4. WHAT MUST NOT MOVE

**The 11 goldens under `backend/tests/golden/`** — `rne_envelope.json`,
`rne_settled_rows.json`, `rne_analysis.json`, `rne_host_maps.json`, `rne_place.json`,
`rne_preanalysis.json`, `rne_prelearn.json`, `rne_premap.json`, `rne_prequestions.json`,
`rne_assessment_candidates.json`, `rne_assessment_groups.json`. A step-8 diff touching any
of them is a defect, not a design move (`docs/restructure-handoff.md:467-468`).
[measured] `rne_envelope.json` sha256 stays
`e27cdcf02ed8579b1210c1d55d484cf20d604b2f08cb379c814d3d4ba1e42c79`; it is instruction-set-
free by construction (its metadata keys carry no `instruction_set_sha256`), which is why S1
does not move it.

**One of the eleven constrains a step-8 design decision, and it is named here so nobody
"tidies" it.** [measured] `rne_assessment_candidates.json:16-20` records the profile its run
executed under as `{"name","appears_in","allow_subjective_rows"}`, and
`tests/test_mes_candidate_golden.py:86` feeds that dict straight into `cells.decide_cells`,
which [verified] does **not** call `assessment_profile.resolve` (`:242-244` type-checks the
Mapping and reads it with `.get`). T12/M6b deletes `allow_subjective_rows` from the live
profile — but the golden keeps it, as the historical record of an input, and does not move,
because T12/M5b reads the new key through one accessor that falls back to
`DEFAULT_PROFILE["sheet_kinds"]`. That is the whole reason M5b is an accessor rather than a
merging `resolve()`: the merge would not reach this call at all, and adding one would
disable `_profile_payload:107-111`'s live `appears_in` gate.

**The golden gates** — `test_phase3_{settle,host,assemble,runner}_golden.py`,
`test_phase3_kernel.py`, `test_phase3_flip_seam.py`, `test_phase3_polish.py`,
`test_phase3_place.py`, `test_phase3_analyse.py`, `test_phase3_case_uniqueness.py`.

**The phase3 modules' decision identities.** No `kind` or `policy_version` in
`phase3/` moves except premap's new empty-capture verdict (S9), which is a NEW kind with
its own `policy_version` — never an edit to an existing one, which would replay verdicts
made under the old text.

**The three pre-spend pause suites pass unchanged** —
`test_phase3_source_review_decisions.py`, `test_source_topic_decision.py`,
`test_type_granularity_decision.py`, `test_type_granularity_gate_order.py`. They are the
canary that the Fixer carve-out is intact, and S1 is the one commit that moves the surface
under them: diff them deliberately.

**`tests/test_assessment_reference_acceptance.py` — KEEP VERBATIM.** It is the only
field-by-field comparison against the accepted corpus and it needs no change if step 8
migrates *toward* the target; a normaliser added to the writer would fail it, which is the
correct outcome.

**What it does NOT prove, so the spec does not overclaim it** — all four measured:

1. **It never exercises a production snapshot builder.** Its only app import is line 18,
   `from app.bulk_import import assessment_workbook as aw`. `_snapshots_by_chapter:137-186`
   reconstructs each snapshot **from the gold rows** (`topic = {**_fields(row,
   _TOPIC_FIELDS), "concepts": []}`, `concept_key = concept_title`, `group_key =
   group_name`, `question_label` read off the row). Neither
   `assessment_release_snapshot.build()` nor `snapshot_from_chapter` /
   `snapshot_from_staged_release` is invoked. So it cannot see the `topic_concept_labels ==
   ""` defect (T3.7), the missing `chapter_duration` key, or any snapshot-builder omission
   — it proves the RENDERER given a perfect snapshot.
2. **It never mints.** No tag, no machine id, no `question_label` is produced by it, and
   [measured] no golden carries one either. That removes the largest perceived blast radius
   from the ID rebuild — and it means S4 needs its own regressions, because this gate
   cannot break on a tag or label change.
3. **It is not positionally coupled to the atomic commit.** [measured] the only matches for
   the schema-constant grep in that file are lines 36 and 164, both its own LOCAL
   `_CONCEPT_FIELDS` tuple. It appears in the "14 positionally-coupled files" list as a
   grep false positive.
4. **Its harness dedupes concepts chapter-wide by title** (`_concept_seen`, :159-168) —
   an artifact of reconstructing a snapshot from rendered rows, not a pipeline contract.
   Nothing in step 8 may read it as one (T11.2).

**Every test named KEEP in map section 8**, plus these, each with the reason: all 18
collected tests in `tests/test_mes_release_lifecycle.py` (the fullest publication coverage
in the repo, and the convergence target) — **with one amendment OD4 forces and this spec
states rather than breaks quietly: `:319`'s
`pytest.raises(svc.UploadRefused, match="Output-01 identity")` becomes
`match="Output-03 identity"` in S10, in the same commit that rewrites the message.
AMENDED IN S8 (a second amendment to this file, recorded here rather than left to
contradict this paragraph): `test_duplicate_group_key_is_named_and_publication_fails_
closed:188-218` is INVERTED by T7.5/B4 — publication no longer raises on a duplicate
`group_key`; it succeeds, `_readiness` is `BLOCKED` and the upload raises
`UploadRefused`. The refusal is unchanged; the point at which it is taken moves from the
renderer to the staged verdict, which is the whole of B4. Its unused
`assessment_workbook as mp` import goes with the `pytest.raises` it served. So 16 of the
18 are untouched, one changes a string literal and no behaviour, and one is inverted with
its reason written in its own docstring** (§5);
`tests/test_release_refiner.py:182, :590` (the
`concepts_release` branch stays byte-behaviour identical);
`tests/test_chapter_topic_quality.py:849-877` (published Post labels byte-identical);
`tests/test_data_io.py:166` (the reader's placement-edge round trip);
`tests/test_pre_release_lane_wiring.py:388, :463` (the manifest twins — **`:463`'s BODY is
kept byte-identical; its NAME gains the OD4 renumbering in S6**), `:806, :844, :868,
:935` (Rule-G lane isolation); `tests/test_mes_dual_output.py:132` (the target expressed as
a test).

---

## 5. TESTS — by file

**INVERTED (the assertion's polarity flips because the layout moved):**

* `tests/test_bulk_import_schema.py` — all 8. `:4` counts 65/92/374 → manifest equality;
  `:13` "keywords not in CONCEPT_FIELDS" and `CONCEPT_FIELDS[2] == "parent_concept"` →
  the target band; `:24` question_text last; `:29` concept_source position; **`:37` the
  band-sum contiguity invariant, which the target cannot satisfy** — replaced by
  `bi.SECTION_BANDS[kind] == aw.BANDS[sheet]`; `:42` group-band label columns. This file
  becomes the anti-drift gate.
* `tests/test_generation_validation_diagnostics.py:750-761` — re-authored to assert the
  named codes are NOT in `_BLOCKING_CODES` and still reach the repair pass.
* `tests/test_build_concepts_release.py:871-884` —
  `test_a_short_shared_tail_is_not_a_repeated_question` becomes
  `test_a_short_shared_tail_is_reported_as_a_collision`.
* `tests/test_pre_release_lane_wiring.py:632-673` — the deliberate state mismatch becomes
  `test_an_empty_but_decided_pre_release_is_ready`.
* `tests/test_chapter_topic_quality.py:822-846` — re-authored (not deleted) to pin
  lane-scoped `T##` plus the `PL|PrL` token. **It calls `g._topic_index` and
  `g.question_label` directly** (`:841`, `:842`, `:846`), so it can only be re-authored in
  the commit that replaces them — S4, via T4-7. Without the minter rebuild there is nothing
  to re-author it against and S4 cannot close green.
* `tests/test_assessment_cells.py:428-445`
  `test_profile_cannot_enable_an_unsupported_subjective_wire` → **re-authored in S8** as
  `test_a_widened_profile_widens_the_cells_wire_contract`. T12/M5-M6b make the profile
  genuinely widen the cells wire, which is the whole point of the plumbing, so the polarity
  of this assertion flips and the refusal moves downstream to `sheet_kind_not_renderable`
  (T7.5/B3). **[measured] it would otherwise stay GREEN while proving nothing** — its profile
  passes `allow_subjective_rows: True` and no `sheet_kinds`, so M5b's accessor falls back to
  the default and it still raises. A green test whose name has stopped being true is worse
  than a red one. Its sibling `tests/test_assessment_release_run.py:1176-1183` stays green
  **UNCHANGED** for the same measured reason, and that is why it is left alone.

**REWRITTEN (same intent, new coordinates or new key set):**

* `tests/test_pre_release_lane_wiring.py:583` — the exact payload key set, with the new
  set named in S6's commit message.
* `tests/test_sources.py` (`:34` and `:151` KEEP+EXTEND with a target-layout case; the
  offsets at `:57`/`:59` migrate; **`:44-48` the one-column stub header is re-authored in
  S2**), `tests/test_question_text.py` (`:98` rewrite, `:112` keep, offsets at `:129-139`
  and `:217-227` migrate; **`:122-126` the stub header re-authored in S2**),
  `tests/test_workbook_concept_refresh.py` (`:127-131, :251, :304, :409`),
  `tests/test_data_io.py`, `tests/test_create_workbook.py` (Create Workbooks is §9
  *Keep / out of scope* yet positionally coupled — flag the out-of-scope move in the PR
  body), `tests/test_build_concepts_release.py:677` (opens `workbook[SHEET_OBJECTIVE]`),
  `tests/test_tagging.py`, `tests/test_grounding_certificate_real_deposit.py`,
  `tests/test_mes_release_skeleton.py`, `tests/test_concept_mapping_format.py` (`:45`,
  `:632` migrate).

**DELETED, with the reason:**

* `tests/test_concept_mapping_format.py:654, :669, :687` — the three `parent_concept`
  tests. The column no longer exists in the target and [verified] `writer.py:610` already
  ships it blank, so they assert a column that is gone, not a behaviour that changed.
  **The field's survival is pinned instead** by a new test that a staged record's
  `parent_concept` round-trips into the published DB row and through the release identity.
* Nothing else is deleted. **Correction to map section 8:** it lists
  `test_mes_dual_output.py:132` as "the target expressed as a test — KEEP verbatim", and
  that is right; but its §0 line "the twelve above plus
  `test_grounding_certificate_real_deposit.py` and `test_mes_release_skeleton.py`"
  double-counts `test_mes_release_skeleton.py`, which is already in its own table. The SET
  of 14 files is nonetheless exactly right — I re-ran the grep.
  A second correction: map section 8 does **not** name
  `tests/test_bulk_import_schema.py:37-39`'s contiguity invariant as a blocker, and it is
  one.

**RENAMED BY THE OD4 NUMBERING (D9/Q22).** The artifact and the assertion never change;
only the number in the name moves, and every one of these was named for the file it
exercises. Two are new tests this spec proposes; two are **existing** tests; one is an
existing assertion on a **live message string**, and it is the only edit OD4 forces into a
file this spec otherwise keeps verbatim.

*New tests proposed by this spec:*

* `test_output_01_carries_the_machine_id_pair_in_every_title_cell` →
  **`test_output_03_carries_the_machine_id_pair_in_every_title_cell`** (S8 — the **Post**
  Concept File).
* `test_output_02_builds_from_the_sound_rows` →
  **`test_output_04_builds_from_the_sound_rows`** (S9 — the **Post** Master File).

*Existing tests, [measured] by `grep -rn "output_0\|Output 0\|Output-0" tests/ --include=*.py`:*

* `tests/test_pre_release_lane_wiring.py:349`
  `test_output_04_refuses_a_job_with_no_staged_pre_release` →
  **`test_output_02_refuses_a_job_with_no_staged_pre_release`** (it exercises the **Pre**
  Master File). Renamed in **S6**, which opens `run_pre_release_for_job` for OD1/T15.
* `tests/test_pre_release_lane_wiring.py:463`
  `test_both_snapshot_writers_are_lane_correct_for_output_04` →
  **`…_for_output_02`**, same file, same slice. Note this test is in §4's KEEP list
  (`:388, :463` — the manifest twins): **its BODY stays byte-identical; only the name
  moves.** Say that in the commit message so the KEEP claim stays honest.
* `tests/test_mes_release_lifecycle.py:319`
  `pytest.raises(svc.UploadRefused, match="Output-01 identity")` →
  **`match="Output-03 identity"`**. This is the **one** line of
  `tests/test_mes_release_lifecycle.py` that OD4 forces, and §4 says all 18 of its collected
  tests stay green **unchanged**. That claim is amended here rather than quietly broken:
  **17 stay unchanged; one changes a single `match=` literal in S10**, in the same commit
  that rewrites the message it matches. No behaviour, no fixture and no assertion structure
  moves.

*And the live message strings those assertions are attached to.* [measured] **six**
user-visible strings in `app/` encode an output number — seven table rows below, because one
of the six is a two-armed ternary whose arms renumber in opposite directions (correction 1)
— and OD4 renumbers each in the slice that already opens its file:

| file:line | today | becomes | slice, and why that slice already opens the file |
|---|---|---|---|
| `assessment_release_service.py:738` | "…one exact published **Output-01** identity" | **Output-03** | **S10** — rewrites `_resolve_snapshot_concept_ids:683-741` wholesale (T4-6/T11.3) |
| `assessment_release_run.py:1309-1310` | "no staged **Output-03** Pre-Learning concept release; … before building **Output 04**" | **Output-01** / **Output 02** | **S6** — calls `run_pre_release_for_job` in-run (OD1/T15) |
| `assessment_release_run.py:1382-1383` (the **Pre** arm of `:1379-1389`'s ternary) | "**Output-03** Pre-Learning concept release; stage the Pre release before building **Output 04**" | **Output-01** / **Output 02** | S6, same function |
| `assessment_release_run.py:1386-1387` (the **Post** arm of the same ternary) | "**Output-01** concept release; stage the concept release before building **Output 02**" | **Output-03** / **Output 04** | S6, same function |
| `assessment_release_run.py:1454` | "the staged **Output-01** release has no question/task inventory" | **Output-03** | S6, same function |
| `assessment_release_run.py:1459` | "the staged **Output-01** release has no target chapter identity" | **lane-generic: "the staged concept release has no target chapter identity"** | S6, same function |
| `build_concepts_release.py:1895` | `where="the staged Pre-Learning release payload (**Output 03**)"` | **Output 01** | **S9** — rewrites `:1918-1975` and the state split |
| `build_concepts_release.py:1908` | "**Output 03** was refused at its release boundary and ships …" | **Output 01** | S9, same region |

**Two corrections to an earlier version of this table, both from reading the lines:**

1. **`:1382-1387` is ONE expression with TWO arms and they renumber in OPPOSITE
   directions.** [verified] `:1379-1389` is
   `raise ReleaseRunError("this job has no staged " + (<Pre arm> if normalize_lane(lane) ==
   LANE_PRE else <Post arm>))`. The earlier table collapsed both into a single "becomes
   Output-03 / Output 04", which applies the Post arm's mapping to the Pre arm and **inverts
   it**: a reviewer on the Pre lane would be told to stage Output-03 before building Output
   04, when under OD4 the Pre files are 01 and 02. Split into two rows above.
2. **`:1459` is lane-GENERIC and must stop naming a lane at all.** [verified] `:1452`'s
   `if not generate_lane` guards `:1454`, so that message really is the source/Post lane's
   and "Output-03" is correct for it. But `chapter_id` is read unguarded at `:1456` and
   `:1459` fires for **both** lanes, so renumbering it to "Output-03" would make it wrong on
   every Pre run — the same defect as today, pointed the other way. It becomes lane-generic.
   This is the one string of the six that OD4 does not merely renumber, and the reason it was
   missed is instructive: the earlier table read the string, not the branch it sits in.

Every other citation in this spec is a PAIR (`Outputs 01/03` = the two Concept Files,
`Outputs 02/04` = the two Master Files). Both pairs are the same SET under either numbering,
so they read correctly unchanged — which is why the sweep is this small.

**NEW files:** `tests/test_bulk_import_layouts.py` (S2),
`tests/test_workbook_layout_migration.py` (S7), `tests/test_release_core.py` (S6),
`tests/test_release_identity.py` (S4), `tests/test_release_publication.py` (S10),
`tests/test_release_qc.py` (S11), plus the parametrised second-profile acceptance test
(S8, in the test tree only).

**Count discipline.** Baseline 2324/6. Step 8 adds roughly **80** tests (the first draft
said 45; the first repair round added about 20 — the T4-7 minter, the T4-8
backfill and import restore, T6.4's lane key, T3.8's row width, T3.3b's publication
survival, T7.5's unplaced defect, T10-0's audit seam, T14's manifest enumeration and
C5b's frozen-literal pins; **this round adds about 15 more** — T3.1b's two workbook/manifest
pins, T4-9's two import-graph pins, T7.5's three refusal-and-deletion pins, T15's one-run
pin, T16's four questionless-concept pins, T17's Concept-file shape pin, OD4's two manifest
pins and S7's fixture-layout pin; **round 4 adds about 8 more and renames 4** — T15-2's
assessment-lane containment test parametrised over the four exits, B4's two raise pins
(`test_the_master_renderer_contains_no_raise`,
`test_every_group_edge_raise_became_a_named_defect`), B3's `reference-1` negative control and
its recorded-golden-profile pin, T12/M5b's one-accessor pin, and T17/B1's Concept-file pin
which is **split into two** — one in S7 against Outputs 01/03 and one in S8 against the sealed
projection), re-authors **7** (the five already listed plus
`tests/test_assessment_cells.py:428-445`, whose name stops being true once `sheet_kinds`
lands, and S8's subjective-candidate regression), **renames 3 existing test names, one
`match=` literal and 3 of this spec's own proposed test names (OD4/B1/B3/B5 — renames and
literal edits, never deletions)**, deletes 3, and must close **above 2324**. Any lower number
is deleted coverage and is accounted for test-by-test by name in the PR body.

---

## 6. THE DOC AMENDMENTS

Each is a §12 register entry to add to `docs/aegis-restructure.md`, in the recording style
of Q7's amendment at `:867-876`. All are recording acts with this spec as provenance;
none reopens a decided question.

### D1 · §6's Topic and Concept ID patterns, and the self-contradiction in adjacent lines

> **Q14 · Recorded — §6:517-518 is superseded by §6:525-526; the ID patterns are
> restated.**
>
> §6's table at line 517 gives the Topic pattern as `<Class><Board><Subj>_<Chapter>_PL|PrL`
> with no topic discriminator, while line 525-526 in the same section says "the current
> writer stamps one chapter-level code on all topics; the restructured writer mints
> per-topic and per-concept IDs natively", and §9:697 lists that rebuild. The table was a
> transcription of the defect. **:525-526 governs.** The patterns become:
>
> | Record | ID pattern |
> |---|---|
> | Chapter key | `<Grade><Board><Subj>_<ChapterSlug12>_<h8>` |
> | Topic | `<ChapterKey>_<PL\|PrL>_T##` |
> | Concept | `<TopicID>_C##` |
> | Group | `(<ConceptID>) BG\|IG\|AG##` |
> | Question | `<ConceptID> Q##` |
>
> `h8` is the first 8 hex characters of a sha256 over the chapter's identity tuple. The
> readable name is **not** inside the id: it is carried beside it in the cell as §6:522's
> "name + (machine ID)" pair. Reason: `directory._underscore_slug` keeps only
> `[A-Za-z0-9]+` and falls back to `"X"`, so a name-derived id is not unique for any
> non-Latin source — two different Marathi chapters mint an identical concept tag — and a
> name-bearing id produces a 130-character `question_label` against a `String(128)`
> column. The id is **minted once and persisted** on `models.Topic.machine_id` /
> `models.Concept.machine_id`; it is never re-derived, because no formula is stable under
> the rename/merge/split/re-tag §7:577 permits, and §6:523's "unique and stable forever"
> is therefore a property of storage.
>
> **One minter, named.** `generation.question_label` (`generation.py:107-114`) becomes
> `f"{concept.machine_id} Q{n:02d}"` and is the ONLY producer of the Question pattern;
> `generation._topic_index` (`:77-104`) is retired, and its own docstring's reason for
> existing — "`question_label` carries a literal `_PL_` segment and no lane discriminator"
> — is what the `<PL|PrL>` token in the Topic pattern removes. **Existing rows** acquire
> their id in `db._backfill_and_normalize`, positioned by
> `writer._source_order_key` (`source_order` when set, otherwise creation id), filling
> blanks only and never overwriting. **The importer restores rather than erases:**
> `bulk_import.reader` writes a round-tripping title tag into `machine_id` on a newly
> created row, records a `machine_id_conflict` issue when it disagrees with a persisted
> one — where the persisted value wins — and notes a tagless row instead of refusing it.

### D2 · §6:520's question-label separator

> **Q15 · Recorded — the question-label separator is a SPACE, not an underscore.**
>
> §6:520 writes `..._T##_<Concept>_Q##`. Every accepted gold label and both live minters
> use a space (`generation.py:113`, `assessment_release_run.py:2155`, gold
> `06MSSC_Charat_PL_T01_GrowthAndReproductionA Q01`). Q5 settles layout, not naming, so
> this is decided on evidence: the space stands. Changing it buys nothing and costs a
> numbering restart on every published base, because four sites parse it —
> `assessment_release_run.py:1255`, `:2155`, `tagging.py:43`,
> `build_assessments.py:218` — and the last two feed `assessment_grouping.group_key_for`,
> so a separator change silently corrupts group keys as well as numbering.

### D3 · Q5's "remain readable"

> **Q16 · Recorded — Q5's "older canonical-layout workbooks remain readable through
> auto-detection" becomes "remain readable AND appendable".**
>
> A workbook on a registered older layout is **identified by its exact header signature**,
> read **by name**, and migrated once — recorded, with the pre-migration file retained as
> a sibling and a receipt naming source and target layout ids, per-sheet row counts and
> sha256 before and after — before the first append in the current layout. An unrecognised
> header is refused (HTTP 422 on import); it is never guessed. Reason: today the reader
> silently drops the reference Objective sheet (a trailing space in a sheet-name constant)
> and mis-bands the other two — 342 of 344 Descriptive and 63 of 63 Subjective
> question-band positions — with zero issues logged, on an authenticated POST endpoint.
> A migration that would drop a non-blank value routes to The Fixer with a recorded
> release issue; it never halts a run.
>
> The migration is `bulk_import.layouts.migrate_workbook_layout(path, *,
> target_layout_id) -> MigrationReceipt`, called once from `bulk_import.writer.
> append_concepts` on the **staged sibling** the transactional outbox already provides, and
> serialized by `workbook_sync.output_workbook_lock()` (an `RLock`, so it composes with the
> five callers that already hold it). It never raises on row content; an unmappable
> non-blank value lands in the receipt's `unmappable` list.
>
> **What migrates, stated precisely, because an earlier draft named the wrong file.** The
> subject is `config.BULK_IMPORT_OUTPUT` (`bulk_import_output.xlsx`) — the append-only
> generation accumulator, which is **gitignored runtime state on the deployed volume**
> (`.gitignore:46-47`, "The append-only generation output is runtime state, not source").
> It is **not** `config.BULK_IMPORT_DB` (`bulk_import_database.xlsx`), which nothing appends
> to: `.gitignore:57` records it as the committed **database fixture**, its only writers are
> `scripts/generate_dummy_data.py` and `data_reset`'s deletion, and CI regenerates it
> through `app.bulk_import.writer` before every pytest run
> (`.github/workflows/ci.yml:28-29`). It therefore acquires the new layout automatically
> when the writer moves, needs no migration, no coordination window and no sequence-of-paths
> plumbing, and the single obligation is to commit the regenerated fixture in the same
> commit as the writer move.
>
> **The layout the target entry describes is read from the committed format workbook**
> `backend/data/Testing/reference_bulk_import/bulk_import_format.xlsx`, not transcribed.
> Where that workbook and `assessment_workbook_template.json` disagree, **the workbook
> wins** and the disagreement is a named `layout_manifest_drift` defect: a transcription in
> the trust chain is the same defect shape as the trailing-space sheet-name constant this
> amendment exists to close.

### D4 · §4's release states and the empty-but-decided lane

> **Q17 · Recorded — §4's three release states stand; there is no fourth.**
>
> `structural_defects` is split from `nothing_to_publish`. A lane with nothing to write is
> *Ready* and its publication is an idempotent zero-row success with a receipt — **only
> when a recorded Phase-03 model verdict says the chapter assumes no prior knowledge.**
> Where the capture merely produced nothing and no verdict exists, the release stays
> *Diagnostic*. Reason: `phase3/premap.py:860-879` returns the empty map "without spending
> a decision", so the mere presence of a Pre map cannot distinguish "this chapter needs
> nothing" from "the capture failed to reach it" — and inferring the first from an empty
> list is the shape-matching Rule 1 forbids. Step 8 makes that one verdict explicit.
> Separately: nothing blocks a download. Every raise inside a shared artifact builder
> becomes a recorded defect, so a malformed staged row costs its own row and a named issue,
> never the four outputs.
>
> **And the converse, so the split is not read as permission to downgrade everything:** a
> candidate the renderer writes to **no sheet at all** is a structural defect, never a
> warning. `assessment_workbook.render_master_file` has three such branches — an unresolved
> home concept/group, a candidate whose `concept_key` disagrees with its group's home, and a
> sheet kind the profile allows that **this step's renderer cannot write** — and each of them
> `continue`s (or, for the second, raises) before the only function that appends a row.
> §4:470-474's "flags never block" governs semantic doubt on a row that ships; a row that
> does not ship is R4 exactly-once loss. All three become named defects
> (`unresolved_question_home`, `group_home_disagreement`, `sheet_kind_not_renderable`):
> *Diagnostic release*, all four downloads open, database write blocked, and every such
> candidate named on the release's Issues sheet.
>
> **The renderable set is a property of the CODE, not of the profile and not of the layout,
> and saying otherwise makes the third defect unreachable.** The layout of record carries a
> full Subjective sheet and a widened profile allows the kind, so "allowed by the profile,
> absent from the layout" has no member; what is genuinely absent is a renderer —
> `assessment_workbook._question_record` has an Objective branch and a Descriptive branch and
> nothing else, so a Subjective candidate today raises and costs all four workbooks. The set
> is named as `assessment_workbook.RENDERABLE_SHEET_KINDS` and widened by the step that
> teaches that function the 144-column Subjective answer blocks. One profile key,
> `sheet_kinds`, answers "what may this school carry"; one code constant answers "what can we
> write today"; no key answers either question twice.
>
> **And no raise survives inside the Master renderer.** All seven of
> `render_master_file`'s `WorkbookRenderError` raises and all seven of `_question_record`'s
> become recorded verdicts computed at staging — six of the fourteen already had a staging
> twin in `validate_group`, `duplicate_group_keys` or `validate_candidate` and are simply
> deleted. A raise there is not a stricter gate; it is the same verdict delivered where it
> costs every row on every sheet instead of one named defect.
>
> **Where that verdict is computed, which is the part a reader must not get wrong.** It is
> computed at **STAGING**, from the frozen snapshot's candidates, groups and concept-key set
> plus the active profile's sheet map — never from anything the renderer discovers. The
> renderer runs at projection time, after the payload is frozen, so a verdict it produced
> could reach the release only by mutating an immutable snapshot during a GET. The verdict
> rides `diagnostics["payload_errors"]`, the same transport a duplicate `question_label`
> already uses, and is refused at the assessment lane's publication act. The renderer keeps
> listing the candidates as evidence and decides nothing.

### D5 · The migration route's real reach

> **Q18 · Recorded — the additive migration is SQLite-only, and that is a deployment
> property, not a code guarantee.**
>
> `db.py:42-45 _ensure_columns` returns early for any non-SQLite `AEGIS_DB_URL` and emits
> only `ALTER TABLE … ADD COLUMN` plus `CREATE INDEX IF NOT EXISTS`; there is no alembic,
> no `migrations/`, no `*.sql` anywhere in the repo. `fly.toml:16` pins
> `sqlite:////data/aegis.db`, so every column and index this step adds is live on the
> current deployment and absent on any other. `models.AssessmentRelease.__tablename__`
> stays `"assessment_releases"` — the name is historical and a rename is not expressible:
> `create_all` would mint a new empty table and orphan every published release, its
> receipt and its workbook hashes.

### D6 · Where the QC checklist lives, and what may block

> **Q19 · Recorded — the SOP §7 pre-upload QC checklist is reconstructed in-repo, and its
> default verdict is a flag.**
>
> The SOP is not committed; §6:536-537's checklist is reconstructed as
> `docs/release-qc-checklist.md` and `app/services/release_qc.py` from §4 Phase 05, §5, §6
> and Rules A-G, with gold-workbook agreement as corroborating evidence for mechanical
> items only. **Every item's default verdict is a flag on the shipped row.** Only a closed,
> enumerated set may block, and blocking means the **database write** only — never a
> download, never the run: identity, arithmetic, exactly-once accounting, and
> schema/layout. An item that would need to decide what the source *means* is recorded as
> JUDGMENT with no mechanical check, so that nobody adds a regex to it later. Consequence
> the owner should expect: flagged rows increase, deliberately and visibly. That is Q10 and
> Q13 applied to the release boundary.

### D7 · The reference workbooks' tags are not reproducible, and are not a target

> **Q20 · Recorded — the accepted gold's machine tags were not produced by any live code
> path, and byte-exact reproduction of them is not a design target.**
>
> The gold's chapter segment is CamelCase-concatenated
> (`06MSSC_CharateristicsOfLivingOrganisms_PL_Growth_and_Reproduction`) while
> `directory.concept_tag` produces
> `06MSSC_Charateristics_of_Living_Organisms_PL_Growth_and_Reproduction`. The repo carries
> three chapter-slug conventions and the accepted corpus uses a fourth. Q5 settles the
> column layout; a tag inside a cell value is not a column, so Q5 settles nothing here and
> Q14 does. Any future claim that a minted shape "matches the gold" must be executed, not
> read.

### D8 · Which file each of the four outputs IS

> **Q21 · Recorded — §4 Phase 05's four outputs are bound to named builders, and the
> Concept-file projection is sealed evidence, not a fifth output.**
>
> Outputs **01** and **03** — the two Concept Files — are `build_concepts_release_files.
> build_release_bulk_import_workbook(db, job, lane=…)` — the transient release hierarchy
> before the database upload, `bulk_import.writer.write_concepts_workbook` after it —
> served at `/build-concepts/uploads/{job}/release-bulk-import.xlsx`, with `?lane=pre` for
> **01** (Pre) and no lane parameter for **03** (Post), per the numbering Q22 records.
> Outputs **02** and **04** — the two Master Files — are
> `assessment_workbook.render_master_file` via
> `build_dual_output`, written as `aegis_master.xlsx` under the lane's release staging
> directory and served at `/build-assessments/releases/{id}/master.xlsx`.
> `aegis_concepts.xlsx` — `build_dual_output`'s `concepts_xlsx` — is **not** an output: it
> is the sealed Concept-file projection whose sha256 the publication-hardening invariant
> re-verifies, and no manifest entry points at it. `release.xlsx`, `diagnostics.zip` and
> `release.json` are evidence artifacts, not outputs.
>
> **The consequence for the Concept File's shape, because two functions in two modules share
> the names `_new_workbook` and "concept file" and an earlier draft answered the owner's
> ruling against the wrong one.** "Filled from the first column through the concept-related
> columns" is a rule about **Outputs 01 and 03**, so it binds
> `build_release_bulk_import_workbook` and the `bulk_import.writer` functions it calls —
> `write_concepts_workbook`, `_new_workbook`, `_concept_to_row` — and not
> `assessment_workbook.render_concept_file`, which builds the sealed projection. On the
> columns the rule already holds there (`_concept_to_row` builds the front bands with
> `include_group_columns=False` and pads the rest); on the SHEETS it does not, because
> `bulk_import.writer._new_workbook` emits a fourth "Doc Link" sheet that no registered
> layout describes and orders the other three differently from the format workbook. Removing
> it is part of the layout move, not of the renderer work. The sealed projection is held to
> the identical shape, and a regression compares the two, because a projection whose shape
> drifts from the artifact it projects is a sha256 that proves nothing.
>
> **The release manifest enumerates all four, in both lanes.** Before step 8 it enumerated
> none of them: its eight entries were the review workbook, the diagnostics zip, the
> release JSON and the upload action, twice. A reviewer who cannot see four outputs in one
> place cannot check that four exist.
>
> **The `name + (machine ID)` composition and the `topic_concept_labels` roster are shared
> code, not one renderer's private detail.** Both the bulk-import writer and the
> assessment-workbook renderers call the same helper. §6:522's identity pair and
> §6:509-512's five-level join are properties of *every* projection, so no convergence may
> be implemented by moving them out of one of the two.

### D9 · The output numbering — OWNER RULING OD4. Two edits to the doc: the Phase 05 table, and the register entry.

**Edit 1 — replace `docs/aegis-restructure.md:416-421` (the §4 Phase 05 output table)
with exactly this.** The four rows are the four the doc already has; only the numbers and
the row order change, and the "As Output 01" cross-reference in the Concept row is
re-pointed so the table stays self-consistent.

```markdown
| Output | Contents |
|---|---|
| **01 · Pre-Learning Concept Review** | Everything up to the Concepts column: Chapter, Topic and Concept bands filled, one row per concept, full concept detailing — for the Pre-Learning map. Opens as a rendered, editable page (§7). |
| **02 · Pre-Learning Master File** | All columns filled: the generated pre-learning questions (adaptive target 40 per concept), grouped and marked the same way as Output 04. |
| **03 · Post-Learning Concept Review** | As Output 01, for the Post-Learning map. |
| **04 · Post-Learning Master File** | All columns filled, including assessments: every source question, polished, on its answer-style sheet (Objective / Subjective / Descriptive), with Groups, master records, categories, cognitive skills, difficulty, `answer_restriction`, marks and marking. |
```

**Edit 2 — add this §12 register entry**, in the recording style of Q7's amendment at
`:867-876`:

> **Q22 · Recorded — the four outputs are numbered Pre first: 01/02 Pre, 03/04 Post. The
> §4 Phase 05 table is corrected; nothing else changes.**
>
> §4 Phase 05's table numbered them 01 Post Concept, 02 Post Master, 03 Pre Concept, 04 Pre
> Master. The owner's numbering is the reverse and it governs: **Output 01 · Pre-Learning
> Concept File, Output 02 · Pre-Learning Master File, Output 03 · Post-Learning Concept
> File, Output 04 · Post-Learning Master File** — "four separate files serving four
> separate purposes." This is a numbering and ordering decision, not a product change: no
> artifact, builder, filename, route, database write or column moves, and Q5's layout
> family, Q3's one-run contract and §7's one action are untouched. The release manifest
> lists the four in this order, then the three evidence artifacts (the review workbook, the
> diagnostics zip and the release JSON), which are not outputs (Q21).
>
> **Provenance for a reader of the earlier PRs:** step 7 shipped what its PR text called
> "Outputs 03-04" — the Pre-Learning lane. Under this numbering the same work is **Outputs
> 01-02**. The earlier text is not wrong; it is superseded, and the two numberings describe
> one deliverable, not two.
>
> **What in the code carried the old numbers, and what step 8 does about it.** No manifest
> `kind` string carries a digit — all eight are lane-prefixed semantic names
> (`released_concepts` … `pre_database_upload`) — and the kinds added for the four outputs
> follow that convention deliberately, so a future renumbering can never break the
> frontend. No frontend label carries one either; the single frontend occurrence is a code
> comment. **Six live user-facing strings do** — one `UploadRefused` message
> (`assessment_release_service.py:738`), four `ReleaseRunError` messages
> (`assessment_release_run.py:1309-1310`, `:1382-1387`, `:1454`, `:1459`) and one release
> issue (`build_concepts_release.py:1895`, `:1908`) — and each is renumbered in the slice
> that already opens its function, with the two existing tests that assert on them moving in
> the same commit. **Two of those five are not simple renumberings, and §5's table carries
> both:** `:1382-1387` is one raise with a two-armed ternary whose Pre and Post arms move in
> OPPOSITE directions, and `:1459` fires on **both** lanes (its `chapter_id` read at `:1456`
> is not inside `:1452`'s `if not generate_lane` guard, unlike `:1454`'s), so it stops naming
> a lane at all and becomes lane-generic. A message that names one lane while firing for two
> is the defect this amendment exists to remove, not a number to update. Backend docstrings and comments carrying the old
> numbering are renumbered only in the files each slice already opens; a repo-wide comment
> sweep is deliberately out of scope, because an unreviewable comment diff inside a large
> PR is where a real change hides.

---

## 7. OPEN — nothing. All three questions are answered.

**This section is deliberately empty of questions, and that is a fact about the round, not
an omission.** OR1, OR2 and OR3 were the three items this spec escalated. All three now
carry owner rulings and are folded into §2 as DECISIONS:

| was | ruling | now lives in |
|---|---|---|
| **OR1** — does "One action: Build Concepts" build Outputs 02/04 in the run, or on a second click? | **OD1: the run builds all four.** §7:541 and Q3 taken literally. Every chapter pays the assessment-lane spend, including discarded ones. | **T15**, and S6 (the fallback bullet is deleted) |
| **OR2** — migrate `bulk_import_database.xlsx` in place, or freeze it and start a new file? | **OD2: the question's premise was false.** Nothing appends to that file; it is the committed CI-regenerated fixture. The migration subject is `config.BULK_IMPORT_OUTPUT`, gitignored runtime state. No ruling was needed and no fallback is built. | **T7.6a**, T12/M9, S7, D3/Q16 |
| **OR3** — does the reference school's importer accept `keywords` / `related_concepts`, or require them blank? | **OD3: fill them.** The gold leaving them blank is fill practice, not a rule. `forced_blank_fields` stays as the one-line lever. | **T3.3** (the OD3 paragraph), T3.2, T12/M4 |

Two further owner rulings arrived with them and are also decisions, not questions:
**OD4** the output numbering (**D9/Q22** in §6, swept through §1-§6 and §9), **OD5**
questionless concepts in the Master (**T16**) and **OD6** the Concept File's shape
(**T17**).

**Four candidate rulings were REJECTED as doctrine-derivable and are recorded so nobody
escalates them:** whether already-published labels are grandfathered (R5 settles it —
grandfather); whether a stale name inside a persisted id is acceptable (moot — Q14 puts no
name inside the id); whether a zero-row publication counts as DONE downstream (no backend
work turns on the answer); and whether `concept_source` leaving the Descriptive sheet needs
a ruling (it does not — every concept catalogue row is written to the Objective sheet,
which keeps the column).

**One thing genuinely remains the owner's, and it is not a blocker — it is a number only a
future run can produce.** OD1 makes every chapter pay the full assessment-lane model spend
before the reviewer has read the Concept File. Whether that is acceptable **at R7's
"thousands of chapters"** is a budget judgment, and this repo cannot compute it: the
per-chapter call count is a function of concept, group and question counts, none of which
step 8 may derive from volume (CLAUDE.md:17-18), and no cost telemetry exists here. It is
**not** a question step 8 waits on — the ruling is implemented as given. What step 8 owes is
that the number becomes measurable: S6 writes the assessment lane's provider/model/prompt
identity into `AssessmentRelease.provider_identity` (T2/MC-K), so the first real run
produces the figure the owner would need in order to revisit OD1. Recorded here rather than
in a decision, because there is nothing to decide until that figure exists.

---

## 8. CORRECTIONS TO map-step8.md

Every correction below was re-measured by me on this tree at HEAD `6335fe6` and re-checked
at `7f71b16`; the only file `7f71b16` adds is the committed format workbook (T3.1b), which
moves no coordinate.

**MC-A · HEADLINE — the map's baseline and all three of its frozen-core hashes are
STALE.** The map records HEAD `19ed7e4`; the branch is at `6335fe6`, which edited the
frozen-core prompt `concepts.canonicalize.system`. [measured] `empty_set_sha256()` =
`88a68507…332eff`, **not** §9's `a5f280cb…676539`; doc-order append is now
`039bd4ca…7c930` (map said `cfe3aa84…`) and alphabetical is `9b5552cd…b784a` (map said
`2b5fe15c…`). Corroborated by 6335fe6's own commit message. The map's §11 C15 correction
was right about order-dependence and wrong about the values.

**MC-B · The suite baseline is 2324/6, not 2314/6.** [measured] `2324 passed, 6 xfailed,
7 warnings in 142.42s`, exit 0, from a fresh isolation dir. 6335fe6 added 10 tests.

**MC-C · Sweep H1 no longer exists.** [measured] `grep -n "_CANONICALIZE_MIN\|
structural_floor\|min_keep" app/services/generation.py` returns no identifier. The sweep's
rank-2 remediation item is done; do not re-open it. `_GENERIC_SKELETON_FAMILY_RE` survives
at `generation.py:15857` but now feeds family-identity accounting, not a numeric floor.

**MC-D · Every `generation.py` line number in the map is stale.** `git diff --stat
19ed7e4..6335fe6` → generation.py +492/-146. Re-resolved: `_FATAL_CODES` **:12871** (map
:12830); `_FIXER_UNACCEPTABLE_CODES` **:12907** (map :12863-12868);
`_dedupe_titles_chapter_wide` def **:16624** with call sites **:16263, :16268, :16503**
(map :16358-16389 / :15999, :16004, :16237 — that range now points into
`_coalesce_method_family_rows`). Anyone implementing from the map must re-grep every
`generation.py` reference; the substantive claims survive, the coordinates do not.

**MC-E · "(b) discards the only publication path that is actually hardened" (§2,
convergence option b) is FALSE.** A has a transactional-outbox publication at
`build_concepts.py:1788-1830`: shared workbook lock, `recover_pending_publication` first,
`record_publication_intent` durably BEFORE `db.commit()`, atomic publish, clear intent,
rollback when the failure precedes the commit. A lacks artifact-hash verification and a
receipt; B lacks the append-only workbook outbox. Acting on the map's sentence would delete
A's outbox as redundant and lose R5's crash-safety.

**MC-F · "one malformed staged row means no download of any kind, for any of the four
outputs" (§6, Rule E) is FALSE.** [measured] `transient_release_hierarchy` has exactly two
call sites, `build_concepts_release_files.py:81` and `assessment_release_snapshot.py:115`;
`build_release_workbook:308`, `release_payload_bytes:503` and `build_diagnostics_zip:597`
never call it. On a real staged empty Pre release: `release-bulk-import.xlsx` **RAISES**,
`release.xlsx` OK 7841 B, `diagnostics.zip` OK ~14.25 kB, `release.json` OK 1374 B. What is
lost is the bulk-import export and Outputs 02/04. The fix is real but narrower — **and
wider in one respect the map missed: there are EIGHT raises inside that function**
(`:128, :133, :136, :141, :145, :149, :153, :158`), not seven, and `:149`/`:153` fire on a
single malformed row.

**MC-G · "`clean_concept_record` at publication is the likely cause of
`_resolve_snapshot_concept_ids` refusals" (§6) is UNSUPPORTED — and the real cause is
findable.** [measured] the cleaner is idempotent (`clean(clean(x)) == clean(x)`) and the
staged rows are already cleaned upstream, so on the generation path the published row is
byte-identical to the staged row. The other half of the map's claim is true and worse than
stated: it rewrites chosen wording (`"pH and its meaning" → "pH and Its Meaning"`), deletes
figure references from `concept_details`, downcases "The Elevator", and is a NO-OP on
Devanagari. **The refusal cause is `build_concepts._find_or_create_topic:576`**, which
matches leniently and then rewrites `t.topic_title` to the incoming casing, while
`_resolve_snapshot_concept_ids:711-716` matches `Topic.topic_title` EXACTLY — reproduced on
a real SQLite session (see T4-6).

**MC-H · "release state is computed and never surfaced by any endpoint" (§2) is imprecise,
and the imprecision inverts which lane is broken.** The Post `release_result` dict IS the
terminal `{"type":"result"}` NDJSON event, consumed at `frontend/src/RunConsole.tsx:97`; it
is missing the five FIELDS, not the surface. The Pre branch's five fields are the ones that
reach nobody, because `_stage_pre_sibling` discards `stage_pre_release_from_run`'s return
value. **And the map's fix is incomplete in the other direction:** [measured]
`RunConsole.tsx:97-99` reads only `usageFromResult(evt.data)` and `DocumentUpload.tsx:729-
749` renders only eight `ActionableArtifact` fields, none of them a state — so adding
`release_state` to the payload and the manifest still displays nothing.

**MC-I · The capability table's "Lane (Pre/Post) awareness: B = derived only" understates
B.** `run_release_for_job` takes an explicit `lane` (`assessment_release_run.py:1327`) and
reads the slot through `staged_release_for_lane`, refusing lane-specifically. What B
genuinely lacks is a PERSISTED lane on the release ROW. The precise defect is
"unrecorded", not "underived".

**MC-J · §7 P1 misses that `build_dual_output` already takes a profile.** Its signature is
`build_dual_output(snapshot, profile=None)` (`assessment_workbook.py:974-976`) and the
manifest records `"profile": profile["name"]` (`:988`). The defect is the CALL SITE
(`assessment_release_service.py:356` passes none) and `create_release` never persisting it.
Worse than stated, and this is what makes T1's routing recommendation unsafe: **the profile
never reaches EITHER renderer**, only `validate_master_file`.

**MC-K · `models.AssessmentRelease.provider_identity` (`models.py:435`) is declared and
NEVER written** — [measured] one grep hit, the declaration. It is a free, zero-migration
home for §3 guard 2's run-context pinning and for the discarded profile, which materially
cheapens T1/T2.

**MC-L · §3's Subjective delta is off by one.** [measured] 53 only-target fields = 3
(`answer_restriction`, `keywords`, `related_concepts`) + **50** answer-block fields (blocks
11-20 × {answer_type, answer, answer_display, weightage, placeholder}), not 49. The
field-name range is right.

**MC-M · §3's band table understates what a span list cannot express.** Beyond the
Objective col-23 gap the map names: [measured] the reference Descriptive **Group band field
ORDER differs** from Objective's (`group_display_name, group_description, group_name` vs
`group_name, group_display_name, group_description`), and the target's bands cover only 30
of 67 Objective columns, 70 of 374 Descriptive and 144 of 144 Subjective. A second and
third reason one shared list cannot express the target — and the reason
`tests/test_bulk_import_schema.py:37-39`'s contiguity assertion must be replaced rather
than migrated.

**MC-N · §4's "12 scalars / 11 for subjective / MES has 13" is wrong in both directions.**
[measured] counting from `question_label` inclusive to the first `answer_type_*`: reference
Objective **12**, Subjective **13**, Descriptive **14**; canonical 10 / 11 / 15 (Descriptive
canonical's 15 is measured from its *second* `question_label` at idx 30, which is why
`reader.py:108`'s `+12` is self-consistent for canonical). The reader's 10/11/12 are each
short by 2 against the target. The map leaves Descriptive implicit. **And the map does not
name the block-count divergence at all:** the reference Subjective sheet carries **20**
answer blocks against the reader's `range(10)`.

**MC-O · §5's "changing that constructor changes `records`, hence the release sha, hence
every id" is FALSE, and the hard ordering constraint built on it does not exist.**
[measured] `assessment_release_snapshot.py:112` computes `release_sha` **before**
`transient_release_hierarchy` is called at `:115`, and `source_release_sha256:57-80` hashes
payload keys only. Adding `related_concepts=`/`digicards=` to the transient `models.Concept`
cannot move `concept_key` or `machine_id`. Both Cluster B and Cluster C repeated this claim
from the map; all three are wrong. The ID work may — and in this spec does — land **before**
the column work, which is strictly better than "atomically with".

**MC-P · §5's ID table misses two production `group_key` bases and one topic-identity
divergence.** `group_key` has three bases, not one: `REL<sha12>C###` (snapshot), the
truncated label base (`tagging.py:43`, `build_assessments.py:218` — neither reads a
persisted value), and the friendly `group_name` (`reader.py:397`). And three different
topic identities are live: `build_concepts_release_files.py:185` `casefold()`,
`build_concepts_release_publication.py:126` `normalize_question_text`,
`build_concepts._find_or_create_topic:569-585` strip-then-normalise.

**MC-Q · §5's T11 evidence is incomplete in the way that matters.** It names
`_dedupe_titles_chapter_wide` and `concept_validator`'s `duplicate_title`. The site that
actually destroys content is `build_concepts._find_concept_in_chapter:551-566` as used at
`build_concepts_release_publication.py:137` → `existing.topic = topic` (`:154`) →
`db.delete(tag)` (`:155-163`) → full content overwrite (`:164-170`), with
`concept_validator` never invoked on that path. The map mentions that function only as a
tag-stripping boundary.

**MC-R · §7 P5's subject-code claim is board-dependent and one code is wrong.** [measured]
Sociology and Social Studies both → **"SO"** on MSBSHSE, ICSE and KSEEB; they are "SS" only
on CBSE, where `effective_subject_for_tags` folds both into Social Science. The
Marathi/Mathematics → `MA` and Physical Education/Physics → `PH` collisions reproduce on
every board tested. The board-dependence matters because the reference school's own board
is MSBSHSE.

**MC-S · §7 P8's framing does not survive measurement, but neither does the sweep's.**
[measured] every gold row uses `Less`/`Moderate` and Bloom action verbs, so
`COGNITIVE_SKILLS`/`DIFFICULTY_LEVELS` are the reference workbooks' own wire values, not a
portability defect to fix in step 8. But the sweep's counter-reason ("the destination
product's DB columns") is also false: `models.py:125` and `:132` are `String(64)` and
`String(16)` free text with no enum and no constraint. Ruled not-step-8 on the honest
reason: nothing in the repo proves them universal and nothing proves them school-specific.

**MC-T · §7 P3b understates itself, and misses its twin.** The `max(40, n_concepts * 12)`
duration at `build_concepts.py:1209-1211` is live at HEAD, and so is
`build_concepts.py:1198-1201`'s `chapter_description = "This chapter develops {n} concept(s)
across {m} topic(s): …"` — code-composed recap text scaled from counts, on §3's own purge
list, and [verified] carried into the snapshot chapter dict at
`assessment_release_snapshot.py:216`, so the migration would ship it in all four outputs.

**MC-U · §8's acceptance-gate caveat is right and understates the good news.** [measured]
the harness's only app import is `assessment_workbook`; its two schema-constant grep
matches are its own LOCAL `_CONCEPT_FIELDS` tuple, so it is a **false positive** in the
14-file positional-coupling list; and no golden carries a tag or a label. The Q5 gate
therefore cannot break on any tag or label change — which removes the largest perceived
blast radius from the ID rebuild and should be stated positively.

**MC-V · Three things the map does not carry at all, each a live defect on the path step 8
rewrites.** (1) Both snapshot builders hard-code `topic_concept_labels: ""`
(`assessment_release_snapshot.py:199`, `assessment_release_service.py:118`) while the gold
fills it on 23/23 rows — so migrating Outputs 01/03 onto that renderer deletes the §6
five-level join. (2) `build_concepts_release_publication.py:100-106` silently drops any
staged row missing a topic or concept title; [measured] `structural_defects → []`,
`release_state → "ready"`, one of two rows gone. (3)
`concept_cleanup.filter_review_violations`, live on the release path at
`release_refiner.py:271` and `phase3/assemble.py:900`, **deletes** rows by English topic
name; [measured] 5 in, 2 out, with the Marathi equivalent surviving.

**MC-X · THE SECOND HEADLINE — `backend/data/bulk_import_database.xlsx` is NOT "the
checked-in append-only production workbook appended to under a process-wide lock by five
call sites". Nothing appends to it.** [measured]
`grep -rn "BULK_IMPORT_DB" backend --include=*.py` returns six sites and not one is an
append: `config.py:70` (the declaration), `data_reset.py:38-40` (**deletes** it), `:68`
(reports existence), `scripts/generate_dummy_data.py:39` (**writes** it, through
`app.bulk_import.writer`), and `tests/conftest.py:20,31` + `tests/test_data_reset.py:11,22`
(read it). `.gitignore:57` says so in the repo's own words — *"data/bulk_import_database.xlsx
IS committed — it is the database fixture."* — and `.github/workflows/ci.yml:28-29` runs
`python scripts/generate_dummy_data.py` immediately before `pytest -q`, so **CI regenerates
it** and the layout move carries it automatically. The **real** append-only accumulator is
`config.py:72`'s `BULK_IMPORT_OUTPUT` (`bulk_import_output.xlsx`), which is **gitignored**
(`.gitignore:46-47`, "The append-only generation output is runtime state, not source") and
whose consumers are `build_concepts.py:1923`, `post_generation.py:118`, `tagging.py:183`,
`api/data.py:66-70` and `build_concepts_release_publication.py:225`. And the "five call
sites" were `output_workbook_lock()` **acquisitions**, not appends
(`build_concepts.py:1805`, `:1865`; `build_assessments.py:691`, `:1049`; `api/data.py:63`)
— guarding the OUTPUT file. This spec repeated the map's framing in OR2, T7.6, T12/M9 and
D3/Q16; every one is corrected, the escalation is withdrawn, and the fallback plumbing it
implied is deleted rather than re-pointed.

**MC-Y · The map's §4 Phase 05 output numbering is reversed from the owner's, and the
owner's governs.** `docs/aegis-restructure.md:416-421` numbers them 01 Post Concept, 02
Post Master, 03 Pre Concept, 04 Pre Master; the owner's numbering is 01 Pre Concept, 02 Pre
Master, 03 Post Concept, 04 Post Master. No artifact, builder, filename or route changes.
Recorded as **D9/Q22** with the exact table replacement. Anyone reading the map, this spec's
earlier drafts, or step 7's PR text must map "Outputs 03-04" (Pre) onto **Outputs 01-02**.

**MC-W · Minor coordinate drift, no substantive effect.** `assessment_release_snapshot`'s
`concept_key` is `release:<sha20>:<pos:04d>` (four-digit position, `:133`), not the bare
`<pos>` the map writes; `assessment_cells._allowed_sheet_kinds` is at **`:99-103`** — an
earlier draft of this line had the correction the wrong way round and said `:97-101`;
[measured] `def _allowed_sheet_kinds` is line 99 and its `return tuple(rel.SHEET_KINDS)` is
line 103, and T12/M5 now cites the measured range;
`QUESTION_CATEGORIES` is exposed at `app/api/directory.py:46`, not `:44` (the
"nothing gates on it" claim is confirmed); `assessment_release.py`'s "MES never uses
Subjective" is at `:167-169`; `build_concepts_release_publication`'s
`_find_concept_in_chapter` call is at `:137`; `models.AssessmentRelease` spans `:394-448`
with the `UniqueConstraint` at `:447`. §5's transient-Concept constructor range is right.

---

## 9. REPAIR LOG — one section, one short delta per round

**How to read this.** Each round's findings were re-verified on this tree before being acted
on, and each one's *decision* now lives in §2, §3 or §6 — that is where an implementer reads
it. This log exists so a reader of an earlier draft knows what moved and why, and it is
deliberately a delta, not a second copy of the decisions. Where a later round superseded an
earlier one, the earlier entry says so and points forward.

### Round 2 — a completeness critic returned NOT BUILDABLE AS WRITTEN (10 findings, all reproduced)

* **C1 · The question-label minter was in no slice.** `generation._topic_index:77-104` and
  `question_label:107-114` survived untouched, so D1/Q14's `Question = <ConceptID> Q##` was
  half-built. → **T4-7**, S4's Changes, S5's "must land after S4",
  `test_question_label_is_minted_by_exactly_one_function`.
* **C2 · Machine-id backfill and the import round trip were unspecified.** → **T4-8**: the
  backfill is `db._backfill_and_normalize:113-164` in S4; an empty column is a mint, never a
  defect (T9-1 B1 rewritten); the reader RESTORES the stripped tag rather than erasing it.
* **C3 · Two named deliverables had no call site.** → **T10-0** (`release_qc.audit`'s call
  sites, transports and four surfaces) and **T7.6** (`migrate_workbook_layout`'s module,
  signature, caller, staged sibling and `RLock`).
* **C4 · The spec created an R4 silent-loss path.** Diagnosis right, remedy wrong —
  ***superseded by V1 below.*** Read T7.5 and D8.5b as they now stand.
* **C5 · Two ordering/coordinate errors that silently no-op.** S3 named only the two Post
  manifest blocks (now all four, by function and line); the three `canonical-*` registry
  entries became FROZEN LITERALS, with S2 forbidding writer-built fixtures and S7 forbidding
  edits to them.
* **C6 · `return row[:expected]`.** → **T3.8**: pad and truncation both become one recorded
  Fixer decision, in S7, never a raise.
* **C7 · The Pre `related_concepts` value never survived publication.** → **T3.3b**:
  publication lifts the resolved marker into an explicit key BEFORE
  `_strip_release_fields`, and both create and merge paths persist `related_concepts` and
  `digicards`.
* **C8 · The reader merges a Pre and a Post topic that share a title.** → **T6.4**, S2.
* **C9 · The final artifact set is now named.** → **T14** and **D8/Q21**; `concepts_xlsx` is
  aliased, not retired; the composition and roster are SHARED, not moved.
* **C10 · The "or none does" escape is removed.** → T12/M4 enumerates all three sites.
* **Coordinate drift fixed in the same pass:** `build_concepts_release.py:436-438`;
  `_HUB_PREFIX_RE` def `:376`; `len(culmination_title) > 120` at `:532`;
  `_publication.py:155-163` / `:164-170` / `:119-172`; `_append_group_row:384-393`.

### Round 3 — six owner rulings and five verifier findings

* **OD1** the run builds all four outputs → **T15**; OR1 and S6's fallback deleted.
* **OD2** the migration subject is `config.BULK_IMPORT_OUTPUT`, not the CI-regenerated
  fixture → **T7.6a**, **MC-X**; the whole fallback plumbing deleted, not re-pointed (V5).
* **OD3** `keywords`/`related_concepts` ship as content → **T3.3** (see §1 for the precise
  form; round 4 corrected §1's one-line summary, which overclaimed it).
* **OD4** the numbering is Pre-first → **D9/Q22**, with the exact doc-table replacement, §5's
  rename and string tables, and the pin that no manifest `kind` may carry a digit. The sweep
  found 46 matching lines, of which **38 were numbering-invariant pairs** and 8 were singles;
  all 8 were changed. Round 4 corrected two rows of the string table.
* **OD5** a questionless concept is one tail row → **T16**, and with it the SECOND read-back
  at `assessment_master_refiner.py:673-682`, found while writing the consequence note.
* **OD6** the Concept File's shape → **T17**. Round 4 re-pointed it at the right builders.
* **V1 · BLOCKING — the C4 repair opened a wider R4 hole than it closed.** `unplaced` has one
  reader (`assessment_release_service.py:326`) and `structural_defects` cannot see it, so
  deleting the read in S6 would have left an unresolved home refused by nothing. → T7.5
  rewritten around one rule; the verdict computed at STAGING by
  `unresolved_question_homes`; `:326-330` deleted in **S8**, not S6, in the same commit as
  its replacement.
* **V2 · BLOCKING — the Post lane had no transport for the audit's blocking set.**
  `stage_release`'s payload dict has no `snapshot_defects` key. → S11 adds it in `:2053`'s
  shape with the matching parameter.
* **V3 · BLOCKING — S2 forward-depended on a module S4 creates.** → T4-9(a): S2 creates
  `identity.py` with `topic_identity` alone; S4 extends it.
* **V4 · A real module cycle between `identity.py` and `writer.py`.** → T4-9(b): the ordering
  key MOVES into `identity.py`; all eleven sites move; a subprocess-import pin.
* **V5 · The previous round wired OR2's fallback to the wrong constant.** Settled by OD2 as a
  deletion at every site.

### Round 4 — four blocking items and five minor ones, all re-verified here

**HEAD moved under this round and it does not invalidate anything measured here.** [verified]
the branch is now at **`6c6ebc9`** ("List the six Post-lane prompts in the frozen core (§10
step 8, S1)") — **S1 has LANDED** — and `git show --stat 6c6ebc9` touches exactly two files,
`backend/app/services/instruction_architect.py` (+20/-6) and
`backend/tests/test_instruction_architect.py` (+45). No file this round measured is among
them, so every `file:line` below and above was taken on a tree those two edits do not move.
Update T13/S1's status when reading: the frozen-core listing is done, not pending.

* **B1 · OD6 was answered against the wrong function.** The draft verified
  `assessment_workbook.render_concept_file:180-192` and `assessment_workbook._new_workbook:
  116-121`, but this spec's own T14 and D8/Q21 bind Outputs 01/03 to
  `build_concepts_release_files.build_release_bulk_import_workbook:44-107` →
  `writer.write_concepts_workbook:1213-1257` (returned at `:79`) or
  `writer._new_workbook():85` + `writer._concept_to_row:811-832`, and call
  `render_concept_file`'s product the sealed projection. **[measured, executed]** the real
  Outputs 01/03 carry FOUR sheets — `['Objective ', 'Subjective', 'Descriptive', 'Doc Link
  <> Each fields ']` — against the format workbook's three, so "the other two sheets exist
  and are empty — keep it" was **false of them**. → **T17 rewritten** with both builders
  tabled and a rule for each; the sheet half bound to **T12/M1 in S7** (`_new_workbook:
  1140-1154`, Doc Link at `:1146`); the pin **split**:
  `test_the_concept_files_are_filled_to_the_concept_band_and_no_further` → **S7** (fails
  today on the sheet set and order), `test_the_sealed_concept_projection_keeps_the_same_
  shape` → **S8** (fails today on `topic_concept_labels`, and compares the two sheet sets so
  the sealed sha256 keeps meaning something). D8/Q21 gained the consequence paragraph; §1's
  OD6 line now says "bound to the builders that actually make them".
* **B2 · OD1 had no call site, and as specified it cost the concept outputs.** → **T15-1**
  names it: `build_concepts_release_contract._run_generation_release:268-370`, whose body
  moves verbatim into `_stage_generation_release` (the four `stage_release`/
  `_stage_pre_sibling` pairs at `:209`/`:232`, `:243`/`:254`, `:301`/`:323`, `:336`/`:347`
  are untouched), with ONE tail call to a new `_build_master_siblings` — chosen over four
  call sites because all four exits converge there, it sits outside the `_RELEASE_MODE`
  `finally` at `:365-367`, and a fifth exit inherits it. → **T15-2** specifies containment:
  `try/except Exception` per lane (broad on purpose — an enumerated tuple goes stale and a
  new exception type must never cost Outputs 01/03), each failure a named
  `assessment_lane_unavailable` **issue** on that lane's concept release — not a structural
  defect, which would refuse the concept lane's DB write for a fault in another lane and
  collapse Rule G's two publications into one. The concept `release_state` is unchanged; the
  Master manifest entries go present-and-`disabled` with the recorded reason; recovery is the
  existing re-build route. Regression
  `test_an_assessment_lane_failure_does_not_cost_the_concept_outputs`, parametrised over the
  four exits, in **S6**.
* **B3 · The subjective/profile knot — ONE decision, not four patches.**
  `sheet_kinds` **supersedes** `allow_subjective_rows`, which is DELETED
  (`assessment_profile.py:25`; [measured] its only live read is
  `assessment_workbook.py:751-753`, plus a comment at `assessment_cells.py:101`), read
  through the single accessor `assessment_profile.sheet_kinds(profile)` — **not** a merging
  `resolve()`, because [verified] `decide_cells:226-245` never calls `resolve` and adding one
  would make `_profile_payload:107-111`'s live `appears_in` gate unreachable, and because
  `tests/golden/rne_assessment_candidates.json:16-20`'s recorded profile must keep resolving
  unchanged (§4 forbids moving that golden). **Consequently `sheet_kind_not_emittable` does
  not exist**: a kind the profile allows always has a data sheet (the layout's Subjective
  header is 144 fields), and a kind it disallows is already refused by
  `validate_candidate:191-195` → `freeze_payload:502-503` → `payload_errors` → BLOCKED. It is
  replaced by `sheet_kind_not_renderable` against a new code constant
  `assessment_workbook.RENDERABLE_SHEET_KINDS`, because the real gap is
  `_question_record:199-283`'s two branches (`:237` Objective, `:248` Descriptive) — a
  Subjective candidate falls into the Descriptive branch and **raises** at `:216-219`.
  `sheet_for_kind:407` derives from that constant × the layout, never from the profile.
  Reconciled in T7.5 item 1, T9-1 B1, T12/M2, M5b, M6, M6b, M8, D4/Q17 and S8; S8's
  regression renamed to `test_a_subjective_candidate_a_widened_profile_allows_is_a_named_
  defect_not_a_render_error` with a `reference-1` negative control.
* **B4 · `render_master_file` had SIX other raises, not five.** [measured] by AST walk:
  `:315`, `:317-318`, `:324-326`, `:331-333`, `:336-337`, `:343-345`, plus `:373` — SEVEN in
  total, and the verifier that reported "five more" undercounted by one. All are de-raised.
  Three already have a staging twin and simply go (`validate_group:386-391` covers `:315` and
  `:336-337`; `duplicate_group_keys:419-435` wired at `freeze_payload:510-511` covers
  `:317-318`); three become new codes `group_concept_home_unknown`, `group_home_unnamed`,
  `group_visible_name_mismatch`. `_question_record`'s seven are named too: `:207`, `:213`,
  `:217` already have twins in `validate_candidate` (`:236-238`, `:263-266`, `:293`) and the
  four count caps at `:239`, `:252`, `:265`, `:275` become `render_shape_overflow`. After
  this the Master renderer raises nothing, which is what lets S9's Rule-E pin be total:
  `test_the_master_renderer_contains_no_raise` (AST) plus
  `test_every_group_edge_raise_became_a_named_defect`.
* **B5, five minor ones.** (i) T7.5's duplicated "argument that lost" paragraph — the second
  copy deleted. (ii) §5's OD4 string table inverted the **Pre** arm of
  `assessment_release_run.py:1379-1389` — one raise, two ternary arms renumbering in opposite
  directions, now two rows — and flattened `:1459`, which [verified] is **not** guarded by
  `:1452`'s `if not generate_lane` and fires on both lanes, so it becomes lane-generic
  instead of being renumbered. `:1454` **is** so guarded and is correctly Output-03.
  (iii) "the renderer keeps producing `issues['unplaced']`" vs S8's "no consumer" pin —
  reconciled: the key is still written and still lands on disk in `manifest.json`
  (`assessment_release_service.py:35`, `:387-389`), what goes is the DECIDER, and the pin is
  renamed `test_no_code_path_decides_on_unplaced_and_the_refusal_survives`. Also recorded
  honestly: [verified] `_release_summary:305-318` never exposed `unplaced` to a human anyway,
  so the reviewer-facing channel is the NAMED defect, not this key. (iv) §1's OD3 summary
  overclaimed — corrected to match T3.3 (`related_concepts` filled on **Pre** rows, blank on
  Post rows until a relations pass exists). (v) The two repair logs are pruned into this one
  section, one short delta per round; no decision and no slice was pruned.

**Nothing in this round introduces a numeric threshold or keyword vocabulary deciding
meaning, a volume-derived count, a mid-run halt where Q13 requires a recorded flagged
decision, or a critic that gates.** Every new consequence is one of: a **gate on an artifact**
that compares literal tuples, counts identities or tests a string for emptiness and judges no
content (`sheet_kind_not_renderable`, the three group-edge codes, `render_shape_overflow`); a
**recorded issue that blocks nothing** (`assessment_lane_unavailable`, which is deliberately
NOT a structural defect precisely so it cannot cost the concept lane its database write); or a
**refusal of a database write** through the transport T5-1 already established. B2 is the one
that removes a halt rather than adding a gate, and it is the item Q13 most directly demands.

**What was checked and left alone.** The one verifier claim that did not survive measurement
is B4's count: it said five, the AST says six (seven including the one already de-raised), and
the spec now carries the measured number with the command that produces it. Two owner-facing
facts were re-confirmed rather than assumed: the committed format workbook's sheet names and
order (`['Objective','Descriptive','Subjective']`, executed) and the four-sheet output of
`writer._new_workbook` (executed). No finding was rejected outright.

### Round 5 — owner ruling OD7 closes RES-1 (recorded here as the register entry the ruling names)

**OD7 · Decided — the Post lane gets NO empty-publication verdict; this entry IS the record.**
Owner ruling, 19 Aug 2026, closing S9's RES-1. The question: D8.3 gives the PRE lane a
positive model verdict on an empty capture ("does this chapter genuinely have no
prerequisites, or did the capture fail?"), and the Post lane had no analogue — should a
zero-row POST publication carry one, or a register entry saying none is wanted? The ruling:
**a register entry; no Post-lane verdict is built.** Current behaviour is already exactly
compliant: D8.1 forbids treating emptiness as corruption, D8.2 is written unqualified, and
since S9 the Post lane's empty publication completes while its receipt reports the emptiness
"as measured rather than as decided"
(`build_concepts_release_publication.py`, the no-row branch) — asserting no verdict the run
did not make. S10 therefore changes NOTHING on this path; its regression
`test_an_empty_pre_release_still_exports_and_still_publishes_zero_rows` pins the Pre lane,
and the Post lane's honest no-verdict receipt stays pinned by S9's tests. The asymmetry with
D8.3 is deliberate and owner-decided, not an omission.

### Round 6 — S10 pre-implementation delta: measured drift, and the decisions the slice executes under

Three parallel re-reads of the S10 surfaces at d7d2e2f (the publication lane, the master
lane, the test inventory) before any S10 code moved. Everything below is measured against
that commit; S10's own line refs (`:125`, `:100-106`, `:137-172`) are stale and the work
proceeds by symbol.

**Spec-vs-code drift, recorded so S10 does not re-do landed work.** (i) The T3.3b lift
already runs — `_publication.py` builds its records through
`_strip_release_fields(_lift_resolved_related_concepts(row))` — and the merge branch
already persists `related_concepts`/`digicards`; both halves are pinned by
`test_step8_one_renderer.py` (including a source-text grep pin on the exact assignment
strings, which any rewrite of the merge branch must keep verbatim or amend in the same
commit). (ii) `_add_concept` already persists both columns; S10's "today it persists
neither" is stale. (iii) MC-G's `_find_or_create_topic` title-rewrite claim is stale —
the stored `topic_title` is never rewritten (measured, and pinned by
`test_topic_display_name_is_clean_when_topic_is_created_or_reused`). What remains of S10
is therefore exactly: the OD4 string, the cleaner removal (atomic with `clean=`), the
machine-id resolutions (both lanes), flags-not-deletes, the named row drop, and the
tamper gates. None of the nine S10-named regressions exists yet.

**Red-today reproductions (measured in-memory, real `upload_release_to_database`).**
(i) Staging `concept_title="pH and its meaning"` with details `"See Fig. 2.1 for the pH
scale."` persists `'pH and Its Meaning'` / `'for the pH scale.'` — the reviewer's title
recased and the figure reference destroyed, by `clean_concept_record` at the publication
boundary. (ii) Cross-publication same-title destruction is real: publishing `"Shared
Name"` under a second topic re-parents the first concept, deletes its tags and overwrites
its details (`updated: [1]`); the single-publication form accidentally passes because the
stale in-session `topic.concepts` collection hides row 1 from `_find_concept_in_chapter` —
so the S10 regression uses the cross-publication form. (iii) The master lane still
refuses a recased staged topic title (`UploadRefused` at the five-field byte match) even
though `_find_or_create_topic` now matches leniently.

**Decisions, with the losing argument recorded.**

* **S10-a (OD4 string).** `_resolve_snapshot_concept_ids` derives the output name from
  each topic row's own `pre_post_learning` — Pre → "Output-01", Post → "Output-03" —
  instead of the hard-coded "Output-01"; `test_staged_master_waits_for_exact_output01_
  publication` is renamed and re-matched to "Output-03 identity" in the same commit
  (spec §4's amendment). The backwards lane comment in `upload_release_to_database`'s
  docstring ("Output 01 (post) or Output 03 (pre)") is corrected to OD4's mapping in the
  same pass. Losing argument: a lane-less legacy release could keep the old string — but
  the topic rows carry the lane either way, and one string with a recorded derivation
  beats two strings with a guard.
* **S10-b (cleaner removal, atomic).** `_add_concept` gains `clean: bool = True`;
  the publication deletes its own `clean_concept_record` call and passes `clean=False`.
  Default True keeps the deposit path (`_deposit_concepts`) and
  `test_add_concept_cleans_name_and_description` byte-identical. The cleaner is
  idempotent (MC-G), so either half alone changes nothing on the create path — the two
  land in one commit or not at all.
* **S10-c (publication resolution by slot identity).** The staged records carry NO
  machine-id field; the transient export stamps composed positional ids
  (`compose_topic_machine_id(chapter_key, lane, topic_order)` →
  `compose_concept_machine_id(topic_mid, concept_order)`) that are, by T4-4's design,
  "the same string the publication will persist" — and
  `test_source_order_agrees_between_export_and_publication` pins staged ids == published
  ids. So the publication resolves each row to the persisted concept holding the id the
  staged workbook SHOWED for that slot: `machine_id == compose(persisted topic
  machine_id, this release's source_order)`, queried against the DB (never the loaded
  relationship — the stale-collection accident above), scoped to the resolved topic.
  Exactly one → update in place (no re-parent, no `db.delete(tag)` — resolution inside
  one topic makes re-parenting structurally impossible, so the tag-repair loop it
  existed for goes with it). Zero → create through `_add_concept` and mint through the
  one minter. More than one → refuse naming the duplicate identity (a gate on corrupted
  identity, not a judgment). The composed string is used as a LOOKUP key only; minting
  stays with `machine_id_for_concept`/`free_slot` (T4-4's inline-composition warning is
  about minting, and still binds). Consequences accepted and recorded: a legacy row with
  a blank `machine_id` is never merged into — title-shape matching is exactly the
  deterministic judgment Rule 1 forbids, so an unprovable identity CREATES rather than
  guesses, and the legacy row stays untouched beside it (nothing lost, R4); a release
  that reorders same-titled rows re-files content by slot, which is what the reviewer's
  approved workbook showed. Losing argument: keep title-match as a fallback for blank-id
  rows — rejected because it re-admits T11.3's destruction class through the fallback.
* **S10-d (flags, not deletes).** `filter_review_violations` keeps every
  `_FORBIDDEN_TOPIC_NAMES` row and records the suspicion ON the row through
  `_add_review_flag` (deterministic text, so the assemble/deposit fixpoint replay
  converges — the same mechanism the pedagogy branch already pins). The vocabulary
  itself survives as a flag-raiser only (advisory, Q10-shaped, gates nothing); its purge
  is tracked separately, exactly like `_PEDAGOGY_TOPIC_RE`. The omission log dies with
  the omission. `phase3/assemble.py` and `release_refiner.py` call sites are untouched —
  the behavior change flows through `concept_cleanup.py` alone and the `phase3/` diff
  stays empty. The two tests pinning deletion invert in the same commit.
* **S10-e (the named row drop).** The publication's record filter stops eating rows: each
  incoming row is checked with the EXISTING `row_projection_defect` vocabulary
  (`staged_row_unusable`), and a droppable row raises, naming the row — same transport as
  the `structural_defects` gate above it. This fires only in the measured gap where a
  stale recorded `staged_row_defects` key disagrees with the records
  (`structural_defects`' recorded-key-wins branch); the regression constructs exactly
  that payload. No new defect code is minted.
* **S10-f (master-lane machine-id resolution).** `_resolve_snapshot_concept_ids`
  resolves a snapshot concept row that carries `concept_machine_id` by
  `models.Concept.machine_id` alone, scoped to the chapter and the topic row's lane —
  killing the five-field byte-match refusal class (the recased-topic reproduction). The
  five-field exact match survives ONLY as the recorded legacy fallback for snapshot rows
  frozen before the column existed (no `concept_machine_id` key). Deliberate behavior
  change, recorded: the content-drift half of the old match is gone — editing a
  published concept's details after the concept release uploaded no longer refuses the
  Master upload; drift protection is the seal's job (S10-g), not a byte-compare against
  mutable rows. A legacy PUBLISHED row (blank `machine_id`) against an id-carrying
  snapshot refuses with "upload that concept release first" — republishing the concept
  release mints the ids and unblocks, which is the migration path.
* **S10-g (tamper gates).** Master lane: the disk re-hash already refuses both tampered
  artifacts; the S10 regression exercises all four projections. Concept lane (the gap —
  no verification of any kind today): `upload_release_to_database` recomputes
  `assessment_release_snapshot.source_release_sha256(payload)` and compares it against
  the frozen same-job, same-lane `AssessmentRelease`'s recorded
  `concept_snapshot["source_concept_release_sha256"]` — but ONLY when that row's
  `provider_identity["staged_release_version"]` equals the payload's current
  `staged_version`: the same draft version must hash to what was frozen, and a mismatch
  is an in-place edit nobody recorded. A re-stage bumps the version and is therefore
  never a false positive; a frozen row predating the version record (or no frozen row at
  all) leaves nothing to compare and the publication proceeds — the gate refuses only on
  measured drift, judges no content, and blocks nothing it cannot prove. The seal
  excludes `issues` and `summary` by construction, so the publication's own summary
  writes and the `assessment_lane_unavailable` recorder never move it.

**Test consequences, decided up front.** Inverted: `test_overview_topic_is_dropped_not_
reassigned`, `test_omitted_umbrella_topic_rows_are_named_never_bare_counted` (both now
pin survival-with-flag; the never-reassigned half survives the inversion). Amended:
`test_staged_master_waits_for_exact_output01_publication` (rename to `..._output03_...`,
match "Output-03 identity", and the satisfying fixture row gains the `machine_id` the
resolver now reads). Watched, amended only if red or made vacuous:
`test_a_published_id_is_not_re_keyed_by_a_second_publication` and
`test_a_second_publication_that_prepends_a_topic_mints_no_duplicate` — under slot
resolution both scenarios still resolve to the same rows, so both should stay green
while still testing a merge. Kept verbatim: the grep pins in `test_step8_one_renderer.py`
(the merge-branch assignment strings survive the rewrite), the deposit twin's tests
(`_deposit_concepts` is NOT in S10), and `test_add_concept_cleans_name_and_description`
(default True). The stale "Publication title-cases the labels it writes" comment in
`test_pre_release_lane_wiring.py` is corrected where touched. Of the nine S10 names, two
are pins of behavior that already landed (`test_pre_related_concepts_survives_publication`,
`test_digicards_survive_publication` — S8 follow-up persisted both columns) plus the
OD7-pinned Pre-lane empty publication; each pin is proven live by neutralising the code
it guards before being trusted, per the standing red-first rule; the other six are
red-first regressions.

### Round 7 — the S10 audits, the withdrawn slot design, and the repair

Three adversarial audits (doctrine, mechanical correctness, coverage/blast-radius) ran
against Round 6's implementation before it was ever committed. Their blocking findings
were each reproduced end-to-end and are recorded here as the reason Round 6's slice-c/f
design is WITHDRAWN — none of it reached the branch history; this round is the record of
why the committed shape differs from the Round-6 plan.

**What the audits measured against the slot design.** (i) Resolution by composed
position (`compose(topic_mid, source_order)`) overwrote a removed row's neighbour on an
ordinary shrinking republication — Concept B's learner-visible details replaced by
Concept C's, no flag, no log, a stale duplicate left beside it (R4, and worse than
d7d2e2f, whose title merge handled the same flow losslessly). (ii) The transient
export's purely positional stamps diverged from the publication's minting the moment
the chapter held prior state (a prepended topic, a seeded chapter, a blank-id legacy
sibling), so the Master's machine-id resolution silently attached a question authored
for Concept X to the unrelated Concept A — the old five-field byte match at least
refused; and beside a blank-id legacy row the create path minted a fresh duplicate on
EVERY republication, unboundedly, with the reviewer-seen id never persisted. (iii) The
seal gate's `staged_version` key collides after a legitimate inventory reset
(`replace_file` / `convert_job` / the source-review checkpoint restart the counter at
1), so it accused a clean fresh run of tampering; and the tamper it exists for could
strip the version field and walk through. (iv) The row-drop gate ran after the
zero-row branch, so a payload whose EVERY row was tampered unusable published as an
idempotent empty success saying "nothing was removed". All four reproduced, all four
repaired, and the audits' own reproduction scripts re-run green against the repair.

**The repaired resolution rule (per record, in order).** (1) A record CARRYING
`machine_id` resolves by the persisted column, chapter+lane wide — recorded identity
wins (P-C1); found under a different topic → fields update in place, NO re-parent, and
the disagreement rides the receipt as a flag; the id held by nothing → the identity is
restored verbatim with the content, exactly as the workbook reader restores one (B4).
No record carries an id today; the workbook round-trip is the intended carrier.
(2) Otherwise, EXACTLY ONE unclaimed persisted concept under THIS resolved topic whose
title matches through the one normaliser (`bi.normalize_question_text`) is the same
row re-staged: updated in place, id kept, no re-parent, no `db.delete(tag)`, no
cleaner. (3) Zero candidates, or two-plus, CREATE — an identity that cannot be proven
unique is never guessed, and a `claimed` set guarantees no persisted row is merged
into twice in one publication. This is a RECORDED AMENDMENT of S10's literal "no
merge": the audits measured that id-only resolution with no carrier destroys (slot
keying) or pollutes (create-always duplicates every republication and the platform
reads the DB rows), while the topic-scoped exactly-one title match restores d7d2e2f's
lossless republication cases with T11.3's three destructive ingredients (chapter-wide
scope, re-parent + tag deletion, the cleaner rewrite) all removed. The chapter-wide
`_find_concept_in_chapter` stays out of the publication permanently.

**Identity agreement, both sides of the publication.** `transient_release_hierarchy`
now consults the persisted chapter for IDENTITY ONLY — never content — and stamps by
the SAME rule the publication resolves by: persisted topic ids reused verbatim on a
lenient title match, persisted concept ids reused on the exactly-one title match, a
blank-id legacy row stamped with the mint its adoption will produce, and fresh rows
slotted by `free_slot` from this release's position against the persisted lane
siblings. The minters gain an explicit `position=` mode (still ONE producer, still
`free_slot`, still persisted) because their sibling-sort guess counts blank-id rows
and ties, which is exactly the drift the audit measured. The docstring's "no
persisted row is read" is amended to what it always protected: content authority
stays with the staged records. Consequence: reviewer-seen workbook ids, snapshot ids
and persisted ids are one string in every deterministic case, pinned end-to-end by
`test_a_prepended_topic_still_homes_master_questions` and
`test_a_legacy_blank_id_row_is_adopted_not_duplicated`. The Master resolver keeps a
defence-in-depth topic read: the machine-id match must sit under the snapshot row's
topic through the one lenient normaliser, so an id that drifts anyway refuses loudly
instead of mis-homing (a recase still resolves;
`test_master_resolution_survives_a_published_content_edit` pins the deliberately
dropped content half, `test_a_blank_id_published_row_refuses_an_id_carrying_snapshot`
pins the migration refusal).

**The seal gate, re-keyed to lineage.** Every staging act mints
`staged_release_uid`; `run_context` records it on the frozen row; the gate compares
seals only within one recorded lineage, so a version-counter restart can never accuse
a clean draft. A frozen row that RECORDS a lineage while the payload carries none is
the field stripped in place and refuses. Recorded limits, in the gate's own
docstring: pre-uid payloads and rows leave nothing to compare (dormant, like the
Master lane before recorded hashes), and an edit that swaps in a fresh uuid is
indistinguishable from a legitimate re-stage by the payload alone — the Master's
artifacts stay hash-sealed regardless. The row-defect gate moved above the zero-row
branch (`test_an_entirely_unusable_tampered_payload_is_refused_not_published_as_empty`).

**The OD4 string table, completed.** The S6-assigned `assessment_release_run` strings
were never delivered and read BACKWARDS against OD4; observable contradiction once
S10's resolver spoke the new numbering. Delivered now: Pre names Output 01/02, Post
names Output 03/04, in both missing-release raises and the Post-only inventory raise;
the both-lanes chapter-identity raise names NO output (T14); the Pre-Master API
docstring, the snapshot module header and the two stale test comments corrected with
them. Accepted cosmetic residue: a lane-less legacy snapshot row still reads
"Output-03" in the refusal message (its resolution goes through `db:` keys in
practice).

**Smaller deliveries out of the audit list.** T7.2's flag clause is implemented at the
publication: where the cleaner WOULD have rewritten a row, the divergence is recorded
into the receipt's `identity_review_flags` (idempotent staging rows diverge on
nothing, so the note appears only where a human's edit won). The umbrella flag carries
its T7.3 code (`review_topic_name`) in the flag text. `filter_review_violations`' two
retained vocabulary roles are BOTH named now — the reassignment regex and the
umbrella set's part in choosing the reassignment's destination — with the measured
all-umbrella fallback corner recorded rather than smoothed over. The legacy Master
read-back's topic twin DECLINES to judge a composed key two snapshot rows share with
two different topics (a legacy no-id snapshot with duplicate titles), instead of
last-wins collapsing into a permanent false refusal — S10's publication makes
same-titled concepts a supported state, and the interaction was the audit's find. The
S10 test module builds its OWN chapter per DB-writing test (the shared fixture
chapter [measured] turned five lifecycle tests red under targeted orderings).

**The red-first ledger for this round.** Red at d7d2e2f and green now: the six Round-6
regressions, the all-defective row-gate case, and the two payload-tamper legs plus
strip-lineage leg of the four-outputs test. Pins of the repaired design whose red
state was the WITHDRAWN intermediate rather than any commit: shrink, reorder,
legacy-adoption, prepend-homing, content-drift, blank-id refusal — their red proof is
the audit reproduction scripts, re-run before and after the repair in this session.
Residues accepted and named: umbrella rows now enter the deposit validation contract
that never saw them (severity measured non-fatal; untested end-to-end), and two
same-titled rows staged under ONE topic re-staged create beside their originals
(ambiguity is never guessed) — both owed to S11's QC pass if they bite.

### Round 8 — S11 pre-implementation delta: the map, the drift, and the slice decisions

Three parallel re-reads at 79625c0 (the S11 spec text pinned to code; the generation
polarity landscape; the writer read-backs and the release threshold) before any S11 code
moves. Verdict on the S11 section: **substantively CURRENT throughout** — every named
deliverable is still absent, every deletion target still present, every claimed gap still
open — with ONE item already landed (`question_source` reads from the profile via S8's
T12 threading, at `assessment_workbook._question_record`, with a recorded divergence: the
profile default is `""` where T10-7 item 5 ruled "UpSchool DB") and every line coordinate
stale (work proceeds by symbol; the re-resolved coordinates live in the map transcripts).
Facts re-measured and confirmed: the exactly-two-hit `"snapshot_defects"` grep (the read
in `structural_defects`, the write in `stage_pre_release` — S9's `staged_row_defects` is
a DIFFERENT key and does not satisfy V2); `len(_FATAL_CODES) == 52`; the `strict=True`
raise in `_repair_records_via_api` has no production caller; `require_parent=True` has no
caller anywhere, so `required_parent` cannot currently fire; no test anywhere pins any of
the five writer deletion targets FIRING, and nothing catches or pins
`ConceptWorkbookValidationError`; umbrella-topic rows (Round 7's residue) fire NO
fatal-family code structurally — measured, that residue is closed.

**Slice decisions, with the losing argument where one existed.**

* **S11-a (the polarity inversion).** `_BLOCKING_CODES = frozenset({"required",
  "required_parent"})` per T10-2, a literal pinned by its own test. Both gates
  (`_validate_final_or_raise`, the deposit gate in `build_concepts`) filter their raise
  set on it; every other `_FATAL_CODES` finding becomes a review flag carried on the row
  through the 6335fe6 `_carry_review_flags` machinery — no Fixer round for advisory
  codes (the Fixer at these gates now only ever sees the two mechanics codes, for which
  acceptance is refused and correction remains the final-gate option). `_FATAL_CODES`
  keeps its name and its classify role; the repair selector (severity) is untouched, so
  every judgment code still earns its bounded model correction. Gates 3b/3c (invalid
  inventory identity, exact inventory coverage), the topology gate and the grounding
  certificate are NOT `_FATAL_CODES` gates and keep raising — they are R4/identity
  accounting. `_FIXER_UNACCEPTABLE_CODES` shrinks to the same two codes (T10-3). The
  dead `strict=True` branch stays (its one caller is a script), recorded here.
* **S11-b (the writer).** The five blocks delete as T10-4 orders. The surviving
  identity read-back stops raising at BOTH call sites — the spec names only
  `append_concepts`, but `write_concepts_workbook` calls the same helper; a de-raise
  that left the export path raising would keep the exact class T10-4 retires, so the
  helper returns its findings and each caller records them on its own transport (the
  deposit path as record-only Fixer decisions riding `fixer_decisions`/`issues`, the
  bytes-only export path through the full-record logging transport that exists for
  precisely that shape). With no raise left and no catcher or test pinning it,
  `ConceptWorkbookValidationError` is deleted; `semantic_recovery`'s
  `_PERSISTENCE_PATTERNS` entry for its message becomes dead and is left in place,
  recorded here (removing it is a classifier change S11 does not need).
* **S11-c (the audit's own instruments).** T10-5 verbatim: threshold deleted, casefold
  before the noise substitution, Unicode-aware noise class; one grouped collision issue
  per shared wording; `_QUESTION_ITEM_MARKER_RE` survives as the checklist's named
  residue. T10-6: `_learner_analysis_count` purged; the topology tie-break becomes the
  recorded `_aegis_analysis_allotments` marker count, else the artifact's own recorded
  completeness. T9-3: the swallowed `_type_catalog` exception becomes a named defect.
* **S11-d (T9's concept-lane identity set feeds `structural_defects`).**
  `duplicate_qid_assignment`, `unknown_type_case_qid`, `duplicate_case_identity`,
  `duplicate_qid_route`, a duplicate persisted `machine_id`, and a staged row still
  blank after the mint — all become structural defects (block the DB write, never a
  download; `release_state` reads DIAGNOSTIC_RELEASE). T9-2's stays-flags list
  (`unassigned_inventory_qid`, `qid_render_count_mismatch`, `example_less_case_shell`)
  is pinned by the named negative-control regressions. The assessment-lane B1 codes
  keep their D8.5b transport and are NOT routed through `structural_defects`.
* **S11-e (release_qc + the transport).** NEW `app/services/release_qc.py` with T10-0's
  exact signature, called from both staging functions immediately before payload
  assembly; `issues` merges into the existing ledger, `blocking` rides
  `payload["snapshot_defects"]` — which `stage_release` GAINS (key + parameter, in
  `stage_pre_release`'s exact shape) with `_POST_PAYLOAD_KEYS` gaining the one key, as
  V2 orders. The audit never raises and never runs inside an artifact builder. It calls
  `coverage_ledger.build_coverage_ledger` and turns each `unaccounted` entry into a
  named issue (item 22 — R4's enforcement stops being inert). ADDITION beyond the
  spec's list, recorded as such: the audit also reads
  `_aegis_pre_related_concepts_unresolved` — [measured] a producer with NO consumer
  anywhere (its "RECORDED REVIEW FLAG" comment promises a record nothing makes) — and
  reports each unresolved needed-for link as an informational issue; this is the same
  producer-with-no-consumer closure S8/S9 made for `unplaced` and `staged_row_defects`.
  T7.5's seven assessment-lane codes are reported as issues where present, never owned.
  NEW `docs/release-qc-checklist.md` reconstructs the 23 SOP items from T10-7's rulings
  — where T10-7 carries no gloss for an item number, the entry records the ruling class
  and states plainly that the original wording is not in-repo — and enumerates the
  reconstruction's own three additions (the repeated-question collision audit, the
  type-catalog parseability defect, the unresolved needed-for reader) as the "three
  extras", flagged for owner confirmation since the spec never named them.
* **S11-f (`question_source` default).** The profile default moves `""` → "UpSchool DB"
  per T10-7 item 5 ("it names the ORIGIN SYSTEM, not a school"); the acceptance
  workbook comparison decides whether any fixture moves with it.

**Test consequences, decided up front.** Sixteen S11-named regressions plus
`tests/test_release_qc.py`, all currently absent. Re-authored:
`test_final_repair_options_include_every_terminal_strict_contract` (asserts the two
codes are NOT in `_BLOCKING_CODES` and still reach the repair pass) and
`test_a_short_shared_tail_is_not_a_repeated_question` → `..._is_reported_as_a_collision`.
Inverted: `test_final_validation_logs_every_fatal_with_exact_location` and the
mastery/Q1 final-gate raise pins in `test_generation_validation_diagnostics` (their
codes now ship flagged), and `test_concept_mapping_format`'s
`verbatim_source_description` deposit raise. Amended:
`test_mechanics_codes_are_never_acceptable_with_a_flag` (the duplicate-title pair
leaves the mechanics set) and the phase3_fixer accept-path fixtures (their blocked
codes no longer reach the gate Fixer; the accept machinery is re-pinned on a
`_BLOCKING_CODES` correction flow or retired to the seams that still use it). Kept:
`test_final_validation_rejects_missing_source_inventory` (gate 3c is identity), the
topology and grounding pins, and every writer does-not-fire pin (they keep passing
through the deletions). `_POST_PAYLOAD_KEYS` gains `"snapshot_defects"`.

### Round 9 — the S11 audits and the repair

Three adversarial audits (doctrine, correctness, blast-radius) ran against Round 8's
implementation before it reached the branch. Every blocking finding was reproduced,
repaired, and re-verified; this round is the record, including the places where Round 8's
own text was the defect.

**The blocking transport told a false story — repaired with its own key.** [measured, all
three lenses] routing the audit's blocking strings onto ``snapshot_defects`` (V2's literal
order) stamped every finding with the reader's "an input snapshot could not be read"
preamble — a fact no coverage finding measured — and on the Pre lane minted one spurious
``pre_learning_snapshot_unreadable`` error issue per finding. The review stream's notes
(§5.1/§5.4) warned about exactly this. Repair: the blocking set rides the NEW
``qc_blocking_defects`` key on both lanes, read by ``structural_defects`` under its own
honest sentence; ``snapshot_defects`` keeps its original input-artifact meaning, and the
Post lane's V2 key/parameter stay as the dormant parity transport they honestly are — now
pinned by an explicit-parameter regression. V2 is AMENDED by this, and the amendment is
this paragraph.

**T9-1's machine-id pair, delivered where the ids exist.** Round 8's S11-d claimed the
duplicate-persisted-id and blank-after-mint defects "become structural defects" — a
structural impossibility its own S10 established, since the payload never carries ids.
[measured] neither check existed anywhere, while two in-tree comments claimed the
publication act enforced them. Delivered: the publication act now refuses a row still
blank after the mint (create and merge branches both) and refuses when the chapter+lane
holds a duplicate persisted machine id — identity counting over persisted rows, at Rule
G's model-free act, downloads untouched. The identity/build_assessments comments that
cited the gate are true now rather than corrected. T9-4's mint-time (board, grade,
subject) scope remains undelivered and owner-visible as its own line item.

**The audit can no longer void itself.** [measured] one try/except around all three
passes let a later pass's crash discard a blocking finding the coverage pass had already
computed — T9-3's voidable-gate shape rebuilt one layer up — and labeled payload
corruption as a mere warning-of-record. Each pass now runs isolated; a failed pass
contributes its own BLOCKING ``release_qc_unavailable`` finding (a net that could not run
to completion certifies nothing), and an earlier pass's findings always survive.

**The identity gate fails closed and takes no severity side door.** [measured] a payload
with its ``issues`` key stripped passed the T9 closed-set read on absence — inconsistent
with the row gate's fail-closed recompute one block above — and a warning-shaped
``duplicate_qid_assignment`` slipped a set whose own comment brags that extending it is a
spec change. Both repaired: no ``issues`` key → the identity issues are recomputed from
the payload's own mined-Type material (an unreadable catalog during that recompute is
itself the blocking ``type_catalog_unreadable``), and the severity carve-out is gone —
T9-1 conditions blocking on the CODE. The gate's "[measured] warning-shaped" comment was
also wrong about history (the issue was error-severity; the gate simply never read
issues) and now says so.

**Records that were promised and not made.** ``_flag_advisory_validation_findings``
[measured] silently dropped a finding whose ``row_index`` did not resolve and logged only
a count; it now logs every advisory finding per-finding (code, concept, message) and
flags the row when one resolves — the log line is also the surviving per-finding record
at the direct DB-deposit boundary, where the row flags die with the deposit's own copies.
That boundary's deposit-only-finding gap (a defect first minted by deposit-only cleanup
has no durable artifact record; the affected path is non-release internal tooling — the
release path intercepts before this gate) is recorded in the checklist and owed to the
owner rather than claimed solved. The stale F38/F39 deposit-Fixer comment now states the
measured truth: the validation-gate Fixer round is structurally dead at deposit (blocking
⊆ mechanics, rows sealed); the inventory seams keep their decisions.

**Promised re-authorings, actually delivered this time.** ``test_final_repair_options_
include_every_terminal_strict_contract`` now asserts the two judgment codes are outside
``_BLOCKING_CODES`` while staying in the repair-driving fatal family (Round 8 claimed
this and had not done it); the ``unallotted_analysis_section`` membership pin and the
premap ``types_too_early`` rationale (module and test) no longer justify themselves with
the retired blocking behaviour — the premap refusal stands on the Pre lane's own
no-extraction contract. The gate-parity test ignores comments when reading source. Round
8's "T7.5's seven codes are reported as issues where present" was an overclaim — no
caller can supply artifacts today; the docstrings and checklist say "reserved", and that
is the record.

**Honesty items on the audit's own instruments.** The coverage net's blocking half is
[measured] normally SHIELDED in the wired staging flow (``unassigned_inventory_qid``
names every unplaced item; rendered examples count as placements) — it is a gap net for
stale, stripped and legacy payloads and for future producers, the same class as the row
gate's recorded-key case; the checklist now says so instead of "stops being inert", and
also records the staging ledger's false-positive direction (no ``hub_placements``
snapshot at staging). The flagged-and-issue-named duplicate warning is gone (one
vocabulary). ``question_source`` keeps its two aligned literals (profile + bulk-import
default), recorded. Dead code left by the deletions (``_identified_concept_fields``, an
unused import) is removed; ``semantic_recovery``'s dead pattern stays, recorded in Round
8. New pins land for: the failed-pass survival, the Pre-lane coverage skip, the explicit
``snapshot_defects`` parameter, the warning-severity block, the stripped-issues
fail-closed recompute, and both publication-act machine-id refusals.

**Expanded owner register.** The checklist's "Open with the owner" now carries the
review stream's uncovered items verbatim as questions — ``task_blocks_left_unruled`` vs
R4, the asset split, language-mode ownership, observability of error-severity
non-fatal-family codes, the open-polarity code list, the worked-method-anchor log-only
record — plus the deposit-only-finding gap and T9-4's mint-time scope. Nothing in this
round decides any of them.
