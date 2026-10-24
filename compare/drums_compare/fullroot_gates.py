"""Task gates for Drums full-root sessions on the pinned image root with the private system bus, plus the parity capture that says WHAT DIFFERS from the incumbent VM. PROTOTYPE: the live part has never
run; everything below is tested with fake guests. It runs the UNTOUCHED official task and evaluator through the same `_DesktopEnvLease` the other gates use: score before any action must be 0, the correct
action 1, a wrong action 0 after a reset; reset hygiene (a process token, a file marker and a running Writer from before a reset are gone after it); and TWO simultaneous sessions must not see or disturb each
other's canaries and must have different private buses, mount namespaces and X displays.

LABEL. Every timing taken under these differences is `ARCHITECTURAL_NOT_FAIR_COMPARISON`; nothing here resolves the parity gaps listed in PARITY_GAPS, and a single boot or gate pass proves no training economics.
Phases (cold setup, checkpoint/save, restore, hygiene, task, evaluation) are recorded as SEPARATE clocks; a phase that does not exist in this configuration is `null` with the reason, never 0. State bytes are
recorded apart from time.

    python -m drums_compare.fullroot_gates preflight --config CFG --out DIR      # offline: no host
    python -m drums_compare.fullroot_gates run --config CFG --out DIR            # live: the authorized host only"""
import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager

PARITY_GAPS = [
    {"id": "xvfb_vs_xorg", "gap": "Drums sessions use Xvfb (the image's own 22.04 build); the incumbent VM runs a real Xorg server", "effect": "display/input stack differs; GUI timing and some rendering can differ"},
    {"id": "no_display_manager", "gap": "no display manager (gdm) or login session runs in a Drums session; gnome-session is started directly", "effect": "no greeter/lock/notification services of a logged-in desktop"},
    {"id": "private_bus_non_activating", "gap": "the session has a private system bus that CANNOT activate services (no upower, accountsservice, polkit, logind); LSM (SELinux/AppArmor) hooks are dropped", "effect": "apps that expect those services get ServiceUnknown"},
    {"id": "kernel", "gap": "Drums runs on the host kernel, the VM on its own 6.5.0-15 kernel", "effect": "structural; not removable inside this architecture"},
    {"id": "screen_lock_and_clock", "gap": "screen lock was observed disabled; the guest clock and timezone are not yet aligned with the VM's (guest showed a different date than the host's EDT clock)", "effect": "date/time-dependent tasks and screenshots can differ"},
]

PARITY_PROBES = {
    "kernel": "uname -r", "os_release": "grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release", "user": "id -un; id -u", "home": "echo $HOME",
    "timezone_file": "cat /etc/timezone 2>&1", "tz_env": "echo TZ=$TZ", "date_utc": "date -u +%Y-%m-%dT%H:%M:%SZ", "date_local": "date +%Z%z", "locale": "locale 2>&1",
    "fonts_count": "fc-list 2>/dev/null | wc -l", "fonts_names_sha": "fc-list : family 2>/dev/null | sort -u | sha256sum | cut -c1-16",
    "display": "xdpyinfo 2>/dev/null | grep -E 'dimensions|depth of root' || echo no-xdpyinfo", "x_server": "xdpyinfo 2>/dev/null | grep -E 'vendor string|version number' || echo no-xdpyinfo",
    "wm_name": "xprop -root _NET_SUPPORTING_WM_CHECK 2>&1 | head -1",
    "gnome_shell": "gnome-shell --version 2>&1", "chrome": "(google-chrome --version || chromium --version || chromium-browser --version) 2>&1 | head -1",
    "office": "soffice --version 2>&1 | head -1", "python": "python3 --version 2>&1", "screen_lock": "gsettings get org.gnome.desktop.screensaver lock-enabled 2>&1",
    "idle_delay": "gsettings get org.gnome.desktop.session idle-delay 2>&1", "display_manager": "pgrep -x gdm3 >/dev/null && echo gdm3-running || echo no-display-manager",
    "system_bus_socket": "stat -c '%d %i' /run/dbus/system_bus_socket 2>&1", "mnt_ns": "readlink /proc/1/ns/mnt", "pid_ns": "readlink /proc/1/ns/pid",
    "bus_id": "dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.GetId 2>&1",
    "bus_activatable": "dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListActivatableNames 2>&1",
}
BUS_REQUIRED = ("system_bus_socket", "mnt_ns", "bus_id", "bus_activatable")


def capture_parity(run):
    """run(script) -> {"stdout","stderr","returncode"}. Every probe is kept RAW with its return code; a probe that cannot run is recorded as an error, never as a blank success."""
    out = {}
    for name, script in PARITY_PROBES.items():
        try:
            r = run(script)
            out[name] = {"rc": r["returncode"], "out": (r["stdout"] or "").strip()[:2000], "err": (r["stderr"] or "").strip()[:300]}
        except Exception as e:
            out[name] = {"rc": None, "out": "", "err": f"probe failed: {type(e).__name__}: {str(e)[:200]}"}
    return out


def parity_report(vm, drums):
    """Field by field; the VM side may be None (not captured). `fair_comparison` is False whenever any listed gap exists, which is always until a gap is removed in code AND re-captured."""
    fields = {}
    for k in PARITY_PROBES:
        d = drums.get(k) if drums else None
        v = vm.get(k) if vm else None
        fields[k] = {"drums": d, "vm": v, "equal": None if (v is None or d is None) else (v["out"] == d["out"] and v["rc"] == d["rc"])}
    return {"fields": fields, "vm_captured": vm is not None, "different": sorted(k for k, f in fields.items() if f["equal"] is False), "not_comparable": sorted(k for k, f in fields.items() if f["equal"] is None),
            "gaps": PARITY_GAPS, "fair_comparison": False, "label": "ARCHITECTURAL_NOT_FAIR_COMPARISON",
            "why": "the listed gaps (Xvfb vs Xorg, no display manager, a non-activating private bus, the kernel, clock/lock) are not resolved; a captured difference is data, an uncaptured one is unknown"}


def bus_problems(captures):
    """From the parity captures of two or more sessions: the buses must be real, different and private, judged on (device, inode) and namespaces, with probe failures counted as failures."""
    from .bus_validate import identity_problems
    problems, buses = [], []
    for i, cap in enumerate(captures):
        for k in BUS_REQUIRED:
            p = cap.get(k, {})
            if p.get("rc") != 0:
                problems.append(f"session {i}: probe {k} failed (rc {p.get('rc')}): {p.get('err') or p.get('out')}")
        sock = cap.get("system_bus_socket", {}).get("out", "").split()
        bid = cap.get("bus_id", {}).get("out", "")
        buses.append({"socket": f"session{i}", "socket_dev": int(sock[0]) if len(sock) == 2 and sock[0].isdigit() else None, "socket_inode": int(sock[1]) if len(sock) == 2 and sock[1].isdigit() else None,
                      "id": (bid.split('"')[1] if '"' in bid else None), "get_id_rc": cap.get("bus_id", {}).get("rc"), "activatable_rc": cap.get("bus_activatable", {}).get("rc")})
    if len(buses) == 2 and not problems:
        problems += identity_problems(buses)
        if captures[0]["mnt_ns"]["out"] == captures[1]["mnt_ns"]["out"]:
            problems.append("the two sessions share one mount namespace")
        for i, cap in enumerate(captures):
            names = [l.split('"')[1] for l in cap["bus_activatable"]["out"].splitlines() if '"' in l]
            if [n for n in names if n != "org.freedesktop.DBus"]:
                problems.append(f"session {i}: activatable names besides org.freedesktop.DBus: {names}")
    return problems


def two_session_gate(a, b):
    """a, b: guests with .run(script) -> dict and .step(code). Canaries (a file and a live process with a unique marker) made in A must be invisible to B; a cold reset of B must leave A's alive."""
    token = uuid.uuid4().hex[:10]
    res = {"token": token}
    a.step(f"open('/tmp/canary_{token}','w').write('A')")
    a.step("import subprocess; subprocess.Popen(['sh','-c','exec sleep 600 # canary_%s'], start_new_session=True)" % token)
    time.sleep(0.5)
    seen = lambda g: (g.run(f"test -e /tmp/canary_{token} && echo file").get("stdout", "").strip(), g.run(f"pgrep -f canary_{token} | head -1").get("stdout", "").strip())
    sa, sb = seen(a), seen(b)
    res.update({"canary_visible_in_A": sa == ("file", sa[1]) and bool(sa[1]), "canary_file_leaked_to_B": sb[0] != "", "canary_process_leaked_to_B": sb[1] != ""})
    b.reset()
    sa2 = seen(a)
    res["A_canary_survives_B_reset"] = sa2[0] == "file" and bool(sa2[1])
    res["pass"] = res["canary_visible_in_A"] and not res["canary_file_leaked_to_B"] and not res["canary_process_leaked_to_B"] and res["A_canary_survives_B_reset"]
    return res


class PhaseClock:
    """Separate clocks per phase; `unavailable(name, why)` records null (not 0) for a phase this configuration does not have."""
    PHASES = ("cold_setup", "checkpoint_save", "restore", "hygiene", "task", "evaluation")

    def __init__(self, clock=time.time):
        self.clock, self.t, self.why = clock, {}, {}

    @contextmanager
    def phase(self, name):
        if name not in self.PHASES:
            raise ValueError(name)
        t0 = self.clock()
        try:
            yield
        finally:
            self.t[name] = self.t.get(name, 0.0) + (self.clock() - t0)

    def unavailable(self, name, why):
        self.why[name] = why

    def report(self):
        return {n: (self.t[n] if n in self.t else None) for n in self.PHASES} | {"unavailable": dict(self.why)}


def state_bytes(path):
    """Bytes of a directory tree (a session's private writable layer); None if it does not exist (not 0)."""
    if not os.path.isdir(path):
        return None
    tot = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                tot += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return tot


def gate_applicability(cfg):
    """Which gates can run in this configuration, and the label every timing carries."""
    img = cfg.get("drums_base_root", "/") != "/"
    g = {"score_before_correct_wrong": {"runs": True, "kind": "cold-reset semantics (every score from a fresh reset)"},
         "reset_hygiene": {"runs": True, "kind": "process token, file marker, Writer gone after reset"},
         "two_session_interference": {"runs": True, "kind": "canaries and bus/namespace identity"},
         "snapshot_state_and_gui_state": {"runs": False, "why": "no checkpoint/restore with an image base root: the CRIU path assumes '/' as the lower layer; the snapshot gates apply to snapshot incumbents only"},
         "checkpoint_save_restore_clocks": {"runs": False, "why": "no CRIU checkpoint/restore with an image base root (same reason); reported as null"}}
    return {"gates": g, "image_base_root": img, "label": "ARCHITECTURAL_NOT_FAIR_COMPARISON", "gaps": [x["id"] for x in PARITY_GAPS], "training_economics_claim": False}


def preflight(cfg):
    """Offline checks before any host is paid for. Returns {"checks": [...], "all_ok": bool, "command": str}."""
    from envd_linux import rootcheck
    checks = []

    def chk(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    need = {"drums_base_root": "/mnt/vm-image-ro", "drums_template_overlay": None, "drums_home": "/home/user", "drums_desktop": "gnome", "drums_uid": 1000, "drums_system_bus": True}
    for k, v in need.items():
        chk(f"config_{k}", cfg.get(k) not in (None, "", False, "/") if v is not None else bool(cfg.get(k)), f"{k}={cfg.get(k)!r}")
    if all(c["ok"] for c in checks):
        found = rootcheck.check_userland([cfg["drums_template_overlay"], cfg["drums_base_root"]], kit=cfg.get("kit", "/opt/drums-osworld-real"), desktop=cfg["drums_desktop"], uid=int(cfg["drums_uid"]),
                                         gid=int(cfg.get("drums_gid", cfg["drums_uid"])), user=cfg.get("drums_user", "user"), home=cfg["drums_home"], system_bus=True, xvfb=cfg.get("drums_xvfb", "Xvfb"))
        chk("rootcheck_userland", not found["problems"], "; ".join(found["problems"]) or f"ok; warnings: {found['warnings']}")
    chk("two_sessions_possible", int(cfg.get("drums_sessions", 2)) >= 2, f"drums_sessions={cfg.get('drums_sessions', 2)}")
    chk("incumbent_cold_reset_only", cfg.get("incumbent") != "virtualbox", "an image base root has no checkpoint/restore; the snapshot incumbent is not run by this driver")
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks), "applicability": gate_applicability(cfg),
            "command": "python -m drums_compare.fullroot_gates run --config CONFIG.json --out OUTDIR  (authorized host only; after the offline preflight reports ALL OK)"}


class _Guest:
    def __init__(self, env, lease, task):
        from . import guestexec
        self.env, self.lease, self.task, self._ge = env, lease, task, guestexec

    def run(self, script):
        return self._ge.guest_run(self.env, script)

    def step(self, code):
        return self.env.step(code)

    def reset(self):
        return self.lease.reset(self.task)


def run_live(cfg, out):
    """The live gates (authorized host only). Never run in this repository's tests; the gate functions it calls are."""
    from . import host_run as hr
    from . import osworld_matched as om
    from . import stages
    work = cfg.get("work_dir", os.path.join(out, "work"))
    os.makedirs(work, exist_ok=True)
    de, stubs = hr.strict_import(tuple(cfg.get("allow_stub", [])))
    task = json.load(open(os.path.join(cfg["osworld_src"], "evaluation_examples", "examples", "os", hr.FIRST_TASK + ".json")))
    clock = PhaseClock()
    res = {"label": "ARCHITECTURAL_NOT_FAIR_COMPARISON", "training_economics_claim": False, "gaps": PARITY_GAPS, "applicability": gate_applicability(cfg), "stubbed_modules": stubs, "config_sha256": hr.config_hash(cfg)}
    sup = None
    envs = []
    try:
        with clock.phase("cold_setup"):
            sup, pool, prep_s = stages.build_drums(cfg, work)
            factory = om.drums_factory(pool, desktop_env_module=de, **stages.hr_kw(cfg))
            envs = [factory(i) for i in range(2)]
            leases = [om._DesktopEnvLease(e) for e in envs]
            guests = [_Guest(e, l, task) for e, l in zip(envs, leases)]
            for g in guests:
                g.reset()
        res["drums_prep_s"] = prep_s
        res["parity_capture"] = [capture_parity(g.run) for g in guests]
        res["parity_report_vm_not_captured"] = parity_report(None, res["parity_capture"][0])
        res["bus_problems"] = bus_problems(res["parity_capture"])
        sh_out = lambda script, g=guests[0]: g.run(script)["stdout"]
        step = lambda code, g=guests[0]: g.step(code)
        with clock.phase("hygiene"):
            res["reset_hygiene"] = hr.cold_isolation_gate(leases[0], task, sh_out, step)
        with clock.phase("task"):
            res["scores"] = hr.cold_score_gate(leases[0], task, stages.CORRECT, stages.WRONG)
        res["two_session"] = two_session_gate(guests[0], guests[1])
        for ph in ("checkpoint_save", "restore"):
            clock.unavailable(ph, "no checkpoint/restore with an image base root (CRIU assumes '/' as the lower layer)")
        res["state_bytes"] = {f"s{i}_upper": state_bytes(os.path.join(work, "state", f"s{i}", "upper")) for i in range(2)}
        res["clocks"] = clock.report()
        res["all_ok"] = not res["bus_problems"] and res["reset_hygiene"]["pass"] and res["scores"]["pass"] and res["two_session"]["pass"]
    except Exception as e:  # raw: a failed gate is a result
        res["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        res["all_ok"] = False
    finally:
        for e in envs:
            try:
                e.close()
            except Exception:
                pass
        if sup is not None:
            sup.close()
    json.dump(res, open(os.path.join(out, "fullroot_gates.json"), "w"), indent=1, default=str)
    print("FULLROOT GATES " + ("PASS" if res["all_ok"] else "FAIL: see fullroot_gates.json") + " (ARCHITECTURAL_NOT_FAIR_COMPARISON; no timing claim)")
    return 0 if res["all_ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fullroot_gates")
    ap.add_argument("stage", choices=["preflight", "run"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    cfg = json.load(open(a.config))
    if a.stage == "preflight":
        r = preflight(cfg)
        json.dump(r, open(os.path.join(a.out, "fullroot_preflight.json"), "w"), indent=1, default=str)
        for c in r["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ": " + c["detail"])
        print("ALL OK. next: " + r["command"] if r["all_ok"] else "REFUSED: a preflight check failed")
        return 0 if r["all_ok"] else 1
    r = preflight(cfg)
    if not r["all_ok"]:
        print("REFUSED: the offline preflight failed; run it first")
        return 1
    return run_live(cfg, a.out)


if __name__ == "__main__":
    sys.exit(main())
