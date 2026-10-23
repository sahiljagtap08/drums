"""PID 1 of a session namespace. Runs inside `unshare --mount --pid --ipc --uts --fork
--mount-proc`. Sets up the session's private filesystem view, starts the session's X server
and then reaps children until told to stop.

Private (per session): the overlay on $HOME, /tmp, /dev/shm, the X socket directory, the
process tree, IPC and hostname. Shared with the host: kernel, the rest of the root
filesystem, and (in this spike) the network namespace."""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time


def sh(*cmd):
    subprocess.run(cmd, check=True)


def setup_full_root(a):
    """Give the session a private copy-on-write view of the WHOLE root filesystem and pivot into it.

    Layout: a tmpfs stage holds upper, work and merged; the overlay's lower layers are the
    template tree (on top) and the host's `/`. Writes anywhere (package installs, /etc, /usr)
    land in the tmpfs upper and vanish with the session. The upper is RAM-backed in this spike,
    so private state counts as memory. /proc is new, /dev has only the basic device nodes,
    /sys is bound read-only from the host, /tmp /run /dev/shm are private tmpfs."""
    stage = f"/run/drums-stage-{a.display}"
    os.makedirs(stage, exist_ok=True)
    sh("mount", "-t", "tmpfs", "tmpfs", stage)
    for d in ("upper", "work", "merged"):
        os.makedirs(f"{stage}/{d}")
    lower = f"{a.lower}:/" if a.lower else "/"
    sh("mount", "-t", "overlay", "overlay", "-o",
       f"lowerdir={lower},upperdir={stage}/upper,workdir={stage}/work", f"{stage}/merged")
    m = f"{stage}/merged"
    sh("mount", "-t", "proc", "proc", f"{m}/proc")
    sh("mount", "--rbind", "/sys", f"{m}/sys")
    sh("mount", "-o", "remount,bind,ro", f"{m}/sys")
    sh("mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", f"{m}/dev")
    for node in ("null", "zero", "full", "random", "urandom", "tty"):
        open(f"{m}/dev/{node}", "w").close()
        sh("mount", "--bind", f"/dev/{node}", f"{m}/dev/{node}")
    os.makedirs(f"{m}/dev/shm", exist_ok=True)
    sh("mount", "-t", "tmpfs", "-o", "mode=1777", "tmpfs", f"{m}/dev/shm")
    for link, target in (("fd", "/proc/self/fd"), ("stdin", "/proc/self/fd/0"),
                         ("stdout", "/proc/self/fd/1"), ("stderr", "/proc/self/fd/2")):
        os.symlink(target, f"{m}/dev/{link}")
    sh("mount", "-t", "tmpfs", "-o", "mode=1777", "tmpfs", f"{m}/tmp")
    sh("mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", f"{m}/run")
    os.makedirs(f"{m}/tmp/.X11-unix", exist_ok=True)
    os.chmod(f"{m}/tmp/.X11-unix", 0o1777)
    sh("mount", "--bind", a.x11dir, f"{m}/tmp/.X11-unix")
    os.makedirs(f"{m}/.drums-old", exist_ok=True)
    sh("pivot_root", m, f"{m}/.drums-old")
    os.chdir("/")
    sh("umount", "-l", "/.drums-old")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lower", required=True)
    ap.add_argument("--upper", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--home", required=True)
    ap.add_argument("--x11dir", required=True)
    ap.add_argument("--display", type=int, required=True)
    ap.add_argument("--size", default="1280x800x24")
    ap.add_argument("--xvfb", default="Xvfb")
    ap.add_argument("--ready", required=True)
    ap.add_argument("--xlog", required=True)
    ap.add_argument("--root", choices=["home", "full"], default="home")
    ap.add_argument("--osworld-port", type=int, default=0)
    ap.add_argument("--pkg", default="")
    ap.add_argument("--python", default="/usr/bin/python3")
    a = ap.parse_args(argv)

    socket.sethostname(f"drums-s{a.display}")
    xlog = open(a.xlog, "wb")
    ready = open(a.ready, "w")
    if a.root == "full":
        setup_full_root(a)
    else:
        # Everything under the state directory may live in /tmp on the host, and this namespace is
        # about to get its own /tmp. So open the files we still need, and mount the overlay and the
        # X socket directory, before /tmp is replaced.
        sh("mount", "-t", "overlay", "overlay", "-o",
           f"lowerdir={a.lower},upperdir={a.upper},workdir={a.work}", a.home)
        stash = f"/run/drums-x11-{a.display}"
        os.makedirs(stash, exist_ok=True)
        sh("mount", "--bind", a.x11dir, stash)
        sh("mount", "-t", "tmpfs", "-o", "mode=1777", "tmpfs", "/tmp")
        sh("mount", "-t", "tmpfs", "-o", "mode=1777", "tmpfs", "/dev/shm")
        os.makedirs("/tmp/.X11-unix", exist_ok=True)
        os.chmod("/tmp/.X11-unix", 0o1777)
        sh("mount", "--bind", stash, "/tmp/.X11-unix")
        sh("umount", "-l", stash)

    x = subprocess.Popen([a.xvfb, f":{a.display}", "-screen", "0", a.size, "-nolisten", "tcp",
                          "-nolisten", "local", "-noreset"], stdout=xlog, stderr=subprocess.STDOUT)
    sock = f"/tmp/.X11-unix/X{a.display}"
    deadline = time.time() + 15
    while not os.path.exists(sock):
        if x.poll() is not None or time.time() > deadline:
            print("Xvfb did not come up", file=sys.stderr)
            return 2
        time.sleep(0.05)

    if a.osworld_port:
        env = dict(os.environ, PYTHONPATH=a.pkg, DISPLAY=f":{a.display}", HOME=a.home)
        subprocess.Popen([a.python, "-m", "envd_linux.osworld_server", "--port", str(a.osworld_port),
                          "--display", f":{a.display}"], env=env, stdout=subprocess.DEVNULL,
                         stderr=open("/tmp/osworld-server.log", "wb"))

    stop = {"v": False}

    def on_term(signum, frame):
        stop["v"] = True
        x.terminate()

    signal.signal(signal.SIGTERM, on_term)
    ready.write("ready\n")
    ready.close()
    while True:  # as PID 1: reap orphans; leave when the X server is gone
        try:
            pid, _ = os.waitpid(-1, 0)
        except ChildProcessError:
            break
        except InterruptedError:
            continue
        if pid == x.pid or stop["v"]:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
