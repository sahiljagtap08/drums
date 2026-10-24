"""Read-only preflight of a CANDIDATE HOST for the matched comparison (docs/incumbent-preflight-plan.md, item I2). It changes nothing, installs nothing and contacts no
network service. It reports measured facts and a go / no-go per arm; it makes NO capacity claim (2 vCPU / 4 GiB per environment is a parity setting, not a host minimum).

    python -m envd_linux.preflight [--criu-home DIR] [--disk-path PATH]"""

import argparse
import json
import os
import shutil
import subprocess

from . import limits


def _read(path):
    with open(path) as f:
        return f.read()


def check(name, ok, detail, needed_for, untested=False):
    """status: "ok" (checked, present), "unsupported" (checked, absent or failing: a capability this host does not have) or "untested" (NOT checked: unknown, never
    to be read as supported or as unsupported)."""
    return {"name": name, "ok": bool(ok) and not untested, "status": "untested" if untested else ("ok" if ok else "unsupported"), "detail": detail, "needed_for": needed_for}


def run(criu_home=None, disk_path="/", read=_read, which=shutil.which, run_cmd=None):
    run_cmd = run_cmd or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=30))
    out = []
    try:
        cpu = read("/proc/cpuinfo")
    except OSError:
        cpu = ""
    flags = {f for ln in cpu.splitlines() if ln.startswith("flags") for f in ln.split(":", 1)[1].split()}
    out.append(check("cpu_virtualization", bool(flags & {"vmx", "svm"}), "vmx" if "vmx" in flags else "svm" if "svm" in flags else "neither vmx nor svm in /proc/cpuinfo", ["incumbent"]))
    out.append(check("dev_kvm", os.path.exists("/dev/kvm"), "present" if os.path.exists("/dev/kvm") else "absent (informational: VirtualBox does not need it)", []))
    vb = which("VBoxManage")
    ver = run_cmd(["VBoxManage", "--version"]).stdout.strip() if vb else ""
    out.append(check("virtualbox_installed", bool(vb), ver or "VBoxManage not found", ["incumbent"]))
    ext = run_cmd(["VBoxManage", "list", "extpacks"]).stdout.strip().splitlines()[0] if vb else "VirtualBox absent"
    out.append(check("virtualbox_extension_pack", False, f"{ext}. The Extension Pack (separate PUEL / commercial licence) is NOT ASSUMED; whether the first pass needs a feature "
                     "that lives in it is UNTESTED, not 'not needed'", ["incumbent"], untested=True))
    out.append(check("virtualbox_and_drums_on_one_host", False, "co-residence untested (alternating blocks avoid simultaneous use; correctness of each beside the other is not established)",
                     ["joint"], untested=True))
    out.append(check("virtualbox_on_a_nested_or_cloud_host", False, "not documented as supported by the AWS or GCE nested-virtualization pages; untested; this preflight assumes a non-virtualized host", [], untested=True))
    out.append(check("root", os.geteuid() == 0, f"euid {os.geteuid()}", ["incumbent", "drums"]))
    out.append(check("arch_x86_64", os.uname().machine == "x86_64", os.uname().machine, ["incumbent", "drums"]))
    fs = read("/proc/filesystems") if os.path.exists("/proc/filesystems") else ""
    out.append(check("overlayfs", "overlay" in fs, "overlay in /proc/filesystems" if "overlay" in fs else "no overlayfs", ["drums"]))
    for ns in ("pid", "mnt", "net", "ipc", "uts"):
        out.append(check(f"namespace_{ns}", os.path.exists(f"/proc/self/ns/{ns}"), f"/proc/self/ns/{ns}", ["drums"]))
    out.append(check("criu_kernel_support", os.path.exists("/proc/sys/kernel/ns_last_pid"), "kernel.ns_last_pid present" if os.path.exists("/proc/sys/kernel/ns_last_pid") else "no ns_last_pid (CHECKPOINT_RESTORE?)", ["drums_restore"]))
    for tool in ("unshare", "nsenter", "pivot_root", "mount", "nft", "Xvfb"):
        out.append(check(f"tool_{tool}", bool(which(tool)), which(tool) or "not found", ["drums"]))
    out.append(check("tool_ip", bool(which("ip")), which("ip") or "not found (needed only by CRIU's network restore; a private copy on PATH is enough)", ["drums_restore"]))
    be = limits.backend()
    out.append(check("cgroup_hierarchy", be is not None, str(be) + (" (the v2 backend has never run on a real v2 kernel here)" if be == "v2" else ""), ["drums_limits"], untested=(be == "v2")))
    if criu_home:
        c = os.path.join(criu_home, "x-4.2", "usr", "sbin", "criu")
        out.append(check("criu_private_install", os.path.exists(c), c, ["drums_restore"]))
    else:
        out.append(check("criu_private_install", False, "no --criu-home given: not checked", ["drums_restore"], untested=True))
    mem = {}
    try:
        mem = {k: int(v.split()[0]) for k, v in (ln.split(":") for ln in read("/proc/meminfo").splitlines())}
    except (OSError, ValueError):
        pass
    du = shutil.disk_usage(disk_path)
    out.append(check("measured_memory_and_disk", True, {"MemTotal_MiB": mem.get("MemTotal", 0) // 1024, "MemAvailable_MiB": mem.get("MemAvailable", 0) // 1024,
                                                      "disk_free_GiB_at_" + disk_path: round(du.free / 2**30, 1), "note": "reported, never judged: no capacity claim"}, []))
    go = lambda arm: all(c["status"] == "ok" for c in out if arm in c["needed_for"])
    return {"go_incumbent": go("incumbent"), "go_drums": go("drums"), "go_drums_restore": go("drums_restore"),
            "go_joint_run_on_this_host": go("incumbent") and go("drums") and go("drums_restore") and go("joint"),
            "no_go_reasons": [f"{c['name']}: {c['detail']}" for c in out if c["status"] == "unsupported" and c["needed_for"]],
            "untested": [c["name"] for c in out if c["status"] == "untested"], "checks": out,
            "note": "go_* needs every needed check to be 'ok': an 'untested' item keeps the answer at no-go until it is tested. No minimum RAM or disk is asserted."}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--criu-home", default=os.environ.get("DRUMS_CRIU_HOME"))
    ap.add_argument("--disk-path", default="/")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.criu_home, a.disk_path), indent=1))


if __name__ == "__main__":
    main()
