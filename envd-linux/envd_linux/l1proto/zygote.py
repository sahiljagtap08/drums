"""The zygote: a long-lived process that has already imported the guest server's heavy dependencies and forks one process
per session. PROTOTYPE of mechanism F (docs/l1-shared-runtime-design.md).

Each fork: child A calls unshare(NEWNS|NEWPID|NEWIPC|NEWUTS[|NEWNET]) and forks B, which is PID 1 of the new PID
namespace and runs `session_init.main(argv)` with every preloaded module already mapped copy-on-write. Inside, session_init
starts Xvfb, D-Bus, AT-SPI and openbox by exec, and runs the real OSWorld server as a FORK of itself (server-mode "fork").

Fork-safe point: before `import pyautogui`, which connects to X at import time. Everything else the server imports is
imported here, with no DISPLAY and no D-Bus (checked: flask, lxml, requests, PIL, tkinter, gi, pyatspi, Xlib import cleanly
without them).

Protocol: one JSON object per connection on a unix socket: {argv, env, log, net}; the reply is {"pid": <host pid of B>}.
Run with the kit's Python 3.12:  python -m envd_linux.l1proto.zygote --sock PATH --kit KIT --tag drums:RUN
The `--tag` and the module name on the command line are the ownership record the sweep matches (forked sessions share
the zygote's command line)."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time

PRELOAD = ["json", "flask", "werkzeug.serving", "jinja2", "lxml.etree", "requests", "PIL.Image", "PIL.ImageGrab",
           "tkinter", "gi", "pyatspi", "pyscreeze", "Xlib.display", "Xlib.X", "psutil", "ctypes"]
NEWFLAGS = lambda net: (os.CLONE_NEWNS | os.CLONE_NEWPID | os.CLONE_NEWIPC | os.CLONE_NEWUTS | (os.CLONE_NEWNET if net else 0))


def preload():
    done, failed = [], {}
    for m in PRELOAD:
        try:
            __import__(m)
            done.append(m)
        except BaseException as e:  # pragma: no cover - reported, not hidden
            failed[m] = f"{type(e).__name__}: {e}"
    for m in filter(None, os.environ.get("DRUMS_ZYGOTE_EXTRA", "").split(",")):  # hygiene test: a library with global state
        try:
            __import__(m)
            done.append(m)
        except BaseException as e:  # pragma: no cover
            failed[m] = f"{type(e).__name__}: {e}"
    import envd_linux.session_init  # noqa: F401  (B runs its main)
    return done, failed


def spawn(req):
    r, w = os.pipe()
    a = os.fork()
    if a == 0:
        try:
            os.close(r)
            os.setsid()
            os.unshare(NEWFLAGS(req.get("net", True)))
            b = os.fork()
            if b == 0:
                os.close(w)
                os.closerange(3, 4096)  # the zygote's listening socket and friends must not leak into a session
                fd = os.open(req["log"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
                os.dup2(fd, 1)
                os.dup2(fd, 2)
                os.close(fd)
                subprocess.run(["mount", "--make-rprivate", "/"], check=True)
                os.environ.clear()
                os.environ.update(req["env"])
                import envd_linux.session_init as si
                os._exit(si.main(req["argv"]) or 0)
            os.write(w, str(b).encode())
            os._exit(0)
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(1)
    os.close(w)
    data = os.read(r, 32)
    os.close(r)
    os.waitpid(a, 0)
    if not data:
        raise RuntimeError("zygote fork failed")
    return int(data)


def serve(sock_path, ready_line=True):
    t0 = time.time()
    done, failed = preload()
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(8)
    if ready_line:
        print(json.dumps({"ready": True, "preloaded": done, "failed": failed, "preload_s": round(time.time() - t0, 3)}),
              flush=True)
    while True:
        conn, _ = srv.accept()
        try:
            req = json.loads(conn.makefile("r").readline())
            if req.get("op") == "quit":
                conn.sendall(b'{"ok": true}\n')
                return
            if req.get("op") == "probe":  # state-hygiene probe: what a fresh fork inherits
                conn.sendall((json.dumps(probe_child()) + "\n").encode())
                continue
            conn.sendall((json.dumps({"pid": spawn(req)}) + "\n").encode())
        except BaseException as e:
            conn.sendall((json.dumps({"error": f"{type(e).__name__}: {e}"}) + "\n").encode())
        finally:
            conn.close()


def probe_child():
    """Fork a child and report what it inherited: pid, random state, urandom, uuid, numpy global RNG."""
    import random
    import uuid
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        out = {"pid": os.getpid(), "random": random.random(), "urandom": os.urandom(4).hex(),
               "uuid4": str(uuid.uuid4())}
        try:
            import numpy
            out["numpy_global"] = float(numpy.random.rand())
        except ImportError:
            out["numpy_global"] = None
        os.write(w, json.dumps(out).encode())
        os._exit(0)
    os.close(w)
    data = b""
    while True:
        chunk = os.read(r, 4096)
        if not chunk:
            break
        data += chunk
    os.close(r)
    os.waitpid(pid, 0)
    return json.loads(data)


class ZygoteClient:
    """Starts a zygote under the kit's Python and asks it for sessions. Owns the zygote process."""

    def __init__(self, kit, sock, tag, env=None, python=None):
        self.sock = sock
        self.python = python or os.path.join(kit, "venv", "bin", "python")
        pkg = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.proc = subprocess.Popen(
            [self.python, "-m", "envd_linux.l1proto.zygote", "--sock", sock, "--kit", kit, "--tag", tag],
            env=dict(env or os.environ, PYTHONPATH=pkg), stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.hello = json.loads(self.proc.stdout.readline())

    def _ask(self, req):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock)
        s.sendall((json.dumps(req) + "\n").encode())
        data = s.makefile("r").readline()
        s.close()
        out = json.loads(data)
        if "error" in out:
            raise RuntimeError(out["error"])
        return out

    def spawn(self, argv, env, log, net=True):
        return self._ask({"argv": argv, "env": env, "log": log, "net": net})["pid"]

    def probe(self):
        return self._ask({"op": "probe"})

    def close(self):
        try:
            self._ask({"op": "quit"})
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", required=True)
    ap.add_argument("--kit", required=True)
    ap.add_argument("--tag", default="")
    a = ap.parse_args(argv)
    serve(a.sock)


if __name__ == "__main__":
    sys.exit(main())
