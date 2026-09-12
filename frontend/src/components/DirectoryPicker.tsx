import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api/client";
import { displayLabel } from "../lib/displayLabels";
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
  reloadSignal = 0,
  initialChapterIdentity,
}: {
  onScope: (scope: Scope | null) => void;
  allowConceptScope?: boolean;
  chapterOnly?: boolean;
  /** Increment to refetch the directory tree (e.g. after syllabus upload). */
  reloadSignal?: number;
  /** Stable checkpoint destination to select after the tree has loaded. */
  initialChapterIdentity?: Record<string, string>;
}) {
  const [tree, setTree] = useState<BoardNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [board, setBoard] = useState("");
  const [grade, setGrade] = useState("");
  const [subject, setSubject] = useState("");
  const [unit, setUnit] = useState("");
  const [chapter, setChapter] = useState<ChapterRef | null>(null);
  const [detail, setDetail] = useState<ChapterDetail | null>(null);
  const [scopeType, setScopeType] = useState<"chapter" | "topic" | "concept">("chapter");
  const [picked, setPicked] = useState<number[]>([]);
  const [initialSelectionMessage, setInitialSelectionMessage] = useState("");
  // Carried, not re-derived from the message text. The message is now written
  // by the server and is built from the saved chapter's own title, so a
  // chapter called "Saved checkpoint ..." would otherwise render its refusal
  // inside the success box.
  const [initialSelectionTone, setInitialSelectionTone] =
    useState<"ok" | "error">("error");
  const appliedInitialIdentityRef = useRef("");
  const idBase = useId();

  useEffect(() => {
    setLoading(true);
    setLoadError(null);
    api.tree()
      .then(setTree)
      .catch((e) => {
        setTree([]);
        setLoadError(String(e));
      })
      .finally(() => setLoading(false));
  }, [reloadSignal]);

  /*
     Selecting the saved checkpoint destination is a SERVER lookup, not a
     string walk over the tree labels. A checkpoint stores the chapter's raw
     subject — `History` for a CBSE social-science chapter — while the
     directory deliberately re-groups CBSE History/Geography/Civics/Economics
     under `Social Science` so the dropdowns line up with the chapter codes.
     Comparing the two as plain strings failed at the subject level, and
     because the walk was ordered, unit and chapter were never reached: all
     five dropdowns came up empty and the reviewer was told only that the
     destination "is not in the current directory".

     `/directory/resolve-chapter` folds both sides through the same closed
     subject table the tree uses and names the level that actually failed.
     Nothing here rewrites what the checkpoint stored: the resume comparison
     still matches the identity it recorded.
  */
  useEffect(() => {
    if (loading || !initialChapterIdentity) return;
    const identityKey = JSON.stringify(initialChapterIdentity);
    const applicationKey = `${reloadSignal}:${identityKey}`;
    if (appliedInitialIdentityRef.current === applicationKey) return;
    appliedInitialIdentityRef.current = applicationKey;
    // A newer identity (or a reloaded tree) moves the ref on; an answer that
    // arrives after that is stale and must not overwrite the newer selection.
    const superseded = () => appliedInitialIdentityRef.current !== applicationKey;

    api.resolveSavedChapter(initialChapterIdentity)
      .then((resolution) => {
        if (superseded()) return;
        if (!resolution.resolved || !resolution.chapter) {
          // The server's sentence names the level that failed — a moved
          // chapter, a renamed unit and one that was never imported have
          // different remedies. Show it as written.
          setInitialSelectionTone("error");
          setInitialSelectionMessage(
            resolution.reason
            || "The saved destination did not resolve to a chapter. Select "
              + "the matching chapter manually; generation will remain "
              + "disabled until then.",
          );
          return;
        }
        // Prefer the tree's own chapter object (and the labels the dropdowns
        // actually carry) over the resolved payload, so every select holds a
        // real option. The payload is the fallback for a tree that failed to
        // load, which keeps the resolved id rather than blanking the picker.
        const located = locateChapterInTree(tree, resolution.chapter.id);
        const selected: ChapterRef = located?.chapter ?? {
          id: resolution.chapter.id,
          chapter_code: resolution.chapter.chapter_code,
          chapter_title: resolution.chapter.chapter_title,
          chapter_display_name: resolution.chapter.chapter_display_name,
          topic_count: 0,
          concept_count: 0,
        };
        const savedSubject = String(initialChapterIdentity.subject ?? "").trim();
        const directorySubject = located?.subject ?? resolution.subject ?? "";
        // One short clause, so a reviewer who saved "History" and is now
        // reading "Social Science" can see it is the same row.
        const fold = resolution.subject_folded && directorySubject
          ? ` (saved under ${savedSubject || "another subject"}, shown here`
            + ` under ${directorySubject})`
          : "";

        setBoard(located?.board ?? resolution.board ?? "");
        setGrade(located?.grade ?? resolution.grade ?? "");
        setSubject(directorySubject);
        setUnit(located?.unit ?? resolution.unit ?? "");
        setChapter(selected);
        setScopeType("chapter");
        setPicked([]);
        setInitialSelectionTone("ok");
        setInitialSelectionMessage(
          `Saved checkpoint target selected: ${displayLabel(
            selected.chapter_display_name,
            selected.chapter_title,
            "saved chapter",
          )}${fold}.`,
        );
      })
      .catch((lookupError) => {
        if (superseded()) return;
        // A dropped network or a 500 must not leave five blank dropdowns and
        // no explanation; manual selection still works.
        setInitialSelectionTone("error");
        setInitialSelectionMessage(
          `The saved destination could not be looked up (${String(lookupError)}). `
          + "Select the matching chapter manually; generation will remain "
          + "disabled until then.",
        );
      });
  }, [initialChapterIdentity, loading, reloadSignal, tree]);

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
      onScope({
        type: "chapter",
        ids: [chapter.id],
        label: displayLabel(
          chapter.chapter_display_name,
          chapter.chapter_title,
          "Untitled chapter",
        ),
      });
    } else if (picked.length) {
      onScope({
        type: scopeType,
        ids: picked,
        label: `${picked.length} ${scopeType}(s) in ${displayLabel(
          chapter.chapter_display_name,
          chapter.chapter_title,
          "Untitled chapter",
        )}`,
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
    setInitialSelectionMessage("");
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
      {loading && (
        <div className="row muted">
          <span className="spinner" aria-hidden="true" />
          <span>Loading directory…</span>
        </div>
      )}
      {!loading && loadError && (
        <div className="error-box mb-8">{loadError}</div>
      )}
      {!loading && !loadError && tree.length === 0 && (
        <div className="error-box mb-8">
          No units or chapters are loaded yet. Upload your syllabus structure
          Excel files below, then pick Board → Class → Subject → Unit → Chapter
          manually. The PDF is never auto-matched to a chapter.
        </div>
      )}
      {initialSelectionMessage && (
        <div
          className={
            initialSelectionTone === "ok"
              ? "resume-target-ok mb-8"
              : "error-box mb-8"
          }
          role="status"
        >
          {initialSelectionMessage}
        </div>
      )}
      <div className="row">
        <div className="field">
          <label className="field-label" htmlFor={`${idBase}-board`}>
            Board
          </label>
          <select id={`${idBase}-board`} value={board}
            onChange={(e) => { setBoard(e.target.value); reset("board"); }}
            disabled={loading || tree.length === 0}>
            <option value="">Board…</option>
            {tree.map((b) => <option key={b.board}>{b.board}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${idBase}-grade`}>
            Class
          </label>
          <select id={`${idBase}-grade`} value={grade} disabled={!boardNode}
            onChange={(e) => { setGrade(e.target.value); reset("grade"); }}>
            <option value="">Class…</option>
            {boardNode?.grades.map((g) => <option key={g.grade}>{g.grade}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${idBase}-subject`}>
            Subject
          </label>
          <select id={`${idBase}-subject`} value={subject} disabled={!gradeNode}
            onChange={(e) => { setSubject(e.target.value); reset("subject"); }}>
            <option value="">Subject…</option>
            {gradeNode?.subjects.map((s) => <option key={s.subject}>{s.subject}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${idBase}-unit`}>
            Unit
          </label>
          <select id={`${idBase}-unit`} value={unit} disabled={!subjectNode}
            onChange={(e) => { setUnit(e.target.value); reset("unit"); }}>
            <option value="">Unit…</option>
            {subjectNode?.units.map((u) => (
              <option key={u.unit} value={u.unit}>
                {displayLabel("", u.unit, "Untitled unit")}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="field-label" htmlFor={`${idBase}-chapter`}>
            Chapter
          </label>
          <select id={`${idBase}-chapter`} value={chapter?.id ?? ""} disabled={!unitNode}
            onChange={(e) => {
              const id = Number(e.target.value);
              setChapter(unitNode?.chapters.find((c) => c.id === id) ?? null);
              setScopeType("chapter");
              setPicked([]);
              setInitialSelectionMessage("");
            }}>
            <option value="">Chapter…</option>
            {unitNode?.chapters.map((c) => (
              <option key={c.id} value={c.id}>
                {displayLabel(c.chapter_display_name, c.chapter_title, "Untitled chapter")}
                {` (${c.concept_count} concepts)`}
              </option>
            ))}
          </select>
        </div>
      </div>

      {chapter && !chapterOnly && (
        <div className="card mt-12">
          <div className="row mb-8">
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
                  <span>{displayLabel(t.topic_display_name, t.topic_title, "Untitled topic")}</span>
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
                    <span>{displayLabel(c.concept_display_name, c.concept_title, "Untitled concept")}</span>
                    <span className="muted">
                      {displayLabel(t.topic_display_name, t.topic_title, "Untitled topic")}
                    </span>
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

/**
 * Find the tree node for a chapter the server already identified.
 *
 * Bookkeeping only — an id lookup over the loaded tree. It decides nothing
 * about which chapter is meant; it just returns the object (and the exact
 * board/class/subject/unit labels) the dropdowns are rendered from.
 */
function locateChapterInTree(
  tree: BoardNode[],
  chapterId: number,
): {
  board: string;
  grade: string;
  subject: string;
  unit: string;
  chapter: ChapterRef;
} | null {
  for (const boardNode of tree) {
    for (const gradeNode of boardNode.grades) {
      for (const subjectNode of gradeNode.subjects) {
        for (const unitNode of subjectNode.units) {
          const found = unitNode.chapters.find((item) => item.id === chapterId);
          if (found) {
            return {
              board: boardNode.board,
              grade: gradeNode.grade,
              subject: subjectNode.subject,
              unit: unitNode.unit,
              chapter: found,
            };
          }
        }
      }
    }
  }
  return null;
}
