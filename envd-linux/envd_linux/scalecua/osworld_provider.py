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

SEMANTICS. A session is rebuilt per attempt: `revert_to_snapshot` ends the session and returns the same
path, then DesktopEnv's own `_start_emulator` calls `start_emulator` again, which takes a fresh session.
`save_state` is not supported (there is no snapshot to save; raises NotImplementedError).

NOT PROVEN: no real `desktop_env` package is installed in this sandbox, so this was tested against local
stand-ins written from the public abstract classes and a replica of the public `_start_emulator` parsing;
and the session behind it answers with the OSWorld guest-server STUB (accessibility tree, terminal empty,
pyautogui absent), not the real server. That is claim (b); actual OSWorld compatibility (c) is open."""

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
        raise NotImplementedError("Drums sessions have no snapshot to save; a session is rebuilt per attempt")

    def revert_to_snapshot(self, path_to_vm, snapshot_name):
        self.stop_emulator(path_to_vm)
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
