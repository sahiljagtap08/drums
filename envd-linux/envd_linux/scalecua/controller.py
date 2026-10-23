"""`DrumsEnvironmentController`: the AgentRL `EnvironmentController` interface over Drums sessions.

Interface (read from the MIT `agentrl-worker` 0.4.0 package, `agentrl/worker/environment/_base.py`):

    async start_session(subtypes, immutable=True, **kwargs)
        -> (session_id, {subtype: environment_id}, {subtype: url})
    async renew_session(session_id) / end_session(session_id)
    async execute_command(environment_id, command, timeout=30) -> (returncode, stdout, stderr)
    async execute_shell / create_shell / get_env_variables / background_task
    sync_* wrappers run the coroutine on `self.loop`, which the worker's task sets.

`url` for an OSWorld subtype is the host that serves the guest ports; the OSWorld provider
turns it into "host:5000:9222:8006:8080". Blocking pool work runs in the loop's executor.

Not covered: how ScaleCUA's own worker code calls these (its `osworld_env` code is licence-
unresolved and is not used), and registering the driver in the real `create_controller`
(see patch.py)."""

import asyncio
import threading

from .client import GuestClient
from .compat import EnvironmentController, EnvironmentDelegation
from .pool import DrumsSessionPool, SessionStartError

GUEST_PORTS = [5000, 9222, 8006, 8080]


class DrumsEnvironmentDelegation(EnvironmentDelegation):
    def __init__(self, name="osworld", subtypes=("Ubuntu-modified",)):
        super().__init__(name)
        self._subtypes = list(subtypes)

    def get_subtypes(self):
        return list(self._subtypes)

    def get_service_port(self, subtype):
        return list(GUEST_PORTS)

    def is_exclusive(self, subtype):
        return True

    def get_concurrency_limit(self, subtype):
        return 1

    def get_reuse_limit(self, subtype):
        return 1  # a session is never reused; it is rebuilt per attempt

    async def create_docker_container(self, *a, **k):
        raise NotImplementedError("Drums sessions are not Docker containers")

    async def create_k8s_pod(self, *a, **k):
        raise NotImplementedError("Drums sessions are not Kubernetes pods")


class DrumsEnvironmentController(EnvironmentController):
    def __init__(self, delegation, backend, slots, max_age_s=None, idle_s=None, **pool_options):
        super().__init__(delegation)
        self.pool = DrumsSessionPool(backend, slots, **pool_options)
        self.max_age_s, self.idle_s = max_age_s, idle_s
        self._sessions = {}  # session_id -> {subtype: (pool_sid, env_id, ip)}
        self._env_ip = {}  # environment_id -> ip
        self._lock = threading.Lock()
        self.loop = None

    async def _run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *a)

    async def start_session(self, subtypes, immutable=True, **kwargs):
        """One exclusive, freshly built Drums session per subtype. `immutable` and extra kwargs
        (for example the OSWorld `image_path`) are accepted and ignored: every session is rebuilt."""
        names = [subtypes] if isinstance(subtypes, str) else list(subtypes)
        parts, started = {}, []
        try:
            for st in names:
                pool_sid, env_id, ip = await self._run(self.pool.start)
                started.append(pool_sid)
                parts[st] = (pool_sid, env_id, ip)
        except Exception:
            for sid in started:
                await self._run(self.pool.end, sid)
            raise
        import uuid
        sid = uuid.uuid4().hex
        with self._lock:
            self._sessions[sid] = parts
            for _, env_id, ip in parts.values():
                self._env_ip[env_id] = ip
        return sid, {st: p[1] for st, p in parts.items()}, {st: p[2] for st, p in parts.items()}

    async def renew_session(self, session_id):
        for pool_sid, _, _ in (self._sessions.get(session_id) or {}).values():
            self.pool.renew(pool_sid)

    async def end_session(self, session_id):
        with self._lock:
            parts = self._sessions.pop(session_id, None)
        for pool_sid, env_id, _ in (parts or {}).values():
            self._env_ip.pop(env_id, None)
            await self._run(self.pool.end, pool_sid)

    def _client(self, environment_id):
        ip = self._env_ip.get(environment_id)
        if ip is None:
            raise KeyError(f"unknown environment {environment_id}")
        return GuestClient(ip)

    async def execute_command(self, environment_id, command, timeout=30):
        c = self._client(environment_id)
        c.timeout = timeout
        r = await self._run(lambda: c.execute(command, shell=isinstance(command, str)))
        return r["returncode"], (r.get("output") or "").encode(), (r.get("error") or "").encode()

    async def execute_shell(self, environment_id, command, timeout=30):
        rc, out, err = await self.execute_command(environment_id, command, timeout)
        return out + err

    async def create_shell(self, environment_id, shell="/bin/bash"):
        raise NotImplementedError("interactive shells are not part of the Drums driver")

    async def get_env_variables(self, environment_id):
        r = await self.execute_command(environment_id, "env")
        return dict(line.split("=", 1) for line in r[1].decode().splitlines() if "=" in line)

    async def background_task(self):
        """Reaper: end sessions past the maximum age or idle time. Runs until cancelled."""
        while True:
            await asyncio.sleep(5)
            for pool_sid in self.pool.expired(self.max_age_s, self.idle_s):
                with self._lock:
                    owners = [sid for sid, parts in self._sessions.items()
                              if any(p[0] == pool_sid for p in parts.values())]
                for sid in owners:
                    self.pool._event(event="expired", session_id=sid)
                    await self.end_session(sid)

    # helpers outside the AgentRL interface
    def session_info(self, session_id):
        parts = self._sessions.get(session_id) or {}
        return {st: self.pool.session_info(p[0]) for st, p in parts.items()}

    def run_loop_in_thread(self):
        """For tests and the smoke job: start an event loop thread and make it `self.loop`."""
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        self.loop = loop
        return loop

    def shutdown(self):
        self.pool.shutdown()
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
