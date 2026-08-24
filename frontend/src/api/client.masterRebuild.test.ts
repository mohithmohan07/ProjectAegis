import { afterEach, expect, test, vi } from "vitest";

import { api } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

test.each([
  ["pre", "/build-assessments/releases/from-job/81/pre"],
  ["post", "/build-assessments/releases/from-job/81"],
] as const)("Master rebuild keeps the %s lane on its exact route", async (
  lane, expectedPath,
) => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    id: 22,
    release_uid: "REL-22",
    version: 1,
    state: "published",
    readiness: "ready",
    concept_snapshot_sha256: "abc",
    workbook_sha256s: {},
    published: true,
    uploaded: false,
    created_at: "2026-08-24T00:00:00Z",
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);

  await api.rebuildMasterFromConceptJob(81, lane);

  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledWith(
    expectedPath,
    expect.objectContaining({
      method: "POST",
      credentials: "include",
    }),
  );
});
