import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ManualConceptBody, ManualQuestionBody } from "../api/client";
import { useAsync } from "../hooks";
import DirectoryPicker from "../components/DirectoryPicker";
import type { Scope, Vocab } from "../types";

type Tab = "concept" | "question";

const SUBJECT_SUGGESTIONS = [
  "Mathematics", "Physics", "Chemistry", "Biology",
  "English Grammar", "English Literature",
];

export default function ManualEntry() {
  const [tab, setTab] = useState<Tab>("concept");
  const vocab = useAsync(() => api.vocab(), []);

  return (
    <>
      <h1>Create</h1>
      <div className="subtitle">
        Type a concept or a question directly. Everything you create here is
        also appended to the Bulk Import output workbook (download it from the
        Database tab).
      </div>

      <div className="card row" style={{ marginBottom: 16 }}>
        <button className={tab === "concept" ? "" : "ghost"} onClick={() => setTab("concept")}>
          Create concept
        </button>
        <button className={tab === "question" ? "" : "ghost"} onClick={() => setTab("question")}>
          Create question
        </button>
        <div className="spacer" />
        <span className="muted">
          Tip: wrap equations as <span className="mono">[katex] ... [/katex]</span>; links as <span className="mono">[Text](https://...)</span>.
        </span>
      </div>

      {vocab.data && tab === "concept" && <ConceptForm vocab={vocab.data} />}
      {vocab.data && tab === "question" && <QuestionForm vocab={vocab.data} />}
    </>
  );
}

/* --------------------------------------------------------------------------- */
/* Concept form                                                                */
/* --------------------------------------------------------------------------- */

function ConceptForm({ vocab }: { vocab: Vocab }) {
  const [body, setBody] = useState<ManualConceptBody>({
    board: vocab.boards[0] ?? "CBSE",
    grade: vocab.grades[0] ?? "10",
    subject: SUBJECT_SUGGESTIONS[0],
    chapter_title: "",
    topic_title: "",
    concept_title: "",
    summary: "",
    formula: "",
    keywords: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ id: number; concept_title: string } | null>(null);

  function set<K extends keyof ManualConceptBody>(key: K, value: ManualConceptBody[K]) {
    setBody((b) => ({ ...b, [key]: value }));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.manualCreateConcept({
        ...body,
        formula: body.formula?.trim() ? body.formula : undefined,
      });
      setResult(r);
      setBody((b) => ({ ...b, concept_title: "", summary: "", formula: "", keywords: "" }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const required = body.chapter_title.trim() && body.concept_title.trim();

  return (
    <div className="card">
      <div className="grid cols-2">
        <Field label="Board">
          <select value={body.board} onChange={(e) => set("board", e.target.value)}>
            {vocab.boards.map((b) => <option key={b}>{b}</option>)}
          </select>
        </Field>
        <Field label="Class / Grade">
          <select value={body.grade} onChange={(e) => set("grade", e.target.value)}>
            {vocab.grades.map((g) => <option key={g}>{g}</option>)}
          </select>
        </Field>
        <Field label="Subject">
          <input list="subject-suggestions" value={body.subject}
            onChange={(e) => set("subject", e.target.value)} />
          <datalist id="subject-suggestions">
            {SUBJECT_SUGGESTIONS.map((s) => <option key={s} value={s} />)}
          </datalist>
        </Field>
        <Field label="Chapter title (new or existing)">
          <input value={body.chapter_title} onChange={(e) => set("chapter_title", e.target.value)}
            placeholder="e.g. Trigonometry" />
        </Field>
        <Field label="Topic title (within the chapter)">
          <input value={body.topic_title} onChange={(e) => set("topic_title", e.target.value)}
            placeholder="e.g. Trig Identities" />
        </Field>
        <Field label="Concept title">
          <input value={body.concept_title} onChange={(e) => set("concept_title", e.target.value)}
            placeholder="e.g. Pythagorean Identity" />
        </Field>
      </div>
      <Field label="Summary">
        <textarea rows={3} value={body.summary} onChange={(e) => set("summary", e.target.value)}
          placeholder="A short description used as the concept_details body." />
      </Field>
      <div className="grid cols-2">
        <Field label="Key formula (LaTeX, optional — auto-wrapped in [katex])">
          <input value={body.formula ?? ""} onChange={(e) => set("formula", e.target.value)}
            placeholder="\\sin^2 \\theta + \\cos^2 \\theta = 1" />
        </Field>
        <Field label="Keywords (comma separated)">
          <input value={body.keywords} onChange={(e) => set("keywords", e.target.value)}
            placeholder="sin, cos, identity" />
        </Field>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <div className="spacer" />
        <button disabled={!required || busy} onClick={submit}>
          {busy ? "Saving…" : "Create concept"}
        </button>
      </div>
      {error && <div className="error-box" style={{ marginTop: 12 }}>{error}</div>}
      {result && (
        <div className="card success-card" style={{ marginTop: 12 }}>
          <strong>Concept created · id {result.id}</strong> — {result.concept_title}.
          <div className="muted" style={{ marginTop: 4 }}>
            Three groups (Basic / Intermediate / Advanced) were auto-attached so you can add questions to it.
          </div>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Question form                                                               */
/* --------------------------------------------------------------------------- */

interface AnswerRow {
  answer_content: string;
  answer?: string;
  correct_answer?: string;
  answer_weightage?: string;
  weightage?: string;
  answer_display?: string;
  placeholder?: string;
  answer_type: string;
}

interface SubQ {
  text: string;
  marks: string;
  keywords: { answer_type: string; weightage: string; keyword: string }[];
}

function emptyAnswer(kind: string): AnswerRow {
  if (kind === "objective") {
    return { answer_type: "Words", answer_content: "", correct_answer: "No", answer_weightage: "0" };
  }
  if (kind === "subjective") {
    return { answer_type: "Words", answer_content: "", answer: "", answer_display: "Yes",
             weightage: "1", placeholder: "answer" };
  }
  return { answer_type: "Words", answer_content: "", answer_weightage: "1" };
}

function QuestionForm({ vocab }: { vocab: Vocab }) {
  const [scope, setScope] = useState<Scope | null>(null);
  const [sheet, setSheet] = useState("objective");
  const [category, setCategory] = useState(vocab.question_categories["objective"][0]);
  const [cog, setCog] = useState(vocab.cognitive_skills[1] ?? "Understanding");
  const [diff, setDiff] = useState(vocab.difficulty_levels[1] ?? "Moderate");
  const [marks, setMarks] = useState(1);
  const [question, setQuestion] = useState("");
  const [explanation, setExplanation] = useState("");
  const [answers, setAnswers] = useState<AnswerRow[]>([
    emptyAnswer("objective"), emptyAnswer("objective"),
  ]);
  const [subQs, setSubQs] = useState<SubQ[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ id: number; question_label: string } | null>(null);

  // When the sheet kind changes, reset categories + answer rows so they match the new shape.
  useEffect(() => {
    const opts = vocab.question_categories[sheet] ?? [];
    if (opts.length && !opts.includes(category)) setCategory(opts[0]);
    setAnswers([emptyAnswer(sheet), ...(sheet === "objective" ? [emptyAnswer(sheet)] : [])]);
    if (sheet !== "descriptive") setSubQs([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sheet]);

  function setAns<K extends keyof AnswerRow>(i: number, key: K, value: AnswerRow[K]) {
    setAnswers((rows) => rows.map((r, idx) => (idx === i ? { ...r, [key]: value } : r)));
  }

  function addAns() { setAnswers((rows) => [...rows, emptyAnswer(sheet)]); }
  function rmAns(i: number) { setAnswers((rows) => rows.filter((_, idx) => idx !== i)); }

  function addSubQ() {
    setSubQs((s) => [...s, {
      text: "", marks: "2",
      keywords: [{ answer_type: "Words", weightage: "2", keyword: "" }],
    }]);
  }
  function rmSubQ(i: number) { setSubQs((s) => s.filter((_, idx) => idx !== i)); }

  async function submit() {
    if (!scope || scope.type !== "concept" || scope.ids.length === 0) {
      setError("Pick exactly one concept from the directory.");
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const body: ManualQuestionBody = {
        concept_id: scope.ids[0],
        sheet_kind: sheet,
        category,
        cognitive_skills: cog,
        difficulty: diff,
        marks,
        question,
        answer_explanation: explanation,
        answers: answers.map((a) => ({ ...a })) as Record<string, string>[],
        sub_questions: subQs as unknown as Record<string, unknown>[],
      };
      const r = await api.manualCreateQuestion(body);
      setResult({ id: r.id, question_label: r.question_label });
      setQuestion("");
      setExplanation("");
      setAnswers([emptyAnswer(sheet), ...(sheet === "objective" ? [emptyAnswer(sheet)] : [])]);
      setSubQs([]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const cats = vocab.question_categories[sheet] ?? [];
  const ready = !!scope && scope.type === "concept" && scope.ids.length === 1 && question.trim();

  return (
    <>
      <div className="section-title">1 · Pick the concept this question belongs to</div>
      <div className="card">
        <DirectoryPicker onScope={setScope} allowConceptScope />
        <div className="muted" style={{ marginTop: 8 }}>
          {scope?.type === "concept" && scope.ids.length === 1
            ? `Concept selected: ${scope.label}`
            : "Drill down to a chapter, then choose 'Specific concepts' and tick one."}
        </div>
      </div>

      <div className="section-title">2 · Question fields</div>
      <div className="card">
        <div className="grid cols-2">
          <Field label="Sheet kind">
            <select value={sheet} onChange={(e) => setSheet(e.target.value)}>
              {vocab.question_types.map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Category">
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {cats.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Cognitive skill">
            <select value={cog} onChange={(e) => setCog(e.target.value)}>
              {vocab.cognitive_skills.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Difficulty">
            <select value={diff} onChange={(e) => setDiff(e.target.value)}>
              {vocab.difficulty_levels.map((d) => <option key={d}>{d}</option>)}
            </select>
          </Field>
          <Field label="Marks">
            <input type="number" min={1} max={20} value={marks}
              onChange={(e) => setMarks(Math.max(1, Number(e.target.value)))} />
          </Field>
        </div>
        <Field label="Question (rich-text: [katex] eq [/katex], [Text](url))">
          <textarea rows={3} value={question} onChange={(e) => setQuestion(e.target.value)}
            placeholder="What is [katex] \sin^2 \theta + \cos^2 \theta [/katex]?" />
        </Field>
        <Field label="Answer explanation (optional)">
          <textarea rows={2} value={explanation} onChange={(e) => setExplanation(e.target.value)} />
        </Field>
      </div>

      <div className="section-title">3 · Answers</div>
      <div className="card">
        {answers.map((a, i) => (
          <div key={i} className="row" style={{ marginBottom: 8, alignItems: "flex-start" }}>
            <span className="muted" style={{ minWidth: 40 }}>#{i + 1}</span>
            {sheet === "subjective" ? (
              <input style={{ flex: 1 }} value={a.answer ?? ""}
                onChange={(e) => setAns(i, "answer", e.target.value)}
                placeholder="expected answer text" />
            ) : (
              <input style={{ flex: 1 }} value={a.answer_content}
                onChange={(e) => setAns(i, "answer_content", e.target.value)}
                placeholder={sheet === "descriptive" ? "model answer / rubric" : "option text"} />
            )}
            {sheet === "objective" && (
              <>
                <select value={a.correct_answer} onChange={(e) => setAns(i, "correct_answer", e.target.value)}>
                  <option value="Yes">Correct</option>
                  <option value="No">Distractor</option>
                </select>
                <input type="number" min={0} max={10} style={{ width: 64 }}
                  value={a.answer_weightage} onChange={(e) => setAns(i, "answer_weightage", e.target.value)} />
              </>
            )}
            {(sheet === "subjective" || sheet === "descriptive") && (
              <input type="number" min={0} max={10} style={{ width: 64 }}
                value={sheet === "subjective" ? a.weightage : a.answer_weightage}
                onChange={(e) => setAns(i, sheet === "subjective" ? "weightage" : "answer_weightage", e.target.value)} />
            )}
            <button className="ghost" onClick={() => rmAns(i)}>✕</button>
          </div>
        ))}
        <button className="ghost" onClick={addAns}>+ Add row</button>
      </div>

      {sheet === "descriptive" && (
        <>
          <div className="section-title">4 · Sub-questions (descriptive)</div>
          <div className="card">
            {subQs.length === 0 && <div className="muted">No sub-questions added yet.</div>}
            {subQs.map((sq, i) => (
              <div key={i} style={{ marginBottom: 12, paddingBottom: 12, borderBottom: "1px solid var(--border, #ddd)" }}>
                <div className="row">
                  <input style={{ flex: 1 }} value={sq.text}
                    placeholder="i. Define …"
                    onChange={(e) => setSubQs((s) => s.map((q, idx) => idx === i ? { ...q, text: e.target.value } : q))} />
                  <input type="number" min={1} max={10} style={{ width: 64 }}
                    value={sq.marks}
                    onChange={(e) => setSubQs((s) => s.map((q, idx) => idx === i ? { ...q, marks: e.target.value } : q))} />
                  <button className="ghost" onClick={() => rmSubQ(i)}>✕</button>
                </div>
                <div className="row" style={{ marginTop: 6 }}>
                  <span className="muted" style={{ minWidth: 80 }}>Keyword</span>
                  <input style={{ flex: 1 }} placeholder="raw KaTeX or text (no [katex] wrapper)"
                    value={sq.keywords[0]?.keyword ?? ""}
                    onChange={(e) => setSubQs((s) => s.map((q, idx) => idx === i
                      ? { ...q, keywords: [{ ...q.keywords[0], keyword: e.target.value }] }
                      : q))} />
                </div>
              </div>
            ))}
            <button className="ghost" onClick={addSubQ}>+ Add sub-question</button>
          </div>
        </>
      )}

      <div className="row" style={{ marginTop: 16 }}>
        <div className="spacer" />
        <button disabled={!ready || busy} onClick={submit}>
          {busy ? "Saving…" : "Create question"}
        </button>
      </div>
      {error && <div className="error-box" style={{ marginTop: 12 }}>{error}</div>}
      {result && (
        <div className="card success-card" style={{ marginTop: 12 }}>
          <strong>Question created · id {result.id}</strong>
          <div className="mono" style={{ marginTop: 4 }}>{result.question_label}</div>
          <div className="muted" style={{ marginTop: 4 }}>
            Row was also appended to the Bulk Import output workbook (download from the Database tab).
          </div>
        </div>
      )}
    </>
  );
}

/* --------------------------------------------------------------------------- */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <div className="field-label">{label}</div>
      {children}
    </div>
  );
}
