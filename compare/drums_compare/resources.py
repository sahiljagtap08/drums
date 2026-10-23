"""RSS and CPU per environment, sampled from the host.

Linux reader uses /proc (RSS and PSS summed over each env's process tree, so a KVM
guest counts as its qemu process). It reports per-env numbers; run-level host totals are
from /proc/meminfo and /proc/stat. Linux only: every side of the comparison is Linux.
"""

import json
import os
import subprocess
import threading
import time


def _children_map_linux():
    kids = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as f:
                data = f.read()
            ppid = int(data[data.rindex(")") + 2:].split()[1])
        except (OSError, ValueError):
            continue
        kids.setdefault(ppid, []).append(int(name))
    return kids


def tree(root, kids):
    out, stack = [], [root]
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(kids.get(p, []))
    return out


def cgroup_pids(cgroup_dir):
    """All pids in a cgroup v2 directory (for sessions that live in their own cgroup)."""
    try:
        with open(os.path.join(cgroup_dir, "cgroup.procs")) as f:
            return [int(x) for x in f.read().split()]
    except OSError:
        return []


class LinuxProcReader:
    clk_tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def env_usage(self, roots):
        """Return (rss_kib, pss_kib or None, cpu_ticks) summed over the trees of `roots`."""
        kids = _children_map_linux()
        rss = cpu = 0
        pss = 0
        pss_ok = True
        # A set: roots may overlap (for example every pid of a cgroup), and a process
        # must be counted once.
        pids = {pid for root in roots for pid in tree(root, kids)}
        for pid in sorted(pids):
            try:
                with open(f"/proc/{pid}/stat") as f:
                    d = f.read()
                fields = d[d.rindex(")") + 2:].split()
                cpu += int(fields[11]) + int(fields[12])  # utime + stime
                with open(f"/proc/{pid}/statm") as f:
                    rss += int(f.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
            except (OSError, ValueError, IndexError):
                continue
            try:
                with open(f"/proc/{pid}/smaps_rollup") as f:
                    for line in f:
                        if line.startswith("Pss:"):
                            pss += int(line.split()[1])
            except OSError:
                pss_ok = False
        return rss, (pss if pss_ok else None), cpu

    def host(self):
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                mem[k] = int(v.split()[0])
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        ticks = [int(x) for x in parts]
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
        return {"mem_total_kib": mem["MemTotal"], "mem_avail_kib": mem["MemAvailable"],
                "cpu_busy_ticks": sum(ticks) - idle, "cpu_total_ticks": sum(ticks)}


class Sampler:
    """Background thread. Appends one JSON line per env per interval to `path`."""

    def __init__(self, path, envs, reader, interval_s=5.0, clock=time.time):
        self.path, self.envs, self.reader = path, envs, reader
        self.interval_s, self.clock = interval_s, clock
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def sample_once(self, fh):
        t = self.clock()
        host = self.reader.host()
        if host:
            fh.write(json.dumps({"t": t, "kind": "host", **host}) + "\n")
        for env in self.envs:
            rss, pss, cpu = self.reader.env_usage(env.pids())
            fh.write(json.dumps({"t": t, "kind": "env", "slot": env.slot, "rss_kib": rss,
                                 "pss_kib": pss, "cpu_ticks": cpu,
                                 "clk_tck": self.reader.clk_tck}) + "\n")
        fh.flush()

    def _loop(self):
        with open(self.path, "a") as fh:
            while not self._stop.is_set():
                self.sample_once(fh)
                self._stop.wait(self.interval_s)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()
