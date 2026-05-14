import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BoardNode, ChapterDetail, ChapterRef, Scope } from "../types";

/**
 * Reusable Board > Grade > Subject > Unit > Chapter drill-down. Once a chapter
 * is opened, the user can scope to the whole chapter, to specific topics
 * (multi-select), or to specific concepts (multi-select) — matching the
 * "directory deposition" flow used by both modules.
 */
export default function DirectoryPicker({
  onScope,
  allowConceptScope = true,
  chapterOnly = false,
}: {
  onScope: (scope: Scope | null) => void;
  allowConceptScope?: boolean;
  chapterOnly?: boolean;
}) {
  const [tree, setTree] = useState<BoardNode[]>([]);
  const [board, setBoard] = useState("");
  const [grade, setGrade] = useState("");
  const [subject, setSubject] = useState("");
  const [unit, setUnit] = useState("");
  const [chapter, setChapter] = useState<ChapterRef | null>(null);
  const [detail, setDetail] = useState<ChapterDetail | null>(null);
  const [scopeType, setScopeType] = useState<"chapter" | "topic" | "concept">("chapter");
  const [picked, setPicked] = useState<number[]>([]);

  useEffect(() => {
    api.tree().then(setTree).catch(() => setTree([]));
  }, []);

  useEffect(() => {
    if (!chapter) {
      setDetail(null);
      return;
    }
    api.chapter(chapter.id).then(setDetail);
  }, [chapter]);

  // Emit the resolved scope upward whenever the selection changes.
  useEffect(() => {
    if (!chapter) {
      onScope(null);
      return;
    }
    if (scopeType === "chapter") {
      onScope({ type: "chapter", ids: [chapter.id], label: chapter.chapter_title });
    } else if (picked.length) {
      onScope({
        type: scopeType,
        ids: picked,
        label: `${picked.length} ${scopeType}(s) in ${chapter.chapter_title}`,
      });
    } else {
      onScope(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapter, scopeType, picked]);

  const boardNode = tree.find((b) => b.board === board);
  const gradeNode = boardNode?.grades.find((g) => g.grade === grade);
  const subjectNode = gradeNode?.subjects.find((s) => s.subject === subject);
  const unitNode = subjectNode?.units.find((u) => u.unit === unit);

  function reset(level: "board" | "grade" | "subject" | "unit") {
    if (level === "board") {
      setGrade(""); setSubject(""); setUnit(""); setChapter(null);
    } else if (level === "grade") {
      setSubject(""); setUnit(""); setChapter(null);
    } else if (level === "subject") {
      setUnit(""); setChapter(null);
    } else {
      setChapter(null);
    }
    setPicked([]);
  }

  function toggle(id: number) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  }

  return (
    <div className="dir-picker">
      <div className="row">
        <select value={board} onChange={(e) => { setBoard(e.target.value); reset("board"); }}>
          <option value="">Board…</option>
          {tree.map((b) => <option key={b.board}>{b.board}</option>)}
        </select>
        <select value={grade} disabled={!boardNode}
          onChange={(e) => { setGrade(e.target.value); reset("grade"); }}>
          <option value="">Class…</option>
          {boardNode?.grades.map((g) => <option key={g.grade}>{g.grade}</option>)}
        </select>
        <select value={subject} disabled={!gradeNode}
          onChange={(e) => { setSubject(e.target.value); reset("subject"); }}>
          <option value="">Subject…</option>
          {gradeNode?.subjects.map((s) => <option key={s.subject}>{s.subject}</option>)}
        </select>
        <select value={unit} disabled={!subjectNode}
          onChange={(e) => { setUnit(e.target.value); reset("unit"); }}>
          <option value="">Unit…</option>
          {subjectNode?.units.map((u) => <option key={u.unit}>{u.unit}</option>)}
        </select>
        <select value={chapter?.id ?? ""} disabled={!unitNode}
          onChange={(e) => {
            const id = Number(e.target.value);
            setChapter(unitNode?.chapters.find((c) => c.id === id) ?? null);
            setScopeType("chapter");
            setPicked([]);
          }}>
          <option value="">Chapter…</option>
          {unitNode?.chapters.map((c) => (
            <option key={c.id} value={c.id}>{c.chapter_title} ({c.concept_count} concepts)</option>
          ))}
        </select>
      </div>

      {chapter && !chapterOnly && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="row" style={{ marginBottom: 8 }}>
            <strong>Scope:</strong>
            {(["chapter", "topic", ...(allowConceptScope ? ["concept" as const] : [])] as const).map((t) => (
              <label key={t} className="radio">
                <input type="radio" checked={scopeType === t}
                  onChange={() => { setScopeType(t); setPicked([]); }} />
                {t === "chapter" ? "Whole chapter" : t === "topic" ? "Specific topics" : "Specific concepts"}
              </label>
            ))}
          </div>

          {scopeType === "topic" && (
            <div className="pick-list">
              {detail?.topics.map((t) => (
                <label key={t.id} className="pick-item">
                  <input type="checkbox" checked={picked.includes(t.id)} onChange={() => toggle(t.id)} />
                  <span>{t.topic_title}</span>
                  <span className="muted">{t.concepts.length} concepts · {t.pre_post_learning}</span>
                </label>
              ))}
            </div>
          )}
          {scopeType === "concept" && (
            <div className="pick-list">
              {detail?.topics.flatMap((t) =>
                t.concepts.map((c) => (
                  <label key={c.id} className="pick-item">
                    <input type="checkbox" checked={picked.includes(c.id)} onChange={() => toggle(c.id)} />
                    <span>{c.concept_title}</span>
                    <span className="muted">{t.topic_title}</span>
                  </label>
                )),
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
