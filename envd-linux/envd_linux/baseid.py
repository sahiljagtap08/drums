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
    """Identity of a directory tree: for every entry its relative path, type, mode and size, the TARGET of a symlink and the SHA-256 of the BYTES of a regular file (a same-length change to the kit or to the
    copied stage-2 package therefore changes it). None if the tree does not exist. Reads every file once: a kit with a virtualenv takes seconds, not minutes."""
    if not path or not os.path.isdir(path):
        return None
    rows = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for n in sorted(files + dirs):
            p = os.path.join(root, n)
            st = os.lstat(p)
            if os.path.islink(p):
                kind, extra = "l", os.readlink(p)
            elif os.path.isdir(p):
                kind, extra = "d", None
            elif os.path.isfile(p):
                kind, extra = "f", sha256_file(p)
            else:
                kind, extra = "o", None
            rows.append([os.path.relpath(p, path), kind, st.st_mode, st.st_size if kind == "f" else 0, extra])
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def measure_base(base_root, runner=None):
    """What the mounted base root actually is (measured, not declared). The path must be EXACTLY a mount point (`findmnt --mountpoint`; `--target` would resolve an ancestor mount and describe the wrong
    filesystem); a missing mount, an unreadable answer or a source that is not a block device is an error, never a quiet default. `runner(cmd) -> stdout` is replaceable in tests."""
    runner = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    try:
        out = runner(["findmnt", "-n", "-o", "TARGET,FSTYPE,OPTIONS,SOURCE", "--mountpoint", base_root])
    except subprocess.CalledProcessError as e:
        raise BaseIdentityError(f"{base_root} is not a mount point (findmnt --mountpoint failed)") from e
    f = out.split()
    if len(f) < 4 or f[0] != base_root:
        raise BaseIdentityError(f"{base_root} is not exactly a mount point: findmnt answered {out.strip()!r}")
    _, fs, opts, src = f[:4]
    read = lambda rel: sha256_file(os.path.join(base_root, rel)) if os.path.isfile(os.path.join(base_root, rel)) else None
    return {"base_root": base_root, "fstype": fs, "read_only": "ro" in opts.split(","), "source": src, "os_release_sha256": read("etc/os-release"), "dpkg_status_sha256": read("var/lib/dpkg/status")}


class BaseIdentityError(RuntimeError):
    pass


def _nbd_image_for(dev, proc="/proc"):
    """The image file a qemu-nbd process is serving on /dev/nbdN, read from its command line."""
    for p in sorted(os.listdir(proc)):
        if not p.isdigit():
            continue
        try:
            argv = open(f"{proc}/{p}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in argv if a]
        if argv and os.path.basename(argv[0]).startswith("qemu-nbd"):
            conn = [a for a in argv if a == f"--connect={dev}" or a == f"-c{dev}"] or ([dev] if "-c" in argv and dev in argv else [])
            if conn:
                imgs = [a for a in argv[1:] if not a.startswith("-") and a != dev]
                if imgs:
                    return imgs[-1]
    return None


def verify_image_binding(base_root, expected_sha256, runner=None, proc="/proc", hasher=sha256_file):
    """The RECEIPT that ties the pinned qcow2 to the mounted root: exact mount point, read-only, source `/dev/nbdNpM`, the qemu-nbd process serving /dev/nbdN and the image file it opened, and the
    SHA-256 COMPUTED now over that file. `verified` is True only if the computed hash equals the pinned one and every link of the chain holds; otherwise `problems` says which. Run on the host that holds
    the mount (it reads the whole image once)."""
    problems = []
    rec = {"base_root": base_root, "expected_sha256": expected_sha256, "verified": False, "problems": problems}
    try:
        m = measure_base(base_root, runner)
    except BaseIdentityError as e:
        problems.append(str(e))
        return rec
    rec["mount"] = m
    if not m["read_only"]:
        problems.append("the base root is not mounted read-only")
    import re
    mm = re.fullmatch(r"/dev/(nbd\d+)p\d+", m["source"])
    if not mm:
        problems.append(f"the mount source {m['source']!r} is not an nbd partition")
        return rec
    dev = "/dev/" + mm.group(1)
    img = _nbd_image_for(dev, proc)
    rec["nbd_device"], rec["image_path"] = dev, img
    if img is None or not os.path.isfile(img):
        problems.append(f"no qemu-nbd process serving {dev} was found (or its image file is missing)")
        return rec
    st = os.stat(img)
    rec["image_size"], rec["image_mtime_ns"] = st.st_size, st.st_mtime_ns
    rec["computed_sha256"] = hasher(img)
    if rec["computed_sha256"] != expected_sha256:
        problems.append(f"the served image's sha256 {rec['computed_sha256']} is not the pinned {expected_sha256}")
    rec["verified"] = not problems
    return rec


def rebind_problems(receipt, runner=None, proc="/proc"):
    """Cheap re-check at checkpoint/restore time (no re-hash): the same exact mount, still read-only, still served by a qemu-nbd on the SAME image file whose size and mtime are unchanged."""
    if not isinstance(receipt, dict) or receipt.get("verified") is not True:
        return ["there is no verified image receipt"]
    try:
        m = measure_base(receipt["base_root"], runner)
    except BaseIdentityError as e:
        return [str(e)]
    out = []
    if not m["read_only"]:
        out.append("the base root is not mounted read-only now")
    import re
    mm = re.fullmatch(r"/dev/(nbd\d+)p\d+", m["source"])
    img = _nbd_image_for("/dev/" + mm.group(1), proc) if mm else None
    if img != receipt.get("image_path"):
        out.append(f"the root is now served from {img!r}, the receipt names {receipt.get('image_path')!r}")
    elif os.path.isfile(img):
        st = os.stat(img)
        if (st.st_size, st.st_mtime_ns) != (receipt.get("image_size"), receipt.get("image_mtime_ns")):
            out.append("the image file's size or mtime changed since it was verified")
    return out


def conf_of(sup):
    return {"desktop": sup.desktop, "uid": sup.uid, "gid": sup.gid, "user": sup.user, "home": sup.home, "system_bus": sup.system_bus, "xvfb": sup.xvfb}


def current_identity(sup, measure=measure_base):
    """The identity of the Supervisor's own configuration, for a checkpoint to record or a restore to compare."""
    custom = sup.base_root != "/"
    rec = getattr(sup, "image_receipt", None)
    return {"meta_version": META_VERSION, "base_root": sup.base_root, "image_sha256": getattr(sup, "image_sha256", None),
            "image_receipt": ({k: rec.get(k) for k in ("verified", "computed_sha256", "image_path", "image_size", "image_mtime_ns", "nbd_device")} if (custom and rec) else None),
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
        mr, cr_ = meta.get("image_receipt") or {}, current.get("image_receipt") or {}
        if mr.get("verified") is not True or mr.get("computed_sha256") != meta.get("image_sha256"):
            out.append("the checkpoint carries no VERIFIED image receipt whose computed hash equals the pinned one (a declared hash is not proof)")
        for k in ("computed_sha256", "image_path", "image_size", "image_mtime_ns"):
            if mr.get(k) is None or mr.get(k) != cr_.get(k):
                out.append(f"the image receipt's {k} differs or is unknown (checkpoint {mr.get(k)!r}, now {cr_.get(k)!r})")
        mb, cb = meta.get("base") or {}, current.get("base") or {}
        if not cb.get("read_only"):
            out.append("the base root is not mounted read-only now")
        for k in ("fstype", "os_release_sha256", "dpkg_status_sha256"):  # the mount SOURCE (/dev/nbdN) legitimately differs between runs; the receipt binds it to the image
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
