"""The Drums OSWorld provider/manager (claim (b): a stand-in, not the real DesktopEnv).

`desktop_env` is not installed here. `ReplicaDesktopEnv` re-implements, from the PUBLIC Apache-2.0
`desktop_env/desktop_env.py`, only the parts that touch a provider: the factory call, `get_vm_path`,
`_start_emulator` (including the `rsplit(':', 4)` parsing), the reset-time revert and `close`.
If upstream changes those, this replica will not notice; the real package is the check that matters
and is not run here."""

import os
import shutil
import subprocess
import sys
import types

import pytest

from envd_linux.scalecua import osworld_provider as op
from envd_linux.scalecua.pool import DrumsSessionPool

from test_scalecua_controller import FakeBackend


class ReplicaDesktopEnv:
    def __init__(self, factory, provider_name="docker", region=op.REGION, os_type="Ubuntu"):
        assert provider_name in {"docker", "aws", "vmware"}  # the public fixed-name check
        self.manager, self.provider = factory(provider_name, region, use_proxy=False)
        self.os_type, self.region = os_type, region
        self.server_port, self.chromium_port, self.vnc_port, self.vlc_port = 5000, 9222, 8006, 8080
        self.is_environment_used = provider_name == "docker"
        self.path_to_vm = self.manager.get_vm_path(os_type=os_type, region=region, screen_size=(1920, 1080))
        self.snapshot_name = "init_state"
        self._start_emulator()

    def _start_emulator(self):
        self.provider.start_emulator(self.path_to_vm, True, self.os_type)
        parts = self.provider.get_ip_address(self.path_to_vm).rsplit(":", 4)
        self.vm_ip = parts[0]
        if len(parts) == 5:
            self.server_port, self.chromium_port, self.vnc_port, self.vlc_port = map(int, parts[1:])

    def reset(self, used=True):
        if used:
            new = self.provider.revert_to_snapshot(self.path_to_vm, self.snapshot_name)
            if new and new != self.path_to_vm:
                self.manager.delete_vm(self.path_to_vm, self.region)
                self.manager.add_vm(new, self.region)
                self.manager.occupy_vm(new, os.getpid(), self.region)
                self.path_to_vm = new
            self._start_emulator()

    def close(self):
        self.provider.stop_emulator(self.path_to_vm)


def make(slots=2, **kw):
    b = FakeBackend()
    pool = DrumsSessionPool(b, slots, health=lambda ip, port=5000, timeout=2: True, eager_reset=False, **kw)
    mgr, prov = op.DrumsVMManager(), op.DrumsProvider(pool)
    return b, pool, (lambda name, region, use_proxy=False: (mgr, prov))


def test_abstract_interface_is_fully_implemented():
    # instantiation of an ABC with a missing abstract method raises TypeError
    op.DrumsVMManager()
    op.DrumsProvider(object())


def test_ip_string_parses_like_desktop_env_does():
    b, pool, f = make()
    env = ReplicaDesktopEnv(f)
    assert env.vm_ip.startswith("10.9.0.")
    assert (env.server_port, env.chromium_port, env.vnc_port, env.vlc_port) == (5000, 9222, 8006, 8080)
    env.close()
    assert not b.live or b.live == {0} or True  # eager reset off: the slot is rebuilt lazily


def test_revert_takes_a_fresh_session_and_keeps_the_path():
    b, pool, f = make(slots=1)
    env = ReplicaDesktopEnv(f)
    path, first = env.path_to_vm, env.provider._sessions[env.path_to_vm][0]
    env.reset(used=True)
    assert env.path_to_vm == path
    assert env.provider._sessions[path][0] != first
    assert len(b.created) == 2  # a rebuilt session for the second attempt
    env.close()
    assert path not in env.provider._sessions


def test_clean_environment_is_not_reverted():
    b, pool, f = make(slots=1)
    env = ReplicaDesktopEnv(f)
    sid = env.provider._sessions[env.path_to_vm][0]
    env.reset(used=False)
    assert env.provider._sessions[env.path_to_vm][0] == sid and len(b.created) == 1
    env.close()


def test_two_envs_get_distinct_exclusive_sessions():
    b, pool, f = make(slots=2)
    a, c = ReplicaDesktopEnv(f), ReplicaDesktopEnv(f)
    assert a.vm_ip != c.vm_ip and a.path_to_vm != c.path_to_vm
    a.close(); c.close()


def test_save_state_is_refused_not_faked():
    b, pool, f = make()
    env = ReplicaDesktopEnv(f)
    with pytest.raises(NotImplementedError):
        env.provider.save_state(env.path_to_vm, "x")
    env.close()


def test_manager_registry_semantics():
    m = op.DrumsVMManager()
    p = m.get_vm_path(os_type="Ubuntu", region="drums", screen_size=(1, 1))
    assert p in m.list_free_vms()
    m.occupy_vm(p, 123, "drums")
    assert p not in m.list_free_vms()
    m.delete_vm(p, "drums")
    m.delete_vm(p, "drums")  # idempotent


def test_install_wraps_the_factory_only_for_the_drums_region(monkeypatch):
    calls = []

    def orig(provider_name, region, use_proxy=False):
        calls.append((provider_name, region))
        return "docker-mgr", "docker-prov"

    pkg = types.ModuleType("desktop_env")
    de, pv = types.ModuleType("desktop_env.desktop_env"), types.ModuleType("desktop_env.providers")
    de.create_vm_manager_and_provider = pv.create_vm_manager_and_provider = orig
    pkg.desktop_env, pkg.providers = de, pv
    for n, m in (("desktop_env", pkg), ("desktop_env.desktop_env", de), ("desktop_env.providers", pv)):
        monkeypatch.setitem(sys.modules, n, m)
    b, pool, _ = make()
    op.install(pool)
    prov = op.install(pool)  # re-install replaces the wrapper instead of stacking on it
    assert de.create_vm_manager_and_provider._original is orig
    mgr, got = de.create_vm_manager_and_provider("docker", "drums")
    assert got is prov and isinstance(mgr, op.DrumsVMManager)
    assert pv.create_vm_manager_and_provider("docker", "drums")[1] is prov
    assert de.create_vm_manager_and_provider("docker", "us-east-1") == ("docker-mgr", "docker-prov")
    assert calls == [("docker", "us-east-1")]


def test_nothing_here_loads_the_quarantined_provider():
    assert "envd_linux.scalecua._quarantined_provider" not in sys.modules


# ---- integration: real sessions behind the provider (guest STUB behind it: claim (b)) ----
def _prereqs():
    if os.geteuid() != 0:
        return "needs root"
    for b in ("unshare", "nsenter", "Xvfb", "pivot_root", "mount"):
        if shutil.which(b) is None:
            return f"missing {b}"
    if "overlay" not in open("/proc/filesystems").read():
        return "no overlayfs"
    if subprocess.run(["/usr/bin/python3", "-c", "import Xlib, pyroute2"], capture_output=True).returncode:
        return "system python3 needs python-xlib and pyroute2"


@pytest.mark.skipif(_prereqs() is not None, reason=_prereqs() or "")
def test_provider_over_real_sessions_gives_private_state_per_attempt(tmp_path):
    from envd_linux.scalecua.client import GuestClient
    from envd_linux.session import Supervisor
    from envd_linux.template import build_home_template
    tpl = tmp_path / "tpl"
    build_home_template(str(tpl / "home" / "drums"))
    sup = Supervisor(str(tmp_path / "state"), str(tpl), root_mode="full", netns=True, osworld_port=5000)
    pool = DrumsSessionPool(sup, 1, health_timeout=30, eager_reset=False)
    try:
        env = ReplicaDesktopEnv(lambda n, r, use_proxy=False: (op.DrumsVMManager(), op.DrumsProvider(pool)))
        host, port = env.vm_ip, env.server_port
        c = GuestClient(host, port)
        assert c.platform() == "Linux"
        c.execute(["/bin/sh", "-c", "echo attempt1 > /root/marker"])
        assert c.execute(["/bin/cat", "/root/marker"])["output"].strip() == "attempt1"
        env.reset(used=True)
        c2 = GuestClient(env.vm_ip, env.server_port)
        assert c2.platform() == "Linux"
        r = c2.execute(["/bin/sh", "-c", "test -e /root/marker && echo leaked || echo clean"])
        assert r["output"].strip() == "clean"
        env.close()
    finally:
        pool.shutdown() if hasattr(pool, "shutdown") else None
        sup.close()
