"""
The API client.

Garmin's gc-api is cookie-authenticated AND CSRF-protected: every working
request carries a `connect-csrf-token` header (plus x-app-ver / x-lang) that the
web app reads and echoes. Requests without it get 403'd. We don't hardcode that
token -- it's per-session and rotates -- we sniff the app's own header set
(see auth.capture_app_headers) and replay it here. The fetch runs in-page and
same-origin, so the browser still supplies cookies, user-agent, and sec-* for
free; we only add the app-specific headers.
"""
from __future__ import annotations
from . import endpoints as ep

_DEFAULT_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "x-requested-with": "XMLHttpRequest",
    "nk": "NT",
}

_BATCH_JS = """
async ([urls, headers]) => {
  const out = {};
  await Promise.all(urls.map(async ([label, url]) => {
    try {
      const r = await fetch(url, { credentials:'include', headers });
      const text = await r.text();
      if (!r.ok) { out[label] = { __error: r.status }; return; }
      try { out[label] = JSON.parse(text); } catch (e) { out[label] = { __error:'non-json' }; }
    } catch (e) { out[label] = { __error:'fetch-failed' }; }
  }));
  return out;
}
"""

_ONE_JS = """
async ([url, headers]) => {
  try {
    const r = await fetch(url, { credentials:'include', headers });
    const text = await r.text();
    if (!r.ok) return { __error: r.status, __body: text.slice(0,200) };
    try { return JSON.parse(text); } catch (e) { return { __error:'non-json', __body: text.slice(0,200) }; }
  } catch (e) { return { __error:'fetch-failed', __body:String(e) }; }
}
"""


class GarminAPIError(RuntimeError):
    pass


class GarminClient:
    def __init__(self, page, base: str = ep.API_BASE, app_headers: dict | None = None):
        self.page = page
        self.base = base.rstrip("/")
        self.headers = {**_DEFAULT_HEADERS, **(app_headers or {})}
        self._display_name: str | None = None

    def _abs(self, path: str) -> str:
        return path if path.startswith("http") else self.base + path

    def get(self, path: str):
        result = self.page.evaluate(_ONE_JS, [self._abs(path), self.headers])
        if isinstance(result, dict) and "__error" in result:
            raise GarminAPIError(f"{result['__error']} for {path}: {result.get('__body','')}")
        return result

    def batch(self, specs: list[dict], chunk: int = 12) -> dict:
        out: dict = {}
        for i in range(0, len(specs), chunk):
            part = [[s["label"], self._abs(s["url"])] for s in specs[i:i + chunk]]
            out.update(self.page.evaluate(_BATCH_JS, [part, self.headers]))
        return out

    @property
    def display_name(self) -> str:
        if self._display_name is None:
            prof = self.get(ep.PROFILE)
            candidates = [prof.get(k) for k in
                          ("displayName", "profileId", "garminGUID", "userName", "id")]
            uuidish = [c for c in candidates if isinstance(c, str) and c.count("-") >= 4]
            self._display_name = (uuidish[0] if uuidish
                                  else next((c for c in candidates if c), None))
            if not self._display_name:
                raise GarminAPIError(f"No profile id found. Keys present: {list(prof)[:25]}")
        return str(self._display_name)

    def day_specs(self, date: str) -> list[dict]:
        dn = self.display_name
        return [
            {"label": f"daily::{date}", "url": ep.DAILY_SUMMARY.format(display_name=dn, date=date)},
            {"label": f"sleep::{date}", "url": ep.SLEEP.format(display_name=dn, date=date)},
            {"label": f"hrv::{date}", "url": ep.HRV.format(date=date)},
            {"label": f"readiness::{date}", "url": ep.TRAINING_READINESS.format(date=date)},
        ]

    def activities_spec(self, limit: int = 50) -> dict:
        return {"label": "activities", "url": ep.ACTIVITIES.format(limit=limit, start=0)}
