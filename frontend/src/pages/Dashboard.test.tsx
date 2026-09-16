import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Dashboard, { money } from "./Dashboard";
import type { RunDashboard } from "../types";

const dashboard = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ api: { runDashboard: dashboard }, isNonTransientStatus: () => false }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const costs = { known_cost_usd: 7, known_cost_inr: null, cost_complete: false, batch_cost_usd: 2,
  synchronous_cost_usd: 3, unclassified_cost_usd: 2, batch_requests: 4, synchronous_requests: 2,
  reused_responses: 3, pending_requests: 1, unresolved_requests: 0, request_count: 8, incomplete_runs: 1 };
const data: RunDashboard = {
  summary: { ...costs, uploaded_runs: 2, runs_started: 2, unique_chapters_started: 1, unassigned_runs: 0,
    running: 1, queued: 0, recovering: 1, failed: 0, concepts_complete: 1, masters_complete: 0, published: 0 },
  states: [{ value: "recovering", label: "Interrupted — recovering", count: 1 }],
  users: [{ email: "starter@up.school", runs: 2, ...costs }],
  items: [{ ...costs, job_id: 5, chapter_id: 8, chapter_title: "Electricity", chapter_code: "CH08",
    board: "CBSE", grade: "10", subject: "Science", catalogue_active: false, historical_source: true,
    filename: "source.pdf", started: true, created_at: "2026-09-16T10:00:00Z", state: "recovering",
    state_label: "Interrupted — recovering", stage: "Checkpoint recovery", initiator_email: "starter@up.school",
    requested_mode: "batch", cohort_id: "cohort", error: "", concepts_complete: true, masters_complete: false,
    published: false, notification: { state: "pending", recipient: "starter@up.school" } }],
  page: 1, page_size: 25, total: 1, total_pages: 1,
  queue: { running: 1, queued: 0, blocked: 0, capacity: 6, worker_alive: true },
  server_time: "2026-09-16T11:00:00Z", cost_scope: "Known costs exclude pending usage.",
  visibility: "Shared chapter history and your private runs.", notifications: { configured: false },
};

test("dashboard separates known, pending and unclassified spend and links the exact historical job", async () => {
  dashboard.mockResolvedValue(data);
  render(<MemoryRouter><Dashboard /></MemoryRouter>);
  expect(await screen.findByText("Electricity")).toBeDefined();
  expect(screen.getByText("Historical / unclassified")).toBeDefined();
  expect(screen.getByText("Sender setup required")).toBeDefined();
  expect(screen.getByRole("link", { name: "Electricity" }).getAttribute("href")).toBe("/build-concepts?job=5");
  expect(screen.getByText("3 reused · no new charge")).toBeDefined();
  expect(screen.getByText("Email: pending")).toBeDefined();
  expect(screen.getByText("Previous source")).toBeDefined();
  expect(dashboard).toHaveBeenCalledTimes(1);
});

test("a stage selection filters all dashboard totals, not only the visible rows", async () => {
  dashboard.mockResolvedValue(data);
  render(<MemoryRouter><Dashboard /></MemoryRouter>);
  await screen.findByText("Electricity");
  fireEvent.change(screen.getByLabelText("Stage / status"), { target: { value: "recovering" } });
  await waitFor(() => expect(dashboard).toHaveBeenLastCalledWith({ q: "", state: "recovering", page: 1 }));
});

test("missing or non-finite costs are unpriced, never a misleading zero", () => {
  expect(money(null)).toBe("Unpriced");
  expect(money(Number.NaN)).toBe("Unpriced");
  expect(money(0)).toContain("0.00");
});
