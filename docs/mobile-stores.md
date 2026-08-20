# Shipping Aegis to the Play Store and the App Store

The deployed web app (https://projectaegis.fly.dev) is the product. Both
store apps are thin shells around it, so **every feature update ships by
deploying the site — no store re-submission** (store review is needed
only for the shells themselves: first submission, icon/name changes,
native-plugin changes).

Three layers, built in this order:

1. **PWA layer** (already in the repo, ships with the normal deploy):
   `frontend/public/manifest.webmanifest`, `frontend/public/sw.js`,
   icons under `frontend/public/icons/`, service-worker registration in
   `src/main.tsx`. This alone makes the site installable from the
   browser ("Add to Home Screen") on any phone — usable by anyone
   today, before either store listing exists.
2. **Native sign-in** (already in the repo): Google blocks its Identity
   Services sign-in inside app WebViews, so the store apps sign in
   through the system browser — `backend/app/api/native_auth.py`, the
   `aegis://auth` deep link, and the native branch in
   `frontend/src/Auth.tsx`.
3. **Store shells** (scaffolded in `mobile/`, built on your machines):
   Play = Trusted Web Activity via Bubblewrap; App Store = Capacitor
   iOS shell.

---

## 0. One-time accounts and costs

| Store | Account | Cost | Needs |
| --- | --- | --- | --- |
| Play Store | Google Play Console developer account | $25 one-time | any OS with Android SDK (Android Studio) |
| App Store | Apple Developer Program | $99/year | a Mac with Xcode |

## 1. Server configuration (do this first)

Set these on the deployment (Fly: `fly secrets set …`; never commit
secrets):

| Variable | What | Where to get it |
| --- | --- | --- |
| `AEGIS_GOOGLE_CLIENT_SECRET` | Enables `/auth/native/*` (routes answer 404 without it) | Google Cloud Console → APIs & Services → Credentials → your existing OAuth *Web application* client → "Client secret" |
| `AEGIS_PUBLIC_BASE_URL` | Public origin used in OAuth redirects (already used for asset URLs) | `https://projectaegis.fly.dev` |
| `AEGIS_ANDROID_PACKAGE_NAME` | Play app id served in assetlinks.json | default `school.up.aegis` — override only if you pick a different id |
| `AEGIS_ANDROID_CERT_SHA256` | Signing-cert fingerprint(s) served in assetlinks.json, comma-separated | step 3 below (Play App Signing key + upload key) |

Also in the Google Cloud OAuth client, add to **Authorized redirect
URIs**: `https://projectaegis.fly.dev/auth/native/callback`.

Verify after deploy:

- `https://projectaegis.fly.dev/auth/native/start` redirects to a
  Google sign-in page (404 means the secret isn't set).
- `https://projectaegis.fly.dev/.well-known/assetlinks.json` returns
  your fingerprints (empty list until step 3).
- `https://projectaegis.fly.dev/manifest.webmanifest` loads, and Chrome
  on Android offers "Install app" from the ⋮ menu.

## 2. How the native sign-in works (for review answers and debugging)

    app button → system browser: GET /auth/native/start
      → Google consent (normal browser, GIS-permitted)
      → GET /auth/native/callback  (server exchanges the code)
      → 302 aegis://auth?ticket=…  (OS routes the scheme to the app)
    app: POST /auth/native/exchange {ticket}
      → normal 12h session cookie in the app's WebView

The ticket is a standard Aegis session value with a 90-second expiry and
single-use guard — one token authority, no parallel format.

## 3. Play Store (Trusted Web Activity, `mobile/twa/`)

Prereqs: Node 18+, Java 17, Android SDK (installing Android Studio is
easiest; Bubblewrap can also download its own).

```bash
cd mobile/twa
npx @bubblewrap/cli doctor          # checks JDK/SDK
npx @bubblewrap/cli update          # regenerates the project from twa-manifest.json
npx @bubblewrap/cli build           # prompts to create ./android.keystore on first run
```

`build` produces `app-release-bundle.aab` (upload this) and an APK for
local testing (`adb install app-release-signed.apk`).

Fingerprints → server:

```bash
keytool -list -v -keystore android.keystore -alias aegis | grep SHA256
```

Upload the `.aab` in Play Console → your app → Production. Play
Console almost certainly re-signs with **Play App Signing**: copy the
"App signing key certificate" SHA-256 from Play Console → Setup → App
integrity, and set BOTH fingerprints, comma-separated:

```bash
fly secrets set AEGIS_ANDROID_CERT_SHA256="<play-app-signing-sha256>,<upload-key-sha256>"
```

Then re-check `/.well-known/assetlinks.json`. If verification fails the
app still works but shows a browser address bar — that is always an
assetlinks/fingerprint mismatch.

Listing requirements to have ready: privacy-policy URL, screenshots
(phone + 7" tablet), 512×512 icon (use
`frontend/public/icons/icon-512.png`), feature graphic 1024×500.

## 4. App Store (Capacitor shell, `mobile/capacitor/`)

On a Mac with Xcode:

```bash
cd mobile/capacitor
npm install
npx cap add ios
```

Register the deep-link scheme — in Xcode (`npx cap open ios`), target
**App** → Info → URL Types → add one with URL Schemes `aegis`
(equivalently, `CFBundleURLTypes` with `CFBundleURLSchemes: [aegis]` in
Info.plist). Without this the sign-in redirect never reaches the app.

Then: set the signing team, bump the display name/icon if Xcode didn't
pick them up (App Icon source: use `frontend/public/icons/icon-512.png`
via an asset catalog), run on a device to test, and archive → upload
via Xcode Organizer. Submit through App Store Connect.

**Honest risk — App Review Guideline 4.2 (minimum functionality):**
Apple sometimes rejects apps that are "just a website in a shell". A
utility used by a real organization with sign-in, uploads, and
long-running jobs has a reasonable case, but rejection is possible.
Mitigations if it happens: describe the app's workflow (long-running
generation runs, background journaling, review/edit flows) in the
review notes; add a native touch or two (push notifications for run
completion is the natural one); or distribute iOS via TestFlight /
"Add to Home Screen" (the PWA) while the listing is argued. Budget for
one rejection-and-resubmit cycle.

App updates: because the shell loads `server.url`, deploying the site
updates the app. Only shell changes (icon, plugins, scheme) need a new
build and review.

## 5. Testing checklist (both platforms)

- [ ] Install, cold start, sign in with a @up.school account — lands on
      the app signed in (system browser opened and returned).
- [ ] Kill the app after sign-in, reopen: still signed in (cookie
      persisted; 12h TTL, then the expired-session gate appears).
- [ ] Start a run, background the app 2+ minutes, return: console
      catches up silently from the journal (same contract as the
      browser tab).
- [ ] Airplane mode, open app: the shell/offline page appears rather
      than a raw error; going online and reopening recovers.
- [ ] Android only: no browser address bar (assetlinks verified).
- [ ] Ticket replay: completing sign-in twice from one link fails
      cleanly with "already used" (expected; user just signs in again).
