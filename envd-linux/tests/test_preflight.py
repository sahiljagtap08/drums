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
