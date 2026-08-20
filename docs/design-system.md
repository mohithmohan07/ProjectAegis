# Aegis design system (frontend)

The 2026-08 makeover replaced the ad-hoc dark stylesheet with a token-driven
system. This note is the map for anyone touching the frontend's look.

## Where things live

| Concern | File |
|---|---|
| Every color, radius, shadow, font, motion token | `frontend/src/styles.css` (`:root` + theme blocks at the top) |
| Theme persistence (auto / light / dark) | `frontend/src/theme.ts` + the pre-paint script in `frontend/index.html` |
| The logo mark | `frontend/src/components/Logo.tsx` (one component, swap the paths to rebrand) |
| Fonts | Google Fonts `Inter` (UI) + `JetBrains Mono` (console/code), loaded in `index.html`, with full system-stack fallbacks |

## Rules

1. **No hex colors outside `styles.css`.** Pages consume classes; the accent
   palette is swappable in the one token block at the top (`--accent*`).
   When UpSchool's exact brand palette is provided, re-skinning is a
   five-line change.
2. **Two themes, three choices.** Light is the canonical palette on bare
   `:root`; dark is duplicated under `[data-theme="dark"]` and under the
   `prefers-color-scheme: dark` media block (system default). The toggle
   cycles Auto → Light → Dark and persists to `localStorage["aegis-theme"]`.
3. **The console stays dark in both themes.** It is a terminal — the run's
   full record — and keeps its own `--console-*` palette.
4. **No time estimates anywhere.** The ETA was removed end to end (backend
   `progress.py` no longer computes or emits `eta_seconds`/`eta_label`; the
   console shows real elapsed time, event counts, steps and percent — facts,
   not predictions). Cost estimates (`estimated_cost_usd`) are real reported
   data and stay.
5. **Spacing via utilities** (`.mt-8/.mt-12/.mt-16/.mt-24`, `.mb-*`,
   `.stack`), not inline `style={{margin…}}`. The only sanctioned inline
   styles: `display:none` on hidden file inputs, state-driven opacity, and
   dynamic progress widths.
6. **Test contracts are law.** Class names asserted by tests
   (`download`/`primary` on the three release downloads, `katex-inline` on a
   `<code>`, `rich-section*`), every `data-testid`, the
   `revision-instruction` label pairing, and "Aegis" as its own text node in
   the sidebar brand must survive any restyle.

## Console (the log surface)

`RunConsolePanel` renders every stream event with timestamp and level,
retains up to 3000 lines per run, filters (All / Steps / Issues), follows the
newest line unless the reader scrolls up (a "Follow latest" pill returns),
shows a live elapsed clock and event count, and copies the full log to the
clipboard. Backend emitters: `progress.log/step/set_progress` — detailed
step-level narration is the product's voice; keep emitting it.

## Recorded residues

- Real KaTeX typesetting on the review page is still a follow-up (chip shows
  raw LaTeX).
- The mark is the owner's pick: the Constellation Shield (five graph nodes
  tracing a shield, amber hub — "the concept map is the armor"), with the
  Clarius Blue accent direction. `Logo.tsx` and the `index.html` favicon
  remain the two swap points.
- UpSchool's exact brand palette could not be fetched from this environment
  (network egress blocked for up.school / examin8.com); the owner chose the
  Clarius Blue direction from the gallery — exact parent-brand matching
  stays a five-line token swap when the real values arrive.
