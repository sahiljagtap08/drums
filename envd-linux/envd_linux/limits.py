"""Per-session resource limits (READINESS, not a result): cgroup limits that match the incumbent's shipped VM shape.

The ScaleCUA worker config gives each environment 2 vCPU and 4 GiB. A Drums-vs-incumbent number taken with sessions
running unlimited would be uninterpretable, so the Supervisor can give every session its own cgroup with the same shape.

Backends: "v2" (unified hierarchy: cpu.max, memory.max, memory.swap.max, pids.max) and "v1" (separate controller
mounts: cpu.cfs_quota_us, memory.limit_in_bytes, pids.max). The v2 path is the one a modern host (Ubuntu 22.04+,
Amazon Linux 2023) uses. The development sandbox is a v1/hybrid host, so ENFORCEMENT of the v1 path is tested here
and the v2 path is tested against a fake hierarchy only (it writes the files in the right form; the kernel is not
involved). That is a declared gap, not a result.

Attachment is done by the session's own init: its first act, before it forks anything, is to write "0" (itself) into each
cgroup.procs, so every descendant inherits the cgroup with no race. The cgroup directories are recorded in the sweep
ledger before they exist and removed by close() and by the sweep."""

import json
import os
from dataclasses import asdict, dataclass

CGROUP_ROOT = "/sys/fs/cgroup"
GIB = 1 << 30


@dataclass(frozen=True)
class Limits:
    cpus: float = 2.0
    memory_bytes: int = 4 * GIB
    pids: int = 4096  # a fork-bomb bound, not part of the incumbent's shape
    swap: bool = False


INCUMBENT_SHAPE = Limits()


def backend(root=CGROUP_ROOT):
    """"v2" when `root` is a unified hierarchy with cpu, memory and pids delegated, "v1" when those controllers are
    separate mounts, else None."""
    ctl = os.path.join(root, "cgroup.controllers")
    if os.path.exists(ctl):
        have = open(ctl).read().split()
        return "v2" if {"cpu", "memory", "pids"} <= set(have) else None
    return "v1" if all(os.path.isdir(os.path.join(root, c)) for c in ("cpu", "memory", "pids")) else None


def _write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


class SessionCgroup:
    """The directories for one session. create() makes them and applies the limits; procs_files() are what the session's
    init writes "0" into; remove() deletes them once empty."""

    def __init__(self, run_id, slot, limits=INCUMBENT_SHAPE, root=CGROUP_ROOT, kind=None):
        self.kind = kind or backend(root)
        if self.kind is None:
            raise OSError(f"no usable cgroup hierarchy under {root}")
        self.limits, self.root = limits, root
        sub = f"drums/{run_id}/s{slot}"
        self.dirs = ([os.path.join(root, sub)] if self.kind == "v2"
                     else [os.path.join(root, c, sub) for c in ("cpu", "memory", "pids")])

    def create(self):
        for d in self.dirs:
            os.makedirs(d, exist_ok=True)
        lim = self.limits
        period = 100000
        if self.kind == "v2":
            parent = os.path.dirname(self.dirs[0])
            for p in (os.path.dirname(parent), parent):  # enable the controllers for the children
                if os.path.exists(os.path.join(p, "cgroup.subtree_control")):
                    try:
                        _write(os.path.join(p, "cgroup.subtree_control"), "+cpu +memory +pids")
                    except OSError:
                        pass
            d = self.dirs[0]
            _write(os.path.join(d, "cpu.max"), f"{int(lim.cpus * period)} {period}")
            _write(os.path.join(d, "memory.max"), int(lim.memory_bytes))
            if not lim.swap:
                _write(os.path.join(d, "memory.swap.max"), 0)
            _write(os.path.join(d, "pids.max"), lim.pids)
        else:
            cpu, mem, pids = self.dirs
            _write(os.path.join(cpu, "cpu.cfs_period_us"), period)
            _write(os.path.join(cpu, "cpu.cfs_quota_us"), int(lim.cpus * period))
            _write(os.path.join(mem, "memory.limit_in_bytes"), int(lim.memory_bytes))
            if not lim.swap and os.path.exists(os.path.join(mem, "memory.memsw.limit_in_bytes")):
                _write(os.path.join(mem, "memory.memsw.limit_in_bytes"), int(lim.memory_bytes))
            _write(os.path.join(pids, "pids.max"), lim.pids)

    def procs_files(self):
        return [os.path.join(d, "cgroup.procs") for d in self.dirs]

    def remove(self):
        gone = []
        for d in self.dirs:
            try:
                os.rmdir(d)
                gone.append(d)
            except OSError:
                pass
            for up in (os.path.dirname(d), os.path.dirname(os.path.dirname(d))):  # drums/<run>, then drums if now empty
                try:
                    os.rmdir(up)
                except OSError:
                    pass
        return gone

    def record(self):
        """What goes into run.json."""
        return {"backend": self.kind, "dirs": self.dirs, **asdict(self.limits),
                "enforced_on_this_host": self.kind == backend(self.root)}


def read_back(cg):
    """The limits as the kernel reports them (for the audit: what was applied, not what was asked)."""
    out = {}
    if cg.kind == "v2":
        d = cg.dirs[0]
        out = {"cpu.max": open(os.path.join(d, "cpu.max")).read().strip(),
               "memory.max": open(os.path.join(d, "memory.max")).read().strip(),
               "pids.max": open(os.path.join(d, "pids.max")).read().strip()}
    else:
        cpu, mem, pids = cg.dirs
        out = {"cpu.cfs_quota_us": open(os.path.join(cpu, "cpu.cfs_quota_us")).read().strip(),
               "cpu.cfs_period_us": open(os.path.join(cpu, "cpu.cfs_period_us")).read().strip(),
               "memory.limit_in_bytes": open(os.path.join(mem, "memory.limit_in_bytes")).read().strip(),
               "pids.max": open(os.path.join(pids, "pids.max")).read().strip()}
    return out


if __name__ == "__main__":
    print(json.dumps({"backend": backend()}))
