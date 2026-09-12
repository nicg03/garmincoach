"""
Garmin Sync -- a minimalistic local web app (no Tk, no extra dependencies).

Some Python builds (e.g. Homebrew's) ship without Tk, so the desktop window
can't open. This uses only the standard library's http.server to serve a small
page in your default browser. It wraps the exact same engine (garmin_sync.core).

Run it with:  python3 -m garmin_sync.webapp
The launcher picks this automatically when Tk is unavailable.

One long job runs at a time in a background thread; the page polls for new log
lines and refreshes status when the job finishes.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import core

# ---- shared job state -------------------------------------------------------
_lock = threading.Lock()
_state = {"busy": False, "log": [], "done_msg": "", "error": ""}


def _reset_log():
    _state["log"] = []
    _state["done_msg"] = ""
    _state["error"] = ""


def _start_job(fn, done_msg: str) -> bool:
    """Start a job if idle. Returns False if one is already running."""
    with _lock:
        if _state["busy"]:
            return False
        _state["busy"] = True
        _reset_log()

    def worker():
        try:
            fn(lambda m: _state["log"].append(m))
            _state["done_msg"] = done_msg
        except Exception as e:  # noqa: BLE001 - surfaced to the browser
            _state["error"] = str(e)
            _state["log"].append("✗ " + str(e))
        finally:
            _state["busy"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


# ---- page -------------------------------------------------------------------
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Garmin Sync</title>
<style>
  :root{--bg:#f5f6f8;--card:#fff;--ink:#1c2530;--muted:#8b95a1;
        --accent:#2f6df6;--accent2:#2457cc;--line:#e6e9ee;--ok:#1f9d5b;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",Arial,sans-serif;}
  .wrap{max-width:600px;margin:0 auto;padding:28px 20px 40px;}
  h1{font-size:26px;margin:0 0 2px;}
  .sub{color:var(--muted);font-size:13px;margin:0 0 18px;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:16px;margin-bottom:14px;}
  .last{font-size:15px;font-weight:600;}
  .counts{color:var(--muted);font-size:13px;margin-top:4px;}
  button{font:inherit;border-radius:10px;cursor:pointer;border:1px solid var(--line);}
  button:disabled{opacity:.5;cursor:default;}
  .primary{width:100%;padding:14px;font-size:16px;font-weight:700;color:#fff;
           background:var(--accent);border:none;margin-bottom:12px;}
  .primary:hover:not(:disabled){background:var(--accent2);}
  .row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px;}
  .row .muted{color:var(--muted);font-size:14px;}
  .ghost{padding:9px 14px;background:var(--card);color:var(--ink);}
  .ghost:hover:not(:disabled){background:#eef1f6;}
  input[type=number]{width:64px;padding:8px;border:1px solid var(--line);
        border-radius:8px;font:inherit;text-align:center;}
  input[type=text],input[type=password]{flex:1;min-width:0;padding:9px;
        border:1px solid var(--line);border-radius:8px;font:inherit;}
  .opt{color:var(--muted);font-size:13px;margin:2px 0 14px;display:flex;
       align-items:center;gap:6px;}
  #log{background:#0f141b;color:#cdd6e3;border-radius:12px;padding:14px;
       font:12px/1.5 Menlo,Consolas,monospace;height:200px;overflow:auto;
       white-space:pre-wrap;}
  .footer{color:var(--muted);font-size:12px;margin-top:10px;min-height:16px;}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid #c9d2e0;
        border-top-color:var(--accent);border-radius:50%;vertical-align:-2px;
        animation:sp .7s linear infinite;margin-right:6px;}
  @keyframes sp{to{transform:rotate(360deg)}}
</style></head>
<body><div class="wrap">
  <h1>Garmin Sync</h1>
  <p class="sub">Pull your Garmin data to this folder.</p>

  <div class="card">
    <div class="last" id="last">Last data: …</div>
    <div class="counts" id="counts">History: …</div>
  </div>

  <button class="primary" id="btn-sync" onclick="run('sync')">Update &nbsp;&amp;&nbsp; publish to my site</button>

  <div class="row">
    <span class="muted">or just fetch, without publishing</span>
    <button class="ghost" id="btn-fill" onclick="run('fill')" style="margin-left:auto">Fill the blank</button>
  </div>

  <div class="row">
    <span class="muted">or fetch the last</span>
    <input type="number" id="days" value="14" min="1" max="365">
    <span class="muted">days</span>
    <button class="ghost" id="btn-fetch" onclick="runFetch()" style="margin-left:auto">Fetch</button>
  </div>

  <div class="row">
    <button class="ghost" id="btn-history" onclick="run('history')">Combine full history &rarr; 1 file</button>
    <button class="ghost" id="btn-login" onclick="run('login')" style="margin-left:auto">Log in</button>
  </div>

  <div class="card">
    <div class="last">My site</div>
    <div class="counts" id="site-state">Not configured yet.</div>
    <div class="counts">Paste the site address, then Connect — a browser opens so you can approve. No token to copy.</div>
    <div class="row" style="margin-top:10px">
      <input type="text" id="site-url" placeholder="https://your-app.up.railway.app">
      <button class="ghost" id="btn-save-site" onclick="linkSite()">Connect</button>
    </div>
    <div class="row">
      <button class="ghost" id="btn-push" onclick="run('push')">Publish the last 30 days</button>
      <button class="ghost" id="btn-pushall" onclick="run('push?all=1')" style="margin-left:auto">Publish everything</button>
    </div>
  </div>

  <label class="opt"><input type="checkbox" id="watch"> Watch the browser while fetching</label>

  <div id="log"></div>
  <div class="footer" id="footer">Ready</div>
</div>
<script>
let polling=false;
const BUTTONS=['btn-sync','btn-fill','btn-fetch','btn-history','btn-login',
               'btn-push','btn-pushall','btn-save-site'];
const $=id=>document.getElementById(id);
function setBusy(b){for(const id of BUTTONS)$(id).disabled=b;}
async function refresh(){
  const s=await (await fetch('/api/status')).json();
  $('last').textContent = s.last
     ? `Last data: ${s.last}  (${s.ago})`
     : 'Last data: none yet — Log in, then Fill the blank';
  $('counts').textContent = `History: ${s.activities} activities · ${s.days} days stored`;
  $('site-url').value = s.site_url || '';
  $('site-state').textContent = s.site_url
     ? (s.site_token ? `Publishing to ${s.site_url}` : 'Address saved — click Connect to finish linking.')
     : 'Not configured yet — paste the address and click Connect.';
}
function watch(){return $('watch').checked?1:0;}
async function linkSite(){
  const url=$('site-url').value.trim();
  if(!url){$('footer').textContent='Enter the site address.';return;}
  await fetch('/api/site',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url})});
  const r=await (await fetch('/api/link',{method:'POST'})).json();
  if(r.ok){$('log').textContent='';startPoll();}
  else $('footer').textContent=r.msg||'Busy';
}
async function run(kind){
  let url='/api/'+kind;
  if(kind==='fill'||kind==='login'||kind==='history'||kind==='sync')
    url+=(url.includes('?')?'&':'?')+'watch='+watch();
  const r=await (await fetch(url,{method:'POST'})).json();
  if(r.ok){$('log').textContent='';startPoll();}
  else $('footer').textContent=r.msg||'Busy';
}
async function runFetch(){
  const d=parseInt($('days').value||'14',10);
  const r=await (await fetch(`/api/fetch?days=${d}&watch=${watch()}`,{method:'POST'})).json();
  if(r.ok){$('log').textContent='';startPoll();}
}
async function startPoll(){
  if(polling)return; polling=true; setBusy(true);
  $('footer').innerHTML='<span class="spin"></span>Working…';
  const t=setInterval(async()=>{
    const s=await (await fetch('/api/log')).json();
    $('log').textContent=s.log.join('\\n'); $('log').scrollTop=$('log').scrollHeight;
    if(!s.busy){
      clearInterval(t); polling=false; setBusy(false);
      $('footer').textContent = s.error ? ('Error: '+s.error) : (s.done||'Done');
      refresh();
    }
  },500);
}
refresh();
</script>
</body></html>"""


# ---- request handling -------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence default stderr access log
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            return body if isinstance(body, dict) else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self):
        p = urlparse(self.path)
        if p.path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if p.path == "/api/status":
            last = core.last_pulled_date()
            counts = core.db_counts()
            ago = ""
            if last:
                gap = (date.today() - date.fromisoformat(last)).days
                ago = "today" if gap == 0 else ("yesterday" if gap == 1 else f"{gap} days ago")
            site = core.load_config()
            return self._json({"last": last, "ago": ago, "busy": _state["busy"],
                               "site_url": site.get("site_url", ""),
                               "site_token": bool(site.get("sync_token")),
                               **counts})
        if p.path == "/api/log":
            return self._json({"log": _state["log"], "busy": _state["busy"],
                               "done": _state["done_msg"], "error": _state["error"]})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        headless = q.get("watch", ["0"])[0] != "1"

        if p.path == "/api/site":
            body = self._read_json()
            if not body.get("url"):
                return self._json({"ok": False, "msg": "Enter the site address."}, 400)
            core.save_config(site_url=body["url"],
                             sync_token=body.get("token") or None)
            return self._json({"ok": True})

        if p.path == "/api/link":
            cfg = core.load_config()
            url = cfg.get("site_url")
            if not url:
                return self._json({"ok": False, "msg": "Save the site address first."}, 400)
            ok = _start_job(lambda log: core.link(url=url, log=log),
                            "Computer connected")
            return self._json({"ok": ok, "msg": "" if ok else "A job is already running."})

        if p.path == "/api/login":
            ok = _start_job(lambda log: core.login(fresh=False, log=log), "Login complete")
        elif p.path == "/api/fill":
            ok = _start_job(lambda log: core.fill_the_blank(headless=headless, log=log),
                            "Filled up to today")
        elif p.path == "/api/sync":
            ok = _start_job(lambda log: core.sync(headless=headless, log=log),
                            "Fetched and published")
        elif p.path == "/api/push":
            since = "all" if q.get("all", ["0"])[0] == "1" else None
            ok = _start_job(lambda log: core.push(since=since, log=log), "Published")
        elif p.path == "/api/history":
            ok = _start_job(lambda log: core.export_history(log=log), "Combined history written")
        elif p.path == "/api/fetch":
            try:
                n = max(1, int(q.get("days", ["14"])[0]))
            except ValueError:
                return self._json({"ok": False, "msg": "bad days"}, 400)
            since = date.today() - timedelta(days=n - 1)
            ok = _start_job(lambda log: core.pull(since=since, headless=headless, log=log),
                            f"Fetched last {n} days")
        else:
            return self._send(404, "not found", "text/plain")

        return self._json({"ok": ok, "msg": "" if ok else "A job is already running."})


def main():
    import socket

    # find a free port on localhost
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Garmin Sync running at {url}")
    print("Leave this window open while you use the app. Press Ctrl+C to quit.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
