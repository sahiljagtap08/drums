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


RECEIPT = {"verified": True, "computed_sha256": SHA, "image_path": "/opt/img/Ubuntu.qcow2", "image_size": 1000, "image_mtime_ns": 5, "nbd_device": "/dev/nbd0", "base_root": "/mnt/img"}


def ident(**kw):
    d = {"meta_version": baseid.META_VERSION, "base_root": "/mnt/img", "image_sha256": SHA, "image_receipt": dict(RECEIPT), "base": dict(BASE), "template_sha256": "t" * 64, "conf": dict(CONF)}
    d.update(kw)
    return d


def test_a_matching_checkpoint_is_accepted_and_each_difference_is_a_named_refusal():
    assert baseid.check_restore(ident(), ident()) == []
    cases = [(dict(base_root="/other"), "base root is"), (dict(image_sha256="b" * 64), "image sha256 differs"), (dict(image_sha256=None), "image sha256 differs"),
             (dict(base=dict(BASE, os_release_sha256="x" * 64)), "os_release_sha256"), (dict(base=dict(BASE, dpkg_status_sha256=None)), "dpkg_status_sha256"),
             (dict(base=dict(BASE, fstype="xfs")), "fstype"), (dict(template_sha256="z" * 64), "template layer"), (dict(template_sha256=None), "template layer"),
             (dict(conf=dict(CONF, uid=1001)), "configuration differs"), (dict(conf=dict(CONF, system_bus=False)), "configuration differs"), (dict(meta_version=3), "unknown checkpoint meta_version"),
             (dict(meta_version=None), "legacy"), (dict(image_receipt=dict(RECEIPT, verified=False)), "no VERIFIED image receipt"), (dict(image_receipt=None), "no VERIFIED image receipt"),
             (dict(image_receipt=dict(RECEIPT, computed_sha256="c" * 64)), "no VERIFIED image receipt"), (dict(image_receipt=dict(RECEIPT, image_path="/other.qcow2")), "image_path differs"),
             (dict(image_receipt=dict(RECEIPT, image_mtime_ns=6)), "image_mtime_ns differs")]
    for change, why in cases:
        now = ident()
        meta = ident(**change)
        problems = baseid.check_restore(meta, now)
        assert problems and any(why in p for p in problems), (change, problems)
    assert any("not mounted read-only" in p for p in baseid.check_restore(ident(), ident(base=dict(BASE, read_only=False))))


def test_the_template_identity_covers_file_bytes_so_a_same_length_change_is_seen(tmp_path):
    (tmp_path / "kit").mkdir()
    (tmp_path / "kit" / "main.py").write_text("print('a')\n")
    h1 = baseid.tree_listing_sha(str(tmp_path))
    (tmp_path / "kit" / "main.py").write_text("print('b')\n")  # the same length
    h2 = baseid.tree_listing_sha(str(tmp_path))
    os.chmod(tmp_path / "kit" / "main.py", 0o700)
    h3 = baseid.tree_listing_sha(str(tmp_path))
    os.symlink("main.py", tmp_path / "kit" / "ln")
    h4 = baseid.tree_listing_sha(str(tmp_path))
    os.remove(tmp_path / "kit" / "ln")
    os.symlink("other.py", tmp_path / "kit" / "ln")
    h5 = baseid.tree_listing_sha(str(tmp_path))
    assert len({h1, h2, h3, h4, h5}) == 5


def test_measuring_requires_the_exact_mount_point_and_never_describes_an_ancestor(tmp_path):
    base = str(tmp_path)
    ok = lambda cmd: f"{base} ext4 ro,noload /dev/nbd0p3\n"
    assert baseid.measure_base(base, runner=ok)["source"] == "/dev/nbd0p3"
    assert "--mountpoint" in (lambda seen=[]: (baseid.measure_base(base, runner=lambda c: seen.append(c) or ok(c)), seen[0])[1])()  # `--target` would resolve an ancestor's mount
    anc = lambda cmd: "/ ext4 rw /dev/sda1\n"  # what --target answers when nothing is mounted there
    with pytest.raises(baseid.BaseIdentityError, match="not exactly a mount point"):
        baseid.measure_base(base, runner=anc)

    def missing(cmd):
        raise subprocess.CalledProcessError(1, cmd)
    with pytest.raises(baseid.BaseIdentityError, match="not a mount point"):
        baseid.measure_base(base, runner=missing)
    with pytest.raises(baseid.BaseIdentityError):
        baseid.measure_base(base, runner=lambda cmd: "")


def fake_proc(tmp_path, dev="/dev/nbd0", img="/opt/img/Ubuntu.qcow2"):
    pr = tmp_path / "proc"
    (pr / "123").mkdir(parents=True)
    (pr / "123" / "cmdline").write_bytes(b"\0".join([b"/usr/bin/qemu-nbd", b"--read-only", f"--connect={dev}".encode(), img.encode()]) + b"\0")
    (pr / "7").mkdir()
    (pr / "7" / "cmdline").write_bytes(b"/bin/sleep\x0030\x00")
    return str(pr)


def test_the_image_receipt_ties_the_computed_qcow_hash_to_the_exact_read_only_mount(tmp_path):
    img = tmp_path / "Ubuntu.qcow2"
    img.write_bytes(b"qcow-bytes")
    good = baseid.sha256_file(str(img))
    proc = fake_proc(tmp_path, img=str(img))
    base = str(tmp_path / "mnt")
    run = lambda cmd: f"{base} ext4 ro,noload /dev/nbd0p3\n"
    r = baseid.verify_image_binding(base, good, runner=run, proc=proc)
    assert r["verified"] and r["problems"] == [] and r["computed_sha256"] == good and r["image_path"] == str(img) and r["nbd_device"] == "/dev/nbd0"
    bad = baseid.verify_image_binding(base, "0" * 64, runner=run, proc=proc)
    assert not bad["verified"] and any("is not the pinned" in p for p in bad["problems"])  # a declared hash that is NOT the served image is refused
    assert not baseid.verify_image_binding(base, good, runner=lambda c: f"{base} ext4 rw /dev/nbd0p3\n", proc=proc)["verified"]
    assert any("not an nbd partition" in p for p in baseid.verify_image_binding(base, good, runner=lambda c: f"{base} ext4 ro /dev/sda1\n", proc=proc)["problems"])
    assert any("no qemu-nbd process" in p for p in baseid.verify_image_binding(base, good, runner=lambda c: f"{base} ext4 ro /dev/nbd5p1\n", proc=proc)["problems"])
    assert baseid.rebind_problems(r, runner=run, proc=proc) == []
    img.write_bytes(b"changed-bytes!")  # size changes
    assert any("size or mtime changed" in p for p in baseid.rebind_problems(r, runner=run, proc=proc))
    assert baseid.rebind_problems({"verified": False}) == ["there is no verified image receipt"]
    assert any("served from" in p for p in baseid.rebind_problems(r, runner=run, proc=fake_proc(tmp_path / "x", img="/elsewhere.qcow2")) if (tmp_path / "x").mkdir(exist_ok=True) is None)


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
    m = baseid.measure_base(str(tmp_path), runner=lambda cmd: f"{tmp_path} ext4 ro,noload,relatime /dev/nbd0p3\n")
    assert m["fstype"] == "ext4" and m["read_only"] is True and m["os_release_sha256"] == baseid.sha256_file(str(tmp_path / "etc/os-release")) and m["dpkg_status_sha256"]
    assert baseid.measure_base(str(tmp_path), runner=lambda cmd: f"{tmp_path} ext4 rw,relatime /dev/nbd0p3\n")["read_only"] is False
    assert baseid.measure_base(str(tmp_path / "nowhere"), runner=lambda cmd: f"{tmp_path}/nowhere ext4 ro /dev/nbd0p3\n")["os_release_sha256"] is None  # unknown stays unknown, never a hash of nothing


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
    receipt = dict(RECEIPT, base_root=r)
    kw.setdefault("firewall", False)  # the production default (firewall on netns) is unchanged; the focused tests must not start a real nft firewall in an unprivileged sandbox
    return S.Supervisor(str(tmp_path / "state"), t, root_mode="full", netns=True, base_root=r, osworld_real_kit=T.KIT, criu_friendly=True, image_sha256=SHA, image_receipt=receipt, **kw), r, t


def test_the_guard_needs_the_pinned_image_hash_and_a_runnable_stage2_for_criu_on_an_image_root(tmp_path, monkeypatch):
    sup, r, t = make_sup(tmp_path, monkeypatch)
    assert sup.base_root == r and sup.criu_friendly and sup.image_sha256 == SHA
    monkeypatch.setattr(S.Supervisor, "_mount_template", lambda self: None)
    with pytest.raises(ValueError, match="needs image_sha256"):
        S.Supervisor(str(tmp_path / "s2"), t, root_mode="full", netns=True, base_root=r, osworld_real_kit=T.KIT, criu_friendly=True, sweep_on_start=False, firewall=False)
    for rc_ in (None, dict(RECEIPT, base_root=r, verified=False), dict(RECEIPT, base_root=r, computed_sha256="c" * 64), dict(RECEIPT, base_root="/elsewhere")):  # a declared hash without a matching VERIFIED receipt
        with pytest.raises(ValueError, match="VERIFIED image receipt"):
            S.Supervisor(str(tmp_path / "s2"), t, root_mode="full", netns=True, base_root=r, osworld_real_kit=T.KIT, criu_friendly=True, image_sha256=SHA, image_receipt=rc_, sweep_on_start=False, firewall=False)
    r3, t3 = image_world(tmp_path / "c", with_pkg=False)
    with pytest.raises(ValueError, match="stage-2"):
        S.Supervisor(str(tmp_path / "s3"), t3, root_mode="full", netns=True, base_root=r3, osworld_real_kit=T.KIT, criu_friendly=True, image_sha256=SHA, image_receipt=dict(RECEIPT, base_root=r3), sweep_on_start=False, firewall=False)
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
    monkeypatch.setattr(baseid, "rebind_problems", lambda receipt, runner=None, proc="/proc": [])
    monkeypatch.setattr(baseid, "current_identity", lambda s, measure=None: _ident_of(s))
    monkeypatch.setattr(S.subprocess, "run", lambda cmd, **k: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(cr, "restore", lambda *a, **k: {"rc": restore_rc, "seconds": 0.1, "log": ["Error: fake"]})


def _ident_of(s):
    custom = s.base_root != "/"
    return {"meta_version": baseid.META_VERSION, "base_root": s.base_root, "image_sha256": s.image_sha256, "image_receipt": dict(RECEIPT, base_root=s.base_root) if custom else None,
            "base": dict(BASE, base_root=s.base_root) if custom else None,
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
