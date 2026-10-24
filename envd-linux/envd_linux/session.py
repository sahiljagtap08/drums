"""Session supervisor: creates, resets and destroys private sessions.

A session = a PID+mount+IPC+UTS namespace (via util-linux `unshare`), an overlayfs on $HOME
(lower: a shared read-only template tree; upper: private to the session), private /tmp and
/dev/shm, and its own Xvfb. Needs root. Stdlib only.

Not done in the spike: a network namespace per session, cgroup limits, pivot_root onto an
overlay of the whole root filesystem, a window manager."""

import json
import os
import uuid
import shutil
import signal
import subprocess
import sys
import time

HOME = "/home/drums"
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SessionError(RuntimeError):
    pass


def _child_pids(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(x) for x in out]


class _ForkedProc:
    """Stand-in for Popen: a session PID 1 forked by the zygote (not our child, so no wait())."""

    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        try:
            with open(f"/proc/{self.pid}/stat") as f:
                return 0 if f.read().rsplit(")", 1)[1].split()[0] == "Z" else None
        except OSError:
            return 0

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def wait(self, timeout=None):
        end = time.time() + (timeout or 10)
        while self.poll() is None and time.time() < end:
            time.sleep(0.02)


class Supervisor:
    def __init__(self, state_dir, lower_home, python="/usr/bin/python3", xvfb="Xvfb",
                 size="1280x800x24", display_base=90, root_mode="home", netns=False, osworld_port=0,
                 firewall=None, nat=False, block_private_egress=True,
                 sweep_on_start=True, sweep_dir=None, osworld_real_kit=None, exec_systemd=False,
                 zygote=None, init_python=None, server_mode="asshipped", workload="", ready_timeout=30,
                 ksm=False, no_aslr=False, limits=None, cgroup_root=None):
        """root_mode "home": overlay on $HOME only. "full": private copy-on-write view of the
        whole root filesystem (pivot_root). netns: a network namespace per session (veth,
        10.200.<slot>.0/30). osworld_port: start the OSWorld guest-server stub in the session.
        lower_home is a $HOME-shaped tree in "home" mode and a root-shaped tree in "full" mode.
        firewall (default: on whenever netns is): the nftables policy in firewall.py, so sessions cannot
        reach each other whatever `ip_forward` is. nat: also give sessions outbound internet by masquerade;
        this sets the host's net.ipv4.ip_forward=1 (restored by close()). block_private_egress: with nat,
        drop session traffic to private/link-local ranges (cloud metadata, the VPC, the LAN).
        sweep_on_start: first run the outer sweep (sweep.py), which removes what a crashed earlier run left
        (sessions, links, the nftables table, ip_forward) but never touches another LIVE Supervisor. This
        Supervisor then writes a ledger entry (in sweep_dir) so a later sweep can undo its changes if it dies."""
        self.root_mode, self.netns, self.osworld_port = root_mode, netns, osworld_port
        self.sweep_dir, self._ledger = sweep_dir, None
        if exec_systemd and not (netns and os.environ.get("DRUMS_ALLOW_SYSTEMD_PROBE") == "1"):
            # an earlier probe without a netns started the host rootfs's docker.service in the host network
            # namespace (docker0, netfilter tables, ip_forward) and wrote cgroups into the host hierarchy
            raise ValueError("exec_systemd is an experiment with host side effects: it needs netns=True and "
                             "DRUMS_ALLOW_SYSTEMD_PROBE=1, and still writes into the host cgroup hierarchy")
        # PROTOTYPE of L1 mechanism F (docs/l1-shared-runtime-design.md section 8): sessions forked from a zygote
        self.zygote, self.server_mode, self.workload = zygote, server_mode, workload
        self.limits, self.cgroup_root, self._cgroups = limits, cgroup_root, {}  # READINESS: per-session cgroup limits (limits.py)
        self.ksm, self.no_aslr = ksm, no_aslr  # PROTOTYPE of mechanism K (page merging); see l1-shared-runtime-design.md section 12
        self.init_python, self.ready_timeout = init_python or python, ready_timeout
        self.exec_systemd = exec_systemd  # EXPERIMENT: systemd as the session's PID 1 (see systemd_probe.py)
        self.osworld_real_kit = osworld_real_kit  # path to a realkit.py kit: run the REAL OSWorld server
        self.firewall = netns if firewall is None else firewall
        self.nat, self._saved_forward = nat, None
        if self.firewall and not netns:
            raise ValueError("the firewall needs a network namespace per session")
        if nat and not self.firewall:
            raise ValueError("nat requires the firewall (egress without policy is refused)")
        self.state_dir = os.path.abspath(state_dir)
        self.lower = os.path.abspath(lower_home) if lower_home else ""
        self._tplfs = None
        self.python, self.xvfb, self.size, self.display_base = python, xvfb, size, display_base
        self._procs = {}
        self._epochs = {}
        os.makedirs(self.state_dir, exist_ok=True)
        from . import sweep as sw
        self.start_sweep = sw.sweep(ledger=sweep_dir) if sweep_on_start else None
        self.run_id = uuid.uuid4().hex[:12]
        self.tag, self.table = f"drums:{self.run_id}", f"drums_{self.run_id}"
        self._ledger = sw.register(self.state_dir, self.run_id, sweep_dir)
        if self.firewall:
            from . import firewall as fw
            sw.record(self._ledger, "tables", self.table)  # the record comes BEFORE the table exists
            fw.install(nat=nat, block_private=block_private_egress, table=self.table)
            if nat:
                self._saved_forward = sw.read_forward()
                sw.record(self._ledger, "forward", {"prior": self._saved_forward, "set_to": "1"})
                sw.write_forward("1")
        if self.root_mode == "full" and self.lower:
            self._mount_template()

    def _mount_template(self):
        """In "full" mode the overlay's lower layers include `/`, and overlayfs refuses a layer
        that sits inside another layer. So the template is copied once into its own tmpfs,
        mounted on the host and shared (one RAM copy, page cache shared) by every session."""
        self._tplfs = os.path.join(self.state_dir, "tplfs")
        os.makedirs(self._tplfs, exist_ok=True)
        from . import sweep as sw
        sw.record(self._ledger, "mounts", self._tplfs)  # recorded before the mount exists
        subprocess.run(["mount", "-t", "tmpfs", "tmpfs", self._tplfs], check=True)
        subprocess.run(["cp", "-a", self.lower + "/.", self._tplfs], check=True)
        self.lower = self._tplfs

    def close(self):
        for slot in list(self._procs):
            self.destroy(slot)
        if self._tplfs:
            # Sessions are gone, so the template can normally be unmounted at once; fall back to a lazy
            # unmount if something still holds it. A leftover mount would block removing the state dir.
            for _ in range(20):
                if subprocess.run(["umount", self._tplfs], capture_output=True).returncode == 0:
                    break
                time.sleep(0.1)
            else:
                subprocess.run(["umount", "-l", self._tplfs], check=False)
            self._tplfs = None
        if self.firewall:
            from . import firewall as fw
            fw.remove(self.table)
            if self._saved_forward is not None:
                from . import sweep as sw
                sw.write_forward(self._saved_forward)
                self._saved_forward = None
        if self._ledger:
            from . import sweep as sw
            try:
                with open(self._ledger) as f:
                    sw.restore_sysfs(json.load(f).get("sysfs", []))
            except (OSError, ValueError):
                pass
            sw.unregister(self._ledger)
            self._ledger = None

    def _dir(self, slot):
        return os.path.join(self.state_dir, f"s{slot}")

    def display(self, slot):
        return self.display_base + slot

    def create(self, slot):
        d = self._dir(slot)
        if slot in self._procs:
            raise SessionError(f"slot {slot} already running")
        shutil.rmtree(d, ignore_errors=True)
        for sub in ("upper", "work", "x11"):
            os.makedirs(os.path.join(d, sub))
        os.makedirs(HOME, exist_ok=True)
        os.makedirs("/tmp/.X11-unix", exist_ok=True)
        try:
            os.chmod("/tmp/.X11-unix", 0o1777)
        except PermissionError:
            pass
        n = self.display(slot)
        host_link = f"/tmp/.X11-unix/X{n}"
        if os.path.lexists(host_link):
            # only a symlink that is dangling or points into a state dir is ours to replace; anything
            # else (a real X server's socket, someone else's link) is refused, never deleted
            target = os.readlink(host_link) if os.path.islink(host_link) else None
            if target is None or (os.path.exists(host_link) and not target.startswith(self.state_dir + "/")):
                raise SessionError(f"{host_link} exists and is not a Drums link; refusing to replace it")
            os.unlink(host_link)
        from . import sweep as sw
        sw.record(self._ledger, "x11_links", host_link)
        os.symlink(os.path.join(d, "x11", f"X{n}"), host_link)
        ready = os.path.join(d, "ready")
        env = dict(os.environ, PYTHONPATH=PKG_DIR)
        initlog = open(os.path.join(d, "init.log"), "wb")
        timing = os.path.join(d, "timing.jsonl")
        sargs = ["--lower", self.lower, "--upper", os.path.join(d, "upper"), "--work", os.path.join(d, "work"),
                 "--home", HOME, "--x11dir", os.path.join(d, "x11"), "--display", str(n), "--size", self.size,
                 "--xvfb", self.xvfb, "--ready", ready, "--xlog", os.path.join(d, "xvfb.log"),
                 "--root", self.root_mode, "--osworld-port", str(self.osworld_port), "--pkg", PKG_DIR,
                 "--python", self.python, "--tag", self.tag,
                 *(["--osworld-real", self.osworld_real_kit] if self.osworld_real_kit else []),
                 *(["--exec-systemd"] if self.exec_systemd else []),
                 *(["--server-mode", self.server_mode, "--timing", timing] if self.osworld_real_kit else []),
                 *(["--workload", self.workload] if self.workload else []),
                 *(["--ksm"] if self.ksm else []), *(["--no-aslr"] if self.no_aslr else [])]
        cg = None
        if self.limits is not None:
            from . import limits as lim
            cg = lim.SessionCgroup(self.run_id, slot, self.limits, **({"root": self.cgroup_root} if self.cgroup_root else {}))
            for dd in cg.dirs:
                sw.record(self._ledger, "cgroups", dd)  # recorded before the directory exists
            cg.create()
            self._cgroups[slot] = cg
            sargs += [x for f in cg.procs_files() for x in ("--cgroup-procs", f)]
        self.t_request = {**getattr(self, "t_request", {}), slot: time.time()}
        if self.zygote:  # PROTOTYPE: fork from the pre-warmed zygote instead of exec'ing a fresh interpreter
            proc = _ForkedProc(self.zygote.spawn(sargs, env, os.path.join(d, "init.log"), net=self.netns))
        else:
            initlog = open(os.path.join(d, "init.log"), "wb")
            proc = subprocess.Popen(
                ["unshare", "--mount", "--pid", "--ipc", "--uts", *(["--net"] if self.netns else []),
                 "--fork", "--kill-child", "--mount-proc",
                 self.init_python, "-m", "envd_linux.session_init", *sargs],
                env=env, stdout=initlog, stderr=subprocess.STDOUT, start_new_session=True)
            initlog.close()
        deadline = time.time() + self.ready_timeout
        def is_ready():
            try:
                with open(ready) as f:
                    return f.read() == "ready\n"
            except OSError:
                return False

        while not is_ready():
            if proc.poll() is not None or time.time() > deadline:
                proc.kill()
                if os.path.islink(host_link):
                    os.unlink(host_link)
                with open(os.path.join(d, "init.log")) as f:
                    raise SessionError(f"session {slot} did not start: {f.read()[-400:]}")
            time.sleep(0.05)
        kids = [proc.pid] if self.zygote else _child_pids(proc.pid)
        if not kids:
            raise SessionError("session init pid not found")
        self._procs[slot] = proc
        self._epochs[slot] = self._epochs.get(slot, 0) + 1
        info = {"epoch": self._epochs[slot], "display": n, "init_pid": kids[0], "home": HOME,
                "unshare_pid": proc.pid, "created": time.time(), "root_mode": self.root_mode,
                "netns": self.netns, "osworld_port": self.osworld_port}
        if cg is not None:
            info["limits"] = cg.record()
        if self.netns:
            sw.record(self._ledger, "links", f"dvh{slot}")  # recorded before the link exists
            r = subprocess.run([self.python, "-m", "envd_linux.netsetup", "--slot", str(slot), "--pid",
                                str(kids[0]), *(["--firewall", "--table", self.table] if self.firewall else []), "--tag", self.tag], capture_output=True, text=True, env=env)
            if r.returncode != 0:
                self._procs.pop(slot, None)
                proc.kill()
                raise SessionError(f"network setup failed: {r.stderr[-400:]}")
            info.update(json.loads(r.stdout))
        with open(os.path.join(d, "session.json.tmp"), "w") as f:
            json.dump(info, f)
        os.replace(os.path.join(d, "session.json.tmp"), os.path.join(d, "session.json"))
        return info

    def set_host_knob(self, path, value):
        """Write a sysfs/proc knob with a ledger record first; undone by close() (and by the sweep after a crash)."""
        from . import sweep as sw
        return sw.set_sysfs(self._ledger, path, value)

    def info(self, slot):
        with open(os.path.join(self._dir(slot), "session.json")) as f:
            return json.load(f)

    def destroy(self, slot, keep_state=False):
        proc = self._procs.pop(slot, None)
        d = self._dir(slot)
        try:
            info = self.info(slot)
            os.kill(info["init_pid"], signal.SIGKILL)  # killing PID 1 of the namespace kills all of it
        except (OSError, FileNotFoundError, KeyError, ValueError):
            pass
        if proc is not None and not isinstance(proc, _ForkedProc):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
        link = f"/tmp/.X11-unix/X{self.display(slot)}"
        if os.path.islink(link):
            os.unlink(link)
        cg = self._cgroups.pop(slot, None)
        if cg is not None:
            for _ in range(20):  # the killed processes leave the cgroup asynchronously
                if cg.remove():
                    break
                time.sleep(0.1)
        if not keep_state:
            shutil.rmtree(d, ignore_errors=True)

    def reset(self, slot):
        """Destroy and recreate: a new upper layer, new X server, all processes gone. The epoch
        is kept increasing so a daemon notices and reconnects."""
        self.destroy(slot)
        return self.create(slot)

    def upper_bytes(self, slot):
        total = 0
        for root, _, files in os.walk(os.path.join(self._dir(slot), "upper")):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total
