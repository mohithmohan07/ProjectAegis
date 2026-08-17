# Open/Specific policy registry — evidence for the `answer_restriction` verdict

    registry_id: registry-v2.0
    status: AUTHORITATIVE — the corrected Open/Specific API Policy Registry v2.0
    version_line: Version 2.0 · Corrected 16 August 2026 · Aegis semantic answer-space policy
    binary_source_of_truth: docs/open-specific-registry-v2.xlsx

## What this file is, and how it is used

This file is **evidence the model reasons over** when it decides a question's
`answer_restriction`. Per **Q11** it is *"versioned evidence for the model —
never executable classification."* The workbook says the same of itself:
*"This workbook is never an executable lookup table"* and the answer
restriction *"is decided from the complete item and scoring contract, never
from command words or local matching."* Two rules bind every consumer:

1. **Pass it whole.** The complete text below goes into the
   `assessment.answer_restriction` decision payload. No code parses it,
   indexes it, extracts rules from it, or branches on subject, question type,
   Policy ID, keyword, or family name. The Policy IDs (e.g. `CHEM-A01`) are
   the workbook's own labels for its worked policies — they are reading aids
   for the model, never keys any code matches against.
2. **Hash it into the policy version.** The pass's `policy_version` includes
   the sha256 of this file. Replacing it re-keys and re-decides every stored
   verdict — the same mechanism the Architect uses for the instruction-set
   hash (`docs/restructure-handoff.md` §3).

The authoritative artifact is the committed workbook
`docs/open-specific-registry-v2.xlsx` (Version 2.0, corrected 16 August
2026). The transcription below is a faithful, complete rendering of its
unique policy content for the text payload; the `.xlsx` is the binary source
of truth. The workbook's "All Subjects (Combined)" sheet is a generated
exact mirror of the four subject registries and is present in the `.xlsx`; it
is not duplicated here.

**The decided carve-out (Q11) still governs.** The Math/Physics
method-equivalence families (word problems, variable assignment,
multi-formula, numerical technique, own-words definitions) keep their v1
**Open** stance so a learner solving by a valid alternate method is always
safe in grading — revisit only when Clarius' Specific grading provably
honours recorded equivalents. The no-local-fallback invariant holds: an
unclassifiable item ships with the author's best verdict and a review flag;
`answer_restriction` never receives an invented third value and the row is
never withheld.

---

## Index & Legend

* **Purpose** — Calibrated policy and examples supplied to the API author, independent critic, and adjudicator. This workbook is never an executable lookup table.
* **Decision input** — Use the complete question, source context, image or table, expected answer, rubric, response modality, subject, and grade.
* **Open** — The prompt and scoring contract intentionally permit materially different full-credit content, evidence, assumptions, methods, representations, or justified conclusions.
* **Specific** — The task has a bounded semantic target or closed answer set. Accepted equivalents may include wording, notation, units, algebraic form, and harmless ordering.
* **Equivalence** — Paraphrase or equivalent notation alone does not make an item Open. Specific means a fixed semantic target, not literal-string matching.
* **Assessed response** — If only a fixed final result is assessed, the item is Specific. It is Open when materially different methods, evidence, assumptions, or conclusions themselves earn full credit.
* **Objective policy** — After the API and critic verify a closed response contract and exactly one correct option, the answer restriction is Specific.
* **Disagreement** — Author and critic disagreement goes to an adjudicator API. If the evidence still does not support a decision, export classification_unresolved with diagnostics.
* **No local fallback** — No command-word matching, keyword matching, regular expressions, first-match behavior, deterministic default, or unlisted-family fallback may assign Open or Specific.
* **Policy family** — Meaning
* **A — Generally Open** — Calibrated examples whose scoring contracts usually invite materially different valid responses. Every actual item is still judged from its complete evidence.
* **B — Context-dependent** — Paired Open and Specific examples. The same broad assessment family can have either answer restriction.
* **C — Closed-response** — Calibrated examples with bounded response contracts. Every actual item is still verified from its complete evidence.
* **Subject-sheet authority** — The corrected subject sheets are the canonical policy registry.
* **Combined sheet** — All Subjects (Combined) is generated exactly from the subject sheets and must never contain independently authored semantic rows.
* **Versioning** — Store this registry version and identity with every author, critic, adjudicator, cache, audit, and release record.
* **Change summary** — Removed the default-to-Specific rule; reconciled science conflicts; corrected equivalent-form logic; repaired misleading examples; converted command-word rules into answer-space policies.
* **Export mapping** — Mode maps to answer_restriction. The answer-space contract, required elements, accepted variations, evidence, and rationale remain in the semantic audit.
* **Status** — Corrected and reviewed. Use as API policy and calibration evidence only.

## Reviewer rubrics and worked examples

*Open / Specific — Reviewer Rubrics and Worked Examples*

*Every classification and rubric is authored from the complete assessment evidence, reviewed independently, and adjudicated through the API when required.*

### Full-credit answer space
* **Open — Full-credit Rule:** More than one materially different response can earn full credit when each satisfies the stated evidence and quality requirements.
* **Open — Example:** Recommend two defensible ways to reduce contamination in a local water source and justify each.
* **Specific — Full-credit Rule:** The scoring contract has one bounded semantic target or a closed set of required elements, while equivalent wording or notation remains acceptable.
* **Specific — Example:** Why is sodium stored under kerosene?
* **Reviewer Focus:** Inspect the complete expected-answer and marking contract, not the command word.

### Required content
* **Open — Full-credit Rule:** The rubric defines quality, relevance, scientific or textual support, and constraints without prescribing one fixed content set.
* **Open — Example:** Explain two plausible causes of an unexpected experimental yield using the supplied observations.
* **Specific — Full-credit Rule:** The rubric identifies a fixed set of facts, relations, labels, stages, or causal elements that must be present.
* **Specific — Example:** List all components of the reflex arc in order.
* **Reviewer Focus:** Verify whether the content universe is intentionally selectable or completely bounded.

### Method and working
* **Open — Full-credit Rule:** Different valid methods themselves are assessed and can independently earn credit.
* **Open — Example:** Solve the problem using two valid methods and compare their efficiency.
* **Specific — Full-credit Rule:** A method is prescribed, or only one bounded result is assessed even though unseen alternative methods could exist.
* **Specific — Example:** Solve the pair of equations using substitution.
* **Reviewer Focus:** Check what the rubric actually rewards: method choice, prescribed steps, or only the result.

### Evidence selection
* **Open — Full-credit Rule:** Learners may select different relevant evidence and build different defensible lines of reasoning.
* **Open — Example:** Develop an interpretation of the character's change using evidence you select from the passage.
* **Specific — Full-credit Rule:** The source and rubric require a fixed quotation, fact, event, or defined evidence set.
* **Specific — Example:** Who is speaking in the quoted line?
* **Reviewer Focus:** Compare the permitted evidence set with the evidence required for full marks.

### Assumptions and models
* **Open — Full-credit Rule:** Different defensible assumptions or models are explicitly permitted, assessed, and compared.
* **Open — Example:** Model the situation under two defensible assumptions and compare the results.
* **Specific — Full-credit Rule:** The assumptions, reference system, variables, or model are supplied and lead to a bounded result.
* **Specific — Example:** Use the stated sign convention to calculate displacement.
* **Reviewer Focus:** Do not treat the mere possibility of another method or notation as Open.

### Conclusion or judgment
* **Open — Full-credit Rule:** Different conclusions may earn full credit when they are relevant, evidence-based, and satisfy the rubric.
* **Open — Example:** Evaluate two conservation strategies and defend a recommendation using the data.
* **Specific — Full-credit Rule:** The evidence supports one bounded conclusion, observation, identification, or relationship.
* **Specific — Example:** Read the enzyme activity at the stated temperature from the graph.
* **Reviewer Focus:** Confirm whether the rubric permits genuinely different conclusions, not merely different phrasing.

### Accepted variation
* **Open — Full-credit Rule:** Accepted responses may differ materially in content, method, evidence, representation, or justified conclusion.
* **Open — Example:** Represent the relationship in two valid forms and explain how they correspond.
* **Specific — Full-credit Rule:** Variation is limited to semantically equivalent wording, notation, units, algebraic form, or harmless ordering.
* **Specific — Example:** Write the formula of sodium carbonate.
* **Reviewer Focus:** Record accepted equivalents explicitly without changing a bounded target into an Open item.

### Scoring design
* **Open — Full-credit Rule:** Use an analytic rubric for relevance, correctness, evidence, reasoning, fulfilment of constraints, and communication.
* **Open — Example:** Design an investigation and justify the variables, controls, procedure, and reliability measures.
* **Specific — Full-credit Rule:** Use a bounded element or step rubric with explicit accepted equivalents and partial-credit rules where appropriate.
* **Specific — Example:** Explain why alcohol is used to decolourise a leaf in the starch test.
* **Reviewer Focus:** The API author drafts the rubric; an independent API critic verifies that it matches the real answer space.

### Worked Question-and-Rubric Examples

### Subject
* **Open — Full-credit Rule:** Assessment Family
* **Open — Example:** Open Question
* **Specific — Full-credit Rule:** Open Full-credit Rubric
* **Specific — Example:** Specific Question
* **Reviewer Focus:** Specific Full-credit Rubric

### Chemistry
* **Open — Full-credit Rule:** Scientific explanation
* **Open — Example:** A reaction gives a much lower yield than expected. Using the observations, explain two plausible causes.
* **Specific — Full-credit Rule:** 5 marks: two scientifically plausible causes (2); each cause linked to supplied evidence (2); coherent conclusion distinguishing or prioritising the causes (1). Accept different cause pairs when fully supported.
* **Specific — Example:** Why is sodium stored under kerosene?
* **Reviewer Focus:** 3 marks: sodium reacts readily with oxygen or moisture (1); kerosene isolates sodium from air and water (1); this prevents a violent reaction, oxidation, or fire (1). Accept equivalent scientific wording.

### Chemistry
* **Open — Full-credit Rule:** Experimental reasoning
* **Open — Example:** Propose two valid modifications that would improve the reliability of the investigation and justify each.
* **Specific — Full-credit Rule:** 4 marks: two workable modifications (2); a scientifically valid reliability justification for each (2). Accept different modifications that address repeatability, measurement precision, or control of variables.
* **Specific — Example:** Why is the precipitate washed before drying?
* **Reviewer Focus:** 2 marks: washing removes soluble impurities or residual ions (1); drying then yields a purer precipitate for accurate mass or analysis (1).

### Biology
* **Open — Full-credit Rule:** Ecological prediction
* **Open — Example:** Predict possible ecosystem changes after removal of the top predator and justify them.
* **Specific — Full-credit Rule:** 5 marks: two biologically plausible changes (2); food-web or population reasoning for each (2); assumptions or evidence stated (1). Accept different defensible pathways consistent with the ecosystem.
* **Specific — Example:** Predict the immediate effect of stopping insulin secretion on blood glucose.
* **Reviewer Focus:** 2 marks: blood glucose rises (1); because cellular uptake or storage of glucose is reduced without insulin (1). Accept an equivalent bounded mechanism.

### Biology
* **Open — Full-credit Rule:** Experimental design
* **Open — Example:** Design a fair investigation of one factor affecting transpiration.
* **Specific — Full-credit Rule:** 6 marks: testable factor and prediction (1); viable procedure (2); suitable controls (1); measurement and repeats (1); safety or reliability justification (1). Different valid designs may earn full credit.
* **Specific — Example:** Why is alcohol used to decolourise the leaf before testing for starch?
* **Reviewer Focus:** 2 marks: alcohol removes chlorophyll (1); removal makes the iodine colour change visible (1).

### Languages & Humanities
* **Open — Full-credit Rule:** Interpretation
* **Open — Example:** Develop an interpretation of the character's change and support it with evidence from the passage.
* **Specific — Full-credit Rule:** 6 marks: defensible interpretation (2); relevant evidence (2); explanation connecting evidence to interpretation (1); coherent expression (1). Accept materially different readings when supported.
* **Specific — Example:** Who is speaking in the quoted line?
* **Reviewer Focus:** 1 mark: the uniquely identified speaker. Accept the full name or an unambiguous equivalent reference supported by the extract.

### Languages & Humanities
* **Open — Full-credit Rule:** Writing and transformation
* **Open — Example:** Write a persuasive response supporting a defensible position on the issue.
* **Specific — Full-credit Rule:** 8 marks: position and relevance (2); evidence or examples (2); reasoning (2); organisation and language control (2). Different positions may earn full credit when well supported.
* **Specific — Example:** Change the sentence into the specified passive form without changing its meaning.
* **Reviewer Focus:** 2 marks: correct passive construction (1); original tense and meaning preserved (1). Accept only grammatically equivalent forms allowed by the context.

### Mathematics & Physics
* **Open — Full-credit Rule:** Solution method
* **Open — Example:** Solve the problem using two valid methods and compare their efficiency.
* **Specific — Full-credit Rule:** 6 marks: first valid method and result (2); second valid method and result (2); meaningful comparison of efficiency or suitability (2). Accept any mathematically valid pair of methods.
* **Specific — Example:** Solve the pair of equations using substitution.
* **Reviewer Focus:** 4 marks: correct substitution setup (1); valid algebraic steps (1); both correct values (1); verification or correctly stated ordered solution (1). Equivalent notation is accepted.

### Mathematics & Physics
* **Open — Full-credit Rule:** Model and calculation
* **Open — Example:** Develop two defensible models for the observed trend and compare their limitations.
* **Specific — Full-credit Rule:** 6 marks: two coherent models (2); evidence-based fit or reasoning for each (2); comparison of limitations or assumptions (2). Accept different valid model pairs.
* **Specific — Example:** Calculate current when the potential difference and resistance are given.
* **Reviewer Focus:** 3 marks: correct relation between current, potential difference, and resistance (1); correct substitution (1); correct value with unit (1). Accept algebraically equivalent working.

## Chemistry

*Corrected API policy registry: answer restriction is decided from the complete item and scoring contract, never from command words or local matching.*

### CHEM-A01 · Open — Deliberate valid selection
* **Policy family:** A — Generally Open
* **Semantic decision rule:** The rubric deliberately accepts a learner-selected subset from a larger scientifically valid universe.
* **Example 1:** Give three different valid uses of noble gases and justify each selection.
* **Example 2:** Choose any two corrosion-prevention methods suitable for a coastal bridge and justify them.

### CHEM-A02 · Open — Design / suggest
* **Policy family:** A — Generally Open
* **Semantic decision rule:** Several scientifically defensible designs or interventions can satisfy the stated constraints.
* **Example 1:** Propose two viable ways to reduce acid contamination in a local water source.
* **Example 2:** Design a safe test sequence to distinguish the supplied salt solutions.

### CHEM-A03 · Open — Evaluate / critique
* **Policy family:** A — Generally Open
* **Semantic decision rule:** Different evidence-based judgments can earn full credit when the rubric assesses reasoning and trade-offs.
* **Example 1:** Evaluate two material choices for a reusable food container.
* **Example 2:** Critique the investigation and propose justified improvements.

### CHEM-B01-S · Specific — Scientific explanation / reason
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The question targets one bounded causal mechanism or a defined set of required scientific elements.
* **Example 1:** Why is sodium stored under kerosene?
* **Example 2:** Explain why quicklime is used to dry ammonia.

### CHEM-B01-O · Open — Scientific explanation / reason
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The question explicitly permits materially different explanations, evidence selections, or defensible causal accounts.
* **Example 1:** Explain two different scientifically defensible causes of the observed corrosion pattern.
* **Example 2:** Construct an evidence-based explanation for the unexpected yield, considering more than one plausible factor.

### CHEM-B02-S · Specific — Predict / infer
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** Fully specified conditions determine a bounded outcome or observation.
* **Example 1:** Predict the gas evolved when zinc reacts with dilute hydrochloric acid.
* **Example 2:** Predict the products observed when copper carbonate is strongly heated.

### CHEM-B02-O · Open — Predict / infer
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The scenario permits multiple scientifically defensible consequences, provided assumptions and evidence are stated.
* **Example 1:** Predict possible effects of untreated acidic effluent on the local water system and justify them.
* **Example 2:** Infer more than one plausible cause of the anomalous reaction rate from the supplied evidence.

### CHEM-B03-S · Specific — Experimental reasoning
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The assessed target is the fixed purpose of a step, control, apparatus, or accepted laboratory principle.
* **Example 1:** Why is the gas collected over water in this setup?
* **Example 2:** Why is the precipitate washed before drying?

### CHEM-B03-O · Open — Experimental reasoning
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task assesses alternative valid experimental designs, critiques, or reliability improvements.
* **Example 1:** Propose two valid modifications that would improve the reliability of the investigation.
* **Example 2:** Design an alternative procedure to distinguish the ions and justify each chosen test.

### CHEM-B04-S · Specific — Comparison / application
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** Stated criteria and constraints lead to a bounded comparison or best-supported choice.
* **Example 1:** Compare ionic and covalent compounds using the three listed properties.
* **Example 2:** Choose the correct electrode for the stated cell conditions and explain the fixed chemical basis.

### CHEM-B04-O · Open — Comparison / application
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts different valid criteria, trade-offs, or solution choices.
* **Example 1:** Compare two fuels using criteria you select and defend your recommendation.
* **Example 2:** Recommend a material for the application and justify a defensible trade-off.

### CHEM-B05-S · Specific — Identification / listing
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The evidence identifies one target, or the question requires a complete bounded set.
* **Example 1:** Identify the gas from the stated test results.
* **Example 2:** List every stage of the Contact Process in the required sequence.

### CHEM-B05-O · Open — Identification / listing
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts a valid subset or multiple qualifying examples from a larger universe.
* **Example 1:** Identify three different laboratory acids and state one valid use of each.
* **Example 2:** List four distinct examples of oxidation from everyday life.

### CHEM-B06-S · Specific — Observation / data
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task requires a fixed read-off, expected observation, or uniquely supported interpretation.
* **Example 1:** State the colour change when blue litmus is placed in the given acid.
* **Example 2:** Read the solubility at the specified temperature from the graph.

### CHEM-B06-O · Open — Observation / data
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts multiple evidence-based interpretations or explanations of non-ideal data.
* **Example 1:** Interpret the anomalous data point and give two defensible explanations.
* **Example 2:** Use the observations to propose and justify more than one plausible reaction pathway.

### CHEM-B07-S · Specific — Equation / formula / symbol
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task has a bounded chemical relation, formula, name, or balanced equation; harmless equivalent notation remains acceptable.
* **Example 1:** Write the formula of sodium carbonate.
* **Example 2:** Complete and balance the stated reaction.

### CHEM-B07-O · Open — Equation / formula / symbol
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task explicitly assesses multiple valid chemical examples, representations, or pathways.
* **Example 1:** Write two different valid neutralisation equations and explain how each meets the definition.
* **Example 2:** Construct two valid reaction sequences that produce the stated compound.

### CHEM-B08-S · Specific — Numerical / definition
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task has a fixed semantic target or calculated result, although equivalent working and expression are accepted.
* **Example 1:** Calculate the amount of substance in 44 g of carbon dioxide.
* **Example 2:** Define oxidation using the required scientific elements.

### CHEM-B08-O · Open — Numerical / definition
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The scoring contract explicitly credits materially different solution methods or explanatory representations.
* **Example 1:** Determine the composition using any valid quantitative method and compare two possible approaches.
* **Example 2:** Explain the concept using two different valid representations and relate them.

### CHEM-C01 · Specific — Verified objective response
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The API and critic verify one correct option or one closed response set with no overlap among alternatives.
* **Example 1:** Which option gives the atomic number of chlorine?
* **Example 2:** Evaluate the assertion–reason pair using the supplied response code.

### CHEM-C02 · Specific — Closed recall / label
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The complete evidence establishes one bounded fact, label, term, or pairing; accepted scientific equivalents are recorded.
* **Example 1:** Name the ore shown by the supplied evidence.
* **Example 2:** Label the indicated electrode in the diagram.

## Biology

*Corrected API policy registry: answer restriction is decided from the complete item and scoring contract, never from command words or local matching.*

### BIO-A01 · Open — Deliberate valid selection
* **Policy family:** A — Generally Open
* **Semantic decision rule:** The rubric deliberately accepts a learner-selected subset from a larger biologically valid universe.
* **Example 1:** Choose four valid adaptations of desert plants and explain their value.
* **Example 2:** Give three valid functions of the skin and relate each to homeostasis.

### BIO-A02 · Open — Design / suggest
* **Policy family:** A — Generally Open
* **Semantic decision rule:** Several biologically defensible interventions or investigation designs satisfy the stated constraints.
* **Example 1:** Propose two viable strategies to reduce mosquito breeding in the locality.
* **Example 2:** Design a fair investigation of one factor affecting transpiration.

### BIO-A03 · Open — Evaluate / critique
* **Policy family:** A — Generally Open
* **Semantic decision rule:** Different evidence-based judgments can earn full credit when supported by biological reasoning.
* **Example 1:** Evaluate two conservation strategies for the stated ecosystem.
* **Example 2:** Critique the investigation and propose justified improvements.

### BIO-B01-S · Specific — Scientific explanation / reason
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The question targets one bounded biological mechanism or a defined set of required elements.
* **Example 1:** Why are red blood cells biconcave?
* **Example 2:** Explain the role of chlorophyll in photosynthesis.

### BIO-B01-O · Open — Scientific explanation / reason
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The question explicitly permits materially different explanations, evidence selections, or defensible perspectives.
* **Example 1:** Explain two defensible ecological consequences of the habitat change using evidence.
* **Example 2:** Construct an explanation for the population decline using more than one plausible factor.

### BIO-B02-S · Specific — Predict / infer
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** Fully specified biological conditions determine a bounded outcome.
* **Example 1:** Predict the immediate effect when chlorophyll is absent from the tested leaf region.
* **Example 2:** Predict the direct effect of stopping insulin secretion on blood glucose.

### BIO-B02-O · Open — Predict / infer
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The scenario permits multiple biologically defensible consequences when assumptions and evidence are stated.
* **Example 1:** Predict possible ecosystem changes after removal of the top predator and justify them.
* **Example 2:** Infer more than one plausible cause of the population pattern from the supplied field data.

### BIO-B03-S · Specific — Experimental reasoning
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The assessed target is the fixed purpose of a step, control, stain, reagent, or accepted biological principle.
* **Example 1:** Why is alcohol used to decolourise the leaf?
* **Example 2:** Why is a control setup required in this experiment?

### BIO-B03-O · Open — Experimental reasoning
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task assesses alternative valid designs, critiques, controls, or reliability improvements.
* **Example 1:** Propose two valid changes that would improve reliability and justify them.
* **Example 2:** Design an alternative investigation of the factor and explain the controls.

### BIO-B04-S · Specific — Comparison / application
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** Stated criteria and evidence lead to a bounded comparison or best-supported decision.
* **Example 1:** Compare mitosis and meiosis using the listed criteria.
* **Example 2:** Select the treatment supported by the stated diagnosis and explain the fixed biological basis.

### BIO-B04-O · Open — Comparison / application
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts different valid criteria, interventions, or evidence-based trade-offs.
* **Example 1:** Compare two conservation approaches using defensible criteria of your choice.
* **Example 2:** Recommend a public-health response and justify the trade-offs.

### BIO-B05-S · Specific — Identification / listing
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The evidence uniquely identifies a target, or a complete bounded set is required.
* **Example 1:** Identify the labelled nephron structure from the diagram.
* **Example 2:** List all components of the reflex arc in order.

### BIO-B05-O · Open — Identification / listing
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts a valid subset or multiple qualifying examples from a larger universe.
* **Example 1:** Identify three adaptations of aquatic plants and explain each.
* **Example 2:** List four valid functions of blood and connect each to a component.

### BIO-B06-S · Specific — Observation / data
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task requires a fixed read-off, expected observation, or uniquely supported inference.
* **Example 1:** State the colour produced by iodine in the presence of starch.
* **Example 2:** Read the enzyme activity at the specified temperature.

### BIO-B06-O · Open — Observation / data
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts multiple evidence-based interpretations or explanations of complex or anomalous data.
* **Example 1:** Interpret the population graph and give two defensible biological explanations.
* **Example 2:** Explain the anomalous result using more than one plausible biological factor.

### BIO-B07-S · Specific — Definition / function / process
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task has a bounded semantic target or required process elements; equivalent scientific wording remains acceptable.
* **Example 1:** Define photosynthesis using the required elements.
* **Example 2:** State the endocrine function of the pancreas in the stated context.

### BIO-B07-O · Open — Definition / function / process
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The scoring contract explicitly accepts materially different explanatory models, examples, or connected functions.
* **Example 1:** Explain the organ's role using two valid biological perspectives.
* **Example 2:** Represent the process in two valid forms and explain their correspondence.

### BIO-B08-S · Specific — Numerical / sequence / label
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task has a bounded value, sequence, scientific term, or label; accepted equivalents are recorded.
* **Example 1:** Calculate magnification from the supplied values.
* **Example 2:** Arrange the stages of mitosis in the required order.

### BIO-B08-O · Open — Numerical / model reasoning
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric explicitly credits materially different quantitative methods, assumptions, or biological models.
* **Example 1:** Estimate population size using two valid sampling assumptions and compare the results.
* **Example 2:** Use two defensible models to explain the observed inheritance data.

### BIO-C01 · Specific — Verified objective response
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The API and critic verify one correct option or one closed response set with no overlap among alternatives.
* **Example 1:** Which option identifies the functional unit of the kidney?
* **Example 2:** Evaluate the assertion–reason pair using the supplied response code.

### BIO-C02 · Specific — Closed recall / label
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The complete evidence establishes one bounded fact, term, label, or pairing; accepted scientific equivalents are recorded.
* **Example 1:** Name the scientific term defined by the prompt.
* **Example 2:** Label the indicated part of the flower.

## Languages & Humanities

*Corrected API policy registry: answer restriction is decided from the complete item and scoring contract, never from command words or local matching.*

### LANG-A01 · Open — Opinion / creative response
* **Policy family:** A — Generally Open
* **Semantic decision rule:** The rubric intentionally accepts materially different viewpoints, compositions, or creative choices that satisfy common criteria.
* **Example 1:** Write a persuasive response supporting a defensible position.
* **Example 2:** Create a diary entry from the character's perspective using textual evidence.

### LANG-A02 · Open — Suggest / design / value response
* **Policy family:** A — Generally Open
* **Semantic decision rule:** Several relevant recommendations or value-based responses can earn full credit when justified.
* **Example 1:** Recommend measures to protect the heritage site and justify them.
* **Example 2:** Propose two solutions to the civic problem using evidence.

### LANG-A03 · Open — Interpretive synthesis
* **Policy family:** A — Generally Open
* **Semantic decision rule:** The source and rubric deliberately permit multiple defensible interpretations supported by evidence.
* **Example 1:** Develop an interpretation of the poem's central tension using selected lines.
* **Example 2:** Explain a possible message of the passage and support it with evidence.

### LANG-B01-S · Specific — Reason / explanation
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The source and rubric require a bounded reason, cause, process, or set of essential points.
* **Example 1:** Why does the named character leave at this moment?
* **Example 2:** Explain the stated constitutional process using the required stages.

### LANG-B01-O · Open — Reason / explanation
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts materially different evidence-based reasons or explanatory structures.
* **Example 1:** Explain two defensible motives for the decision using different evidence.
* **Example 2:** Account for the event using more than one supported factor.

### LANG-B02-S · Specific — Inference / theme / character
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The extract and question establish one bounded inference or required evidence set.
* **Example 1:** Infer the speaker's location from the stated details.
* **Example 2:** Identify the fixed theme element named by the prompt and cite the required line.

### LANG-B02-O · Open — Inference / theme / character
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The text permits multiple defensible readings and the rubric accepts different evidence selections.
* **Example 1:** Develop a supported interpretation of the character's change.
* **Example 2:** Infer two plausible meanings of the image and defend each from the text.

### LANG-B03-S · Specific — Comparison / evaluation
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The prompt supplies fixed comparison dimensions or a bounded evaluative target.
* **Example 1:** Compare the two systems using the three stated criteria.
* **Example 2:** Evaluate whether the stated action meets the legal definition using the supplied evidence.

### LANG-B03-O · Open — Comparison / evaluation
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts different defensible criteria, evidence, or judgments.
* **Example 1:** Compare the characters using criteria you select and support each point.
* **Example 2:** Evaluate the policy from two defensible stakeholder perspectives.

### LANG-B04-S · Specific — Extract / evidence response
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The extract supplies one bounded speaker, event, meaning, or answer set.
* **Example 1:** Who is speaking in the quoted line?
* **Example 2:** State the event that immediately precedes the extract.

### LANG-B04-O · Open — Extract / evidence response
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts different supported interpretations or selections of evidence from the extract.
* **Example 1:** What does the extract reveal about the character? Support a defensible reading.
* **Example 2:** Select evidence for two possible interpretations and explain it.

### LANG-B05-S · Specific — Grammar / transformation / editing
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The instruction and context determine one bounded correction or required grammatical form.
* **Example 1:** Change the sentence into the specified passive form without altering meaning.
* **Example 2:** Correct the single contextual error in the sentence.

### LANG-B05-O · Open — Grammar / transformation / editing
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The instruction explicitly permits materially different valid transformations or revisions.
* **Example 1:** Combine the sentences in two different grammatically valid ways without changing meaning.
* **Example 2:** Revise the paragraph in two valid ways to improve cohesion and explain the choices.

### LANG-B06-S · Specific — Identification / list / definition
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The source identifies one target, or the task requires a complete bounded set or semantic definition.
* **Example 1:** Identify the speaker from the supplied extract.
* **Example 2:** Define the term using the required civic elements.

### LANG-B06-O · Open — Identification / list / definition
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts a valid subset, multiple qualifying examples, or different explanatory formulations.
* **Example 1:** Identify three valid features and explain their significance.
* **Example 2:** Define the idea through two different valid examples and relate them.

### LANG-B07-S · Specific — Data / map / chronology / numerical
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The source evidence supports one bounded reading, location, sequence, date, or calculated result.
* **Example 1:** Give the six-figure grid reference shown by the map.
* **Example 2:** Arrange the supplied events in chronological order.

### LANG-B07-O · Open — Data / map / chronology / numerical
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts different evidence-based interpretations, routes, models, or presentations.
* **Example 1:** Use the data to develop two defensible interpretations of the trend.
* **Example 2:** Propose and justify two valid routes using the map evidence.

### LANG-B08-S · Specific — Literary device / language effect
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The prompt and evidence establish one required device or bounded effect with specified evidence.
* **Example 1:** Identify the named device in the underlined phrase and state its required effect.
* **Example 2:** Match each quoted line to the specified device list.

### LANG-B08-O · Open — Literary device / language effect
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The passage supports multiple devices or effects and the rubric accepts evidence-backed alternatives.
* **Example 1:** Identify and defend two literary devices operating in the passage.
* **Example 2:** Explain two defensible effects of the repeated image using textual evidence.

### LANG-C01 · Specific — Verified objective response
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The API and critic verify one correct option or one closed response set with no overlap among alternatives.
* **Example 1:** Which option completes the sentence correctly?
* **Example 2:** Evaluate the assertion–reason pair using the supplied response code.

### LANG-C02 · Specific — Fixed source fact / completion
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The complete evidence establishes one bounded fact, date, quotation completion, label, or pairing.
* **Example 1:** Complete the quoted line from the supplied extract.
* **Example 2:** Name the event identified by the given date.

### LANG-C03 · Specific — Fixed spelling / label / sequence
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The task and source establish one bounded written form, label, or order; accepted orthographic variants are recorded where relevant.
* **Example 1:** Correct the misspelt word in the supplied sentence.
* **Example 2:** Label the indicated location using the source map.

## Mathematics & Physics

*Corrected API policy registry: answer restriction is decided from the complete item and scoring contract, never from command words or local matching.*

### MATH-B01-S · Specific — Variable / assumption
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** Only a fixed result is assessed, or the prompt prescribes variables, assumptions, or conventions; equivalent working remains acceptable.
* **Example 1:** Solve using the variables defined in the question and report the required value.
* **Example 2:** Use the stated sign convention to calculate the displacement.

### MATH-B01-O · Open — Variable / assumption
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric explicitly credits materially different valid variable choices, assumptions, or model constructions.
* **Example 1:** Model the age problem using a valid variable assignment of your choice and justify it.
* **Example 2:** Solve under two defensible assumptions and compare the resulting models.

### MATH-B02-S · Specific — Proof / specified-method derivation
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The prompt prescribes a starting relation, construction, convention, or required proof route.
* **Example 1:** Derive the lens relation using the stated sign convention and the supplied ray construction.
* **Example 2:** Prove the result using mathematical induction with the required base case.

### MATH-B02-O · Open — Proof / derivation
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts materially different valid proofs, constructions, or derivations.
* **Example 1:** Prove the identity using any valid method and justify each transformation.
* **Example 2:** Give two different valid proofs of the geometric statement.

### MATH-B03-S · Specific — Coordinate / reference choice
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The question fixes the origin, axes, reference point, or sign convention and assesses one bounded result.
* **Example 1:** Use the given origin and axes to determine the coordinates.
* **Example 2:** Calculate potential relative to the stated reference point.

### MATH-B03-O · Open — Coordinate / reference choice
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts materially different coordinate systems, reference points, or conventions and assesses their equivalence.
* **Example 1:** Choose a convenient coordinate system, solve, and justify the choice.
* **Example 2:** Solve with two valid reference choices and show why the physical result agrees.

### MATH-B04-S · Specific — Definition / formula
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task has a bounded semantic definition or relation; equivalent wording, notation, and algebraic forms remain acceptable.
* **Example 1:** State Ohm's law and identify the quantities in the relation.
* **Example 2:** Define acceleration using the required physical elements.

### MATH-B04-O · Open — Definition / representation
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The scoring contract explicitly accepts materially different explanatory representations, examples, or connected formulations.
* **Example 1:** Explain the concept using a graph and an algebraic representation, then relate them.
* **Example 2:** Define the transformation through two different valid examples and explain the invariant.

### MATH-B05-S · Specific — Word problem / method
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** Only a fixed final result is assessed or the method is prescribed; alternate unseen methods do not by themselves make the item Open.
* **Example 1:** Use substitution to solve the stated pair of equations.
* **Example 2:** Calculate the time using the given model and report the required value.

### MATH-B05-O · Open — Word problem / multiple method
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric explicitly assesses and accepts materially different solution strategies or model formulations.
* **Example 1:** Solve the problem by two valid methods and compare their efficiency.
* **Example 2:** Formulate a valid model of your choice, solve it, and justify the formulation.

### MATH-B06-S · Specific — Graph / data
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The graph or data supports one bounded read-off, relation, or calculated result.
* **Example 1:** Read the velocity at the specified time from the graph.
* **Example 2:** Calculate the gradient over the stated interval.

### MATH-B06-O · Open — Graph / data
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric accepts materially different valid models, representations, or evidence-based interpretations.
* **Example 1:** Represent the relation graphically and algebraically and explain their equivalence.
* **Example 2:** Develop two defensible models for the trend and compare them.

### MATH-B07-S · Specific — Numerical technique
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** Only one bounded result or a prescribed technique is assessed; equivalent arithmetic presentation remains acceptable.
* **Example 1:** Find the mean using the specified assumed-mean method.
* **Example 2:** Compute the value using the stated recurrence.

### MATH-B07-O · Open — Numerical technique
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The rubric explicitly accepts and assesses materially different valid computational techniques.
* **Example 1:** Find the mean using two valid methods and compare the working.
* **Example 2:** Evaluate the expression by two valid techniques and justify both.

### MATH-B08-S · Specific — Experimental / model reasoning
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The supplied model and conditions determine one bounded relation, inference, or parameter.
* **Example 1:** Determine the spring constant from the stated model and measurements.
* **Example 2:** Infer the resistance from the specified graph interval.

### MATH-B08-O · Open — Experimental / model reasoning
* **Policy family:** B — Context-dependent
* **Semantic decision rule:** The task assesses alternative valid models, assumptions, or experimental approaches.
* **Example 1:** Propose two valid models for the data and compare their limitations.
* **Example 2:** Design two valid measurement approaches and justify their error controls.

### MATH-C01 · Specific — Verified objective response
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The API and critic verify one correct option or one closed response set with no overlap among alternatives.
* **Example 1:** Which option is equivalent to the given expression?
* **Example 2:** Select the graph that represents the stated relation.

### MATH-C02 · Specific — Direct closed calculation
* **Policy family:** C — Closed-response
* **Semantic decision rule:** The task requests one bounded calculated value from supplied quantities; equivalent units and exact forms are recorded.
* **Example 1:** Calculate the area of a circle with the stated radius.
* **Example 2:** Calculate current from the supplied voltage and resistance.

---

## Supplementary calibration — accepted reference classifications

A second, independent source of ground truth: the real `answer_restriction`
verdicts in the three workbooks the reference school accepted
(`backend/data/Testing/reference_bulk_import/grade6_{english,mathematics,science}.xlsx`).
The authoritative policy is the v2.0 registry above; these accepted rows
simply corroborate it on real graded items. Same rules apply — recorded
evidence the model reasons over, never a lookup table, no code reads it.

### English

* **Specific** (Objective) — Choose the correct meaning of “buckle up” in the poem.
* **Specific** (Descriptive) — Write True or False: The mother lark decided to leave the nest when the farmer said that he himself would reap the corn the next day.
* **Specific** (Descriptive) — Do as directed: Fill in the correct article. _____ sun rises in the east.
* **Open** (Descriptive) — Answer in 2–3 sentences: What is the poem “The School Bell Rings Again...” about, and how does the poet describe each school term?
* **Open** (Descriptive) — Language Study: (a) Use “keep your eyes and ears open” in a meaningful sentence. (1) (b) Fill in the correct article: I saw _____ elephant walking thr…
* **Open** (Descriptive) — Write a short composition of about 60–80 words on: “A time when self-help helped me achieve a goal.”
* **Open** (Descriptive) — Imagine the farmer had decided to reap the corn himself from the beginning. Write a short alternative ending to the story.

### Mathematics

* **Specific** (Objective) — Choose the correct option: Which of the following shapes is not three-dimensional?
* **Specific** (Objective) — Choose the correct option: A complete angle contains ______ right angles.
* **Specific** (Descriptive) — Write the smallest whole number.
* **Specific** (Descriptive) — A solid has 5 faces, 5 vertices and 8 edges. One face is square and the remaining faces are triangular. (a) Name the solid. (b) How many triangular fa…
* **Specific** (Descriptive) — At exactly 3:00 p.m., find the smaller angle between the hour hand and minute hand of a clock. Write its measure and type.
* **Open** (Descriptive) — Write two differences between a line and a ray.
* **Open** (Descriptive) — A lift starts at ground floor 0. It goes down to floor -3, then rises 7 floors, and finally goes down 2 floors. (a) At which floor is it now? (1) (b) …
* **Open** (Descriptive) — A prism and a pyramid each have a base with 4 sides. Using the rules for prisms and pyramids, find the number of edges in each and explain why the two…

### Science

* **Specific** (Objective) — Choose the correct option: Which pair is described as the main characteristics of living organisms?
* **Specific** (Objective) — Choose the correct option: The SI unit of length is:
* **Open** (Descriptive) — Two students measure the same desk using their hand spans and obtain different answers. Why can this happen?
* **Open** (Descriptive) — Name the most suitable instrument for measuring: (a) the girth of a large tree trunk; (b) the thickness of an eraser.
* **Open** (Descriptive) — Differentiate between breathing and respiration in two points.
* **Open** (Descriptive) — A potted plant kept near a window bends towards sunlight. Identify the stimulus and the response, and name the characteristic of living organisms show…
* **Open** (Descriptive) — A laboratory thermometer has a difference of 10°C between two big marks and 10 equal small divisions between them. (a) What temperature does each smal…
* **Open** (Descriptive) — During a severe drought, animals have very little food and water. Explain three effects this may have on animals using the characteristics of living o…

---

*Provenance: `docs/open-specific-registry-v2.xlsx` is the authoritative
corrected Open/Specific API Policy Registry v2.0 (16 August 2026), supplied
by the owner. The transcription above is generated faithfully from that
workbook; the supplementary rows are read verbatim from the accepted
reference workbooks in `backend/data/Testing/reference_bulk_import/`. Nothing
here is invented.*
