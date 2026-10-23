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
    a = ap.parse_args(argv)

    socket.sethostname(f"drums-s{a.display}")
    # Everything under the state directory may live in /tmp on the host, and this namespace is
    # about to get its own /tmp. So open the files we still need, and mount the overlay and the
    # X socket directory, before /tmp is replaced.
    xlog = open(a.xlog, "wb")
    ready = open(a.ready, "w")
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
