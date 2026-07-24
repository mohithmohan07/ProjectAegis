/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/admin": "http://127.0.0.1:8000",
      "/auth": "http://127.0.0.1:8000",
      "/build-assessments": "http://127.0.0.1:8000",
      "/build-concepts": "http://127.0.0.1:8000",
      "/data": "http://127.0.0.1:8000",
      "/directory": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/tagging": "http://127.0.0.1:8000",
      "/workbooks": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
