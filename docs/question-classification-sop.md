# Question classification by response mechanism

Owner-supplied source: `SOP_ Classification of Questions.pdf`, received
9 September 2026 (three pages). Adopted by Q41. This amendment controls new
policy-bound work where an older prompt or example differs; historical sealed
work retains its recorded policy. The master contract remains verbatim.

The API author decides from the complete question, source context, response
instructions, answer choices and visual evidence. An independent critic reviews
the decision. Mechanical code validates the output vocabulary and schema; it
does not classify by verbs, marks, question length, final-answer length or
layout.

| Classification | Required response | Examples and boundaries |
|---|---|---|
| Objective | Select or identify an answer from two or more explicitly supplied answer choices. | MCQ, MSQ, assertion–reason with options, matching with supplied answer combinations, odd-one-out choices, image-based choices. A picture alone is not an answer choice. |
| Subjective | Supply a short, fixed response without independently constructing an explanation or solution. | A word, phrase, factual statement, term, symbol, date, name or value; fill in the blank; complete the sentence; **True/False** under this SOP. |
| Descriptive | Construct an explanation, reasoning, calculation, description, analysis, application, drawing, mapping or extended answer. | Explain/justify/compare; calculations requiring working even when the final answer is only a number; draw and label; locate, mark or label on a map; prove/derive; written case responses. |

The owner refinement of 11 September 2026 requires the API to identify the
**complete learner response required for full credit first**, then apply the
SOP's distinctions. A short final answer, unique answer key or worksheet blank
does not establish a short factual response. Keep calculation, interpretation,
reasoning, explanation, construction and other required work Descriptive.
Do not add demands solely because an evaluator's explanation includes them.

| Source demand | Classification evidence |
|---|---|
| Calculate the mean from supplied observations or frequencies. | The learner derives the result from data: Descriptive even when the answer is one fixed number. Preserve the complete table. |
| Read the stated frequency for an identified value from a table. | A direct factual lookup can be Subjective when no interpretation or constructed solution is required. |
| Select the correct mean from supplied answer options. | Objective if selecting the option is the complete required response; mental working alone does not add a submitted-response requirement. |
| Select a conclusion and justify it using the data. | Preserve the required constructed justification and every dependent response in the integrated task. A selection component cannot erase the Descriptive demand. |
| Write a fixed term, or give a bare True/False response. | Subjective under the SOP. An independently required explanation remains a separate constructed demand. |

Table values, interval labels, observations, diagrams, premises and examples
are not answer choices merely because they appear as a list. “State the reason”
may request a fixed factual entry or independently constructed reasoning; the
API reads the complete evidence rather than matching those words. Choose the
lane before selecting a compatible approved category and marks contract.
Category labels, marks, Bloom levels and Specific/Open answer restrictions do
not determine question type.

For a mixed multipart task, preserve the shared context and all dependent
children. Determine each child's response mechanism under the existing
multipart output contract; do not force every child into its parent's lane or
discard a child to make the classification simpler.

Question identity is a separate decision. “1. Answer the following questions:
(a) … (b) …” is an administrative wrapper when the two tasks stand alone; each
gets its own question identity. A source passage, scenario, diagram, table or
other meaningful necessary context can support a single question with dependent
subquestions. Neither lettering nor common print position proves dependence.

Activity and info-hub questions retain enough of the supplied activity, data,
passage or visual to stand alone when polished. Extract an actual source-set
request; do not manufacture Post questions from informational prose. Keep the
original wording, source occurrence, accepted polished wording and any missing
dependency finding.

New work carries `generation_quality_policy =
owner-generation-quality-2026-09-11-v1`. Source and generated cell authors,
independent critics, and the Fixer's original payload receive the strengthened
response-demand contract. Generated cell decisions include the complete carried
question evidence, including structured tables, options and children. The
cell's rationale identifies the required response, its evidence and why an
alternative lane does not fit. The decision identity carries the policy suffix.
Unstamped sealed work retains its earlier prompt, evidence shape and identity.

Materialization must preserve the accepted demand: never invent options, insert
answer blanks, discard working or rewrite a task simply to force the recorded
lane. Record a genuine lane/format incompatibility in rationale or review
evidence. Classification remains an API decision with independent advisory
review; this refinement adds no local semantic classifier or automatic lane
substitution.
