import type { CapacitorConfig } from "@capacitor/cli";

// The iOS (App Store) shell. The app IS the deployed site: `server.url`
// points the WebView at production, so shipping an update to the site
// updates the app with no store re-submission. `webDir` holds only a
// placeholder page shown if the site is unreachable at cold start.
//
// Sign-in: Google blocks its Identity Services flow in WebViews, so the
// app signs in through the system browser and the aegis:// deep link —
// the scheme is registered in Info.plist (CFBundleURLTypes, scheme
// "aegis"); see docs/mobile-stores.md step by step.
const config: CapacitorConfig = {
  appId: "school.up.aegis",
  appName: "Aegis",
  webDir: "www",
  server: {
    url: "https://projectaegis.fly.dev",
    // The session cookie lives on the site origin; nothing else may be
    // treated as app-internal navigation.
    allowNavigation: ["projectaegis.fly.dev"],
  },
};

export default config;
