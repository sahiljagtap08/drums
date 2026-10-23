"""Minimal client of the OSWorld guest-server contract (what DesktopEnv's controller does over
HTTP), used by tests and the harness. Our own code, written from the contract's shapes."""

import json
import urllib.error
import urllib.request


class GuestError(RuntimeError):
    pass


class GuestClient:
    def __init__(self, host, port=5000, timeout=30):
        self.base, self.timeout = f"http://{host}:{port}", timeout

    def _req(self, method, path, body=None, form=None):
        data, headers = None, {}
        if body is not None:
            data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
        elif form is not None:
            from urllib.parse import urlencode
            data, headers = urlencode(form).encode(), {"Content-Type": "application/x-www-form-urlencoded"}
        elif method == "POST":
            data, headers = b"{}", {"Content-Type": "application/json"}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except OSError as e:
            raise GuestError(str(e)) from e

    def platform(self):
        return self._req("GET", "/platform")[1].decode()

    def screenshot(self):
        st, b = self._req("GET", "/screenshot")
        if st != 200:
            raise GuestError(f"screenshot http {st}")
        return b

    def screen_size(self):
        return json.loads(self._req("POST", "/screen_size")[1])

    def cursor(self):
        return json.loads(self._req("GET", "/cursor_position")[1])

    def execute(self, command, shell=False):
        st, b = self._req("POST", "/execute", body={"command": command, "shell": shell})
        out = json.loads(b)
        if st != 200:
            raise GuestError(out.get("message", f"http {st}"))
        return out

    def launch(self, command, shell=False):
        st, b = self._req("POST", "/setup/launch", body={"command": command, "shell": shell})
        if st != 200:
            raise GuestError(b.decode()[:200])
        return b.decode()

    def file(self, path):
        st, b = self._req("POST", "/file", form={"file_path": path})
        return b if st == 200 else None

    def list_dir(self, path):
        st, b = self._req("POST", "/list_directory", body={"path": path})
        return json.loads(b)

    def run_python(self, code):
        return json.loads(self._req("POST", "/run_python", body={"code": code})[1])
