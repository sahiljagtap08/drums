import os, sys, tempfile, time, subprocess
sys.path.insert(0, "/home/user/drums/envd-linux")
from envd_linux.l1proto.criu_probe import make_sup
from envd_linux.criu import Criu
tmp = tempfile.mkdtemp(prefix="sudodbg"); sup = make_sup(tmp, "t", "", 90, Criu())
try:
    i = sup.create(0)
    def run(cmd):
        t = time.time(); r = subprocess.run(["nsenter", "-t", str(i["init_pid"]), "-m", "-u", "-n", "-p", "--", "sh", "-c", cmd], capture_output=True, text=True, timeout=120)
        return round(time.time() - t, 2), (r.stdout + r.stderr).strip()[:160]
    print("hostname", run("hostname"))
    print("hosts", run("cat /etc/hosts; getent hosts $(hostname) || echo NO-RESOLVE"))
    print("sudo", run("echo x | sudo -S true"))
finally:
    sup.close()
