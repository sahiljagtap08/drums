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


# ---- the read-only mount script, with FAKE qemu-nbd/mount/lsblk/findmnt (no real device, no real mount) ----
# `FAKE_TERM_AT=<command>` makes that fake send SIGTERM to the script; `FAKE_FAIL=<command>` makes it fail; `FAKE_UMOUNT_FAIL` / `FAKE_DISC_FAIL` leave the mount / device in place.
FAKES = {
    "qemu-nbd": '''echo "qemu-nbd $*" >> "$FAKE_LOG"
case "$1" in
  --disconnect) [ -z "${FAKE_DISC_FAIL:-}" ] || exit 1; rm -f "$FAKE_DIR/attached"; echo 0 > "$DRUMS_SYS_BLOCK/$(basename "$2")/size";;
  --read-only) [ "${FAKE_FAIL:-}" != qemu-nbd ] || exit 1; d=$(basename "${2#--connect=}"); echo "$d" > "$FAKE_DIR/attached"; echo 100 > "$DRUMS_SYS_BLOCK/$d/size";;
esac''',
    "qemu-img": "exit 0", "modprobe": '''echo "modprobe $*" >> "$FAKE_LOG"''', "partprobe": "exit 0",
    "blockdev": '''echo "blockdev $*" >> "$FAKE_LOG"
[ "${FAKE_TERM_AT:-}" != blockdev ] || kill -TERM $PPID
if [ "$1" = "--getro" ]; then echo "${FAKE_GETRO:-1}"; fi''',
    "lsblk": '''[ -e "$FAKE_DIR/attached" ] || exit 0
d=$(cat "$FAKE_DIR/attached")
if [ -n "${FAKE_NO_EXT4:-}" ]; then echo "${d}p1 100 vfat"; else echo "${d}p1 1000 ext4"; echo "${d}p2 5000 ext4"; echo "${d}p3 9999 swap"; fi''',
    "mount": '''echo "mount $*" >> "$FAKE_LOG"
[ -z "${FAKE_MOUNT_FAIL:-}" ] || exit 32
touch "$FAKE_DIR/mounted"; mkdir -p "${@: -1}/etc"; [ -n "${FAKE_NO_OSREL:-}" ] || echo 'PRETTY_NAME="Ubuntu 22.04.3 LTS"' > "${@: -1}/etc/os-release"
[ "${FAKE_TERM_AT:-}" != mount ] || kill -TERM $PPID''',
    "umount": '''echo "umount $*" >> "$FAKE_LOG"; [ -z "${FAKE_UMOUNT_FAIL:-}" ] || exit 1; rm -f "$FAKE_DIR/mounted"; rm -rf "$1/etc"''',
    "mountpoint": '''[ -e "$FAKE_DIR/mounted" ]''',
    "findmnt": '''case "$*" in *SOURCE*) echo "${FAKE_SRC:-/dev/$(cat "$FAKE_DIR/attached" 2>/dev/null || echo nbd0)p2}";; *) echo "${FAKE_OPTS:-ro,noload,relatime}";; esac''',
}
OWNED_OPEN = ["modprobe nbd max_part=16", "qemu-nbd --read-only --connect=/dev/nbd0 {img}", "blockdev --setro /dev/nbd0", "blockdev --getro /dev/nbd0"]


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
    sd = tmp_path / "statedir"
    sd.mkdir()

    def run(*args, **env):
        e = {"PATH": f"{fake}:/usr/bin:/bin", "FAKE_LOG": str(d / "log"), "FAKE_DIR": str(d), "DRUMS_IMAGE_STATE_DIR": str(sd), "DRUMS_SYS_BLOCK": str(sysblock), "HOME": "/nonexistent"}
        e.update({k: str(v) for k, v in env.items()})
        r = subprocess.run(["bash", os.path.join(D, "mount_image_rootfs.sh"), *[str(a) for a in args]], capture_output=True, text=True, env=e, timeout=60)
        log = (d / "log").read_text().splitlines() if (d / "log").exists() else []
        return r, log

    run.dir, run.img, run.mp, run.sysblock, run.statedir = d, img, mnt_parent / "ro", sysblock, sd
    run.states = lambda: sorted(sd.glob("drums-image-rootfs.*.state"))
    run.clear_log = lambda: (d / "log").unlink() if (d / "log").exists() else None
    return run


def mounts_and_attaches(log):
    return [x for x in log if x.startswith(("mount ", "umount ", "qemu-nbd", "blockdev --setro"))]


def test_a_clean_run_attaches_read_only_sets_the_block_device_read_only_mounts_the_largest_ext4_ro_noload_and_records_state(mount_env):
    r, log = mount_env(mount_env.img, mount_env.mp)
    assert r.returncode == 0, r.stderr
    assert log == [x.format(img=mount_env.img) for x in OWNED_OPEN] + [f"mount -o ro,noload /dev/nbd0p2 {mount_env.mp}"]  # the 5000-byte partition, not the 1000 or the swap; nothing else ran
    (state,) = mount_env.states()
    assert state.read_text().split() == [str(mount_env.mp), "/dev/nbd0", "nbd0p2", "/dev/nbd0p2"] and "Ubuntu 22.04.3" in r.stdout


CLEAN_FAILURES = [({"FAKE_MOUNT_FAIL": "1"}, "mount fails"), ({"FAKE_OPTS": "rw,relatime"}, "mount comes up read-write"), ({"FAKE_GETRO": "0"}, "device is not read-only"),
                  ({"FAKE_NO_EXT4": "1"}, "no ext4 partition"), ({"FAKE_SRC": "/dev/sda1"}, "mount source is not the partition"), ({"FAKE_NO_OSREL": "1"}, "not a root filesystem"),
                  ({"FAKE_TERM_AT": "mount"}, "terminated right after the mount"), ({"FAKE_TERM_AT": "blockdev"}, "terminated after attach"), ({"FAKE_FAIL": "qemu-nbd"}, "attach itself fails")]


@pytest.mark.parametrize("env,reason", CLEAN_FAILURES)
def test_every_failure_runs_exactly_the_owned_cleanup_in_order_and_leaves_no_state_directory_or_device(mount_env, env, reason):
    r, log = mount_env(mount_env.img, mount_env.mp, **env)
    assert r.returncode != 0, reason
    acts = mounts_and_attaches(log)
    mounted = any(x.startswith("mount ") for x in acts)
    expect = [f"qemu-nbd --read-only --connect=/dev/nbd0 {mount_env.img}"]
    if reason != "attach itself fails":
        expect += ["blockdev --setro /dev/nbd0"]
    if mounted:
        expect += [f"mount -o ro,noload /dev/nbd0p2 {mount_env.mp}", f"umount {mount_env.mp}"]  # unmount BEFORE disconnect
    expect += ["qemu-nbd --disconnect /dev/nbd0"]
    assert acts == expect, (reason, acts)
    assert not mount_env.states() and not mount_env.mp.exists() and not (mount_env.dir / "mounted").exists() and not (mount_env.dir / "attached").exists()
    assert (mount_env.sysblock / "nbd0" / "size").read_text().strip() == "0"
    if reason == "device is not read-only":
        assert not [x for x in log if x.startswith("mount ")]  # refused BEFORE the mount
    if "terminated" in reason:
        assert r.returncode == 130


def test_a_state_write_failure_after_attach_still_disconnects_exactly_the_attached_device(mount_env):
    import hashlib
    blocked = mount_env.statedir / f"drums-image-rootfs.{hashlib.sha256(str(mount_env.mp).encode()).hexdigest()[:16]}.state"
    blocked.mkdir()  # the record cannot be written: a directory sits where the file must go
    r, log = mount_env(mount_env.img, mount_env.mp)
    assert r.returncode != 0 and mounts_and_attaches(log) == [f"qemu-nbd --read-only --connect=/dev/nbd0 {mount_env.img}", "qemu-nbd --disconnect /dev/nbd0"]
    assert not mount_env.mp.exists() and not (mount_env.dir / "attached").exists()


def test_a_second_invocation_is_refused_before_it_touches_anything_while_the_lock_is_held(mount_env):
    import fcntl
    fd = open(mount_env.statedir / "drums-image-rootfs.lock", "w")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for args in ((mount_env.img, mount_env.mp), ("--unmount", mount_env.mp)):
        r, log = mount_env(*args)
        assert r.returncode == 2 and "holds the lock" in r.stderr and not log and not mount_env.states() and not mount_env.mp.exists()
    fcntl.flock(fd, fcntl.LOCK_UN)
    assert mount_env(mount_env.img, mount_env.mp)[0].returncode == 0  # released: the same call now works


def test_a_failed_unmount_in_cleanup_keeps_the_state_never_disconnects_and_says_how_to_recover(mount_env):
    r, log = mount_env(mount_env.img, mount_env.mp, FAKE_OPTS="rw,relatime", FAKE_UMOUNT_FAIL="1")
    assert r.returncode != 0 and "STILL MOUNTED" in r.stderr and "--unmount" in r.stderr
    assert not [x for x in log if "--disconnect" in x]  # a device that is still mounted is never pulled away
    assert len(mount_env.states()) == 1 and mount_env.mp.exists()


def test_a_disconnect_that_cannot_be_verified_keeps_the_state_and_is_a_failure(mount_env):
    r, log = mount_env(mount_env.img, mount_env.mp, FAKE_MOUNT_FAIL="1", FAKE_DISC_FAIL="1")
    assert r.returncode != 0 and "did not report disconnected" in r.stderr and len(mount_env.states()) == 1


def test_a_busy_device_is_never_touched_even_when_the_run_fails_and_with_all_devices_busy_nothing_is_attached(mount_env):
    (mount_env.sysblock / "nbd0" / "size").write_text("123\n")
    (mount_env.sysblock / "nbd1" / "pid").write_text("999\n")
    r, log = mount_env(mount_env.img, mount_env.mp, FAKE_MOUNT_FAIL="1")
    assert r.returncode != 0 and f"qemu-nbd --read-only --connect=/dev/nbd2 {mount_env.img}" in log and "qemu-nbd --disconnect /dev/nbd2" in log
    assert not [x for x in log if "nbd0" in x or "nbd1" in x] and (mount_env.sysblock / "nbd0" / "size").read_text().strip() == "123"
    for n in range(16):
        (mount_env.sysblock / f"nbd{n}" / "size").write_text("5\n")
    mount_env.clear_log()
    r3, log3 = mount_env(mount_env.img, mount_env.mp.parent / "third")
    assert r3.returncode == 2 and "all are busy" in r3.stderr and not mounts_and_attaches(log3) and not (mount_env.mp.parent / "third").exists() and not mount_env.states()


def test_unsafe_arguments_are_refused_before_any_command_runs(mount_env, tmp_path):
    link = tmp_path / "link.qcow2"
    os.symlink(mount_env.img, link)
    nonempty = tmp_path / "mnt" / "nonempty"
    nonempty.mkdir()
    (nonempty / "f").write_text("x")
    symdir = tmp_path / "mnt" / "symdir"
    os.symlink(tmp_path, symdir)
    cases = [(["relative.qcow2", mount_env.mp], "absolute"), ([mount_env.img, "relative/mp"], "absolute"), ([link, mount_env.mp], "canonical"), ([mount_env.img, "/"], "top-level"),
             ([mount_env.img, "/mnt"], "top-level"), ([mount_env.img, "/proc/x"], "system path"), ([mount_env.img, "/etc/x"], "system path"), ([mount_env.img, nonempty], "not empty"),
             ([mount_env.img, symdir], "canonical"), ([mount_env.img, str(mount_env.mp) + "/../x"], "canonical"), ([mount_env.img, str(mount_env.mp) + ",x"], "safe characters"),
             ([tmp_path / "missing.qcow2", mount_env.mp], "no such image")]
    for args, why in cases:
        r, log = mount_env(*args)
        assert r.returncode == 2 and why in r.stderr and not log and not mount_env.states(), (args, r.stderr, log)  # no fake command was ever called


def test_state_is_per_mount_point_and_unmount_needs_the_exact_record_and_a_matching_live_source(mount_env):
    assert mount_env(mount_env.img, mount_env.mp)[0].returncode == 0
    other = mount_env.mp.parent / "second"
    r, log = mount_env(mount_env.img, other)  # a different mount point has its own (absent) record and its own busy-device scan
    assert r.returncode == 0 and len(mount_env.states()) == 2
    assert mount_env("--unmount", other)[0].returncode == 0 and len(mount_env.states()) == 1  # unmounting one never touches the other
    r, _ = mount_env(mount_env.img, mount_env.mp)
    assert r.returncode == 2 and "previous image mount" in r.stderr  # same mount point again is refused
    unrelated = mount_env.mp.parent / "unrelated"
    mount_env.clear_log()
    r, log = mount_env("--unmount", unrelated)
    assert r.returncode == 2 and "no recorded image mount" in r.stderr and not [x for x in log if x.startswith(("umount", "qemu-nbd --disconnect"))]
    mount_env.clear_log()
    r, log = mount_env("--unmount", mount_env.mp, FAKE_SRC="/dev/sda1")  # something else is mounted there now
    assert r.returncode == 2 and "does not match" in r.stderr and not [x for x in log if x.startswith(("umount", "qemu-nbd"))] and len(mount_env.states()) == 1
    r, log = mount_env("--unmount", mount_env.mp)
    assert r.returncode == 0 and [x for x in log if x.startswith(("umount", "qemu-nbd --disconnect"))] == [f"umount {mount_env.mp}", "qemu-nbd --disconnect /dev/nbd0"] and not mount_env.states()
    assert mount_env("--unmount", mount_env.mp)[0].returncode == 2


def test_an_unmount_that_fails_keeps_the_record_and_the_device(mount_env):
    assert mount_env(mount_env.img, mount_env.mp)[0].returncode == 0
    mount_env.clear_log()
    r, log = mount_env("--unmount", mount_env.mp, FAKE_UMOUNT_FAIL="1")
    assert r.returncode == 2 and not [x for x in log if "--disconnect" in x] and len(mount_env.states()) == 1


def test_the_script_text_keeps_the_exit_trap_the_success_flag_and_never_asks_for_write_access():
    s = read("mount_image_rootfs.sh")
    assert subprocess.run(["bash", "-n", os.path.join(D, "mount_image_rootfs.sh")]).returncode == 0 and "set -euo pipefail" in s
    assert "trap cleanup EXIT" in s and "trap 'exit 130' INT TERM" in s and "OK=1" in s and s.index("OK=1") > s.index("OPTS=$(findmnt") > s.index("mount -o ro,noload \"") and "ONLY what this run mounted" in s
    assert s.index("trap cleanup EXIT") < s.index("qemu-nbd --read-only --connect") and s.index("ATTACHED=1") < s.index("qemu-nbd --read-only --connect")
    assert "--read-only --connect" in s and "blockdev --setro" in s and "mount -o ro,noload" in s and "LARGEST ext4 partition" in s and "nosuid" not in s
    assert "mount -o rw" not in s and "mount -w" not in s and "--connect=\"$DEV\" \"$Q\"" in s and "rm -rf" not in s
