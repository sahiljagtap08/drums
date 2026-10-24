import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "envd-linux"))
from envd_linux import criu as cr  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "aws_host")
SH = os.path.join(D, "install_criu_private.sh")


def make_deb(path, name, version, files):
    tree = path / f"{name}_tree"
    for f, body in files.items():
        p = tree / f.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        p.chmod(0o755)
    (tree / "DEBIAN").mkdir()
    (tree / "DEBIAN" / "control").write_text(f"Package: {name}\nVersion: {version}\nArchitecture: amd64\nMaintainer: t <t@example.invalid>\nDescription: t\n")
    subprocess.run(["dpkg-deb", "-b", str(tree), str(path / f"{name}_{version}_amd64.deb")], check=True, capture_output=True)


@pytest.fixture
def env(tmp_path):
    fake, pool = tmp_path / "bin", tmp_path / "pool"
    fake.mkdir()
    pool.mkdir()
    make_deb(pool, "criu", "3.19-4build2", {"/usr/sbin/criu": "#!/bin/sh\necho 'Version: 3.19'\n"})
    make_deb(pool, "libprotobuf-c1", "1.4.1-1ubuntu4", {"/usr/lib/x86_64-linux-gnu/libprotobuf-c.so.1": "x"})
    scripts = {
        "apt-cache": f'''case "$1" in
 policy) echo "criu:"; echo "  Installed: (none)"; echo "  Candidate: ${{FAKE_CAND-3.19-4build2}}";;
 depends) echo criu; echo "  Depends: libprotobuf-c1"; echo "  Depends: libc6"; echo libprotobuf-c1; echo libc6; echo "<virtual>";;
esac''',
        "apt-get": f'''[ "$1" = download ] || exit 9; shift; echo "$@" > "{tmp_path}/fetched"
for p in "$@"; do cp {pool}/${{p}}_*.deb . 2>/dev/null || exit 100; done''',
        "dpkg": '''[ "$1" = -s ] && case "$2" in libc6) exit 0;; *) exit 1;; esac''',
        "ldd": '''echo "libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6"; [ -z "${FAKE_MISSING:-}" ] || echo "libnl-3.so.200 => not found"''',
        "ip": "exit 0",
    }
    for n, b in scripts.items():
        (fake / n).write_text("#!/bin/bash\n" + b + "\n")
        (fake / n).chmod(0o755)

    def run(dest, **e):
        ev = {"PATH": f"{fake}:/usr/bin:/bin", "HOME": "/nonexistent"}
        ev.update(e)
        return subprocess.run(["bash", SH, str(dest)], capture_output=True, text=True, env=ev)
    run.tmp = tmp_path
    return run


def test_the_script_unpacks_a_private_criu_under_its_real_version_with_only_the_missing_libraries_and_provenance(env, tmp_path):
    dest = tmp_path / "criu"
    r = env(dest)
    assert r.returncode == 0, r.stderr
    assert (dest / "x-3.19" / "usr/sbin/criu").exists() and not (dest / "x-4.2").exists()  # named after the REAL upstream version
    assert (dest / "libs/usr/lib/x86_64-linux-gnu/libprotobuf-c.so.1").exists() and (dest / "libs/bin/ip").is_symlink()
    assert sorted(open(env.tmp / "fetched").read().split()) == ["criu", "libprotobuf-c1"]  # libc6 is installed on the host: not fetched
    prov = (dest / "CRIU_PROVENANCE.txt").read_text()
    assert "criu real upstream version: 3.19" in prov and "criu_3.19-4build2_amd64.deb 3.19-4build2 sha256 " in prov and "libprotobuf-c1_1.4.1-1ubuntu4_amd64.deb" in prov
    assert "UNKNOWN until" in r.stdout
    assert cr.Criu(home=str(dest)).binary == str(dest / "x-3.19/usr/sbin/criu")  # the Supervisor's Criu() finds it without a hard-coded version


def test_refusals_leave_nothing_half_installed_in_a_new_state(env, tmp_path):
    assert env("relative/dir").returncode == 2
    assert env(tmp_path / "x", FAKE_CAND="(none)").returncode == 2 and not (tmp_path / "x" / "CRIU_PROVENANCE.txt").exists()
    r = env(tmp_path / "y", FAKE_MISSING="1")
    assert r.returncode == 2 and "libnl-3.so.200" in r.stderr and not (tmp_path / "y" / "CRIU_PROVENANCE.txt").exists()  # an unresolved library is a refusal, with no provenance claiming success
    dest = tmp_path / "z"
    assert env(dest).returncode == 0
    again = env(dest)
    assert again.returncode == 2 and "already holds" in again.stderr


def test_criu_detects_exactly_one_version_directory_and_refuses_ambiguity(tmp_path, monkeypatch):
    monkeypatch.delenv("DRUMS_CRIU_VERSION", raising=False)
    assert cr.Criu(home=str(tmp_path)).binary.endswith("x-4.2/usr/sbin/criu")  # none: the historical name
    (tmp_path / "x-3.19").mkdir()
    assert cr.Criu(home=str(tmp_path)).binary.endswith("x-3.19/usr/sbin/criu")
    (tmp_path / "x-4.2").mkdir()
    with pytest.raises(cr.CriuError, match="several CRIU versions"):
        cr.Criu(home=str(tmp_path))
    monkeypatch.setenv("DRUMS_CRIU_VERSION", "4.2")
    assert cr.Criu(home=str(tmp_path)).binary.endswith("x-4.2/usr/sbin/criu")
    assert cr.Criu(home=str(tmp_path), version="3.19").binary.endswith("x-3.19/usr/sbin/criu")  # an explicit argument still wins


def test_bootstrap_uses_the_new_script_and_no_longer_hardcodes_the_version_name():
    s = open(os.path.join(D, "bootstrap.sh")).read()
    assert "install_criu_private.sh" in s and "x-4.2" not in s and "CRIU_DEB_URLS" not in s
