import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";
import SyllabusUploader from "../components/SyllabusUploader";
import type { BoardNode } from "../types";

const SHEETS = ["objective", "subjective", "descriptive"];
const ADMIN_TOKEN_KEY = "aegis_admin_token";

export default function Database() {
  const [sheet, setSheet] = useState("objective");
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resetMsg, setResetMsg] = useState<string | null>(null);

  const [syllabusMsg, setSyllabusMsg] = useState<string | null>(null);

  const stats = useAsync(() => api.stats(), []);
  const questions = useAsync(() => api.questions({ sheet_kind: sheet, limit: "100" }), [sheet]);

  async function importWorkbook(file: File) {
    setBusy(true);
    setImportMsg(null);
    try {
      const counts = await api.importWorkbook(file);
      setImportMsg(`Imported: ${JSON.stringify(counts)}`);
      stats.reload();
      questions.reload();
    } catch (e) {
      setImportMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function clearAll() {
    if (!window.confirm(
      "Clear everything? This removes all chapters, concepts, questions, uploads, "
      + "output workbook, and generated PDFs. This cannot be undone.",
    )) return;
    let token = "";
    try {
      token = window.sessionStorage.getItem(ADMIN_TOKEN_KEY) || "";
    } catch {
      // Treat blocked browser storage as an unauthenticated admin session.
    }
    if (!token) {
      setResetMsg(
        "Admin authentication required. Sign in on the Admin page, then "
        + "return here to clear shared data.",
      );
      return;
    }
    setBusy(true);
    setResetMsg(null);
    try {
      const result = await api.resetData(token);
      setResetMsg(`Cleared: ${JSON.stringify(result)}`);
      stats.reload();
      questions.reload();
    } catch (e) {
      setResetMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Database</h1>
      <div className="subtitle">
        The Bulk Import workbook is the source of truth. Import a workbook to load
        it, or export the canonical 3-sheet format.
      </div>

      <div className="card">
        <div className="row">
          <a className="button-link ghost" href={api.exportUrl("all")}>
            Export full database (.xlsx)
          </a>
          <a className="button-link ghost" href={api.exportUrl("output")}>
            Export append-only output (.xlsx)
          </a>
          <div className="spacer" />
          <label className="upload-label">
            Import Bulk Import workbook
            <input
              type="file"
              accept=".xlsx"
              disabled={busy}
              style={{ display: "none" }}
              onChange={(e) => e.target.files?.[0] && importWorkbook(e.target.files[0])}
            />
          </label>
          <button className="danger" disabled={busy} onClick={clearAll}>
            Clear all data
          </button>
        </div>
        {importMsg && <div className="muted mono mt-8">{importMsg}</div>}
        {resetMsg && <div className="muted mono mt-8">{resetMsg}</div>}
      </div>

      <div className="section-title">Syllabus structure (units & chapters)</div>
      <SyllabusUploader
        disabled={busy}
        onLoaded={() => {
          stats.reload();
          setSyllabusMsg("Syllabus loaded — use Build Concepts to deposit into a chapter.");
        }}
      />
      {syllabusMsg && <div className="muted mono mt-8">{syllabusMsg}</div>}

      {stats.data && (
        <div className="grid cols-4 mt-16">
          <Stat label="Chapters" value={stats.data.chapters} />
          <Stat label="Topics" value={stats.data.topics} />
          <Stat label="Concepts" value={stats.data.concepts} />
          <Stat label="Groups" value={stats.data.groups} />
        </div>
      )}

      <CreateWorkbook />

      <div className="section-title">Questions ({sheet})</div>
      <div className="card mb-12">
        <div className="row">
          {SHEETS.map((s) => (
            <button key={s} className={sheet === s ? "" : "ghost"} onClick={() => setSheet(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>

      {questions.error && <div className="error-box mb-12">{questions.error}</div>}
      <div className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Label</th>
                <th scope="col">Category</th>
                <th scope="col">Cognitive</th>
                <th scope="col">Difficulty</th>
                <th scope="col">Marks</th>
                <th scope="col">Question</th>
                <th scope="col">Origin</th>
              </tr>
            </thead>
            <tbody>
              {questions.data?.map((q) => (
                <tr key={q.id}>
                  <td className="mono">{q.question_label}</td>
                  <td>{q.question_category}</td>
                  <td><span className="badge">{q.cognitive_skills}</span></td>
                  <td>{q.level_of_difficulty}</td>
                  <td>{q.marks}</td>
                  <td>{q.question.slice(0, 140)}</td>
                  <td><span className="badge accent">{q.origin}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {questions.data?.length === 0 && <div className="empty">No questions in this sheet yet.</div>}
      </div>
    </>
  );
}

function CreateWorkbook() {
  const tree = useAsync(() => api.tree(), []);
  const [board, setBoard] = useState("");
  const [grade, setGrade] = useState("");
  const [subject, setSubject] = useState("");
  const [mode, setMode] = useState<"blank" | "content">("content");

  // Subjects available under the chosen board/grade (or all, when unfiltered).
  const { boards, grades, subjects } = useMemo(() => {
    const t: BoardNode[] = tree.data ?? [];
    const boards = t.map((b) => b.board);
    const gradeSet = new Set<string>();
    const subjectSet = new Set<string>();
    for (const b of t) {
      if (board && b.board !== board) continue;
      for (const g of b.grades) {
        gradeSet.add(g.grade);
        if (grade && g.grade !== grade) continue;
        for (const s of g.subjects) subjectSet.add(s.subject);
      }
    }
    return { boards, grades: [...gradeSet].sort(), subjects: [...subjectSet].sort() };
  }, [tree.data, board, grade]);

  return (
    <>
      <div className="section-title">Create Bulk Import Workbook (subject-wise)</div>
      <div className="card">
        <div className="muted mb-12">
          Generate a canonical 3-sheet <strong>Bulk Import Excel workbook</strong> for
          one subject — a blank authoring template, or pre-filled with the subject's
          existing content. Headers always match the canonical format exactly.
          (For student revision-workbook PDFs, use the Create Workbooks tab.)
        </div>
        <div className="grid cols-3">
          <div className="field">
            <label className="field-label" htmlFor="cw-board">Board</label>
            <div className="row">
              <select
                id="cw-board"
                value={board}
                onChange={(e) => { setBoard(e.target.value); setSubject(""); }}
              >
                <option value="">All boards</option>
                {boards.map((b) => <option key={b}>{b}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cw-grade">Grade</label>
            <div className="row">
              <select
                id="cw-grade"
                value={grade}
                onChange={(e) => { setGrade(e.target.value); setSubject(""); }}
              >
                <option value="">All grades</option>
                {grades.map((g) => <option key={g}>{g}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label className="field-label" htmlFor="cw-subject">Subject</label>
            <div className="row">
              <select
                id="cw-subject"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
              >
                <option value="">Subject…</option>
                {subjects.map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
          </div>
        </div>
        <div className="row">
          <label className="radio">
            <input type="radio" checked={mode === "content"} onChange={() => setMode("content")} />
            With existing content
          </label>
          <label className="radio">
            <input type="radio" checked={mode === "blank"} onChange={() => setMode("blank")} />
            Blank template
          </label>
          <div className="spacer" />
          {subject ? (
            <a className="button-link" href={api.createWorkbookUrl(subject, board, grade, mode)}>
              Create workbook (.xlsx)
            </a>
          ) : (
            <button disabled>Create workbook (.xlsx)</button>
          )}
        </div>
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
