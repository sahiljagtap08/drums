"""PID 1 of a session namespace. Runs inside `unshare --mount --pid --ipc --uts --fork
--mount-proc`. Sets up the session's private filesystem view, starts the session's X server
and then reaps children until told to stop.

Private (per session): the overlay on $HOME, /tmp, /dev/shm, the X socket directory, the
process tree, IPC and hostname. Shared with the host: kernel, the rest of the root
filesystem, and (in this spike) the network namespace."""

import argparse
import json
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
    upper, work = f"{stage}/upper", f"{stage}/work"
    if a.criu_friendly:  # the upper must live where the host can copy it (a checkpoint of the processes is not a checkpoint of the files)
        upper, work = a.upper, a.work
    opts = f"lowerdir={lower},upperdir={upper},workdir={work}"
    if a.criu_friendly:
        opts += ",redirect_dir=on"  # without it renaming a directory that lives in a lower layer fails with EXDEV (found on a restored task state)
    sh("mount", "-t", "overlay", "overlay", "-o", opts, f"{stage}/merged")
    m = f"{stage}/merged"
    sh("mount", "-t", "proc", "proc", f"{m}/proc")
    sh("mount", "--rbind", "/sys", f"{m}/sys")
    sh("mount", "-o", "remount,bind,ro", f"{m}/sys")
    sh("mount", "-t", "tmpfs", "-o", "mode=755", "tmpfs", f"{m}/dev")
    for node in ("null", "zero", "full", "random", "urandom", "tty"):
        open(f"{m}/dev/{node}", "w").close()
        sh("mount", "--bind", f"/dev/{node}", f"{m}/dev/{node}")
    # a private devpts instance, so terminals can open a PTY (gnome-terminal: "Failed to open PTY" without it)
    os.makedirs(f"{m}/dev/pts", exist_ok=True)
    sh("mount", "-t", "devpts", "-o", "newinstance,ptmxmode=0666,mode=0620,gid=5", "devpts", f"{m}/dev/pts")
    os.symlink("pts/ptmx", f"{m}/dev/ptmx")
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


TIMING = {"f": None}


def mark(name, **kw):
    if TIMING["f"]:
        TIMING["f"].write(json.dumps(dict(kw, mark=name, t=time.time())) + "\n")
        TIMING["f"].flush()


def http_ok(url, timeout=0.5):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200, r.read()
    except Exception:
        return False, b""


def lo_up():
    """Bring the loopback interface up (a fresh network namespace starts with it down; the session's own readiness
    probes talk to 127.0.0.1 before the supervisor has configured the veth)."""
    import fcntl
    import struct
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        fcntl.ioctl(s, 0x8914, struct.pack("16sH14x", b"lo", 0x1 | 0x40))  # SIOCSIFFLAGS: IFF_UP | IFF_RUNNING
    except OSError:
        pass
    finally:
        s.close()


def start_server(a, env, kit):
    """The real OSWorld server, one of three ways (the arms of the L1-F prototype)."""
    log = open("/tmp/osworld-real-server.log", "wb")
    server_dir = f"{kit}/server"
    py = f"{kit}/venv/bin/python"
    entry = "import main; main.app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)"
    if a.criu_friendly:  # signal handlers the per-sibling reseed of a restored session uses (clone.py); installed before any checkpoint
        from .clone import SERVER_HOOK
        entry = SERVER_HOOK + entry
    if a.server_mode == "asshipped":  # `python main.py`: OSWorld's debug reloader, so two interpreters
        return subprocess.Popen([py, "main.py"], cwd=server_dir, env=env, stdout=log, stderr=subprocess.STDOUT)
    if a.server_mode == "module":  # fresh interpreter, same entry as the fork arm
        return subprocess.Popen([py, "-c", entry], cwd=server_dir, env=env, stdout=log, stderr=subprocess.STDOUT)
    pid = os.fork()  # "fork": this process was itself forked from the zygote, so flask, lxml, ... are already mapped
    if pid == 0:
        try:
            os.dup2(log.fileno(), 1)
            os.dup2(log.fileno(), 2)
            os.environ.update(env)
            os.chdir(server_dir)
            sys.path.insert(0, server_dir)
            sys.argv = ["main.py"]
            import main  # pyautogui connects to X here: after the fork, to THIS session's display
            main.app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
        finally:
            os._exit(1)
    return pid


def start_chromium(a, env):
    html = "data:text/html,<title>drums-ready</title><h1>ready</h1>"
    return subprocess.Popen(
        [a.chrome, "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={a.home}/profile", "--remote-debugging-port=9222", "--window-size=1280,800", html],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_real_desktop(a):
    """Session bus, AT-SPI, a window manager, then the public OSWorld server (main.py, unmodified).

    The server is Flask on port 5000; `python`/`python3` on PATH are the kit's virtualenv python, which has
    pyautogui, as OSWorld tasks expect (`python -c "import pyautogui ..."`)."""
    kit = a.osworld_real
    runtime = "/run/drums-xdg"
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    env = dict(os.environ, DISPLAY=f":{a.display}", HOME=a.home, LANG="C.UTF-8", LC_ALL="C.UTF-8",
               XDG_RUNTIME_DIR=runtime, PATH=f"{kit}/bin:" + os.environ.get("PATH", ""),
               GTK_MODULES="gail:atk-bridge", NO_AT_BRIDGE="0", ACCESSIBILITY_ENABLED="1")
    open(os.path.join(a.home, ".Xauthority"), "a").close()  # python-xlib insists on the file existing
    bus = subprocess.run(["dbus-daemon", "--session", "--fork", "--print-address=1", "--print-pid=1"],
                         capture_output=True, text=True, env=env, check=True).stdout.split()
    env["DBUS_SESSION_BUS_ADDRESS"] = bus[0]
    quiet = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    subprocess.Popen(["/usr/libexec/at-spi-bus-launcher", "--launch-immediately"], **quiet)
    subprocess.Popen(["openbox"], **quiet)
    time.sleep(0.5)
    lo_up()
    mark("desktop_stack_started")
    start_server(a, env, kit)
    mark("server_spawned")
    deadline = time.time() + 60
    while time.time() < deadline and not http_ok("http://127.0.0.1:5000/platform")[0]:
        time.sleep(0.02)
    mark("server_ready")
    if a.workload == "chromium":
        start_chromium(a, env)
        mark("chromium_spawned")
        deadline = time.time() + 60
        while time.time() < deadline:
            ok, body = http_ok("http://127.0.0.1:9222/json/list")
            if ok and b"drums-ready" in body:
                break
            time.sleep(0.02)
        mark("chromium_ready")


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
    ap.add_argument("--osworld-real", default="")  # kit dir (realkit.py): run the REAL OSWorld server instead of the stub
    ap.add_argument("--exec-systemd", action="store_true")  # EXPERIMENT: exec systemd as PID 1 after the root setup
    # PROTOTYPE of L1 mechanism F (docs/l1-shared-runtime-design.md section 8)
    ap.add_argument("--server-mode", choices=["asshipped", "module", "fork"], default="asshipped")
    ap.add_argument("--workload", choices=["", "chromium"], default="")
    ap.add_argument("--timing", default="")  # jsonl of wall-clock marks, opened before the pivot
    ap.add_argument("--chrome", default="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    # PROTOTYPE of mechanism K (docs/l1-shared-runtime-design.md section 12): page merging across sessions
    ap.add_argument("--ksm", action="store_true", help="prctl(PR_SET_MEMORY_MERGE): the whole session's memory may be merged by KSM")
    ap.add_argument("--no-aslr", action="store_true", help="personality(ADDR_NO_RANDOMIZE) for every process exec'd in this session")
    # PROTOTYPE of mechanism C (docs/l1-shared-runtime-design.md section 14): a session whose processes CRIU can dump. After the pivot the init
    # re-execs itself (its interpreter mapping belonged to the detached old root) and drops every fd that points outside the new root.
    ap.add_argument("--criu-friendly", action="store_true")
    ap.add_argument("--stage2", action="store_true")
    ap.add_argument("--ready-fd", type=int, default=-1)
    ap.add_argument("--cgroup-procs", action="append", default=[])  # cgroup.procs files to join, before forking anything
    ap.add_argument("--tag", default="")  # ownership tag, read by the sweep from /proc/<pid>/cmdline
    argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv)

    for f in a.cgroup_procs:  # first act: join the session's cgroup ("0" = self), so every descendant inherits it
        with open(f, "w") as fh:
            fh.write("0")
    if a.no_aslr or a.ksm:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if a.no_aslr:
            cur = libc.personality(0xFFFFFFFF)
            libc.personality(cur | 0x0040000)  # ADDR_NO_RANDOMIZE: inherited across fork and exec
        if a.ksm:
            libc.prctl(67, 1, 0, 0, 0)  # PR_SET_MEMORY_MERGE
    if a.timing and not a.stage2:
        TIMING["f"] = open(a.timing, "a")
    mark("init_start")
    if a.criu_friendly and not a.stage2:
        import ctypes
        ctypes.CDLL(None).prctl(1, 0, 0, 0, 0)  # PR_SET_PDEATHSIG off: `unshare --kill-child` armed SIGKILL-on-parent-exit, which a restore would trigger at once
        try:
            os.setsid()  # CRIU: a session leader must lie inside the dumped pid namespace
        except OSError:
            pass
    if a.stage2:
        for fd in range(3):  # stdio onto the new root, never the supervisor's files
            os.close(fd)
        os.open("/dev/null", os.O_RDONLY)
        log = os.open("/tmp/init.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.dup2(log, 1)
        os.dup2(log, 2)
        for name in os.listdir("/proc/self/fd"):
            if int(name) > 2 and int(name) not in (a.ready_fd, log):
                try:
                    os.close(int(name))
                except OSError:
                    pass
        xlog = open("/tmp/xvfb.log", "wb")
        ready = os.fdopen(a.ready_fd, "w")
    else:
        socket.sethostname(f"drums-s{a.display}")
        xlog = open(a.xlog, "wb")
        ready = open(a.ready, "w")
    if a.root == "full" and not a.stage2:
        setup_full_root(a)
        try:  # sudo, getent and friends wait ~24 s for a DNS answer when the hostname does not resolve (the session has no DNS): found as the cost of a task setup
            with open("/etc/hosts", "a") as hf:
                hf.write(f"127.0.1.1 drums-s{a.display}\n")
        except OSError:
            pass
        if a.criu_friendly:
            os.set_inheritable(ready.fileno(), True)
            os.execv(sys.executable, [sys.executable, "-m", "envd_linux.session_init", *argv, "--stage2", "--ready-fd", str(ready.fileno())])
    elif a.stage2:
        pass
    if a.root != "full":
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

    if a.exec_systemd:
        # experiment (docs/gnome-namespace-investigation.md): become systemd instead of starting Xvfb
        ready.write("ready\n")
        ready.close()
        os.execve("/lib/systemd/systemd", ["/lib/systemd/systemd", "--system", "--unit=multi-user.target",
                                           "--log-target=console", "--log-level=info"],
                  dict(os.environ, container="drums"))
    x = subprocess.Popen([a.xvfb, f":{a.display}", "-screen", "0", a.size, "-nolisten", "tcp",
                          "-nolisten", "local", "-noreset"], stdout=xlog, stderr=subprocess.STDOUT)
    sock = f"/tmp/.X11-unix/X{a.display}"
    deadline = time.time() + 15
    while not os.path.exists(sock):
        if x.poll() is not None or time.time() > deadline:
            print("Xvfb did not come up", file=sys.stderr)
            return 2
        time.sleep(0.05)

    if a.osworld_real:
        start_real_desktop(a)
    elif a.osworld_port:
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
