# Garmin Coach Sync

A Chrome extension that publishes your Garmin Connect data to your own Garmin Coach site. It runs inside the Garmin tab you are already signed into, so there is no extra login, no MFA to repeat, and no Python to install.

Most people don't need it any more: the site can connect to Garmin directly (Settings → Data sources → Connect Garmin). The extension stays as the fallback for when Garmin refuses sign-ins from the server.

## Install (while it's not on the Chrome Web Store)

1. Download `garmin-coach-extension.zip` from your site (`/extension.zip`), or use the `extension/` folder in this repo.
2. Unzip it if you downloaded the zip.
3. Open `chrome://extensions`.
4. Turn on **Developer mode**.
5. Click **Load unpacked** and choose the folder that contains `manifest.json`.

Works the same in Edge, Brave and Arc.

## Use it

1. Sign in to [Garmin Connect](https://connect.garmin.com) in this browser.
2. Open your Garmin Coach site, click the extension icon, and click **Connect to your-site**. The popup recognises the site in the active tab; on any other page you can still paste the address.
3. Approve the link in the tab that opens.
4. The first sync and the history backfill start by themselves. After that it refreshes every few hours in a background tab, without taking focus.

For years of history, request Garmin's official export (Account → Data Management → Export Your Data) and drop the zip on the site instead.

## Publishing to the Chrome Web Store

Listing it removes Developer mode and the zip from the install. What the review asks for:

- **Package**: zip the contents of this folder (the site's `/extension.zip` is already the right shape). Bump `version` in `manifest.json` for every upload.
- **Single purpose**: "Copies the signed-in user's own Garmin Connect training and wellness data to the Garmin Coach site they choose, and writes planned workouts back."
- **Permission justifications**:
  - `host_permissions: connect.garmin.com` — the content script reads Garmin's JSON through the user's own session.
  - `optional_host_permissions` — requested at connect time for the one site the user approves, never up front.
  - `tabs`, `scripting` — find or open a Garmin tab in the background and inject the content script after an extension reload.
  - `alarms` — the sync every four hours.
  - `storage` — the site address and sync token.
- **Privacy**: link [privacy.md](privacy.md) as the privacy policy, and declare "health and fitness data" and "authentication information (sync token)" in the data-usage form. Nothing is sold or used for unrelated purposes.
- **Screenshots**: the popup before and after connecting, 1280×800.
