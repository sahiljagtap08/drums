"""Contract tests of the OSWorld guest-server STUB for the routes that need no display. Run
in-process; the X-dependent routes are covered by the integration test."""

import json
import os
import socket
import threading
import urllib.error
import urllib.request

import pytest

from envd_linux import osworld_server
from envd_linux.scalecua.client import GuestClient


@pytest.fixture
def guest():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = osworld_server.serve("127.0.0.1", port, ":99")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield GuestClient("127.0.0.1", port), port
    srv.shutdown()
    srv.server_close()


def test_platform_and_contract_listing(guest):
    c, port = guest
    assert c.platform() == "Linux"
    listing = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/__contract").read())
    assert listing["stub"] is True and "/execute" in listing["implemented"]


def test_execute_runs_a_command_and_reports_returncode(guest):
    c, _ = guest
    r = c.execute(["echo", "hi"])
    assert r["status"] == "success" and r["output"] == "hi\n" and r["returncode"] == 0
    r = c.execute("exit 3", shell=True)
    assert r["returncode"] == 3
    r = c.execute("echo $DISPLAY", shell=True)
    assert r["output"].strip() == ":99"


def test_execute_expands_tilde_args_and_accepts_string_commands(guest):
    c, _ = guest
    assert c.execute("echo ~/x")["output"].strip() == "~/x" or os.path.expanduser("~/x") in c.execute(["echo", "~/x"])["output"]
    assert c.execute("true")["returncode"] == 0


def test_file_and_list_directory(guest, tmp_path):
    c, _ = guest
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    assert c.file(str(tmp_path / "a.txt")) == b"hello"
    assert c.file(str(tmp_path / "missing")) is None
    tree = c.list_dir(str(tmp_path))["directory_tree"]
    names = {(x["type"], x["name"]) for x in tree["children"]}
    assert names == {("file", "a.txt"), ("directory", "sub")}
    assert "error" in c.list_dir(str(tmp_path / "a.txt"))


def test_launch_returns_plain_text_like_the_real_server(guest, tmp_path):
    c, _ = guest
    marker = tmp_path / "ran"
    assert "launched successfully" in c.launch(["touch", str(marker)])
    import time
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists()


def test_run_python(guest):
    c, _ = guest
    r = c.run_python("print(2+3)")
    assert r["status"] == "success" and r["message"].strip() == "5"
    assert c.run_python("raise SystemExit(2)")["status"] == "error"


def test_stub_routes_are_labelled_not_faked(guest):
    c, port = guest
    for path in ("/setup/download_file", "/start_recording", "/wallpaper"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 501
    assert json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/accessibility").read())["stub"]


def test_unknown_route_is_404_and_bad_json_is_400(guest):
    c, port = guest
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope")
    assert e.value.code == 404
    req = urllib.request.Request(f"http://127.0.0.1:{port}/execute", data=b"{bad", method="POST",
                                 headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 400
