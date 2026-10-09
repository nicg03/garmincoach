# gepard.fit mobile

Capacitor 7 app for iOS and Android. The UI is built from `../server/static`;
there is no second frontend to keep in sync. Production packages contain those
assets and call `https://gepard.fit` for API data.

## Prerequisites

- Node 20+
- Xcode 16+ and CocoaPods on macOS
- Android Studio with Android SDK 35 and JDK 21
- Apple Developer and Google Play Console accounts for signed releases

## Build

```bash
cd mobile
npm ci
npm run sync
npm run check
```

Open a native project with `npm run open:ios` or `npm run open:android`.
`npm run build:android` creates the release bundle after signing is configured.
The iOS archive is produced from Xcode.

`npm run build` deletes and recreates `www/`, copies the shared web source and
bundles `src/native.ts`. Never edit `www/` or the copies under native `public`
directories by hand.

## Authentication

The browser continues to use its HttpOnly cookie. The bundled app sends
`X-Gepard-Client: capacitor`; successful account operations return a signed,
30-day session stored in the iOS Keychain or Android Keystore-backed encrypted
storage. API calls send it as a Bearer token. Password changes and “sign out
other devices” rotate the account session version and return a replacement.

## App links

The native projects claim only `https://gepard.fit/`, used by email links whose
route is in the URL fragment. Privacy, support and pairing pages remain in the
browser.

Before TestFlight:

1. Set Railway `APPLE_TEAM_ID` to the 10-character Apple Developer Team ID.
2. Confirm `https://gepard.fit/.well-known/apple-app-site-association`.

Before Play internal testing:

1. Create/choose the release upload key.
2. Obtain its SHA-256 fingerprint:
   `keytool -list -v -keystore release.jks -alias <alias>`.
3. Set Railway `ANDROID_APP_LINK_SHA256` to that fingerprint. Multiple
   comma-separated fingerprints are accepted for key rotation.
4. Confirm `https://gepard.fit/.well-known/assetlinks.json`.

## Versioning and signing

- Keep `package.json` version, Android `versionName` and iOS
  `MARKETING_VERSION` aligned.
- Increment Android `versionCode` and iOS `CURRENT_PROJECT_VERSION` for every
  upload.
- Do not commit `.jks`, `.keystore`, `local.properties`, provisioning profiles
  or store API keys.
- Use Play App Signing and automatic Apple signing for the first release.

## Store submission checklist

- Public privacy URL: `https://gepard.fit/privacy`
- Support URL: `https://gepard.fit/support`
- Bundle/application ID: `fit.gepard.app`
- Contact: `support@gepard.fit`
- Prepare phone screenshots in English and Italian.
- Complete Apple App Privacy and Google Data safety for email, health/fitness
  data, user content (coach chat/notes), identifiers and diagnostics.
- Explain in review notes that Garmin credentials are transmitted once to the
  gepard.fit server, never stored, and that users can use an official export.
- Demonstrate native daily reminders, app links, secure session storage,
  offline state and account deletion to the reviewer.

## Device smoke test

On one real iPhone and one real Android device:

1. Sign up, kill/reopen, sign out and sign back in.
2. Open verification/reset links from email.
3. Connect Garmin, including MFA; trigger a sync.
4. Open all charts and stream a coach reply.
5. Import a small export ZIP through the system file picker.
6. Schedule, receive and tap the daily reminder.
7. Disable network, restore it, and check the offline banner.
8. Change password, revoke other sessions and delete a test account.

## Current local-machine requirements

Capacitor can generate both native projects without signing. A real iOS build
still requires the full Xcode application (Command Line Tools alone are not
enough). Android builds require JDK 21; older JDKs cannot run the current
Android Gradle plugin.
