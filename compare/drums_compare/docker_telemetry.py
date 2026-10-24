"""Per-environment capture for the STOCK Docker/KVM incumbent: the live container's cgroup directory, fed to `envd_linux.cgroup_telemetry.CgroupProbe` (cgroup v2: the container is
created and removed on every cold reset, so the directory set changes during a block and the probe banks CPU per directory). PROTOTYPE, never run against a real container."""

import os


def docker_cgroup_dirs(runner, exists=os.path.exists, root="/sys/fs/cgroup"):
    """`runner(cmd) -> (rc, text)`. For each running container: the systemd-driver scope `system.slice/docker-<id>.scope` or the cgroupfs-driver `docker/<id>`; only existing
    directories are returned (a container that vanished between `docker ps` and the read is simply absent)."""
    rc, out = runner(["docker", "ps", "-q", "--no-trunc"])
    if rc != 0:
        return []
    dirs = []
    for cid in out.split():
        for cand in (os.path.join(root, "system.slice", f"docker-{cid}.scope"), os.path.join(root, "docker", cid)):
            if exists(cand):
                dirs.append(cand)
                break
    return dirs


def container_facts(runner):
    """Facts about the running container(s) read from `docker inspect`: devices, whether KVM was silently disabled, and the guest budget actually passed."""
    rc, ids = runner(["docker", "ps", "-q"])
    out = []
    for cid in ids.split() if rc == 0 else []:
        rc2, text = runner(["docker", "inspect", cid, "--format", "{{json .HostConfig.Devices}}|{{json .Config.Env}}|{{.Image}}"])
        if rc2 != 0:
            continue
        dev, env, img = text.split("|", 2)
        out.append({"id": cid[:12], "devices": dev, "env": env, "image": img[:80], "kvm_disabled": '"KVM=N"' in env, "has_kvm_device": "/dev/kvm" in dev})
    return out
