import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

// Installability for the store apps (Play TWA) and home-screen PWA. The
// worker only caches the app shell and hashed build assets — API calls
// and progress streams pass through untouched (see public/sw.js). Dev
// builds skip it so Vite's module graph is never cached.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // An uninstallable environment (e.g. private mode) is still a
      // fully working web app.
    });
  });
}
