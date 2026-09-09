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

The SOP's normal sequence is: explicit answer selection → Objective; otherwise
a short fixed entry → Subjective; otherwise a constructed response →
Descriptive. Apply its explicit True/False exception. A request to calculate
cannot become Subjective merely because its final answer is numeric. A prompt
to “state the reason” can be Subjective for a fixed factual entry or Descriptive
when reasoning is required. “Choose the correct reason” with supplied answer
options is Objective. These are examples of judging the actual response, not
keyword rules.

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
