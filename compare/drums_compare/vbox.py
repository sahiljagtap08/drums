"""VirtualBox incumbent helpers: readback of the VM's ACTUAL settings, snapshot listing and deletion, and per-VM CPU and memory measured from the VM's own processes.
Nothing here starts, imports or modifies a VM except `delete_snapshots` (between-block cleanup of snapshots THIS run took). Runs only on the authorized host; the parsers are
tested against recorded text. PROTOTYPE, no real VM has been read yet."""

import os
import subprocess
import threading
import time


def parse_machinereadable(text):
    """`VBoxManage showvminfo --machinereadable` lines are key=value, values optionally double-quoted."""
    out = {}
    for ln in text.splitlines():
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip().strip('"')] = v.strip().strip('"')
    return out


def run_vbox(args, runner=subprocess.run, timeout=120):
    r = runner(["VBoxManage", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"VBoxManage {' '.join(args)} failed: {(r.stderr or r.stdout)[:300]}")
    return r.stdout


def vm_readback(vm, runner=subprocess.run):
    """The settings the hypervisor reports, not the ones we asked for."""
    info = parse_machinereadable(run_vbox(["showvminfo", vm, "--machinereadable"], runner))
    return {"name": info.get("name"), "uuid": info.get("UUID"), "state": info.get("VMState"), "cpus": int(info["cpus"]) if info.get("cpus") else None,
            "memory_mib": int(info["memory"]) if info.get("memory") else None, "nic1": info.get("nic1"), "hostonlyadapter1": info.get("hostonlyadapter1"),
            "bridgeadapter1": info.get("bridgeadapter1"), "cpuexecutioncap": info.get("cpuexecutioncap"), "pae": info.get("pae"), "hwvirtex": info.get("hwvirtex"),
            "nestedpaging": info.get("nestedpaging"), "vrde": info.get("vrde"), "raw_keys": len(info)}


def snapshot_names(vm, runner=subprocess.run):
    try:
        text = run_vbox(["snapshot", vm, "list", "--machinereadable"], runner)
    except RuntimeError as e:
        if "does not have any snapshots" in str(e):
            return []
        raise
    return sorted({v for k, v in parse_machinereadable(text).items() if k.startswith("SnapshotName")})


def check_parity(readback, cpus=2, memory_mib=4096):
    """Hard requirement for the matched run: 2 vCPU, 4096 MiB exactly as the hypervisor reports, and no execution cap that would change the effective CPU."""
    why = []
    if readback.get("cpus") != cpus:
        why.append(f"VM reports cpus={readback.get('cpus')}, need {cpus}")
    if readback.get("memory_mib") != memory_mib:
        why.append(f"VM reports memory={readback.get('memory_mib')} MiB, need {memory_mib}")
    if readback.get("cpuexecutioncap") not in (None, "100"):
        why.append(f"cpuexecutioncap={readback.get('cpuexecutioncap')} (must be unset or 100)")
    return why


def delete_snapshots(vm, names, runner=subprocess.run):
    """Delete the snapshots this run took (never init_state). Returns {name: error or None}."""
    out = {}
    for n in names:
        if n == "init_state":
            out[n] = "refused: init_state is the base snapshot"
            continue
        try:
            run_vbox(["snapshot", vm, "delete", n], runner, timeout=600)
            out[n] = None
        except RuntimeError as e:
            out[n] = str(e)[:200]
    return out


def vm_pids(vm_uuid, vm_name, proc_root="/proc"):
    """Pids of the VM's headless processes: VBoxHeadless / VirtualBoxVM / VBoxSVC children carrying the VM's uuid or name on their command line."""
    pids = []
    for p in os.listdir(proc_root):
        if not p.isdigit():
            continue
        try:
            cmd = open(os.path.join(proc_root, p, "cmdline"), "rb").read().decode(errors="replace").split("\0")
        except OSError:
            continue
        exe = os.path.basename(cmd[0]) if cmd else ""
        if exe in ("VBoxHeadless", "VirtualBoxVM", "VBoxVMM") and any(a in (vm_uuid, vm_name) or (vm_uuid and vm_uuid in a) for a in cmd[1:]):
            pids.append(int(p))
    return pids


def _stat(pid, proc_root):
    try:
        f = open(os.path.join(proc_root, str(pid), "stat")).read().rsplit(")", 1)[1].split()
        rss_pages = int(open(os.path.join(proc_root, str(pid), "statm")).read().split()[1])
        return int(f[11]) + int(f[12]), rss_pages
    except (OSError, IndexError, ValueError):
        return None


class VBoxProbe:
    """Per-VM resource capture for the incumbent arm, the counterpart of `envd_linux.cgroup_telemetry.CgroupProbe` (same begin()/end() shape). CPU is the sum over the VM's
    processes of utime+stime (per pid the highest seen) and RSS peak is the highest sampled sum: both are LOWER BOUNDS from sampling. It does not see work done by
    kernel threads on the VM's behalf, and a VM's guest memory is counted only as far as it is resident in these processes."""

    def __init__(self, vm_uuid, vm_name, period=0.5, proc_root="/proc", hz=None):
        self.uuid, self.name, self.period, self.proc_root = vm_uuid, vm_name, period, proc_root
        self.hz = hz or os.sysconf("SC_CLK_TCK")
        self._stop = threading.Event()
        self._t = None
        self.reset()

    def reset(self):
        self.ticks, self.rss_peak_pages, self.samples, self.empty_samples, self.pids_max = {}, 0, 0, 0, 0

    def _sample(self):
        pids = vm_pids(self.uuid, self.name, self.proc_root)
        self.samples += 1
        self.pids_max = max(self.pids_max, len(pids))
        if not pids:
            self.empty_samples += 1
        total = 0
        for pid in pids:
            st = _stat(pid, self.proc_root)
            if st:
                self.ticks[pid] = max(self.ticks.get(pid, 0), st[0])
                total += st[1]
        self.rss_peak_pages = max(self.rss_peak_pages, total)

    def _run(self):
        while not self._stop.wait(self.period):
            self._sample()

    def begin(self):
        self.reset()
        self._stop.clear()
        self._sample()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def end(self):
        self._stop.set()
        self._t.join(5)
        self._sample()
        page = os.sysconf("SC_PAGE_SIZE")
        return {"vm": self.name, "cpu_s_lower_bound": sum(self.ticks.values()) / self.hz, "rss_peak_mib_sampled": self.rss_peak_pages * page / (1 << 20),
                "samples": self.samples, "empty_samples": self.empty_samples, "vm_processes_max": self.pids_max, "period_s": self.period,
                "note": "sampled lower bound from the VM's own processes (VBoxHeadless / VirtualBoxVM); an empty sample means no VM process was found"}
