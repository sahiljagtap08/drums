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


HEX32 = __import__("re").compile(r"^[0-9a-f]{32}$")
NS = __import__("re").compile(r"^mnt:\[\d+\]$")


def parse_bus(cap, i):
    """One session's bus facts, STRICTLY parsed. Returns (facts, problems): a missing probe, a non-zero rc, a malformed device/inode, namespace, bus ID or activation listing is a problem."""
    problems, f = [], {"socket": f"session{i}"}
    for k in BUS_REQUIRED:
        p = cap.get(k)
        if not isinstance(p, dict) or p.get("rc") != 0:
            problems.append(f"session {i}: probe {k} failed or is missing (rc {p.get('rc') if isinstance(p, dict) else None}): {(p or {}).get('err') or (p or {}).get('out')}")
    if problems:
        return None, problems
    st = cap["system_bus_socket"]["out"].split()
    if len(st) != 2 or not all(x.isdigit() for x in st):
        problems.append(f"session {i}: malformed socket stat {cap['system_bus_socket']['out']!r} (need 'device inode')")
    else:
        f["socket_dev"], f["socket_inode"] = int(st[0]), int(st[1])
    ns = cap["mnt_ns"]["out"].strip()
    if not NS.match(ns):
        problems.append(f"session {i}: malformed mount namespace {ns!r}")
    f["mnt_ns"] = ns
    ids = [l.split('"')[1] for l in cap["bus_id"]["out"].splitlines() if l.count('"') >= 2]
    if len(ids) != 1 or not HEX32.match(ids[0]):
        problems.append(f"session {i}: no single valid 32-hex bus ID in {cap['bus_id']['out']!r}")
    else:
        f["id"] = ids[0]
    names = [l.split('"')[1] for l in cap["bus_activatable"]["out"].splitlines() if l.count('"') >= 2]
    if "org.freedesktop.DBus" not in names:
        problems.append(f"session {i}: the activation listing is empty or malformed ({cap['bus_activatable']['out']!r}); an unreadable inventory is not an empty one")
    f["activatable"] = names
    f.update(get_id_rc=0, activatable_rc=0)
    return (None if problems else f), problems


def bus_problems(captures):
    """EXACTLY two valid captures are required. Judged on (device, inode), mount namespaces, bus IDs and activation names. NARROW: it does not capture the host socket before/after or the bus's child
    processes (the offline `bus_validate` and the session's own record do); a pass here is not a security pass."""
    from .bus_validate import identity_problems
    if len(captures) != 2:
        return [f"exactly two session captures are required, got {len(captures)}"]
    problems, buses = [], []
    for i, cap in enumerate(captures):
        f, pr = parse_bus(cap, i)
        problems += pr
        buses.append(f)
    if problems:
        return problems
    problems += identity_problems(buses)
    if buses[0]["mnt_ns"] == buses[1]["mnt_ns"]:
        problems.append("the two sessions share one mount namespace")
    for i, f in enumerate(buses):
        extra = [n for n in f["activatable"] if n != "org.freedesktop.DBus"]
        if extra:
            problems.append(f"session {i}: activatable names besides org.freedesktop.DBus: {extra}")
    return problems


SCAN_CODE = """import os,sys
proc,tok=sys.argv[1],sys.argv[2]
me=str(os.getpid())
for p in sorted(os.listdir(proc)):
    if not p.isdigit() or p==me:
        continue
    try:
        argv=open(proc+"/"+p+"/cmdline","rb").read().split(b"\\0")
        st=open(proc+"/"+p+"/stat").read().rsplit(")",1)[1].split()
    except OSError:
        continue
    if tok.encode() in argv and st[0]!="Z":
        print(p,st[19])
"""


def scan_script(token, proc="/proc"):
    """Shell command printing 'pid starttime' for every live process one of whose ARGV ELEMENTS is exactly `canary_<token>`. Exact element equality means the probe itself (whose argv holds the long code string)
    never matches, and an exec'd program that keeps the marker as an argument keeps being found; the marker is not a shell comment."""
    import shlex
    return f"python3 -c {shlex.quote(SCAN_CODE)} {shlex.quote(proc)} {shlex.quote('canary_' + token)}"


def canary_step(token):
    return ("import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)','canary_%s'], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)" % token)


def parse_scan(out):
    rows = []
    for line in (out or "").splitlines():
        f = line.split()
        if len(f) == 2 and all(x.isdigit() for x in f):
            rows.append((int(f[0]), int(f[1])))
    return rows


def two_session_gate(a, b, proc="/proc", proc_b=None):
    """a, b: guests with .run(script) -> dict, .step(code), .reset(). A canary file and a live process (marked by an exact argv element) made in A must be invisible to B; the SAME process (pid and start time)
    must still be alive in A after B's cold reset. The canary is killed at the end, by pid AND start time, whatever happened."""
    token = uuid.uuid4().hex[:10]
    res = {"token": token}
    sc_a, sc_b = scan_script(token, proc), scan_script(token, proc_b or proc)
    try:
        a.step(f"open('/tmp/canary_{token}','w').write('A')")
        a.step(canary_step(token))
        found = []
        for _ in range(20):
            found = parse_scan(a.run(sc_a)["stdout"])
            if found:
                break
            time.sleep(0.25)
        res["canary_in_A"] = found
        fa = a.run(f"test -e /tmp/canary_{token} && echo file")["stdout"].strip()
        fb = b.run(f"test -e /tmp/canary_{token} && echo file")["stdout"].strip()
        pb = parse_scan(b.run(sc_b)["stdout"])
        res.update({"canary_alive_in_A": len(found) == 1 and fa == "file", "canary_file_leaked_to_B": fb != "", "canary_process_leaked_to_B": bool(pb)})
        b.reset()
        after = parse_scan(a.run(sc_a)["stdout"])
        res["canary_in_A_after_B_reset"] = after
        res["A_same_process_survives_B_reset"] = len(found) == 1 and after == found  # same pid AND start time: not a lookalike
        res["pass"] = (res["canary_alive_in_A"] and not res["canary_file_leaked_to_B"] and not res["canary_process_leaked_to_B"] and res["A_same_process_survives_B_reset"])
    finally:
        try:
            for pid, start in parse_scan(a.run(sc_a)["stdout"]):
                a.run(f"kill {pid} 2>/dev/null; rm -f /tmp/canary_{token}")
        except Exception as e:
            res["cleanup_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return res


class PhaseClock:
    """Separate MONOTONIC clocks per phase; `unavailable(name, why)` records null (not 0) for a phase this configuration really does not have."""
    PHASES = ("cold_setup", "reset_setup", "task", "evaluation", "hygiene", "checkpoint_save", "restore")

    def __init__(self, clock=time.monotonic):
        self.clock, self.t, self.why = clock, {}, {}

    def add(self, name, seconds):
        if name not in self.PHASES:
            raise ValueError(name)
        self.t[name] = self.t.get(name, 0.0) + seconds

    @contextmanager
    def phase(self, name):
        t0 = self.clock()
        try:
            yield
        finally:
            self.add(name, self.clock() - t0)

    def unavailable(self, name, why):
        self.why[name] = why

    def report(self):
        return {n: (self.t[n] if n in self.t else None) for n in self.PHASES} | {"unavailable": dict(self.why)}


class TimedLease:
    """Wraps a lease so the UNCHANGED gate functions run as before while each call is timed: reset -> reset_setup, step -> task, evaluate -> evaluation, save_branch/restore_branch -> their own phases.
    `bucket` (set by the caller around a whole gate) attributes everything inside it to one phase instead (for example `hygiene`) so nothing is counted twice."""
    MAP = {"reset": "reset_setup", "step": "task", "evaluate": "evaluation", "save_branch": "checkpoint_save", "restore_branch": "restore"}

    def __init__(self, lease, clock):
        self._lease, self._clock = lease, clock
        self.bucket = None

    def __getattr__(self, name):
        attr = getattr(self._lease, name)
        if name not in self.MAP or not callable(attr):
            return attr

        def timed(*a, **k):
            with self._clock.phase(self.bucket or self.MAP[name]):
                return attr(*a, **k)
        return timed


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


REQUIRED = {"drums_desktop": "gnome", "drums_user": "user", "drums_uid": 1000, "drums_gid": 1000, "drums_home": "/home/user", "drums_system_bus": True, "incumbent": "docker_kvm"}


def _build_drums_safety():
    """AST of stages.build_drums: the Supervisor it builds must be root_mode='full' with netns=True (the private bus's safety condition) and must receive the desktop/uid/bus settings."""
    import ast
    import inspect
    from . import stages
    tree = ast.parse(inspect.getsource(stages.build_drums))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Supervisor":
            kw = {k.arg: k.value for k in n.keywords}
            full = isinstance(kw.get("root_mode"), ast.Constant) and kw["root_mode"].value == "full"
            net = isinstance(kw.get("netns"), ast.Constant) and kw["netns"].value is True
            passes = any(k.arg is None for k in n.keywords)  # **desktop_kw(cfg)
            return full and net and passes
    return False


def preflight(cfg, scope="config"):
    """Two scopes, stated plainly.
    config: OFFLINE checks of this repository's code and of the configuration VALUES (exact required settings, the full-root/netns safety of build_drums, format of the pinned hashes). It says NOTHING about
            whether any image root, kit or task file on a host works.
    host:   everything of `config` PLUS checks that need the host's files: the mounted read-only image root, the overlay, the kit, the OSWorld checkout and task file with its pinned sha256, and the actual
            merged-root `rootcheck`. Only `host` ALL OK allows `run`."""
    import re
    checks = []

    def chk(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
    for k, want in REQUIRED.items():
        got = cfg.get(k, "gid-default" if k == "drums_gid" and "drums_uid" in cfg else None)
        if k == "drums_gid" and got == "gid-default":
            got = cfg.get("drums_uid")
        chk(f"config_{k}", got == want and type(got) is type(want), f"{k}={cfg.get(k)!r} (required exactly {want!r})")
    for k in ("drums_base_root", "drums_template_overlay"):
        v = cfg.get(k)
        chk(f"config_{k}", isinstance(v, str) and os.path.isabs(v) and os.path.normpath(v) == v and v != "/" and re.fullmatch(r"[A-Za-z0-9_./-]+", v) is not None, f"{k}={v!r} (absolute, normalized, not /, safe characters)")
    chk("config_drums_sessions", int(cfg.get("drums_sessions", 2)) >= 2, f"drums_sessions={cfg.get('drums_sessions', 2)}")
    chk("config_task_sha256", isinstance(cfg.get("task_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", cfg.get("task_sha256", "")) is not None, "the pinned sha256 of the task JSON must be given (64 hex)")
    chk("config_osworld_src", isinstance(cfg.get("osworld_src"), str) and bool(cfg.get("osworld_src")), "osworld_src is required")
    chk("code_build_drums_full_root_netns", _build_drums_safety(), "stages.build_drums constructs the Supervisor with root_mode='full', netns=True and the desktop/uid/bus settings")
    if scope == "host":
        from envd_linux import rootcheck
        from . import host_run as hr
        base, ov, kit = cfg.get("drums_base_root", ""), cfg.get("drums_template_overlay", ""), cfg.get("kit", "/opt/drums-osworld-real")
        chk("host_base_root_is_dir", os.path.isdir(base), base)
        chk("host_overlay_is_dir", os.path.isdir(ov), ov)
        tp = os.path.join(cfg.get("osworld_src", ""), "evaluation_examples", "examples", "os", hr.FIRST_TASK + ".json")
        actual = hr.file_sha256(tp) if os.path.exists(tp) else None
        chk("host_task_sha256", actual is not None and actual == cfg.get("task_sha256"), f"{tp}: actual {actual}")
        chk("host_kit_present", os.path.exists(os.path.join(kit, "server", "main.py")) and os.path.exists(os.path.join(kit, "venv", "bin", "python")) or os.path.isdir(ov), f"kit {kit} (or inside the overlay)")
        if os.path.isdir(base) and os.path.isdir(ov):
            found = rootcheck.check_userland([ov, base], kit=kit, desktop=cfg["drums_desktop"], uid=int(cfg["drums_uid"]), gid=int(cfg["drums_gid"]), user=cfg["drums_user"], home=cfg["drums_home"],
                                             system_bus=True, xvfb=cfg.get("drums_xvfb", "Xvfb"))
            chk("host_merged_root_rootcheck", not found["problems"], "; ".join(found["problems"]) or f"ok; warnings: {found['warnings']}")
    return {"scope": scope, "checks": checks, "all_ok": all(c["ok"] for c in checks), "applicability": gate_applicability(cfg),
            "scope_note": ("OFFLINE config/code checks only: no image root, kit or task file was examined" if scope == "config" else "host checks included, including the merged-root rootcheck"),
            "command": "python -m drums_compare.fullroot_gates run --config CONFIG.json --out OUTDIR  (authorized host only; after `preflight --scope host` reports ALL OK)"}


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


def run_live(cfg, out, deps=None):
    """The live gates (authorized host only). `deps` replaces the host-bound pieces in tests (build_drums, drums factory, strict import, task loader, lease class); nothing else changes. Cleanup runs
    whatever happened and a failing cleanup is RECORDED, never allowed to hide the gate result."""
    from . import host_run as hr
    from . import osworld_matched as om
    from . import stages
    d = deps or {}
    work = cfg.get("work_dir", os.path.join(out, "work"))
    os.makedirs(work, exist_ok=True)
    clock = PhaseClock()
    res = {"label": "ARCHITECTURAL_NOT_FAIR_COMPARISON", "training_economics_claim": False, "gaps": PARITY_GAPS, "applicability": gate_applicability(cfg), "config_sha256": hr.config_hash(cfg),
           "bus_scope": "NARROW: strict parse of two in-session captures (device+inode, mount namespace, bus ID, activation names). NOT captured here: the host socket before/after and the bus's child processes; no security pass is implied",
           "cleanup_errors": []}
    sup = None
    envs = []
    try:
        de, stubs = (d.get("strict_import") or hr.strict_import)(tuple(cfg.get("allow_stub", [])))  # refuses stubbed modules BEFORE anything is built
        res["stubbed_modules"] = stubs
        task = (d.get("task") or (lambda: json.load(open(os.path.join(cfg["osworld_src"], "evaluation_examples", "examples", "os", hr.FIRST_TASK + ".json")))))()
        with clock.phase("cold_setup"):
            sup, pool, prep_s = (d.get("build_drums") or stages.build_drums)(cfg, work)
            factory = (d.get("factory") or (lambda pool, de: om.drums_factory(pool, desktop_env_module=de, **stages.hr_kw(cfg))))(pool, de)
            envs = [factory(i) for i in range(2)]
            lease_cls = d.get("lease_cls") or om._DesktopEnvLease
            raw = [lease_cls(e) for e in envs]
            leases = [TimedLease(l, clock) for l in raw]
            guests = [_Guest(e, l, task) for e, l in zip(envs, raw)]
        res["drums_prep_s"] = prep_s
        from . import guestexec
        res["guest_exec_selftest"] = [guestexec.selftest(lambda script, e=e: guestexec.guest_run(e, script)) for e in envs]
        if not all(x["pass"] for x in res["guest_exec_selftest"]):
            raise guestexec.GuestExecError(f"the probe helper failed its selftest: {res['guest_exec_selftest']}")
        for g in guests:
            g.reset()
        res["parity_capture"] = [capture_parity(g.run) for g in guests]
        res["parity_report_vm_not_captured"] = parity_report(None, res["parity_capture"][0])
        res["bus_problems"] = bus_problems(res["parity_capture"])
        sh_out = lambda script, g=guests[0]: g.run(script)["stdout"]
        step = lambda code, g=guests[0]: g.step(code)
        leases[0].bucket = "hygiene"
        res["reset_hygiene"] = hr.cold_isolation_gate(leases[0], task, sh_out, step)
        leases[0].bucket = None
        res["scores"] = hr.cold_score_gate(leases[0], task, stages.CORRECT, stages.WRONG)  # reset/step/evaluate are timed apart by TimedLease
        res["two_session"] = two_session_gate(guests[0], guests[1])
        for ph in ("checkpoint_save", "restore"):
            clock.unavailable(ph, "no checkpoint/restore with an image base root in the cold configuration (CRIU assumes '/' as the lower layer)")
        res["state_bytes"] = {f"s{i}_upper": state_bytes(os.path.join(sup._dir(i), "upper")) for i in range(2)}  # the Supervisor's own layout
        res["clocks"] = clock.report()
        res["all_ok"] = (not res["bus_problems"] and res["reset_hygiene"]["pass"] and res["scores"]["pass"] and res["two_session"]["pass"])
    except Exception as e:  # raw: a failed gate is a result
        res["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        res["all_ok"] = False
        res["clocks"] = clock.report()
    finally:
        for e in envs:
            try:
                e.close()
            except Exception as ce:
                res["cleanup_errors"].append(f"env.close: {type(ce).__name__}: {str(ce)[:200]}")
        if sup is not None:
            try:
                sup.close()
            except Exception as ce:
                res["cleanup_errors"].append(f"supervisor.close: {type(ce).__name__}: {str(ce)[:200]}")
        if res["cleanup_errors"]:
            res["all_ok"] = False
    json.dump(res, open(os.path.join(out, "fullroot_gates.json"), "w"), indent=1, default=str)
    print("FULLROOT GATES " + ("PASS" if res["all_ok"] else "FAIL: see fullroot_gates.json") + " (ARCHITECTURAL_NOT_FAIR_COMPARISON; no timing claim)")
    return 0 if res["all_ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fullroot_gates")
    ap.add_argument("stage", choices=["preflight", "run"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scope", choices=["config", "host"], default="config")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    cfg = json.load(open(a.config))
    if a.stage == "preflight":
        r = preflight(cfg, a.scope)
        json.dump(r, open(os.path.join(a.out, f"fullroot_preflight_{a.scope}.json"), "w"), indent=1, default=str)
        for c in r["checks"]:
            print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ": " + c["detail"])
        print(f"{r['scope'].upper()} CHECKS OK ({r['scope_note']})" + ("; next: " + r["command"] if r["scope"] == "host" else "; the host scope is still to run") if r["all_ok"] else "REFUSED: a preflight check failed")
        return 0 if r["all_ok"] else 1
    r = preflight(cfg, "host")
    if not r["all_ok"]:
        print("REFUSED: the HOST-scope preflight failed; run `preflight --scope host` first")
        return 1
    return run_live(cfg, a.out)


if __name__ == "__main__":
    sys.exit(main())
