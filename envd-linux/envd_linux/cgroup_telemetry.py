"""Per-session resource capture from the ACTUAL session cgroups (cgroup v2), for the first-check driver. PROTOTYPE.

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


class CgroupProbe:
    """`dirs()` returns the cgroup directories to watch now (for a Supervisor: the live `_cgroups` dirs). begin()/end() bracket one measured block."""

    def __init__(self, dirs, period=0.2):
        self.dirs, self.period = dirs, period
        self._stop = threading.Event()
        self._t = None
        self.state = {}

    def _sample(self):
        for d in list(self.dirs()):
            s = self.state.setdefault(d, {"cpu_us_done": 0, "cpu_us_last": 0, "mem_peak_sampled": 0, "mem_peak_kernel": 0, "samples": 0, "empty_samples": 0, "procs_max": 0,
                                          "limits": None})
            procs = _read(os.path.join(d, "cgroup.procs"))
            if procs is None:
                continue
            n = len([p for p in procs.split() if p])
            s["samples"] += 1
            s["procs_max"] = max(s["procs_max"], n)
            if n == 0:
                s["empty_samples"] += 1
            cpu = _cpu_us(d)
            if cpu is not None:
                if cpu < s["cpu_us_last"]:  # the directory was recreated: bank the previous total
                    s["cpu_us_done"] += s["cpu_us_last"]
                s["cpu_us_last"] = cpu
            mem = _read(os.path.join(d, "memory.current"))
            if mem and mem.isdigit():
                s["mem_peak_sampled"] = max(s["mem_peak_sampled"], int(mem))
            pk = _read(os.path.join(d, "memory.peak"))
            if pk and pk.isdigit():
                s["mem_peak_kernel"] = max(s["mem_peak_kernel"], int(pk))
            if s["limits"] is None:
                s["limits"] = {"cpu.max": _read(os.path.join(d, "cpu.max")), "memory.max": _read(os.path.join(d, "memory.max")),
                               "pids.max": _read(os.path.join(d, "pids.max")), "memory.swap.max": _read(os.path.join(d, "memory.swap.max"))}

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
        for d, s in self.state.items():
            out[d] = {"cpu_s_lower_bound": (s["cpu_us_done"] + s["cpu_us_last"]) / 1e6, "mem_peak_mib_sampled": s["mem_peak_sampled"] / (1 << 20),
                      "mem_peak_mib_kernel": s["mem_peak_kernel"] / (1 << 20) if s["mem_peak_kernel"] else "MISSING (no memory.peak)",
                      "samples": s["samples"], "empty_samples": s["empty_samples"], "procs_max": s["procs_max"], "limits_as_read_by_kernel": s["limits"]}
        return {"period_s": self.period, "cgroups": out, "lower_bound_note": "sampled; CPU and peaks can be missed between samples",
                "backend": lim.backend()}
