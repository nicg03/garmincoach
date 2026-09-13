/**
 * The content script: the only code that talks to Garmin.
 *
 * It runs inside connect.garmin.com, so every request it makes is same-origin
 * and carries your real session. That is the whole reason this extension
 * exists: no separate login, no MFA to repeat, no Cloudflare challenge, and
 * no dependency on your home IP address. The headless-browser approach fought
 * all four of those; here they simply don't arise.
 *
 * It deliberately knows almost nothing. It can find the CSRF token the Garmin
 * web app puts in the page, and it can fetch a list of paths that somebody
 * else chose. Which endpoints exist and which dates are missing is decided by
 * the server, because changing this file means waiting on a Chrome Web Store
 * review and changing the server does not.
 */

// Garmin's own web app sends this header on every gc-api call and 403s
// without it. It is per-session and rotates, so it is read from the page
// rather than stored anywhere.
const DEFAULT_HEADERS = {
  accept: "application/json, text/plain, */*",
  "x-requested-with": "XMLHttpRequest",
  nk: "NT",
};

// Politeness, not performance. Garmin rate-limits, and a sync of a month is
// a couple of hundred requests, so it goes in small batches with a gap.
const BATCH_SIZE = 3;
const BATCH_PAUSE_MS = 400;
const RETRY_STATUSES = new Set([429, 500, 502, 503, 504]);

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta?.content) return meta.content;
  // Some builds of the app keep it in web storage instead of the document.
  for (const store of [window.localStorage, window.sessionStorage]) {
    try {
      for (const key of Object.keys(store)) {
        if (!/csrf/i.test(key)) continue;
        const value = store.getItem(key);
        if (value) return value.replace(/^"|"$/g, "");
      }
    } catch {
      // Storage can be blocked; the meta tag is the normal path anyway.
    }
  }
  return null;
}

function headers() {
  const token = csrfToken();
  return token
    ? { ...DEFAULT_HEADERS, "connect-csrf-token": token }
    : { ...DEFAULT_HEADERS };
}

/**
 * One request, with a single retry on the failures that are worth retrying.
 *
 * Errors are returned rather than thrown: a sync touches dozens of endpoints
 * and some of them fail routinely because your watch doesn't record that
 * metric. The server drops `__error` markers, so one dead endpoint never
 * costs the whole sync.
 */
async function fetchOne(url, requestHeaders) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      const response = await fetch(url, {
        credentials: "include",
        headers: requestHeaders,
      });
      if (response.ok) {
        const text = await response.text();
        if (!text) return null;
        try {
          return JSON.parse(text);
        } catch {
          // An HTML body here means the session lapsed and Garmin answered
          // with the sign-in page instead of JSON.
          return { __error: "non-json" };
        }
      }
      if (RETRY_STATUSES.has(response.status) && attempt < 3) {
        await sleep(1500 * (attempt + 1));
        continue;
      }
      return { __error: response.status };
    } catch (error) {
      if (attempt < 3) {
        await sleep(600 * (attempt + 1));
        continue;
      }
      return { __error: "fetch-failed", __detail: String(error) };
    }
  }
  return { __error: "fetch-failed" };
}

function absolute(base, path) {
  // The server sends root-relative paths. Anything else is refused: this is
  // the one place where a bad answer from the site could aim requests
  // somewhere unintended, and it costs one line to make that impossible.
  if (typeof path !== "string" || !path.startsWith("/") || path.includes("..")) {
    return null;
  }
  return base.replace(/\/$/, "") + path;
}

async function fetchSpecs(base, specs) {
  const requestHeaders = headers();
  const results = {};
  for (let i = 0; i < specs.length; i += BATCH_SIZE) {
    const batch = specs.slice(i, i + BATCH_SIZE);
    await Promise.all(
      batch.map(async ({ label, path }) => {
        const url = absolute(base, path);
        if (!url) {
          results[label] = { __error: "bad-path" };
          return;
        }
        results[label] = await fetchOne(url, requestHeaders);
      })
    );
    if (i + BATCH_SIZE < specs.length) await sleep(BATCH_PAUSE_MS);
  }
  return results;
}

/**
 * Write side, used from Phase 1 onwards: create, update, schedule and
 * unschedule workouts. The payload is built by the server and sent through
 * unchanged, so the translation to Garmin's workout format stays in Python
 * where it can be tested.
 */
async function sendOne(base, { method, path, body }) {
  const url = absolute(base, path);
  if (!url) return { __error: "bad-path" };
  const requestHeaders = { ...headers(), "content-type": "application/json" };
  try {
    const response = await fetch(url, {
      method: method || "POST",
      credentials: "include",
      headers: requestHeaders,
      body: body === undefined || body === null ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    if (!response.ok) {
      return { __error: response.status, __detail: text.slice(0, 300) };
    }
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch {
      return { __error: "non-json" };
    }
  } catch (error) {
    return { __error: "fetch-failed", __detail: String(error) };
  }
}

// Reloading the unpacked extension (or a programmatic inject) can run this
// file twice in the same tab. A second listener would answer every fetch
// twice and look like a hung sync.
if (!globalThis.__garminCoachSync) {
  globalThis.__garminCoachSync = true;
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (!message || typeof message.type !== "string") return false;

    const handlers = {
      "garmin:ping": async () => ({ ready: true, csrf: Boolean(csrfToken()) }),
      "garmin:fetch": async () => ({
        results: await fetchSpecs(message.base, message.specs || []),
      }),
      "garmin:send": async () => ({ result: await sendOne(message.base, message) }),
    };

    const handler = handlers[message.type];
    if (!handler) return false;
    handler()
      .then(respond)
      .catch((error) => respond({ error: String(error) }));
    // Keeps the message channel open for the async reply.
    return true;
  });
}
