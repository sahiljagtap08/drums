"""Adapter for the Drums-Linux guest daemon (`envd-linux`, referenced in pyproject.toml but
NOT in this checkout yet, scoped in docs/drums-linux-architecture.md). It speaks the HTTP
protocol of the repo's envd (Swift, macOS), which is the specification. Routes used: GET /health, POST /gen, GET /screenshot, POST /action,
POST /kill, DELETE /pb, POST /launch.

Written from the envd Swift source. Tested only against a FAKE server in the tests
(no macOS here, and envd-linux does not exist); never run against a real daemon.
"""

import json
import struct
import urllib.error
import urllib.request

from .protocol import Env
from .types import EnvFault, Observation


def png_size(png: bytes):
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise EnvFault("bad_screenshot", "not a PNG")
    return struct.unpack(">II", png[16:24])


class EnvdHttpEnv(Env):
    def __init__(self, slot, port, launch_cmd, host="127.0.0.1", timeout_s=15.0, pid_roots=(),
                 pre_reset=None, pids_fn=None, settle_timeout_s=0.0, settle_poll_s=0.5, settle_quiet_s=3.0):
        """launch_cmd: {"path": ..., "args": [... "{url}" ...]} started on each reset.

        pre_reset(slot, gen): optional hook run before the daemon is told the new
        generation. The Linux Drums side uses it to have the session manager destroy
        and recreate the attempt's private sandbox. pids_fn() returns the pids to sample
        for resources (for example every pid in the session's cgroup)."""
        self.slot, self.base, self.timeout = slot, f"http://{host}:{port}", timeout_s
        self.launch_cmd = launch_cmd
        self.pid_roots = list(pid_roots)
        self.pre_reset, self.pids_fn = pre_reset, pids_fn
        self.settle_timeout_s, self.settle_poll_s = settle_timeout_s, settle_poll_s
        self.settle_quiet_s = settle_quiet_s
        self.gen = 0
        self.seq = 0

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise EnvFault("daemon_unreachable", str(e)) from e

    def _json(self, method, path, body=None):
        status, _, raw = self._req(method, path, body)
        if status == 409:
            raise EnvFault("stale_gen", raw.decode(errors="replace"))
        if status != 200:
            raise EnvFault(f"http_{status}", raw.decode(errors="replace")[:200])
        return json.loads(raw or b"{}")

    def reset(self, task, gen, token):
        self.gen, self.seq = gen, 0
        # The new generation goes in first, so a late action from the previous attempt is
        # refused (409) while the session is being rebuilt; only then is the sandbox recreated.
        self._json("POST", "/gen", {"gen": gen})
        if self.pre_reset:
            self.pre_reset(self.slot, gen)
        health = self._json("GET", "/health")
        if health.get("ax_trusted") is False or health.get("screen_recording") is False:
            raise EnvFault("permissions_missing", json.dumps(health))
        self._json("POST", "/kill", {"gen": gen, "all": True})
        self._req("DELETE", f"/pb?gen={gen}")
        url = task.verifier.get("start_url", "").format(slot=self.slot, token=token, gen=gen)
        args = [a.format(url=url) for a in self.launch_cmd["args"]]
        body = {"gen": gen, "path": self.launch_cmd["path"], "args": args}
        if self.launch_cmd.get("env"):
            body["env"] = self.launch_cmd["env"]
        self._json("POST", "/launch", body)
        self._settle()
        return self.observe()

    def _settle(self):
        """Wait until a freshly launched app has painted: the display must be non-black and
        stop changing. A frame that never changes is accepted after settle_quiet_s (apps that
        paint once), otherwise at least one change must be seen first (a browser window
        usually paints its chrome before the page). Counted as reset time. Off unless
        settle_timeout_s > 0."""
        if self.settle_timeout_s <= 0:
            return
        import time
        start = time.time()
        last, first_t, changed, stable = None, None, False, 0
        while time.time() - start < self.settle_timeout_s:
            status, headers, png = self._req("GET", "/screenshot")
            h = {k.lower(): v for k, v in headers.items()}
            if status == 200 and h.get("x-drums-black") != "true":
                digest = hash(png)
                now = time.time()
                if last is None:
                    first_t = now
                elif digest != last:
                    changed, stable = True, 0
                else:
                    stable += 1
                last = digest
                if (changed and stable >= 2) or (not changed and now - first_t >= self.settle_quiet_s):
                    return
            time.sleep(self.settle_poll_s)
        raise EnvFault("settle_timeout", "display did not settle")

    def observe(self):
        status, headers, png = self._req("GET", "/screenshot")
        if status != 200:
            raise EnvFault("screenshot_failed", f"http {status}")
        h = {k.lower(): v for k, v in headers.items()}
        w, hgt = png_size(png)
        return Observation(png=png, width=w, height=hgt, gen=int(h.get("x-drums-gen", -1)),
                           stale=h.get("x-drums-stale") == "true",
                           black=h.get("x-drums-black") == "true")

    def act(self, action):
        self.seq += 1
        self._json("POST", "/action", {"gen": self.gen, "seq": self.seq, **action.to_json()})
        return self.observe()

    def pids(self):
        return self.pids_fn() if self.pids_fn else self.pid_roots
