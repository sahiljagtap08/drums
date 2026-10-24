"""The pre-mount userland check for a custom base root, on synthetic trees. No mounts."""
import os

import pytest

from envd_linux import rootcheck as rc
from envd_linux import session as S


def mk(root, path, content="", mode=0o755, link=None):
    p = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if link:
        os.symlink(link, p)
    else:
        open(p, "w").write(content)
        os.chmod(p, mode)


def image_root(tmp_path, with_py312=False, programs=("dbus-daemon", "wmctrl"), usr_merge=True):
    """A 22.04-shaped root: usr-merge symlinks, python 3.10, the dynamic loader, dbus and at-spi; NO openbox, NO python 3.12 unless asked."""
    r = str(tmp_path / "image")
    os.makedirs(r)
    if usr_merge:
        for d in ("bin", "sbin", "lib", "lib64"):
            os.symlink("usr/" + d, os.path.join(r, d))
    mk(r, "/usr/lib64/ld-linux-x86-64.so.2")
    mk(r, "/usr/bin/python3.10")
    mk(r, "/usr/libexec/at-spi-bus-launcher")
    for p in programs:
        mk(r, f"/usr/bin/{p}")
    if with_py312:
        mk(r, "/usr/bin/python3.12")
    return r


def kit_layer(tmp_path, python_target="/usr/bin/python3.12", with_server=True):
    t = str(tmp_path / "tpl")
    os.makedirs(t, exist_ok=True)
    if with_server:
        mk(t, "/opt/drums-osworld-real/server/main.py", "x = 1\n")
    mk(t, "/opt/drums-osworld-real/venv/bin/python", link=python_target)
    return t


KIT = "/opt/drums-osworld-real"


def test_the_required_programs_are_read_from_start_real_desktop_not_guessed():
    progs = rc.desktop_programs()
    assert progs == ["/usr/libexec/at-spi-bus-launcher", "dbus-daemon", "openbox"]  # xclip and wmctrl are NOT launched there
    assert rc.desktop_programs("def start_real_desktop(a):\n    subprocess.Popen(['foo', '-x'])\n    subprocess.run(['bar'])\n") == ["bar", "foo"]
    with pytest.raises(ValueError):
        rc.desktop_programs("def other(): pass\n")


def test_server_programs_come_from_the_pinned_server_source():
    src = "import subprocess\ndef a():\n    subprocess.run(['wmctrl', '-l'], capture_output=True)\n\ndef b():\n    subprocess.Popen(['xdotool', 'x'])\n    subprocess.run(['/abs/prog'])\n"
    assert rc.server_programs(src) == ["wmctrl", "xdotool"]
    p = "/opt/drums-osworld-real/server/main.py"
    if os.path.exists(p):
        assert "wmctrl" in rc.server_programs(open(p).read())  # the real pinned server runs wmctrl


def test_symlinks_resolve_inside_the_union_and_never_out_to_the_host(tmp_path):
    r = image_root(tmp_path)
    assert rc.resolve("/bin/dbus-daemon", [r]) == (r, "/usr/bin/dbus-daemon")  # usr-merge symlink followed within the tree
    mk(r, "/usr/bin/escape", link="/etc/hostname")  # an absolute link resolves against the NEW root, where /etc/hostname does not exist
    assert rc.resolve("/usr/bin/escape", [r]) is None
    mk(r, "/usr/bin/loop", link="loop")
    assert rc.resolve("/usr/bin/loop", [r]) is None  # a loop is bounded, not followed forever
    assert rc.resolve("/usr/bin/nope", [r]) is None


def test_the_template_layer_wins_over_the_base_root(tmp_path):
    r, t = image_root(tmp_path), str(tmp_path / "tpl")
    mk(t, "/usr/bin/openbox")
    assert rc.find_program("openbox", [t, r]) == "/usr/bin/openbox" and rc.find_program("openbox", [r]) is None


def test_an_image_root_without_openbox_the_kit_or_python_312_is_refused_with_each_reason(tmp_path):
    r = image_root(tmp_path)
    found = rc.check_userland([r], kit=KIT)
    text = " | ".join(found["problems"])
    assert "openbox is not an executable" in text and "kit's server" in text and "kit's python" in text
    assert "dbus-daemon" not in text and "at-spi-bus-launcher" not in text and "dynamic loader" not in text  # present: not reported


def test_the_kit_in_the_template_layer_still_fails_when_its_python_symlink_points_at_a_python_the_root_lacks(tmp_path):
    r, t = image_root(tmp_path, programs=("dbus-daemon", "wmctrl", "openbox")), kit_layer(tmp_path)
    found = rc.check_userland([t, r], kit=KIT)
    assert len(found["problems"]) == 1 and "kit's python" in found["problems"][0] and "python3.12" in found["problems"][0]
    r2, t2 = image_root(tmp_path / "b", with_py312=True, programs=("dbus-daemon", "openbox")), kit_layer(tmp_path / "b")
    assert rc.check_userland([t2, r2], kit=KIT)["problems"] == []  # explicit, validated visibility: kit and a compatible interpreter are in the tree


def test_a_non_executable_interpreter_or_program_is_a_problem(tmp_path):
    r, t = image_root(tmp_path, with_py312=True, programs=("dbus-daemon", "openbox")), kit_layer(tmp_path)
    os.chmod(os.path.join(r, "usr/bin/python3.12"), 0o644)
    assert any("not executable" in x for x in rc.check_userland([t, r], kit=KIT)["problems"])
    os.chmod(os.path.join(r, "usr/bin/openbox"), 0o644)
    assert any("openbox is not an executable" in x for x in rc.check_userland([t, r], kit=KIT)["problems"])


def test_a_missing_loader_is_reported_and_per_task_programs_are_warnings_not_problems(tmp_path):
    r = image_root(tmp_path, programs=("dbus-daemon", "openbox"))
    os.remove(os.path.join(r, "usr/lib64/ld-linux-x86-64.so.2"))
    assert any("dynamic loader" in x for x in rc.check_userland([r])["problems"])
    mk(r, "/usr/lib64/ld-linux-x86-64.so.2")
    out = rc.check_userland([r], server_main_source="import subprocess\ndef f():\n    subprocess.run(['wmctrl', '-l'])\n")
    assert out["problems"] == [] and any("wmctrl" in w for w in out["warnings"])


def test_the_supervisor_refuses_before_any_mount_when_the_new_root_cannot_run_the_session(tmp_path):
    r = image_root(tmp_path)
    tpl = str(tmp_path / "tpl")
    os.makedirs(tpl)
    with pytest.raises(ValueError, match="cannot run the session.*openbox"):
        S.Supervisor(str(tmp_path / "state"), tpl, root_mode="full", base_root=r, osworld_real_kit=KIT, sweep_on_start=False)
    assert not os.path.exists(tmp_path / "state" / "tplfs")  # refused before the template mount
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.12")
    with pytest.raises(ValueError, match="kit's python"):
        S.Supervisor(str(tmp_path / "state2"), t, root_mode="full", base_root=r, osworld_real_kit=KIT, sweep_on_start=False)
