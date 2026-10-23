"""verify_http runs against the REAL verify-server. envd_http runs against a FAKE envd
(a local stub of the routes read from the Swift source), so it proves wire format only."""

import json
import os
import struct
import threading
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from drums_compare.envd_http import EnvdHttpEnv
from drums_compare.harness import run_attempt
from drums_compare import fakes
from drums_compare.types import Action, EnvFault, Task
from drums_compare.verify_http import VerifyServerVerifier
from verify_server.app import make_server


def tiny_png(w=4, h=3):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d))
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture
def verify_url(tmp_path):
    srv = make_server(str(tmp_path / "v.db"), port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post_form(base, env, tok):
    data = urllib.parse.urlencode({"env": env, "tok": tok}).encode()
    urllib.request.urlopen(urllib.request.Request(base + "/form", data=data)).read()


def test_real_verify_server_pass_and_clean(verify_url):
    v = VerifyServerVerifier(verify_url)
    v.lease(0, 1)
    assert v.is_clean(0, 1)
    post_form(verify_url, 0, "TOK")
    r = v.check(Task("t", "", {}), 0, 1, "TOK", 0.0)
    assert r.ran and r.passed and r.foreign_rows == 0


def test_real_verify_server_detects_write_after_reset(verify_url):
    import time
    v = VerifyServerVerifier(verify_url)
    v.lease(0, 1)
    v.lease(0, 2)  # reset happened
    since = time.time() - 1
    # a late submit from the old attempt, carrying its old gen explicitly
    data = urllib.parse.urlencode({"env": 0, "tok": "OLD", "gen": 1}).encode()
    urllib.request.urlopen(urllib.request.Request(verify_url + "/form", data=data)).read()
    r = v.check(Task("t", "", {}), 0, 2, "NEW", since)
    assert r.foreign_rows == 1 and not r.passed


def test_legit_history_is_not_flagged_as_foreign(verify_url):
    import time
    v = VerifyServerVerifier(verify_url)
    v.lease(0, 1)
    post_form(verify_url, 0, "A")
    time.sleep(0.05)
    since = time.time()
    v.lease(0, 2)
    post_form(verify_url, 0, "B")
    r = v.check(Task("t", "", {}), 0, 2, "B", since)
    assert r.passed and r.foreign_rows == 0


class FakeEnvd(BaseHTTPRequestHandler):
    calls = []
    gen = 0
    shots = []  # optional queue of PNGs to serve; the last one repeats

    def log_message(self, *a):
        pass

    def _send(self, status, body=b"{}", headers=None):
        self.send_response(status)
        for k, v in (headers or {"Content-Type": "application/json"}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeEnvd.calls.append(("GET", self.path))
        if self.path == "/health":
            self._send(200, json.dumps({"ax_trusted": True, "screen_recording": True}).encode())
        elif self.path.startswith("/screenshot"):
            png = (FakeEnvd.shots.pop(0) if len(FakeEnvd.shots) > 1 else FakeEnvd.shots[0]) if FakeEnvd.shots else tiny_png()
            self._send(200, png, {"Content-Type": "image/png", "X-Drums-Gen": str(FakeEnvd.gen),
                                         "X-Drums-Stale": "false", "X-Drums-Black": "false"})
        else:
            self._send(404)

    def do_DELETE(self):
        FakeEnvd.calls.append(("DELETE", self.path))
        self._send(200)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        FakeEnvd.calls.append(("POST", self.path, body))
        if self.path == "/gen":
            FakeEnvd.gen = body["gen"]
        if self.path == "/action" and body["gen"] != FakeEnvd.gen:
            return self._send(409, b'{"error":"stale_gen","current_gen":%d}' % FakeEnvd.gen)
        self._send(200)


@pytest.fixture
def envd_port():
    FakeEnvd.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeEnvd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_envd_adapter_reset_and_action_wire_format(envd_port):
    env = EnvdHttpEnv(0, envd_port, {"path": "/bin/browser", "args": ["{url}"]})
    t = Task("t", "", {"start_url": "http://x/form?env={slot}"})
    obs = env.reset(t, 5, "TOK")
    assert (obs.width, obs.height, obs.gen) == (4, 3, 5)
    paths = [c[1] for c in FakeEnvd.calls if c[0] == "POST"]
    assert paths == ["/gen", "/kill", "/launch"]
    launch = [c for c in FakeEnvd.calls if c[1] == "/launch"][0][2]
    assert launch["args"] == ["http://x/form?env=0"]
    env.act(Action("click", x=1, y=2))
    act = [c for c in FakeEnvd.calls if c[1] == "/action"][0][2]
    assert act == {"gen": 5, "seq": 1, "type": "click", "x": 1, "y": 2}


def test_envd_adapter_stale_gen_is_a_fault(envd_port):
    env = EnvdHttpEnv(0, envd_port, {"path": "p", "args": []})
    env.gen = 1
    FakeEnvd.gen = 2
    with pytest.raises(EnvFault) as e:
        env.act(Action("click", x=1, y=1))
    assert e.value.kind == "stale_gen"


def test_envd_adapter_unreachable(tmp_path):
    env = EnvdHttpEnv(0, 1, {"path": "p", "args": []}, timeout_s=0.5)
    with pytest.raises(EnvFault) as e:
        env.observe()
    assert e.value.kind == "daemon_unreachable"


def test_pre_reset_hook_runs_before_daemon_calls(envd_port):
    order = []
    env = EnvdHttpEnv(0, envd_port, {"path": "p", "args": []},
                      pre_reset=lambda slot, gen: order.append(("hook", slot, gen, len(FakeEnvd.calls))))
    env.reset(Task("t", "", {"start_url": ""}), 3, "T")
    # the new generation is posted first (late actions refused), then the hook runs
    assert order == [("hook", 0, 3, 1)]
    assert FakeEnvd.calls[0][1] == "/gen" 


def test_settle_waits_for_a_change_then_for_stability(envd_port):
    # blank, blank, then painted and stable: the adapter must not accept the first blank frame
    FakeEnvd.shots = [tiny_png(4, 3), tiny_png(4, 3), tiny_png(5, 3), tiny_png(5, 3), tiny_png(5, 3)]
    env = EnvdHttpEnv(0, envd_port, {"path": "p", "args": []}, settle_timeout_s=5, settle_poll_s=0.01,
                      settle_quiet_s=60)
    env.reset(Task("t", "", {"start_url": ""}), 1, "T")
    # reset consumed the change and the following stable frames, then observe() read the last one
    assert FakeEnvd.shots[0] == tiny_png(5, 3)
    FakeEnvd.shots = []


def test_settle_accepts_a_frame_that_never_changes_after_the_quiet_period(envd_port):
    FakeEnvd.shots = [tiny_png(4, 3)]
    env = EnvdHttpEnv(0, envd_port, {"path": "p", "args": []}, settle_timeout_s=5, settle_poll_s=0.01,
                      settle_quiet_s=0.1)
    env.reset(Task("t", "", {"start_url": ""}), 1, "T")
    FakeEnvd.shots = []


def test_settle_times_out_if_the_display_keeps_changing(envd_port):
    import itertools
    FakeEnvd.shots = [tiny_png(4 + i % 7, 3) for i in range(1, 400)]
    env = EnvdHttpEnv(0, envd_port, {"path": "p", "args": []}, settle_timeout_s=0.3, settle_poll_s=0.01)
    with pytest.raises(EnvFault) as e:
        env.reset(Task("t", "", {"start_url": ""}), 1, "T")
    assert e.value.kind == "settle_timeout"
    FakeEnvd.shots = []
