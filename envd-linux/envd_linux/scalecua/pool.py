"""Blocking, thread-safe pool of exclusive Drums sessions.

The async `DrumsEnvironmentController` (controller.py) is a thin facade over this pool. The
pool hands out one rebuilt session per attempt and returns `(session_id, container_id,
address)`; `address` is the host that serves the OSWorld guest ports (5000 server, 9222 Chrome
DevTools, 8080 VLC, 8006 VNC). Every session has its own network namespace, so the ports are
identical in every session.

Failure handling is the point of a training backend: a session that does not become healthy is
recorded as a fault, its slot is rebuilt, and another slot is tried. Nothing is hidden; every
start, reset and fault is an event in the events file so the harness can count it."""

import json
import threading
import time
import urllib.request
import uuid

GUEST_PORT = 5000


class SessionStartError(RuntimeError):
    pass


class PoolExhausted(SessionStartError):
    pass


def http_health(ip, port=GUEST_PORT, timeout=2.0):
    """The guest is ready when GET /platform answers 200 (the OSWorld readiness probe)."""
    try:
        with urllib.request.urlopen(f"http://{ip}:{port}/platform", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


class _Slot:
    FREE, BUSY, DIRTY, BROKEN = "free", "busy", "dirty", "broken"

    def __init__(self, n):
        self.n, self.state, self.info, self.sid = n, _Slot.DIRTY, None, None
        self.started = self.active = 0.0


class DrumsSessionPool:
    def __init__(self, backend, slots, health=http_health, health_timeout=30.0, acquire_timeout=60.0,
                 max_start_attempts=3, events_path=None, eager_reset=True, clock=time.time):
        """backend: object with create(slot)->info dict (must contain "ip") and destroy(slot)."""
        self.backend, self.health, self.clock = backend, health, clock
        self.health_timeout, self.acquire_timeout = health_timeout, acquire_timeout
        self.max_start_attempts, self.eager_reset = max_start_attempts, eager_reset
        self.events_path = events_path
        self._slots = [_Slot(i) for i in range(slots)]
        self._by_sid = {}
        self._cond = threading.Condition()
        self._ev_lock = threading.Lock()
        self._closed = False
        self._eager_threads = []
        self.loop = None  # the real controller is handed an asyncio loop by its task; unused here

    # -- events
    def _event(self, **kw):
        kw["t"] = self.clock()
        if self.events_path:
            with self._ev_lock, open(self.events_path, "a") as f:
                f.write(json.dumps(kw) + "\n")

    # -- slot handling
    def _acquire(self):
        deadline = self.clock() + self.acquire_timeout
        with self._cond:
            while True:
                # prefer a ready session, then one that needs a rebuild
                for want in (_Slot.FREE, _Slot.DIRTY, _Slot.BROKEN):
                    for s in self._slots:
                        if s.state == want:
                            prev = s.state
                            s.state = _Slot.BUSY
                            return s, prev
                left = deadline - self.clock()
                if left <= 0:
                    raise PoolExhausted("no free Drums session within the acquire timeout")
                self._cond.wait(min(left, 0.5))

    def _rebuild(self, s):
        """Destroy and recreate the slot's session, wait for the guest. Returns reset seconds."""
        t0 = self.clock()
        try:
            self.backend.destroy(s.n)
        except Exception:
            pass
        s.info = self.backend.create(s.n)
        deadline = self.clock() + self.health_timeout
        while not self.health(s.info["ip"]):
            if self.clock() > deadline:
                raise SessionStartError(f"guest on slot {s.n} not healthy within {self.health_timeout}s")
            time.sleep(0.05)
        return self.clock() - t0

    def _release(self, s, state):
        with self._cond:
            s.state, s.sid = state, None
            self._cond.notify_all()

    # -- contract
    def start(self):
        last = None
        for attempt in range(1, self.max_start_attempts + 1):
            s, prev = self._acquire()
            try:
                reset_s = 0.0
                if prev != _Slot.FREE or s.info is None:
                    reset_s = self._rebuild(s)
                    self._event(event="reset", slot=s.n, reset_s=reset_s, reason=prev, attempt=attempt)
            except Exception as e:
                last = e
                self._event(event="fault", kind="start_failed", slot=s.n, attempt=attempt, detail=str(e)[:200])
                self._release(s, _Slot.BROKEN)
                continue
            sid = uuid.uuid4().hex
            s.sid = sid
            s.started = s.active = self.clock()
            with self._cond:
                self._by_sid[sid] = s
            self._event(event="start", slot=s.n, session_id=sid, reset_s=reset_s, attempt=attempt)
            return sid, f"drums-slot-{s.n}", s.info["ip"]
        raise SessionStartError(f"could not start a session after {self.max_start_attempts} attempts: {last}")

    def end(self, session_id):
        with self._cond:
            s = self._by_sid.pop(session_id, None)
        if s is None:
            self._event(event="end_unknown", session_id=session_id)
            return
        self._event(event="end", slot=s.n, session_id=session_id)
        if self.eager_reset and not self._closed:
            t = threading.Thread(target=self._eager, args=(s,), daemon=True)
            self._eager_threads.append(t)
            t.start()
        else:
            self._release(s, _Slot.DIRTY)

    def _eager(self, s):
        try:
            if self._closed:
                return
            reset_s = self._rebuild(s)
            self._event(event="reset", slot=s.n, reset_s=reset_s, reason="eager")
            self._release(s, _Slot.FREE)
        except Exception as e:
            self._event(event="fault", kind="reset_failed", slot=s.n, detail=str(e)[:200])
            self._release(s, _Slot.BROKEN)

    # -- helpers for the harness
    def renew(self, session_id):
        with self._cond:
            s = self._by_sid.get(session_id)
            if s:
                s.active = self.clock()
            return s is not None

    def expired(self, max_age_s=None, idle_s=None):
        """Session ids past a maximum age or idle time (the controller's reaper ends them)."""
        now, out = self.clock(), []
        with self._cond:
            for sid, s in self._by_sid.items():
                if (max_age_s and now - s.started > max_age_s) or (idle_s and now - s.active > idle_s):
                    out.append(sid)
        return out

    def session_info(self, session_id):
        with self._cond:
            s = self._by_sid.get(session_id)
        return dict(s.info, slot=s.n) if s and s.info else None

    def warm(self, n=None):
        """Create sessions up front, in parallel, so the first starts do not pay for it."""
        slots = self._slots[: n or len(self._slots)]
        threads = []

        def go(s):
            try:
                with self._cond:
                    if s.state != _Slot.DIRTY:
                        return
                    s.state = _Slot.BUSY
                self._rebuild(s)
                self._release(s, _Slot.FREE)
            except Exception as e:
                self._event(event="fault", kind="warm_failed", slot=s.n, detail=str(e)[:200])
                self._release(s, _Slot.BROKEN)
        for s in slots:
            t = threading.Thread(target=go, args=(s,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def stats(self):
        with self._cond:
            states = [s.state for s in self._slots]
        return {"slots": len(states), **{k: states.count(k) for k in ("free", "busy", "dirty", "broken")}}

    def shutdown(self):
        """Stop background rebuilds, wait for any in flight, then destroy every session."""
        self._closed = True
        for t in list(self._eager_threads):
            t.join(timeout=60)
        for s in self._slots:
            try:
                self.backend.destroy(s.n)
            except Exception:
                pass
