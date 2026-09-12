import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

beforeEach(() => {
  // Pages fire requests on mount; stub fetch so render does not crash.
  vi.stubGlobal(
    "fetch",
    (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.endsWith("/auth/config")
        ? {
          mode: "local",
          google_client_id: "",
          allowed_google_domain: "",
          csrf_token: "local-csrf",
        }
        : url.endsWith("/auth/me")
          ? {
            authenticated: true,
            user: {
              sub: "local",
              email: "local@localhost",
              name: "Local mode",
            },
          }
          : [];
      return (
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      }) as unknown as Promise<Response>
      );
    },
  );
});

test("renders brand and the integrated-tool navigation", async () => {
  render(
    <MemoryRouter initialEntries={["/home"]}>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByText("Aegis")).toBeDefined();
  expect(screen.getByText("Build Assessments")).toBeDefined();
  expect(screen.getByText("Build Concepts")).toBeDefined();
  expect(screen.getByText("Database")).toBeDefined();
});

test("the Chapters console has BOTH a nav entry and a route that renders it", async () => {
  render(
    <MemoryRouter initialEntries={["/chapters"]}>
      <App />
    </MemoryRouter>,
  );
  // Half of this is invisible on its own: pages/ReleaseReview.tsx is a
  // complete page with no route, and a route with no nav entry is a page
  // nobody can find. Assert both halves.
  const link = await screen.findByRole("link", { name: "Chapters" });
  expect(link.getAttribute("href")).toBe("/chapters");
  expect(
    await screen.findByRole("heading", { level: 1, name: "Chapters" }),
  ).toBeDefined();
});
