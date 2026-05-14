import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";

const SHEETS = ["objective", "subjective", "descriptive"];

export default function Questions() {
  const [sheet, setSheet] = useState("objective");
  const [difficulty, setDifficulty] = useState("");

  const questions = useAsync(() => {
    const params: Record<string, string> = { sheet_kind: sheet };
    if (difficulty) params.difficulty = difficulty;
    return api.questions(params);
  }, [sheet, difficulty]);

  return (
    <>
      <h1>Questions</h1>
      <div className="subtitle">
        Bulk-upload question bank — Objective / Subjective / Descriptive sheets, mirroring the
        existing 3-sheet export schema.
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row">
          {SHEETS.map((s) => (
            <button
              key={s}
              className={sheet === s ? "" : "ghost"}
              onClick={() => setSheet(s)}
            >
              {s}
            </button>
          ))}
          <label style={{ marginLeft: 12 }}>Difficulty</label>
          <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
            <option value="">All</option>
            <option value="Less">Less</option>
            <option value="Moderate">Moderate</option>
            <option value="High">High</option>
          </select>
          <div className="spacer" />
          <a href={api.exportUrl()}>
            <button className="ghost">Download bulk-upload .xlsx</button>
          </a>
        </div>
      </div>

      {questions.error && <div className="error-box">{questions.error}</div>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Label</th>
              <th>Category</th>
              <th>Cognitive</th>
              <th>Difficulty</th>
              <th>Marks</th>
              <th>Question</th>
              <th>Answer / Rubric</th>
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
                <td>{q.question}</td>
                <td className="muted">
                  {q.answers.map((a, i) => (
                    <div key={i}>
                      {a.correct_answer ? "✓ " : ""}
                      {a.answer_content} <span className="mono">({a.answer_weightage})</span>
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {questions.data?.length === 0 && <div className="empty">No questions in this sheet.</div>}
      </div>
    </>
  );
}
