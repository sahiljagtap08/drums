"""Host-side validation of the private, non-activating system bus, as a repeatable tool (not ad hoc). Starts TWO daemons from the Drums-generated configuration of a stock `system.conf`, each with its own
socket, and records: the daemon pid and its mount-namespace id, the socket inode, the bus GetId, the activatable names (and whether any service file of the image's service directories is listed),
the daemon's child processes, and the host's own system-bus socket before and after. `ok` only if the IDs and sockets differ, nothing from the image's service directories is activatable, no child
process exists and the host socket is unchanged. It starts nothing but the two daemons and kills them at the end. It proves the daemon CLI and configuration only: it runs in the HOST mount namespace,
so the session-namespace isolation still needs the live boot (where `boot_validate` records the same facts from inside).
    python -m drums_compare.bus_validate [--system-conf /usr/share/dbus-1/system.conf] [--out report.json]"""
import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import tempfile

HOST_SOCKET = "/run/dbus/system_bus_socket"


def stat_sig(path):
    try:
        st = os.stat(path)
        return [st.st_ino, st.st_mtime_ns]
    except OSError:
        return None


def service_names(conf_dir_hint="/usr/share/dbus-1"):
    """Activatable names advertised by .service files of the standard system service directories (for the inventory)."""
    names = []
    for d in ("/usr/share/dbus-1/system-services", "/usr/local/share/dbus-1/system-services", "/lib/dbus-1/system-services"):
        for f in sorted(glob.glob(os.path.join(d, "*.service"))):
            for line in open(f, errors="replace"):
                if line.startswith("Name="):
                    names.append(line.split("=", 1)[1].strip())
    return names


def identity_problems(buses):
    """Identity of two buses. A file's inode number is unique only WITHIN one filesystem, so two sockets share a file only if (st_dev, st_ino) are both equal; equal inodes on different devices (two private /run
    tmpfs mounts) are NOT a collision. The probes themselves must succeed: a failed GetId or ListActivatableNames is a problem, never a pass by absence."""
    out = []
    a, b = buses
    for x in (a, b):
        if x.get("get_id_rc") != 0 or not x.get("id"):
            out.append(f"GetId failed on {x.get('socket')} (rc {x.get('get_id_rc')}): the bus identity is unknown")
        if x.get("activatable_rc") != 0:
            out.append(f"ListActivatableNames failed on {x.get('socket')} (rc {x.get('activatable_rc')}): the activation inventory is unknown, not empty")
    if a.get("id") and a.get("id") == b.get("id"):
        out.append("the two buses have the same ID")
    if a["socket"] == b["socket"] or (a["socket_dev"], a["socket_inode"]) == (b["socket_dev"], b["socket_inode"]):
        out.append("the two buses share a socket file (same device and inode)")
    return out


def run(system_conf, runner=subprocess.run):
    from envd_linux import session_init as SI
    report = {"host_socket_before": stat_sig(HOST_SOCKET), "buses": [], "problems": []}
    pids = []
    tmp = tempfile.mkdtemp(prefix="drums-busval-")
    try:
        text = open(system_conf).read()
        installed = service_names()
        report["image_activatable_inventory"] = installed
        for n in (1, 2):
            sock = os.path.join(tmp, f"bus{n}.sock")
            conf = os.path.join(tmp, f"bus{n}.conf")
            open(conf, "w").write(SI.private_system_conf(text, os.path.dirname(os.path.abspath(system_conf)), sock, os.path.join(tmp, f'bus{n}.pid')))
            r = runner(SI.system_bus_command(conf), capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                report["problems"].append(f"bus {n} did not start: {r.stderr[-300:]}")
                break
            pid = next(int(t) for t in r.stdout.split() if t.isdigit())
            pids.append(pid)
            addr = f"unix:path={sock}"

            def call(member, dest="org.freedesktop.DBus", path="/org/freedesktop/DBus"):
                return runner(["dbus-send", f"--bus={addr}", "--print-reply", f"--dest={dest}", path, member], capture_output=True, text=True, timeout=30)
            gid = call("org.freedesktop.DBus.GetId")
            act = call("org.freedesktop.DBus.ListActivatableNames")
            kids = runner(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
            st = os.stat(sock)
            report["buses"].append({"pid": pid, "socket": sock, "socket_inode": st.st_ino, "socket_dev": st.st_dev,
                                    "get_id_rc": gid.returncode, "activatable_rc": act.returncode, "mnt_ns": os.readlink(f"/proc/{pid}/ns/mnt"), "id": gid.stdout.split('"')[1] if '"' in gid.stdout else None,
                                    "activatable_leaked": [x for x in installed if f'"{x}"' in act.stdout], "child_pids": kids})
    except Exception as e:
        report["problems"].append(f"{type(e).__name__}: {e}")
    finally:
        for p in pids:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass
    report["host_socket_after"] = stat_sig(HOST_SOCKET)
    b = report["buses"]
    if len(b) == 2:
        problems = identity_problems(b)
        report["problems"] += problems
        for x in b:
            if x["activatable_leaked"]:
                report["problems"].append(f"activatable names leaked from the image's service directories: {x['activatable_leaked']}")
            if x["child_pids"]:
                report["problems"].append(f"a bus has child processes: {x['child_pids']}")
    else:
        report["problems"].append("fewer than two buses were started")
    if report["host_socket_before"] != report["host_socket_after"]:
        report["problems"].append("the host's own system-bus socket changed")
    report["ok"] = not report["problems"]
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--system-conf", default="/usr/share/dbus-1/system.conf")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    r = run(a.system_conf)
    text = json.dumps(r, indent=1)
    if a.out:
        open(a.out, "w").write(text)
    print(text)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
