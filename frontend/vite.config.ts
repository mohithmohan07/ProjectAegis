/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import type { IncomingMessage } from "http";

const API = "http://127.0.0.1:8000";

/* Several backend API prefixes are ALSO SPA routes (/build-concepts,
   /tagging, …). A hard load / deep link of the page itself must get
   index.html from vite, while every real API call under the prefix keeps
   proxying. The page URLs are exactly the bare prefix — plus the deep-linked
   review surface — and no API endpoint or download lives at those exact
   paths, so the bypass is precise. */
const SPA_PATHS = new Set([
  "/admin",
  "/build-assessments",
  "/build-concepts",
  "/tagging",
  "/workbooks",
]);

function spaBypass(req: IncomingMessage): string | undefined {
  const path = (req.url ?? "").split("?")[0].replace(/\/$/, "") || "/";
  if (SPA_PATHS.has(path) || path.startsWith("/build-concepts/review/")) {
    return "/index.html";
  }
  return undefined;
}

const proxied = (prefixes: string[]) =>
  Object.fromEntries(
    prefixes.map((p) => [p, { target: API, bypass: spaBypass }]),
  );

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      ...proxied([
        "/admin",
        "/auth",
        "/build-assessments",
        "/build-concepts",
        "/directory",
        "/health",
        "/tagging",
        "/workbooks",
      ]),
      // Regex key: /data/* only — a bare-prefix "/data" would also capture
      // the /database SPA route.
      "^/data(/|$)": { target: API },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
