"""Composition manifest (docs/incumbent-preflight-plan.md, item I1): pinned facts about the software a comparison arm runs, in one format both arms fill in, and a
diff that lists every difference. It records what IS installed; it does not make two environments the same, and it makes no licence statement (the licence
composition of an image is unverified until audited component by component).

    python -m envd_linux.composition --out drums.json [--kit /opt/drums-osworld-real] [--osworld-src CHECKOUT]
    python -m envd_linux.composition --diff drums.json vm.json"""

import argparse
import json
import os
import platform
import subprocess

DPKG_PACKAGES = ["libreoffice-core", "xvfb", "openbox", "at-spi2-core", "dbus", "wmctrl", "xclip", "python3", "nftables", "iproute2"]
BROWSERS = ["/opt/pw-browsers/chromium-1194/chrome-linux/chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]


def _run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def build(kit=None, osworld_src=None, pip_python=None):
    m = {"format": 1, "os_release": {}, "kernel": platform.release(), "machine": platform.machine(), "dpkg": {}, "browsers": {}, "python": {}, "guest_server": {}, "desktop_env": {}}
    try:
        for ln in open("/etc/os-release"):
            k, _, v = ln.strip().partition("=")
            if k in ("NAME", "VERSION_ID", "VERSION_CODENAME"):
                m["os_release"][k] = v.strip('"')
    except OSError:
        pass
    for pkg in DPKG_PACKAGES:
        v = _run(["dpkg-query", "-W", "-f=${Version}", pkg])
        m["dpkg"][pkg] = v or None
    for b in BROWSERS:
        if os.path.exists(b):
            m["browsers"][b] = _run([b, "--version"])
    soffice = _run(["soffice", "--version"])
    m["dpkg"]["soffice_version_string"] = soffice or None
    if pip_python or kit:
        py = pip_python or os.path.join(kit, "venv", "bin", "python")
        if os.path.exists(py):
            m["python"] = {"interpreter": _run([py, "--version"]), "packages": sorted(_run([py, "-m", "pip", "freeze"]).splitlines())}
    if kit and os.path.exists(os.path.join(kit, "PROVENANCE.json")):
        m["guest_server"] = json.load(open(os.path.join(kit, "PROVENANCE.json")))
    if osworld_src:
        m["desktop_env"] = {"git_commit": _run(["git", "-C", osworld_src, "rev-parse", "HEAD"]) or None}
    return m


def diff(a, b, path=""):
    """Every difference between two manifests, as 'path: a != b' lines (a missing key counts)."""
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out += diff(a.get(k), b.get(k), f"{path}/{k}")
    elif isinstance(a, list) and isinstance(b, list):
        sa, sb = set(map(json.dumps, a)), set(map(json.dumps, b))
        out += [f"{path}: only in A: {x}" for x in sorted(sa - sb)] + [f"{path}: only in B: {x}" for x in sorted(sb - sa)]
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--kit")
    ap.add_argument("--osworld-src")
    ap.add_argument("--diff", nargs=2)
    a = ap.parse_args(argv)
    if a.diff:
        d = diff(json.load(open(a.diff[0])), json.load(open(a.diff[1])))
        print(json.dumps({"differences": d, "count": len(d)}, indent=1))
        return
    m = build(a.kit, a.osworld_src)
    if a.out:
        json.dump(m, open(a.out, "w"), indent=1)
    print(json.dumps(m, indent=1)[:2000])


if __name__ == "__main__":
    main()
