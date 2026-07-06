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

  <button class="primary" id="btn-fill" onclick="run('fill')">Fill the blank &nbsp;→&nbsp; today</button>

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

  <label class="opt"><input type="checkbox" id="watch"> Watch the browser while fetching</label>

  <div id="log"></div>
  <div class="footer" id="footer">Ready</div>
</div>
<script>
let polling=false;
const $=id=>document.getElementById(id);
function setBusy(b){for(const id of ['btn-fill','btn-fetch','btn-history','btn-login'])$(id).disabled=b;}
async function refresh(){
  const s=await (await fetch('/api/status')).json();
  $('last').textContent = s.last
     ? `Last data: ${s.last}  (${s.ago})`
     : 'Last data: none yet — Log in, then Fill the blank';
  $('counts').textContent = `History: ${s.activities} activities · ${s.days} days stored`;
}
function watch(){return $('watch').checked?1:0;}
async function run(kind){
  let url='/api/'+kind;
  if(kind==='fill'||kind==='login'||kind==='history') url+='?watch='+watch();
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
            return self._json({"last": last, "ago": ago, "busy": _state["busy"],
                               **counts})
        if p.path == "/api/log":
            return self._json({"log": _state["log"], "busy": _state["busy"],
                               "done": _state["done_msg"], "error": _state["error"]})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        p = urlparse(self.path)
        q = parse_qs(p.query)
        headless = q.get("watch", ["0"])[0] != "1"

        if p.path == "/api/login":
            ok = _start_job(lambda log: core.login(fresh=False, log=log), "Login complete")
        elif p.path == "/api/fill":
            ok = _start_job(lambda log: core.fill_the_blank(headless=headless, log=log),
                            "Filled up to today")
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
