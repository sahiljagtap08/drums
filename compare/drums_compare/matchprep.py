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


def packages(image_dpkg, drums_dpkg, complete=False):
    a, b = parse_dpkg(image_dpkg), parse_dpkg(drums_dpkg)
    common = set(a) & set(b)
    same = sorted(p for p in common if a[p] == b[p])
    diff = sorted(p for p in common if a[p] != b[p])
    return {"complete_inventory": complete, "note": None if complete else f"PARTIAL: the Drums side lists only {len(b)} tracked packages, so this is NOT a full inventory comparison (the VM side has {len(a)})",
            "vm_count": len(a), "drums_count": len(b), "common": len(common), "common_same_version": len(same), "common_different_version": len(diff),
            "only_in_vm": len(set(a) - set(b)), "only_in_drums": len(set(b) - set(a)),
            "different_sample": [(p, a[p], b[p]) for p in diff[:25]], "same_fraction_of_vm": ((len(same) / len(a)) if a else None) if complete else None}


# program (as the code starts it) -> the Debian packages that provide it; used only to read PRESENCE out of an image's dpkg array
PROGRAM_PACKAGES = {"dbus-daemon": ("dbus-daemon", "dbus"), "/usr/libexec/at-spi-bus-launcher": ("at-spi2-core",), "openbox": ("openbox",), "wmctrl": ("wmctrl",), "xclip": ("xclip",),
                    "xdotool": ("xdotool",), "scrot": ("scrot",), "socat": ("socat",)}


def _present(idpkg, program):
    pk = PROGRAM_PACKAGES.get(program, (program,))
    return next(((p, idpkg[p]) for p in pk if p in idpkg), None)


def route_report(image, drums, drums_dpkg=None, session_source=None, server_source=None):
    """What each route needs, derived from the manifests and from the code. The programs a session STARTS are read from `session_init.start_real_desktop` (rootcheck.desktop_programs), and the
    programs the pinned server runs per task from its source when given; presence is read from the image's dpkg array. Nothing is guessed from a package that is merely absent: for instance
    the image has no package named `mutter`, but it has gnome-shell and the mutter libraries, and whether gnome-shell can run in a session is NOT concluded here.

    ROUTE A: install the VM's userland on the Drums HOST. ROUTE B: use the image's OWN root filesystem, mounted READ-ONLY, as the base layer of the overlay."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "envd-linux"))
    from envd_linux import rootcheck
    idpkg = parse_dpkg(image.get("dpkg") or [])
    launched = rootcheck.desktop_programs(session_source)
    launched_status = {p: (lambda h: {"package": h[0], "version": h[1]} if h else "ABSENT from the image")(_present(idpkg, p)) for p in launched}
    server_status = None
    if server_source:
        server_status = {p: (lambda h: {"package": h[0], "version": h[1]} if h else "ABSENT from the image")(_present(idpkg, p)) for p in rootcheck.server_programs(server_source)}
    facts = {n: idpkg.get(n) for n in ("python3", "gnome-shell", "gir1.2-mutter-10", "libmutter-10-0", "mutter-common", "mutter", "google-chrome-stable", "libreoffice-core", "sudo")}
    gaps = []
    py = _ver(facts.get("python3") or "")
    if py and not py.startswith("3.12"):
        gaps.append(f"the kit venv's python is a symlink to the HOST's /usr/bin/python3.12 (`realkit.build`) and the image has python3 {facts['python3']}: the kit cannot run in the image root as built (interpreter and library compatibility differ); a kit/runtime that is visible and validated inside the root is required, and `Supervisor` refuses before any mount without it")
    gaps.append("the kit directory lives on the HOST (`/opt/drums-osworld-real`); after the pivot it is gone unless explicitly placed in the template layer (`drums_template_overlay`), which `rootcheck` verifies before any mount")
    for p, st in launched_status.items():
        if st == "ABSENT from the image":
            gaps.append(f"{p}, which the session starts after the pivot, is ABSENT from the image (a recorded addition would be needed, or the session code changed)")
    for p, st in (server_status or {}).items():
        if st == "ABSENT from the image":
            gaps.append(f"(per task) the pinned server runs {p} and the image lacks it")
    comp = "full dpkg list supplied" if drums_dpkg else "ONLY the packages composition.py tracks (a full list needs `drums_inventory`): this is not a full inventory comparison"
    return {
        "inventory_basis": "image dpkg array; Drums side: " + comp,
        "session_launched_programs": launched_status, "pinned_server_programs": server_status or "not derived (pass the pinned server's main.py)",
        "image_package_facts": facts,
        "gnome": ("gnome-shell " + facts["gnome-shell"] + " is installed in the image, with the mutter libraries" if facts.get("gnome-shell") else "gnome-shell not found in the image's dpkg list") +
                 "; whether it can run inside a Drums session (no logind, no system D-Bus) is UNVALIDATED and is not concluded from package names",
        "route_A_install_on_host": {
            "needs": ["a 22.04 host (the kit, CRIU, cgroup and nested-virtualization findings so far are for 24.04)", f"google-chrome-stable at the VM's exact version ({facts.get('google-chrome-stable') or image.get('browser')!r}): Google's apt repository serves the CURRENT stable, so an old .deb must be fetchable from a pool URL (UNVERIFIED availability)",
                      "LibreOffice 7.3.7 from the jammy archive (the upstream version can match; the Ubuntu patch revision may not)", "a `user` account and /home/user on the host (an identity decision)"],
            "cannot_match": ["kernel (the host's own; structural)", "per-package versions that Ubuntu has since superseded (old versions may no longer be installable): exact package equality is NOT achievable by this route"],
            "validated": False},
        "route_B_read_only_image_rootfs": {
            "needs": ["the qcow2 root filesystem mounted READ-ONLY (mount_image_rootfs.sh; nbd) - UNVERIFIED on the AWS kernel", "`--base-root` plumbing (built, unit-tested only)", "a kit/runtime placed in the template layer that runs inside the image root (verified by rootcheck before any mount)"],
            "gaps": gaps,
            "matches_by_construction": ["every package and version in the image (the same bytes)", "Chrome and LibreOffice at the image's versions with no download", "/home/user and its contents"],
            "cannot_match": ["kernel (the host's; structural)", "systemd as PID 1 and logind: a session has its own init"],
            "incompatible_with": ["CRIU checkpoint/restore (the restore path assumes `/` as a lower layer): cold-reset stages only"],
            "validated": False},
        "identity_decisions_needed": [
            "sessions run as ROOT with HOME set; the VM user is `user` with sudo. Changing the uid or ownership would be a privilege and ownership change and is NOT made silently; the home/base-root plumbing does NOT declare identity or GNOME matched",
            "network stays Drums' per-session veth; the VM's is QEMU user-mode: recorded as different",
        ],
    }


def compare(image, drums, drums_dpkg_lines=None, session_source=None, server_source=None):
    asp = aspects(image, drums)
    ddp = drums_dpkg_lines if drums_dpkg_lines is not None else [f"{k}={v}" for k, v in (drums.get("dpkg") or {}).items() if v and k != "soffice_version_string"]
    pk = packages(image.get("dpkg") or [], ddp, complete=drums_dpkg_lines is not None)
    return {"aspects": asp, "all_aspects_equal": all(a["equal"] for a in asp), "differences": [a["aspect"] for a in asp if not a["equal"]],
            "structural_differences": [a["aspect"] for a in asp if not a["equal"] and a["kind"] == "structural"], "packages": pk, "routes": route_report(image, drums, drums_dpkg_lines, session_source, server_source),
            "verdict": "NOT_A_COMPARISON" if any(not a["equal"] for a in asp) else "aspects equal (packages and behaviour still need measured evidence)"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--drums", required=True)
    ap.add_argument("--drums-dpkg", help="a file with one package=version per line")
    ap.add_argument("--server-main", help="the pinned OSWorld server's main.py (to derive the programs it runs per task)")
    a = ap.parse_args(argv)
    lines = open(a.drums_dpkg).read().splitlines() if a.drums_dpkg else None
    print(json.dumps(compare(json.load(open(a.image)), json.load(open(a.drums)), lines, None, open(a.server_main).read() if a.server_main else None), indent=1))


if __name__ == "__main__":
    sys.exit(main())
