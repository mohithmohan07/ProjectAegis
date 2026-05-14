import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

beforeEach(() => {
  // Pages fire requests on mount; stub fetch so render does not crash.
  vi.stubGlobal(
    "fetch",
    () =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve([]),
        text: () => Promise.resolve("[]"),
      }) as unknown as Promise<Response>,
  );
});

test("renders brand and nav", () => {
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <App />
    </MemoryRouter>,
  );
  expect(screen.getByText("Aegis")).toBeDefined();
  expect(screen.getByText("Pipeline")).toBeDefined();
  expect(screen.getByText("Assessment Tagging")).toBeDefined();
});
