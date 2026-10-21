"""Tiny local HTTP service that records form submits and visibility ticks.

The experiment checks results here. It never trusts the agent under test.
"""

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SCHEMA = """
CREATE TABLE IF NOT EXISTS rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    env INTEGER NOT NULL,
    gen INTEGER,
    tok TEXT
);
CREATE TABLE IF NOT EXISTS leases (
    env INTEGER NOT NULL,
    gen INTEGER NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vis (
    env INTEGER NOT NULL,
    count INTEGER NOT NULL,
    visible INTEGER NOT NULL,
    ts REAL NOT NULL
);
"""

# Each row is compared with the newest lease for the same env.
AUDIT_SQL = """
SELECT r.id, r.ts, r.kind, r.env, r.gen, r.tok
FROM rows r
JOIN leases l ON l.env = r.env
WHERE l.rowid = (SELECT MAX(rowid) FROM leases WHERE env = r.env)
  AND r.gen IS NOT l.gen
ORDER BY r.id
"""

ROW_COLUMNS = ("id", "ts", "kind", "env", "gen", "tok")


def _connect(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def audit(db_path):
    """Return rows whose gen disagrees with the latest lease for their env."""
    conn = _connect(db_path)
    try:
        found = conn.execute(AUDIT_SQL).fetchall()
    finally:
        conn.close()
    return [dict(zip(ROW_COLUMNS, row)) for row in found]


def _form_page(env):
    hue = (env * 47) % 360
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>form {env}</title></head>
<body style="background: hsl({hue},80%,60%); margin: 0; padding: 40px;">
<form method="POST" action="/form">
<input type="hidden" name="env" value="{env}">
<label>Token <input type="text" id="tok" name="tok"></label>
<button type="submit" id="go">Submit</button>
</form>
</body></html>
"""


def _vis_page(env):
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>vis {env}</title></head>
<body>
<div id="counter" style="font-size: 200px;">0</div>
<script>
const env = {env};
let count = 0;
function frame() {{
  if (document.visibilityState === "visible") {{
    count += 1;
    document.getElementById("counter").textContent = String(count);
  }}
  requestAnimationFrame(frame);
}}
requestAnimationFrame(frame);
function tick() {{
  fetch("/vis/tick", {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{
      env: env,
      count: count,
      visible: document.visibilityState === "visible"
    }})
  }});
}}
setInterval(tick, 5000);
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, status, body, content_type):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload, status=200):
        self._send(status, json.dumps(payload), "application/json")

    def _html(self, body, status=200):
        self._send(status, body, "text/html; charset=utf-8")

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode()

    def _int(self, query, name):
        return int(query[name][0])

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        try:
            self._route_get(url.path, query)
        except (KeyError, ValueError, IndexError):
            self._json({"error": "bad request"}, status=400)

    def _route_get(self, path, query):
        srv = self.server
        if path == "/form":
            self._html(_form_page(self._int(query, "env")))
        elif path == "/vis":
            self._html(_vis_page(self._int(query, "env")))
        elif path == "/lease":
            env, gen = self._int(query, "env"), self._int(query, "gen")
            with srv.lock:
                srv.conn.execute(
                    "INSERT INTO leases (env, gen, ts) VALUES (?, ?, ?)",
                    (env, gen, time.time()),
                )
                srv.conn.commit()
            self._json({"env": env, "gen": gen})
        elif path == "/rows":
            env = self._int(query, "env")
            with srv.lock:
                found = srv.conn.execute(
                    "SELECT id, ts, kind, env, gen, tok FROM rows WHERE env = ? ORDER BY id",
                    (env,),
                ).fetchall()
            self._json([dict(zip(ROW_COLUMNS, row)) for row in found])
        elif path == "/vis/count":
            env = self._int(query, "env")
            with srv.lock:
                latest = srv.conn.execute(
                    "SELECT count, visible, ts FROM vis WHERE env = ? "
                    "ORDER BY rowid DESC LIMIT 1",
                    (env,),
                ).fetchone()
            if latest is None:
                self._json({"count": 0, "visible": False, "ts": 0.0})
            else:
                self._json(
                    {"count": latest[0], "visible": bool(latest[1]), "ts": float(latest[2])}
                )
        elif path == "/audit":
            with srv.lock:
                found = srv.conn.execute(AUDIT_SQL).fetchall()
            self._json([dict(zip(ROW_COLUMNS, row)) for row in found])
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            if path == "/form":
                self._post_form(parse_qs(body))
            elif path == "/vis/tick":
                self._post_tick(json.loads(body))
            else:
                self._json({"error": "not found"}, status=404)
        except (KeyError, ValueError, IndexError, TypeError):
            self._json({"error": "bad request"}, status=400)

    def _post_form(self, fields):
        env = int(fields["env"][0])
        tok = fields["tok"][0]
        srv = self.server
        with srv.lock:
            if fields.get("gen", [""])[0] != "":
                gen = int(fields["gen"][0])
            else:
                # A browser form has no gen field. Use the newest lease.
                lease = srv.conn.execute(
                    "SELECT gen FROM leases WHERE env = ? ORDER BY rowid DESC LIMIT 1",
                    (env,),
                ).fetchone()
                gen = lease[0] if lease else None
            srv.conn.execute(
                "INSERT INTO rows (ts, kind, env, gen, tok) VALUES (?, 'form', ?, ?, ?)",
                (time.time(), env, gen, tok),
            )
            srv.conn.commit()
        self._html('<!doctype html><html><body><h1 id="ok">saved</h1></body></html>')

    def _post_tick(self, data):
        env, count, visible = int(data["env"]), int(data["count"]), bool(data["visible"])
        srv = self.server
        with srv.lock:
            srv.conn.execute(
                "INSERT INTO vis (env, count, visible, ts) VALUES (?, ?, ?, ?)",
                (env, count, int(visible), time.time()),
            )
            srv.conn.commit()
        self._json({"ok": True})


def make_server(db_path, host="127.0.0.1", port=7600):
    """Build the server. The caller runs serve_forever."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.conn = _connect(db_path)
    server.lock = threading.Lock()
    return server


if __name__ == "__main__":
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else "verify.db"
    make_server(db).serve_forever()
