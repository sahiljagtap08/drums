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
shared, unique = mmap.mmap(-1, PAGES * PS), mmap.mmap(-1, PAGES * PS)
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
    out.sort(); return {"median_ns": out[len(out) // 2], "p95_ns": out[int(len(out) * .95)], "mean_ns": int(statistics.mean(out))}
print(json.dumps({"ksm_merging_pages_before": before, "shared": lat(shared), "unique": lat(unique)}))
'''


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
        res["probes"] = [json.loads(o.strip().splitlines()[-1]) for o in outs]
        for pr in res["probes"]:
            pr["shared_over_unique_median"] = pr["shared"]["median_ns"] / max(pr["unique"]["median_ns"], 1)
    finally:
        for s in (0, 1):
            sup.destroy(s)
        sup.close()
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
