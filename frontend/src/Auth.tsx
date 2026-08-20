import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api, SESSION_EXPIRED_EVENT } from "./api/client";
import type { AuthConfig, AuthSession, AuthUser } from "./types";
import Logo from "./components/Logo";

type GoogleCredentialResponse = {
  credential?: string;
};

type GoogleIdentityApi = {
  initialize: (options: {
    client_id: string;
    callback: (response: GoogleCredentialResponse) => void;
    hd?: string;
  }) => void;
  renderButton: (
    parent: HTMLElement,
    options: Record<string, string | number>,
  ) => void;
  disableAutoSelect: () => void;
};

// Injected by the Capacitor shell when the site runs inside the store
// apps. Only the two plugins the sign-in flow needs are typed here.
type CapacitorListenerHandle = { remove: () => void };
type CapacitorShell = {
  isNativePlatform?: () => boolean;
  Plugins?: {
    App?: {
      addListener: (
        event: "appUrlOpen",
        handler: (data: { url: string }) => void,
      ) => CapacitorListenerHandle | Promise<CapacitorListenerHandle>;
      getLaunchUrl?: () => Promise<{ url?: string } | null | undefined>;
    };
    Browser?: {
      open: (options: { url: string }) => Promise<void>;
      close?: () => Promise<void>;
    };
  };
};

declare global {
  interface Window {
    google?: {
      accounts: {
        id: GoogleIdentityApi;
      };
    };
    Capacitor?: CapacitorShell;
  }
}

function isNativeShell(): boolean {
  try {
    return window.Capacitor?.isNativePlatform?.() === true;
  } catch {
    return false;
  }
}

type AuthContextValue = {
  config: AuthConfig | null;
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  signInWithGoogle: (credential: string) => Promise<void>;
  signOut: () => Promise<void>;
  reload: () => Promise<void>;
};

const LOCAL_CONFIG: AuthConfig = {
  mode: "local",
  google_client_id: "",
  allowed_google_domain: "",
  csrf_token: "",
};

const AuthContext = createContext<AuthContextValue | null>(null);

function normalizeConfig(value: AuthConfig): AuthConfig {
  if (value?.mode !== "google" && value?.mode !== "local") {
    throw new Error("The server returned an invalid authentication mode.");
  }
  if (value.mode === "local") return {
    ...LOCAL_CONFIG,
    ...value,
    mode: "local",
  };
  return {
    mode: "google",
    google_client_id: String(value.google_client_id || ""),
    allowed_google_domain: String(value.allowed_google_domain || ""),
    csrf_token: String(value.csrf_token || ""),
    drive_checkpoint_backup: value.drive_checkpoint_backup,
  };
}

function localUser(session?: AuthSession): AuthUser {
  return session?.user ?? {
    sub: "local",
    email: "local@localhost",
    name: "Local mode",
  };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const initialLoadStartedRef = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const loadedConfig = normalizeConfig(await api.authConfig());
      setConfig(loadedConfig);
      const session = await api.authMe();
      setUser(
        loadedConfig.mode === "local"
          ? localUser(session)
          : session.authenticated
            ? session.user
            : null,
      );
    } catch (loadError) {
      setConfig(null);
      setUser(null);
      setError(
        "Aegis could not load its access configuration. "
        + `Check the server connection and try again. ${String(loadError)}`,
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (initialLoadStartedRef.current) return;
    initialLoadStartedRef.current = true;
    void load();
  }, [load]);

  useEffect(() => {
    // A 401 from any API call means the session cookie died under us —
    // sessions last 12h and a long run outlives them. Return the whole app
    // to the sign-in gate with a truthful message, and reload the config so
    // the next Google sign-in submits a fresh CSRF pair (the CSRF cookie
    // expires even sooner than the session).
    let handling = false;
    const onSessionExpired = () => {
      if (handling) return;
      handling = true;
      setUser((current) => {
        if (current === null) {
          handling = false;
          return current;
        }
        void load().then(() => {
          setError(
            "Your session expired while you were away. Sign in again to "
            + "continue — running jobs and saved outputs are unaffected.",
          );
          handling = false;
        });
        return null;
      });
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    };
  }, [load]);

  const signInWithGoogle = useCallback(async (credential: string) => {
    if (!credential || !config?.csrf_token) {
      setError("Google did not return a valid sign-in credential.");
      return;
    }
    setError(null);
    try {
      const session = await api.authGoogle(credential, config.csrf_token);
      if (!session.authenticated || !session.user) {
        throw new Error("Google sign-in did not create an authenticated session.");
      }
      setUser(session.user);
    } catch (signInError) {
      setError(String(signInError));
    }
  }, [config?.csrf_token]);

  const signOut = useCallback(async () => {
    setError(null);
    try {
      await api.authLogout();
      window.google?.accounts.id.disableAutoSelect();
      setUser(null);
      // Logout rotates/removes the CSRF cookie. Reload config so the next
      // Google sign-in submits the matching fresh token.
      await load();
    } catch (logoutError) {
      setError(String(logoutError));
    }
  }, [load]);

  const value = useMemo<AuthContextValue>(() => ({
    config,
    user,
    loading,
    error,
    signInWithGoogle,
    signOut,
    reload: load,
  }), [config, user, loading, error, signInWithGoogle, signOut, load]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  if (auth.loading) {
    return (
      <main className="auth-page">
        <div className="card auth-card" role="status">
          Checking access…
        </div>
      </main>
    );
  }
  if (!auth.config) {
    return (
      <main className="auth-page">
        <section className="card auth-card" aria-labelledby="auth-error-title">
          <h1 id="auth-error-title">Aegis is unavailable</h1>
          <div className="error-box">
            {auth.error || "The access configuration could not be loaded."}
          </div>
          <button type="button" onClick={() => void auth.reload()}>
            Try again
          </button>
        </section>
      </main>
    );
  }
  if (auth.config?.mode !== "google") return <>{children}</>;
  if (auth.user) return <>{children}</>;

  return (
    <main className="auth-page">
      <section className="card auth-card" aria-labelledby="auth-title">
        <div className="brand">
          <Logo />
          Aegis
          <small>Content Intelligence Engine</small>
        </div>
        <h1 id="auth-title">Sign in to continue</h1>
        <p className="muted">
          Use your @{auth.config.allowed_google_domain || "up.school"} Google
          account. Your uploads and resumable checkpoints are kept separate
          from other signed-in users.
        </p>
        {isNativeShell() ? (
          <NativeGoogleSignIn onSignedIn={auth.reload} />
        ) : (
          <GoogleSignInButton
            config={auth.config}
            onCredential={auth.signInWithGoogle}
          />
        )}
        {auth.error && <div className="error-box">{auth.error}</div>}
      </section>
    </main>
  );
}

export function AuthAccount() {
  const auth = useAuth();
  if (auth.config?.mode !== "google" || !auth.user) {
    return <div className="auth-local badge">Local mode</div>;
  }
  return (
    <div className="auth-account">
      <div className="row">
        {auth.user.picture && (
          <img
            className="auth-avatar"
            src={auth.user.picture}
            alt=""
            referrerPolicy="no-referrer"
          />
        )}
        <div className="auth-account-copy">
          <strong>{auth.user.name || auth.user.email}</strong>
          <span>{auth.user.email}</span>
        </div>
      </div>
      <button className="ghost" type="button" onClick={() => void auth.signOut()}>
        Sign out
      </button>
      {auth.error && <div className="error-box">{auth.error}</div>}
    </div>
  );
}

let googleScriptPromise: Promise<void> | null = null;

function loadGoogleIdentityServices(): Promise<void> {
  if (window.google?.accounts.id) return Promise.resolve();
  if (googleScriptPromise) return googleScriptPromise;
  googleScriptPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      'script[data-aegis-google-identity="true"]',
    );
    const script = existing ?? document.createElement("script");
    const loaded = () => {
      if (window.google?.accounts.id) resolve();
      else reject(new Error("Google Identity Services did not initialize."));
    };
    script.addEventListener("load", loaded, { once: true });
    script.addEventListener("error", () => {
      googleScriptPromise = null;
      reject(new Error("Could not load Google sign-in."));
    }, { once: true });
    if (!existing) {
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.dataset.aegisGoogleIdentity = "true";
      document.head.appendChild(script);
    }
  });
  return googleScriptPromise;
}

function GoogleSignInButton({
  config,
  onCredential,
}: {
  config: AuthConfig;
  onCredential: (credential: string) => Promise<void>;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (!config.google_client_id) {
      setError("Google sign-in is not configured on this server.");
      return;
    }
    loadGoogleIdentityServices()
      .then(() => {
        if (!active || !containerRef.current || !window.google) return;
        containerRef.current.replaceChildren();
        window.google.accounts.id.initialize({
          client_id: config.google_client_id,
          hd: config.allowed_google_domain || undefined,
          callback: (response) => {
            if (response.credential) void onCredential(response.credential);
            else setError("Google did not return a sign-in credential.");
          },
        });
        window.google.accounts.id.renderButton(containerRef.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          text: "signin_with",
          shape: "rectangular",
          width: 280,
        });
      })
      .catch((loadError) => {
        if (active) setError(String(loadError));
      });
    return () => {
      active = false;
    };
  }, [
    config.allowed_google_domain,
    config.google_client_id,
    onCredential,
  ]);

  return (
    <div className="google-signin">
      <div ref={containerRef} aria-label="Sign in with Google" />
      {error && <div className="error-box">{error}</div>}
    </div>
  );
}

/* Sign-in inside the store apps (Capacitor shell). Google refuses its
   Identity Services flow in embedded WebViews, so the app opens
   /auth/native/start in the SYSTEM browser; after Google, the server
   bounces a 90-second one-time ticket back on the aegis://auth deep
   link, and exchanging it here sets the normal session cookie in the
   app's WebView. */
function NativeGoogleSignIn({
  onSignedIn,
}: {
  onSignedIn: () => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [exchanging, setExchanging] = useState(false);
  const exchangingRef = useRef(false);

  const completeFromUrl = useCallback(async (url: string) => {
    if (!url.startsWith("aegis://auth")) return;
    let ticket = "";
    try {
      ticket = new URL(url).searchParams.get("ticket") ?? "";
    } catch {
      ticket = "";
    }
    if (!ticket) {
      setError("The sign-in link was missing its ticket. Try again.");
      return;
    }
    if (exchangingRef.current) return;
    exchangingRef.current = true;
    setExchanging(true);
    setError(null);
    try {
      await window.Capacitor?.Plugins?.Browser?.close?.();
    } catch {
      // The in-app browser sheet may already be gone; signing in matters,
      // closing the sheet doesn't.
    }
    try {
      const session = await api.authNativeExchange(ticket);
      if (!session.authenticated) {
        throw new Error("The sign-in ticket did not create a session.");
      }
      await onSignedIn();
    } catch (exchangeError) {
      setError(String(exchangeError));
    } finally {
      exchangingRef.current = false;
      setExchanging(false);
    }
  }, [onSignedIn]);

  useEffect(() => {
    const appPlugin = window.Capacitor?.Plugins?.App;
    if (!appPlugin) return;
    let removed = false;
    let handle: CapacitorListenerHandle | null = null;
    Promise.resolve(
      appPlugin.addListener("appUrlOpen", (data) => {
        void completeFromUrl(String(data?.url ?? ""));
      }),
    ).then((listener) => {
      if (removed) listener.remove();
      else handle = listener;
    }).catch(() => {
      // Without the listener the button still works via getLaunchUrl on
      // a cold start; warm-start deep links would be lost, so surface it.
      setError("The app could not listen for the sign-in redirect.");
    });
    // Cold start straight from the deep link: the event fired before
    // this component mounted, so ask for the launch URL explicitly.
    void appPlugin.getLaunchUrl?.()?.then((launch) => {
      if (launch?.url) void completeFromUrl(String(launch.url));
    }).catch(() => undefined);
    return () => {
      removed = true;
      handle?.remove();
    };
  }, [completeFromUrl]);

  const openSystemBrowser = useCallback(async () => {
    setError(null);
    const url = `${window.location.origin}/auth/native/start`;
    try {
      const browserPlugin = window.Capacitor?.Plugins?.Browser;
      if (browserPlugin) await browserPlugin.open({ url });
      else window.open(url, "_blank");
    } catch {
      window.open(url, "_blank");
    }
  }, []);

  return (
    <div className="google-signin">
      <button
        type="button"
        onClick={() => void openSystemBrowser()}
        disabled={exchanging}
        data-testid="native-google-signin"
      >
        {exchanging ? "Finishing sign-in…" : "Continue with Google"}
      </button>
      <p className="muted">
        Google opens in your browser to sign you in, then returns you to
        the app automatically.
      </p>
      {error && <div className="error-box">{error}</div>}
    </div>
  );
}
