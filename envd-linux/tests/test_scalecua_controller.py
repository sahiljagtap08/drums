"""Pool, controller and provider tests against LOCAL STUBS (a fake session backend and a fake
health check). They prove pool logic, failure accounting and the call shapes; they say nothing
about the real ScaleCUA worker, which is not used here."""

import json
import threading
import time

import pytest

from envd_linux.scalecua.controller import DrumsEnvironmentController, DrumsEnvironmentDelegation
from envd_linux.scalecua.pool import DrumsSessionPool, PoolExhausted, SessionStartError
from envd_linux.scalecua.provider import DrumsVMProvider


class FakeBackend:
    def __init__(self, fail_slots=(), delay=0.0):
        self.created, self.destroyed = [], []
        self.fail = set(fail_slots)
        self.delay = delay
        self.live = set()
        self.lock = threading.Lock()

    def create(self, slot):
        time.sleep(self.delay)
        with self.lock:
            self.created.append(slot)
            if slot in self.fail:
                self.fail.discard(slot)
                raise RuntimeError("boom")
            self.live.add(slot)
        return {"ip": f"10.9.0.{slot}"}

    def destroy(self, slot):
        with self.lock:
            self.destroyed.append(slot)
            self.live.discard(slot)


def ctl(backend, slots=2, **kw):
    kw.setdefault("health", lambda ip, port=5000, timeout=2: True)
    kw.setdefault("acquire_timeout", 2)
    kw.setdefault("eager_reset", False)
    return PoolAdapter(DrumsSessionPool(backend, slots, **kw))


class PoolAdapter:
    """Old-style (os_type) call shape over the pool, so the pool tests read as before."""

    def __init__(self, pool):
        self.pool = pool

    def sync_start_session(self, os_type, image_path=None):
        sid, env, ip = self.pool.start()
        return sid, {os_type: env}, {os_type: ip}

    def sync_end_session(self, sid):
        self.pool.end(sid)

    def __getattr__(self, name):
        return getattr(self.pool, name)


def test_start_returns_the_documented_shape():
    c = ctl(FakeBackend())
    sid, containers, addrs = c.sync_start_session("Ubuntu-modified", image_path="/x")
    assert isinstance(sid, str) and containers["Ubuntu-modified"].startswith("drums-slot-")
    assert addrs["Ubuntu-modified"].startswith("10.9.0.")
    assert c.session_info(sid)["slot"] in (0, 1)


def test_sessions_are_exclusive_under_concurrency():
    b = FakeBackend(delay=0.01)
    c = ctl(b, slots=4, acquire_timeout=10)
    in_use, violations = {}, []
    lock = threading.Lock()

    def worker():
        for _ in range(5):
            sid, cont, addr = c.sync_start_session("u")
            slot = c.session_info(sid)["slot"]
            with lock:
                if slot in in_use:
                    violations.append(slot)
                in_use[slot] = sid
            time.sleep(0.005)
            with lock:
                in_use.pop(slot, None)
            c.sync_end_session(sid)

    ts = [threading.Thread(target=worker) for _ in range(12)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not violations
    assert c.stats()["busy"] == 0


def test_ended_session_is_rebuilt_before_reuse():
    b = FakeBackend()
    c = ctl(b, slots=1)
    sid, *_ = c.sync_start_session("u")
    c.sync_end_session(sid)
    assert c.stats()["dirty"] == 1
    sid2, *_ = c.sync_start_session("u")
    assert b.created == [0, 0]  # a fresh session for the second attempt, never a reused one


def test_eager_reset_makes_the_next_start_free_of_rebuild():
    b = FakeBackend()
    c = ctl(b, slots=1, eager_reset=True)
    sid, *_ = c.sync_start_session("u")
    c.sync_end_session(sid)
    deadline = time.time() + 5
    while c.stats()["free"] != 1 and time.time() < deadline:
        time.sleep(0.01)
    n = len(b.created)
    c.sync_start_session("u")
    assert len(b.created) == n  # the pre-built session was handed out


def test_failed_start_is_counted_and_another_slot_is_tried(tmp_path):
    ev = tmp_path / "events.jsonl"
    b = FakeBackend(fail_slots={0})
    c = ctl(b, slots=2, events_path=str(ev))
    sid, cont, addr = c.sync_start_session("u")
    rows = [json.loads(x) for x in ev.read_text().splitlines()]
    assert any(r["event"] == "fault" and r["kind"] == "start_failed" for r in rows)
    assert any(r["event"] == "start" and r["attempt"] == 2 for r in rows)
    assert c.stats()["broken"] == 1


def test_unhealthy_guest_is_a_fault_not_a_session():
    c = ctl(FakeBackend(), slots=1, health=lambda ip, **k: False, health_timeout=0.2, max_start_attempts=2)
    with pytest.raises(SessionStartError):
        c.sync_start_session("u")
    assert c.stats()["broken"] == 1


def test_pool_exhaustion_times_out_instead_of_hanging():
    c = ctl(FakeBackend(), slots=1, acquire_timeout=0.2)
    c.sync_start_session("u")
    with pytest.raises(PoolExhausted):
        c.sync_start_session("u")


def test_ending_an_unknown_session_is_logged_not_fatal(tmp_path):
    ev = tmp_path / "e.jsonl"
    c = ctl(FakeBackend(), events_path=str(ev))
    c.sync_end_session("nope")
    assert "end_unknown" in ev.read_text()


def test_warm_builds_all_sessions_in_parallel():
    b = FakeBackend(delay=0.1)
    c = ctl(b, slots=4)
    t0 = time.time()
    c.warm()
    assert c.stats()["free"] == 4 and time.time() - t0 < 0.35


def real_controller(backend, slots=2, **kw):
    kw.setdefault("health", lambda ip, port=5000, timeout=2: True)
    kw.setdefault("acquire_timeout", 2)
    kw.setdefault("eager_reset", False)
    c = DrumsEnvironmentController(DrumsEnvironmentDelegation(), backend, slots, **kw)
    c.run_loop_in_thread()
    return c


def test_provider_follows_the_osworld_provider_shape():
    c = real_controller(FakeBackend())
    p = DrumsVMProvider(c)
    p.start_emulator("/vm", True, os_type="Ubuntu-modified")
    parts = p.get_ip_address().split(":")
    assert parts[0].startswith("10.9.0.") and parts[1:] == ["5000", "9222", "8006", "8080"]
    p.revert_to_snapshot("/vm")
    assert p.session_id is None
    with pytest.raises(NotImplementedError):
        p.save_state()
    p2 = DrumsVMProvider(c, os_type="Ubuntu2_scienceboard")
    p2.start_emulator("/vm", True)
    assert p2.get_ip_address().endswith(":8000")
    c.shutdown()


def test_async_interface_matches_the_agentrl_controller_shape():
    b = FakeBackend()
    c = real_controller(b)
    sid, envs, urls = c.sync_start_session("Ubuntu-modified", immutable=True, image_path="/vm")
    assert set(envs) == set(urls) == {"Ubuntu-modified"} and urls["Ubuntu-modified"].startswith("10.9.0.")
    c.sync_renew_session(sid)
    c.sync_end_session(sid)
    assert c.stats() if hasattr(c, "stats") else True
    sid2, envs2, _ = c.sync_start_session(["a", "b"])
    assert set(envs2) == {"a", "b"} and len(set(envs2.values())) == 2  # two exclusive sessions
    c.sync_end_session(sid2)
    c.shutdown()


def test_a_failed_second_subtype_releases_the_first():
    b = FakeBackend(fail_slots={1})
    c = real_controller(b, slots=2, max_start_attempts=1)
    with pytest.raises(SessionStartError):
        c.sync_start_session(["a", "b"])
    assert c.pool.stats()["busy"] == 0  # the first session was handed back, not leaked
    c.shutdown()


def test_execute_command_routes_to_the_session_guest(monkeypatch):
    import envd_linux.scalecua.controller as m
    calls = []

    class G:
        def __init__(self, ip):
            self.ip, self.timeout = ip, 0

        def execute(self, command, shell=False):
            calls.append((self.ip, command, shell))
            return {"returncode": 0, "output": "out", "error": "err"}

    monkeypatch.setattr(m, "GuestClient", G)
    c = real_controller(FakeBackend())
    sid, envs, urls = c.sync_start_session("u")
    rc, out, err = c.sync_execute_command(envs["u"], "echo hi")
    assert (rc, out, err) == (0, b"out", b"err") and calls == [(urls["u"], "echo hi", True)]
    assert c.sync_execute_shell(envs["u"], ["ls"]) == b"outerr"
    with pytest.raises(KeyError):
        c.sync_execute_command("nope", "x")
    c.shutdown()


def test_reaper_ends_expired_sessions():
    import asyncio
    b = FakeBackend()
    c = real_controller(b, slots=1, max_age_s=0.05)
    sid, envs, _ = c.sync_start_session("u")

    async def run():
        t = asyncio.create_task(c.background_task())
        await asyncio.sleep(6)
        t.cancel()
    asyncio.run_coroutine_threadsafe(run(), c.loop).result(20)
    assert c.pool.stats()["busy"] == 0 and sid not in c._sessions
    c.shutdown()


def test_delegation_matches_the_agentrl_delegation_interface():
    d = DrumsEnvironmentDelegation()
    assert d.get_name() == "osworld" and d.get_subtypes() == ["Ubuntu-modified"]
    assert d.is_exclusive("x") and d.get_concurrency_limit("x") == 1 and d.get_reuse_limit("x") == 1
    assert d.get_service_port("x") == [5000, 9222, 8006, 8080]
    import asyncio
    with pytest.raises(NotImplementedError):
        asyncio.run(d.create_docker_container({}, "x"))
    with pytest.raises(NotImplementedError):
        asyncio.run(d.create_k8s_pod(None, "x"))
