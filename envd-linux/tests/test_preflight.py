"""The host preflight is read-only and gives a reasoned go / no-go; here it must say NO-GO for the incumbent on this sandbox (no CPU virtualization, no VirtualBox)
and report, never judge, memory and disk."""

from envd_linux import preflight


def test_this_sandbox_is_no_go_for_the_incumbent_and_says_why():
    r = preflight.run()
    assert r["go_incumbent"] is False
    reasons = " ".join(r["no_go_reasons"])
    assert "cpu_virtualization" in reasons and "virtualbox_installed" in reasons
    st = {c["name"]: c["status"] for c in r["checks"]}
    assert st["cpu_virtualization"] == "unsupported" and st["virtualbox_installed"] == "unsupported"  # checked and absent
    assert st["virtualbox_extension_pack"] == "untested" and st["virtualbox_and_drums_on_one_host"] == "untested"  # not checked: neither supported nor unsupported
    assert "virtualbox_extension_pack" in r["untested"] and "virtualbox_extension_pack" not in " ".join(r["no_go_reasons"])
    assert "minimum" not in str(r["checks"]).lower().replace("no minimum", "")  # no speculative minimum RAM or disk anywhere in the checks
    mem = [c for c in r["checks"] if c["name"] == "measured_memory_and_disk"][0]
    assert mem["ok"] and "no capacity claim" in mem["detail"]["note"]


def test_a_host_with_vmx_and_virtualbox_passes_the_incumbent_checks():
    fake = {"/proc/cpuinfo": "flags\t: fpu vmx sse2\n"}

    def read(p):
        if p in fake:
            return fake[p]
        return open(p).read()

    class R:
        def __init__(self, s):
            self.stdout = s
    r = preflight.run(read=read, which=lambda t: "/usr/bin/" + t if t == "VBoxManage" else None,
                      run_cmd=lambda cmd: R("7.1.4r165100\n" if "--version" in cmd else "Extension Packs: 0\n"))
    names = {c["name"]: c for c in r["checks"]}
    assert names["cpu_virtualization"]["ok"] and names["virtualbox_installed"]["ok"] and names["virtualbox_installed"]["detail"].startswith("7.1")
    assert names["virtualbox_extension_pack"]["status"] == "untested" and "Extension Packs: 0" in names["virtualbox_extension_pack"]["detail"]
    assert r["go_incumbent"] is False  # untested items keep the answer at no-go until they are tested


def test_empty_extpacks_output_does_not_crash_and_static_checks_are_not_runtime_probes():
    class R:
        def __init__(self, s, rc=0):
            self.stdout, self.returncode, self.stderr = s, rc, ""
    r = preflight.run(which=lambda t: "/usr/bin/" + t if t == "VBoxManage" else None, run_cmd=lambda cmd: R(""))
    ext = [c for c in r["checks"] if c["name"] == "virtualbox_extension_pack"][0]
    assert "empty output" in ext["detail"] and ext["status"] == "untested"
    kinds = {c["name"]: c["kind"] for c in r["checks"]}
    assert kinds["namespace_pid_file"] == "static" and kinds["probe_namespaces_can_be_created"] == "runtime_probe" and kinds["gate_canary_clean_on_both_arms"] == "gate"
    probes = [c for c in r["checks"] if c["kind"] == "runtime_probe"]
    assert probes and all(c["status"] == "untested" for c in probes)  # not run: untested, neither supported nor unsupported


def test_joint_is_not_ready_while_gates_or_limits_or_hardware_state_are_untested_even_if_everything_else_passes():
    class R:
        def __init__(self, s="", rc=0):
            self.stdout, self.returncode, self.stderr = s, rc, ""
    probes = [("probe_namespaces_can_be_created", ["drums"], lambda: (True, "ok")), ("probe_overlay_mount_with_redirect_dir", ["drums"], lambda: (True, "ok")),
              ("probe_cgroup_limits_can_be_written", ["drums_limits"], lambda: (True, "ok")), ("probe_virtualbox_reports_hardware_virtualization", ["incumbent"], lambda: (True, "yes"))]
    r = preflight.run(criu_home="/nonexistent", read=lambda p: "flags : vmx\n" if p == "/proc/cpuinfo" else open(p).read(), which=lambda t: "/usr/bin/" + t,
                      run_cmd=lambda cmd: R("7.1\n"), probe=True, probes=probes)
    assert r["go_joint_run_on_this_host"] is False
    assert "gate_evaluator_scores_correct_and_wrong" in r["untested"]  # the gates alone keep a joint run at no-go


def test_a_failing_probe_is_unsupported_with_its_error():
    def boom():
        raise PermissionError("not permitted")
    r = preflight.run(probe=True, probes=[("probe_namespaces_can_be_created", ["drums"], boom)])
    c = [c for c in r["checks"] if c["name"] == "probe_namespaces_can_be_created"][0]
    assert c["status"] == "unsupported" and "PermissionError" in c["detail"]
