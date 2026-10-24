import hashlib
import os
import subprocess

import pytest

D = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "aws_host")
SH = os.path.join(D, "add_debs_to_overlay.sh")


def make_deb(dirpath, name, files, arch="amd64"):
    root = dirpath / (name + "_tree")
    for f, content in files.items():
        p = root / f.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (root / "DEBIAN").mkdir()
    (root / "DEBIAN" / "control").write_text(f"Package: {name}\nVersion: 1.0-1ubuntu1\nArchitecture: {arch}\nMaintainer: t <t@example.invalid>\nDescription: t\n")
    out = dirpath / f"{name}.deb"
    subprocess.run(["dpkg-deb", "-b", str(root), str(out)], check=True, capture_output=True)
    return out


@pytest.fixture
def env(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("the script refuses to run unless root")
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "usr/lib").mkdir(parents=True)
    (root / "usr/lib/existing.so").write_text("image")
    debs = tmp_path / "debs"
    debs.mkdir()
    ov = tmp_path / "ov"

    def run(*names, tamper=False):
        sums = debs / "SHA256SUMS"
        sums.write_text("".join(f"{hashlib.sha256((debs / n).read_bytes()).hexdigest()}  {n}\n" for n in names))
        if tamper:
            (debs / names[0]).write_bytes((debs / names[0]).read_bytes() + b"x")
        return subprocess.run(["bash", SH, str(root), str(ov), str(debs), str(sums)], capture_output=True, text=True)
    run.root, run.debs, run.ov = root, debs, ov
    return run


def test_a_clean_deb_is_unpacked_into_the_overlay_only_and_recorded(env):
    make_deb(env.debs, "xvfb", {"/usr/bin/Xvfb": "x", "/usr/lib/x86_64-linux-gnu/libnew.so.1": "x"})
    r = env("xvfb.deb")
    assert r.returncode == 0, r.stderr
    assert (env.ov / "upper/usr/bin/Xvfb").exists() and not (env.root / "usr/bin").exists()  # the image root has no new path
    assert "xvfb 1.0-1ubuntu1 xvfb.deb" in (env.ov / "additions.txt").read_text()


def test_overwrites_outside_paths_wrong_arch_and_bad_hashes_are_refused_before_unpacking_anything(env):
    make_deb(env.debs, "good", {"/usr/bin/ok": "x"})
    for name, files, kw, why in (("over", {"/usr/lib/existing.so": "new"}, {}, "would change"), ("outside", {"/bin/evil": "x"}, {}, "outside /usr"),
                                 ("etc", {"/etc/passwd": "x"}, {}, "outside /usr"), ("arm", {"/usr/bin/a": "x"}, {"arch": "arm64"}, "not an amd64")):
        make_deb(env.debs, name, files, **kw)
        r = env("good.deb", f"{name}.deb")
        assert r.returncode == 2 and why in r.stderr, (name, r.stderr)
        assert not (env.ov / "upper").exists() and not [p for p in env.ov.iterdir() if p.name.startswith(".stage")]  # `good.deb` was not unpacked into the overlay either: all debs are checked first
    r = env("good.deb", tamper=True)
    assert r.returncode == 2 and "sha256" in r.stderr and not (env.ov / "upper").exists()


def test_documentation_paths_are_dropped_and_identical_existing_files_are_tolerated_but_different_ones_refuse(env):
    (env.root / "usr/share/doc/xvfb").mkdir(parents=True)
    (env.root / "usr/share/doc/xvfb/copyright").write_text("image doc")
    (env.root / "usr/share/man/man1").mkdir(parents=True)
    make_deb(env.debs, "xvfb", {"/usr/bin/Xvfb": "x", "/usr/share/doc/xvfb/copyright": "deb doc", "/usr/share/man/man1/Xvfb.1": "man", "/usr/lib/existing.so": "image"})
    r = env("xvfb.deb")
    assert r.returncode == 0, r.stderr  # the doc/man overlap is dropped and the identical existing.so is accepted
    assert (env.ov / "upper/usr/bin/Xvfb").exists() and not (env.ov / "upper/usr/share/doc").exists() and (env.root / "usr/share/doc/xvfb/copyright").read_text() == "image doc"
    assert not [p for p in env.ov.iterdir() if p.name.startswith(".stage")]  # the staging directory is removed


def test_two_debs_writing_different_bytes_to_one_path_are_refused(env):
    make_deb(env.debs, "a", {"/usr/bin/shared": "one"})
    make_deb(env.debs, "b", {"/usr/bin/shared": "two"})
    r = env("a.deb", "b.deb")
    assert r.returncode == 2 and "different content" in r.stderr and not (env.ov / "upper").exists()


def test_the_pinned_xvfb_fetch_script_pins_url_hash_and_size_and_refuses_a_wrong_download(tmp_path):
    sh = os.path.join(D, "fetch_xvfb_deb.sh")
    s = open(sh).read()
    assert "20240301T030400Z" in s and "f8c64bc652e3dc1a1041e85025267f574c27f86b00d7965545a8b3ec41a60cd4" in s and "SIZE=863344" in s and "--proto '=https'" in s and "--max-redirs 0" in s
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "curl").write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done; printf wrong > "$out"\n')
    (fake / "curl").chmod(0o755)
    dest = tmp_path / "debs"
    r = subprocess.run(["bash", sh, str(dest)], capture_output=True, text=True, env={"PATH": f"{fake}:/usr/bin:/bin"})
    assert r.returncode == 2 and "size is not" in r.stderr and not (dest / "xvfb.deb").exists() and not [p for p in dest.iterdir()]
    assert subprocess.run(["bash", sh, "relative"], capture_output=True, text=True).returncode == 2
