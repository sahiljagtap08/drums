"""Static and refusal-path checks of the host scripts. They only ever hit the scripts' EARLY refusals (nothing is installed, downloaded or started)."""
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
D = os.path.join(ROOT, "deploy", "aws_host")
SCRIPTS = ["bootstrap.sh", "fetch_image.sh", "fetch_docker_image.sh"]


def read(n):
    return open(os.path.join(D, n)).read()


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_parse_and_fail_on_error(name):
    assert subprocess.run(["bash", "-n", os.path.join(D, name)]).returncode == 0
    assert "set -euo pipefail" in read(name)


def test_bootstrap_creates_and_owns_every_directory_it_later_writes_and_declares_the_stage():
    s = read("bootstrap.sh")
    loop = s.split("for d in ", 1)[1].split("; do", 1)[0]
    for var in ("$STATE", "$SRC", "$OSW", "$CRIU_HOME", "/opt/vm-image", "/opt/drums-osworld-real", "/opt/pw-browsers"):
        assert var in loop, var
    for assign in ("STATE=/var/lib/drums-host", "SRC=/opt/drums-src", "OSW=/opt/osworld-pinned", "CRIU_HOME=/opt/drums-criu"):
        assert assign in s, assign
    assert 'sudo mkdir -p "$d" && sudo chown "$USER" "$d"' in s
    assert 'case "${STAGE:-}" in docker|vbox' in s and "set DRUMS_REF" in s


def test_bootstrap_installs_every_system_piece_the_realkit_needs():
    s = read("bootstrap.sh")
    import importlib.util
    spec = importlib.util.spec_from_file_location("realkit", os.path.join(ROOT, "envd-linux", "envd_linux", "realkit.py"))
    rk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rk)
    pkgs = {"dbus-daemon": "dbus-x11", "Xvfb": "xvfb", "openbox": "openbox", "scrot": "scrot", "xclip": "xclip", "wmctrl": "wmctrl", "socat": "socat", "xprop": "x11-utils"}
    for b in rk.NEEDED_BINARIES:
        assert pkgs[b] in s, f"{b} needs apt package {pkgs[b]}"
    for p in ("python3-pyatspi", "at-spi2-core", "python3.12", "libreoffice-writer", "python3-xlib", "python3-pyroute2", "gnome-terminal"):
        assert p in s


def test_the_two_python_environments_and_root_requirement_are_explicit():
    s = read("bootstrap.sh")
    assert "TWO PYTHON ENVIRONMENTS" in s and ".venv-host" in s and "sudo env PYTHONPATH" in s and "librosa" in s
    assert 'sudo VBoxManage' in s  # every VirtualBox call in bootstrap runs as root, like host_run
    for n in ("fetch_image.sh", "fetch_docker_image.sh"):
        assert '"$(id -u)" -eq 0' in read(n)


def test_image_scripts_check_completeness_before_use_and_never_skip_on_a_bare_file_test():
    v, d = read("fetch_image.sh"), read("fetch_docker_image.sh")
    assert "[ -f Ubuntu.zip ] ||" not in v and "unzip -tq" in v and "Content-Length" in v.lower() or "content-length" in v
    assert "unzip -tq" in d and "--max-filesize" in d and "--zip-bytes" in d and "sha256sum" in d
    assert "Ubuntu.partial" in v  # extraction goes to a scratch dir and is renamed only after the OVF is confirmed


@pytest.mark.parametrize("name,args", [("bootstrap.sh", []), ("fetch_image.sh", []), ("fetch_docker_image.sh", []), ("fetch_docker_image.sh", ["--accept-licence-risk"])])
def test_each_script_refuses_before_doing_anything(name, args):
    r = subprocess.run(["bash", os.path.join(D, name), *args], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": "/nonexistent"}, timeout=30)
    assert r.returncode == 2 and "REFUSED" in (r.stdout + r.stderr)
