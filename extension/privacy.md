# Privacy

Garmin Coach Sync talks to two places: Garmin Connect (in the tab you already signed into) and the Garmin Coach site you connect it to.

- Your Garmin password never enters the extension. Requests use the session cookie the browser already has.
- The sync token that identifies your site account is stored in `chrome.storage.local` on this computer. Disconnecting the extension deletes it.
- Training and wellness data is sent only to the site address you paste in. The extension does not send it anywhere else, and does not sell it.
- Optional host permission is requested only for that site, so the extension cannot publish to an address you did not approve.
