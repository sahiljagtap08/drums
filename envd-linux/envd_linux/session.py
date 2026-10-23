"""Session supervisor: creates, resets and destroys private sessions.

A session = a PID+mount+IPC+UTS namespace (via util-linux `unshare`), an overlayfs on $HOME
(lower: a shared read-only template tree; upper: private to the session), private /tmp and
/dev/shm, and its own Xvfb. Needs root. Stdlib only.

Not done in the spike: a network namespace per session, cgroup limits, pivot_root onto an
overlay of the whole root filesystem, a window manager."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

HOME = "/home/drums"
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SessionError(RuntimeError):
    pass


def _child_pids(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(x) for x in out]


class Supervisor:
    def __init__(self, state_dir, lower_home, python="/usr/bin/python3", xvfb="Xvfb",
                 size="1280x800x24", display_base=90):
        self.state_dir = os.path.abspath(state_dir)
        self.lower = os.path.abspath(lower_home)
        self.python, self.xvfb, self.size, self.display_base = python, xvfb, size, display_base
        self._procs = {}
        self._epochs = {}
        os.makedirs(self.state_dir, exist_ok=True)

    def _dir(self, slot):
        return os.path.join(self.state_dir, f"s{slot}")

    def display(self, slot):
        return self.display_base + slot

    def create(self, slot):
        d = self._dir(slot)
        if slot in self._procs:
            raise SessionError(f"slot {slot} already running")
        shutil.rmtree(d, ignore_errors=True)
        for sub in ("upper", "work", "x11"):
            os.makedirs(os.path.join(d, sub))
        os.makedirs(HOME, exist_ok=True)
        os.makedirs("/tmp/.X11-unix", exist_ok=True)
        try:
            os.chmod("/tmp/.X11-unix", 0o1777)
        except PermissionError:
            pass
        n = self.display(slot)
        host_link = f"/tmp/.X11-unix/X{n}"
        if os.path.lexists(host_link):
            os.unlink(host_link)
        os.symlink(os.path.join(d, "x11", f"X{n}"), host_link)
        ready = os.path.join(d, "ready")
        env = dict(os.environ, PYTHONPATH=PKG_DIR)
        initlog = open(os.path.join(d, "init.log"), "wb")
        proc = subprocess.Popen(
            ["unshare", "--mount", "--pid", "--ipc", "--uts", "--fork", "--kill-child", "--mount-proc",
             self.python, "-m", "envd_linux.session_init",
             "--lower", self.lower, "--upper", os.path.join(d, "upper"), "--work", os.path.join(d, "work"),
             "--home", HOME, "--x11dir", os.path.join(d, "x11"), "--display", str(n), "--size", self.size,
             "--xvfb", self.xvfb, "--ready", ready, "--xlog", os.path.join(d, "xvfb.log")],
            env=env, stdout=initlog, stderr=subprocess.STDOUT, start_new_session=True)
        initlog.close()
        deadline = time.time() + 30
        def is_ready():
            try:
                with open(ready) as f:
                    return f.read() == "ready\n"
            except OSError:
                return False

        while not is_ready():
            if proc.poll() is not None or time.time() > deadline:
                proc.kill()
                with open(os.path.join(d, "init.log")) as f:
                    raise SessionError(f"session {slot} did not start: {f.read()[-400:]}")
            time.sleep(0.05)
        kids = _child_pids(proc.pid)
        if not kids:
            raise SessionError("session init pid not found")
        self._procs[slot] = proc
        self._epochs[slot] = self._epochs.get(slot, 0) + 1
        info = {"epoch": self._epochs[slot], "display": n, "init_pid": kids[0], "home": HOME,
                "unshare_pid": proc.pid, "created": time.time()}
        with open(os.path.join(d, "session.json.tmp"), "w") as f:
            json.dump(info, f)
        os.replace(os.path.join(d, "session.json.tmp"), os.path.join(d, "session.json"))
        return info

    def info(self, slot):
        with open(os.path.join(self._dir(slot), "session.json")) as f:
            return json.load(f)

    def destroy(self, slot, keep_state=False):
        proc = self._procs.pop(slot, None)
        d = self._dir(slot)
        try:
            info = self.info(slot)
            os.kill(info["init_pid"], signal.SIGKILL)  # killing PID 1 of the namespace kills all of it
        except (OSError, FileNotFoundError, KeyError, ValueError):
            pass
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
        link = f"/tmp/.X11-unix/X{self.display(slot)}"
        if os.path.islink(link):
            os.unlink(link)
        if not keep_state:
            shutil.rmtree(d, ignore_errors=True)

    def reset(self, slot):
        """Destroy and recreate: a new upper layer, new X server, all processes gone. The epoch
        is kept increasing so a daemon notices and reconnects."""
        self.destroy(slot)
        return self.create(slot)

    def upper_bytes(self, slot):
        total = 0
        for root, _, files in os.walk(os.path.join(self._dir(slot), "upper")):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total
