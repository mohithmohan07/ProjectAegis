# Project Aegis Concept Release Contract

This contract extends `concept-placement-rules.md` without changing its semantic
placement authority. The placement rules decide what belongs where. This
contract decides how a generation result leaves the pipeline.

## 1. No in-generation user selection

Build Concepts must run unattended after the user starts it. It may use bounded
provider/critic repair, autonomous source review, move/refine/split decisions,
and terminal evidence narrowing. It must not render a concept, Type, Case, QID,
BLK, topic, or repair choice for the user to select at 81%, 89%, or any other
point.

When autonomous resolution does not produce a clean candidate, generation
releases the latest materialized output with the unresolved issue attached. It
does not park the job.

## 2. An output exists for every terminal condition

Success, semantic disagreement, exhausted repair, source-critical failure,
validation failure, and provider unavailability all converge on one release
boundary. The release may contain zero concept rows if the run failed before a
row existed, but it must still contain the source, converted MMD, complete run
log, checkpoint, evidence, BLKs, pending issue, and manifest.

## 3. Release before database publication

Generation performs no concept database insert and does not append to the
canonical Bulk Import output workbook. It creates a review release first.

Database publication is a separate explicit action named **Upload to database**.
That action is enabled only for a release that passes all deterministic concept,
QID, Type/Case, source-evidence, topic-placement, inventory, and grounding gates.
The upload action makes no generation request.

## 4. Review workbook

The review workbook contains at least:

- **Concepts Review** with `release_status`, `error_count`, `warning_count`,
  `error_codes`, and `error_messages` beside every concept row;
- **Issues** with severity, exact row/concept/field, QID, Type, Case, BLKs,
  message, snippet, and diagnostic source;
- **QID Type Case Routes** with reusable Type identity, Type definition, Case
  identity, Case definition, QID, certified owner topic, host hints, Example,
  and exact evidence blocks;
- **Evidence BLKs** with every available canonical block and its text;
- **Run Log**; and
- **Release Manifest**.

Rows with errors are highlighted red, warnings yellow, and clean rows green.
The full JSON values remain in the context package if an Excel cell must be
truncated.

## 5. Complete context package

The context ZIP travels with the issue and includes:

- original uploaded source;
- converted MMD;
- generation log in JSON and text;
- generation checkpoint;
- pending semantic issue and candidate/evidence packet;
- Question/Task Inventory;
- mined reusable Types and Cases;
- exact QID-to-Type/Case routes;
- semantic graph;
- canonical source;
- all BLKs and the referenced evidence subset;
- final grounding certificate, when available;
- concept placement rules;
- review workbook; and
- a SHA-256 file manifest.

Credentials, authorization headers, passwords, session tokens, and API secrets
must be redacted from exported JSON.

## 6. Reusable Type and split Case ownership

A reusable Type is a method identity, not a single-topic container. Its Cases
may have different certified owners.

For example:

```text
Type 01: Applying an arithmetic-progression relationship
  Case 01: Find a_n       -> owner: The nth Term
    Example 01: QINV-0001
  Case 02: Find S_n       -> owner: Sum of First n Terms
    Example 01: QINV-0002
```

The Type number remains the same. Each Case is rendered with its definition and
its source-owned Example beneath it at the certified destination. A QID appears
in exactly one Type/Case destination. The same QID may not be copied merely to
make both topics look complete.

## 7. Semantic placement remains authoritative

The release boundary does not weaken the governing placement rules:

- the latest genuinely required topic owns shared material;
- split material keeps both independently teachable sides;
- retrospective references stay where the underlying material is taught;
- a multi-topic task belongs to the latest topic required to attempt it; and
- these rules apply across subjects, boards, grades, and chapter structures.

A release records disagreements. It does not use row highlighting as permission
to silently invent ownership or duplicate teaching content.
