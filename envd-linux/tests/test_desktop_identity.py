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
