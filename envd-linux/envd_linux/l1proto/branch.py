"""PROTOTYPE of L1 mechanism B (shared browser branching): ONE Chromium per host, each session attached as its own PROFILE.
See docs/l1-shared-runtime-design.md section 10 (pre-registration) and its results section.

The isolation unit here is a Chromium profile, which is weaker than process isolation: the browser, network service, GPU process and
debugging endpoint are shared by every attached session, and the browser's windows live in ONE host-level X display (a shared
browser cannot draw into per-session X servers).

This module: a minimal CDP client (stdlib websocket), `SharedBrowser`, and a test HTTP server."""

import base64
import hashlib
import http.server
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.request

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
FLAGS = ["--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*"]


# ---- minimal CDP over websocket (RFC 6455, client side, text frames) ----
class WS:
    def __init__(self, url, timeout=10):
        assert url.startswith("ws://")
        host, _, path = url[5:].partition("/")
        h, _, p = host.partition(":")
        self.s = socket.create_connection((h, int(p or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake closed")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n")[0]:
            raise ConnectionError(resp.split(b"\r\n")[0].decode())
        self.buf = resp.split(b"\r\n\r\n", 1)[1]
        self.n = 0

    def _read(self, k):
        while len(self.buf) < k:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            msg += payload
            if b0 & 0x80:
                return json.loads(msg) if (b0 & 0x0F) in (1, 2) else None

    def call(self, method, params=None, timeout=15):
        self.n += 1
        self.send({"id": self.n, "method": method, "params": params or {}})
        end = time.time() + timeout
        self.s.settimeout(timeout)
        while time.time() < end:
            m = self.recv()
            if m and m.get("id") == self.n:
                return m
        raise TimeoutError(method)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def http_json(host, port, path, timeout=3):
    with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def evaluate(ws_url, expr, timeout=15):
    """Runtime.evaluate in one target, awaiting promises; returns the JSON value."""
    ws = WS(ws_url, timeout)
    try:
        r = ws.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}, timeout)
    finally:
        ws.close()
    res = r.get("result", {})
    if "exceptionDetails" in res:
        raise RuntimeError(res["exceptionDetails"].get("text", "evaluate failed"))
    return res.get("result", {}).get("value")


class SharedBrowser:
    """One headful Chromium on a host Xvfb; `attach(name)` opens a window of profile `name` in it."""

    def __init__(self, tpl_profile, root, display=":199", port=9333, listen="0.0.0.0"):
        self.tpl, self.root, self.display, self.port, self.listen = tpl_profile, root, display, port, listen
        self.ud = os.path.join(root, "ud")
        self.xvfb = self.proc = None
        self.attached = {}

    def _env(self):
        return dict(os.environ, DISPLAY=self.display)

    def start(self):
        shutil.rmtree(self.ud, ignore_errors=True)
        shutil.copytree(self.tpl, self.ud, symlinks=True)
        self.xvfb = subprocess.Popen(["Xvfb", self.display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        self.proc = subprocess.Popen(
            [CHROME, *FLAGS, f"--user-data-dir={self.ud}", f"--remote-debugging-port={self.port}",
             f"--remote-debugging-address={self.listen}", "--window-size=1280,800", "about:blank"],
            env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while True:
            try:
                http_json("127.0.0.1", self.port, "/json/version")
                break
            except Exception:
                if time.time() - t0 > 30:
                    raise RuntimeError("shared browser did not come up")
                time.sleep(0.05)
        return time.time() - t0

    def targets(self, host="127.0.0.1"):
        return http_json(host, self.port, "/json/list")

    def prepare_profile(self, name):
        dst = os.path.join(self.ud, name)
        shutil.copytree(os.path.join(self.ud, "Default"), dst, symlinks=True, dirs_exist_ok=False)

    def attach(self, name, wait=True, poll=0.01, timeout=20, copy=True):
        """Copy the template profile, hand off to the running browser, wait for the page title. Returns timings."""
        t0 = time.time()
        if copy:
            self.prepare_profile(name)
        t_copy = time.time()
        url = f"data:text/html,<title>ready-{name}</title><h1>{name}</h1>"
        subprocess.run([CHROME, *FLAGS, f"--user-data-dir={self.ud}", f"--profile-directory={name}", url],
                       env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        t_handoff = time.time()
        tid = None
        while wait and time.time() - t0 < timeout:
            for t in self.targets():
                if t.get("type") == "page" and t.get("title") == f"ready-{name}":
                    tid = t["id"]
                    break
            if tid:
                break
            time.sleep(poll)
        t_ready = time.time()
        self.attached[name] = tid
        return {"copy_s": t_copy - t0, "handoff_s": t_handoff - t_copy, "ready_s": t_ready - t0, "target": tid}

    def close_target(self, tid):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/close/{tid}", timeout=3).read()
        except Exception:
            pass

    def process_types(self):
        """Chromium children rewrite their process title, so /proc/<pid>/cmdline can be one space-joined string: tokenise it."""
        import re
        out = {}
        for p in os.listdir("/proc"):
            if not p.isdigit():
                continue
            try:
                cmd = " ".join(open(f"/proc/{p}/cmdline", "rb").read().decode(errors="replace").split("\0")).strip()
            except OSError:
                continue
            if "chrome-linux/chrome" not in cmd.split(" ")[0] or "crashpad" in cmd.split(" ")[0]:
                continue
            m = re.search(r"--type=(\S+)", cmd)
            if not m and f"--user-data-dir={self.ud}" not in cmd:
                continue
            kind = m.group(1) if m else "browser"
            sub = re.search(r"--utility-sub-type=(\S+)", cmd)
            out.setdefault(kind + (f":{sub.group(1).split('.')[-1]}" if sub else ""), []).append(int(p))
        return out

    def stop(self):
        for pr in (self.proc, self.xvfb):
            if pr is not None:
                try:
                    pr.terminate()
                    pr.wait(10)
                except Exception:
                    try:
                        pr.kill()
                    except Exception:
                        pass
        # renderer/gpu children exit with the browser; remove stragglers that carry OUR user-data-dir
        for kind, pids in self.process_types().items():
            for pid in pids:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass


class TestServer:
    """Host HTTP server for the isolation tests: cookies, a cacheable resource, request log with client ports."""

    def __init__(self, port=0):
        self.log, self.cached_hits, self.lock = [], 0, threading.Lock()
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                with outer.lock:
                    outer.log.append({"path": self.path, "cookie": self.headers.get("Cookie", ""),
                                      "client_port": self.client_address[1], "t": time.time()})
                    if self.path.startswith("/cached"):
                        outer.cached_hits += 1
                body = json.dumps({"hits": outer.cached_hits}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if self.path.startswith("/cached"):
                    self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
