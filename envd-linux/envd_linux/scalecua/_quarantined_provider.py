"""QUARANTINED. DO NOT IMPORT FROM ANY RUN PATH.

This module is shaped like a provider class in ScaleCUA's `osworld_env/worker` source, which has no
licence of its own (source-readable is not permission) and which its author read before that was
known. A rewrite by the same person is not a clean-room cure, so this file stays quarantined:
no driver entry point, smoke path, test run path or documentation example may import it.

Resolution paths, in order:
  (a) an independent implementation by someone who has NOT read `osworld_env`, working only from the
      MIT `agentrl-worker` 0.4.0 interface and public OSWorld API documentation (Apache-2.0); or
  (b) written permission from the rightsholder.

What is missing while it is quarantined: the bridge between OSWorld's `DesktopEnv` (which expects a
`Provider` with start_emulator / get_ip_address / revert_to_snapshot / stop_emulator) and
`DrumsEnvironmentController`. Without it the real ScaleCUA worker path cannot drive a Drums session;
the controller, the guest-server stub and the smoke job do not depend on it.

Kept in git history on purpose. Importing it raises unless DRUMS_ALLOW_QUARANTINED=1 is set, which
exists only so the guard test can prove the quarantine is enforced."""

import os

if os.environ.get("DRUMS_ALLOW_QUARANTINED") != "1":
    raise ImportError("scalecua/_quarantined_provider.py is quarantined (licence provenance); see its docstring")

GUEST_PORT = 5000


class DrumsVMProvider:
    server_port = GUEST_PORT
    chromium_port = 9222
    vnc_port = 8006
    vlc_port = 8080
    app_port = 8000

    def __init__(self, controller, os_type="Ubuntu-modified"):
        self.controller = controller
        self.os_type = os_type
        self.session_id = None
        self.container_id = None
        self.container_addr = None

    def start_emulator(self, path_to_vm, headless=True, os_type=None, *args, **kwargs):
        self.stop_emulator()
        os_type = os_type or self.os_type
        sid, containers, addresses = self.controller.sync_start_session(os_type, image_path=path_to_vm)
        self.session_id, self.container_id = sid, containers[os_type]
        self.container_addr, self.os_type = addresses[os_type], os_type

    def get_ip_address(self, *args, **kwargs):
        base = (f"{self.container_addr}:{self.server_port}:{self.chromium_port}:"
                f"{self.vnc_port}:{self.vlc_port}")
        if self.os_type == "Ubuntu2_scienceboard":
            base += f":{self.app_port}"
        return base

    def save_state(self, *args, **kwargs):
        raise NotImplementedError("snapshots are not part of the Drums driver")

    def revert_to_snapshot(self, path_to_vm, *args, **kwargs):
        """OSWorld reverts by stopping the emulator; the controller rebuilds the session."""
        self.stop_emulator()
        return path_to_vm

    def stop_emulator(self, *args, **kwargs):
        if self.session_id:
            self.controller.sync_end_session(self.session_id)
        self.session_id = self.container_id = self.container_addr = None
