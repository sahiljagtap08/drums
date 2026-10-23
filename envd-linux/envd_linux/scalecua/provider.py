"""The OSWorld `Provider` interface over a Drums controller.

OSWorld's DesktopEnv asks its provider to start an emulator and then reads one string,
"ip:server:chromium:vnc:vlc" (plus an app port for ScienceBoard), to find the guest services.
This is our own implementation of that interface from its documented shape."""

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
