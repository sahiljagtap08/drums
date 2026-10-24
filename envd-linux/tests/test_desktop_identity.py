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
    assert any(pid == os.getpid() and uid == os.geteuid() for pid, uid, _ in t)


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
