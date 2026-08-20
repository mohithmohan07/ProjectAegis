# Aegis mobile store apps

Two thin shells around the deployed web app — the site is the product;
these wrap it for the stores:

- `twa/` — **Play Store** (Android): Bubblewrap Trusted Web Activity
  project. The app is the installed PWA, full screen, verified against
  the site via `/.well-known/assetlinks.json`.
- `capacitor/` — **App Store** (iOS): Capacitor shell whose WebView
  loads the deployed site (`server.url`), with native Google sign-in via
  the system browser and the `aegis://` deep link.

Neither build runs from this repository's CI — Android needs the Android
SDK and a signing keystore, iOS needs Xcode on a Mac and an Apple
Developer account. The complete step-by-step runbook, including store
accounts, signing, server environment variables, and review-policy
notes, is in **docs/mobile-stores.md**.
