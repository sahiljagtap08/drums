"""Mechanism K side-channel EXISTENCE check (measurement M-K5, reported, no threshold): can a process in one session tell, by write latency,
that its page content is also held by a process in ANOTHER session?

Two probes run inside two real sessions' namespaces. Each maps two regions and marks them mergeable: `shared`, whose pages hold the same
deterministic content in both probes, and `unique`, whose pages hold random content. After a wait for merging, each probe times the first
write to every page. A merged page takes a copy-on-write fault on write and is slower than an unmerged one. A clear gap means a session
could detect content held by a sibling (the known deduplication timing channel); it is not an exploit, and it matters only if a task can
guess a sibling's page content.

    python -m envd_linux.l1proto.ksm_sidechannel --out ksm_sidechannel.json"""

import argparse
import json
import math
import os
import subprocess
import tempfile

from ..session import Supervisor
from ..template import build_home_template
from .ksm_bench import KNOBS, KSM

KIT = "/opt/drums-osworld-real"
PROBE = r'''
import mmap, os, time, json, sys, struct, statistics
PAGES, PS, WAIT = 8192, 4096, float(sys.argv[1])
def pat(i): return struct.pack("<Q", (i * 2654435761 + 12345) & (2**64 - 1)) * (PS // 8)
shared, unique = (mmap.mmap(-1, PAGES * PS, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS) for _ in range(2))
for i in range(PAGES):
    shared[i * PS:(i + 1) * PS] = pat(i)
    unique[i * PS:(i + 1) * PS] = os.urandom(PS)
shared.madvise(mmap.MADV_MERGEABLE); unique.madvise(mmap.MADV_MERGEABLE)
time.sleep(WAIT)
def merged():
    try:
        return int([l for l in open("/proc/self/ksm_stat") if l.startswith("ksm_merging_pages")][0].split()[1])
    except Exception:
        return None
before = merged()
def lat(region):
    out = []
    for i in range(PAGES):
        t = time.perf_counter_ns(); region[i * PS] = region[i * PS] ^ 1; out.append(time.perf_counter_ns() - t)
    return out
t0 = time.perf_counter(); ls = lat(shared); lu = lat(unique); probe_s = time.perf_counter() - t0
print(json.dumps({"ksm_merging_pages_before": before, "shared_ns": ls, "unique_ns": lu, "probe_s": probe_s}))
'''


def summarise(r, wait):
    """Per-page present/absent classifier. The threshold is fitted on the even-indexed pages and scored on the odd-indexed ones
    (held out). Bandwidth is the capacity of the resulting binary symmetric channel times pages per second, where one probe round
    costs the merge wait plus the timing pass."""
    sh, un = r["shared_ns"], r["unique_ns"]
    def pct(x, q):
        y = sorted(x)
        return y[int(len(y) * q)]
    fit = (sh[0::2], un[0::2])
    best, thr = -1, 0
    for t in sorted(set(fit[0] + fit[1]))[::8]:
        acc = (sum(v > t for v in fit[0]) + sum(v <= t for v in fit[1])) / (len(fit[0]) + len(fit[1]))
        if acc > best:
            best, thr = acc, t
    ts, tu = sh[1::2], un[1::2]
    tpr = sum(v > thr for v in ts) / len(ts)
    tnr = sum(v <= thr for v in tu) / len(tu)
    acc = (tpr + tnr) / 2
    e = 1 - acc
    h = 0 if e in (0, 1) else -(e * math.log2(e) + (1 - e) * math.log2(1 - e))
    cap = 1 - h
    pages = len(sh) + len(un)
    round_s = wait + r["probe_s"]
    return {"ksm_merging_pages_before": r["ksm_merging_pages_before"], "pages_per_region": len(sh),
            "shared": {"median_ns": pct(sh, .5), "p95_ns": pct(sh, .95)}, "unique": {"median_ns": pct(un, .5), "p95_ns": pct(un, .95)},
            "shared_over_unique_median": pct(sh, .5) / max(pct(un, .5), 1), "threshold_ns": thr,
            "heldout_tpr": tpr, "heldout_tnr": tnr, "heldout_balanced_accuracy": acc,
            "bsc_capacity_bits_per_page": cap, "round_s": round_s,
            "bandwidth_bits_per_s_one_bit_per_page": cap * pages / 2 / round_s,
            "bandwidth_note": "pages/2 candidate pages are tested per round (one region of each kind); rate = capacity x pages tested / round time"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", type=float, default=40)
    a = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="l1ksmsc")
    tpl = os.path.join(tmp, "tpl")
    build_home_template(os.path.join(tpl, "home", "drums"))
    py = os.path.join(KIT, "venv", "bin", "python")
    sup = Supervisor(os.path.join(tmp, "state"), tpl, root_mode="full", netns=True, osworld_port=5000, osworld_real_kit=KIT,
                     init_python=py, server_mode="module", workload="", ready_timeout=120)
    res = {"knobs_prior": {}}
    try:
        for k, v in KNOBS.items():
            if os.path.exists(f"{KSM}/{k}"):
                res["knobs_prior"][k] = sup.set_host_knob(f"{KSM}/{k}", v)
        infos = [sup.create(0), sup.create(1)]
        procs = [subprocess.Popen(["nsenter", "-t", str(i["init_pid"]), "-m", "-p", "-n", "-u", "-i", "--", py, "-c", PROBE, str(a.wait)],
                                  stdout=subprocess.PIPE, text=True) for i in infos]
        outs = [p.communicate(timeout=a.wait + 120)[0] for p in procs]
        raw = [json.loads(o.strip().splitlines()[-1]) for o in outs]
        res["probes"] = [summarise(r, a.wait) for r in raw]
    finally:
        for s in (0, 1):
            sup.destroy(s)
        sup.close()
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
