"""Before/after evidence for the two product findings of section 21.2 (controlled, same code): (1) overlayfs rename of a lower-layer directory with and without
redirect_dir=on; (2) sudo in a session with and without a resolvable hostname. Writes JSON.

    python -m envd_linux.l1proto.findings_evidence --out findings_evidence.json     (root; private CRIU via DRUMS_CRIU_HOME)"""

import argparse
import json
import os
import subprocess
import tempfile
import time

from ..criu import Criu
from .criu_probe import make_sup


def overlay_rename():
    tmp = tempfile.mkdtemp(prefix="ovfind")
    out = {}
    try:
        for name in ("lower", "upper"):
            os.makedirs(f"{tmp}/{name}_mnt")
            subprocess.run(["mount", "-t", "tmpfs", "tmpfs", f"{tmp}/{name}_mnt"], check=True)
        os.makedirs(f"{tmp}/lower_mnt/d1")
        os.makedirs(f"{tmp}/upper_mnt/up")
        os.makedirs(f"{tmp}/upper_mnt/wk")
        os.makedirs(f"{tmp}/m")
        for label, opt in (("default", ""), ("redirect_dir=on", ",redirect_dir=on")):
            subprocess.run(["mount", "-t", "overlay", "overlay", "-o", f"lowerdir={tmp}/lower_mnt,upperdir={tmp}/upper_mnt/up,workdir={tmp}/upper_mnt/wk{opt}", f"{tmp}/m"], check=True)
            try:
                os.rename(f"{tmp}/m/d1", f"{tmp}/m/d2")
                out[label] = "rename OK"
            except OSError as e:
                out[label] = f"rename failed: {e}"
            finally:
                subprocess.run(["umount", f"{tmp}/m"], check=False)
                subprocess.run(["rm", "-rf", f"{tmp}/upper_mnt/up", f"{tmp}/upper_mnt/wk"])
                os.makedirs(f"{tmp}/upper_mnt/up")
                os.makedirs(f"{tmp}/upper_mnt/wk")
                if label == "default":
                    pass
    finally:
        for name in ("lower", "upper"):
            subprocess.run(["umount", "-l", f"{tmp}/{name}_mnt"], check=False)
    return out


def sudo_stall():
    tmp = tempfile.mkdtemp(prefix="sudofind")
    sup = make_sup(tmp, "t", "", 90, Criu())
    out = {}
    try:
        i = sup.create(0)

        def run(cmd):
            t = time.time()
            r = subprocess.run(["nsenter", "-t", str(i["init_pid"]), "-m", "-u", "-n", "-p", "--", "sh", "-c", cmd], capture_output=True, text=True, timeout=120)
            return {"seconds": round(time.time() - t, 2), "output": (r.stdout + r.stderr).strip()[:160]}
        out["with_hostname_in_etc_hosts (current code)"] = run("echo x | sudo -S true")
        run("sed -i '/^127.0.1.1 /d' /etc/hosts")  # recreate the old state: the hostname does not resolve
        out["without_hostname_in_etc_hosts (the old behaviour)"] = run("echo x | sudo -S true")
    finally:
        sup.close()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    res = {"overlay_rename_of_a_lower_layer_directory": overlay_rename(), "sudo_in_a_session": sudo_stall()}
    json.dump(res, open(a.out, "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
