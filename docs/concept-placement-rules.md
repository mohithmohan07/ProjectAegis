# Concept and Type Placement Rules

Authoritative statement of the placement decisions made by Mohith Mohan
(Founder, UpSchool) for Project Aegis. These are product decisions, not
engineering preferences. The Phase 3.1/3.2/3.8 grounding contracts and the
Type-mining and Type-alignment prompts implement them.

Where a rule and an implementation disagree, this document is correct and the
implementation is a defect.

---

## Rule 1 — Unattended operation, and an output every time

Generation must reach the output workbook without a human answering anything
mid-run. No pause, no review click, no confirmation.

**Once a chapter has a concept map, an output has to be given.** Past that
point the run may not stop, and it may not wait. Every semantic difficulty
below it has a deterministic answer — see the terminal policy at the end of
this document — so "we could not decide" is never a reason to produce nothing.

A run may still **fail** only *before* there is anything to produce, and only
where a person must supply something no automation can invent:

- the uploaded document must be replaced (it is unreadable, or it is not the
  chapter it claims to be)
- a free-text instruction is genuinely required

Those are the two exits. Neither is a pause: the run ends with the reason
recorded. A stalled job is worse than a failed one, because nothing is
watching it — and a failed job past the concept map is worse than both,
because the work was already done and paid for.

---

## Rule 2 — Advanced placement: the later topic owns shared material

**If a concept contains context of a later topic, it belongs to that later
topic.** Not to the earlier one.

This holds *irrespective of how foundational the rest of the concept looks*. A
concept is not pushed back to the earlier topic on the grounds that its main
idea seems basic.

The reason is teaching order. A teacher reaches such a concept only once the
later topic has been taught, so that is where it belongs in the map. Needing an
earlier topic's definition or formula makes that earlier topic a **prerequisite,
not the owner**.

The only structural requirement: the concept must genuinely involve the later
topic. A concept that draws on nothing from the topic it sits in is misplaced
and moves back.

### How "contains context" is applied

Stated operationally, so that two people — or two model calls — reach the same
answer: **an atomic claim or task belongs to the latest topic whose knowledge,
method or interpretive framework is genuinely necessary to understand or
perform it.**

That is the same rule, not a softer one. It exists because "contains context"
has to be distinguished from "happens to say the words", which is exactly what
Rule 4 already carves out. None of the following moves material on its own:

- mere mention of a later topic
- where the evidence physically sits in the book
- chronology, or section numbering
- shared terminology
- an *optional* later solution method

---

## Rule 3 — Splitting keeps both sides

When material genuinely teaches two topics, it is **split**, and **both parts
survive**:

- the **earlier topic keeps** a concept covering the foundational idea on its
  own terms;
- the **later topic gains** a concept covering how that idea behaves under the
  later topic's method.

Neither may be dropped:

- promoting the advanced behaviour **must not delete** the foundational concept;
- keeping the foundational concept **must not leave** the advanced behaviour
  untaught.

Concept count is expected to rise as a result. That is correct. The learner
meets the basics where the basics are taught, and meets the advanced behaviour
where it becomes reachable.

### What a split may not do

Splitting decides *placement*; it may not decide *what is taught*. Two
operational limits keep it honest:

- **Atomise first.** Break a draft into independently verifiable claims
  *before* applying Rules 2 and 4, so each claim is placed on its own merits
  rather than on the loudest phrase in a paragraph.
- **An irreducible relationship is one claim and must not be split.**
  *"The zollverein contributed to German unification"* is a single
  relationship, owned by the later unification topic. Breaking it into a
  standalone "zollverein" concept and a standalone "unification" concept
  destroys the very thing being taught. Example H1 below depends on this.

---

## Rule 4 — Exception: retrospective reference

Rules 2 and 3 assume the later topic *needs* the earlier one. Some chapters —
especially History — run the other way: a later section merely **mentions or
illustrates** something taught earlier.

**Test the direction of dependence:**

| Direction | Placement |
|---|---|
| Understanding the concept **requires** the later topic's method or framework | Later topic (Rule 2) |
| The later topic **merely refers back** to it | Stays in the topic that teaches it |

In the second case, what the later topic gains depends on how much it does:

- a **bare back-reference** ("as discussed earlier") creates only a typed
  **reference edge** — it must not manufacture a duplicate concept;
- a **substantive** illustration, consequence, application or relationship
  that the later topic independently teaches earns its **own later-owned
  concept**.

Appearing later in the book does not by itself mean later in teaching.
Chronological and thematic chapters refer backwards constantly.

---

## Rule 5 — Type and Case placement

**A task requiring methods from more than one topic belongs to the latest of
those topics** — even when it tests both.

A learner can only attempt it after reaching that topic. Needing an earlier
topic's formula makes that topic a prerequisite, not the owner.

The retrospective-reference exception (Rule 4) applies here too: if the later
topic only illustrates the earlier material rather than being needed to attempt
the task, the task stays with the topic that teaches it.

### When ownership cannot be certified

Ownership is normally settled by a provider/critic pair. That pair can fail —
omit a QID, reformat one, spend its correction budget, or have its answer
rejected — and none of those failures may end the run.

Rule 5 has a deterministic reading that needs no model. Walk the topics in
teaching order and find which ones the task's own vocabulary requires:

- a topic is needed if it is the first to supply **two or more** of the task's
  words, or if it supplies a word found in **no other topic in the chapter**;
- the **latest** needed topic owns the task, the earlier ones are
  prerequisites.

Words describing what the learner must *do* — "find", "calculate", "show",
"given" — are ignored. Every task phrases itself that way, so they identify no
topic.

The derived contract records `certified: false` and
`basis: deterministic_evidence_fallback`, so an audit can always separate a
reviewed owner from a computed one. **It still never uses where the task is
printed.** Physical location is provenance; the only case that falls back to it
is a task whose words match no topic at all, and that is recorded as such.

---

## Rule 6 — Universality

These rules apply to **every subject, board, grade and chapter**. They are not
specific to numerically ordered sections. History's narrative sections,
Physics's cumulative sections, and Mathematics's numbered sections are all
governed by the same test: *what does understanding this require?*

---

## Worked examples — Mathematics

Chapter: **Arithmetic Progressions**
- §5.2 Arithmetic Progressions — definition, first term `a`, common difference
  `d`, real-world situations (taxi fare, ladder rungs)
- §5.3 nth Term — `aₙ = a + (n−1)d`
- §5.4 Sum of First n Terms — `Sₙ`

Note §5.2 and §5.4 are **not adjacent**; §5.3 sits between them.

### Example M1 — the `d` / `Sₙ` case (Rule 3)

Draft material: *"The common difference `d` determines whether an AP increases
or decreases, and how the sum behaves."*

**Correct outcome — split, both survive:**

| Topic | Concept |
|---|---|
| §5.2 | The **sign of `d`** determines whether terms increase, decrease or stay constant |
| §5.4 | In `Sₙ = n/2[2a + (n−1)d]`, `d` affects the **finite sum** for fixed `a` and `n` |

> Do not phrase the §5.4 side as "`d` determines whether the sum grows without
> bound". That is mathematically false — `Sₙ` of an AP is unbounded for
> essentially any `d` — and Class 10 works with finite sums only.

**Wrong outcomes:**
- Keeping the whole thing in §5.2 — the `Sₙ` behaviour is then untaught, and
  ungrounded where it sits.
- Moving the whole thing to §5.4 — §5.2 loses its concept on `d`.

### Example M2 — advanced placement (Rule 2)

Draft material: *"Recognising that a real-world sequence is an AP — the
ladder-rung and taxi-fare situations — is the prerequisite for applying the sum
formula to it."*

The ladder-rung and taxi-fare evidence lives in §5.2. The concept belongs to
**§5.4**, citing §5.2 as prerequisite context. §5.2 keeps its own concept on
recognising an AP.

### Example M3 — Type placement (Rule 5)

A combined numerical requiring **both** `aₙ` and `Sₙ`.

**Placed under §5.4.** Not §5.3, not §5.2 — even though it tests both. It cannot
be attempted before §5.4 is taught.

---

## Worked examples — History

Chapter: **The Rise of Nationalism in Europe**
1. The French Revolution and the Idea of the Nation
2. The Making of Nationalism in Europe (§2.2 liberal nationalism / zollverein;
   §2.3 conservatism after 1815 / Treaty of Vienna)
3. The Age of Revolutions 1830–1848 (§3.3 the Frankfurt Parliament)
4. The Making of Germany and Italy
5. Visualising the Nation (Marianne, Germania)
6. Nationalism and Imperialism (the Balkans)

### Example H1 — advanced placement (Rule 2)

Draft material: *"Economic union through the zollverein prepared the ground for
German political unification."*

The zollverein is taught in **§2.2**. Unification is **§4** — not adjacent.

Understanding this claim requires §4's framework, so the concept belongs to
**§4**, citing §2.2 as prerequisite context. Under Rule 3, **§2 keeps its own
concept** on the zollverein as economic nationalism.

### Example H2 — retrospective reference (Rule 4)

The Germania painting (§5, Fig. 17) is captioned as having hung in the Church of
St Paul *where the Frankfurt Parliament was convened in March 1848* — and the
Frankfurt Parliament is taught in **§3.3**.

**Correct outcome:**

| Topic | Concept |
|---|---|
| §3.3 | The Frankfurt Parliament — stays where it is taught |
| §5 | Germania as a national allegory — its own concept |

**Wrong outcome:** dragging the Frankfurt Parliament concept into §5 because §5
names it. §5 only *illustrates* §3.3; it is not needed to understand it.

### Example H3 — retrospective reference (Rule 4)

The Treaty of Vienna (1815) is taught in **§2.3**. Its settlement eventually
unravelled in the Balkans, which is **§6**.

The Vienna concept **stays in §2.3**. §6 refers back to it; understanding Vienna
does not require §6.

**But if the source explicitly teaches how the settlement was later challenged
or unravelled in the Balkans, that relationship is itself a claim, and it is
owned by §6.** It must not disappear because its subject was introduced
earlier. Two claims, two owners:

| Claim | Owner |
|---|---|
| The Treaty of Vienna restored conservative monarchies | §2.3 |
| The Vienna settlement was progressively challenged as Balkan nationalities broke away | §6 |

---

## The decision test, in one place

For any material that touches more than one topic:

1. **Does understanding it require the later topic's method or framework?**
   - Yes → it belongs to the **later topic** (Rule 2).
   - No, the later topic merely refers back → it **stays where it is taught**
     (Rule 4).
2. **Does it genuinely teach both topics?**
   - Yes → **split**, and both topics keep a concept (Rule 3).
3. **Is it a task or Type spanning topics?**
   - It belongs to the **latest topic** it requires (Rule 5), subject to the
     same Rule 4 test.
4. Apply identically in every subject and chapter (Rule 6).

---

## Terminal policy — what happens to a concept that will not ground

**Settled: modify it based on its evidence. Never retire it, never stop the
run.**

A concept that cannot be grounded after every bounded repair is **not**
deleted, **not** suppressed, **not** shipped with a warning label, and **not**
turned into a failed run. It is **rewritten to say exactly what its evidence
supports**. The ladder is:

    bounded refine / move / split
      -> one final evidence-supported atomisation or minimal-concept attempt
      -> deterministic evidence narrowing  ← always produces output
           * keep every clause the topic's canonical source blocks support
           * drop every clause they do not, and name it in the run log
           * if no written clause survives, restate the concept verbatim
             from its own source block

The last rung makes no model call, invents no wording, and removes no row, so
it is available even when the provider is unreachable, the budget is spent, or
a downstream contract has failed outright. That is what makes "an output has to
be given" a guarantee rather than an aspiration.

Three properties are what buy the guarantee, and all three are load-bearing:

- **Deterministic.** The rewrite is a pure function of the candidate and the
  canonical source, so a Resume reproduces it instead of paying for it again.
- **Conservative.** A clause survives only when every content word in it
  appears in the cited evidence. The test drops a borderline clause rather
  than keeping an unsupported one.
- **Lossless in count.** Concepts are rewritten, never removed.

The cost is explicit and accepted: a disposed concept may **teach less** than
its draft intended. What it may never do is teach something the source does
not support, or vanish. The earlier 25% retirement cap is withdrawn along with
retirement itself.

Placement is not part of this. Narrowing changes what a concept *claims*; only
Rules 2–5 change which topic *owns* it. A clause dropped for lack of support in
§5.2 is not silently relocated to §5.4.

Deduplication remains permitted only when claim-level certification proves
every protected claim, QID, figure, image, example and evidence edge survives
elsewhere.

### Implementation

| Rule | Enforced by |
|---|---|
| 1 (unattended, always an output) | `canonical_source_phase38_boundary_grounding_turnover_contract._terminal_disposition` |
| 2, 4 (ownership, retrospective reference) | `placement_policy.compute_placement`, applied in `canonical_source_phase32_topology_adjudication_contract._enforce_placement_policy` |
| 3 (splitting keeps both sides) | `placement_policy.audit_split` |
| 5 (Type/Case ownership) | `_type_case_contract_for_qid` in `generation.py` Case routing |
| 5 when certification fails | `placement_policy.compute_deterministic_placement` |
| Terminal policy | `evidence_narrowing.narrow_records` |
