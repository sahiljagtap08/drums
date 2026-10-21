import json
import re
import threading
import urllib.parse
import urllib.request

import pytest

from verify_server.app import audit, make_server


class approx_dict:
    """Equal to a dict that has these keys and values. Ignores id and ts."""

    def __init__(self, **expected):
        self.expected = expected

    def __eq__(self, other):
        trimmed = {k: v for k, v in other.items() if k not in ("id", "ts")}
        return trimmed == self.expected

    def __repr__(self):
        return f"approx_dict({self.expected!r})"


@pytest.fixture
def server(tmp_path):
    db_path = str(tmp_path / "v.db")
    srv = make_server(db_path, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    srv.db_path = db_path
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def get(srv, path):
    with urllib.request.urlopen(srv.base + path) as resp:
        return resp.read().decode()


def post(srv, path, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(srv.base + path, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


def post_json(srv, path, payload):
    req = urllib.request.Request(
        srv.base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


def extract_bg(html):
    match = re.search(r"background:\s*(hsl\([^)]*\))", html)
    assert match, "no hsl background found"
    return match.group(1)


def test_form_roundtrip(server):
    html = get(server, "/form?env=3")
    assert 'name="env" value="3"' in html and 'id="tok"' in html
    saved = post(server, "/form", {"env": "3", "tok": "D03-G1-R0001-deadbeef", "gen": "1"})
    assert '<h1 id="ok">saved</h1>' in saved
    rows = json.loads(get(server, "/rows?env=3"))
    assert rows == [approx_dict(kind="form", env=3, gen=1, tok="D03-G1-R0001-deadbeef")]


def test_form_without_gen_uses_latest_lease(server):
    get(server, "/lease?env=4&gen=7")
    post(server, "/form", {"env": "4", "tok": "D04-G7-R0001-abcd0000"})
    rows = json.loads(get(server, "/rows?env=4"))
    assert rows == [approx_dict(kind="form", env=4, gen=7, tok="D04-G7-R0001-abcd0000")]
    assert json.loads(get(server, "/audit")) == []


def test_vis_tick_and_count(server):
    post_json(server, "/vis/tick", {"env": 2, "count": 17, "visible": True})
    latest = json.loads(get(server, "/vis/count?env=2"))
    assert latest["count"] == 17
    assert latest["visible"] is True
    assert isinstance(latest["ts"], float)


def test_audit_flags_gen_mismatch(server):
    get(server, "/lease?env=1&gen=2")
    post(server, "/form", {"env": "1", "tok": "D01-G1-R0001-00000000", "gen": "1"})
    flagged = json.loads(get(server, "/audit"))
    assert len(flagged) == 1
    assert flagged[0]["gen"] == 1
    assert audit(server.db_path) == flagged


def test_form_color_differs_per_env(server):
    assert extract_bg(get(server, "/form?env=1")) != extract_bg(get(server, "/form?env=2"))
    assert extract_bg(get(server, "/form?env=2")) == "hsl(94,80%,60%)"
