"""The pre-mount userland check for a custom base root, on synthetic trees. No mounts."""
import os
import shutil

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
    for p in (*programs, "umount", "Xvfb", "xkbcomp"):  # the post-pivot `umount`, the X server and its keymap compiler are always required
        mk(r, f"/usr/bin/{p}")
    mk(r, "/usr/share/X11/xkb/rules/evdev", "x")
    if with_py312:
        mk(r, "/usr/bin/python3.12")
    return r


def gnome_files(r, uid=1000, gid=1000, user="user", home="/home/user"):
    """The finite list of files the GNOME chain check names (synthetic stand-ins)."""
    for f in ("/usr/libexec/gnome-session-binary",):
        mk(r, f)
    for f in ("/usr/share/gnome-session/sessions/ubuntu.session", "/usr/share/glib-2.0/schemas/gschemas.compiled", "/usr/share/gnome-shell/x"):
        mk(r, f, "x", 0o644)
    mk(r, "/usr/share/dbus-1/system.conf", "<busconfig/>", 0o644)
    mk(r, "/etc/passwd", f"root:x:0:0:root:/root:/bin/bash\nmessagebus:x:103:106::/nonexistent:/usr/sbin/nologin\n{user}:x:{uid}:{gid}:U:{home}:/bin/bash\n", 0o644)


def kit_layer(tmp_path, python_target="/usr/bin/python3.12", with_server=True, venv_version="3.12.3"):
    t = str(tmp_path / "tpl")
    os.makedirs(t, exist_ok=True)
    if with_server:
        mk(t, "/opt/drums-osworld-real/server/main.py", "x = 1\n")
    mk(t, "/opt/drums-osworld-real/venv/bin/python", link=python_target)
    if venv_version:
        mk(t, "/opt/drums-osworld-real/venv/pyvenv.cfg", f"home = /usr/bin\nversion = {venv_version}\n")
        os.makedirs(os.path.join(t, f"opt/drums-osworld-real/venv/lib/python{'.'.join(venv_version.split('.')[:2])}/site-packages"), exist_ok=True)
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


def test_a_312_kit_is_refused_in_a_310_guest_and_a_matching_kit_passes_with_no_mixing(tmp_path):
    r = image_root(tmp_path, programs=("dbus-daemon", "wmctrl", "openbox"))
    # the kit's interpreter link points at the root's own python3.10 but the venv was built for 3.12: its 3.12 site-packages must not be loaded by a 3.10 interpreter
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.12.3")
    out = rc.check_userland([t, r], kit=KIT)
    assert any("built for python 3.12" in p and "(3.10)" in p and "never mixed" in p for p in out["problems"]), out
    t2 = str(tmp_path / "tpl2")
    os.makedirs(t2)
    mk(t2, "/opt/drums-osworld-real/server/main.py", "x = 1\n")
    mk(t2, "/opt/drums-osworld-real/venv/bin/python", link="/usr/bin/python3.10")
    mk(t2, "/opt/drums-osworld-real/venv/pyvenv.cfg", "version = 3.10.12\n")
    os.makedirs(os.path.join(t2, "opt/drums-osworld-real/venv/lib/python3.10/site-packages"))
    assert rc.check_userland([t2, r], kit=KIT)["problems"] == []
    shutil.rmtree(os.path.join(t2, "opt/drums-osworld-real/venv/lib"))
    assert any("site-packages" in p for p in rc.check_userland([t2, r], kit=KIT)["problems"])
    os.remove(os.path.join(t2, "opt/drums-osworld-real/venv/pyvenv.cfg"))
    assert any("pyvenv.cfg" in p for p in rc.check_userland([t2, r], kit=KIT)["problems"])


def test_a_refused_construction_creates_no_state_directory(tmp_path):
    r = image_root(tmp_path, programs=("dbus-daemon", "wmctrl", "openbox"))
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.12.3")
    with pytest.raises(ValueError, match="built for python 3.12"):
        S.Supervisor(str(tmp_path / "state3"), t, root_mode="full", base_root=r, osworld_real_kit=KIT, sweep_on_start=False)
    assert not os.path.exists(tmp_path / "state3")


def test_the_gnome_desktop_adds_its_own_programs_and_openbox_stays_the_default(tmp_path):
    assert "openbox" in rc.desktop_programs() and "gnome-session" not in rc.desktop_programs()
    g = rc.desktop_programs(desktop="gnome")
    assert {"gnome-session", "gnome-shell", "xprop", "dbus-daemon"} <= set(g) and "openbox" not in g
    with pytest.raises(ValueError, match="unknown desktop"):
        rc.desktop_programs(desktop="kde")
    r = image_root(tmp_path, programs=("dbus-daemon", "wmctrl"))  # a root with GNOME session programs and no openbox
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    probs = rc.check_userland([t, r], kit=KIT, desktop="gnome")["problems"]
    assert any("gnome-session" in p for p in probs) and not any("openbox" in p for p in probs)


def test_the_post_pivot_programs_are_read_from_setup_full_root_and_xvfb_is_always_required(tmp_path):
    assert rc.post_pivot_setup_programs() == ["umount"]  # only the final `umount -l /.drums-old` runs after the pivot; the mounts before it ran from the host
    assert rc.post_pivot_setup_programs("def setup_full_root(a):\n    sh('mount', 'x')\n    sh('pivot_root', 'a', 'b')\n    sh('umount', '-l', 'c')\n    sh('tool')\n") == ["tool", "umount"]
    r = image_root(tmp_path, programs=("dbus-daemon", "openbox"))
    os.remove(os.path.join(r, "usr/bin/Xvfb"))
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    probs = rc.check_userland([t, r], kit=KIT)["problems"]
    assert any(p.startswith("Xvfb is not an executable") and "image has none" in p for p in probs), probs
    os.remove(os.path.join(r, "usr/bin/umount"))
    assert any(p.startswith("umount is not an executable") for p in rc.check_userland([t, r], kit=KIT)["problems"])
    assert any(p.startswith("/opt/xvfb is not an executable") for p in rc.check_userland([t, r], kit=KIT, xvfb="/opt/xvfb")["problems"])  # a dynamic --xvfb argument is checked too


def test_the_shared_library_closure_is_audited_inside_the_union_not_the_host(tmp_path):
    import shutil
    r = image_root(tmp_path, programs=("dbus-daemon", "openbox"))
    shutil.copy("/usr/bin/true", os.path.join(r, "usr/bin/Xvfb"))  # a real dynamically linked ELF with no libraries around it
    need = rc.elf_needed("/usr/bin/true")
    assert "libc.so.6" in need
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    probs = rc.check_userland([t, r], kit=KIT)["problems"]
    assert any(p.startswith("Xvfb needs shared libraries") and "libc.so.6" in p for p in probs), probs
    mk(r, "/usr/lib/x86_64-linux-gnu/libc.so.6")  # a (non-ELF) stand-in satisfies the soname through the usr-merge symlinks
    assert not any(p.startswith("Xvfb needs") for p in rc.check_userland([t, r], kit=KIT)["problems"])
    assert rc.elf_needed(os.path.join(r, "usr/bin/python3.10")) == []  # a text file is not an ELF: never executed, never guessed
    mk(r, "/etc/ld.so.conf.d/x.conf", "/opt/extra/lib\n")
    os.remove(os.path.join(r, "usr/lib/x86_64-linux-gnu/libc.so.6"))
    mk(r, "/opt/extra/lib/libc.so.6")
    assert not any(p.startswith("Xvfb needs") for p in rc.check_userland([t, r], kit=KIT)["problems"])  # ld.so.conf.d directories are searched


def test_every_lazy_import_on_the_init_path_is_preloaded_before_the_pivot():
    import ast
    import inspect
    from envd_linux import session_init as SI
    tree = ast.parse(inspect.getsource(SI))
    lazy = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name in ("main", "http_ok", "lo_up", "start_real_desktop", "start_server", "record_identity", "process_table"):
            for n in ast.walk(fn):
                if isinstance(n, ast.Import):
                    lazy |= {a.name for a in n.names}
    allowed_elsewhere = {"ctypes", "main"}  # ctypes is also preloaded; `main` is the OSWorld server module of the fork arm (its own kit path, refused with a uid)
    assert lazy - set(SI.PRELOAD) - allowed_elsewhere == set(), lazy - set(SI.PRELOAD)
    SI.preload()


def test_script_interpreters_and_the_elf_interpreter_are_checked_not_just_the_global_loader(tmp_path):
    r = image_root(tmp_path, programs=("dbus-daemon", "openbox"))
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    mk(r, "/usr/bin/xvfb-run-like", "#!/usr/bin/nope\n")
    assert any("interpreter /usr/bin/nope" in p for p in rc.interpreter_problems("x", "/usr/bin/xvfb-run-like", [r]))
    mk(r, "/usr/bin/viaenv", "#!/usr/bin/env missingtool\n")
    mk(r, "/usr/bin/env")
    assert any("`missingtool` is not in the new root" in p for p in rc.interpreter_problems("x", "/usr/bin/viaenv", [r]))
    mk(r, "/usr/bin/missingtool")
    mk(r, "/usr/bin/shok", "#!/usr/bin/env missingtool\n")
    assert rc.interpreter_problems("x", "/usr/bin/shok", [r]) == []
    assert rc.shebang(os.path.join(r, "usr/bin/dbus-daemon")) is None
    import shutil
    shutil.copy("/usr/bin/true", os.path.join(r, "usr/bin/Xvfb"))
    assert rc.elf_interp("/usr/bin/true").startswith("/lib")
    mk(r, "/usr/lib/x86_64-linux-gnu/libc.so.6")
    os.remove(os.path.join(r, "usr/lib64/ld-linux-x86-64.so.2"))
    probs = rc.check_userland([t, r], kit=KIT)["problems"]
    assert any("Xvfb's ELF interpreter /lib64/ld-linux-x86-64.so.2 is not in the new root" in p for p in probs), probs


def test_xvfb_run_time_files_and_dlopen_names_are_named_and_limits_are_stated(tmp_path):
    r = image_root(tmp_path, programs=("dbus-daemon", "openbox"))
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    out = rc.check_userland([t, r], kit=KIT)
    assert out["problems"] == [] and any("may dlopen libGL.so.1" in w for w in out["warnings"]) and any("nothing here proves" in l for l in out["limits"])
    os.remove(os.path.join(r, "usr/bin/xkbcomp"))
    os.remove(os.path.join(r, "usr/share/X11/xkb/rules/evdev"))
    probs = rc.check_userland([t, r], kit=KIT)["problems"]
    assert any(p.startswith("xkbcomp is missing") for p in probs) and any("rules/evdev is missing" in p for p in probs)


def test_the_gnome_chain_checks_a_finite_named_list_and_reports_the_unknowns_as_warnings(tmp_path):
    r = image_root(tmp_path, programs=("dbus-daemon", "gnome-session", "gnome-shell", "xprop"))
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    kw = dict(kit=KIT, desktop="gnome", uid=1000, gid=1000, user="user", home="/home/user")
    probs = rc.check_userland([t, r], **kw)["problems"]
    for need in ("/usr/share/gnome-shell", "gschemas.compiled", "gnome-session-binary", "ubuntu.session", "'user' is not in the new root's /etc/passwd"):
        assert any(need in p for p in probs), (need, probs)
    gnome_files(r)
    out = rc.check_userland([t, r], **kw)
    assert out["problems"] == [] and any("private system bus" in w for w in out["warnings"]) and any("XDG_RUNTIME_DIR=/run/user/1000" in w for w in out["warnings"])
    assert any("uid 1000 gid 1000, not 1001/1001" in p for p in rc.check_userland([t, r], **dict(kw, uid=1001, gid=1001))["problems"])
    assert any("home as /home/user, not /home/other" in p for p in rc.check_userland([t, r], **dict(kw, home="/home/other"))["problems"])


def _build(tmp_path, name, src, *flags):
    import subprocess
    c = tmp_path / f"{name}.c"
    c.write_text(src)
    out = tmp_path / name
    subprocess.run(["gcc", str(c), *flags, "-o", str(out)], check=True, capture_output=True)
    return str(out)


def test_runpath_rpath_and_origin_are_honoured_per_binary_not_from_global_dirs(tmp_path):
    import shutil
    import subprocess
    lib = tmp_path / "libfoo.so"
    (tmp_path / "foo.c").write_text("int foo(void){return 1;}\n")
    subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libfoo.so.1", "-o", str(lib), str(tmp_path / "foo.c")], check=True, capture_output=True)
    main_src = "int foo(void); int main(void){return foo();}\n"
    for flag, kind in (("-Wl,--enable-new-dtags,-rpath,/usr/lib/gnome-shell", "runpath"), ("-Wl,--disable-new-dtags,-rpath,/usr/lib/gnome-shell", "rpath"), ("-Wl,--enable-new-dtags,-rpath,$ORIGIN/../lib/priv", "origin")):
        prog = _build(tmp_path, f"p_{kind}", main_src, str(lib), flag, "-Wl,-rpath-link," + str(tmp_path))
        needed, runp, rp = rc.elf_dynamic(prog)
        assert "libfoo.so.1" in needed and (runp if kind != "rpath" else rp)
        r = str(tmp_path / f"root_{kind}")
        os.makedirs(os.path.join(r, "usr/bin"))
        shutil.copy(prog, os.path.join(r, "usr/bin/prog"))
        mk(r, "/usr/lib/x86_64-linux-gnu/libc.so.6")  # satisfies libc; libfoo is NOT in any default directory
        assert rc.missing_libraries(os.path.join(r, "usr/bin/prog"), [r], vpath="/usr/bin/prog") == ["libfoo.so.1"], kind
        where = "/usr/lib/gnome-shell" if kind != "origin" else "/usr/lib/priv"
        mk(r, f"{where}/libfoo.so.1")
        assert rc.missing_libraries(os.path.join(r, "usr/bin/prog"), [r], vpath="/usr/bin/prog") == [], kind  # found through ITS OWN search path


def test_the_gnome_session_name_is_read_from_the_command_and_matches_the_images_session_file(tmp_path):
    from envd_linux import session_init as SI
    assert rc.desktop_session_name() == "ubuntu" and "--session=ubuntu" in SI.WM_COMMANDS["gnome"] and "ubuntu-xorg" not in " ".join(SI.WM_COMMANDS["gnome"])
    assert rc.desktop_session_name("WM_COMMANDS = {'gnome': ['gnome-session', '--session=other']}\n") == "other"
    r = image_root(tmp_path, programs=("dbus-daemon", "gnome-session", "gnome-shell", "xprop"))
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    gnome_files(r)
    os.remove(os.path.join(r, "usr/share/gnome-session/sessions/ubuntu.session"))
    mk(r, "/usr/share/gnome-session/sessions/ubuntu-xorg.session", "x", 0o644)  # the name the old code wrongly assumed
    probs = rc.check_userland([t, r], kit=KIT, desktop="gnome", uid=1000, gid=1000, user="user", home="/home/user")["problems"]
    assert any("sessions/ubuntu.session is missing" in p and "--session=ubuntu" in p for p in probs)


def test_desktop_children_keep_their_stderr_in_tmp_not_devnull():
    import inspect
    from envd_linux import session_init as SI
    src = inspect.getsource(SI.start_real_desktop)
    assert "subprocess.DEVNULL" not in src and "drums-desktop-" in src and 'kept("desktop")' in src and 'logfile("dbus")' in src and "logs=logs" in inspect.getsource(SI.record_identity)


def test_the_private_system_bus_is_required_only_when_asked_and_names_what_it_needs(tmp_path):
    r = image_root(tmp_path, programs=("dbus-daemon", "openbox"))
    t = kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    assert rc.check_userland([t, r], kit=KIT)["problems"] == []  # default: unchanged
    probs = rc.check_userland([t, r], kit=KIT, system_bus=True)["problems"]
    assert any("system.conf is missing" in p for p in probs) and any("`messagebus` is not in" in p for p in probs)
    mk(r, "/usr/share/dbus-1/system.conf", "x", 0o644)
    mk(r, "/etc/passwd", "messagebus:x:103:106::/nonexistent:/usr/sbin/nologin\n", 0o644)
    assert rc.check_userland([t, r], kit=KIT, system_bus=True)["problems"] == []


def test_the_system_bus_is_private_started_first_and_nothing_else_is_bound_from_the_host():
    import inspect
    from envd_linux import session_init as SI
    src = inspect.getsource(SI.start_real_desktop)
    assert src.index("start_system_bus(") < src.index('"--session"') < src.index('kept("desktop")')  # system bus, then session bus, then the desktop
    body = inspect.getsource(SI.start_system_bus)
    assert '"dbus-daemon", "--system"' in body and "/run/dbus" in body and "--bind" not in body  # no host path is bound in
    assert "--system-bus" in inspect.getsource(SI.main) and SI.SYSTEM_BUS_SOCKET == "/run/dbus/system_bus_socket"
