/* Aegis service worker — the installability requirement for the store
   apps (Play TWA) and the home-screen PWA, kept deliberately narrow.

   What it does:
   - navigations: network-first, falling back to the last cached app shell
     so the installed app opens (with its saved-checkpoint recovery UI)
     even when the network drops;
   - hashed build assets (/assets/*): cache-first — a hash change IS a new
     URL, so stale entries can never be served for a new build;
   - icons + manifest: stale-while-revalidate.

   What it deliberately does NOT touch: every API call, upload, NDJSON
   progress stream, and journal poll — anything not matched above passes
   straight through to the network untouched. Runs and reattach behavior
   are exactly the browser-tab behavior. */

const CACHE = "aegis-shell-v1";
const SHELL_KEY = "/__app_shell__";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((name) => name !== CACHE).map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const fresh = await fetch(request);
          if (fresh.ok) {
            const cache = await caches.open(CACHE);
            cache.put(SHELL_KEY, fresh.clone());
          }
          return fresh;
        } catch (error) {
          const cached = await caches.match(SHELL_KEY);
          if (cached) return cached;
          throw error;
        }
      })(),
    );
    return;
  }

  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      (async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        const fresh = await fetch(request);
        if (fresh.ok) {
          const cache = await caches.open(CACHE);
          cache.put(request, fresh.clone());
        }
        return fresh;
      })(),
    );
    return;
  }

  if (
    url.pathname.startsWith("/icons/")
    || url.pathname === "/manifest.webmanifest"
  ) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(CACHE);
        const cached = await cache.match(request);
        const refresh = fetch(request)
          .then((fresh) => {
            if (fresh.ok) cache.put(request, fresh.clone());
            return fresh;
          })
          .catch(() => cached);
        return cached || refresh;
      })(),
    );
  }
  // Everything else — API, streams, uploads — is not intercepted.
});
