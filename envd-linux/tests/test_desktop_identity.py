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
    assert "<type>system</type>" in out and '<policy context="default">' in out and "<listen>unix:path=/run/dbus/system_bus_socket</listen>" in out and "/var/run/dbus" not in out
    with pytest.raises(SystemExit, match="not a system bus"):
        SI.private_system_conf("<busconfig><type>session</type></busconfig>")


def test_includes_are_inlined_and_checked_so_an_included_file_cannot_bring_launching_back(tmp_path):
    d = tmp_path / "system.d"
    d.mkdir()
    (d / "evil.conf").write_text("<busconfig><servicedir>/x</servicedir><servicehelper>/h</servicehelper><policy user='root'><allow own='*'/></policy></busconfig>")
    (tmp_path / "local.conf").write_text("<busconfig><standard_system_servicedirs/><limit name='max_incoming_bytes'>1</limit></busconfig>")
    main = f"<busconfig><type>system</type><include>{tmp_path}/local.conf</include><includedir>{d}</includedir></busconfig>"
    out = SI.private_system_conf(main, str(tmp_path))
    assert "servicedir" not in out and "servicehelper" not in out and "<include" not in out and "policy user=" in out and "max_incoming_bytes" in out  # policy and limits survive, launching does not
    for bad, why in (("<busconfig><type>system</type><bogus/></busconfig>", "does not understand: <bogus>"), (f"<busconfig><type>system</type><include>{tmp_path}/missing.conf</include></busconfig>", "does not exist")):
        with pytest.raises(SystemExit, match=why):
            SI.private_system_conf(bad, str(tmp_path))
    (tmp_path / "wrong.conf").write_text("<other/>")
    with pytest.raises(SystemExit, match="not a busconfig"):
        SI.private_system_conf(f"<busconfig><type>system</type><include>{tmp_path}/wrong.conf</include></busconfig>", str(tmp_path))


def test_system_and_config_file_are_never_combined():
    cmd = SI.system_bus_command("/run/x.conf")
    assert "--system" not in cmd and "--config-file=/run/x.conf" in cmd and "--fork" in cmd and "--print-pid=1" in cmd


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
    assert "DBUS_SYSTEM_BUS_ADDRESS" not in seen and cmd[0] == "dbus-daemon" and "--system" not in cmd and f"--config-file={conf}" in cmd  # the host's address was dropped BEFORE the daemon ran
    assert env["DBUS_SYSTEM_BUS_ADDRESS"] == f"unix:path={sock}" and "servicedirs" not in conf.read_text()
    assert SI.SYSTEM_BUS["pid"] == 4242 and SI.SYSTEM_BUS["socket_inode"] == os.stat(sock).st_ino and SI.SYSTEM_BUS["mnt_ns"].startswith("mnt:") and "none" in SI.SYSTEM_BUS["activation"] and len(SI.SYSTEM_BUS["conf_sha256"]) == 64
    SI.SYSTEM_BUS.clear()


def test_the_identity_mark_lists_what_the_bus_launched_and_the_init_refuses_a_bus_outside_full_root():
    import inspect
    assert "launched_children" in inspect.getsource(SI.record_identity)
    assert 'a.system_bus and a.root != "full"' in inspect.getsource(SI.main)


def test_two_REAL_unprivileged_daemons_from_the_generated_config_have_distinct_ids_no_activation_and_leave_the_host_socket_alone(tmp_path):
    import shutil
    import signal
    import subprocess as sp
    if not (shutil.which("dbus-daemon") and shutil.which("dbus-send")):
        pytest.skip("dbus-daemon/dbus-send are not installed")
    host = "/run/dbus/system_bus_socket"
    before = (os.stat(host).st_ino, os.stat(host).st_mtime_ns) if os.path.exists(host) else None
    svc = tmp_path / "services"
    svc.mkdir()
    (svc / "org.example.Launch.service").write_text("[D-BUS Service]\nName=org.example.Launch\nExec=/bin/true\nUser=root\n")
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "x.conf").write_text(f"<busconfig><servicedir>{svc}</servicedir><policy context='default'><allow user='*'/><allow send_destination='*'/><allow receive_sender='*'/><allow own='*'/></policy></busconfig>")
    base = f"<busconfig><type>system</type><auth>EXTERNAL</auth><standard_system_servicedirs/><servicedir>{svc}</servicedir><includedir>{inc}</includedir></busconfig>"
    pids, ids = [], []
    try:
        for n in (1, 2):
            sock = tmp_path / f"bus{n}.sock"
            conf = tmp_path / f"bus{n}.conf"
            conf.write_text(SI.private_system_conf(base, str(tmp_path), str(sock), str(tmp_path / f"bus{n}.pid")))
            r = sp.run(SI.system_bus_command(str(conf)), capture_output=True, text=True, timeout=20)
            assert r.returncode == 0, r.stderr  # the REAL daemon accepts this command line (it rejects --system together with --config-file)
            toks = r.stdout.split()
            pids.append(next(int(t) for t in toks if t.isdigit()))
            printed = next(t for t in toks if t.startswith("unix:"))
            assert printed.split("=")[1].split(",")[0] == str(sock)
            addr = f"unix:path={sock}"
            gid = sp.run(["dbus-send", f"--bus={addr}", "--print-reply", "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.GetId"], capture_output=True, text=True, timeout=20)
            assert gid.returncode == 0, gid.stderr
            ids.append(gid.stdout.split('"')[1])
            act = sp.run(["dbus-send", f"--bus={addr}", "--print-reply", "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.ListActivatableNames"], capture_output=True, text=True, timeout=20).stdout
            assert "org.example.Launch" not in act  # the service file was stripped: nothing is activatable
            start = sp.run(["dbus-send", f"--bus={addr}", "--print-reply", "--dest=org.example.Launch", "/", "org.example.Launch.Ping"], capture_output=True, text=True, timeout=20)
            assert start.returncode != 0 and "ServiceUnknown" in start.stderr  # and asking for it does not launch it
        assert ids[0] != ids[1] and len(set(pids)) == 2
        after = (os.stat(host).st_ino, os.stat(host).st_mtime_ns) if os.path.exists(host) else None
        assert before == after  # the host's own system bus socket was not touched
    finally:
        for p in pids:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass


STOCK_LIKE = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>system</type>
  <fork/>
  <auth>EXTERNAL</auth>
  <listen>unix:path=/var/run/dbus/system_bus_socket</listen>
  <policy context="default"><allow user="*"/><deny own="*"/><deny send_type="method_call"/><allow send_destination="org.freedesktop.DBus" send_interface="org.freedesktop.DBus"/></policy>
  <policy user="root"><allow send_destination="*" eavesdrop="true"/></policy>
  <includedir>system.d</includedir>
  <include ignore_missing="yes">/etc/dbus-1/system-local.conf</include>
  <standard_system_servicedirs/>
  <servicehelper>/usr/lib/dbus-1.0/dbus-daemon-launch-helper</servicehelper>
  <selinux><associate own="org.freedesktop.DBus" context="x"/></selinux>
  <include if_selinux_enabled="yes" selinux_root_relative="yes">contexts/dbus_contexts</include>
</busconfig>
"""


def test_a_stock_shaped_config_with_relative_policy_dirs_and_the_conditional_selinux_include_is_transformed(tmp_path):
    (tmp_path / "system.d").mkdir()
    (tmp_path / "system.d" / "a.conf").write_text("<busconfig><policy user='messagebus'><allow own='org.a'/></policy></busconfig>")
    out = SI.private_system_conf(STOCK_LIKE, str(tmp_path))  # no contexts/dbus_contexts exists: the conditional SELinux include is dropped, not followed
    assert "messagebus" in out and "selinux" not in out and "servicehelper" not in out and "servicedirs" not in out and "<include" not in out and "/var/run/dbus" not in out
    assert out.count("<listen>") == 1 and '<policy context="default">' in out and "<fork" in out
    assert SI.private_system_conf(STOCK_LIKE.replace("<fork/>", "<fork/><pidfile>/run/dbus/pid</pidfile>"), str(tmp_path), "/s", "/p.pid").count("<pidfile>/p.pid</pidfile>") == 1 and "/run/dbus/pid" not in SI.private_system_conf(STOCK_LIKE.replace("<fork/>", "<fork/><pidfile>/run/dbus/pid</pidfile>"), str(tmp_path))
    with pytest.raises(SystemExit, match="attribute this policy does not understand"):
        SI.private_system_conf("<busconfig><type>system</type><include whatever='1'>x</include></busconfig>", str(tmp_path))
    with pytest.raises(SystemExit, match="selinux_root_relative without if_selinux_enabled"):
        SI.private_system_conf("<busconfig><type>system</type><include selinux_root_relative='yes'>x</include></busconfig>", str(tmp_path))


def test_the_actually_installed_stock_system_conf_transforms_without_effects():
    path = "/usr/share/dbus-1/system.conf"
    if not os.path.exists(path):
        pytest.skip("no stock system.conf on this machine")
    out = SI.private_system_conf(open(path).read(), "/usr/share/dbus-1")
    assert "<type>system</type>" in out and "servicehelper" not in out and "servicedir" not in out and "<include" not in out and out.count("<listen>") == 1


def test_the_host_validation_tool_proves_two_buses_on_this_machine(tmp_path):
    import shutil
    if not (shutil.which("dbus-daemon") and shutil.which("dbus-send") and os.path.exists("/usr/share/dbus-1/system.conf")):
        pytest.skip("dbus tools or stock system.conf missing")
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "compare"))
    from drums_compare import bus_validate as bv
    import json
    # the stock config's own policy forbids connecting as an ordinary client in some builds; the tool reports that as a problem rather than hiding it
    r = bv.run("/usr/share/dbus-1/system.conf")
    json.dumps(r)
    assert set(r) >= {"host_socket_before", "host_socket_after", "buses", "problems", "ok", "image_activatable_inventory"}
    assert r["host_socket_before"] == r["host_socket_after"]  # the host socket is never touched either way
    import time
    for _ in range(50):
        if not pgrep_left(r):
            break
        time.sleep(0.1)
    assert not pgrep_left(r)  # both daemons were killed


def pgrep_left(r):
    import subprocess
    return [b["pid"] for b in r["buses"] if os.path.exists(f"/proc/{b['pid']}") and "Z" not in open(f"/proc/{b['pid']}/stat").read().rsplit(")", 1)[1].split()[0]]
