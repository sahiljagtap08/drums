"""Build the REAL OSWorld guest-server kit that sessions can run instead of the stub.

The kit is the public, Apache-2.0 xlang-ai/OSWorld `desktop_env/server/` files, copied UNMODIFIED from a
local checkout, plus a Python 3.12 virtualenv (system site-packages, for `pyatspi`/`gi`) holding Flask,
lxml, pyautogui, python-xlib, Pillow and friends. It is installed once on the host (default
`/opt/drums-osworld-real`), outside any session, and reaches every session through the shared lower layer
(`/`), the same way the OS tree does. Nothing from it is committed to this repository. The kit carries the
Apache-2.0 `LICENSE`, the source's `NOTICE` if it has one, and `PROVENANCE.json` (source repo, exact commit, licence,
date, per-file hashes, "modified: false", and that it is build-time only). `build()` refuses without a LICENSE.

System packages the kit needs (apt, Ubuntu 24.04 names): python3-pyatspi at-spi2-core dbus-x11 python3-gi
gir1.2-atspi-2.0 python3-tk scrot gnome-screenshot wmctrl xclip socat openbox x11-utils python3-venv;
for the terminal observation also gnome-terminal and gir1.2-gtk-3.0. `build()` checks for them and says which
are missing; it does not install anything system-wide.

    python -m envd_linux.realkit /path/to/OSWorld-checkout [--kit /opt/drums-osworld-real]"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

DEFAULT_KIT = "/opt/drums-osworld-real"
SERVER_FILES = ("main.py", "pyxcursor.py", "screen_recording.py", "__init__.py")
PIP = ["flask", "lxml", "pyautogui", "requests", "pillow", "numpy", "python-xlib", "psutil"]
NEEDED_BINARIES = ["dbus-daemon", "Xvfb", "openbox", "scrot", "xclip", "wmctrl", "socat", "xprop"]
NEEDED_FILES = ["/usr/libexec/at-spi-bus-launcher", "/usr/lib/python3/dist-packages/pyatspi/__init__.py",
                "/usr/bin/python3.12"]
GNOME_TERMINAL = "/usr/bin/gnome-terminal"


def missing():
    return ([b for b in NEEDED_BINARIES if shutil.which(b) is None]
            + [f for f in NEEDED_FILES if not os.path.exists(f)])


def available(kit=DEFAULT_KIT):
    return os.path.exists(os.path.join(kit, "server", "main.py")) and os.path.exists(os.path.join(kit, "venv", "bin", "python"))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(checkout, *args):
    r = subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


NOTICE_NAMES = ("NOTICE", "NOTICE.txt", "NOTICE.md")


def write_provenance(checkout, kit, copied):
    """License notices and a provenance manifest travel WITH the copied files (Apache-2.0 section 4: give recipients a copy
    of the License, keep copyright/attribution notices, and carry the NOTICE file if the Work has one)."""
    lic = os.path.join(checkout, "LICENSE")
    if not os.path.exists(lic):
        raise SystemExit("no LICENSE in the source checkout: refusing to build a kit without the licence text")
    shutil.copy2(lic, os.path.join(kit, "LICENSE"))
    notice = next((n for n in NOTICE_NAMES if os.path.exists(os.path.join(checkout, n))), None)
    if notice:
        shutil.copy2(os.path.join(checkout, notice), os.path.join(kit, "NOTICE"))
    manifest = {
        "what": "unmodified copies of the OSWorld guest server files, plus a build-time virtualenv",
        "source_repo": _git(checkout, "remote", "get-url", "origin"),
        "source_commit": _git(checkout, "rev-parse", "HEAD"),
        "source_commit_date": _git(checkout, "log", "-1", "--format=%cI"),
        "license": "Apache-2.0",
        "license_file": "LICENSE",
        "license_file_sha256": _sha256(lic),
        "notice_file": "NOTICE" if notice else None,
        "notice_note": None if notice else "the source repository ships no NOTICE file at this commit (only LICENSE)",
        "fetched_or_built_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "files_copied_unmodified": {f: _sha256(os.path.join(kit, "server", f)) for f in copied},
        "modified": False,
        "distribution": ("BUILD-TIME ONLY. The kit lives on the host that built it (default /opt/drums-osworld-real), is "
                         "NOT committed to the Drums repository, and is NOT redistributed by it. It reaches sessions on "
                         "that host through the shared lower layer. If a kit is ever shipped to another party "
                         "(an image, an archive), this directory, with LICENSE, NOTICE (if any) and this manifest, must "
                         "go with it unmodified."),
        "python_dependencies": "installed from PyPI into venv/ at build time under their own licences (not copied here)",
    }
    with open(os.path.join(kit, "PROVENANCE.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


def build(checkout, kit=DEFAULT_KIT):
    gone = missing()
    if gone:
        raise SystemExit(f"missing system pieces: {gone}")
    src = os.path.join(checkout, "desktop_env", "server")
    os.makedirs(os.path.join(kit, "server"), exist_ok=True)
    for f in SERVER_FILES:
        shutil.copy2(os.path.join(src, f), os.path.join(kit, "server", f))
    write_provenance(checkout, kit, SERVER_FILES)
    legacy = os.path.join(kit, "LICENSE-OSWorld-Apache-2.0")
    if os.path.exists(legacy):
        os.unlink(legacy)
    venv = os.path.join(kit, "venv")
    if not os.path.exists(os.path.join(venv, "bin", "python")):
        subprocess.run(["/usr/bin/python3.12", "-m", "venv", "--system-site-packages", venv], check=True)
    subprocess.run([os.path.join(venv, "bin", "python"), "-m", "pip", "install", "-q", *PIP], check=True)
    # `python` and `python3` for the session PATH: OSWorld tasks run `python -c "import pyautogui ..."`
    os.makedirs(os.path.join(kit, "bin"), exist_ok=True)
    # wrapper scripts, not symlinks: python finds `pyvenv.cfg` next to the path it was invoked by, so a
    # symlink elsewhere would silently run the base interpreter without the venv's packages
    for name in ("python", "python3"):
        path = os.path.join(kit, "bin", name)
        if os.path.lexists(path):
            os.unlink(path)
        with open(path, "w") as f:
            f.write(f'#!/bin/sh\nexec "{venv}/bin/python" "$@"\n')
        os.chmod(path, 0o755)
    return kit


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("checkout")
    ap.add_argument("--kit", default=DEFAULT_KIT)
    a = ap.parse_args(argv)
    print(build(a.checkout, a.kit))


if __name__ == "__main__":
    sys.exit(main())
