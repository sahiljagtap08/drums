"""HTTP layer: one listener per session port, routes and refusals as in envd's Routes.swift.

Stdlib only. The platform lives behind `Backend`."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import VERSION, actionspec, framehash, png
from .backend import Backend, BackendError
from .guard import Generation

KNOWN_PATHS = {"/health", "/gen", "/screenshot", "/action", "/log", "/pb", "/launch", "/kill",
               "/windows", "/axtext"}
LOG_CAPACITY = 5000


class SessionState:
    """Per-session protocol state: generation, action log, last frame hash per region."""

    def __init__(self, env, port, backend: Backend, level="L0", clock=time.time):
        self.env, self.port, self.backend, self.level, self.clock = env, port, backend, level, clock
        self.generation = Generation(0)
        self.log = []
        self.log_lock = threading.Lock()
        self.last_hash = {}
        self.hash_lock = threading.Lock()

    def append_log(self, entry):
        with self.log_lock:
            self.log.append(entry)
            if len(self.log) > LOG_CAPACITY:
                del self.log[: len(self.log) - LOG_CAPACITY]

    def note_hash(self, key, h):
        with self.hash_lock:
            stale = self.last_hash.get(key) == h
            self.last_hash[key] = h
            return stale


def make_handler(state: SessionState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        # -- helpers
        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            for k, v in (headers or {"Content-Type": "application/json"}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, sort_keys=True).encode())

        def _err(self, status, code, detail=None):
            body = {"error": code}
            if detail is not None:
                body["detail"] = detail
            self._json(status, body)

        def _guard(self):
            """Local-only guard, applied to every route including reads and unknown paths."""
            if self.headers.get("Origin") is not None:
                self._json(403, {"error": "forbidden_origin"})
                return True
            host = (self.headers.get("Host") or "").lower()
            if host not in (f"127.0.0.1:{state.port}", f"localhost:{state.port}"):
                self._json(403, {"error": "forbidden_host"})
                return True
            return False

        def _body(self):
            raw_len = self.headers.get("Content-Length")
            try:
                n = int(raw_len or 0)
            except ValueError:
                n = -1
            if n < 0 or n > 1_000_000:
                return None, "bad Content-Length"
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {}, None
            try:
                return json.loads(raw), None
            except ValueError:
                return None, "invalid JSON"

        def _stale(self, current):
            self._json(409, {"error": "stale_gen", "current_gen": current})

        # -- dispatch
        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

        def _route(self, method):
            if self._guard():
                return
            url = urlparse(self.path)
            path, query = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
            table = {
                ("GET", "/health"): self.r_health, ("POST", "/gen"): self.r_gen,
                ("GET", "/screenshot"): self.r_screenshot, ("POST", "/action"): self.r_action,
                ("GET", "/log"): self.r_log, ("POST", "/launch"): self.r_launch,
                ("POST", "/kill"): self.r_kill, ("GET", "/windows"): self.r_windows,
                ("GET", "/axtext"): self.r_axtext,
                ("GET", "/pb"): self.r_pb, ("POST", "/pb"): self.r_pb, ("DELETE", "/pb"): self.r_pb,
            }
            fn = table.get((method, path))
            if fn is not None:
                try:
                    fn(query)
                except BrokenPipeError:
                    pass
                return
            if path in KNOWN_PATHS:
                self._err(405, "method_not_allowed")
            else:
                self._err(404, "not_found")

        # -- routes
        def r_health(self, q):
            w, h = state.backend.screen_size()
            self._json(200, {
                "env": state.env, "uid": os.getuid(), "gen": state.generation.current(),
                "on_console": True, "screen": {"w": w, "h": h, "scale": 1.0},
                "version": VERSION, "backend": "linux", "level": state.level,
                "ax_trusted": state.backend.input_ok(), "screen_recording": state.backend.capture_ok(),
            })

        def r_gen(self, q):
            obj, err = self._body()
            if err or not isinstance(obj.get("gen"), int) or isinstance(obj.get("gen"), bool):
                return self._err(400, "bad_request", "body must be JSON with an integer gen")
            state.generation.set(obj["gen"])
            self._json(200, {"gen": obj["gen"]})

        def r_screenshot(self, q):
            region = None
            if "region" in q:
                region = framehash.parse_region(q["region"])
                if region is None:
                    return self._err(400, "bad_request",
                                     "region must be x,y,w,h: finite numbers, x and y at least 0, w and h above 0")
            key = "full" if region is None else ",".join(str(v) for v in region)
            gen = state.generation.current()
            t0 = state.clock()
            try:
                raw, w, h = state.backend.capture(region)
            except BackendError as e:
                state.append_log({"ts": state.clock(), "kind": "screenshot", "gen": gen, "seq": 0,
                                  "type": "screenshot", "status": "error",
                                  "latency_ms": int((state.clock() - t0) * 1000)})
                return self._err(500, "capture_failed", str(e))
            captured = state.clock()
            stale = state.note_hash(key, framehash.fast_hash_hex(raw))
            black = framehash.is_black(raw, w, h)
            state.append_log({"ts": captured, "kind": "screenshot", "gen": gen, "seq": 0, "type": "screenshot",
                              "status": "black" if black else ("stale" if stale else "ok"),
                              "latency_ms": int((captured - t0) * 1000)})
            body = png.encode_bgrx(raw, w, h)
            self._send(200, body, {
                "Content-Type": "image/png", "X-Drums-Gen": str(gen),
                "X-Drums-Captured-At": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(captured)) + f".{int(captured % 1 * 1000):03d}Z",
                "X-Drums-Stale": "true" if stale else "false", "X-Drums-Black": "true" if black else "false"})

        def r_action(self, q):
            obj, err = self._body()
            if err:
                return self._err(400, "bad_request", err)
            try:
                spec = actionspec.decode_action(obj)
            except actionspec.SpecError as e:
                return self._err(400, "bad_request", str(e))

            def work():
                t0 = state.clock()
                failure = None
                try:
                    state.backend.perform(spec)
                except BackendError as e:
                    failure = str(e)
                return t0, state.clock(), failure

            kind, res = state.generation.apply_if_current(spec["gen"], work)
            if kind == "stale":
                return self._stale(res)
            t0, t1, failure = res
            state.append_log({"ts": t1, "kind": "action", "gen": spec["gen"], "seq": spec["seq"],
                              "type": spec["type"], "status": "error" if failure else "ok",
                              "latency_ms": int((t1 - t0) * 1000)})
            if failure:
                return self._err(500, "action_failed", failure)
            self._json(200, {"applied_at": t1, "seq": spec["seq"]})

        def r_log(self, q):
            try:
                since = float(q.get("since", 0))
                if since != since or abs(since) == float("inf"):
                    raise ValueError
            except ValueError:
                return self._err(400, "bad_request", "since must be a finite number")
            with state.log_lock:
                rows = [e for e in state.log if e["ts"] >= since]
            self._send(200, ("".join(json.dumps(e, sort_keys=True) + "\n" for e in rows)).encode(),
                       {"Content-Type": "application/x-ndjson"})

        def r_launch(self, q):
            obj, err = self._body()
            if err:
                return self._err(400, "bad_request", err)
            try:
                spec = actionspec.decode_launch(obj)
            except actionspec.SpecError as e:
                return self._err(400, "bad_request", str(e))

            def work():
                try:
                    return ("ok", state.backend.launch(spec))
                except BackendError as e:
                    return ("failed", str(e))

            kind, res = state.generation.apply_if_current(spec["gen"], work)
            if kind == "stale":
                return self._stale(res)
            if res[0] == "failed":
                return self._err(500, "launch_failed", res[1])
            self._json(200, {"pid": res[1]})

        def r_kill(self, q):
            obj, err = self._body()
            if err:
                return self._err(400, "bad_request", err)
            try:
                spec = actionspec.decode_kill(obj)
            except actionspec.SpecError as e:
                return self._err(400, "bad_request", str(e))
            kind, res = state.generation.apply_if_current(spec["gen"], lambda: state.backend.kill(spec))
            if kind == "stale":
                return self._stale(res)
            if res[0] == "refused":
                return self._err(403, "protected_process", res[1])
            self._json(200, {"killed": res[1]})

        def r_windows(self, q):
            self._json_list(state.backend.windows())

        def _json_list(self, rows):
            self._send(200, json.dumps(rows, sort_keys=True).encode())

        def r_axtext(self, q):
            self._json(200, {"text": None, "reason": "unsupported"})

        def r_pb(self, q):
            self._err(501, "not_implemented", "pasteboard is not implemented in the spike")

    return Handler


class SessionServer:
    def __init__(self, state: SessionState, host="127.0.0.1"):
        self.state = state
        self.httpd = ThreadingHTTPServer((host, state.port), make_handler(state))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
