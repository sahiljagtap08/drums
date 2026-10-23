"""PROTOTYPE measurements for L1 mechanism F (docs/l1-shared-runtime-design.md section 8: thresholds fixed BEFORE this ran).

Arms (identical except the guest server's start): C0 `python main.py` as shipped, C1 fresh interpreter importing `main`,
F fork from the zygote. Chromium is exec'd in every arm. One Supervisor, sessions in slots, interleaved arm order.

    python -m envd_linux.l1proto.bench --out results.json [--latency-n 20] [--trials 4] [--max-n 20]

Prints progress to stderr, writes raw rows and summaries to --out. Needs root, the kit, Chromium, nft."""

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time

from ..session import Supervisor
from ..template import build_home_template
from ..x11 import pidns_members
from .zygote import ZygoteClient

KIT = "/opt/drums-osworld-real"
ARMS = {"C0": "asshipped", "C1": "module", "F": "fork"}
CHECKPOINTS = (1, 4, 8, 12, 16, 20)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def used_kib():
    m = {}
    with open("/proc/meminfo") as f:
        for ln in f:
            k, v = ln.split(":")
            m[k] = int(v.split()[0])
    return m["MemTotal"] - m["MemAvailable"], m


def smaps_rollup(pid):
    out = {}
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for ln in f:
                parts = ln.split()
                if len(parts) >= 3 and parts[2] == "kB":
                    out[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return out


def classify(pid):
    try:
        argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
        argv = [a.decode(errors="replace") for a in argv]
    except OSError:
        return None
    joined = " ".join(argv)
    if "chrome" in argv[0] or "chrome-linux" in joined:
        return "chromium"
    if argv[0].endswith("Xvfb"):
        return "xvfb"
    if "import main" in joined or joined.endswith("main.py") or " main.py" in joined:
        return "guest_server"
    if "session_init" in joined or "l1proto.zygote" in joined:
        return "session_init_or_forked"  # forked sessions share the zygote's command line
    if any(x in joined for x in ("dbus-daemon", "at-spi", "openbox")):
        return "desktop_stack"
    return "other"


def session_breakdown(init_pid):
    rows = {}
    for pid in pidns_members(init_pid):
        c = classify(pid)
        if c is None:
            continue
        r = smaps_rollup(pid)
        d = rows.setdefault(c, {"n": 0, "pss_kib": 0, "private_kib": 0, "rss_kib": 0})
        d["n"] += 1
        d["pss_kib"] += r.get("Pss", 0)
        d["private_kib"] += r.get("Private_Clean", 0) + r.get("Private_Dirty", 0)
        d["rss_kib"] += r.get("Rss", 0)
    return rows


def read_marks(sup, slot):
    with open(os.path.join(sup._dir(slot), "timing.jsonl")) as f:
        return {m["mark"]: m["t"] for m in map(json.loads, f)}


def create(sup, arm, slot, zyg):
    sup.server_mode = ARMS[arm]
    sup.zygote = zyg if arm == "F" else None
    t0 = time.time()
    info = sup.create(slot)
    t1 = time.time()
    marks = read_marks(sup, slot)
    return info, {"arm": arm, "create_s": t1 - t0, "server_ready_s": marks["server_ready"] - sup.t_request[slot],
                  "chromium_ready_s": marks["chromium_ready"] - sup.t_request[slot],
                  "init_to_server_ready_s": marks["server_ready"] - marks["init_start"]}


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def ols_slope(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)


def bootstrap_diff(a, b, n=10000, seed=1):
    """95% interval of mean(a) - mean(b), resampling trials with replacement."""
    rnd = random.Random(seed)
    diffs = []
    for _ in range(n):
        ra = [rnd.choice(a) for _ in a]
        rb = [rnd.choice(b) for _ in b]
        diffs.append(statistics.mean(ra) - statistics.mean(rb))
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--latency-n", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--trials", type=int, default=4)
    ap.add_argument("--max-n", type=int, default=20)
    ap.add_argument("--skip-latency", action="store_true")
    ap.add_argument("--skip-memory", action="store_true")
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="l1bench")
    build_home_template(os.path.join(tmp, "tpl", "home", "drums"))
    py = os.path.join(KIT, "venv", "bin", "python")
    sup = Supervisor(os.path.join(tmp, "state"), os.path.join(tmp, "tpl"), root_mode="full", netns=True,
                     osworld_port=5000, osworld_real_kit=KIT, init_python=py, server_mode="module",
                     workload="chromium", ready_timeout=120)
    sup.t_request = {}
    mem0, _ = used_kib()
    zyg = ZygoteClient(KIT, os.path.join(tmp, "z.sock"), sup.tag)
    mem1, _ = used_kib()
    result = {"host": {"kernel": os.uname().release, "cpus": os.cpu_count()}, "args": vars(a),
              "zygote": {"preload_s": zyg.hello["preload_s"], "preloaded": zyg.hello["preloaded"],
                         "failed": zyg.hello["failed"], "host_used_delta_kib": mem1 - mem0,
                         "rss_kib": smaps_rollup(zyg.proc.pid).get("Rss"), "pss_kib": smaps_rollup(zyg.proc.pid).get("Pss")},
              "latency": [], "memory": [], "breakdown": {}}
    try:
        if not a.skip_latency:
            order = list(ARMS)
            for i in range(-a.warmup, a.latency_n):
                rot = order[i % 3:] + order[:i % 3]
                for arm in rot:
                    info, row = create(sup, arm, 0, zyg)
                    sup.destroy(0)
                    row["iter"] = i
                    if i >= 0:
                        result["latency"].append(row)
                    log(f"lat i={i} {arm} server={row['server_ready_s']:.2f} chromium={row['chromium_ready_s']:.2f}")
        if not a.skip_memory:
            for trial in range(a.trials):
                for arm in (list(ARMS)[trial % 3:] + list(ARMS)[:trial % 3]):
                    time.sleep(3)
                    base, _ = used_kib()
                    pts = []
                    for n in range(1, a.max_n + 1):
                        info, row = create(sup, arm, n - 1, zyg)
                        if n in CHECKPOINTS:
                            time.sleep(3)
                            now, m = used_kib()
                            pts.append({"n": n, "delta_kib": now - base, "shmem_kib": m.get("Shmem")})
                            if trial == 0 and n == a.max_n:
                                result["breakdown"][arm] = session_breakdown(info["init_pid"])
                    xs = [p["n"] for p in pts]
                    ys = [p["delta_kib"] / 1024 for p in pts]
                    slope = ols_slope(xs, ys)
                    result["memory"].append({"arm": arm, "trial": trial, "points": pts, "slope_mib_per_session": slope})
                    log(f"mem trial={trial} {arm} slope={slope:.1f} MiB/session  N={xs[-1]} delta={ys[-1]:.0f} MiB")
                    for n in range(a.max_n):
                        sup.destroy(n)
    finally:
        zyg.close()
        sup.close()
    # summaries
    lat = {}
    for arm in ARMS:
        rows = [r for r in result["latency"] if r["arm"] == arm]
        for k in ("create_s", "server_ready_s", "chromium_ready_s", "init_to_server_ready_s"):
            xs = [r[k] for r in rows]
            lat.setdefault(arm, {})[k] = {"n": len(xs), "p50": pct(xs, .5), "p95": pct(xs, .95), "mean": statistics.mean(xs) if xs else None}
    result["latency_summary"] = lat
    slopes = {arm: [m["slope_mib_per_session"] for m in result["memory"] if m["arm"] == arm] for arm in ARMS}
    result["memory_summary"] = {arm: {"slopes": v, "mean": statistics.mean(v) if v else None} for arm, v in slopes.items()}
    if slopes["C1"] and slopes["F"]:
        lo, hi = bootstrap_diff(slopes["C1"], slopes["F"])
        result["memory_summary"]["C1_minus_F"] = {"mean": statistics.mean(slopes["C1"]) - statistics.mean(slopes["F"]),
                                                  "ci95": [lo, hi]}
    with open(a.out, "w") as f:
        json.dump(result, f, indent=1)
    log("wrote", a.out)


if __name__ == "__main__":
    main()
