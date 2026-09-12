import { afterEach, expect, test, vi } from "vitest";
import { api, streamNdjson } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("sends the signed session cookie on regular API requests", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      mode: "local",
      google_client_id: "",
      allowed_google_domain: "",
      csrf_token: "csrf",
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.authConfig();

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/auth/config"),
    expect.objectContaining({ credentials: "include" }),
  );
});

test("sends the signed session cookie on streamed generation requests", async () => {
  const encoded = new TextEncoder().encode(
    '{"type":"result","data":{"ok":true}}\n',
  );
  let returned = false;
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (returned) return { done: true, value: undefined };
          returned = true;
          return { done: false, value: encoded };
        },
      }),
    },
  });
  vi.stubGlobal("fetch", fetchMock);

  await streamNdjson("/stream", {}, vi.fn());

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/stream"),
    expect.objectContaining({ credentials: "include" }),
  );
});

test("sends the admin token when clearing shared data", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ status: "ok", chapters: 0, questions: 0 }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.resetData("admin-token");

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/data/reset"),
    expect.objectContaining({
      method: "POST",
      credentials: "include",
      headers: expect.objectContaining({
        "X-Admin-Token": "admin-token",
      }),
    }),
  );
});

test("records a semantic decision without calling a generation endpoint", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      status: "decision_recorded",
      resume_required: true,
      resolved_decision: {},
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.submitConceptDecision(42, "phase33-host-abc123", {
    choice: "expand_existing",
    target_concept_id: "HOST-CONCEPT-0001",
  });

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining(
      "/build-concepts/uploads/42/decisions/phase33-host-abc123",
    ),
    expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({
        choice: "expand_existing",
        target_concept_id: "HOST-CONCEPT-0001",
      }),
    }),
  );
  expect(fetchMock.mock.calls[0][0]).not.toContain("/generate");
});

test("records an explicit Phase 3 source evidence choice by generic target id", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      status: "decision_recorded",
      resume_required: true,
      resolved_decision: {},
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.submitConceptDecision(42, "phase3-source-review-def456", {
    choice: "select_candidate",
    target_id: "PDF-0008:BLK-0048",
  });

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining(
      "/build-concepts/uploads/42/decisions/phase3-source-review-def456",
    ),
    expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({
        choice: "select_candidate",
        target_id: "PDF-0008:BLK-0048",
      }),
    }),
  );
  expect(fetchMock.mock.calls[0][0]).not.toContain("/generate");
});

test("stages corrected Concept workbooks and keeps Master continuation explicit", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ accepted: true }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.uploadCorrectedConceptInput(
    42,
    "pre",
    new File(["xlsx"], "pre-edited.xlsx"),
  );

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining(
      "/build-concepts/uploads/42/concept-review/submit?lane=pre",
    ),
    expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: expect.any(FormData),
    }),
  );
  expect(api.paths.masterGenerate(42)).toBe(
    "/build-concepts/uploads/42/concept-review/master",
  );
  expect(fetchMock.mock.calls[0][0]).not.toContain("upload-edited-workbook");
});

test("stores a reviewed Master file on its lane's master-review route without publishing", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ lane: "post", version: 2, issues: [] }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.uploadReviewedMaster(
    42,
    "post",
    new File(["xlsx"], "post-master-reviewed.xlsx"),
  );

  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining(
      "/build-concepts/uploads/42/master-review/submit?lane=post",
    ),
    expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: expect.any(FormData),
    }),
  );
  // Multipart: the browser sets the boundary, so no JSON content type.
  expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty("Content-Type");
  expect(fetchMock.mock.calls[0][0]).not.toContain("publish");
  expect(fetchMock.mock.calls[0][0]).not.toContain("upload-edited-workbook");
});

test("publishes a reviewed Master on its lane's master-review publish route", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({
      lane: "pre",
      database: { groups_created: 1, questions_created: 2, labels_reissued: 0 },
    }),
  });
  vi.stubGlobal("fetch", fetchMock);

  const receipt = await api.publishReviewedMaster(42, "pre");

  expect(receipt.database?.questions_created).toBe(2);
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining(
      "/build-concepts/uploads/42/master-review/publish?lane=pre",
    ),
    expect.objectContaining({ method: "POST", credentials: "include" }),
  );
});

test("a refused Master publication surfaces the server's readable detail", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: false,
    status: 409,
    statusText: "Conflict",
    json: async () => ({ detail: "Publish the post Concept file first" }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await expect(api.publishReviewedMaster(42, "post")).rejects.toMatchObject({
    message: "Publish the post Concept file first",
    status: 409,
  });
});

test("chapter batch list omits blank filters and keeps the /chapter-batches prefix", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ items: [] }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.chapterBatchList({ board: "NCF", grade: "", subject: "", page: 2 });

  const url = String(fetchMock.mock.calls[0][0]);
  expect(url.startsWith("/chapter-batches?")).toBe(true);
  expect(url).toContain("board=NCF");
  expect(url).toContain("page=2");
  expect(url).not.toContain("grade=");
  expect(url).not.toContain("subject=");
});

test("staging a source carries the publication in both accepted locations", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ chapter_id: 7 }),
  });
  vi.stubGlobal("fetch", fetchMock);

  const file = new File(["%PDF-1.4"], "shapes.pdf", { type: "application/pdf" });
  await api.chapterBatchStageSource(7, file, "Seed to Plant", 42.4);

  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/chapter-batches/7/source?");
  expect(String(url)).toContain("source_book=Seed+to+Plant");
  expect(String(url)).toContain("chapter_duration_minutes=42");
  const body = (init as { body: FormData }).body;
  expect(body.get("source_book")).toBe("Seed to Plant");
  expect(body.get("chapter_duration_minutes")).toBe("42");
  expect(body.get("file")).toBe(file);
});

test("a push sends one request naming the step and every row", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ step: "step01", push_group_id: "g", results: [] }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await api.chapterBatchPush("step01", [{ chapter_id: 1 }, { chapter_id: 2 }]);

  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0];
  expect(String(url)).toContain("/chapter-batches/push");
  expect(JSON.parse((init as { body: string }).body)).toEqual({
    step: "step01",
    rows: [{ chapter_id: 1 }, { chapter_id: 2 }],
  });
});
