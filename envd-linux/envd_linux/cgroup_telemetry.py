"""Per-session resource capture from the ACTUAL session cgroups (cgroup v2), for the first-check driver. PROTOTYPE.

Works on cgroup v2 (one directory per session) and v1 (one directory per controller: cpu, memory, pids). On v1 the `cpuacct` controller is a separate mount that sessions are
not placed in, so CPU is summed from /proc/<pid>/stat (utime+stime, per pid the highest value seen) for the pids IN the session's cgroup; on v2 `cpu.stat` is used.

Sampled at a fixed period, so it is a LOWER BOUND on CPU (a session that ends between two samples loses its last slice) and the memory peak is the highest sample seen
(`memory.peak` is read too when the kernel has it). Records, per session cgroup directory: the limits as the kernel reports them, CPU seconds consumed (accumulated across a
directory being removed and recreated), peak memory, and how many processes were in the cgroup at each sample. A cgroup that holds NO process while its session is alive
means the session escaped its limits: that is recorded as `empty_samples`, not hidden.

It does not verify that a VM has its settings (no VM exists here), and it measures Drums only: no resource-parity statement follows from it."""

import os
import threading
import time

from . import limits as lim


def _read(path, default=None):
    try:
        return open(path).read().strip()
    except OSError:
        return default


def _cpu_us(d):
    for ln in (_read(os.path.join(d, "cpu.stat"), "") or "").splitlines():
        if ln.startswith("usage_usec"):
            return int(ln.split()[1])
    return None


def _ticks(pid):
    try:
        f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        return int(f[11]) + int(f[12])  # utime + stime
    except (OSError, IndexError, ValueError):
        return None


def _key(d):
    """The session key shared by a session's per-controller directories: the path under `drums/`."""
    return d.split("/drums/", 1)[-1]


class CgroupProbe:
    """`dirs()` returns the cgroup directories to watch now (for a Supervisor: the live `_cgroups` dirs). begin()/end() bracket one measured block."""

    def __init__(self, dirs, period=0.2):
        self.dirs, self.period = dirs, period
        self._stop = threading.Event()
        self._t = None
        self.state = {}
        self.hz = os.sysconf("SC_CLK_TCK")

    def _sample(self):
        for d in list(self.dirs()):
            k = _key(d)
            s = self.state.setdefault(k, {"pid_ticks": {}, "cpu_us_done": 0, "cpu_us_last": 0, "mem_sampled": 0, "mem_kernel": 0, "samples": 0, "empty_samples": 0,
                                          "procs_max": 0, "limits": {}, "kind": None})
            procs = _read(os.path.join(d, "cgroup.procs"))
            if procs is None:
                continue
            v2 = os.path.exists(os.path.join(d, "memory.current"))
            v1_cpu, v1_mem = os.path.exists(os.path.join(d, "cpu.cfs_quota_us")), os.path.exists(os.path.join(d, "memory.usage_in_bytes"))
            s["kind"] = "v2" if v2 else "v1"
            pids = [int(p) for p in procs.split() if p]
            if v2 or v1_cpu:  # the cpu directory (v2: the only one) defines the session's process set
                s["samples"] += 1
                s["procs_max"] = max(s["procs_max"], len(pids))
                s["empty_samples"] += 0 if pids else 1
            if v2:
                cpu = _cpu_us(d)
                if cpu is not None:
                    if cpu < s["cpu_us_last"]:  # the directory was recreated: bank the previous total
                        s["cpu_us_done"] += s["cpu_us_last"]
                    s["cpu_us_last"] = cpu
            elif v1_cpu:
                for pid in pids:
                    t = _ticks(pid)
                    if t is not None:
                        s["pid_ticks"][pid] = max(s["pid_ticks"].get(pid, 0), t)
            mem = _read(os.path.join(d, "memory.current" if v2 else "memory.usage_in_bytes"))
            if mem and mem.isdigit():
                s["mem_sampled"] = max(s["mem_sampled"], int(mem))
            pk = _read(os.path.join(d, "memory.peak" if v2 else "memory.max_usage_in_bytes"))
            if pk and pk.isdigit():
                s["mem_kernel"] = max(s["mem_kernel"], int(pk))
            lim_files = ("cpu.max", "memory.max", "pids.max", "memory.swap.max") if v2 else (
                ("cpu.cfs_quota_us", "cpu.cfs_period_us") if v1_cpu else ("memory.limit_in_bytes", "memory.memsw.limit_in_bytes") if v1_mem else ("pids.max",))
            for f in lim_files:
                if f not in s["limits"]:
                    s["limits"][f] = _read(os.path.join(d, f))

    def _run(self):
        while not self._stop.wait(self.period):
            self._sample()

    def begin(self):
        self.state = {}
        self._stop.clear()
        self._sample()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def end(self):
        self._stop.set()
        self._t.join(5)
        self._sample()
        out = {}
        for k, s in self.state.items():
            cpu = (s["cpu_us_done"] + s["cpu_us_last"]) / 1e6 if s["kind"] == "v2" else sum(s["pid_ticks"].values()) / self.hz
            out[k] = {"cgroup_kind": s["kind"], "cpu_s_lower_bound": cpu, "cpu_source": "cpu.stat" if s["kind"] == "v2" else "sum of /proc/<pid>/stat utime+stime of pids seen in the cgroup",
                      "mem_peak_mib_sampled": s["mem_sampled"] / (1 << 20), "mem_peak_mib_kernel": s["mem_kernel"] / (1 << 20) if s["mem_kernel"] else "MISSING",
                      "samples": s["samples"], "empty_samples": s["empty_samples"], "procs_max": s["procs_max"], "limits_as_read_by_kernel": s["limits"]}
        return {"period_s": self.period, "sessions": out, "lower_bound_note": "sampled; CPU and peaks can be missed between samples (a pid that exits between samples loses its last slice)",
                "backend": lim.backend()}
