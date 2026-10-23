"""X11 backend: screenshots (XGetImage), input (XTEST), windows (X tree), and launch/kill
inside the session's namespaces (nsenter). Needs python-xlib.

Reads <state>/s<slot>/session.json (written by the supervisor) on each use so it follows a
session that was destroyed and recreated by a reset."""

import json
import os
import signal
import subprocess
import threading
import time

from .backend import Backend, BackendError

PROTECTED_NAMES = {"Xvfb", "Xorg", "python3", "python", "dbus-daemon"}  # session init, X server, bus


def _xlib():
    try:
        import Xlib  # noqa: F401
        from Xlib import X, XK, display
        from Xlib.ext import xtest
    except ImportError as e:  # pragma: no cover - environment dependent
        raise BackendError("python-xlib is not installed") from e
    return X, XK, display, xtest


def read_state(state_dir, slot):
    with open(os.path.join(state_dir, f"s{slot}", "session.json")) as f:
        return json.load(f)


def pidns_members(init_pid):
    """Host pids that live in the same PID namespace as init_pid (init included)."""
    try:
        target = os.readlink(f"/proc/{init_pid}/ns/pid")
    except OSError:
        return []
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            if os.readlink(f"/proc/{name}/ns/pid") == target:
                out.append(int(name))
        except OSError:
            continue
    return out


def comm_of(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


class XBackend(Backend):
    def __init__(self, state_dir, slot):
        self.state_dir, self.slot = state_dir, slot
        self._lock = threading.RLock()
        self._dpy = None
        self._epoch = None
        self._spawned = []  # Popen objects started through /launch
        self._ok = True

    # -- connection
    def _display(self):
        X, XK, display, xtest = _xlib()
        st = read_state(self.state_dir, self.slot)
        if self._dpy is not None and st["epoch"] != self._epoch:
            self._close()
        if self._dpy is None:
            try:
                self._dpy = display.Display(f":{st['display']}")
            except Exception as e:
                raise BackendError(f"cannot open display :{st['display']}: {e}") from e
            self._epoch = st["epoch"]
        return self._dpy, st

    def _close(self):
        try:
            if self._dpy is not None:
                self._dpy.close()
        except Exception:
            pass
        self._dpy = None

    def _with_display(self, fn):
        with self._lock:
            try:
                dpy, st = self._display()
                return fn(dpy, st)
            except BackendError:
                raise
            except Exception as e:
                self._close()
                raise BackendError(f"{type(e).__name__}: {e}") from e

    # -- Backend
    def screen_size(self):
        def f(dpy, st):
            g = dpy.screen().root.get_geometry()
            return g.width, g.height
        try:
            return self._with_display(f)
        except BackendError:
            self._ok = False
            return (0, 0)

    def capture_ok(self):
        try:
            self._with_display(lambda dpy, st: dpy.screen().root.get_geometry())
            return True
        except BackendError:
            return False

    input_ok = capture_ok

    def capture(self, region):
        X, XK, display, xtest = _xlib()

        def f(dpy, st):
            root = dpy.screen().root
            g = root.get_geometry()
            x, y, w, h = 0, 0, g.width, g.height
            if region:
                rx, ry, rw, rh = region
                x, y = min(int(rx), g.width - 1), min(int(ry), g.height - 1)
                w, h = max(1, min(int(rw + (rx - x)), g.width - x)), max(1, min(int(rh + (ry - y)), g.height - y))
            img = root.get_image(x, y, w, h, X.ZPixmap, 0xFFFFFFFF)
            data = bytes(img.data)
            if len(data) != w * h * 4:
                raise BackendError(f"unexpected image size {len(data)} for {w}x{h} (depth {img.depth})")
            return data, w, h
        return self._with_display(f)

    # -- input
    def perform(self, spec):
        X, XK, display, xtest = _xlib()

        def f(dpy, st):
            t = spec["type"]
            mods = [self._mod_keycode(dpy, XK, m) for m in spec.get("modifiers") or []]
            for kc in mods:
                xtest.fake_input(dpy, X.KeyPress, kc)
            try:
                if t in ("click", "dblclick"):
                    xtest.fake_input(dpy, X.MotionNotify, False, x=int(spec["x"]), y=int(spec["y"]))
                    btn = 3 if spec.get("button") == "right" else 1
                    for _ in range(2 if t == "dblclick" else 1):
                        xtest.fake_input(dpy, X.ButtonPress, btn)
                        xtest.fake_input(dpy, X.ButtonRelease, btn)
                elif t == "type":
                    for ch in spec["text"]:
                        self._type_char(dpy, X, XK, xtest, ch)
                elif t == "key":
                    ks = self._keysym_for_name(XK, spec["key"].lower())
                    kc = dpy.keysym_to_keycode(ks)
                    if not kc:
                        raise BackendError(f"no keycode for {spec['key']}")
                    xtest.fake_input(dpy, X.KeyPress, kc)
                    xtest.fake_input(dpy, X.KeyRelease, kc)
                elif t == "scroll":
                    for delta, neg, pos in ((spec.get("dy"), 4, 5), (spec.get("dx"), 6, 7)):
                        if delta:
                            btn = pos if delta > 0 else neg  # assumption: positive = down/right
                            for _ in range(max(1, int(round(abs(delta))))):
                                xtest.fake_input(dpy, X.ButtonPress, btn)
                                xtest.fake_input(dpy, X.ButtonRelease, btn)
            finally:
                for kc in reversed(mods):
                    xtest.fake_input(dpy, X.KeyRelease, kc)
            dpy.sync()
        self._with_display(f)

    @staticmethod
    def _mod_keycode(dpy, XK, name):
        ks = {"cmd": XK.XK_Super_L, "shift": XK.XK_Shift_L, "alt": XK.XK_Alt_L, "ctrl": XK.XK_Control_L}[name]
        return dpy.keysym_to_keycode(ks)

    @staticmethod
    def _keysym_for_name(XK, name):
        special = {"return": XK.XK_Return, "tab": XK.XK_Tab, "space": XK.XK_space, "delete": XK.XK_BackSpace,
                   "escape": XK.XK_Escape, "left": XK.XK_Left, "right": XK.XK_Right, "down": XK.XK_Down,
                   "up": XK.XK_Up}
        if name in special:
            return special[name]
        if len(name) == 1:
            return ord(name)
        if name.startswith("f") and name[1:].isdigit():
            return getattr(XK, f"XK_F{int(name[1:])}")
        raise BackendError(f"unknown key {name}")

    @staticmethod
    def _type_char(dpy, X, XK, xtest, ch):
        if ch == "\n":
            ks = XK.XK_Return
        elif ch == "\t":
            ks = XK.XK_Tab
        elif 0x20 <= ord(ch) <= 0x7E:
            ks = ord(ch)
        else:
            raise BackendError(f"character {ch!r} is outside the spike's ASCII typing support")
        found = list(dpy.keysym_to_keycodes(ks))
        if not found:
            raise BackendError(f"no keycode for {ch!r}")
        kc, index = found[0]
        shift = bool(index & 1)
        if shift:
            xtest.fake_input(dpy, X.KeyPress, dpy.keysym_to_keycode(XK.XK_Shift_L))
        xtest.fake_input(dpy, X.KeyPress, kc)
        xtest.fake_input(dpy, X.KeyRelease, kc)
        if shift:
            xtest.fake_input(dpy, X.KeyRelease, dpy.keysym_to_keycode(XK.XK_Shift_L))

    # -- processes
    def launch(self, spec):
        st = read_state(self.state_dir, self.slot)
        env = dict(os.environ)
        env.update({"DISPLAY": f":{st['display']}", "HOME": st["home"]})
        env.update(spec.get("env") or {})
        log = open(os.path.join(self.state_dir, f"s{self.slot}", f"proc-{int(time.time()*1000)}.log"), "wb")
        try:
            p = subprocess.Popen(
                ["nsenter", "-t", str(st["init_pid"]), "--mount", "--pid", "--ipc", "--uts", "--",
                 spec["path"], *spec["args"]],
                env=env, cwd="/", start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT)
        except OSError as e:
            raise BackendError(str(e)) from e
        finally:
            log.close()
        self._spawned.append(p)
        return p.pid

    def _targets(self, st):
        members = set(pidns_members(st["init_pid"]))
        protected = {st["init_pid"]} | {p for p in members if comm_of(p) in {"Xvfb", "Xorg"}}
        return members, protected

    def kill(self, spec):
        st = read_state(self.state_dir, self.slot)
        members, protected = self._targets(st)
        spawned = {p.pid for p in self._spawned if p.poll() is None}
        if spec.get("pid"):
            pid = spec["pid"]
            if pid in protected:
                return ("refused", "protected session process")
            if pid not in members and pid not in spawned:
                return ("refused", "pid is not in this session")
            targets = [pid]
        else:
            targets = sorted((members - protected) | spawned)
        groups = []
        for p in targets:
            try:
                if p in spawned:
                    os.killpg(os.getpgid(p), signal.SIGTERM)
                    groups.append(p)
                else:
                    os.kill(p, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.time() + 2.0
        while time.time() < deadline and any(os.path.exists(f"/proc/{p}") and comm_of(p) != "" for p in targets):
            time.sleep(0.05)
        for p in targets:
            if os.path.exists(f"/proc/{p}"):
                try:
                    os.kill(p, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        # also reap session members that remain (children of nsenter inside the namespace)
        for p in sorted(set(pidns_members(st["init_pid"])) - protected):
            if spec.get("all"):
                try:
                    os.kill(p, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for pr in self._spawned:
            pr.poll()
        self._spawned = [pr for pr in self._spawned if pr.returncode is None]
        return ("signalled", targets)

    def windows(self):
        def f(dpy, st):
            root = dpy.screen().root
            out = []
            for w in root.query_tree().children:
                try:
                    a = w.get_attributes()
                    if a.map_state != 2:  # IsViewable
                        continue
                    g = w.get_geometry()
                    name = w.get_wm_name() or ""
                    out.append({"title": name if isinstance(name, str) else name.decode("latin1", "replace"),
                                "x": g.x, "y": g.y, "w": g.width, "h": g.height})
                except Exception:
                    continue
            return out
        return self._with_display(f)
