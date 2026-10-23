"""What a platform backend must provide to the protocol layer."""

from abc import ABC, abstractmethod


class BackendError(Exception):
    """A platform-level failure (becomes 500 action_failed / capture_failed)."""


class Backend(ABC):
    @abstractmethod
    def capture(self, region):
        """Return (bgrx_bytes, width, height) for the region (x,y,w,h) or the full screen."""

    @abstractmethod
    def screen_size(self):
        """(w, h)."""

    @abstractmethod
    def perform(self, spec):
        """Carry out a validated action. Raise BackendError on failure."""

    @abstractmethod
    def launch(self, spec):
        """Start a process in the session; return its pid. Raise BackendError."""

    @abstractmethod
    def kill(self, spec):
        """Return ("refused", why) or ("signalled", [pids])."""

    @abstractmethod
    def windows(self):
        """List of {"title", "pid"?, "x","y","w","h"} dicts."""

    def capture_ok(self):
        return True

    def input_ok(self):
        return True
