"""PROTOTYPE of mechanism C (docs/l1-shared-runtime-design.md section 14): checkpoint a warm session with CRIU and restore copies of it.

STATUS: BUILT as a feasibility probe, results in section 15. Not a product feature.

A checkpoint is the processes' memory and kernel state (CRIU images) plus the session's overlay UPPER layer, which holds the files the
session wrote. A restore mounts a new overlay whose read-only lower layers are [that upper, the template, the host root] and whose own upper is
empty, so siblings restored from one checkpoint share only read-only layers and every write lands in the sibling's own upper.

What CRIU needs from the host, all private to the run:
  * the criu binary and its libraries, kept in a private directory (`DRUMS_CRIU_HOME`, see `Criu`), never installed system-wide;
  * `ip` on its PATH (the same private directory);
  * at restore, a veth pair created by CRIU (host end named dvh<slot>), the overlay root mount and the X11 link: each recorded in the
    sweep ledger before it exists.
The session must be started with `criu_friendly=True` (see session_init): its fds and mappings may not point outside its own root."""

import json
import os
import subprocess
import time

DEV_NODES = ("null", "zero", "full", "random", "urandom", "tty")
IRMAP_PATHS = ("/usr/share/dbus-1", "/etc/dbus-1", "/usr/share/defaults", "/usr/share", "/etc")
GHOST_LIMIT = str(64 * 1024 * 1024)


class CriuError(RuntimeError):
    pass


class Criu:
    """A private CRIU install: `home` holds `x-<version>/usr/sbin/criu` and `libs/{bin,usr/lib/x86_64-linux-gnu,lib/x86_64-linux-gnu}`."""

    def __init__(self, home=None, version=None):
        home = home or os.environ.get("DRUMS_CRIU_HOME")
        if not home:
            raise CriuError("set DRUMS_CRIU_HOME to the private CRIU directory")
        version = version or os.environ.get("DRUMS_CRIU_VERSION") or self.detect_version(home)
        self.home, self.binary = home, os.path.join(home, f"x-{version}", "usr", "sbin", "criu")
        libs = os.path.join(home, "libs")
        self.env = dict(os.environ, PATH=f"{libs}/bin:" + os.environ["PATH"],
                        LD_LIBRARY_PATH=f"{libs}/usr/lib/x86_64-linux-gnu:{libs}/lib/x86_64-linux-gnu")

    @staticmethod
    def detect_version(home):
        """The version directory name under `home`: exactly one `x-<version>` directory is used; none keeps the historical "4.2"; several refuse (set DRUMS_CRIU_VERSION to choose)."""
        found = sorted(d[2:] for d in os.listdir(home) if d.startswith("x-") and os.path.isdir(os.path.join(home, d))) if os.path.isdir(home) else []
        if len(found) > 1:
            raise CriuError(f"several CRIU versions under {home}: {found}; set DRUMS_CRIU_VERSION")
        return found[0] if found else "4.2"

    def run(self, *args):
        return subprocess.run([self.binary, *args], capture_output=True, text=True, env=self.env)

    def version(self):
        return self.run("--version").stdout.strip()

    def check(self):
        r = self.run("check")
        return r.returncode == 0, (r.stdout + r.stderr)[-400:]


def _common():
    out = ["--ext-unix-sk", "--tcp-close", "--file-locks", "--ghost-limit", GHOST_LIMIT]
    return out


def dump(criu, init_pid, img_dir, inner_if="dvs0", log="dump.log"):
    """Checkpoint the session whose PID 1 is `init_pid` (host pid). The processes are killed by the dump (no --leave-running): the
    checkpoint is taken once, offline. Returns {rc, seconds, log_tail}."""
    os.makedirs(img_dir, exist_ok=True)
    ext = ["--external", f"veth[{inner_if}]:ext", "--external", "mnt[/tmp/.X11-unix]:x11"]
    ext += [x for n in DEV_NODES for x in ("--external", f"mnt[/dev/{n}]:{n}")]
    irm = [x for p in IRMAP_PATHS for x in ("--irmap-scan-path", p)]
    t = time.time()
    r = criu.run("dump", "-t", str(init_pid), "-D", img_dir, "-v3", "-o", log, "--force-irmap", *_common(), *ext, *irm)
    return {"rc": r.returncode, "seconds": time.time() - t, "log": _errors(os.path.join(img_dir, log))}


def restore(criu, img_dir, root, x11dir, veth_host, pidfile, inner_if="dvs0", log="restore.log"):
    """Restore into a new PID, mount, network and IPC/UTS namespace set. `root` is a mounted overlay; `x11dir` the new session's X socket
    directory; `veth_host` the name CRIU gives the host end of the recreated veth pair."""
    ext = ["--external", f"veth[{inner_if}]:{veth_host}", "--external", f"mnt[x11]:{x11dir}"]
    ext += [x for n in DEV_NODES for x in ("--external", f"mnt[{n}]:/dev/{n}")]
    t = time.time()
    r = criu.run("restore", "-D", img_dir, "-d", "-v3", "-o", log, "--root", root, "--pidfile", pidfile, *_common(), *ext)
    return {"rc": r.returncode, "seconds": time.time() - t, "log": _errors(os.path.join(img_dir, log))}


def _errors(path, n=12):
    try:
        return [ln[:240] for ln in open(path).read().splitlines() if "Error" in ln][-n:]
    except OSError:
        return []


class Checkpoint:
    """On disk: <dir>/img (CRIU images), <dir>/upper (the session's overlay upper, now a read-only lower layer), <dir>/meta.json."""

    def __init__(self, path):
        self.path = path
        self.img, self.upper = os.path.join(path, "img"), os.path.join(path, "upper")
        self.meta_path = os.path.join(path, "meta.json")

    def meta(self):
        with open(self.meta_path) as f:
            return json.load(f)

    def write_meta(self, **kw):
        with open(self.meta_path, "w") as f:
            json.dump(kw, f)
