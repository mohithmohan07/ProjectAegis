/* Theme control: light / dark / system. "System" removes the override and
   lets the prefers-color-scheme blocks in styles.css decide; an explicit
   choice is stamped as data-theme on <html> and persisted. index.html
   replays the stored choice before first paint so there is no flash. */

import { useCallback, useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";

const STORAGE_KEY = "aegis-theme";
const ORDER: ThemeChoice[] = ["system", "light", "dark"];

const listeners = new Set<() => void>();

function readChoice(): ThemeChoice {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === "light" || raw === "dark" ? raw : "system";
  } catch {
    return "system";
  }
}

function applyChoice(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  try {
    if (choice === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    /* storage unavailable: the attribute still applies for this session */
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useTheme(): { choice: ThemeChoice; cycle: () => void } {
  const choice = useSyncExternalStore(subscribe, readChoice, () => "system" as ThemeChoice);
  const cycle = useCallback(() => {
    const current = readChoice();
    const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
    applyChoice(next);
  }, []);
  return { choice, cycle };
}

export const THEME_LABEL: Record<ThemeChoice, string> = {
  system: "Auto",
  light: "Light",
  dark: "Dark",
};
