# Concept and Type Placement Rules

Authoritative statement of the placement decisions made by Mohith Mohan
(Founder, UpSchool) for Project Aegis. These are product decisions, not
engineering preferences. The Phase 3.1/3.2/3.8 grounding contracts and the
Type-mining and Type-alignment prompts implement them.

Where a rule and an implementation disagree, this document is correct and the
implementation is a defect.

---

## Rule 1 — Unattended operation

Generation must reach the output workbook without a human answering anything
mid-run. No pause, no review click, no confirmation.

A run may still **fail** and report why. It may never **wait**. A stalled job is
worse than a failed one, because nothing is watching it.

The only decisions that remain outside automation are those that require
something a person must supply and no automation can invent:

* replacing the uploaded document
* writing a free-text instruction

> **Amended.** This rule originally ended the run when only those remained. It
> now **ships anyway**: the uploaded document is left exactly as it is, the run
> keeps what generation produced, and the decision is flagged in the delivered
> output for the reviewer to sort out. This deliberately publishes a map from a
> source Aegis flagged as questionable — a reviewer reading it can correct that,
> where a run which stopped would have given them nothing to read. Replacing the
> source and writing an instruction are still never performed automatically.

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

---

## Rule 3 — Splitting keeps both sides

When material genuinely teaches two topics, it is **split**, and **both parts
survive**:

* the **earlier topic keeps** a concept covering the foundational idea on its
own terms;
* the **later topic gains** a concept covering how that idea behaves under the
later topic's method.

Neither may be dropped:

* promoting the advanced behaviour **must not delete** the foundational concept;
* keeping the foundational concept **must not leave** the advanced behaviour
untaught.

Concept count is expected to rise as a result. That is correct. The learner
meets the basics where the basics are taught, and meets the advanced behaviour
where it becomes reachable.

---

## Rule 4 — Exception: retrospective reference

Rules 2 and 3 assume the later topic *needs* the earlier one. Some chapters —
especially History — run the other way: a later section merely **mentions or
illustrates** something taught earlier.

**Test the direction of dependence:**

|Direction|Placement|
|-|-|
|Understanding the concept **requires** the later topic's method or framework|Later topic (Rule 2)|
|The later topic **merely refers back** to it|Stays in the topic that teaches it|

In the second case the later topic still gains its own concept — about the
illustration itself.

### That second concept is a new concept, not a split

This is the operative distinction, and getting it wrong has cost whole runs.

**The original concept is not touched.** Its text, its evidence and its
certified placement all stay exactly as they were. The later topic gains a
**separate, independent concept**, grounded in *its own* source blocks, making
*its own* claim. The two were never one concept, so there is nothing to divide,
no split lineage, and no partitioning of protected source items.

**The test is the evidence, not the wording:**

|Do the two concepts ground in the same source blocks?|Operation|
|-|-|
|Same blocks — one claim carrying two teachable ideas|**Split** (Rule 3); both children inherit lineage|
|Different blocks — two claims that merely mention each other|**Independent concept** (this rule); the original is untouched|

A split rewrites the parent's claim text. Anything already certified against
that text — a placement contract, a grounding certificate — is invalidated the
moment it happens. Splitting where Rule 4 applies therefore does not merely
produce a worse map; it can stop the run outright. Never reach for a split to
satisfy a back-reference.

Appearing later in the book does not by itself mean later in teaching.
Chronological and thematic chapters refer backwards constantly.

---

## Rule 4a — Print position is never placement evidence

An image, illustration, figure or Activity sits where the **layout** put it.
Page-fill, plate sections, two-column flow and figure packing all move printed
material away from what it is about. None of that carries meaning.

**Place it with the topic whose content it depicts, not the topic it was
printed under.** An illustration appearing three sections later does not become
part of that section, and a later section does not acquire a concept merely
because a picture landed in it.

This is the same test as Rule 4, stated for non-prose material: ask what the
item is *about*, never where it physically came to rest. Position in the file,
page number, reading order and proximity to a heading are all typesetting
artifacts.

The same applies to **question order**. A question printed early may require a
later topic's method; one printed late may be pure recall of the first topic.
Source order is not teaching order, and must never stand in for it.

---

## Rule 5 — Type and Case placement

### The order is fixed

1. Take the **question inventory**.
2. Classify every question into a **Type**, and a **Case** where one applies.
   Each Type and Case carries a proper written definition — what the task asks
   the learner to do, in its own terms.
3. **Only then** allot the Type/Case to the topic and concept it belongs to.

Classification comes first and is independent of placement. A Type is defined
by what it asks, not by where its questions happened to appear. Placement is a
separate decision taken afterwards, against a Type that is already defined.

### Cases are judged individually; one concept owns the Type

A Type is a **chapter-level identity** and is never split merely to make its
parts fit. Each Case and QID is first judged against the concept it teaches;
those verdicts are placement evidence. If sibling Cases identify different
concepts, the Q14 owner verdict chooses one supported concept for the Type and
moves every Case and QID there. One Type therefore renders under one concept.

Worked example — a Type covering short notes on historical figures:

|Type|Case|Example|Placed in|
|-|-|-|-|
|Short note on a historical figure|Napoleon|Write a short note on Napoleon|Candidate Topic 4|
|Short note on a historical figure|Mussolini|Write a short note on Mussolini|Candidate Topic 5|
|Short note on a historical figure|Hitler|Write a short note on Hitler|Candidate Topic 4|

One Type, three Case-level candidate verdicts across two topics. The owner pass
chooses Topic 4 or Topic 5 from that evidence and places all three Cases there.
Creating separate "Topic 4" and "Topic 5" Type identities is also forbidden:
that would only hide the repeated Type behind different numbering.

(The Type and Case wording above is deliberately rough; the structure is what
this example fixes, not the phrasing.)

### Where it lands

**A task requiring methods from more than one topic belongs to the latest of
those topics** — even when it tests both.

A learner can only attempt it after reaching that topic. Needing an earlier
topic's formula makes that topic a prerequisite, not the owner.

"Latest" means latest in **teaching order**: the topic whose method the learner
must have been taught in order to attempt the task. It is never inferred from
which of the Type's questions is printed last (Rule 4a).

The retrospective-reference exception (Rule 4) applies here too: if the later
topic only illustrates the earlier material rather than being needed to attempt
the task, the task stays with the topic that teaches it.

---

## Rule 6 — Universality

These rules apply to **every subject, board, grade and chapter**. They are not
specific to numerically ordered sections. History's narrative sections,
Physics's cumulative sections, and Mathematics's numbered sections are all
governed by the same test: *what does understanding this require?*

---

## Worked examples — Mathematics

Chapter: **Arithmetic Progressions**

* §5.2 Arithmetic Progressions — definition, first term `a`, common difference
`d`, real-world situations (taxi fare, ladder rungs)
* §5.3 nth Term — `aₙ = a + (n−1)d`
* §5.4 Sum of First n Terms — `Sₙ`

Note §5.2 and §5.4 are **not adjacent**; §5.3 sits between them.

### Example M1 — the `d` / `Sₙ` case (Rule 3)

Draft material: *"The common difference `d` determines whether an AP increases
or decreases, and therefore whether its sum grows without bound."*

**Correct outcome — split, both survive:**

|Topic|Concept|
|-|-|
|§5.2|`d` determines whether an AP increases or decreases|
|§5.4|How `d` governs the behaviour of `Sₙ`|

**Wrong outcomes:**

* Keeping the whole thing in §5.2 — the `Sₙ` behaviour is then untaught, and
ungrounded where it sits.
* Moving the whole thing to §5.4 — §5.2 loses its concept on `d`.

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

|Topic|Concept|
|-|-|
|§3.3|The Frankfurt Parliament — stays where it is taught|
|§5|Germania as a national allegory — its own concept|

**Wrong outcome:** dragging the Frankfurt Parliament concept into §5 because §5
names it. §5 only *illustrates* §3.3; it is not needed to understand it.

### Example H3 — retrospective reference (Rule 4)

The Treaty of Vienna (1815) is taught in **§2.3**. Its settlement eventually
unravelled in the Balkans, which is **§6**.

The Vienna concept **stays in §2.3**. §6 refers back to it; understanding Vienna
does not require §6. §6 keeps its own concept on Balkan nationalist tension.

---

## The decision test, in one place

For any material that touches more than one topic:

1. **Does understanding it require the later topic's method or framework?**

   * Yes → it belongs to the **later topic** (Rule 2).
   * No, the later topic merely refers back → it **stays where it is taught**
(Rule 4).
2. **Does it genuinely teach both topics?**

   * Yes → **split**, and both topics keep a concept (Rule 3).
3. **Is it a task or Type spanning topics?**

   * It belongs to the **latest topic** it requires (Rule 5), subject to the
same Rule 4 test.
4. Apply identically in every subject and chapter (Rule 6).

---

## Decisions still open

Recorded so they are not mistaken for settled policy.

**What happens to a concept that cannot be grounded after every bounded
repair.** Currently it is retired — dropped from the map, named in the run log,
capped at 25% of concepts before the run stops instead. This was chosen in the
absence of a stated preference, on the grounds that shipping an unsupported
claim contradicts the product's purpose.

Two alternatives have been raised and not yet ruled on:

* **Atomise instead of retire** — narrow the concept to exactly what its
evidence supports rather than deleting it, retiring only if that also fails.
Preserves teaching content; preferred by external review.
* Or Modify based on Evidence/ Type.
