# garmin-claude-sync

Pull **your own** Garmin Connect data to your Mac via a real, logged-in browser
session, summarize it so it's light enough to hand to Claude, and store it
locally. Phase 1 (this repo) is on-demand and fully automatic once you're
logged in. It's structured so an automated, API-driven phase bolts on later.

## Why it works this way

The old Python libraries (`garth`, `python-garminconnect`) broke in early 2026:
Garmin changed their login flow and Cloudflare now blocks known HTTP-library
fingerprints. A **real browser** isn't blocked. So this drives a persistent
Chromium profile -- you log in by hand once (including MFA), the session is
saved, and every later run reuses it headlessly. Data is then fetched by
calling Garmin's own JSON endpoints under `connect.garmin.com/gc-api/` through
the browser session, authenticated by its cookies -- exactly as the web app
does. No password and no token ever touch the code.

The endpoint paths are stable and baked in -- there is **no discovery or
clicking step**. One `pull` fetches activities and every day's sleep, HRV,
training readiness, and daily stats *concurrently* in a single pass.

## Install (Python 3.11+)

```bash
cd garmin-claude-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Use it

```bash
# One time: a Chromium window opens; sign into Garmin yourself (incl. MFA).
python -m garmin_sync login

# Any time after: fetch everything for the range, summarize, export.
python -m garmin_sync pull --since 14d
```

That's it. Each pull writes `data/garmin_YYYY-MM-DD.json` -- a compact bundle of
activity and daily summaries. Drag it into a Claude chat or Project and ask
things like *"how's my training load trending vs last week, and is recovery
keeping up?"* History also accumulates in `data/garmin.db` (SQLite), which the
future automated phase will read from.

Flags: `--since 30d` or `--since 2026-06-01`, `--full` to keep raw data instead
of summarizing, `--show` to watch the browser during a pull, `--limit N` for how
many recent activities to scan.

## Desktop app (no terminal needed)

Prefer clicking to typing? Double-click **`Garmin Sync.command`** in this folder.
The first launch sets up the environment automatically; after that it opens a
small window with everything you need:

- **Log in** — one-time visible sign-in (email, password, MFA). Reused after.
- **Fill the blank → today** — the main button. It looks at the last day already
  stored and fetches only what's missing, up to today. No dates to think about.
- **Fetch the last N days** — pull a specific recent range instead.
- **Combine full history → 1 file** — writes `data/garmin_history.json`, a single
  bundle of your entire local history (every activity + every day) to drag into
  Claude.
- **Watch the browser** — optional; shows the Chromium window during a fetch.

The window shows your last-pulled date and how much history is stored, and logs
each step. It wraps the same engine as the CLI (`garmin_sync/core.py`), so both
stay in sync.

You can also launch it from a terminal with `python -m garmin_sync.app`.

If your Python was built without Tk (common with Homebrew Python — you'd see
`No module named '_tkinter'`), the launcher automatically falls back to an
identical **local web interface** instead: it opens the same controls in your
browser at `http://127.0.0.1:<port>/`. Nothing extra to install; it's pure
standard library. Launch it directly with `python -m garmin_sync.webapp`.

## What "summarize" means

Garmin returns megabytes of per-second streams. By default each activity is
trimmed to the metrics that matter (type, duration, distance, HR, pace,
training effect, load) and each day collapses to one compact record (sleep
stages + score, resting HR, HRV, Body Battery, stress, steps, readiness). A
single run's payload drops from ~80 KB of raw streams to a few hundred bytes.
Use `--full` if you ever want the raw data.

## If something returns an error

`pull` prints any endpoints that failed (e.g. `some endpoints returned: 401`).
Auth is via your session cookies (same as the Garmin web app -- no token). A
401 means the saved session went stale: run `python -m garmin_sync login
--fresh` to wipe it and log in clean. If one specific metric consistently
fails, Garmin may have moved that path; run `python -m garmin_sync doctor` to
see the live paths and reconcile `garmin_sync/endpoints.py`.

## Honest caveats

- **Fragile by nature.** This rides Garmin's web app. When they change the site
  or tighten Cloudflare, a pull may break until you re-login. The official
  Garmin account data export (Settings -> Data Management) is the
  zero-maintenance fallback that never breaks.
- **Session is IP-bound.** Cloudflare clearance is tied to your IP; if it
  changes, you may need to `login` again.
- **Terms of service.** This accesses your own data (GDPR Article 20 gives you a
  portability right to it), but automated access still runs against Garmin's
  developer terms. Keep request volume low and personal.

## What changes in the future automated phase

Two swaps, both already isolated in the code: a `launchd`/cron job replaces you
running `pull`, and a small module reads `garmin.db` and calls the Anthropic API
to produce a daily briefing instead of you dragging JSON into chat.

## Layout

```
garmin_sync/
  auth.py        persistent-profile login + session reuse + token extraction
  client.py      authenticated, concurrent JSON fetch through the browser page
  endpoints.py   Garmin endpoint paths (baked in, stable)
  summarize.py   drop heavy streams, roll up daily wellness
  store.py       SQLite + compact JSON export
  cli.py         login / pull
data/            output (gitignored)
```
