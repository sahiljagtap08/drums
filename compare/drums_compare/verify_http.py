"""Verifier backed by the repo's verify-server (real code, over HTTP). Slot == env."""

import json
import urllib.request

from .protocol import Verifier


class VerifyServerVerifier(Verifier):
    def __init__(self, base_url, timeout_s=10.0):
        self.base, self.timeout = base_url.rstrip("/"), timeout_s

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            return json.loads(r.read())

    def lease(self, slot, gen):
        self._get(f"/lease?env={slot}&gen={gen}")

    def is_clean(self, slot, gen):
        return not any(r["gen"] == gen for r in self.rows(slot))

    def rows(self, slot):
        return self._get(f"/rows?env={slot}")
