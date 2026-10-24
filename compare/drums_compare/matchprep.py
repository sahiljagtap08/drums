"""OFFLINE preparation for a MATCHED-software Drums arm: compare the real incumbent image's manifest with the Drums rootfs and decide, from data, what each route would need. Reads two JSON
manifests and an optional full dpkg list; runs nothing, mounts nothing, installs nothing, touches no cloud. PROTOTYPE: its route findings are code-reading and manifest logic, NOT
validated on a live host, and nothing here claims the arms are or will be equivalent.

INPUT FORMATS (known, from the real runs): the IMAGE manifest written by `deploy/aws_host/fetch_docker_image.sh`: `os_release` (the text of /etc/os-release), `kernel`, `desktop`
(XDG_CURRENT_DESKTOP plus the gnome-shell version), `browser`, `libreoffice`, `guest_user_and_home` (two lines: user, home), `dpkg` (a LIST of "package=version"), optionally `guest_nproc`
and `guest_memtotal`. The DRUMS manifest is `envd_linux.composition` output (`os_release` dict, `kernel`, `dpkg` dict of the packages it tracks, `browsers`); a full Drums inventory in the
SAME "package=version" array form comes from `drums_inventory` (one `dpkg-query` on the host).

    python -m drums_compare.matchprep --image image_manifest.json --drums drums_composition_manifest.json [--drums-dpkg full_dpkg.txt]"""

import argparse
import json
import re
import subprocess
import sys

# Aspects whose difference is a property of the ARCHITECTURE, not of the software list: Drums shares the host kernel, so a guest kernel can never be matched by choosing packages.
STRUCTURAL = ("kernel",)


def parse_dpkg(lines):
    out = {}
    for ln in lines:
        if "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def drums_inventory(runner=lambda cmd: subprocess.run(cmd, capture_output=True, text=True).stdout):
    """The Drums host's FULL package list, in the image manifest's own format (a list of "package=version")."""
    return sorted(ln for ln in runner(["dpkg-query", "-W", "-f=${Package}=${Version}\n"]).splitlines() if "=" in ln)


def _os(text, key):
    m = re.search(rf'^{key}="?([^"\n]*)"?', text or "", re.M)
    return m.group(1) if m else None


def _ver(text):
    m = re.search(r"(\d+(?:\.\d+){1,3})", text or "")
    return m.group(1) if m else None


def aspects(image, drums):
    """Per aspect: the VM value, the Drums value, equal or not, and why it differs (kind)."""
    d_os = drums.get("os_release") or {}
    iu = [x.strip() for x in (image.get("guest_user_and_home") or "").splitlines() if x.strip()]
    rows = [
        ("os_release", _os(image.get("os_release"), "VERSION"), d_os.get("VERSION") or d_os.get("VERSION_ID"), "release"),
        ("os_version_id", _os(image.get("os_release"), "VERSION_ID"), d_os.get("VERSION_ID"), "release"),
        ("kernel", image.get("kernel"), drums.get("kernel"), "structural"),
        ("desktop", image.get("desktop"), "openbox" if not any("gnome-shell" in k for k in (drums.get("dpkg") or {})) else "gnome-shell", "session"),
        ("browser", image.get("browser"), "; ".join(f"{v}" for v in (drums.get("browsers") or {}).values()), "package"),
        ("libreoffice", (image.get("libreoffice") or "").strip(), (drums.get("dpkg") or {}).get("soffice_version_string"), "package"),
        ("guest_user", iu[0] if iu else None, "root (sessions run as root)", "user"),
        ("guest_home", iu[1] if len(iu) > 1 else None, "/home/drums", "path"),
    ]
    out = []
    for name, vm, dr, kind in rows:
        eq = (vm is not None and dr is not None and str(vm).strip() == str(dr).strip())
        out.append({"aspect": name, "vm": vm, "drums": dr, "equal": eq, "kind": "structural" if name in STRUCTURAL else kind})
    if image.get("guest_memtotal"):
        out.append({"aspect": "guest_memory", "vm": image["guest_memtotal"], "drums": "cgroup memory.max set to the guest budget", "equal": False, "kind": "budget",
                    "note": "a VM's MemTotal is below its configured RAM by firmware and QEMU reservations; a cgroup limit is the configured number: a small recorded difference, not removable"})
    return out


def packages(image_dpkg, drums_dpkg):
    a, b = parse_dpkg(image_dpkg), parse_dpkg(drums_dpkg)
    common = set(a) & set(b)
    same = sorted(p for p in common if a[p] == b[p])
    diff = sorted(p for p in common if a[p] != b[p])
    return {"vm_count": len(a), "drums_count": len(b), "common": len(common), "common_same_version": len(same), "common_different_version": len(diff),
            "only_in_vm": len(set(a) - set(b)), "only_in_drums": len(set(b) - set(a)),
            "different_sample": [(p, a[p], b[p]) for p in diff[:25]], "same_fraction_of_vm": (len(same) / len(a)) if a else None}


def route_report(image, drums, drums_dpkg=None):
    """What each route needs, derived from the manifests and from the code (session.py / session_init.py / realkit.py read for this). Every line says what it rests on.

    ROUTE A: install the VM's userland on the Drums HOST (Ubuntu 22.04, GNOME 42, Chrome 130, LibreOffice 7.3.7). ROUTE B: use the VM image's OWN root filesystem, mounted READ-ONLY and
    extracted, as the base layer of the Drums overlay (Drums already overlays the whole root with `lowerdir=<template>:/`; B would replace `/` with the image root)."""
    idpkg = parse_dpkg(image.get("dpkg") or [])
    need = {}
    for name in ("python3", "openbox", "mutter", "wmctrl", "xclip", "dbus-x11", "dbus", "at-spi2-core", "python3-pyatspi", "gnome-terminal", "gnome-shell", "google-chrome-stable",
                 "libreoffice-core", "libreoffice-writer", "libreoffice-impress", "xvfb", "scrot", "socat", "sudo"):
        need[name] = idpkg.get(name)
    py = _ver(need.get("python3") or "")
    gaps_b = []
    if py and not py.startswith("3.12"):
        gaps_b.append(f"the kit venv is built on /usr/bin/python3.12 (`realkit.build`, NEEDED_FILES) but the image has python3 {need['python3']}: the kit cannot run inside the image root; it would need to be rebuilt on the image's python, or the pinned server files run on the image's own python")
    for pkg in ("openbox", "wmctrl", "xclip", "dbus", "at-spi2-core", "python3-pyatspi"):
        if not need.get(pkg):
            gaps_b.append(f"{pkg} is NOT in the image: session_init starts it inside the session root (`start_real_desktop`), so it would have to be added (a recorded software addition) or replaced")
    if need.get("mutter") and not need.get("openbox"):
        gaps_b.append("the image has mutter (GNOME's window manager) but no openbox: a session could run `mutter` instead of openbox (closer to the VM, no panel or hotkeys) - unvalidated")
    ver = lambda s: _ver(s or "")
    route_a_pins = {"google-chrome-stable": need.get("google-chrome-stable") or image.get("browser"), "libreoffice": image.get("libreoffice"), "gnome-shell": image.get("desktop")}
    return {
        "inventory_basis": "image dpkg array; Drums side: " + ("full dpkg list supplied" if drums_dpkg else "only the packages composition.py tracks (a full list needs `drums_inventory`)"),
        "image_package_facts": need,
        "route_A_install_on_host": {
            "needs": ["a 22.04 host (the AMI would change from 24.04: the kit, CRIU, cgroup and nested-virtualization findings so far are for 24.04)", "GNOME 42 and a gnome-shell session, or openbox kept (then desktop stays different)",
                      f"google-chrome-stable at the VM's exact version ({route_a_pins['google-chrome-stable']!r}): Google's apt repository serves the CURRENT stable, so an old .deb must be fetchable from a pool URL (UNVERIFIED availability)",
                      "LibreOffice 7.3.7 from the jammy archive (the upstream version can match; the Ubuntu patch revision may not)", "a `user` account and /home/user on the host (an identity decision, below)"],
            "cannot_match": ["kernel (the host's own; structural)", "per-package versions that Ubuntu has since superseded (old versions may no longer be installable): exact package equality is NOT achievable by this route"],
            "validated": False},
        "route_B_read_only_image_rootfs": {
            "needs": ["the qcow2 root filesystem mounted READ-ONLY on the host (qemu-nbd or libguestfs; the nbd module; `ro,noload`) - UNVERIFIED on the AWS kernel", "`--base-root PATH` in session_init and `base_root` in Supervisor, replacing the hard-coded `/` in `lowerdir=<template>:/` (two places: session_init.py and the restore path in session.py)",
                      "the pinned server files plus a python that runs them inside the image root (see gaps)", "Xvfb stays a HOST process (started outside the session), so it needs nothing from the image"],
            "gaps": gaps_b,
            "matches_by_construction": ["every package and version (the same bytes)", "Chrome 130 and LibreOffice 7.3.7 with no download (the old-Chrome availability problem disappears)", "/home/user and its contents"],
            "cannot_match": ["kernel (the host's; structural)", "systemd as PID 1, logind and the system D-Bus: a Drums session has its own init, so GNOME Shell as a session is NOT expected to run (window manager is openbox or mutter)"],
            "incompatible_with": ["CRIU checkpoint/restore (the restore path assumes `/` as a lower layer): cold-reset stages only"],
            "validated": False},
        "identity_decisions_needed": [
            "sessions run as ROOT with HOME set (as today); the VM user is `user` with sudo. Changing the uid/ownership would be a privilege and ownership change and is NOT made silently",
            "network stays Drums' per-session veth; the VM's is QEMU user-mode/NAT: recorded as different",
        ],
    }


def compare(image, drums, drums_dpkg_lines=None):
    asp = aspects(image, drums)
    ddp = drums_dpkg_lines if drums_dpkg_lines is not None else [f"{k}={v}" for k, v in (drums.get("dpkg") or {}).items() if v and k != "soffice_version_string"]
    pk = packages(image.get("dpkg") or [], ddp)
    return {"aspects": asp, "all_aspects_equal": all(a["equal"] for a in asp), "differences": [a["aspect"] for a in asp if not a["equal"]],
            "structural_differences": [a["aspect"] for a in asp if not a["equal"] and a["kind"] == "structural"], "packages": pk, "routes": route_report(image, drums, drums_dpkg_lines),
            "verdict": "NOT_A_COMPARISON" if any(not a["equal"] for a in asp) else "aspects equal (packages and behaviour still need measured evidence)"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--drums", required=True)
    ap.add_argument("--drums-dpkg", help="a file with one package=version per line")
    a = ap.parse_args(argv)
    lines = open(a.drums_dpkg).read().splitlines() if a.drums_dpkg else None
    print(json.dumps(compare(json.load(open(a.image)), json.load(open(a.drums)), lines), indent=1))


if __name__ == "__main__":
    sys.exit(main())
