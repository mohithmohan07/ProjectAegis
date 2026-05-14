import { useState } from "react";
import { api } from "../api/client";
import type { TagSuggestion } from "../types";

export default function Tagging() {
  const [text, setText] = useState(
    "Explain Newton's third law of motion with a real-world example.",
  );
  const [result, setResult] = useState<TagSuggestion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function suggest() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.suggestTag(text));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Assessment Tagging</h1>
      <div className="subtitle">
        Route a question to its best-matching concept and infer cognitive skill / difficulty.
        This MVP uses a token-overlap heuristic; the live path swaps in the GPT routing from
        the Apps Script <span className="mono">SmartWorkflow</span>.
      </div>

      <div className="card">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          style={{ width: "100%" }}
        />
        <div className="row" style={{ marginTop: 10 }}>
          <button disabled={busy} onClick={suggest}>
            {busy ? "Routing…" : "Suggest tags"}
          </button>
        </div>
      </div>

      {error && <div className="error-box" style={{ marginTop: 16 }}>{error}</div>}

      {result && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="section-title" style={{ marginTop: 0 }}>Routing decision</div>
          <table>
            <tbody>
              <tr>
                <th>Concept</th>
                <td>{result.concept_path || <span className="muted">no confident match</span>}</td>
              </tr>
              <tr>
                <th>Concept ID</th>
                <td className="mono">{result.concept_id ?? "—"}</td>
              </tr>
              <tr>
                <th>Cognitive Skill</th>
                <td><span className="badge accent">{result.cognitive_skills}</span></td>
              </tr>
              <tr>
                <th>Difficulty</th>
                <td><span className="badge">{result.level_of_difficulty}</span></td>
              </tr>
              <tr>
                <th>Confidence</th>
                <td>
                  <div className="progress-track" style={{ width: 200 }}>
                    <div className="progress-fill" style={{ width: `${result.confidence * 100}%` }} />
                  </div>
                  <span className="muted mono">{result.confidence}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
