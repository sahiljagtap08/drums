"""A LOCAL STUB of the OSWorld guest-server contract, run inside each Drums session.

OSWorld's `DesktopEnv` talks to a small HTTP server inside the guest (port 5000). This module
implements the routes and response shapes that environment layers use, written from the
published API shapes (Apache-2.0 OSWorld, xlang-ai/OSWorld `desktop_env/server/main.py`, read
for the shapes only; no code was copied). It is NOT the real server: on a real OSWorld root
filesystem the real server would run here instead, with pyautogui and AT-SPI, and this stub
exists so the contract plumbing can be tested where those are not installable.

Differences from the real server, on purpose and honestly labelled:
  - /accessibility returns an empty tree (no AT-SPI here); /terminal returns empty output;
  - /screenshot does not draw the mouse cursor;
  - download/upload/open_file/wallpaper/recording/activate/close routes return 501;
  - `GET /__contract` lists what is implemented (not part of OSWorld).

/execute and /setup/launch run arbitrary commands in the session by design (that is the
OSWorld contract). Bind it only on the session's private interface."""

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import png

IMPLEMENTED = ["/platform", "/screenshot", "/screen_size", "/window_size", "/cursor_position", "/execute",
               "/setup/execute", "/setup/launch", "/file", "/list_directory", "/desktop_path", "/run_python",
               "/accessibility", "/terminal"]
UNIMPLEMENTED = ["/setup/upload", "/setup/download_file", "/setup/open_file", "/setup/change_wallpaper",
                 "/setup/activate_window", "/setup/close_window", "/wallpaper", "/start_recording",
                 "/end_recording", "/execute_with_verification", "/setup/execute_with_verification",
                 "/run_bash_script"]


class Guest:
    def __init__(self, display):
        self.display = display
        self.lock = threading.Lock()
        self._dpy = None

    def dpy(self):
        from Xlib import display
        if self._dpy is None:
            self._dpy = display.Display(self.display)
        return self._dpy

    def with_x(self, fn):
        with self.lock:
            try:
                return fn(self.dpy())
            except Exception:
                try:
                    if self._dpy:
                        self._dpy.close()
                finally:
                    self._dpy = None
                raise

    def env(self):
        env = dict(os.environ)
        env["DISPLAY"] = self.display
        env.setdefault("HOME", os.path.expanduser("~"))
        return env


def _expand(cmd):
    return [os.path.expanduser(a) if a.startswith("~/") else a for a in cmd]


def make_handler(guest: Guest):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def _send(self, status, body, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=200):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            ctype = (self.headers.get("Content-Type") or "").split(";")[0]
            if ctype == "application/x-www-form-urlencoded":
                return {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                return None

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def _route(self, method):
            path = urlparse(self.path).path
            try:
                fn = {
                    ("GET", "/platform"): self.platform, ("GET", "/screenshot"): self.screenshot,
                    ("POST", "/screen_size"): self.screen_size, ("POST", "/window_size"): self.window_size,
                    ("GET", "/cursor_position"): self.cursor, ("POST", "/execute"): self.execute,
                    ("POST", "/setup/execute"): self.execute, ("POST", "/setup/launch"): self.launch,
                    ("POST", "/file"): self.file, ("POST", "/list_directory"): self.list_directory,
                    ("POST", "/desktop_path"): self.desktop_path, ("POST", "/run_python"): self.run_python,
                    ("GET", "/accessibility"): self.accessibility, ("GET", "/terminal"): self.terminal,
                    ("GET", "/__contract"): self.contract,
                }.get((method, path))
                if fn is None:
                    if path in UNIMPLEMENTED:
                        return self._json({"status": "error", "message": "not implemented in the stub"}, 501)
                    return self._json({"error": "not found"}, 404)
                fn()
            except BrokenPipeError:
                pass
            except Exception as e:  # keep the guest server alive on a bad request
                self._json({"status": "error", "message": f"{type(e).__name__}: {e}"}, 500)

        # -- routes (shapes follow the OSWorld server)
        def platform(self):
            self._send(200, b"Linux", "text/html; charset=utf-8")

        def contract(self):
            self._json({"stub": True, "implemented": IMPLEMENTED, "unimplemented": UNIMPLEMENTED})

        def screenshot(self):
            from Xlib import X

            def grab(d):
                root = d.screen().root
                g = root.get_geometry()
                img = root.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
                return bytes(img.data), g.width, g.height
            raw, w, h = guest.with_x(grab)
            self._send(200, png.encode_bgrx(raw, w, h), "image/png")

        def screen_size(self):
            def f(d):
                s = d.screen()
                return s.width_in_pixels, s.height_in_pixels
            w, h = guest.with_x(f)
            self._json({"width": w, "height": h})

        def window_size(self):
            data = self._body() or {}
            name = data.get("app_class_name")
            if not name:
                return self._json({"error": "app_class_name is required"}, 400)

            def f(d):
                for w in d.screen().root.query_tree().children:
                    try:
                        cls = w.get_wm_class()
                        if cls and name.lower() in [c.lower() for c in cls]:
                            g = w.get_geometry()
                            return {"width": g.width, "height": g.height}
                    except Exception:
                        continue
                return None
            r = guest.with_x(f)
            self._json(r if r else {"error": "window not found"}, 200 if r else 404)

        def cursor(self):
            def f(d):
                p = d.screen().root.query_pointer()
                return [p.root_x, p.root_y]
            self._json(guest.with_x(f))

        def execute(self):
            data = self._body()
            if data is None:
                return self._json({"status": "error", "message": "invalid JSON"}, 400)
            shell = data.get("shell", False)
            cmd = data.get("command", "" if shell else [])
            if isinstance(cmd, str) and not shell:
                cmd = shlex.split(cmd)
            if not shell:
                cmd = _expand(cmd)
            try:
                r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell, text=True,
                                   timeout=120, env=guest.env())
                self._json({"status": "success", "output": r.stdout, "error": r.stderr, "returncode": r.returncode})
            except Exception as e:
                self._json({"status": "error", "message": str(e)}, 500)

        def launch(self):
            data = self._body() or {}
            shell = data.get("shell", False)
            cmd = data.get("command", "" if shell else [])
            if isinstance(cmd, str) and not shell:
                cmd = shlex.split(cmd)
            if not shell:
                cmd = _expand(cmd)
            try:
                subprocess.Popen(cmd, shell=shell, env=guest.env(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                text = cmd if shell else " ".join(cmd)
                self._send(200, f"{text} launched successfully".encode(), "text/html; charset=utf-8")
            except Exception as e:
                self._json({"status": "error", "message": str(e)}, 500)

        def file(self):
            data = self._body() or {}
            fp = data.get("file_path")
            if not fp:
                return self._json({"error": "file_path is required"}, 400)
            fp = os.path.expandvars(os.path.expanduser(fp))
            if not os.path.isfile(fp):
                return self._json({"error": "File not found"}, 404)
            with open(fp, "rb") as f:
                self._send(200, f.read(), "application/octet-stream")

        def list_directory(self):
            data = self._body() or {}
            if "path" not in data:
                return self._json({"error": "Missing 'path' parameter"}, 400)
            start = data["path"]
            if not os.path.isdir(start):
                return self._json({"error": "The provided path is not a directory"}, 400)

            def walk(d):
                tree = {"type": "directory", "name": os.path.basename(d), "children": []}
                try:
                    for e in os.listdir(d):
                        p = os.path.join(d, e)
                        tree["children"].append(walk(p) if os.path.isdir(p) else {"type": "file", "name": e})
                except OSError as e:
                    return {"error": str(e)}
                return tree
            self._json({"directory_tree": walk(start)})

        def desktop_path(self):
            p = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.exists(p):
                self._json({"desktop_path": p})
            else:
                self._json({"error": "Unsupported operating system or desktop path not found"}, 404)

        def run_python(self):
            data = self._body() or {}
            code = data.get("code")
            if not code:
                return self._json({"status": "error", "message": "Code not supplied!"}, 400)
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
                f.write(code)
            try:
                r = subprocess.run([sys.executable, f.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, timeout=30, env=guest.env())
                msg = r.stdout + (("\n" + r.stderr) if r.stdout and r.stderr else r.stderr)
                self._json({"status": "success" if r.returncode == 0 else "error", "message": msg,
                            "need_more": False, "returncode": r.returncode})
            finally:
                os.unlink(f.name)

        def accessibility(self):
            self._json({"AT": "<desktop-frame/>", "stub": "no AT-SPI in this guest"})

        def terminal(self):
            self._json({"output": "", "status": "success"})
    return H


def serve(host, port, display):
    srv = ThreadingHTTPServer((host, port), make_handler(Guest(display)))
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--display", required=True)
    a = ap.parse_args(argv)
    serve(a.host, a.port, a.display).serve_forever()


if __name__ == "__main__":
    main()
