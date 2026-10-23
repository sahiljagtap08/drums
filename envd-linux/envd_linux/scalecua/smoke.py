"""Bounded validation job for the Drums environment driver: the first thing to run on real
infrastructure, and runnable now in this sandbox with the guest-server stub.

    python -m envd_linux.scalecua.smoke --state DIR --template DIR --slots 4 --attempts 8 \
        --concurrency 4 --max-wall-s 600 --out smoke.json --events events.jsonl

It builds sessions through `DrumsEnvironmentController`, and per attempt checks the guest
contract end to end (platform, screen size, screenshot, command execution, file round trip),
recording how long each session took to become healthy. It never trains anything.

TEARDOWN CONTRACT. A bounded job's teardown has up to three layers:
  1. INNER: the wall cap, SIGTERM/SIGINT handling, `ctl.shutdown()` and `Supervisor.close()` below.
  2. OUTER SWEEP (`envd_linux/sweep.py`, built and tested): run at start and again at the end. It removes
     what a killed or crashed run leaves (session processes, mounts, host veth links, the `inet drums`
     table) and restores `net.ipv4.ip_forward` to the value recorded before the run changed it. It is
     idempotent, a no-op on a clean host, and never touches another live Supervisor. Both reports are written
     to the output JSON as `sweep_start` and `sweep_end`. It runs only if a process survives to call it;
     a kill -9 of THIS job is cleaned by the next sweep (the next job's start, or `python -m envd_linux.sweep`).
  3. On AWS only: an independent instance-termination layer (a timer or scheduler outside this process that
     terminates the instance whatever happens to the job). NOT implemented here and out of scope for this
     code; until it exists nothing about an AWS run is guaranteed-capped.
Tested locally only: the 4 s wall-cap test and the sweep tests (tests/test_sweep.py). Safety by layer 1:
  - a hard wall-clock cap: at the cap (or on SIGTERM/SIGINT) every session is destroyed, the
    partial results are written with status "wall_cap_reached" / "interrupted", exit code 3;
  - sessions are always torn down on exit (try/finally and atexit);
  - it creates local namespaces, mounts and veth pairs only. Terminating the machine it runs on
    is a separate, outer mechanism (an instance shutdown timer), not this job's responsibility.

Output (`--out`): status, per-attempt rows, summary (reset p50/p95, fault counts, host memory
low-water mark, mean session PSS), host facts. Numbers are observations of one run, not a
benchmark."""

import argparse
import atexit
import json
import os
import platform
import signal
import statistics
import struct
import sys
import threading
import time

from .client import GuestClient
from .controller import DrumsEnvironmentController, DrumsEnvironmentDelegation


def meminfo():
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            out[k] = int(v.split()[0])
    return out


def pss_kib(pids):
    total = 0
    for p in pids:
        try:
            with open(f"/proc/{p}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss:"):
                        total += int(line.split()[1])
        except OSError:
            pass
    return total


def host_facts():
    mi = meminfo()
    fwd = open("/proc/sys/net/ipv4/ip_forward").read().strip() if os.path.exists("/proc/sys/net/ipv4/ip_forward") else "?"
    return {"kernel": platform.release(), "cpus": os.cpu_count(), "mem_total_mib": mi["MemTotal"] // 1024,
            "dev_kvm": os.path.exists("/dev/kvm"), "ip_forward": fwd}


def percentile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def check_guest(c: GuestClient, expect_size=(1280, 800)):
    """Returns a list of failed check names (empty = all passed)."""
    bad = []
    try:
        if c.platform() != "Linux":
            bad.append("platform")
    except Exception:
        return ["platform_unreachable"]
    try:
        size = c.screen_size()
        if (size.get("width"), size.get("height")) != expect_size:
            bad.append("screen_size")
        shot = c.screenshot()
        if shot[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", shot[16:24]) != expect_size:
            bad.append("screenshot")
    except Exception:
        bad.append("display")
    try:
        r = c.execute("echo drums-smoke > /tmp/smoke.txt && cat /tmp/smoke.txt", shell=True)
        if r.get("output", "").strip() != "drums-smoke":
            bad.append("execute")
        if (c.file("/tmp/smoke.txt") or b"").strip() != b"drums-smoke":
            bad.append("file_roundtrip")
    except Exception:
        bad.append("execute")
    return bad


def run(args, backend, now=time.time):
    result = {"status": "running", "args": {k: v for k, v in vars(args).items() if k != "func"},
              "host": host_facts(), "attempts": [], "t_start": now()}
    ctl = DrumsEnvironmentController(DrumsEnvironmentDelegation(), backend, args.slots,
                                     health_timeout=args.health_timeout, acquire_timeout=args.acquire_timeout,
                                     events_path=args.events, eager_reset=False)
    ctl.run_loop_in_thread()
    stop = threading.Event()
    lock = threading.Lock()
    mem_low = [meminfo()["MemAvailable"]]
    pss_samples = []
    counter = {"next": 0}

    def write(status):
        result["status"] = status
        result["t_end"] = now()
        result["wall_s"] = result["t_end"] - result["t_start"]
        rows = result["attempts"]
        resets = [r["start_s"] for r in rows if r.get("start_s") is not None]
        faults = {}
        for r in rows:
            for k in r.get("failed", []):
                faults[k] = faults.get(k, 0) + 1
            if r.get("error"):
                faults["start_error"] = faults.get("start_error", 0) + 1
        result["summary"] = {
            "attempts": len(rows), "ok": sum(1 for r in rows if r["ok"]), "faults": faults,
            "start_s_p50": percentile(resets, 0.5), "start_s_p95": percentile(resets, 0.95),
            "start_s_mean": statistics.mean(resets) if resets else None,
            "mem_available_low_mib": mem_low[0] // 1024,
            "session_pss_mib_mean": (statistics.mean(pss_samples) / 1024) if pss_samples else None,
        }
        tmp = args.out + ".tmp"
        with open(tmp, "w") as f:
            json.dump(result, f, indent=1)
        os.replace(tmp, args.out)

    def worker():
        while not stop.is_set():
            with lock:
                k = counter["next"]
                if k >= args.attempts:
                    return
                counter["next"] += 1
            row = {"attempt": k, "ok": False}
            t0 = now()
            sid = None
            try:
                sid, envs, urls = ctl.sync_start_session("ubuntu")
                row["start_s"] = now() - t0
                info = ctl.session_info(sid)["ubuntu"]
                row["failed"] = check_guest(GuestClient(urls["ubuntu"]))
                row["ok"] = not row["failed"]
                from ..x11 import pidns_members
                with lock:
                    pss_samples.append(pss_kib(pidns_members(info["init_pid"])))
                    mem_low[0] = min(mem_low[0], meminfo()["MemAvailable"])
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            finally:
                if sid:
                    try:
                        ctl.sync_end_session(sid)
                    except Exception as e:  # pragma: no cover
                        row["end_error"] = str(e)[:200]
            row["wall_s"] = now() - t0
            with lock:
                result["attempts"].append(row)

    def teardown():
        stop.set()
        try:
            ctl.shutdown()
        except Exception:
            pass

    atexit.register(teardown)
    status = {"v": None}

    def on_signal(signum, frame):
        status["v"] = "interrupted"
        stop.set()

    old = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.concurrency)]
    [t.start() for t in threads]
    deadline = now() + args.max_wall_s
    try:
        while any(t.is_alive() for t in threads):
            if now() > deadline:
                status["v"] = status["v"] or "wall_cap_reached"
                stop.set()
                break
            if stop.is_set() and not status["v"]:
                break
            time.sleep(0.2)
        if status["v"]:
            teardown()  # destroy sessions now, in-flight attempts will error out
            for t in threads:
                t.join(timeout=20)
        else:
            for t in threads:
                t.join()
        final = status["v"] or "ok"
        teardown()
        write(final if final != "ok" or all(r["ok"] for r in result["attempts"]) else "failed_checks")
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--template", default="")
    ap.add_argument("--root-mode", choices=["home", "full"], default="full")
    ap.add_argument("--no-netns", action="store_true")
    ap.add_argument("--slots", type=int, default=2)
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--max-wall-s", type=float, default=600)
    ap.add_argument("--health-timeout", type=float, default=60)
    ap.add_argument("--acquire-timeout", type=float, default=120)
    ap.add_argument("--out", required=True)
    ap.add_argument("--events", default=None)
    a = ap.parse_args(argv)
    from .. import sweep as sw
    from ..session import Supervisor
    start_report = sw.sweep()  # outer sweep before: removes what an earlier killed run left
    sup = Supervisor(a.state, a.template, root_mode=a.root_mode, netns=not a.no_netns, osworld_port=5000,
                     sweep_on_start=False)
    res = None
    try:
        res = run(a, sup)
    finally:
        sup.close()  # inner teardown
        end_report = sw.sweep()  # outer sweep after: catches anything the inner teardown missed
        try:
            with open(a.out) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        doc["sweep_start"], doc["sweep_end"] = start_report, end_report
        with open(a.out, "w") as f:
            json.dump(doc, f, indent=1)
    return {"ok": 0, "wall_cap_reached": 3, "interrupted": 3}.get(res["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
