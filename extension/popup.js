/**
 * The popup. It holds no logic of its own: it reads state from the service
 * worker, sends it four or five commands, and renders whatever comes back.
 */

const el = (id) => document.getElementById(id);

const nodes = {
  account: el("account"),
  setup: el("setup"),
  main: el("main"),
  siteUrl: el("site-url"),
  link: el("link"),
  sync: el("sync"),
  backfill: el("backfill"),
  auto: el("auto"),
  last: el("last-value"),
  total: el("total-value"),
  status: el("status"),
  error: el("error"),
  openSite: el("open-site"),
  unlink: el("unlink"),
};

let siteUrl = "";

async function ask(type, extra = {}) {
  const reply = await chrome.runtime.sendMessage({ type, ...extra });
  if (reply?.error) throw new Error(reply.error);
  return reply || {};
}

function showError(message) {
  nodes.error.textContent = message || "";
}

function ago(iso) {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

function renderStatus(status) {
  if (!status) {
    nodes.status.textContent = "";
    nodes.status.className = "status";
    return;
  }
  if (status.state === "running") {
    nodes.status.textContent = `${status.step}…`;
    nodes.status.className = "status";
    return;
  }
  if (status.state === "error") {
    nodes.status.textContent = "";
    showError(status.message);
    return;
  }
  if (status.state === "done") {
    const parts = [`${status.activities} activities`, `${status.days} days`];
    nodes.status.textContent = `Published ${parts.join(", ")}.`;
    nodes.status.className = "status ok";
    if (status.failures) {
      showError(
        `${status.failures} endpoint${status.failures === 1 ? "" : "s"} didn't ` +
          "answer. That's normal for metrics your watch doesn't record."
      );
    }
  }
}

function setBusy(busy) {
  nodes.sync.disabled = busy;
  nodes.backfill.disabled = busy;
  nodes.sync.textContent = busy ? "Syncing…" : "Sync now";
}

function render(state) {
  siteUrl = state.siteUrl || "";
  const linked = Boolean(siteUrl && state.syncToken);

  nodes.setup.classList.toggle("hidden", linked);
  nodes.main.classList.toggle("hidden", !linked);

  if (!linked) {
    nodes.account.textContent = "Not connected";
    return;
  }

  let host = siteUrl;
  try {
    host = new URL(siteUrl).host;
  } catch {
    // A stored value that won't parse still reads fine as-is.
  }
  nodes.account.textContent = state.email ? `${state.email} · ${host}` : host;
  nodes.auto.checked = Boolean(state.autoSync);
  nodes.last.textContent = ago(state.lastSync?.at);
  const total = state.lastSync?.total;
  nodes.total.textContent = total
    ? `${total.activities} activities · ${total.days} days`
    : "—";

  setBusy(Boolean(state.running));
  renderStatus(state.status);
}

async function refresh() {
  try {
    render(await ask("sync:state"));
  } catch (error) {
    showError(error.message);
  }
}

/**
 * Host access for the site is requested here rather than declared in the
 * manifest. The site's address is different for everyone, so the alternative
 * would be asking for every https site up front -- which is both a worse
 * install prompt and more access than this needs.
 */
async function requestSiteAccess(url) {
  const origin = `${new URL(url).origin}/*`;
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) {
    throw new Error("Without access to your site the extension can't publish to it.");
  }
}

nodes.link.addEventListener("click", async () => {
  showError("");
  const url = nodes.siteUrl.value.trim();
  if (!/^https?:\/\/.+/.test(url)) {
    showError("Enter the full address, including https://");
    return;
  }
  nodes.link.disabled = true;
  nodes.link.textContent = "Waiting for approval…";
  try {
    await requestSiteAccess(url);
    await ask("sync:link", { siteUrl: url });
    await refresh();
  } catch (error) {
    showError(error.message);
  } finally {
    nodes.link.disabled = false;
    nodes.link.textContent = "Connect";
  }
});

nodes.sync.addEventListener("click", async () => {
  showError("");
  setBusy(true);
  try {
    await ask("sync:run", { options: {} });
  } catch (error) {
    showError(error.message);
  } finally {
    await refresh();
  }
});

nodes.backfill.addEventListener("click", async () => {
  showError("");
  setBusy(true);
  nodes.backfill.textContent = "Loading history…";
  try {
    // Several pages of the activity list and a wider day window. Garmin is
    // rate-limited, so this is a bigger bite rather than everything at once;
    // for years of history the export import on the site is the fast path.
    await ask("sync:history");
  } catch (error) {
    showError(error.message);
  } finally {
    nodes.backfill.textContent = "Load all history";
    await refresh();
  }
});

nodes.auto.addEventListener("change", async () => {
  try {
    await ask("sync:setAuto", { value: nodes.auto.checked });
  } catch (error) {
    showError(error.message);
  }
});

nodes.openSite.addEventListener("click", () => {
  if (siteUrl) chrome.tabs.create({ url: siteUrl });
});

nodes.unlink.addEventListener("click", async () => {
  try {
    await ask("sync:unlink");
    await refresh();
  } catch (error) {
    showError(error.message);
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "sync:status") {
    renderStatus(message.status);
    setBusy(message.status?.state === "running");
  }
});

refresh();
