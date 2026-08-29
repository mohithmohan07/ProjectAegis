import { useMemo, useState } from "react";
import type { ReactNode } from "react";

/**
 * Full converted-text viewer (owner request, 2026-08-29). The old preview
 * showed only the first 800 characters with no way to see the rest, which
 * read as "the conversion lost my chapter". The stored MMD was always
 * complete — the truncation was display-only — so this viewer shows the
 * whole text on demand: collapsed head by default, one click to expand,
 * a search box that counts and highlights matches, and a client-side
 * download of exactly the text shown.
 */

const COLLAPSED_CHARS = 800;
const MAX_RENDERED_MATCHES = 500;

function escapeForFilename(name: string): string {
  const base = name.replace(/\.[^.]+$/, "") || "converted";
  return `${base}.mmd`;
}

function Highlighted({ text, query }: { text: string; query: string }) {
  const needle = query.trim().toLowerCase();
  if (!needle) return <>{text}</>;
  const lower = text.toLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let rendered = 0;
  while (rendered < MAX_RENDERED_MATCHES) {
    const hit = lower.indexOf(needle, cursor);
    if (hit === -1) break;
    if (hit > cursor) parts.push(text.slice(cursor, hit));
    parts.push(
      <mark key={`${hit}-${rendered}`}>{text.slice(hit, hit + needle.length)}</mark>,
    );
    cursor = hit + needle.length;
    rendered += 1;
  }
  parts.push(text.slice(cursor));
  return <>{parts}</>;
}

export default function MmdViewer({
  text,
  filename,
}: {
  text: string;
  filename?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");

  const matchCount = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return 0;
    let count = 0;
    let cursor = 0;
    const lower = text.toLowerCase();
    while (true) {
      const hit = lower.indexOf(needle, cursor);
      if (hit === -1) break;
      count += 1;
      cursor = hit + needle.length;
    }
    return count;
  }, [text, query]);

  // A search always reads the WHOLE text, never just the collapsed head.
  const searching = query.trim().length > 0;
  const shown = expanded || searching
    ? text
    : text.slice(0, COLLAPSED_CHARS);
  const truncated = !expanded && !searching && text.length > COLLAPSED_CHARS;

  function download() {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = escapeForFilename(filename || "converted");
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mmd-viewer mt-12">
      <div className="row">
        <span className="muted">
          Converted text · {text.length.toLocaleString()} characters
        </span>
        <input
          type="search"
          placeholder="Search the converted text…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search the converted text"
        />
        {searching && (
          <span className="muted">
            {matchCount === 0
              ? "no matches"
              : `${matchCount.toLocaleString()} match${matchCount === 1 ? "" : "es"}`}
          </span>
        )}
        <div className="spacer" />
        <button className="ghost" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Collapse" : "Show full text"}
        </button>
        <button className="ghost" onClick={download}>
          Download .mmd
        </button>
      </div>
      <pre
        className="mmd-preview"
        style={
          expanded || searching
            ? { maxHeight: "60vh", overflow: "auto" }
            : undefined
        }
      >
        <Highlighted text={shown} query={query} />
      </pre>
      {truncated && (
        <div className="hint">
          Showing the first {COLLAPSED_CHARS} characters — the stored text is
          complete. Use “Show full text” or search to read all of it.
        </div>
      )}
    </div>
  );
}
