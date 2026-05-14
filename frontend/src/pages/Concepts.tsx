import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks";

export default function Concepts() {
  const [preLearning, setPreLearning] = useState("");
  const [chapter, setChapter] = useState("");

  const chapters = useAsync(() => api.chapters(), []);
  const concepts = useAsync(() => {
    const params: Record<string, string> = {};
    if (preLearning) params.pre_learning = preLearning;
    if (chapter) params.chapter_code = chapter;
    return api.concepts(params);
  }, [preLearning, chapter]);

  return (
    <>
      <h1>Concepts</h1>
      <div className="subtitle">
        Canonical concept rows (Board · Grade · Subject · Chapter · Topic · Concept) produced by the
        MMD→Concepts and Pre-Learning stages.
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row">
          <label>Chapter</label>
          <select value={chapter} onChange={(e) => setChapter(e.target.value)}>
            <option value="">All chapters</option>
            {chapters.data?.map((c) => (
              <option key={c.chapter_code} value={c.chapter_code}>
                {c.chapter_code} — {c.chapter_title}
              </option>
            ))}
          </select>
          <label>Kind</label>
          <select value={preLearning} onChange={(e) => setPreLearning(e.target.value)}>
            <option value="">All</option>
            <option value="false">Native</option>
            <option value="true">Pre-Learning</option>
          </select>
          <div className="spacer" />
          <span className="muted">{concepts.data?.length ?? 0} rows</span>
        </div>
      </div>

      {concepts.error && <div className="error-box">{concepts.error}</div>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Concept ID</th>
              <th>Subject</th>
              <th>Chapter</th>
              <th>Topic</th>
              <th>Concept</th>
              <th>Description</th>
              <th>Kind</th>
            </tr>
          </thead>
          <tbody>
            {concepts.data?.map((c) => (
              <tr key={c.id}>
                <td className="mono">{c.concept_id}</td>
                <td>{c.subject}</td>
                <td>{c.chapter_title}</td>
                <td>{c.topic}</td>
                <td>{c.concept}</td>
                <td className="muted">{c.concept_description}</td>
                <td>
                  <span className={`badge ${c.is_pre_learning ? "accent" : ""}`}>
                    {c.is_pre_learning ? "pre-learning" : "native"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {concepts.data?.length === 0 && <div className="empty">No concepts match.</div>}
      </div>
    </>
  );
}
