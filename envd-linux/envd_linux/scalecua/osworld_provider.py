"""OSWorld `Provider` and `VMManager` over Drums sessions (the session-address -> DesktopEnv bridge).

PROVENANCE. Written only from the public, Apache-2.0 upstream xlang-ai/OSWorld:
`desktop_env/providers/base.py` (the `Provider` and `VMManager` abstract classes),
`desktop_env/providers/__init__.py` (the factory) and `desktop_env/desktop_env.py` (how `DesktopEnv`
calls them), plus the upstream docker provider's `start_emulator(path_to_vm, headless, os_type)`
signature. It was written without opening ScaleCUA's `osworld_env` port and without reading or
reviving `_quarantined_provider.py`. Disclosure that still stands: earlier in this branch's history I
had read `osworld_env/worker` before its licence status was known, so I am not a clean-room author;
the owner has ruled that the public OSWorld API may be built against and that the quarantine covers
only the port. If that separation is judged not good enough, this file is the thing to review.

WHAT DesktopEnv DOES WITH A PROVIDER (public code, as read):
  * the constructor asks `create_vm_manager_and_provider(provider_name, region)` (a fixed if/elif with no
    plug-in point) and then `provider_name` must be one of a fixed set, else ValueError;
  * `path_to_vm = manager.get_vm_path(os_type=, region=, screen_size=)` unless one is passed;
  * `_start_emulator`: `provider.start_emulator(path, headless, os_type)`, then
    `provider.get_ip_address(path)`, split with `rsplit(':', 4)` into host and the ports
    server/chromium/vnc/vlc (a bare host keeps the default ports);
  * `reset`: for providers it treats as "always clean" (names in its set, including "docker") it only
    reverts after the environment has been used; the revert is `provider.revert_to_snapshot(path, name)`,
    and a returned path different from the old one is re-registered through the manager;
  * `close`: `provider.stop_emulator(path)`.

So the only way to hand DesktopEnv a Drums provider without editing OSWorld is `install()` below, which
wraps the factory in `desktop_env.desktop_env` and selects Drums by `region == "drums"` while the
caller passes `provider_name="docker"` to get through the fixed name check (that name also gives the
"clean until used" revert behaviour Drums wants). It does not change what "docker" does for any other region.

SEMANTICS. By default a session is rebuilt per attempt: `revert_to_snapshot` for an unknown name (including DesktopEnv's generic
"init_state") ends the session and returns the same path, then DesktopEnv's own `_start_emulator` takes a fresh session.
With a pool whose backend can checkpoint (a `Supervisor` with `criu_friendly=True` and a private CRIU, PROTOTYPE of mechanism C, docs/l1-shared-runtime-design.md
sections 14 to 21): `save_state(path, name)` checkpoints the running session's LIVE state with CRIU and carries on from it in place (same slot, same address;
the checkpoint is declared NOT clean, so a task's browser state is preserved, never reset), and `revert_to_snapshot(path, name)` replaces the session's state with
that named checkpoint. Without a checkpoint-capable backend `save_state` raises NotImplementedError. Snapshots are per provider instance, in this process.

TESTED, and how far. Against the REAL public `desktop_env` (PyPI 1.0.2 and OSWorld main `04718ea`) and the REAL unmodified OSWorld guest server in real
sessions: `tests/test_osworld_provider.py`, the matched-runner dry run, and `tests/test_matched_reuse_real.py` (setup-only reuse through this provider's
snapshot API, and a mid-task branch scored by the real local evaluator). All of that is ONE public task (e0df059f) on one host. NOT PROVEN: compatibility with
the rest of the benchmark (tasks with downloads, logins, other applications), and any comparison with an incumbent provider (no hypervisor host here).
Disclosure unchanged: a provider written against the public API is not a ScaleCUA or incumbent port, and the quarantined provider stays unloaded."""

import itertools
import threading

try:  # pragma: no cover - depends on the environment
    from desktop_env.providers.base import Provider, VMManager
    REAL = True
except ImportError:  # pragma: no cover
    from abc import ABC, abstractmethod
    REAL = False

    class Provider(ABC):
        def __init__(self, region=None):
            self.region = region

        @abstractmethod
        def start_emulator(self, path_to_vm, headless): ...
        @abstractmethod
        def get_ip_address(self, path_to_vm): ...
        @abstractmethod
        def save_state(self, path_to_vm, snapshot_name): ...
        @abstractmethod
        def revert_to_snapshot(self, path_to_vm, snapshot_name): ...
        @abstractmethod
        def stop_emulator(self, path_to_vm): ...

    class VMManager(ABC):
        checked_and_cleaned = False

        @abstractmethod
        def initialize_registry(self, **kw): ...
        @abstractmethod
        def add_vm(self, vm_path, **kw): ...
        @abstractmethod
        def delete_vm(self, vm_path, **kw): ...
        @abstractmethod
        def occupy_vm(self, vm_path, pid, **kw): ...
        @abstractmethod
        def list_free_vms(self, **kw): ...
        @abstractmethod
        def check_and_clean(self, **kw): ...
        @abstractmethod
        def get_vm_path(self, **kw): ...

REGION = "drums"
PREFIX = "drums://"
PORTS = (5000, 9222, 8006, 8080)  # server, chromium, vnc, vlc (the guest's fixed ports)


class DrumsVMManager(VMManager):
    """A registry of opaque handles. A handle owns no resource until `start_emulator`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._n = itertools.count()
        self._vms = {}  # path -> pid or None

    def initialize_registry(self, **kw):
        pass

    def add_vm(self, vm_path, *a, **kw):
        with self._lock:
            self._vms.setdefault(vm_path, None)

    def delete_vm(self, vm_path, *a, **kw):
        with self._lock:
            self._vms.pop(vm_path, None)

    def occupy_vm(self, vm_path, pid, *a, **kw):
        with self._lock:
            self._vms[vm_path] = pid

    def list_free_vms(self, *a, **kw):
        with self._lock:
            return [p for p, pid in self._vms.items() if pid is None]

    def check_and_clean(self, *a, **kw):
        pass

    def get_vm_path(self, os_type=None, region=None, screen_size=None, **kw):
        path = f"{PREFIX}{next(self._n)}"
        self.add_vm(path)
        return path


class DrumsProvider(Provider):
    def __init__(self, pool, region=REGION):
        super().__init__(region)
        self.pool = pool
        self._lock = threading.Lock()
        self._sessions = {}  # path -> (session_id, ip)
        self._snaps = {}  # snapshot name -> criu.Checkpoint (PROTOTYPE of mechanism C)
        self._snap_dir = None

    def start_emulator(self, path_to_vm, headless=True, os_type=None):
        with self._lock:
            if path_to_vm in self._sessions:
                return
        sid, _, ip = self.pool.start()
        with self._lock:
            self._sessions[path_to_vm] = (sid, ip)

    def get_ip_address(self, path_to_vm):
        with self._lock:
            sid, ip = self._sessions[path_to_vm]
        return ":".join([ip, *map(str, PORTS)])

    def save_state(self, path_to_vm, snapshot_name):
        """Snapshot the running session's LIVE state (processes, windows, files) with CRIU, and carry on from it in place.
        Needs a pool whose backend can checkpoint (a Supervisor with criu_friendly=True and a private CRIU)."""
        import os
        import tempfile
        with self._lock:
            sid, ip = self._sessions[path_to_vm]
        if not hasattr(self.pool.backend, "checkpoint"):
            raise NotImplementedError("this Drums backend cannot checkpoint a session")
        if self._snap_dir is None:
            self._snap_dir = tempfile.mkdtemp(prefix="drums-snap-")
        path = os.path.join(self._snap_dir, f"{len(self._snaps)}-{snapshot_name.replace('/', '_').replace(':', '_')}")
        ck = self.pool.checkpoint_session(sid, path, clean=False)  # task state must be preserved, never reset
        self._snaps[snapshot_name] = ck
        info = self.pool.session_info(sid)
        with self._lock:
            self._sessions[path_to_vm] = (sid, info["ip"])

    def revert_to_snapshot(self, path_to_vm, snapshot_name):
        ck = self._snaps.get(snapshot_name)
        if ck is None:  # the generic initial state: a session is rebuilt per attempt
            self.stop_emulator(path_to_vm)
            return path_to_vm
        with self._lock:
            sid, _ = self._sessions[path_to_vm]
        info = self.pool.restore_session(sid, ck)
        with self._lock:
            self._sessions[path_to_vm] = (sid, info["ip"])
        return path_to_vm

    def stop_emulator(self, path_to_vm):
        with self._lock:
            entry = self._sessions.pop(path_to_vm, None)
        if entry:
            self.pool.end(entry[0])


def install(pool, manager=None):
    """Make `desktop_env.DesktopEnv(provider_name="docker", region="drums")` use Drums sessions.
    Returns the provider. Needs the real `desktop_env` package; wraps the factory by name in both
    modules that bind it."""
    import desktop_env.desktop_env as de
    import desktop_env.providers as providers
    mgr, prov = manager or DrumsVMManager(), DrumsProvider(pool)
    for mod in (de, providers):
        original = mod.create_vm_manager_and_provider
        if getattr(original, "_drums", False):
            original = original._original

        def factory(provider_name, region, use_proxy=False, _orig=original):
            if region == REGION:
                return mgr, prov
            return _orig(provider_name, region, use_proxy)
        factory._drums, factory._original = True, original
        mod.create_vm_manager_and_provider = factory
    return prov
