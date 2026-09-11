# Three-step generation workflow — review and proposal, 11 September 2026

**Owner instruction (11 September 2026).** Run the whole pipeline in three
explicit parts without changing any existing detail:

| Step | Owner's words | What the team does |
|---|---|---|
| 01 · Generate Concept Files | "extracts all the questions as it is under types, cases" | Nothing until the two Concept files exist. |
| 02 · Generate Master Files | "team reviews it, inculcates the changes … and then upload the same files for generation of master files for Post (with assessments). And for pre, generation of new assessments will happen instead of extraction." | Reviews and edits the Concept files (remove/add concepts, topics, questions), uploads them, presses Generate Master Files. |
| 03 · Publish | "the team then reviews, edits, make changes for master files and then upload the files to cms and also to the data base." | Reviews and edits the Master files, uploads them; Aegis publishes them to the CMS workbook and to the database. |

Additional rulings in the same instruction:

* Work that only serves later steps ("the polishing of question, or writing
  output files") moves out of Step 01 into Step 02.
* After the team uploads reviewed files, Step 01's source artifacts are
  "completely invalid" and are "nowhere connected to the second step".
* Nothing repetitive may run: no stage may spend time or money twice for the
  same purpose.
* Every existing detail is conserved. Only ordering changes, plus "a few extra
  deletions and additions", and the owner wants to be consulted on those.

CLAUDE.md Rule 0/Rule 1 and Q33 still bind: every pipeline-step removal is
proposed to the owner before it is removed, and no deterministic semantic
judgment is introduced. This document is that proposal, with the evidence for
each disposition. The register entry that freezes the decided parts is
[Q51](aegis-restructure.md#q51).

## 1. Where the pipeline stands before this change

Q41 (9 September) introduced the Concept-review pause and Q49 (11 September)
made the reviewed files independent inputs. The run already has two steps:

* **Step 1** — source upload → conversion → concept extraction → Phase 3 →
  Concept Refiner → staged Concept releases for both lanes → pause at 70%
  ("Concept files ready for review"). Pre question generation is deferred for
  new runs (`reviewed_file_workflow_policy` v1).
* **Step 2** — `POST /uploads/{job}/concept-review/master` → read each
  reviewed file with an independent API author, critic and Fixer → generate
  missing Pre questions from the accepted Pre concepts → build both Masters →
  four outputs ready.
* **Publication** — separate explicit acts per output: Concept rows to the
  database plus the shared Bulk Import output workbook, Master rows to the
  database. An edited **Concept** workbook can be uploaded and published in
  one act; there is no path for an edited **Master** workbook.

The stage-by-stage evidence, with file and line citations, is in §2. The
disposition of each stage under the owner's three-step instruction is in §3.

## 2. Stage inventory (verified audit)

_To be completed from the verified audit: ordered Step 1 stages, Step 2
stages, publication acts, with model calls, inputs, outputs and consumers._

## 3. Disposition table

_To be completed: keep in Step 01 / move to Step 02 / already Step 02 /
Step 03 / proposed deletion (owner approval required) / addition._

## 4. Step 02 independence from Step 01 source artifacts

_To be completed: every read of Step 1 material during Step 2, with its
disposition (already severed / severed by this change / legitimate metadata)._

## 5. Repetition register

_To be completed: each candidate, its cost driver, why it exists, and whether
its removal is a mechanical dedup or a step removal needing owner approval._

## 6. Step 03 design — reviewed Master files to CMS and database

_To be completed._

## 7. Decisions requested from the owner

_To be completed: numbered D1…Dn, each with the recommended default that this
change implements unless the owner rules otherwise._
