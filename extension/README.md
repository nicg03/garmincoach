# Garmin Coach Sync

A Chrome extension that publishes your Garmin Connect data to your own Garmin Coach site. It runs inside the Garmin tab you are already signed into, so there is no extra login, no MFA to repeat, and no Python to install.

## Install (while it's not on the Chrome Web Store)

1. Download `garmin-coach-extension.zip` from your site (`/extension.zip`), or use the `extension/` folder in this repo.
2. Unzip it if you downloaded the zip.
3. Open `chrome://extensions`.
4. Turn on **Developer mode**.
5. Click **Load unpacked** and choose the folder that contains `manifest.json`.

Works the same in Edge, Brave and Arc.

## Use it

1. Sign in to [Garmin Connect](https://connect.garmin.com) in this browser.
2. Click the extension icon, paste your site address (for example `https://your-app.up.railway.app` or `http://127.0.0.1:8000`), and click **Connect**.
3. Approve the link in the tab that opens.
4. Click **Sync now**. After that it refreshes by itself every few hours.

For years of history, request Garmin's official export (Account → Data Management → Export Your Data) and drop the zip on the site instead. The extension then keeps the dashboard current.
