"""FAKE backend for protocol tests: a synthetic frame and recorded actions. Proves the
protocol layer only; it touches no display."""

from .backend import Backend, BackendError


class FakeBackend(Backend):
    fake = True

    def __init__(self, w=64, h=48, fail_actions=False):
        self.w, self.h, self.fail_actions = w, h, fail_actions
        self.shade = 40
        self.actions = []
        self.launched = []
        self.killed = []

    def capture(self, region):
        w, h = self.w, self.h
        if region:
            x, y, rw, rh = region
            w, h = max(1, min(int(rw), self.w)), max(1, min(int(rh), self.h))
        return bytes([self.shade, self.shade, self.shade, 0]) * (w * h), w, h

    def screen_size(self):
        return self.w, self.h

    def perform(self, spec):
        if self.fail_actions:
            raise BackendError("fake failure")
        self.actions.append(spec)
        if spec["type"] == "click":
            self.shade = (self.shade + 50) % 250 + 20  # a visible change

    def launch(self, spec):
        self.launched.append(spec)
        return 4242

    def kill(self, spec):
        if spec.get("pid") == 1234:
            return ("refused", "protected")
        self.killed.append(spec)
        return ("signalled", [4242])

    def windows(self):
        return [{"title": "fake", "x": 0, "y": 0, "w": self.w, "h": self.h}]
