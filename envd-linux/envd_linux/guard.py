"""Generation guard: the check and the effect are one atomic step.

Same contract as envd's Generation actor: a late request from a finished attempt is
refused (409 stale_gen) and its effect never happens. A lock plays the actor's role: while
`work` runs a reset (`set`) cannot run, and if `set` ran first the check fails."""

import threading


class Generation:
    def __init__(self, initial=0):
        self._lock = threading.Lock()
        self._value = initial

    def current(self):
        with self._lock:
            return self._value

    def set(self, g):
        with self._lock:
            self._value = g

    def check(self, g):
        with self._lock:
            return g == self._value

    def apply_if_current(self, g, work):
        """Returns ("applied", result) or ("stale", current_gen). `work` runs under the lock;
        keep it short."""
        with self._lock:
            if g != self._value:
                return ("stale", self._value)
            return ("applied", work())
