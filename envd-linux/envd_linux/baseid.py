"""Identity of what a CRIU checkpoint was taken on, and what a restore may be mounted on. PROTOTYPE, tested with fakes only.

A restored session's processes hold pointers into files of the filesystem they were dumped on. The restore overlay used to end in a hard-coded `/` (the HOST root); with an image base root that
would restore the HOST userland under processes that were running on the image. So a checkpoint now records WHAT it ran on, and a restore refuses unless the same thing is present:
  * the base root's path and its measured identity: read-only mount, filesystem type, sha256 of `/etc/os-release` and of `/var/lib/dpkg/status` (the installed-software identity), plus the pinned image
    sha256 the owner declared (the mount itself cannot be tied to that hash here, so it is a declaration, recorded as such);
  * the template layer's identity (a listing hash of the tree: paths, sizes, modes, link targets);
  * the desktop configuration: desktop, uid/gid/user, home, private bus, X server.
A checkpoint without `meta_version` is LEGACY: it was taken on `/`, and is accepted only by a Supervisor whose base root is `/`. A newer or unknown version is refused."""
import hashlib
import json
import os
import subprocess

META_VERSION = 2


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def tree_listing_sha(path):
    """Hash of a directory tree's listing (relative path, type, size, mode, link target); None if it does not exist."""
    if not path or not os.path.isdir(path):
        return None
    rows = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for n in sorted(files + dirs):
            p = os.path.join(root, n)
            st = os.lstat(p)
            rows.append([os.path.relpath(p, path), st.st_mode, st.st_size if not os.path.isdir(p) else 0, os.readlink(p) if os.path.islink(p) else None])
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def measure_base(base_root, runner=None):
    """What the mounted base root actually is (measured, not declared). `runner(cmd) -> stdout` is replaceable in tests."""
    runner = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    fs, opts = (runner(["findmnt", "-n", "-o", "FSTYPE,OPTIONS", "--target", base_root]).split() + ["", ""])[:2]
    read = lambda rel: sha256_file(os.path.join(base_root, rel)) if os.path.isfile(os.path.join(base_root, rel)) else None
    return {"base_root": base_root, "fstype": fs, "read_only": "ro" in opts.split(","), "os_release_sha256": read("etc/os-release"), "dpkg_status_sha256": read("var/lib/dpkg/status")}


def conf_of(sup):
    return {"desktop": sup.desktop, "uid": sup.uid, "gid": sup.gid, "user": sup.user, "home": sup.home, "system_bus": sup.system_bus, "xvfb": sup.xvfb}


def current_identity(sup, measure=measure_base):
    """The identity of the Supervisor's own configuration, for a checkpoint to record or a restore to compare."""
    custom = sup.base_root != "/"
    return {"meta_version": META_VERSION, "base_root": sup.base_root, "image_sha256": getattr(sup, "image_sha256", None),
            "base": measure(sup.base_root) if custom else None, "template_sha256": tree_listing_sha(sup.lower) if custom else None, "conf": conf_of(sup)}


def check_restore(meta, current):
    """Problems that forbid restoring `meta` under `current`; empty means the restore may proceed. Judged BEFORE any mount."""
    v = meta.get("meta_version")
    if v is None:  # legacy: taken on the host root
        return [] if current["base_root"] == "/" else [f"the checkpoint is legacy (no meta_version): it was taken on '/', but this Supervisor's base root is {current['base_root']}"]
    if v != META_VERSION:
        return [f"unknown checkpoint meta_version {v!r} (this code reads {META_VERSION})"]
    out = []
    if meta.get("base_root") != current["base_root"]:
        out.append(f"the checkpoint's base root is {meta.get('base_root')!r}, this Supervisor's is {current['base_root']!r}")
        return out
    if current["base_root"] != "/":
        if not meta.get("image_sha256") or meta.get("image_sha256") != current.get("image_sha256"):
            out.append(f"the pinned image sha256 differs (checkpoint {meta.get('image_sha256')!r}, now {current.get('image_sha256')!r})")
        mb, cb = meta.get("base") or {}, current.get("base") or {}
        if not cb.get("read_only"):
            out.append("the base root is not mounted read-only now")
        for k in ("fstype", "os_release_sha256", "dpkg_status_sha256"):
            if mb.get(k) is None or mb.get(k) != cb.get(k):
                out.append(f"the base root's {k} differs or is unknown (checkpoint {mb.get(k)!r}, now {cb.get(k)!r})")
        if not meta.get("template_sha256") or meta.get("template_sha256") != current.get("template_sha256"):
            out.append("the template layer's listing hash differs or is missing")
    if meta.get("conf") != current["conf"]:
        out.append(f"the desktop/uid/bus configuration differs (checkpoint {meta.get('conf')}, now {current['conf']})")
    return out


def restore_lowerdir(layers, lower, base_root):
    """lowerdir of the restore overlay: the checkpoint's read-only layers, the template, and the checkpoint's OWN base root (`/` for a legacy checkpoint, never silently the host root for an image)."""
    return ":".join([*layers, *([lower] if lower else []), base_root])
