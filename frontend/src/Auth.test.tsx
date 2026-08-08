import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AuthAccount, AuthGate, AuthProvider } from "./Auth";

const apiMock = vi.hoisted(() => ({
  authConfig: vi.fn(),
  authMe: vi.fn(),
  authGoogle: vi.fn(),
  authLogout: vi.fn(),
}));

vi.mock("./api/client", () => ({
  api: apiMock,
  SESSION_EXPIRED_EVENT: "aegis:session-expired",
}));

beforeEach(() => {
  vi.clearAllMocks();
  document
    .querySelectorAll('script[data-aegis-google-identity="true"]')
    .forEach((script) => script.remove());
  delete window.google;
});

afterEach(() => {
  delete window.google;
});

test("fails closed when auth configuration cannot be loaded and retries", async () => {
  apiMock.authConfig.mockRejectedValueOnce(new Error("backend unavailable"));
  render(
    <AuthProvider>
      <AuthGate>
        <div>Protected app</div>
      </AuthGate>
    </AuthProvider>,
  );

  expect(await screen.findByText("Aegis is unavailable")).toBeDefined();
  expect(screen.queryByText("Protected app")).toBeNull();

  apiMock.authConfig.mockResolvedValueOnce({
    mode: "local",
    google_client_id: "",
    allowed_google_domain: "",
    csrf_token: "local-csrf",
  });
  apiMock.authMe.mockResolvedValueOnce({
    authenticated: true,
    user: {
      sub: "local",
      email: "local@localhost",
      name: "Local mode",
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByText("Protected app")).toBeDefined();
});

test("an expired session returns the app to the sign-in gate", async () => {
  // A 12h session dies under a long overnight run. The first 401 must flip
  // the app back to the sign-in card with a truthful message — not leave
  // every panel showing raw "authentication required" banners with no way
  // back in short of a manual page reload.
  window.google = {
    accounts: {
      id: {
        initialize: vi.fn(),
        renderButton: vi.fn((parent: HTMLElement) => {
          const button = document.createElement("button");
          button.textContent = "Google button";
          parent.appendChild(button);
        }),
        disableAutoSelect: vi.fn(),
      },
    },
  };
  apiMock.authConfig
    .mockResolvedValueOnce({
      mode: "google",
      google_client_id: "client-id",
      allowed_google_domain: "up.school",
      csrf_token: "csrf-before-expiry",
    })
    .mockResolvedValueOnce({
      mode: "google",
      google_client_id: "client-id",
      allowed_google_domain: "up.school",
      csrf_token: "csrf-after-expiry",
    });
  apiMock.authMe
    .mockResolvedValueOnce({
      authenticated: true,
      user: {
        sub: "user-1",
        email: "teacher@up.school",
        name: "Teacher",
        hd: "up.school",
      },
    })
    .mockResolvedValueOnce({ authenticated: false, user: null });

  render(
    <AuthProvider>
      <AuthGate>
        <div>Protected app</div>
      </AuthGate>
    </AuthProvider>,
  );

  expect(await screen.findByText("Protected app")).toBeDefined();

  await act(async () => {
    window.dispatchEvent(new Event("aegis:session-expired"));
  });

  expect(await screen.findByText("Sign in to continue")).toBeDefined();
  expect(screen.queryByText("Protected app")).toBeNull();
  expect(
    await screen.findByText(/Your session expired while you were away/),
  ).toBeDefined();
  // The config reload fetched a fresh CSRF pair for the next sign-in.
  await waitFor(() => expect(apiMock.authConfig).toHaveBeenCalledTimes(2));
});

test("refreshes CSRF configuration after logout before another Google sign-in", async () => {
  let googleCallback: ((response: { credential?: string }) => void) | undefined;
  const initialize = vi.fn((options: {
    callback: (response: { credential?: string }) => void;
  }) => {
    googleCallback = options.callback;
  });
  window.google = {
    accounts: {
      id: {
        initialize,
        renderButton: vi.fn((parent: HTMLElement) => {
          const button = document.createElement("button");
          button.textContent = "Google button";
          parent.appendChild(button);
        }),
        disableAutoSelect: vi.fn(),
      },
    },
  };
  apiMock.authConfig
    .mockResolvedValueOnce({
      mode: "google",
      google_client_id: "client-id",
      allowed_google_domain: "up.school",
      csrf_token: "csrf-before-logout",
    })
    .mockResolvedValueOnce({
      mode: "google",
      google_client_id: "client-id",
      allowed_google_domain: "up.school",
      csrf_token: "csrf-after-logout",
    });
  apiMock.authMe
    .mockResolvedValueOnce({ authenticated: false, user: null })
    .mockResolvedValueOnce({ authenticated: false, user: null });
  apiMock.authGoogle
    .mockResolvedValueOnce({
      authenticated: true,
      user: {
        sub: "user-1",
        email: "teacher@up.school",
        name: "Teacher",
        hd: "up.school",
      },
    })
    .mockResolvedValueOnce({
      authenticated: true,
      user: {
        sub: "user-1",
        email: "teacher@up.school",
        name: "Teacher",
        hd: "up.school",
      },
    });
  apiMock.authLogout.mockResolvedValue({ ok: true });

  render(
    <AuthProvider>
      <AuthGate>
        <div>Protected app</div>
        <AuthAccount />
      </AuthGate>
    </AuthProvider>,
  );

  expect(await screen.findByText("Sign in to continue")).toBeDefined();
  await waitFor(() => expect(googleCallback).toBeTypeOf("function"));
  await act(async () => {
    googleCallback?.({ credential: "first-google-token" });
  });
  expect(await screen.findByText("Protected app")).toBeDefined();
  expect(apiMock.authGoogle).toHaveBeenCalledWith(
    "first-google-token",
    "csrf-before-logout",
  );

  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(await screen.findByText("Sign in to continue")).toBeDefined();
  await waitFor(() => expect(apiMock.authConfig).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(googleCallback).toBeTypeOf("function"));
  await act(async () => {
    googleCallback?.({ credential: "second-google-token" });
  });
  await waitFor(() => {
    expect(apiMock.authGoogle).toHaveBeenLastCalledWith(
      "second-google-token",
      "csrf-after-logout",
    );
  });
});
