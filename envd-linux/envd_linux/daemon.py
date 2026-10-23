"""envd-linux daemon: one HTTP listener per session slot, one process.

    python -m envd_linux.daemon --state DIR --slots 0,1 --base-port 18700 [--level L0]

Needs python-xlib and a supervisor-created session per slot (see session.py)."""

import argparse
import signal
import sys
import threading

from .httpd import SessionServer, SessionState
from .x11 import XBackend


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--slots", default="0")
    ap.add_argument("--base-port", type=int, default=18700)
    ap.add_argument("--level", default="L0")
    a = ap.parse_args(argv)
    servers = []
    for s in (int(x) for x in a.slots.split(",")):
        st = SessionState(env=s, port=a.base_port + s, backend=XBackend(a.state, s), level=a.level)
        servers.append(SessionServer(st).start())
    print("ready", ",".join(str(s.state.port) for s in servers), flush=True)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    done.wait()
    for s in servers:
        s.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
