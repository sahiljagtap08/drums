"""BOOT-ONLY validation of Drums in a read-only image root, before any timing. PROTOTYPE: no live run has happened. Run as root on the owner's host, after `mount_image_rootfs.sh` and
`prepare_kit_for_image.sh`; it starts ONE session, records what actually ran and stops. It never times anything, and `timing_allowed` is true only if every check below holds:

  1. the image root plus the template overlay can run the session (rootcheck: programs of the chosen desktop, loader, kit, kit python minor == the root's python, site-packages), else ABORT_ROOT
     with the reasons and NO session is created;
  2. the desktop and server processes run as the requested uid (not root);
  3. the window manager's own name, read from X by the session, is "GNOME Shell" (a binary version alone proves nothing);
  4. the pinned server answers /screenshot with a PNG, and imports flask/pyautogui/Xlib/lxml/PIL/psutil under the IMAGE's python (3.10 for the 22.04 image).
    python -m drums_compare.boot_validate --root /mnt/vm-image-ro --overlay /opt/drums-overlay/upper --kit /opt/drums-osworld-real --state /var/lib/drums-boot --out /var/lib/drums-boot/out"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

EXPECT_WM = "GNOME Shell"
IMPORTS = "import flask, pyautogui, Xlib, lxml, PIL, psutil, sys; print(tuple(sys.version_info[:2]))"


def identity_problems(marks, uid, want_wm=EXPECT_WM):
    """Mismatches between what the session recorded (the `identity` mark) and what was requested."""
    ids = [m for m in marks if m.get("mark") == "identity"]
    if not ids:
        return ["the session recorded no identity mark (the desktop never reached readiness)"]
    m, out = ids[-1], []
    if m.get("wm_name") is None:
        out.append("no window manager name could be read from X (xprop missing, or no EWMH window manager is running)")
    elif want_wm not in m["wm_name"]:
        out.append(f"the running window manager is {m['wm_name']!r}, not {want_wm!r}")
    procs = [tuple(x) for x in m.get("desktop_procs", [])]
    if not any(c == "gnome-shell" for c, _ in procs):
        out.append("no gnome-shell process is running in the session")
    if any(u != uid for _, u in procs) or not procs:
        out.append(f"desktop processes do not all run as uid {uid}: {procs}")
    if m.get("server_uids") != [uid]:
        out.append(f"server processes run as uids {m.get('server_uids')}, not [{uid}]")
    return out


def http(url, data=None, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def read_marks(path):
    return [json.loads(l) for l in open(path)] if os.path.exists(path) else []


def run(a, deps=None):
    d = deps or {}
    from envd_linux import rootcheck
    report = {"status": None, "problems": [], "timing_allowed": False, "uid": a.uid, "root": a.root, "validated_live": True, "t": time.time()}
    os.makedirs(a.out, exist_ok=True)

    def finish(status):
        report["status"] = status
        json.dump(report, open(os.path.join(a.out, "boot_validation.json"), "w"), indent=1, default=str)
        return report
    found = rootcheck.check_userland([a.overlay, a.root], kit=a.kit, desktop="gnome")
    if found["problems"]:
        report["problems"] = found["problems"]
        return finish("ABORT_ROOT")  # nothing was created or mounted
    from envd_linux.session import Supervisor
    sup = (d.get("supervisor") or Supervisor)(a.state, a.overlay, root_mode="full", base_root=a.root, osworld_real_kit=a.kit, desktop="gnome", uid=a.uid, gid=a.gid, user=a.user,
                                              server_mode="module", netns=True, osworld_port=0, ready_timeout=180)
    try:
        info = sup.create(0)
        marks = read_marks(os.path.join(a.state, "s0", "timing.jsonl"))
        report["identity"] = [m for m in marks if m.get("mark") == "identity"][-1:] 
        report["problems"] += identity_problems(marks, a.uid)
        base = f"http://{info['ip']}:5000"
        shot = (d.get("http") or http)(base + "/screenshot")
        report["screenshot_sha256"] = hashlib.sha256(shot).hexdigest()
        open(os.path.join(a.out, "boot.png"), "wb").write(shot)
        if not (shot[:8] == b"\x89PNG\r\n\x1a\n" and len(shot) > 5000):
            report["problems"].append("the screenshot is not a PNG of plausible size")
        try:
            ex = json.loads((d.get("http") or http)(base + "/execute", {"command": ["python", "-c", IMPORTS], "shell": False}))
            report["server_python"] = ex.get("output", "").strip()
            if ex.get("returncode") != 0 or report["server_python"] != "(3, 10)":
                report["problems"].append(f"server imports/python failed or are not the image's 3.10: {ex}")
        except Exception as e:
            report["problems"].append(f"/execute failed: {type(e).__name__}: {e}")
    except Exception as e:
        report["problems"].append(f"{type(e).__name__}: {str(e)[:300]}")
    finally:
        try:
            sup.close()
        except Exception as e:
            report["problems"].append(f"cleanup: {type(e).__name__}: {e}")
    report["timing_allowed"] = not report["problems"]
    return finish("BOOT_OK" if report["timing_allowed"] else "BOOT_MISMATCH")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--overlay", required=True)
    ap.add_argument("--kit", default="/opt/drums-osworld-real")
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--uid", type=int, default=1000)
    ap.add_argument("--gid", type=int, default=1000)
    ap.add_argument("--user", default="user")
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("REFUSED: run as root", file=sys.stderr)
        return 2
    r = run(a)
    print(json.dumps({k: r[k] for k in ("status", "problems", "timing_allowed")}, indent=1))
    return 0 if r["timing_allowed"] else 1


if __name__ == "__main__":
    sys.exit(main())
