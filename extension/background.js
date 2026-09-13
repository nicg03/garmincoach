/**
 * The service worker: the part that decides when things happen.
 *
 * It owns the sequence and nothing else. Garmin is reached only through the
 * content script, and what to ask Garmin for is decided by the site. So a
 * sync reads:
 *
 *   1. make sure a connect.garmin.com tab exists (opening a background one if
 *      needed, and closing it again afterwards)
 *   2. get the Garmin profile from that tab
 *   3. hand the profile to the site, which answers with a list of paths
 *   4. fetch them all through the tab
 *   5. post the raw answers to the site, which summarizes and stores them
 *
 * Step 3 is why adding a metric later needs no new version of this extension.
 */

const GARMIN_ORIGIN = "https://connect.garmin.com";
const GARMIN_HOME = `${GARMIN_ORIGIN}/modern/`;

const SYNC_ALARM = "garmin-coach-sync";
const SYNC_PERIOD_MINUTES = 240;

// How long to wait for the content script in a freshly opened tab.
const TAB_READY_TIMEOUT_MS = 45000;
const TAB_POLL_MS = 500;

// A service worker is stopped when it looks idle. A sync is mostly waiting on
// the network, so a cheap API call on a timer keeps it resident until done.
let keepAliveTimer = null;

function startKeepAlive() {
  if (keepAliveTimer) return;
  keepAliveTimer = setInterval(() => chrome.runtime.getPlatformInfo(), 20000);
}

function stopKeepAlive() {
  if (!keepAliveTimer) return;
  clearInterval(keepAliveTimer);
  keepAliveTimer = null;
}

// ---- stored state ----------------------------------------------------------
async function getConfig() {
  return chrome.storage.local.get({
    siteUrl: "",
    syncToken: "",
    email: "",
    autoSync: true,
    lastSync: null,
    status: null,
  });
}

async function setConfig(values) {
  await chrome.storage.local.set(values);
}

async function setStatus(status) {
  await setConfig({ status });
  // The popup is usually closed, and that is not an error.
  chrome.runtime.sendMessage({ type: "sync:status", status }).catch(() => {});
}

// ---- talking to the site ---------------------------------------------------
function siteUrlFor(base, path) {
  return `${String(base).replace(/\/$/, "")}${path}`;
}

async function siteFetch(base, path, { method = "GET", body, token } = {}) {
  const response = await fetch(siteUrlFor(base, path), {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.error || payload?.detail || text.slice(0, 200);
    const hint =
      response.status === 401
        ? " Reconnect the extension to your site."
        : response.status === 404
        ? " Check the site address."
        : "";
    throw new Error(`The site answered ${response.status}.${hint} ${detail}`.trim());
  }
  return payload ?? {};
}

// ---- talking to Garmin through a tab ---------------------------------------
async function findGarminTab() {
  const tabs = await chrome.tabs.query({ url: `${GARMIN_ORIGIN}/*` });
  return tabs.find((tab) => tab.id !== undefined) || null;
}

async function talk(tabId, message) {
  return chrome.tabs.sendMessage(tabId, message);
}

async function injectContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["garmin.js"],
    });
  } catch {
    // Wrong origin (SSO, interstitial) — the ping loop still times out with
    // a useful error.
  }
}

async function waitForContentScript(tabId) {
  const deadline = Date.now() + TAB_READY_TIMEOUT_MS;
  let lastError = null;
  let injected = false;
  while (Date.now() < deadline) {
    try {
      const reply = await talk(tabId, { type: "garmin:ping" });
      if (reply?.ready) return reply;
    } catch (error) {
      lastError = error;
      if (!injected) {
        injected = true;
        await injectContentScript(tabId);
      }
    }
    await new Promise((done) => setTimeout(done, TAB_POLL_MS));
  }
  throw new Error(
    "Couldn't reach Garmin Connect in the browser. Open connect.garmin.com, " +
      `sign in, and try again. ${lastError ? `(${lastError.message})` : ""}`.trim()
  );
}

/**
 * A Garmin tab to work through, reusing one you already have open.
 *
 * When none is open a background tab is created, which is what makes the
 * scheduled sync invisible. `created` is returned so the caller can close it
 * again and leave the browser as it found it.
 */
async function ensureGarminTab() {
  const existing = await findGarminTab();
  if (existing) {
    await waitForContentScript(existing.id);
    return { tabId: existing.id, created: false };
  }
  const tab = await chrome.tabs.create({ url: GARMIN_HOME, active: true });
  try {
    await waitForContentScript(tab.id);
  } catch (error) {
    // Not signed in lands on sso.garmin.com, where the content script never
    // runs. Showing the tab puts the user exactly where they need to be.
    await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
    throw error;
  }
  return { tabId: tab.id, created: true };
}

async function releaseTab({ tabId, created }) {
  if (created) await chrome.tabs.remove(tabId).catch(() => {});
}

function fetchFailed(value) {
  return value === null || value === undefined ||
    (typeof value === "object" && "__error" in value);
}

/**
 * Split the fetch plan into short round-trips.
 *
 * One `garmin:fetch` of a whole month used to hang the content-script
 * message port (or blow past Chrome's message size cap). Same-day labels
 * stay together so a later ingest cannot overwrite a day with a partial
 * row.
 */
function chunkSpecs(specs, maxSize = 12) {
  const chunks = [];
  let current = [];
  let currentDay = null;
  const dayOf = (label) => {
    const parts = String(label || "").split("::");
    return parts.length > 1 ? parts[1] : "";
  };
  for (const spec of specs) {
    const day = dayOf(spec.label);
    if (current.length >= maxSize && day !== currentDay) {
      chunks.push(current);
      current = [];
    }
    current.push(spec);
    currentDay = day;
  }
  if (current.length) chunks.push(current);
  return chunks;
}

async function fetchAndPublish(tabId, siteUrl, token, base, specs) {
  if (!specs.length) {
    return { stored: { stored: { activities: 0, days: 0 }, activities: 0, days: 0 }, failures: 0, results: {} };
  }
  let stored = null;
  let failures = 0;
  let done = 0;
  const allResults = {};
  for (const slice of chunkSpecs(specs)) {
    done += slice.length;
    await setStatus({
      state: "running",
      step: `Fetching Garmin data (${done}/${specs.length})`,
      total: specs.length,
      done,
    });
    const reply = await talk(tabId, {
      type: "garmin:fetch",
      base,
      specs: slice,
    });
    if (reply?.error) throw new Error(reply.error);
    const results = reply?.results || {};
    Object.assign(allResults, results);
    failures += Object.values(results).filter(fetchFailed).length;
    stored = await siteFetch(siteUrl, "/api/ingest", {
      method: "POST",
      token,
      body: { source: "extension", results },
    });
  }
  return { stored, failures, results: allResults };
}

// ---- the sync ---------------------------------------------------------------
let syncRunning = false;

async function runSync({ full = false, pages = 1, days } = {}) {
  if (syncRunning) throw new Error("A sync is already running.");
  syncRunning = true;
  startKeepAlive();
  let tab = null;
  try {
    const { siteUrl, syncToken } = await getConfig();
    if (!siteUrl || !syncToken) {
      throw new Error("Connect the extension to your site first.");
    }
    await setStatus({ state: "running", step: "Opening Garmin Connect" });
    tab = await ensureGarminTab();

    await setStatus({ state: "running", step: "Reading your Garmin profile" });
    const profilePath = "/userprofile-service/userprofile/userProfileBase";
    const profileReply = await talk(tab.tabId, {
      type: "garmin:fetch",
      base: `${GARMIN_ORIGIN}/gc-api`,
      specs: [{ label: "profile", path: profilePath }],
    });
    if (profileReply?.error) throw new Error(profileReply.error);
    const profile = profileReply?.results?.profile;
    if (fetchFailed(profile)) {
      throw new Error(
        "Garmin didn't accept the request. Open connect.garmin.com, make sure " +
          "you're signed in, then try again."
      );
    }

    await setStatus({ state: "running", step: "Asking the site what's missing" });
    const plan = await siteFetch(siteUrl, "/api/sync/fetchplan", {
      method: "POST",
      token: syncToken,
      body: { profile, full, pages, days: days || 90 },
    });

    const specs = plan.specs || [];
    const { stored, failures } = await fetchAndPublish(
      tab.tabId, siteUrl, syncToken, plan.base, specs
    );

    await setStatus({ state: "running", step: "Writing planned workouts to Garmin" });
    const written = await applyOutbox(tab.tabId, siteUrl, syncToken);

    const lastSync = {
      at: new Date().toISOString(),
      activities: stored?.stored?.activities ?? 0,
      days: stored?.stored?.days ?? 0,
      total: { activities: stored?.activities ?? 0, days: stored?.days ?? 0 },
      through: stored?.last ?? null,
      failures,
      written: written.length,
    };
    await setConfig({ lastSync });
    await setStatus({ state: "done", ...lastSync });
    return lastSync;
  } catch (error) {
    await setStatus({ state: "error", message: error.message });
    throw error;
  } finally {
    if (tab) await releaseTab(tab);
    syncRunning = false;
    stopKeepAlive();
  }
}

function countActivities(results) {
  return Object.entries(results || {}).reduce((n, [key, value]) => (
    key.startsWith("activities::") && Array.isArray(value) ? n + value.length : n
  ), 0);
}

/**
 * Walk Garmin's activity list and daily wellness into the past until Garmin
 * runs out (or we hit five years of sleep/HRV). Incremental sync only keeps
 * the last month; this is what actually fills the site.
 */
async function runHistory() {
  if (syncRunning) throw new Error("A sync is already running.");
  syncRunning = true;
  startKeepAlive();
  let tab = null;
  try {
    const { siteUrl, syncToken } = await getConfig();
    if (!siteUrl || !syncToken) {
      throw new Error("Connect the extension to your site first.");
    }
    await setStatus({ state: "running", step: "Opening Garmin Connect" });
    tab = await ensureGarminTab();

    await setStatus({ state: "running", step: "Reading your Garmin profile" });
    const profileReply = await talk(tab.tabId, {
      type: "garmin:fetch",
      base: `${GARMIN_ORIGIN}/gc-api`,
      specs: [{
        label: "profile",
        path: "/userprofile-service/userprofile/userProfileBase",
      }],
    });
    if (profileReply?.error) throw new Error(profileReply.error);
    const profile = profileReply?.results?.profile;
    if (fetchFailed(profile)) {
      throw new Error(
        "Garmin didn't accept the request. Open connect.garmin.com, make sure " +
          "you're signed in, then try again."
      );
    }

    let activityStart = 0;
    let moreActivities = true;
    let stored = null;
    let failures = 0;
    const pageSize = 8;

    for (let round = 0; round < 80; round += 1) {
      await setStatus({
        state: "running",
        step: moreActivities
          ? `Loading history (pass ${round + 1})`
          : `Loading older days (pass ${round + 1})`,
      });
      const plan = await siteFetch(siteUrl, "/api/sync/fetchplan", {
        method: "POST",
        token: syncToken,
        body: {
          profile,
          backfill: true,
          pages: moreActivities ? pageSize : 0,
          days: 45,
          activity_start: activityStart,
          meta: false,
        },
      });
      const specs = plan.specs || [];
      if (!specs.length) break;

      const published = await fetchAndPublish(
        tab.tabId, siteUrl, syncToken, plan.base, specs
      );
      stored = published.stored;
      failures += published.failures || 0;
      const got = countActivities(published.results);
      const budget = moreActivities ? pageSize * 50 : 0;
      if (got < budget) moreActivities = false;
      else activityStart += got;

      if ((!plan.days || !plan.days.length) && !moreActivities) break;
      await new Promise((done) => setTimeout(done, 400));
    }

    const lastSync = {
      at: new Date().toISOString(),
      activities: stored?.stored?.activities ?? 0,
      days: stored?.stored?.days ?? 0,
      total: { activities: stored?.activities ?? 0, days: stored?.days ?? 0 },
      through: stored?.last ?? stored?.first ?? null,
      failures,
      written: 0,
    };
    await setConfig({ lastSync });
    await setStatus({ state: "done", ...lastSync });
    return lastSync;
  } catch (error) {
    await setStatus({ state: "error", message: error.message });
    throw error;
  } finally {
    if (tab) await releaseTab(tab);
    syncRunning = false;
    stopKeepAlive();
  }
}

async function applyOutbox(tabId, siteUrl, token) {
  const { ops } = await siteFetch(siteUrl, "/api/sync/outbox", { token });
  if (!ops || !ops.length) return [];

  const workoutIds = {};
  const acks = [];
  for (const op of ops) {
    let path = op.path;
    if ((op.op === "schedule" || op.ref === "schedule") && !path) {
      const workoutId = workoutIds[op.session_id];
      if (!workoutId) {
        acks.push({ session_id: op.session_id, error: "No workout id to schedule" });
        continue;
      }
      path = `/workout-service/schedule/${workoutId}`;
    }
    const reply = await talk(tabId, {
      type: "garmin:send",
      base: `${GARMIN_ORIGIN}/gc-api`,
      method: op.method,
      path,
      body: op.body,
    });
    const result = reply?.result || {};
    const ack = { session_id: op.session_id };
    if (result.__error) {
      ack.error = String(result.__detail || result.__error);
    } else if (op.op === "create_workout" || op.op === "update_workout" || op.ref === "workout") {
      const id = result.workoutId || result.workoutIdDTO || result.workout?.workoutId;
      if (id) {
        workoutIds[op.session_id] = id;
        ack.garmin_workout_id = id;
      }
    } else if (op.op === "schedule" || op.ref === "schedule") {
      if (result.workoutScheduleId) ack.garmin_schedule_id = result.workoutScheduleId;
    }
    acks.push(ack);
  }
  if (acks.length) {
    await siteFetch(siteUrl, "/api/sync/ack", {
      method: "POST",
      token,
      body: { results: acks },
    });
  }
  return acks;
}

// ---- linking to the site ---------------------------------------------------
/**
 * The same device-pairing dance the command-line tool uses, so the site needs
 * no new endpoint: ask for a code, let the signed-in owner approve it in a
 * tab, and receive the sync token once. Nobody copies a token by hand.
 */
async function linkSite(rawUrl) {
  const siteUrl = String(rawUrl || "").trim().replace(/\/$/, "");
  if (!/^https?:\/\/.+/.test(siteUrl)) {
    throw new Error("Enter the full address of your site, including https://");
  }

  startKeepAlive();
  try {
    const begin = await siteFetch(siteUrl, "/api/pair/begin", { method: "POST", body: {} });
    const { device_code: deviceCode, user_code: userCode } = begin;
    if (!deviceCode || !userCode) throw new Error("The site didn't start a pairing.");

    const tab = await chrome.tabs.create({ url: `${siteUrl}/pair/${userCode}` });
    const interval = Math.max(1, Number(begin.interval) || 2) * 1000;
    const deadline = Date.now() + (Number(begin.expires_in) || 600) * 1000;

    while (Date.now() < deadline) {
      await new Promise((done) => setTimeout(done, interval));
      let poll;
      try {
        poll = await siteFetch(siteUrl, "/api/pair/poll", {
          method: "POST",
          body: { device_code: deviceCode },
        });
      } catch (error) {
        if (error.message.includes("404")) {
          throw new Error("That pairing expired. Try connecting again.");
        }
        throw error;
      }
      if (poll.status === "ready") {
        await setConfig({
          siteUrl,
          syncToken: poll.sync_token,
          email: poll.email || "",
        });
        // Chrome closes the popup when this tab opened, so the popup never
        // starts the first sync. Do it here, and send the user to the
        // dashboard so they see data as it lands.
        await chrome.tabs.update(tab.id, { url: siteUrl, active: true }).catch(() => {});
        await scheduleSync();
        runSync({ pages: 2, days: 90 })
          .then(() => runHistory())
          .catch(() => {});
        return { siteUrl, email: poll.email || "" };
      }
    }
    throw new Error("Timed out waiting for approval. Try connecting again.");
  } finally {
    if (!syncRunning) stopKeepAlive();
  }
}

async function unlinkSite() {
  await chrome.storage.local.clear();
  await chrome.alarms.clear(SYNC_ALARM);
}

// ---- schedule --------------------------------------------------------------
async function scheduleSync() {
  const { autoSync } = await getConfig();
  await chrome.alarms.clear(SYNC_ALARM);
  if (!autoSync) return;
  await chrome.alarms.create(SYNC_ALARM, {
    delayInMinutes: 1,
    periodInMinutes: SYNC_PERIOD_MINUTES,
  });
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== SYNC_ALARM) return;
  const { siteUrl, syncToken, autoSync } = await getConfig();
  if (!autoSync || !siteUrl || !syncToken) return;
  // A scheduled sync that fails is not worth shouting about: the status is
  // recorded and the next one is four hours away.
  await runSync().catch(() => {});
});

chrome.runtime.onInstalled.addListener(() => scheduleSync());
chrome.runtime.onStartup.addListener(() => scheduleSync());

// ---- popup messages --------------------------------------------------------
chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (!message || typeof message.type !== "string") return false;

  const handlers = {
    "sync:state": async () => {
      const config = await getConfig();
      return { ...config, running: syncRunning };
    },
    "sync:run": async () => ({ lastSync: await runSync(message.options || {}) }),
    "sync:history": async () => ({ lastSync: await runHistory() }),
    "sync:link": async () => ({ linked: await linkSite(message.siteUrl) }),
    "sync:unlink": async () => {
      await unlinkSite();
      return { ok: true };
    },
    "sync:setAuto": async () => {
      await setConfig({ autoSync: Boolean(message.value) });
      await scheduleSync();
      return { ok: true };
    },
  };

  const handler = handlers[message.type];
  if (!handler) return false;
  handler()
    .then(respond)
    .catch((error) => respond({ error: error.message || String(error) }));
  return true;
});
