"""Mechanism C feasibility probe (docs/l1-shared-runtime-design.md section 14, results in section 15). PROTOTYPE, sandbox-local.

Run it with an interpreter that has the public `desktop_env` (the OSWorld venv), for example:

    PYTHONPATH=<OSWorld checkout>:<repo>/envd-linux:<repo>/compare DRUMS_CRIU_HOME=<private criu dir> \\
        <osw venv>/bin/python -m envd_linux.l1proto.criu_probe --out probe.json --task <e0df059f.json>

It runs, in order: the ladder (criu check, a server-only checkpoint and restore, a checkpoint with Chromium), the correctness checks C1 to C3, the
latency comparison (cold vs restore, interleaved), and the memory slope. Everything is written to --out whatever fails; a failed rung is a result."""

import argparse
import glob
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import stat as statmod
import urllib.request
from unittest.mock import MagicMock

from ..criu import Checkpoint, Criu
from ..session import Supervisor
from ..template import build_home_template
from .bench import bootstrap_diff, log, ols_slope, pct, used_kib

KIT = "/opt/drums-osworld-real"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
HOME = "/home/drums"


stubbed = []


def import_desktop_env():
    """The public desktop_env imports optional heavy modules at import time; stub the ones this venv lacks (as the dry-run driver does)."""
    while True:
        try:
            import desktop_env.desktop_env as d
            return d
        except ModuleNotFoundError as e:
            m = (e.name or "").split(".")[0]
            if not m or m.startswith("desktop_env") or m in stubbed:
                raise
            stubbed.append(m)
            sys.modules[m] = MagicMock(name=m)
            for k in [k for k in sys.modules if k.startswith("desktop_env")]:
                del sys.modules[k]


# ------------------------------------------------------------------ probes of a running session
def screenshot(ip, timeout=5):
    with urllib.request.urlopen(f"http://{ip}:5000/screenshot", timeout=timeout) as r:
        return r.read()


def changed_fraction(png_a, png_b):
    import io
    from PIL import Image, ImageChops
    a, b = Image.open(io.BytesIO(png_a)).convert("RGB"), Image.open(io.BytesIO(png_b)).convert("RGB")
    if a.size != b.size:
        return 1.0
    d = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 16 else 0)
    return d.histogram()[255] / (a.size[0] * a.size[1])


def cdp_titles(init_pid):
    """Titles of the page targets of the session's browser, asked from inside the session's network namespace."""
    code = "import urllib.request,json;print(json.dumps([t.get('title') for t in json.load(urllib.request.urlopen('http://127.0.0.1:9222/json/list',timeout=1)) if t.get('type')=='page']))"
    r = subprocess.run(["nsenter", "-t", str(init_pid), "-n", "/usr/bin/python3", "-c", code], capture_output=True, text=True, timeout=10)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []


def agent_ready(info, blank_png, timeout=60, want_browser=True):
    """Ready = the guest server returns a screenshot that differs from a blank desktop in more than 10% of pixels AND the browser's page title
    is the loaded page's. Polls every 20 ms; returns the extra seconds waited, or None on timeout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if not want_browser:
                screenshot(info["ip"])
                return time.time() - t0
            if "drums-ready" in cdp_titles(info["init_pid"]) and changed_fraction(screenshot(info["ip"]), blank_png) > 0.10:
                return time.time() - t0
        except Exception:
            pass
        time.sleep(0.02)
    return None


def start_browser_in(info):
    """The server-only fallback arm: cold-start Chromium inside an already restored session (same command line as session_init)."""
    html = "data:text/html,<title>drums-ready</title><h1>ready</h1>"
    cmd = ["setsid", "nsenter", "-t", str(info["init_pid"]), "-m", "-p", "-n", "-u", "-i", "--", "env", f"DISPLAY=:{info['display']}", f"HOME={HOME}",
           CHROME, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check", f"--user-data-dir={HOME}/profile",
           "--remote-debugging-port=9222", "--window-size=1280,800", html]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ------------------------------------------------------------------ the C3 audit
def descendants(pid):
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        for t in glob.glob(f"/proc/{p}/task/*/children"):
            try:
                todo += [int(x) for x in open(t).read().split()]
            except OSError:
                pass
    return sorted(set(out))


def ns_inode(pid, kind):
    return os.readlink(f"/proc/{pid}/ns/{kind}")


def fd_objects(pid):
    """{fd target: [writable?]} for every fd of `pid`; regular files are keyed by (dev, inode) so two mount namespaces compare by identity."""
    out = []
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            tgt = os.readlink(f"/proc/{pid}/fd/{fd}")
            flags = int([ln for ln in open(f"/proc/{pid}/fdinfo/{fd}") if ln.startswith("flags")][0].split()[1], 8)
            if tgt.startswith(("socket:", "pipe:")):
                out.append((tgt, flags & 3 != 0 or tgt.startswith("socket:")))
            elif tgt.startswith("/"):
                st = os.stat(f"/proc/{pid}/fd/{fd}")
                kind = "chr" if statmod.S_ISCHR(st.st_mode) else "file" if statmod.S_ISREG(st.st_mode) else None
                if kind:
                    out.append((f"{kind}:{st.st_dev}:{st.st_ino}:{tgt}", flags & 3 != 0))
        except (OSError, ValueError, IndexError):
            continue
    return out


def lock_keys(pids):
    keys = set()
    for ln in open("/proc/locks"):
        f = ln.split()
        if len(f) >= 6 and int(f[4]) in pids:
            keys.add(f[5])  # maj:min:inode
    return keys


def audit_pair(a, b):
    """C3 for two live restored siblings. Returns {check: {ok, detail}} and an overall pass."""
    pa, pb = descendants(a["init_pid"]), descendants(b["init_pid"])
    res = {"processes": {"a": len(pa), "b": len(pb)}}
    res["pid_sets_disjoint"] = not (set(pa) & set(pb))
    res["distinct_namespaces"] = {k: ns_inode(a["init_pid"], k) != ns_inode(b["init_pid"], k) for k in ("pid", "mnt", "net", "ipc", "uts")}
    oa, ob = {}, {}
    for p in pa:
        for k, w in fd_objects(p):
            oa[k] = oa.get(k, False) or w
    for p in pb:
        for k, w in fd_objects(p):
            ob[k] = ob.get(k, False) or w
    shared = sorted(set(oa) & set(ob))
    ipc_shared = [k for k in shared if k.startswith(("socket:", "pipe:"))]
    files_shared = [(k, oa[k], ob[k]) for k in shared if k.startswith("file:")]
    writable_shared = [k for k, wa, wb in files_shared if wa or wb]
    devs_shared = [k for k in shared if k.startswith("chr:")]  # character devices, listed apart: judged against the cold control
    res["shared_pipe_or_socket_inodes"] = ipc_shared
    res["shared_regular_files_readonly"] = [k for k, wa, wb in files_shared if not (wa or wb)][:20]
    res["shared_regular_files_writable"] = writable_shared
    res["shared_character_devices"] = devs_shared
    res["shared_lock_entries"] = sorted(lock_keys(set(pa)) & lock_keys(set(pb)))
    res["xvfb_socket_inodes_differ"] = None
    sa = {os.stat(f"/proc/{a['init_pid']}/root/tmp/.X11-unix/X{a['display']}").st_ino}
    sb = {os.stat(f"/proc/{b['init_pid']}/root/tmp/.X11-unix/X{b['display']}").st_ino}
    res["xvfb_socket_inodes_differ"] = sa != sb
    res["addresses_differ"] = a["ip"] != b["ip"]
    # a write by one sibling is invisible to the other
    mark = f"/tmp/.criu_audit_{os.getpid()}"
    for who, pid in (("a", a["init_pid"]),):
        subprocess.run(["nsenter", "-t", str(pid), "-m", "--", "/bin/sh", "-c", f"echo A > {mark}; echo A > {HOME}/.criu_audit"], check=False)
    seen = subprocess.run(["nsenter", "-t", str(b["init_pid"]), "-m", "--", "/bin/sh", "-c", f"ls {mark} {HOME}/.criu_audit 2>/dev/null; true"],
                          capture_output=True, text=True).stdout.strip()
    res["write_in_a_visible_in_b"] = bool(seen)
    res["PASS_ignoring_devices"] = bool(res["pid_sets_disjoint"] and all(res["distinct_namespaces"].values()) and not ipc_shared and not writable_shared
                       and not res["shared_lock_entries"] and res["xvfb_socket_inodes_differ"] and res["addresses_differ"]
                       and not res["write_in_a_visible_in_b"])
    return res


# ------------------------------------------------------------------ harness
def make_sup(tmp, name, workload, display_base, criu):
    tpl = os.path.join(tmp, f"tpl-{name}")
    build_home_template(os.path.join(tpl, "home", "drums"))
    os.makedirs(os.path.join(tpl, "home", "drums", "Desktop"), exist_ok=True)
    return Supervisor(os.path.join(tmp, f"state-{name}"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=KIT,
                      init_python=os.path.join(KIT, "venv", "bin", "python"), server_mode="module", workload=workload, ready_timeout=120,
                      display_base=display_base, criu_friendly=True, criu=criu)


def stage(rec, name, fn):
    t = time.time()
    try:
        out = fn()
        rec["stages"][name] = {"ok": True, "seconds": time.time() - t, **(out or {})}
    except BaseException as e:  # a failed rung is a result
        rec["stages"][name] = {"ok": False, "seconds": time.time() - t, "error": f"{type(e).__name__}: {str(e)[-500:]}"}
        log(f"stage {name} FAILED: {str(e)[-300:]}")
        return False
    log(f"stage {name} ok")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--task", default="")
    ap.add_argument("--starts", type=int, default=20)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--max-n", type=int, default=20)
    ap.add_argument("--skip-memory", action="store_true")
    ap.add_argument("--skip-correctness", action="store_true", help="latency (and memory) only")
    ap.add_argument("--drop-caches", action="store_true",
                    help="NOT part of the pre-registered run: drop the page cache before every start, so a restore must read its images from disk")
    a = ap.parse_args(argv)
    criu = Criu()
    tmp = tempfile.mkdtemp(prefix="l1criu")
    rec = {"host": {"kernel": os.uname().release, "cpus": os.cpu_count()}, "criu": criu.version(), "args": vars(a), "stages": {}}
    supF = make_sup(tmp, "full", "chromium", 90, criu)  # the browser is in the checkpoint
    supS = make_sup(tmp, "srv", "", 140, criu)  # server and desktop only
    ck = {}
    blank = {}
    try:
        stage(rec, "R1_criu_check", lambda: dict(zip(("pass", "detail"), criu.check())))

        def mk(name, sup, slot):
            def go():
                info = sup.create(slot)
                time.sleep(2)
                if name == "srv":
                    blank["png"] = screenshot(info["ip"])
                ck[name] = sup.checkpoint(slot, os.path.join(tmp, f"ck-{name}"))
                return {"dump_seconds": ck[name].meta()["dump_seconds"], "image_mib": ck[name].meta()["image_bytes"] / 2**20}
            return go

        ok_s = stage(rec, "R3_server_only_dump", mk("srv", supS, 40))
        ok_f = stage(rec, "R4_chromium_dump", mk("full", supF, 0))
        if ok_s and not a.skip_correctness:
            supS.restore_from = ck["srv"]

            def r3():
                i = supS.create(41)
                shot = screenshot(i["ip"])
                return {"restore_total_s": i["restore_total_s"], "criu_restore_s": i["criu_restore_s"], "screenshot_bytes": len(shot)}
            stage(rec, "R3_server_only_restore", r3)
            supS.destroy(41)
        if ok_f and not a.skip_correctness:
            supF.restore_from = ck["full"]
            live = {}

            def r4():
                i = supF.create(1)
                live[1] = i
                titles = cdp_titles(i["init_pid"])
                return {"restore_total_s": i["restore_total_s"], "criu_restore_s": i["criu_restore_s"], "browser_titles": titles}
            stage(rec, "R4_chromium_restore", r4)

            def c2():
                i = live[1]
                w = agent_ready(i, blank["png"])
                shot = screenshot(i["ip"])
                frac = changed_fraction(shot, blank["png"])
                return {"ready_extra_s": w, "changed_fraction_vs_blank": frac, "titles": cdp_titles(i["init_pid"]), "PASS": frac > 0.10 and w is not None}
            if 1 in live:
                stage(rec, "C2_screenshot_shows_browser", c2)

            def c3():
                live[2] = supF.create(2)
                time.sleep(1)
                r = audit_pair(live[1], live[2])
                # control: the same audit on two COLD sessions, so sharing that restore did not introduce can be told apart
                supF.restore_from = None
                c_a, c_b = supF.create(3), supF.create(4)
                time.sleep(1)
                ctl = audit_pair(c_a, c_b)
                supF.destroy(3)
                supF.destroy(4)
                supF.restore_from = ck["full"]
                r["cold_pair_control"] = {k: ctl[k] for k in ("shared_pipe_or_socket_inodes", "shared_regular_files_writable", "shared_character_devices",
                                                              "shared_lock_entries", "PASS_ignoring_devices")}
                r["PASS_literal"] = bool(r["PASS_ignoring_devices"] and not r["shared_character_devices"])
                r["introduced_by_restore"] = {"character_devices": sorted(set(r["shared_character_devices"]) - set(ctl["shared_character_devices"])),
                                              "writable_files": sorted(set(r["shared_regular_files_writable"]) - set(ctl["shared_regular_files_writable"])),
                                              "locks": sorted(set(r["shared_lock_entries"]) - set(ctl["shared_lock_entries"]))}
                r["PASS_vs_cold_control"] = bool(r["PASS_ignoring_devices"] and not any(r["introduced_by_restore"].values()))
                return r
            if 1 in live:
                stage(rec, "C3_sibling_audit", c3)
            for s in (1, 2):
                if s in live:
                    supF.destroy(s)
            live.clear()

            if a.task:
                def c1():
                    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "compare"))
                    from drums_compare import osworld_matched as om
                    from ..scalecua.pool import DrumsSessionPool
                    de = import_desktop_env()
                    task = json.load(open(a.task))
                    pool = DrumsSessionPool(supF, 2, health_timeout=90, eager_reset=False)
                    ad = om.DesktopEnvAdapter("criu-restore", om.drums_factory(pool, screen_size=(1280, 800), desktop_env_module=de,
                                                                               cache_dir=os.path.join(tmp, "cache"), require_a11y_tree=False))
                    ad.open(1)
                    lease = ad.acquire(0)
                    lease.reset(task)
                    before = lease.evaluate()
                    lease.step("import os; os.rename(os.path.expanduser('~/Desktop/todo_list_Jan_1'), os.path.expanduser('~/Desktop/todo_list_Jan_2'))")
                    after = lease.evaluate()
                    restored = bool(supF._restored)
                    ad.close()
                    pool.shutdown()
                    return {"score_before": before, "score_after": after, "sessions_were_restored": restored, "PASS": before == 0.0 and after == 1.0 and restored}
                stage(rec, "C1_public_task_from_restored_session", c1)

        for sup in (supF, supS):  # nothing may be left from the correctness stages
            for sl in list(sup._procs):
                sup.destroy(sl)
        # ---- latency: cold vs restore (full) vs restore (server-only + browser cold start), interleaved
        if ok_f and ok_s:
            lat = {"COLD": [], "RESTORE_FULL": [], "RESTORE_SERVER_ONLY": []}
            faults = []
            # drop_caches is write-only and has no state to restore (it discards clean cache), so it is not a ledgered knob; the write is reported in the docs
            for rep in range(a.starts):
                for arm in list(lat)[rep % 3:] + list(lat)[:rep % 3]:
                    if a.drop_caches:
                        subprocess.run(["sync"])
                        with open("/proc/sys/vm/drop_caches", "w") as f:
                            f.write("3")
                    t0 = time.time()
                    try:
                        if arm == "COLD":
                            supF.restore_from = None
                            i = supF.create(0)
                            w = agent_ready(i, blank["png"])
                            sup, slot = supF, 0
                        elif arm == "RESTORE_FULL":
                            supF.restore_from = ck["full"]
                            i = supF.create(1)
                            w = agent_ready(i, blank["png"])
                            sup, slot = supF, 1
                        else:
                            supS.restore_from = ck["srv"]
                            i = supS.create(42)
                            start_browser_in(i)
                            w = agent_ready(i, blank["png"])
                            sup, slot = supS, 42
                        if w is None:
                            faults.append({"arm": arm, "rep": rep, "why": "not ready"})
                        else:
                            lat[arm].append(time.time() - t0)
                    except Exception as e:
                        faults.append({"arm": arm, "rep": rep, "why": str(e)[-200:]})
                        sup, slot = None, None
                    if sup is not None:
                        sup.destroy(slot)
                log(f"latency rep {rep}: " + " ".join(f"{k}={v[-1]:.2f}" for k, v in lat.items() if v))
            rec["latency"] = {k: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95), "mean": statistics.mean(v) if v else None, "all": v} for k, v in lat.items()}
            rec["latency_faults"] = faults
            if lat["COLD"] and lat["RESTORE_FULL"]:
                rec["latency"]["ratio_cold_over_restore_full_p50"] = pct(lat["COLD"], .5) / pct(lat["RESTORE_FULL"], .5)
            if lat["COLD"] and lat["RESTORE_SERVER_ONLY"]:
                rec["latency"]["ratio_cold_over_restore_server_only_p50"] = pct(lat["COLD"], .5) / pct(lat["RESTORE_SERVER_ONLY"], .5)

        # ---- memory: slope of host used memory per added session, cold (with browser) vs restored
        if ok_f and not a.skip_memory:
            CP = (1, 4, 8, 12, 16, 20)
            mem = []
            for trial in range(a.trials):
                for arm in (["COLD", "RESTORE"] if trial % 2 == 0 else ["RESTORE", "COLD"]):
                    time.sleep(5)
                    supF.restore_from = ck["full"] if arm == "RESTORE" else None
                    base, _ = used_kib()
                    pts = []
                    for n in range(1, a.max_n + 1):
                        supF.create(n - 1)
                        if n in CP:
                            time.sleep(5)
                            now, _ = used_kib()
                            pts.append({"n": n, "delta_kib": now - base})
                    slope = ols_slope([p["n"] for p in pts], [p["delta_kib"] / 1024 for p in pts])
                    mem.append({"arm": arm, "trial": trial, "points": pts, "slope_mib_per_session": slope})
                    log(f"memory trial={trial} {arm} slope={slope:.1f} MiB/session")
                    for n in range(a.max_n):
                        supF.destroy(n)
            rec["memory"] = mem
            sl = {arm: [m["slope_mib_per_session"] for m in mem if m["arm"] == arm] for arm in ("COLD", "RESTORE")}
            rec["memory_summary"] = {arm: statistics.mean(v) for arm, v in sl.items()}
            rec["memory_summary"]["restore_over_cold"] = statistics.mean(sl["RESTORE"]) / statistics.mean(sl["COLD"])
            lo, hi = bootstrap_diff(sl["COLD"], sl["RESTORE"])
            rec["memory_summary"]["cold_minus_restore_ci95"] = [lo, hi]
    finally:
        for sup in (supF, supS):
            for s in range(0, 60):
                try:
                    if s in sup._procs:
                        sup.destroy(s)
                except Exception:
                    pass
            sup.close()
        with open(a.out, "w") as f:
            json.dump(rec, f, indent=1, default=str)
        log("wrote", a.out)


if __name__ == "__main__":
    main()
