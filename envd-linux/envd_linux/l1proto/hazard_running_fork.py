"""HAZARD TEST for mechanism F (docs/l1-shared-runtime-design.md section 8, measurement e): what breaks when a RUNNING server
(one that already holds an X connection, a D-Bus connection, a listening socket and live threads) is forked to make a
second session.

Run with the kit's Python 3.12 (has python-xlib and gi). Starts its own Xvfb on :99 and a private session bus, forks, and
reports one JSON object. Touches no other host state; kills what it started.

    /opt/drums-osworld-real/venv/bin/python -m envd_linux.l1proto.hazard_running_fork"""

import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time

DISPLAY = ":99"


def child_json(fn):
    """Run fn() in a forked child; return its JSON-able result (or the crash)."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        try:
            out = fn()
        except BaseException as e:
            out = {"crashed": f"{type(e).__name__}: {e}"}
        os.write(w, json.dumps(out).encode())
        os._exit(0)
    os.close(w)
    return pid, r


def collect(pid, r, timeout):
    end = time.time() + timeout
    data = b""
    while time.time() < end:
        ready, _, _ = select.select([r], [], [], 0.2)
        if ready:
            chunk = os.read(r, 65536)
            if not chunk:
                break
            data += chunk
    done, _ = os.waitpid(pid, os.WNOHANG)
    if not done:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        return {"hung_and_killed_after_s": timeout, "partial": data.decode(errors="replace")}
    return json.loads(data) if data else {"no_output": True}


class Res(dict):
    """Each finding is printed as soon as it exists (a hung step must not lose the earlier ones)."""

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        print(json.dumps({k: v}), flush=True)


def run_with_timeout(fn, seconds):
    """Run fn in a thread; report a hang instead of blocking the whole test."""
    box = {}
    t = threading.Thread(target=lambda: box.update(v=fn()), daemon=True)
    t.start()
    t.join(seconds)
    return box["v"] if "v" in box else {"hung_after_s": seconds}


def main():
    signal.alarm(120)  # global watchdog
    res = Res()
    xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "800x600x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bus = subprocess.run(["dbus-daemon", "--session", "--fork", "--print-address=1", "--print-pid=1"],
                         capture_output=True, text=True, check=True).stdout.split()
    os.environ["DISPLAY"], os.environ["DBUS_SESSION_BUS_ADDRESS"] = DISPLAY, bus[0]
    bus_pid = int(bus[1])
    try:
        time.sleep(1)
        from Xlib import display as xdisplay, error as xerror
        from gi.repository import Gio, GLib

        # ---- the "running server": X connection + window, D-Bus connection, listening socket, a thread holding a lock
        d = xdisplay.Display(DISPLAY)
        win = d.screen().root.create_window(0, 0, 100, 100, 0, d.screen().root_depth)
        win.set_wm_name("PARENT-SESSION-WINDOW")
        win.map()
        d.sync()
        gbus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res["dbus_unique_name_parent"] = gbus.get_unique_name()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        port = listener.getsockname()[1]
        lock = threading.Lock()
        started = threading.Event()

        def holder():
            with lock:
                started.set()
                time.sleep(600)

        threading.Thread(target=holder, daemon=True).start()
        started.wait()

        # 1. shared X server state: a forked "second session" sees the first session's windows
        def see_windows():
            names = []
            for w in d.screen().root.query_tree().children:
                try:
                    names.append(w.get_wm_name())
                except Exception:
                    pass
            return {"windows_visible_to_child": names}
        pid, r = child_json(see_windows)
        res["1_shared_x_server_state"] = collect(pid, r, 10)

        # 2. shared X connection: parent and child issue requests on the same socket at the same time
        def hammer():
            ok = err = 0
            kinds = {}
            end = time.time() + 3
            while time.time() < end:
                try:
                    d.screen().root.get_geometry()
                    ok += 1
                except BaseException as e:
                    err += 1
                    kinds[type(e).__name__] = kinds.get(type(e).__name__, 0) + 1
                    if err > 50:
                        break
            return {"ok": ok, "errors": err, "error_kinds": kinds}
        pid, r = child_json(hammer)
        res["2_shared_x_connection_parent"] = run_with_timeout(hammer, 12)
        res["2_shared_x_connection_child"] = collect(pid, r, 15)

        # 3. shared listening socket: both processes accept from the same queue
        def accept_some():
            n = 0
            listener.settimeout(0.5)
            end = time.time() + 4
            while time.time() < end:
                try:
                    c, _ = listener.accept()
                    c.close()
                    n += 1
                except socket.timeout:
                    pass
            return {"accepted": n}
        pid, r = child_json(accept_some)
        clients = []
        for _ in range(100):
            clients.append(socket.create_connection(("127.0.0.1", port)))
        res["3_shared_listening_socket_parent"] = accept_some()
        res["3_shared_listening_socket_child"] = collect(pid, r, 10)
        res["3_connections_made"] = 100
        for c in clients:
            c.close()

        # 4. D-Bus: same unique name in both; the child's GDBus connection has no worker thread
        def dbus_call():
            t0 = time.time()
            try:
                v = gbus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetId",
                                   None, None, Gio.DBusCallFlags.NONE, 3000, None)
                return {"unique_name": gbus.get_unique_name(), "call": "ok", "s": time.time() - t0}
            except GLib.Error as e:
                return {"unique_name": gbus.get_unique_name(), "call": f"failed: {e.message[:80]}", "s": time.time() - t0}
        pid, r = child_json(dbus_call)
        res["4_dbus_parent"] = dbus_call()
        res["4_dbus_child"] = collect(pid, r, 12)

        # 5. a lock held by another thread at fork time is held forever in the child (Flask's threaded server
        #    has such threads)
        def try_lock():
            return {"acquired_in_child": lock.acquire(timeout=2)}
        pid, r = child_json(try_lock)
        res["5_lock_held_by_a_thread_at_fork"] = collect(pid, r, 8)
    finally:
        xvfb.kill()
        xvfb.wait()
        try:
            os.kill(bus_pid, signal.SIGTERM)
        except OSError:
            pass
    print(json.dumps({"_final": dict(res)}), flush=True)


if __name__ == "__main__":
    sys.exit(main())
