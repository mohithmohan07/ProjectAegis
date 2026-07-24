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
