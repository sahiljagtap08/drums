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
    for name, files, kw, why in (("over", {"/usr/lib/existing.so": "new"}, {}, "would overwrite"), ("outside", {"/bin/evil": "x"}, {}, "outside /usr"),
                                 ("etc", {"/etc/passwd": "x"}, {}, "outside /usr"), ("arm", {"/usr/bin/a": "x"}, {"arch": "arm64"}, "not an amd64")):
        make_deb(env.debs, name, files, **kw)
        r = env("good.deb", f"{name}.deb")
        assert r.returncode == 2 and why in r.stderr, (name, r.stderr)
        assert not env.ov.exists()  # `good.deb` was not unpacked either: all debs are checked first
    r = env("good.deb", tamper=True)
    assert r.returncode == 2 and "sha256" in r.stderr and not env.ov.exists()
