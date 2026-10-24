"""Static and refusal-path checks of the host scripts. They only ever hit the scripts' EARLY refusals (nothing is installed, downloaded or started)."""
import os
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
D = os.path.join(ROOT, "deploy", "aws_host")
SCRIPTS = ["bootstrap.sh", "fetch_image.sh", "fetch_docker_image.sh"]


def read(n):
    return open(os.path.join(D, n)).read()


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_parse_and_fail_on_error(name):
    assert subprocess.run(["bash", "-n", os.path.join(D, name)]).returncode == 0
    assert "set -euo pipefail" in read(name)


def test_bootstrap_creates_and_owns_every_directory_it_later_writes_and_declares_the_stage():
    s = read("bootstrap.sh")
    loop = s.split("for d in ", 1)[1].split("; do", 1)[0]
    for var in ("$STATE", "$SRC", "$OSW", "$CRIU_HOME", "/opt/vm-image", "/opt/drums-osworld-real", "/opt/pw-browsers"):
        assert var in loop, var
    for assign in ("STATE=/var/lib/drums-host", "SRC=/opt/drums-src", "OSW=/opt/osworld-pinned", "CRIU_HOME=/opt/drums-criu"):
        assert assign in s, assign
    assert 'sudo mkdir -p "$d" && sudo chown "$USER" "$d"' in s
    assert 'case "${STAGE:-}" in docker|vbox' in s and "set DRUMS_REF" in s


def test_bootstrap_installs_every_system_piece_the_realkit_needs():
    s = read("bootstrap.sh")
    import importlib.util
    spec = importlib.util.spec_from_file_location("realkit", os.path.join(ROOT, "envd-linux", "envd_linux", "realkit.py"))
    rk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rk)
    pkgs = {"dbus-daemon": "dbus-x11", "Xvfb": "xvfb", "openbox": "openbox", "scrot": "scrot", "xclip": "xclip", "wmctrl": "wmctrl", "socat": "socat", "xprop": "x11-utils"}
    for b in rk.NEEDED_BINARIES:
        assert pkgs[b] in s, f"{b} needs apt package {pkgs[b]}"
    for p in ("python3-pyatspi", "at-spi2-core", "python3.12", "libreoffice-writer", "python3-xlib", "python3-pyroute2", "gnome-terminal"):
        assert p in s


def test_the_two_python_environments_and_root_requirement_are_explicit():
    s = read("bootstrap.sh")
    assert "TWO PYTHON ENVIRONMENTS" in s and ".venv-host" in s and "sudo env PYTHONPATH" in s and "librosa" in s
    assert 'sudo VBoxManage' in s  # every VirtualBox call in bootstrap runs as root, like host_run
    for n in ("fetch_image.sh", "fetch_docker_image.sh"):
        assert '"$(id -u)" -eq 0' in read(n)


def test_image_scripts_check_completeness_before_use_and_never_skip_on_a_bare_file_test():
    v, d = read("fetch_image.sh"), read("fetch_docker_image.sh")
    assert "[ -f Ubuntu.zip ] ||" not in v and "unzip -tq" in v and "Content-Length" in v.lower() or "content-length" in v
    assert "unzip -tq" in d and "--max-filesize" in d and "--zip-bytes" in d and "sha256sum" in d
    assert "Ubuntu.partial" in v  # extraction goes to a scratch dir and is renamed only after the OVF is confirmed


@pytest.mark.parametrize("name,args", [("bootstrap.sh", []), ("fetch_image.sh", []), ("fetch_docker_image.sh", []), ("fetch_docker_image.sh", ["--accept-licence-risk"]),
    ("fetch_docker_image.sh", ["--accept-licence-risk", "--zip-bytes", "1", "--uncompressed-bytes", "2"])])
def test_each_script_refuses_before_doing_anything(name, args):
    r = subprocess.run(["bash", os.path.join(D, name), *args], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": "/nonexistent"}, timeout=30)
    assert r.returncode == 2 and "REFUSED" in (r.stdout + r.stderr)


def test_the_docker_image_script_is_pinned_to_the_commit_and_requires_the_zip_hash():
    d = read("fetch_docker_image.sh")
    assert "resolve/$HF_COMMIT/Ubuntu.qcow2.zip" in d and "HF_COMMIT=a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133" in d and "/resolve/main/" not in d.replace("mutable `main`", "")
    assert "--zip-sha256 is required" in d and "RECORDED ONLY" not in d and '[ "$SHA" = "$ZSHA" ] || die' in d
    assert "b795b6cd4c69b252c1b4f10150a347795555032501b60fd031751ed09b896712" in d and "12273896463" in d and "24460197888" in d


# ---- the read-only mount script, with FAKE qemu-nbd/mount/lsblk (no real device, no real mount) ----
FAKES = {
    "qemu-nbd": '''echo "qemu-nbd $*" >> "$FAKE_LOG"
case "$1" in --disconnect) rm -f "$FAKE_DIR/attached";; --read-only) touch "$FAKE_DIR/attached";; esac''',
    "qemu-img": "exit 0", "modprobe": '''echo "modprobe $*" >> "$FAKE_LOG"''', "partprobe": "exit 0",
    "blockdev": '''echo "blockdev $*" >> "$FAKE_LOG"
if [ "$1" = "--getro" ]; then echo "${FAKE_GETRO:-1}"; fi''',
    "lsblk": '''[ -e "$FAKE_DIR/attached" ] || exit 0
if [ -n "${FAKE_NO_EXT4:-}" ]; then echo "nbd0p1 100 vfat"; else echo "nbd0p1 1000 ext4"; echo "nbd0p2 5000 ext4"; echo "nbd0p3 9999 swap"; fi''',
    "mount": '''echo "mount $*" >> "$FAKE_LOG"
[ -z "${FAKE_MOUNT_FAIL:-}" ] || exit 32
touch "$FAKE_DIR/mounted"; mkdir -p "${@: -1}/etc"; echo 'PRETTY_NAME="Ubuntu 22.04.3 LTS"' > "${@: -1}/etc/os-release"''',
    "umount": '''echo "umount $*" >> "$FAKE_LOG"; rm -f "$FAKE_DIR/mounted"; rm -rf "$1/etc"''',
    "mountpoint": '''[ -e "$FAKE_DIR/mounted" ]''',
    "findmnt": '''echo "${FAKE_OPTS:-ro,noload,relatime}"''',
}


@pytest.fixture
def mount_env(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("the script refuses to run unless root")
    fake = tmp_path / "bin"
    fake.mkdir()
    for name, body in FAKES.items():
        (fake / name).write_text("#!/bin/bash\n" + body + "\n")
        (fake / name).chmod(0o755)
    sysblock = tmp_path / "sysblock"
    for n in range(16):
        (sysblock / f"nbd{n}").mkdir(parents=True)
        (sysblock / f"nbd{n}" / "size").write_text("0\n")
    d = tmp_path / "fake_state"
    d.mkdir()
    img = tmp_path / "image.qcow2"
    img.write_bytes(b"x")
    mnt_parent = tmp_path / "mnt"
    mnt_parent.mkdir()

    def run(*args, **env):
        e = {"PATH": f"{fake}:/usr/bin:/bin", "FAKE_LOG": str(d / "log"), "FAKE_DIR": str(d), "DRUMS_IMAGE_STATE": str(d / "state"), "DRUMS_SYS_BLOCK": str(sysblock), "HOME": "/nonexistent"}
        e.update({k: str(v) for k, v in env.items()})
        r = subprocess.run(["bash", os.path.join(D, "mount_image_rootfs.sh"), *[str(a) for a in args]], capture_output=True, text=True, env=e, timeout=60)
        log = (d / "log").read_text().splitlines() if (d / "log").exists() else []
        return r, log

    run.dir, run.img, run.mp, run.sysblock = d, img, mnt_parent / "ro", sysblock
    run.state = d / "state"
    return run


def test_a_clean_run_attaches_read_only_sets_the_block_device_read_only_mounts_the_largest_ext4_ro_noload_and_records_state(mount_env):
    r, log = mount_env(mount_env.img, mount_env.mp)
    assert r.returncode == 0, r.stderr
    assert f"qemu-nbd --read-only --connect=/dev/nbd0 {mount_env.img}" in log and "blockdev --setro /dev/nbd0" in log
    assert f"mount -o ro,noload /dev/nbd0p2 {mount_env.mp}" in log  # the 5000-byte partition, not the 1000 or the swap
    assert not any(" rw" in x or ",rw" in x for x in log if x.startswith("mount")) and not [x for x in log if "--disconnect" in x]
    assert mount_env.state.read_text().split() == [str(mount_env.mp), "/dev/nbd0", "nbd0p2"] and "Ubuntu 22.04.3" in r.stdout


@pytest.mark.parametrize("env,reason", [({"FAKE_MOUNT_FAIL": "1"}, "mount fails"), ({"FAKE_OPTS": "rw,relatime"}, "mount comes up read-write"), ({"FAKE_GETRO": "0"}, "device is not read-only"),
                                        ({"FAKE_NO_EXT4": "1"}, "no ext4 partition")])
def test_every_failure_unmounts_disconnects_removes_state_and_removes_the_directory_it_created(mount_env, env, reason):
    r, log = mount_env(mount_env.img, mount_env.mp, **env)
    assert r.returncode != 0, reason
    assert any("--disconnect /dev/nbd0" in x for x in log), (reason, log)  # the attached device is detached
    assert not mount_env.state.exists() and not mount_env.mp.exists() and not (mount_env.dir / "mounted").exists() and not (mount_env.dir / "attached").exists()
    if reason == "device is not read-only":
        assert not [x for x in log if x.startswith("mount ")]  # refused BEFORE the mount
    if reason == "mount comes up read-write":
        assert any(x.startswith("umount") for x in log)  # an rw result is unmounted again


def test_a_busy_device_is_never_touched_and_with_all_devices_busy_nothing_is_attached(mount_env):
    (mount_env.sysblock / "nbd0" / "size").write_text("123\n")
    (mount_env.sysblock / "nbd1" / "pid").write_text("999\n")
    r, log = mount_env(mount_env.img, mount_env.mp)
    assert r.returncode == 0 and "qemu-nbd --read-only --connect=/dev/nbd2 " + str(mount_env.img) in log and not any("nbd0 " in x or "nbd1 " in x for x in log if x.startswith("qemu-nbd"))
    for n in range(16):
        (mount_env.sysblock / f"nbd{n}" / "size").write_text("5\n")
    mount_env.state.unlink()
    (mount_env.dir / "log").unlink()
    r3, log3 = mount_env(mount_env.img, mount_env.mp.parent / "third")
    assert r3.returncode == 2 and "all are busy" in r3.stderr and not [x for x in log3 if x.startswith(("qemu-nbd", "mount", "umount", "blockdev"))] and not (mount_env.mp.parent / "third").exists()


def test_unsafe_arguments_are_refused_before_any_command_runs(mount_env, tmp_path):
    link = tmp_path / "link.qcow2"
    os.symlink(mount_env.img, link)
    nonempty = tmp_path / "mnt" / "nonempty"
    nonempty.mkdir()
    (nonempty / "f").write_text("x")
    symdir = tmp_path / "mnt" / "symdir"
    os.symlink(tmp_path, symdir)
    cases = [(["relative.qcow2", mount_env.mp], "absolute"), ([mount_env.img, "relative/mp"], "absolute"), ([link, mount_env.mp], "symlink"), ([mount_env.img, "/"], "top-level"),
             ([mount_env.img, "/mnt"], "top-level"), ([mount_env.img, "/proc/x"], "system path"), ([mount_env.img, "/etc/x"], "system path"), ([mount_env.img, nonempty], "not empty"),
             ([mount_env.img, symdir], "symlink"), ([tmp_path / "missing.qcow2", mount_env.mp], "no such image")]
    for args, why in cases:
        r, log = mount_env(*args)
        assert r.returncode == 2 and why in r.stderr and not log, (args, r.stderr, log)  # no fake command was ever called


def test_an_existing_state_record_blocks_a_second_mount_and_unmount_reverses_exactly_what_was_recorded(mount_env):
    assert mount_env(mount_env.img, mount_env.mp)[0].returncode == 0
    r, log = mount_env(mount_env.img, mount_env.mp.parent / "second")
    assert r.returncode == 2 and "previous image mount is recorded" in r.stderr
    r, log = mount_env("--unmount", mount_env.mp.parent / "wrong")
    assert r.returncode == 2 and "recorded mount is" in r.stderr
    r, log = mount_env("--unmount", mount_env.mp)
    assert r.returncode == 0 and any(x.startswith("umount") for x in log) and any("--disconnect /dev/nbd0" in x for x in log) and not mount_env.state.exists()
    assert mount_env("--unmount", mount_env.mp)[0].returncode == 2  # nothing recorded any more


def test_the_script_text_keeps_the_exit_trap_the_success_flag_and_never_asks_for_write_access():
    s = read("mount_image_rootfs.sh")
    assert subprocess.run(["bash", "-n", os.path.join(D, "mount_image_rootfs.sh")]).returncode == 0 and "set -euo pipefail" in s
    assert "trap cleanup EXIT" in s and "trap 'exit 130' INT TERM" in s and "OK=1" in s and s.index("OK=1") > s.index("findmnt") > s.index("mount -o ro,noload") and "only what THIS run mounted" in s
    assert "--read-only --connect" in s and "blockdev --setro" in s and "mount -o ro,noload" in s and "LARGEST ext4 partition" in s and "nosuid" not in s
    assert "mount -o rw" not in s and "mount -w" not in s and "--connect=\"$DEV\" \"$Q\"" in s
