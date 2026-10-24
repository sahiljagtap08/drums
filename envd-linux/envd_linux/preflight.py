"""Preflight of a CANDIDATE HOST for the matched comparison (docs/incumbent-preflight-plan.md, item I2). By default it is READ-ONLY: it changes nothing, installs nothing and
contacts no network service. It reports measured facts and a go / no-go per arm; it makes NO capacity claim (2 vCPU / 4 GiB per environment is a parity setting, not a host
minimum).

Three kinds of check, kept apart: `static` (a file, flag or binary is present: NOT proof that the capability works), `runtime_probe` (an attempt actually succeeded; only with
`--probe`, which briefly creates and removes a namespace set, an overlay mount, a cgroup directory and asks VirtualBox about the host CPU) and `gate` (the first-pass gates of the
plan: application and token survive a snapshot revert, the real evaluator scores correct and wrong, the canary is clean, resource parity is applied: these can only be shown by the
matched check itself, so here they are always `untested`). A joint run is `ready` only when every needed check is `ok`.

    python -m envd_linux.preflight [--criu-home DIR] [--disk-path PATH] [--probe]"""

import argparse
import json
import os
import shutil
import subprocess

from . import limits


def _read(path):
    with open(path) as f:
        return f.read()


def check(name, ok, detail, needed_for, untested=False, kind="static"):
    """status: "ok" (checked, present), "unsupported" (checked, absent or failing: a capability this host does not have) or "untested" (NOT checked: unknown, never
    to be read as supported or as unsupported)."""
    return {"name": name, "ok": bool(ok) and not untested, "status": "untested" if untested else ("ok" if ok else "unsupported"), "kind": kind, "detail": detail, "needed_for": needed_for}


GATES = ["gate_app_and_token_survive_snapshot_revert", "gate_evaluator_scores_correct_and_wrong", "gate_canary_clean_on_both_arms", "gate_resource_parity_applied_and_measured"]


def default_probes(run_cmd, which):
    """(name, needed_for, function -> (ok, detail)). Each creates and immediately removes what it makes."""
    def namespaces():
        r = run_cmd(["unshare", "--pid", "--mount", "--net", "--uts", "--ipc", "--fork", "true"])
        return r.returncode == 0, "unshare of pid, mount, net, uts, ipc succeeded" if r.returncode == 0 else f"unshare failed rc={r.returncode}: {r.stderr.strip()[:120]}"

    def overlay():
        import tempfile
        d = tempfile.mkdtemp(prefix="drums-preflight-")
        try:
            for n in ("lower", "upper", "work", "merged"):
                os.makedirs(os.path.join(d, n))
            r = run_cmd(["mount", "-t", "overlay", "overlay", "-o", f"lowerdir={d}/lower,upperdir={d}/upper,workdir={d}/work,redirect_dir=on", f"{d}/merged"])
            if r.returncode == 0:
                run_cmd(["umount", f"{d}/merged"])
            return r.returncode == 0, "overlay mount with redirect_dir=on succeeded and was unmounted" if r.returncode == 0 else f"overlay mount failed: {r.stderr.strip()[:120]}"
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def cgroup():
        cg = limits.SessionCgroup("preflight", 0)
        cg.create()
        cg.remove()
        return True, f"{cg.kind} cgroup directory created, limits written and removed"

    def vbox_hw():
        if not which("VBoxManage"):
            return False, "VBoxManage not found"
        out = run_cmd(["VBoxManage", "list", "hostinfo"]).stdout
        line = next((ln.strip() for ln in out.splitlines() if "HW virtualization" in ln), "")
        return "yes" in line.lower(), line or "VirtualBox did not report hardware virtualization"
    return [("probe_namespaces_can_be_created", ["drums"], namespaces), ("probe_overlay_mount_with_redirect_dir", ["drums"], overlay),
            ("probe_cgroup_limits_can_be_written", ["drums_limits"], cgroup), ("probe_virtualbox_reports_hardware_virtualization", ["incumbent"], vbox_hw)]


def run(criu_home=None, disk_path="/", read=_read, which=shutil.which, run_cmd=None, probe=False, probes=None):
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
    ext_lines = run_cmd(["VBoxManage", "list", "extpacks"]).stdout.strip().splitlines() if vb else []
    ext = ext_lines[0] if ext_lines else ("VirtualBox absent" if not vb else "(empty output from `VBoxManage list extpacks`)")
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
        out.append(check(f"namespace_{ns}_file", os.path.exists(f"/proc/self/ns/{ns}"), f"/proc/self/ns/{ns} exists (static: not proof that this process may create one)", ["drums"]))
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
    probes = probes or default_probes(run_cmd, which)
    for name, needed, fn in probes:
        if not probe:
            out.append(check(name, False, "runtime probe not run (use --probe)", needed, untested=True, kind="runtime_probe"))
            continue
        try:
            ok, detail = fn()
        except Exception as e:  # a probe that raises is a failed capability, reported with the error
            ok, detail = False, f"{type(e).__name__}: {e}"
        out.append(check(name, ok, detail, needed, kind="runtime_probe"))
    for g in GATES:
        out.append(check(g, False, "first-pass gate: shown only by the matched check itself", ["joint"], untested=True, kind="gate"))
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
            "go_joint_run_on_this_host": go("incumbent") and go("drums") and go("drums_restore") and go("drums_limits") and go("joint"),
            "no_go_reasons": [f"{c['name']}: {c['detail']}" for c in out if c["status"] == "unsupported" and c["needed_for"]],
            "untested": [c["name"] for c in out if c["status"] == "untested"], "checks": out,
            "note": "go_* needs every needed check to be 'ok': an 'untested' item keeps the answer at no-go until it is tested. No minimum RAM or disk is asserted."}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--criu-home", default=os.environ.get("DRUMS_CRIU_HOME"))
    ap.add_argument("--disk-path", default="/")
    ap.add_argument("--probe", action="store_true", help="run the runtime probes (each briefly creates and removes a namespace set, an overlay mount, a cgroup directory)")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.criu_home, a.disk_path, probe=a.probe), indent=1))


if __name__ == "__main__":
    main()
