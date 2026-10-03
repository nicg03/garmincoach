# Privacy

gepard.fit Sync talks to two places: Garmin Connect (in the tab you already signed into) and the gepard.fit site you connect it to.

- Your Garmin password never enters the extension. Requests use the session cookie the browser already has.
- The sync token that identifies your site account is stored in `chrome.storage.local` on this computer. Disconnecting the extension deletes it.
- To recognise your site, the popup reads the address of the active tab and asks that address's `/api/config` whether it is a gepard.fit site. Nothing is sent to it until you click Connect.
- Training and wellness data is sent only to the site address you connect. The extension does not send it anywhere else, and does not sell it.
- Optional host permission is requested only for that site, so the extension cannot publish to an address you did not approve.
