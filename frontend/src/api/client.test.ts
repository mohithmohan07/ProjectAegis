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
