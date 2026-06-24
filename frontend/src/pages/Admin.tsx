import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { PromptInfo } from "../types";

const TOKEN_KEY = "aegis_admin_token";

export default function Admin() {
  const [token, setToken] = useState<string>(() => sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [authed, setAuthed] = useState(false);

  // Validate any stored token on mount by attempting a list.
  useEffect(() => {
    if (!token) return;
    api.adminListPrompts(token).then(() => setAuthed(true)).catch(() => {
      sessionStorage.removeItem(TOKEN_KEY);
      setToken("");
      setAuthed(false);
    });
  }, [token]);

  if (!authed) {
    return <Login onAuthed={(t) => { sessionStorage.setItem(TOKEN_KEY, t); setToken(t); setAuthed(true); }} />;
  }
  return <PromptManager token={token} onLogout={() => {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken("");
    setAuthed(false);
  }} />;
}

function Login({ onAuthed }: { onAuthed: (token: string) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token } = await api.adminLogin(password);
      onAuthed(token);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Admin</h1>
      <div className="subtitle">
        Enter the admin password to edit the GPT prompts used across the tool.
      </div>
      <div className="card" style={{ maxWidth: 420 }}>
        <form onSubmit={submit}>
          <div className="field-label">Password</div>
          <input
            type="password"
            value={password}
            autoFocus
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Admin password"
            style={{ width: "100%", marginBottom: 12 }}
          />
          <button disabled={busy || !password}>{busy ? "Checking…" : "Unlock"}</button>
        </form>
        {error && <div className="error-box" style={{ marginTop: 12 }}>{error}</div>}
      </div>
    </>
  );
}

function PromptManager({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [prompts, setPrompts] = useState<PromptInfo[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [activeCat, setActiveCat] = useState<string>("");
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load() {
    setLoading(true);
    api.adminListPrompts(token)
      .then((r) => {
        setPrompts(r.prompts);
        setCategories(r.categories);
        setActiveCat((c) => c || r.categories[0] || "");
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(load, [token]);

  const visible = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return prompts.filter((p) =>
      (!activeCat || p.category === activeCat) &&
      (!f || p.label.toLowerCase().includes(f) || p.key.toLowerCase().includes(f)
        || p.current.toLowerCase().includes(f)),
    );
  }, [prompts, activeCat, filter]);

  const overrideCount = prompts.filter((p) => p.overridden).length;

  function onSaved(updated: PromptInfo) {
    setPrompts((ps) => ps.map((p) => (p.key === updated.key ? updated : p)));
  }

  return (
    <>
      <h1>Admin · Prompts</h1>
      <div className="subtitle">
        Edit any GPT prompt in the software. Saved changes apply to the next run of
        every function — no restart needed. {overrideCount > 0 &&
          <span className="badge yellow">{overrideCount} customized</span>}
        <button className="ghost" style={{ marginLeft: 12 }} onClick={onLogout}>Lock</button>
      </div>

      {error && <div className="error-box">{error}</div>}
      {loading && <div className="muted">Loading prompts…</div>}

      {!loading && (
        <>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="row">
              <select value={activeCat} onChange={(e) => setActiveCat(e.target.value)}>
                {categories.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <input
                placeholder="Search prompts…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                style={{ minWidth: 240 }}
              />
              <div className="spacer" />
              <span className="muted">{visible.length} prompt(s)</span>
            </div>
          </div>

          {visible.map((p) => (
            <PromptEditor key={p.key} prompt={p} token={token} onSaved={onSaved} />
          ))}
        </>
      )}
    </>
  );
}

function PromptEditor({ prompt, token, onSaved }: {
  prompt: PromptInfo; token: string; onSaved: (p: PromptInfo) => void;
}) {
  const [text, setText] = useState(prompt.current);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => setText(prompt.current), [prompt.current]);

  const dirty = text !== prompt.current;

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const updated = await api.adminUpdatePrompt(token, prompt.key, text);
      onSaved(updated);
      setMsg("Saved — live on the next run.");
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setMsg(null);
    try {
      const updated = await api.adminResetPrompt(token, prompt.key);
      onSaved(updated);
      setText(updated.current);
      setMsg("Reset to default.");
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row">
        <strong>{prompt.label}</strong>
        {prompt.overridden && <span className="badge yellow">customized</span>}
        <code className="mono muted">{prompt.key}</code>
        <div className="spacer" />
        {dirty && <span className="badge accent">unsaved</span>}
      </div>
      {prompt.description && <div className="muted" style={{ margin: "6px 0" }}>{prompt.description}</div>}
      {prompt.variables.length > 0 && (
        <div className="muted" style={{ marginBottom: 6 }}>
          Variables: {prompt.variables.map((v) => <code key={v} className="mono">{`{{${v}}}`}</code>)}
        </div>
      )}
      <textarea
        className="prompt-text mono"
        value={text}
        spellCheck={false}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row" style={{ marginTop: 8 }}>
        <button disabled={busy || !dirty} onClick={save}>Save</button>
        <button className="ghost" disabled={busy || !prompt.overridden} onClick={reset}>
          Reset to default
        </button>
        {msg && <span className="muted">{msg}</span>}
      </div>
    </div>
  );
}
