"""The conformance checks run against a STUB written to the spec, and against a deliberately
non-conforming stub to prove the checks can fail."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from drums_compare.conformance import run_conformance
from drums_compare.envd_http import png_size  # noqa: F401  (imported to keep adapters in sync)
from tests.test_adapters import tiny_png  # type: ignore


def make_handler(*, enforce_guard=True, enforce_gen=True):
    class H(BaseHTTPRequestHandler):
        gen = 0
        port = 0

        def log_message(self, *a):
            pass

        def _send(self, status, body=b"{}", headers=None):
            self.send_response(status)
            for k, v in (headers or {"Content-Type": "application/json"}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _j(self, status, obj):
            self._send(status, json.dumps(obj).encode())

        def _guard(self):
            if not enforce_guard:
                return False
            if self.headers.get("Origin"):
                self._j(403, {"error": "forbidden_origin"})
                return True
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_address[1]}":
                self._j(403, {"error": "forbidden_host"})
                return True
            return False

        def do_GET(self):
            if self._guard():
                return
            path = self.path.split("?")[0]
            if path == "/health":
                self._j(200, {"env": 0, "uid": 0, "gen": H.gen, "on_console": True,
                              "screen": {"w": 4, "h": 3, "scale": 1.0}, "version": "stub",
                              "ax_trusted": True, "screen_recording": True})
            elif path == "/screenshot":
                if "region=0,0,-1" in self.path:
                    return self._j(400, {"error": "bad_request"})
                self._send(200, tiny_png(), {"Content-Type": "image/png", "X-Drums-Gen": str(H.gen),
                                             "X-Drums-Captured-At": "now", "X-Drums-Stale": "false",
                                             "X-Drums-Black": "false"})
            elif path == "/action":
                self._j(405, {"error": "method_not_allowed"})
            else:
                self._j(404, {"error": "not_found"})

        def do_POST(self):
            if self._guard():
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/gen":
                if not isinstance(body.get("gen"), int):
                    return self._j(400, {"error": "bad_request"})
                H.gen = body["gen"]
                return self._j(200, {"gen": H.gen})
            if enforce_gen and body.get("gen") != H.gen:
                return self._j(409, {"error": "stale_gen", "current_gen": H.gen})
            if self.path == "/action":
                t = body.get("type")
                ok = ((t in ("click", "dblclick") and "x" in body and "y" in body)
                      or (t == "type" and body.get("text")) or (t == "key" and body.get("key") == "return")
                      or (t == "scroll" and ("dx" in body or "dy" in body)))
                if not ok:
                    return self._j(400, {"error": "bad_request"})
                return self._j(200, {"applied_at": 1.0, "seq": body.get("seq")})
            if self.path == "/launch":
                if not str(body.get("path", "")).startswith("/"):
                    return self._j(400, {"error": "bad_request"})
                return self._j(200, {"pid": 1})
            self._j(200, {"killed": []})
    return H


def serve(**kw):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(**kw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_spec_faithful_stub_passes_every_check():
    srv = serve()
    try:
        res = run_conformance(srv.server_address[1])
    finally:
        srv.shutdown()
    failed = [r for r in res if not r[1]]
    assert not failed, failed
    assert len(res) >= 25


def test_stub_without_local_guard_is_caught():
    srv = serve(enforce_guard=False)
    try:
        failed = {n for n, ok, _ in run_conformance(srv.server_address[1]) if not ok}
    finally:
        srv.shutdown()
    assert {"Origin header refused", "foreign Host refused"} <= failed


def test_stub_without_generation_guard_is_caught():
    srv = serve(enforce_gen=False)
    try:
        failed = {n for n, ok, _ in run_conformance(srv.server_address[1]) if not ok}
    finally:
        srv.shutdown()
    assert "stale gen action refused with 409" in failed
