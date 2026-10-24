"""Image-base-root CRIU extension: identity recorded in the checkpoint, restores refused on any mismatch BEFORE a mount, the exact restore lowerdir, and the stage-2 exec audit. FAKES ONLY: no CRIU
has run on an image root, so nothing here shows that the restore works."""
import json
import os
import subprocess
import sys

import pytest

from envd_linux import baseid
from envd_linux import criu as cr
from envd_linux import rootcheck as rc
from envd_linux import session as S

sys.path.insert(0, os.path.dirname(__file__))
import test_rootcheck as T  # noqa: E402

SHA = "a" * 64
BASE = {"base_root": "/mnt/img", "fstype": "ext4", "read_only": True, "os_release_sha256": "o" * 64, "dpkg_status_sha256": "d" * 64}
CONF = {"desktop": "gnome", "uid": 1000, "gid": 1000, "user": "user", "home": "/home/user", "system_bus": True, "xvfb": "Xvfb"}


def ident(**kw):
    d = {"meta_version": baseid.META_VERSION, "base_root": "/mnt/img", "image_sha256": SHA, "base": dict(BASE), "template_sha256": "t" * 64, "conf": dict(CONF)}
    d.update(kw)
    return d


def test_a_matching_checkpoint_is_accepted_and_each_difference_is_a_named_refusal():
    assert baseid.check_restore(ident(), ident()) == []
    cases = [(dict(base_root="/other"), "base root is"), (dict(image_sha256="b" * 64), "image sha256 differs"), (dict(image_sha256=None), "image sha256 differs"),
             (dict(base=dict(BASE, os_release_sha256="x" * 64)), "os_release_sha256"), (dict(base=dict(BASE, dpkg_status_sha256=None)), "dpkg_status_sha256"),
             (dict(base=dict(BASE, fstype="xfs")), "fstype"), (dict(template_sha256="z" * 64), "template layer"), (dict(template_sha256=None), "template layer"),
             (dict(conf=dict(CONF, uid=1001)), "configuration differs"), (dict(conf=dict(CONF, system_bus=False)), "configuration differs"), (dict(meta_version=3), "unknown checkpoint meta_version"),
             (dict(meta_version=None), "legacy")]
    for change, why in cases:
        now = ident()
        meta = ident(**change)
        problems = baseid.check_restore(meta, now)
        assert problems and any(why in p for p in problems), (change, problems)
    assert any("not mounted read-only" in p for p in baseid.check_restore(ident(), ident(base=dict(BASE, read_only=False))))


def test_a_legacy_checkpoint_is_accepted_only_on_the_host_root():
    host = ident(base_root="/", image_sha256=None, base=None, template_sha256=None)
    legacy = {"lower": "/t", "layers": ["/l"]}  # no meta_version, no base_root
    assert baseid.check_restore(legacy, host) == []
    assert any("legacy" in p for p in baseid.check_restore(legacy, ident()))  # never restored under an image base root


def test_the_restore_lowerdir_is_exact_and_never_silently_the_host_root_for_an_image():
    assert baseid.restore_lowerdir(["/c/layer/upper", "/o/upper"], "/tpl", "/mnt/img") == "/c/layer/upper:/o/upper:/tpl:/mnt/img"
    assert baseid.restore_lowerdir(["/c/layer/upper"], "/tpl", "/") == "/c/layer/upper:/tpl:/"  # the legacy string, unchanged
    assert baseid.restore_lowerdir(["/c"], "", "/mnt/img") == "/c:/mnt/img"


def test_measuring_a_base_reads_the_mount_and_the_two_identity_files(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/os-release").write_text("PRETTY_NAME=x\n")
    (tmp_path / "var/lib/dpkg").mkdir(parents=True)
    (tmp_path / "var/lib/dpkg/status").write_text("Package: a\n")
    m = baseid.measure_base(str(tmp_path), runner=lambda cmd: "ext4 ro,noload,relatime\n")
    assert m["fstype"] == "ext4" and m["read_only"] is True and m["os_release_sha256"] == baseid.sha256_file(str(tmp_path / "etc/os-release")) and m["dpkg_status_sha256"]
    assert baseid.measure_base(str(tmp_path), runner=lambda cmd: "ext4 rw,relatime\n")["read_only"] is False
    assert baseid.measure_base(str(tmp_path / "nowhere"), runner=lambda cmd: "ext4 ro\n")["os_release_sha256"] is None  # unknown stays unknown, never a hash of nothing


def test_the_template_listing_hash_changes_with_content_size_and_links(tmp_path):
    (tmp_path / "a").write_text("1")
    h1 = baseid.tree_listing_sha(str(tmp_path))
    (tmp_path / "a").write_text("12")
    h2 = baseid.tree_listing_sha(str(tmp_path))
    os.symlink("a", tmp_path / "l")
    h3 = baseid.tree_listing_sha(str(tmp_path))
    assert len({h1, h2, h3}) == 3 and baseid.tree_listing_sha(str(tmp_path / "x")) is None


def image_world(tmp_path, with_pkg=True):
    r = T.image_root(tmp_path, programs=("dbus-daemon", "openbox"))
    os.symlink("python3.10", os.path.join(r, "usr/bin/python3"))
    t = T.kit_layer(tmp_path, python_target="/usr/bin/python3.10", venv_version="3.10.12")
    if with_pkg:
        for m in rc.STAGE2_MODULES:
            dst = os.path.join(t, S.PKG_DIR.lstrip("/"), "envd_linux", m)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, "w").write(open(os.path.join(S.PKG_DIR, "envd_linux", m)).read())
    return r, t


def test_the_stage2_exec_needs_the_package_the_interpreter_and_modules_that_parse_under_the_roots_python(tmp_path):
    r, t = image_world(tmp_path, with_pkg=False)
    probs = rc.check_userland([t, r], kit=T.KIT)["problems"]
    assert probs == []  # not asked for: the cold path is unchanged
    probs = rc.check_userland([t, r], kit=T.KIT, criu=True, pkg_dir=S.PKG_DIR)["problems"]
    assert any("session_init.py is not visible in the new root" in p and "stage-2" in p for p in probs)
    r2, t2 = image_world(tmp_path / "b")
    assert rc.check_userland([t2, r2], kit=T.KIT, criu=True, pkg_dir=S.PKG_DIR)["problems"] == []
    assert any("stage-2 interpreter" in p for p in rc.check_userland([t2, r2], kit=T.KIT, criu=True, pkg_dir=S.PKG_DIR, init_python="/opt/nope/python")["problems"])
    bad = os.path.join(t2, S.PKG_DIR.lstrip("/"), "envd_linux", "session_init.py")
    open(bad, "w").write("try:\n    pass\nexcept* ValueError:\n    pass\n")  # `except*` needs 3.11
    assert any("does not parse under the new root's python 3.10" in p for p in rc.check_userland([t2, r2], kit=T.KIT, criu=True, pkg_dir=S.PKG_DIR)["problems"])


def test_the_init_path_modules_parse_under_python_310_today():
    import ast
    for m in rc.STAGE2_MODULES:
        ast.parse(open(os.path.join(S.PKG_DIR, "envd_linux", m)).read(), feature_version=(3, 10))


def make_sup(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(S.Supervisor, "_mount_template", lambda self: None)
    r, t = image_world(tmp_path)
    kw.setdefault("sweep_on_start", False)
    return S.Supervisor(str(tmp_path / "state"), t, root_mode="full", netns=True, base_root=r, osworld_real_kit=T.KIT, criu_friendly=True, image_sha256=SHA, **kw), r, t


def test_the_guard_needs_the_pinned_image_hash_and_a_runnable_stage2_for_criu_on_an_image_root(tmp_path, monkeypatch):
    sup, r, t = make_sup(tmp_path, monkeypatch)
    assert sup.base_root == r and sup.criu_friendly and sup.image_sha256 == SHA
    monkeypatch.setattr(S.Supervisor, "_mount_template", lambda self: None)
    with pytest.raises(ValueError, match="needs image_sha256"):
        S.Supervisor(str(tmp_path / "s2"), t, root_mode="full", netns=True, base_root=r, osworld_real_kit=T.KIT, criu_friendly=True, sweep_on_start=False)
    r3, t3 = image_world(tmp_path / "c", with_pkg=False)
    with pytest.raises(ValueError, match="stage-2"):
        S.Supervisor(str(tmp_path / "s3"), t3, root_mode="full", netns=True, base_root=r3, osworld_real_kit=T.KIT, criu_friendly=True, image_sha256=SHA, sweep_on_start=False)
    assert not os.path.exists(tmp_path / "s3")


def checkpoint_dir(tmp_path, sup, **meta_kw):
    ck = cr.Checkpoint(str(tmp_path / "ck"))
    os.makedirs(ck.img, exist_ok=True)
    cur = baseid.current_identity(sup, measure=lambda root: dict(BASE, base_root=root))
    meta = dict(cur, layers=[str(tmp_path / "ck/layer/upper")], lower=sup.lower, display=190, inner_if="dvs0", clean=False, has_browser=False)
    meta.update(meta_kw)
    ck.write_meta(**meta)
    return ck


def patch_world(monkeypatch, sup, calls, restore_rc=0):
    monkeypatch.setattr(baseid, "measure_base", lambda root, runner=None: dict(BASE, base_root=root))
    monkeypatch.setattr(baseid, "current_identity", lambda s, measure=None: _ident_of(s))
    monkeypatch.setattr(S.subprocess, "run", lambda cmd, **k: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(cr, "restore", lambda *a, **k: {"rc": restore_rc, "seconds": 0.1, "log": ["Error: fake"]})


def _ident_of(s):
    custom = s.base_root != "/"
    return {"meta_version": baseid.META_VERSION, "base_root": s.base_root, "image_sha256": s.image_sha256, "base": dict(BASE, base_root=s.base_root) if custom else None,
            "template_sha256": baseid.tree_listing_sha(s.lower) if custom else None, "conf": baseid.conf_of(s)}


def test_restore_mounts_the_overlay_with_the_exact_image_lowerdir_and_cleans_up_when_criu_fails(tmp_path, monkeypatch):
    sup, r, t = make_sup(tmp_path, monkeypatch, display_base=190)
    ck = checkpoint_dir(tmp_path, sup)
    calls = []
    patch_world(monkeypatch, sup, calls, restore_rc=1)
    with pytest.raises(S.SessionError, match="criu restore failed"):
        sup._restore(0, ck)
    overlay = [c for c in calls if c[:3] == ["mount", "-t", "overlay"]]
    assert len(overlay) == 1
    opts = overlay[0][4 + 1]
    assert opts.startswith(f"lowerdir={tmp_path}/ck/layer/upper:{t}:{r},upperdir=") and "redirect_dir=on" in opts  # the checkpoint layer, the template, the IMAGE root: not '/'
    assert ":/," not in opts and not opts.split("lowerdir=")[1].split(",")[0].endswith(":/")
    umounts = [c for c in calls if c[0] == "umount"]
    assert [os.path.basename(c[1]) for c in umounts] == ["root", "stage"]  # the restore root first, then the stage tmpfs
    assert not os.path.lexists("/tmp/.X11-unix/X190")  # the X11 link made for the restore is removed again


def test_a_mismatching_checkpoint_is_refused_before_any_directory_mount_link_or_process(tmp_path, monkeypatch):
    sup, r, t = make_sup(tmp_path, monkeypatch, display_base=191)
    for change, why in ((dict(image_sha256="b" * 64), "image sha256"), (dict(meta_version=9), "unknown checkpoint meta_version"), (dict(base_root="/"), "base root"),
                        (dict(conf=dict(CONF, uid=5)), "configuration differs")):
        ck = checkpoint_dir(tmp_path, sup, **change)
        calls = []
        patch_world(monkeypatch, sup, calls)
        with pytest.raises(S.SessionError, match=why):
            sup._restore(0, ck)
        assert calls == [] and not os.path.exists(sup._dir(0)) and not os.path.lexists("/tmp/.X11-unix/X191")
    ck = checkpoint_dir(tmp_path, sup)
    legacy = ck.meta()
    for k in ("meta_version", "base_root", "image_sha256", "base", "template_sha256", "conf"):
        legacy.pop(k)
    ck.write_meta(**legacy)
    calls = []
    patch_world(monkeypatch, sup, calls)
    with pytest.raises(S.SessionError, match="legacy"):
        sup._restore(0, ck)  # a legacy checkpoint is never restored under an image root
    assert calls == []
