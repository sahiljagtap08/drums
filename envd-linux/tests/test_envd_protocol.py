"""Protocol-layer tests. They run the real envd_linux HTTP code against a FAKE backend: they
prove routes, refusals and the generation guard, not any display behaviour."""

import json
import socket
import threading
import urllib.request

import pytest

from drums_compare.conformance import run_conformance
from envd_linux import actionspec, framehash, png
from envd_linux.fakebackend import FakeBackend
from envd_linux.guard import Generation
from envd_linux.httpd import SessionServer, SessionState


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def served():
    be = FakeBackend()
    st = SessionState(env=3, port=free_port(), backend=be)
    srv = SessionServer(st).start()
    yield st, be
    srv.stop()


def call(st, method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{st.port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_every_conformance_check_passes_against_the_real_protocol_code(served):
    st, _ = served
    res = run_conformance(st.port)
    failed = [r for r in res if not r[1]]
    assert not failed, failed


def test_stale_action_never_reaches_the_backend(served):
    st, be = served
    call(st, "POST", "/gen", {"gen": 5})
    status, _, _ = call(st, "POST", "/action", {"gen": 4, "seq": 1, "type": "click", "x": 1, "y": 1})
    assert status == 409 and be.actions == []
    status, _, _ = call(st, "POST", "/action", {"gen": 5, "seq": 2, "type": "click", "x": 1, "y": 1})
    assert status == 200 and len(be.actions) == 1


def test_reset_cannot_interleave_with_an_in_flight_action():
    """A slow action holds the generation lock, so a concurrent /gen waits and the action is
    applied under the old generation exactly once; afterwards old-gen actions are refused."""
    g = Generation(1)
    started, release = threading.Event(), threading.Event()
    order = []

    def slow():
        started.set()
        release.wait(5)
        order.append("action")
        return "done"

    t = threading.Thread(target=lambda: order.append(g.apply_if_current(1, slow)[0]))
    t.start()
    started.wait(5)
    s = threading.Thread(target=lambda: (g.set(2), order.append("set")))
    s.start()
    release.set()
    t.join(5)
    s.join(5)
    assert order == ["action", "applied", "set"]
    assert g.apply_if_current(1, lambda: "x") == ("stale", 2)


def test_screenshot_stale_flag_follows_frame_content(served):
    st, be = served
    _, h1, _ = call(st, "GET", "/screenshot")
    _, h2, _ = call(st, "GET", "/screenshot")
    call(st, "POST", "/gen", {"gen": 0})
    call(st, "POST", "/action", {"gen": 0, "seq": 1, "type": "click", "x": 1, "y": 1})
    _, h3, _ = call(st, "GET", "/screenshot")
    assert (h1["X-Drums-Stale"], h2["X-Drums-Stale"], h3["X-Drums-Stale"]) == ("false", "true", "false")


def test_black_frame_is_flagged(served):
    st, be = served
    be.shade = 0
    _, h, _ = call(st, "GET", "/screenshot")
    assert h["X-Drums-Black"] == "true"


def test_log_records_actions_and_screenshots(served):
    st, _ = served
    call(st, "POST", "/action", {"gen": 0, "seq": 7, "type": "key", "key": "return"})
    call(st, "GET", "/screenshot")
    _, _, raw = call(st, "GET", "/log?since=0")
    rows = [json.loads(x) for x in raw.decode().splitlines()]
    assert [r["kind"] for r in rows] == ["action", "screenshot"] and rows[0]["seq"] == 7


def test_launch_and_kill_refusals(served):
    st, be = served
    assert call(st, "POST", "/launch", {"gen": 0, "path": "/bin/true"})[0] == 200
    assert call(st, "POST", "/kill", {"gen": 0, "pid": 1234})[0] == 403
    assert call(st, "POST", "/kill", {"gen": 0, "all": True})[0] == 200
    assert call(st, "POST", "/kill", {"gen": 0})[0] == 400
    assert call(st, "POST", "/kill", {"gen": 0, "pid": 1})[0] == 400


def test_backend_failure_is_a_500_not_a_crash():
    be = FakeBackend(fail_actions=True)
    st = SessionState(env=0, port=free_port(), backend=be)
    srv = SessionServer(st).start()
    try:
        assert call(st, "POST", "/action", {"gen": 0, "seq": 1, "type": "click", "x": 1, "y": 1})[0] == 500
    finally:
        srv.stop()


def test_oversized_and_malformed_bodies_rejected(served):
    st, _ = served
    req = urllib.request.Request(f"http://127.0.0.1:{st.port}/action", method="POST", data=b"{nope",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 400


def test_action_spec_rules():
    ok = {"gen": 0, "seq": 1}
    for bad in ({"type": "click"}, {"type": "type", "text": ""}, {"type": "type", "text": "x" * 2001},
                {"type": "key", "key": "nope"}, {"type": "scroll"}, {"type": "click", "x": -1, "y": 0},
                {"type": "click", "x": float("inf"), "y": 0}, {"type": "click", "x": 1, "y": 1, "button": "middle"},
                {"type": "click", "x": 1, "y": 1, "modifiers": ["meta"]}, {"type": "teleport"}):
        with pytest.raises(actionspec.SpecError):
            actionspec.decode_action({**ok, **bad})
    actionspec.decode_action({**ok, "type": "key", "key": "F5"})
    actionspec.decode_action({**ok, "type": "scroll", "dy": -3})


def test_frame_helpers():
    assert framehash.parse_region("0,0,10,10") == (0, 0, 10, 10)
    for bad in ("0,0,-1,10", "a,b,c,d", "0,0,10", "0,0,1e9,10", "nan,0,1,1"):
        assert framehash.parse_region(bad) is None
    assert framehash.is_black(bytes(4 * 100), 10, 10)
    assert not framehash.is_black(bytes([200, 200, 200, 0]) * 100, 10, 10)
    data = png.encode_bgrx(bytes([1, 2, 3, 0]) * 6, 3, 2)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
