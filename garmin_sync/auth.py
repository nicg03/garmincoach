"""
Authentication via a real, persistent Chromium profile.

Strategy (this is the whole reason the project works in 2026):
  - The old Python libraries (garth, python-garminconnect) broke because they
    were HTTP clients, and Garmin's Cloudflare now blocks known HTTP-library
    fingerprints. A real browser is not blocked.
  - So we drive a persistent Chromium profile. You log in ONCE by hand
    (including MFA) in a visible window. The session is saved to disk and
    reused on every later run, headless, with no password ever in the code.

Login is detected by the presence of a Garmin OAuth access token in
localStorage rather than by scraping the DOM, so it survives UI redesigns.
"""
from __future__ import annotations
import sys
import time
from contextlib import contextmanager
from pathlib import Path

CONNECT_HOME = "https://connect.garmin.com/modern/"

# Realistic desktop Chromium flags. Headed + persistent profile is what keeps
# Cloudflare happy; these just remove the most obvious automation tells.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-default-browser-check",
    "--no-first-run",
]

# JS that scans localStorage for a JWT-shaped access token. Garmin stores the
# token under a key whose name has changed over time, so we look at the values,
# not the key name.
_FIND_TOKEN_JS = """
() => {
  for (let i = 0; i < localStorage.length; i++) {
    const v = localStorage.getItem(localStorage.key(i));
    if (!v) continue;
    try {
      const o = JSON.parse(v);
      if (o && typeof o === 'object') {
        if (o.access_token) return o.access_token;
        if (o.accessToken) return o.accessToken;
      }
    } catch (e) {}
    if (typeof v === 'string' && v.startsWith('eyJ') && v.split('.').length === 3) return v;
  }
  return null;
}
"""


@contextmanager
def browser_session(profile_dir: Path, headless: bool = False):
    """Yield a logged-in Playwright page using a persistent profile.

    Lazy-imports Playwright so the rest of the CLI works without it installed.
    """
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=LAUNCH_ARGS,
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield page
        finally:
            ctx.close()


def get_access_token(page) -> str | None:
    try:
        return page.evaluate(_FIND_TOKEN_JS)
    except Exception:
        return None


def is_logged_in(page) -> bool:
    return bool(get_access_token(page))


def ensure_logged_in(page, timeout_s: int = 300) -> str:
    """Make sure we have a valid session; prompt for manual login if not.

    Returns the access token. Raises TimeoutError if the user doesn't finish.
    """
    page.goto(CONNECT_HOME, wait_until="domcontentloaded")
    token = get_access_token(page)
    if token:
        return token

    print(
        "\n  A Chromium window just opened.\n"
        "  Log in to Garmin Connect there (email, password, and MFA if you use it).\n"
        "  This is the only time you'll need to do it by hand.\n"
        "  Waiting for you to finish...\n",
        file=sys.stderr,
    )
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        token = get_access_token(page)
        if token:
            print("  Logged in. Session saved.\n", file=sys.stderr)
            return token
        time.sleep(2)
    raise TimeoutError("Login was not completed in time.")

def get_live_token(page, timeout_s: int = 30) -> str | None:
    """Sniff the bearer token off a real request the Garmin web app makes.

    Far more reliable than reading localStorage: it is exactly the token Garmin
    is currently using, freshly minted, for the right audience. We just load the
    dashboard (which fires data requests) and grab the Authorization header.
    """
    holder = {"token": None}

    def on_request(req):
        if holder["token"]:
            return
        try:
            if "garmin.com" not in req.url:
                return
            authz = req.all_headers().get("authorization", "")
            if authz.lower().startswith("bearer "):
                holder["token"] = authz.split(" ", 1)[1]
        except Exception:
            pass

    page.on("request", on_request)
    try:
        page.goto(CONNECT_HOME, wait_until="domcontentloaded")
        deadline = time.time() + timeout_s
        reloaded = False
        while time.time() < deadline and not holder["token"]:
            time.sleep(0.5)
            if not holder["token"] and not reloaded and time.time() > deadline - timeout_s / 2:
                reloaded = True
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass
    return holder["token"]


def acquire_token(page) -> str:
    """Best available token: live-sniffed first, localStorage as fallback."""
    token = get_live_token(page)
    if token:
        return token
    token = get_access_token(page)
    if token:
        return token
    raise RuntimeError(
        "Logged in, but couldn't capture an access token. Try `login --fresh`."
    )

# Header keys the Garmin web app adds that the browser does NOT add automatically
# (cookie, user-agent, sec-* are added by the browser on an in-page fetch). The
# critical one is connect-csrf-token: the gc-api 403s requests without it.
APP_HEADER_KEYS = ("connect-csrf-token", "nk", "x-app-ver", "x-lang", "x-requested-with", "accept")


def capture_app_headers(page, timeout_s: int = 25) -> dict:
    """Sniff the app-specific headers (incl. the CSRF token) off a real gc-api
    request the Garmin web app makes, so we can replay them on our own calls."""
    found: dict = {}

    def on_request(req):
        if found.get("connect-csrf-token"):
            return
        try:
            if "/gc-api/" not in req.url:
                return
            h = req.all_headers()
            if "connect-csrf-token" in h:
                for k in APP_HEADER_KEYS:
                    if k in h:
                        found[k] = h[k]
        except Exception:
            pass

    page.on("request", on_request)
    try:
        page.goto(CONNECT_HOME, wait_until="domcontentloaded")
        deadline = time.time() + timeout_s
        reloaded = False
        while time.time() < deadline and not found.get("connect-csrf-token"):
            time.sleep(0.5)
            if not reloaded and time.time() > deadline - timeout_s / 2:
                reloaded = True
                try:
                    page.reload(wait_until="domcontentloaded")
                except Exception:
                    pass
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass
    return found
