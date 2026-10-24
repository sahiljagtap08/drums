"""PROTOTYPE B measurements M1 (memory) and M2 (latency); thresholds fixed in docs/l1-shared-runtime-design.md section 10.

Arms: A0 each session execs its own Chromium on its own Xvfb (as C1 in section 9); B one shared Chromium on a host Xvfb, each
session attached as a profile. Sessions are identical otherwise (real OSWorld guest server, module entry, overlay, netns).

    python -m envd_linux.l1proto.branch_bench --out branch_bench.json [--latency-n 20] [--trials 4] [--max-n 20]"""

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time

from ..session import Supervisor
from ..template import build_home_template
from .bench import CHECKPOINTS, bootstrap_diff, log, ols_slope, pct, read_marks, used_kib
from .branch import SharedBrowser

KIT = "/opt/drums-osworld-real"
ARMS = ("A0", "B")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--latency-n", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--max-n", type=int, default=20)
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="l1branch")
    tpl = os.path.join(tmp, "tpl")
    build_home_template(os.path.join(tpl, "home", "drums"))
    py = os.path.join(KIT, "venv", "bin", "python")
    sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=KIT,
                     init_python=py, server_mode="module", workload="chromium", ready_timeout=120)
    sup.t_request = {}
    profile_tpl = os.path.join(tpl, "home", "drums", "profile")
    result = {"host": {"kernel": os.uname().release, "cpus": os.cpu_count()}, "args": vars(a), "latency": [], "memory": [],
              "template_profile_mib": sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(profile_tpl)
                                          for f in fs if os.path.isfile(os.path.join(r, f))) / 2**20}
    sb = None
    try:
        # ---------------- M2 latency, interleaved ----------------
        sb = SharedBrowser(profile_tpl, os.path.join(tmp, "lat"))
        sb.start()
        time.sleep(2)
        for i in range(-a.warmup, a.latency_n):
            for arm in (ARMS if i % 2 == 0 else ARMS[::-1]):
                if arm == "A0":
                    sup.workload = "chromium"
                    sup.create(0)
                    marks = read_marks(sup, 0)
                    row = {"arm": "A0", "iter": i, "ready_s": marks["chromium_ready"] - marks["chromium_spawned"]}
                    sup.destroy(0)
                else:
                    r = sb.attach(f"L{i + a.warmup}")
                    row = {"arm": "B", "iter": i, "ready_s": r["ready_s"], "copy_s": r["copy_s"], "handoff_s": r["handoff_s"],
                           "ready_without_copy_s": r["ready_s"] - r["copy_s"]}
                    sb.close_target(r["target"])
                if i >= 0:
                    result["latency"].append(row)
                log(f"lat i={i} {arm} ready={row['ready_s']:.3f}")
        sb.stop()
        sb = None
        # ---------------- M1 memory ----------------
        for trial in range(a.trials):
            for arm in (ARMS if trial % 2 == 0 else ARMS[::-1]):
                time.sleep(3)
                fixed = 0
                if arm == "B":
                    pre, _ = used_kib()
                    sb = SharedBrowser(profile_tpl, os.path.join(tmp, f"mem{trial}"))
                    sb.start()
                    time.sleep(3)
                    base, _ = used_kib()
                    fixed = base - pre
                else:
                    base, _ = used_kib()
                pts = []
                for n in range(1, a.max_n + 1):
                    sup.workload = "chromium" if arm == "A0" else ""
                    sup.create(n - 1)
                    if arm == "B":
                        sb.attach(f"M{trial}_{n}")
                    if n in CHECKPOINTS:
                        time.sleep(3)
                        now, m = used_kib()
                        pts.append({"n": n, "delta_kib": now - base, "shmem_kib": m.get("Shmem")})
                xs = [p["n"] for p in pts]
                ys = [p["delta_kib"] / 1024 for p in pts]
                slope = ols_slope(xs, ys)
                result["memory"].append({"arm": arm, "trial": trial, "points": pts, "slope_mib_per_session": slope,
                                         "fixed_cost_mib": fixed / 1024,
                                         "total_at_max_n_mib": ys[-1] + fixed / 1024,
                                         "process_types": ({k: len(v) for k, v in sb.process_types().items()} if sb else None)})
                log(f"mem trial={trial} {arm} slope={slope:.1f} MiB/session fixed={fixed / 1024:.0f} MiB total@{xs[-1]}={ys[-1] + fixed / 1024:.0f} MiB")
                for n in range(a.max_n):
                    sup.destroy(n)
                if sb:
                    sb.stop()
                    shutil.rmtree(sb.root, ignore_errors=True)
                    sb = None
    finally:
        if sb:
            sb.stop()
        sup.close()
    lat = {}
    for arm in ARMS:
        rows = [r for r in result["latency"] if r["arm"] == arm]
        for k in ("ready_s", "ready_without_copy_s", "copy_s", "handoff_s"):
            xs = [r[k] for r in rows if k in r]
            if xs:
                lat.setdefault(arm, {})[k] = {"n": len(xs), "p50": pct(xs, .5), "p95": pct(xs, .95), "mean": statistics.mean(xs)}
    result["latency_summary"] = lat
    slopes = {arm: [m["slope_mib_per_session"] for m in result["memory"] if m["arm"] == arm] for arm in ARMS}
    totals = {arm: [m["total_at_max_n_mib"] for m in result["memory"] if m["arm"] == arm] for arm in ARMS}
    result["memory_summary"] = {arm: {"slopes": slopes[arm], "mean": statistics.mean(slopes[arm]),
                                      "total_at_max_n_mean_mib": statistics.mean(totals[arm])} for arm in ARMS}
    result["memory_summary"]["fixed_cost_B_mib"] = statistics.mean(m["fixed_cost_mib"] for m in result["memory"] if m["arm"] == "B")
    lo, hi = bootstrap_diff(slopes["A0"], slopes["B"])
    result["memory_summary"]["A0_minus_B"] = {"mean": statistics.mean(slopes["A0"]) - statistics.mean(slopes["B"]), "ci95": [lo, hi]}
    with open(a.out, "w") as f:
        json.dump(result, f, indent=1)
    log("wrote", a.out)


if __name__ == "__main__":
    main()
