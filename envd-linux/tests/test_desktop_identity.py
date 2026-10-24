"""desktop/uid plumbing: defaults unchanged, privilege-drop order, Supervisor validation. No process is started as another user here."""
import os

import pytest

from envd_linux import session as S
from envd_linux import session_init as SI


def test_defaults_do_not_change_the_processes_or_drop_anything():
    assert SI.WM_COMMANDS["openbox"] == ["openbox"] and SI.drop_privileges(-1, -1) is None
    assert SI.WM_COMMANDS["gnome"][0] == "gnome-session" and "--builtin" in SI.WM_COMMANDS["gnome"]


def test_the_drop_clears_supplementary_groups_then_gid_then_uid(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append(("groups", tuple(g))))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(("uid", u)))
    SI.drop_privileges(1000, 1001)()
    assert calls == [("groups", ()), ("gid", 1001), ("uid", 1000)]  # uid LAST: after it the process may no longer change groups


def test_the_process_table_reads_proc_with_uids():
    t = SI.process_table()
    assert any(pid == os.getpid() and uid == os.geteuid() and ppid == os.getppid() for pid, ppid, uid, _ in t)


def sup(tmp_path, **kw):
    return S.Supervisor(str(tmp_path / "state"), str(tmp_path / "tpl"), sweep_on_start=False, **kw)


def test_the_supervisor_validates_desktop_and_user_and_defaults_stay(tmp_path):
    s = sup(tmp_path)
    assert (s.desktop, s.uid, s.gid, s.user) == ("openbox", -1, -1, "")
    with pytest.raises(ValueError, match="desktop must be"):
        sup(tmp_path, desktop="kde")
    for kw in ({"uid": 0, "gid": 0, "user": "root"}, {"uid": 1000, "gid": -1, "user": "user"}, {"uid": 1000, "gid": 1000, "user": "us er"}, {"uid": 1000, "gid": 1000, "user": ""}):
        with pytest.raises(ValueError, match="uid needs"):
            sup(tmp_path, **kw)
    with pytest.raises(ValueError, match="fork"):
        sup(tmp_path, uid=1000, gid=1000, user="user", server_mode="fork")
    assert sup(tmp_path, uid=1000, gid=1000, user="user", desktop="gnome").user == "user"


def test_the_server_identity_is_its_own_process_tree_never_init_or_other_python():
    table = [(1, 0, 0, "python3"), (10, 1, 1000, "python"), (11, 10, 1000, "python"), (12, 11, 1000, "sh"), (20, 1, 0, "python3"), (30, 1, 1000, "gnome-shell")]
    tree = SI.tree_of(10, table)
    assert tree == {10, 11, 12}
    assert sorted({u for pid, _, u, _ in table if pid in tree}) == [1000]  # init (1, uid 0) and the other root python (20) are not counted
    assert SI.tree_of(99, table) == {99}


def test_only_the_two_server_output_directories_change_owner_and_nothing_else_in_the_kit():
    import inspect
    src = inspect.getsource(SI.start_real_desktop)
    assert '("screenshots", "__pycache__")' in src and "os.chown(d, a.uid, a.gid)" in src and "chown(kit" not in src and "chown(os.path.join(kit" not in src


def test_system_bus_is_off_by_default_and_reaches_the_init_only_when_asked(tmp_path):
    assert sup(tmp_path).system_bus is False
    import inspect
    assert '*(["--system-bus"] if self.system_bus else [])' in inspect.getsource(S.Supervisor.create)


def test_a_system_bus_is_refused_before_any_effect_unless_the_root_is_full_and_the_network_is_private(tmp_path):
    for kw in ({}, {"root_mode": "home"}, {"root_mode": "home", "netns": True}, {"root_mode": "full", "netns": False}):
        with pytest.raises(ValueError, match="system_bus needs root_mode='full' and netns=True"):
            sup(tmp_path, system_bus=True, **kw)
    assert not os.path.exists(tmp_path / "state")  # nothing was created by a refused construction
    assert sup(tmp_path).system_bus is False  # the default is unchanged


import re
SYSTEM_CONF = """<!DOCTYPE busconfig>
<busconfig>
  <type>system</type>
  <fork/>
  <listen>unix:path=/var/run/dbus/system_bus_socket</listen>
  <policy context="default"><allow own="*"/></policy>
  <standard_system_servicedirs/>
  <servicedir>/usr/local/share/dbus-1/system-services</servicedir>
  <servicehelper>/usr/lib/dbus-1.0/dbus-daemon-launch-helper</servicehelper>
  <include ignore_missing="yes">/etc/dbus-1/system-local.conf</include>
</busconfig>
"""


def test_the_generated_bus_configuration_cannot_launch_services_and_keeps_the_images_own_policy():
    out = SI.private_system_conf(SYSTEM_CONF)
    assert "servicedirs" not in out and "servicehelper" not in out and "servicedir" not in out
    assert "<type>system</type>" in out and '<policy context="default">' in out and "system-local.conf" in out
    with pytest.raises(SystemExit, match="not a system bus"):
        SI.private_system_conf("<busconfig><type>session</type></busconfig>")


MI_OK = "36 29 0:30 / /run rw,nosuid - tmpfs tmpfs rw,mode=755\n"
MI_HOST = "22 1 8:1 / / rw - ext4 /dev/sda1 rw\n"


def test_the_bus_starts_only_where_run_is_a_private_tmpfs_with_no_socket_already_there():
    SI.check_private_run(MI_OK, False)
    with pytest.raises(SystemExit, match="not a tmpfs mount"):
        SI.check_private_run(MI_HOST, False)  # the host's /run is not a separate mount of this namespace
    with pytest.raises(SystemExit, match="already exists"):
        SI.check_private_run(MI_OK, True)


def test_start_drops_an_inherited_host_address_runs_the_generated_config_and_records_the_facts(tmp_path, monkeypatch):
    import subprocess as sp
    sock, conf = tmp_path / "bus.sock", tmp_path / "bus.conf"
    monkeypatch.setattr(SI, "SYSTEM_BUS_SOCKET", str(sock))
    monkeypatch.setattr(SI, "SYSTEM_BUS_CONF", str(conf))
    monkeypatch.setattr(SI, "read_text", lambda p: MI_OK if p == "/proc/self/mountinfo" else SYSTEM_CONF)
    monkeypatch.setattr(os, "makedirs", lambda *a, **k: None)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, dict(kw["env"])))
        sock.write_text("")
        return sp.CompletedProcess(cmd, 0, stdout="4242\n")
    monkeypatch.setattr(SI.subprocess, "run", fake_run)
    SI.SYSTEM_BUS.clear()
    env = {"DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/var/run/dbus/system_bus_socket", "X": "1"}
    SI.start_system_bus(env, None)
    (cmd, seen), = calls
    assert "DBUS_SYSTEM_BUS_ADDRESS" not in seen and cmd[:2] == ["dbus-daemon", "--system"] and f"--config-file={conf}" in cmd  # the host's address was dropped BEFORE the daemon ran
    assert env["DBUS_SYSTEM_BUS_ADDRESS"] == f"unix:path={sock}" and "servicedirs" not in conf.read_text()
    assert SI.SYSTEM_BUS["pid"] == 4242 and SI.SYSTEM_BUS["socket_inode"] == os.stat(sock).st_ino and SI.SYSTEM_BUS["mnt_ns"].startswith("mnt:") and "none" in SI.SYSTEM_BUS["activation"] and len(SI.SYSTEM_BUS["conf_sha256"]) == 64
    SI.SYSTEM_BUS.clear()


def test_the_identity_mark_lists_what_the_bus_launched_and_the_init_refuses_a_bus_outside_full_root():
    import inspect
    assert "launched_children" in inspect.getsource(SI.record_identity)
    assert 'a.system_bus and a.root != "full"' in inspect.getsource(SI.main)
