"""INTEGRATION: the REAL public OSWorld guest server (Apache-2.0 `desktop_env/server/main.py`, unmodified)
running inside real Drums sessions, with a session bus, AT-SPI, openbox and real pyautogui.

This closes two of the four "not exercised" items of the stub: a REAL accessibility tree and a REAL terminal
observation (the server's own `/terminal`, which reads gnome-terminal through AT-SPI). It is still L0 and
still not the OSWorld image: no GNOME session, no systemd, this sandbox's Ubuntu 24.04 userland, apps limited
to what is installed (gnome-terminal here; no LibreOffice, GIMP, VLC, Chrome).

Needs the kit built once: `python -m envd_linux.realkit <OSWorld checkout>` (see realkit.py for the apt
packages), plus gnome-terminal and /usr/bin/python3.12 (the Ubuntu 24.04 gi bindings are for 3.12 and the
default python3 here is 3.13, so the terminal is launched by python3.12 explicitly), root, overlayfs, Xvfb, nft."""

import json
import os
import re
import shutil
import subprocess
import time

import pytest

from envd_linux import realkit
from envd_linux.scalecua.client import GuestClient
from envd_linux.scalecua.pool import DrumsSessionPool
from envd_linux.session import Supervisor
from envd_linux.template import build_home_template

KIT = os.environ.get("DRUMS_OSW_KIT", realkit.DEFAULT_KIT)


def _prereqs():
    if os.geteuid() != 0:
        return "needs root"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount", "nft"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if not realkit.available(KIT):
        return "build the kit: python -m envd_linux.realkit <OSWorld checkout>"
    if realkit.missing() or not os.path.exists(realkit.GNOME_TERMINAL):
        return f"missing system pieces: {realkit.missing()} gnome-terminal?"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True).returncode:
        return "system python3 needs python-xlib and pyroute2"


pytestmark = pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")

TERMINAL = ["/usr/bin/python3.12", "/usr/bin/gnome-terminal", "--", "bash", "-c", "echo HELLO-IN-SESSION; exec bash"]


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    root = tmp_path_factory.mktemp("real")
    build_home_template(str(root / "tpl" / "home" / "drums"))
    sup = Supervisor(str(root / "state"), str(root / "tpl"), root_mode="full", netns=True, osworld_port=5000,
                     osworld_real_kit=KIT, sweep_dir=str(root / "ledger"))
    pool = DrumsSessionPool(sup, 1, health_timeout=90, eager_reset=False)
    sid, _, ip = pool.start()
    yield GuestClient(ip), sup
    pool.end(sid)
    pool.shutdown() if hasattr(pool, "shutdown") else None
    sup.close()


def test_real_server_platform_size_and_screenshot(session):
    c, _ = session
    assert c.platform() == "Linux"
    assert c.screen_size() == {"width": 1280, "height": 800}
    png = c.screenshot()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_real_pyautogui_runs_in_the_session(session):
    c, _ = session
    r = c.execute(["python", "-c", "import pyautogui; print(pyautogui.size())"])
    assert r["returncode"] == 0 and "Size(width=1280, height=800)" in r["output"], r


def test_real_accessibility_tree_and_terminal_observation(session):
    c, _ = session
    empty = json.loads(c._req("GET", "/accessibility")[1])["AT"]
    assert empty.startswith("<desktop-frame")
    before = c.screenshot()
    c._req("POST", "/setup/launch", body={"command": TERMINAL})
    out, tree = "", ""
    for _ in range(30):
        time.sleep(1)
        out = json.loads(c._req("GET", "/terminal")[1])["output"] or ""
        if "HELLO-IN-SESSION" in out:
            break
    assert "HELLO-IN-SESSION" in out, out  # the server's own /terminal, through AT-SPI, not a stub string
    tree = json.loads(c._req("GET", "/accessibility")[1])["AT"]
    assert 'name="gnome-terminal-server"' in tree and re.search(r'<frame name="Terminal" st:active="true"', tree)
    assert "<terminal " in tree and "HELLO-IN-SESSION" in tree  # real nodes, with real text
    assert c.screenshot() != before, "a real window should change the real screenshot"
