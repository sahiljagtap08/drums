"""PROTOTYPE of mechanism K (page merging across sessions): memory, CPU and start-latency measurements. Pre-registration:
docs/l1-shared-runtime-design.md section 12 (written before this ran).

Arms (identical sessions: real OSWorld guest server, module entry, per-session Chromium on the session's own Xvfb):
  OFF  no merge flag, address-space randomisation as normal (this is the existing A0 arm of section 10)
  K1   every session process is marked mergeable (prctl PR_SET_MEMORY_MERGE), randomisation as normal
  K2   as K1 and address-space randomisation OFF inside sessions (personality ADDR_NO_RANDOMIZE), so that identical content can sit at
       identical addresses and pointers inside pages match across instances

Host KSM knobs are recorded in the ledger BEFORE they are written and restored by Supervisor.close() (and by the sweep after a crash).

    python -m envd_linux.l1proto.ksm_bench --out ksm_bench.json [--trials 3] [--max-n 20]"""

import argparse
import glob
import json
import os
import statistics
import tempfile
import time

from ..session import Supervisor
from ..template import build_home_template
from .bench import CHECKPOINTS, bootstrap_diff, log, ols_slope, pct, used_kib

KIT = "/opt/drums-osworld-real"
KSM = "/sys/kernel/mm/ksm"
ARMS = {"OFF": dict(ksm=False, no_aslr=False), "K1": dict(ksm=True, no_aslr=False), "K2": dict(ksm=True, no_aslr=True)}
KNOBS = {"run": "1", "pages_to_scan": "20000", "sleep_millisecs": "2", "use_zero_pages": "1"}


def ksm_stats():
    out = {}
    for n in ("pages_shared", "pages_sharing", "pages_unshared", "pages_volatile", "full_scans", "general_profit"):
        try:
            out[n] = int(open(f"{KSM}/{n}").read())
        except (OSError, ValueError):
            out[n] = None
    return out


def ksmd_cpu_s():
    for p in glob.glob("/proc/[0-9]*/comm"):
        try:
            if open(p).read().strip() == "ksmd":
                f = open(p.replace("comm", "stat")).read().rsplit(")", 1)[1].split()
                return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")
        except OSError:
            continue
    return None


def settle(max_s=90):
    """Wait until the merge counters stop moving (less than 1% change over 5 s), up to max_s. Returns seconds waited."""
    t0 = time.time()
    last = ksm_stats()["pages_sharing"] or 0
    while time.time() - t0 < max_s:
        time.sleep(5)
        now = ksm_stats()["pages_sharing"] or 0
        if abs(now - last) <= 0.01 * max(last, 1):
            return time.time() - t0
        last = now
    return time.time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--max-n", type=int, default=20)
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="l1ksm")
    tpl = os.path.join(tmp, "tpl")
    build_home_template(os.path.join(tpl, "home", "drums"))
    py = os.path.join(KIT, "venv", "bin", "python")
    sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=KIT,
                     init_python=py, server_mode="module", workload="chromium", ready_timeout=120)
    sup.t_request = {}
    result = {"host": {"kernel": os.uname().release, "cpus": os.cpu_count()}, "args": vars(a), "knobs_set": KNOBS,
              "knobs_prior": {}, "memory": [], "create_s": {k: [] for k in ARMS}}
    try:
        for k, v in KNOBS.items():
            if os.path.exists(f"{KSM}/{k}"):
                result["knobs_prior"][k] = sup.set_host_knob(f"{KSM}/{k}", v)
        names = list(ARMS)
        for trial in range(a.trials):
            for arm in names[trial % 3:] + names[:trial % 3]:
                time.sleep(5)
                sup.ksm, sup.no_aslr = ARMS[arm]["ksm"], ARMS[arm]["no_aslr"]
                base, _ = used_kib()
                cpu0, t0 = ksmd_cpu_s(), time.time()
                pts = []
                for n in range(1, a.max_n + 1):
                    c0 = time.time()
                    sup.create(n - 1)
                    result["create_s"][arm].append(time.time() - c0)
                    if n in CHECKPOINTS:
                        waited = settle()
                        now, m = used_kib()
                        pts.append({"n": n, "delta_kib": now - base, "settle_s": waited, "ksm": ksm_stats()})
                cpu1, t1 = ksmd_cpu_s(), time.time()
                xs = [p["n"] for p in pts]
                ys = [p["delta_kib"] / 1024 for p in pts]
                slope = ols_slope(xs, ys)
                result["memory"].append({"arm": arm, "trial": trial, "points": pts, "slope_mib_per_session": slope,
                                         "total_at_max_n_mib": ys[-1],
                                         "ksmd_cpu_s": (cpu1 - cpu0) if cpu0 is not None and cpu1 is not None else None,
                                         "trial_wall_s": t1 - t0})
                log(f"ksm trial={trial} {arm} slope={slope:.1f} total@{xs[-1]}={ys[-1]:.0f} MiB sharing={pts[-1]['ksm']['pages_sharing']} "
                    f"ksmd_cpu={(cpu1 - cpu0) if cpu0 is not None else None}")
                for n in range(a.max_n):
                    sup.destroy(n)
    finally:
        sup.close()  # restores the host KSM knobs recorded above
    slopes = {arm: [m["slope_mib_per_session"] for m in result["memory"] if m["arm"] == arm] for arm in ARMS}
    result["memory_summary"] = {arm: {"slopes": slopes[arm], "mean": statistics.mean(slopes[arm]),
                                      "total_at_max_n_mean_mib": statistics.mean(m["total_at_max_n_mib"] for m in result["memory"] if m["arm"] == arm)}
                                for arm in ARMS}
    for arm in ("K1", "K2"):
        lo, hi = bootstrap_diff(slopes["OFF"], slopes[arm])
        result["memory_summary"][f"OFF_minus_{arm}"] = {"mean": statistics.mean(slopes["OFF"]) - statistics.mean(slopes[arm]), "ci95": [lo, hi]}
        cpu = [m["ksmd_cpu_s"] / m["trial_wall_s"] for m in result["memory"] if m["arm"] == arm and m["ksmd_cpu_s"] is not None]
        result["memory_summary"][f"{arm}_ksmd_vcpu_fraction_mean"] = statistics.mean(cpu) if cpu else None
    result["create_summary"] = {arm: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95)} for arm, v in result["create_s"].items()}
    with open(a.out, "w") as f:
        json.dump(result, f, indent=1)
    log("wrote", a.out)


if __name__ == "__main__":
    main()
