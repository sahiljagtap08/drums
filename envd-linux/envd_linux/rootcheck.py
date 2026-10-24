"""Pre-mount checks for a CUSTOM base root (`Supervisor(base_root=...)`): will the programs the session starts actually exist and be executable INSIDE the new root? After `pivot_root`
the session sees only `overlay(template : base_root)`: the host's `/opt/drums-osworld-real` kit and the host's python 3.12 are NOT there unless the owner put them in the template layer
(and the kit venv's `python` is a symlink to the host's python 3.12, whose interpreter and libraries must exist in the new root). Nothing is injected or assumed: every path is resolved
THROUGH the layers (symlinks followed inside the union, never out to the host), and anything missing is reported so the Supervisor can refuse BEFORE any mount. PROTOTYPE, tested on
synthetic trees only. The required programs are read from `session_init.start_real_desktop` itself, not guessed."""

import ast
import os
import re
import struct

PATH_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
LOADER = "/lib64/ld-linux-x86-64.so.2"


def _call_first_literals(func):
    """String constants that are the first element of a list passed as the first argument of a subprocess call inside `func`."""
    out = []
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.List) and n.args[0].elts and isinstance(n.args[0].elts[0], ast.Constant) and isinstance(n.args[0].elts[0].value, str):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("run", "Popen", "check_output", "check_call", "call"):
                out.append(n.args[0].elts[0].value)
    return out


def desktop_programs(source=None, desktop="openbox"):
    """What `start_real_desktop` starts inside the session root, read from its source (default: session_init.py next to this file), plus the chosen desktop's own commands (`WM_COMMANDS`)."""
    if source is None:
        source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_init.py")).read()
    tree = ast.parse(source)
    wm = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "WM_COMMANDS" for t in n.targets):
            wm = ast.literal_eval(n.value)
    extra = []
    if wm:
        if desktop not in wm:
            raise ValueError(f"unknown desktop {desktop!r}")
        extra = [wm[desktop][0]] + (["gnome-shell", "xprop"] if desktop == "gnome" else [])  # gnome-session starts gnome-shell; xprop reads the window manager's name for the identity record
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "start_real_desktop":
            return sorted(set(_call_first_literals(n)) | set(extra))
    raise ValueError("start_real_desktop not found")


def post_pivot_setup_programs(source=None):
    """Programs `setup_full_root` runs through `sh(...)` AFTER its `pivot_root` call (read from the source, in order): they execute from the NEW root (for example the final `umount -l`)."""
    if source is None:
        source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_init.py")).read()
    for n in ast.parse(source).body:
        if isinstance(n, ast.FunctionDef) and n.name == "setup_full_root":
            calls = sorted((c for c in ast.walk(n) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "sh" and c.args and isinstance(c.args[0], ast.Constant)), key=lambda c: (c.lineno, c.col_offset))
            names = [c.args[0].value for c in calls]
            return sorted(set(names[names.index("pivot_root") + 1:])) if "pivot_root" in names else []
    return []


def server_programs(main_py_source):
    """Programs the pinned OSWorld server runs through subprocess (read from its source); these matter per TASK (for example `wmctrl` for activate_window), not at start-up."""
    progs = set()
    for n in ast.walk(ast.parse(main_py_source)):
        if isinstance(n, ast.FunctionDef):
            progs.update(_call_first_literals(n))
    return sorted(p for p in progs if "/" not in p and p.replace("-", "").replace("_", "").isalnum())


def resolve(path, layers, max_links=40):
    """Resolve an absolute `path` inside the union of `layers` (first layer wins), following symlinks relative to the UNION root. Returns (layer_dir, real_path_in_union) or None."""
    parts = [p for p in path.split("/") if p]
    cur, links = [], 0
    while parts:
        p = parts.pop(0)
        if p == ".":
            continue
        if p == "..":
            if cur:
                cur.pop()
            continue
        cand = cur + [p]
        probe = "/" + "/".join(cand)
        hit = next((L for L in layers if os.path.lexists(os.path.join(L, probe.lstrip("/")))), None)
        if hit is None:
            return None
        full = os.path.join(hit, probe.lstrip("/"))
        if os.path.islink(full):
            links += 1
            if links > max_links:
                return None
            tgt = os.readlink(full)
            parts = [x for x in tgt.split("/") if x] + parts
            if tgt.startswith("/"):
                cur = []
            continue
        cur = cand
    final = "/" + "/".join(cur)
    hit = next((L for L in layers if os.path.lexists(os.path.join(L, final.lstrip("/")))), None)
    return (hit, final) if hit else None


LIB_DIRS = ("/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu", "/lib64", "/usr/lib64", "/lib", "/usr/lib", "/usr/local/lib")


def elf_needed(path):
    """DT_NEEDED sonames of an ELF64 little-endian file, read WITHOUT executing it; [] for anything that is not such an ELF (scripts, static binaries)."""
    try:
        with open(path, "rb") as f:
            h = f.read(64)
            if h[:4] != b"\x7fELF" or h[4] != 2 or h[5] != 1:
                return []
            phoff, phentsize, phnum = struct.unpack_from("<Q", h, 32)[0], struct.unpack_from("<H", h, 54)[0], struct.unpack_from("<H", h, 56)[0]
            loads, dyn = [], None
            for i in range(phnum):
                f.seek(phoff + i * phentsize)
                ptype, _, off, vaddr, _, filesz, _, _ = struct.unpack("<IIQQQQQQ", f.read(56))
                if ptype == 1:
                    loads.append((vaddr, off, filesz))
                elif ptype == 2:
                    dyn = (off, filesz)
            if dyn is None:
                return []
            f.seek(dyn[0])
            data = f.read(dyn[1])
            needed, strtab = [], None
            for i in range(0, len(data) - 15, 16):
                tag, val = struct.unpack_from("<qQ", data, i)
                if tag == 0:
                    break
                if tag == 1:
                    needed.append(val)
                elif tag == 5:
                    strtab = val
            if strtab is None:
                return []
            base = next((off + (strtab - va) for va, off, sz in loads if va <= strtab < va + max(sz, 1)), None)
            if base is None:
                return []
            out = []
            for o in needed:
                f.seek(base + o)
                buf = b""
                while not buf.endswith(b"\0"):
                    c = f.read(64)
                    if not c:
                        break
                    buf += c
                out.append(buf.split(b"\0")[0].decode(errors="replace"))
            return out
    except (OSError, struct.error):
        return []


def elf_interp(path):
    """PT_INTERP (the program's own ELF interpreter path) of an ELF64 LE file, else None."""
    try:
        with open(path, "rb") as f:
            h = f.read(64)
            if h[:4] != b"\x7fELF" or h[4] != 2 or h[5] != 1:
                return None
            phoff, phentsize, phnum = struct.unpack_from("<Q", h, 32)[0], struct.unpack_from("<H", h, 54)[0], struct.unpack_from("<H", h, 56)[0]
            for i in range(phnum):
                f.seek(phoff + i * phentsize)
                ptype, _, off, _, _, filesz, _, _ = struct.unpack("<IIQQQQQQ", f.read(56))
                if ptype == 3:
                    f.seek(off)
                    return f.read(filesz).split(b"\0")[0].decode(errors="replace")
    except (OSError, struct.error):
        pass
    return None


def shebang(path):
    """[interpreter, *args] of a `#!` script (first line only), else None."""
    try:
        with open(path, "rb") as f:
            first = f.readline(256)
    except OSError:
        return None
    return first[2:].decode(errors="replace").split() if first.startswith(b"#!") else None


def interpreter_problems(prog, found, layers, depth=0):
    """A program that is a script needs its `#!` interpreter (and, behind `/usr/bin/env`, the named program) in the root; an ELF needs its OWN PT_INTERP, not just the global loader."""
    r = resolve(found, layers)
    real = os.path.join(r[0], r[1].lstrip("/"))
    out = []
    ip = elf_interp(real)
    if ip is not None and (resolve(ip, layers) is None):
        out.append(f"{prog}'s ELF interpreter {ip} is not in the new root")
    sb = shebang(real)
    if sb and depth < 3:
        tgt = sb[0]
        if os.path.basename(tgt) == "env" and len(sb) > 1:
            names = [x for x in sb[1:] if not x.startswith("-")]
            tgt = find_program(names[0], layers) if names else None
            if tgt is None:
                return out + [f"{prog} is a script run through env, but `{names[0] if names else '?'}` is not in the new root"]
        elif find_program(tgt, layers) is None:
            return out + [f"{prog} is a script whose interpreter {tgt} is not an executable in the new root"]
        out += interpreter_problems(f"{prog} (via {tgt})", tgt, layers, depth + 1)
    return out


RUNTIME_FILES = {  # (kind, path, why): checked for presence only. A file here is needed at run time and is NOT a DT_NEEDED
    "Xvfb": [("exe", "xkbcomp", "Xvfb compiles its keymap with xkbcomp"), ("dir", "/usr/share/X11/xkb", "XKB keyboard data"), ("file", "/usr/share/X11/xkb/rules/evdev", "XKB rules")],
}
DLOPEN_NAMES = {  # sonames that may be dlopen()ed at run time: presence is reported as a WARNING only (whether they are needed is not known from the binary)
    "Xvfb": ["libGL.so.1", "libGLX_mesa.so.0", "libgbm.so.1"],
    "gnome-shell": ["libGL.so.1", "libEGL.so.1", "libGLESv2.so.2", "libgbm.so.1"],
}
LIMITS = ["DT_NEEDED and PT_INTERP are read statically; libraries opened with dlopen are only listed by name (DLOPEN_NAMES) and reported as warnings, never proven",
          "ld.so.cache is not read; the search uses the standard directories plus /etc/ld.so.conf.d",
          "programs started by other programs (gnome-session's children, Xvfb's helpers beyond xkbcomp) are not enumerated; the GNOME chain checks a finite named list, not the whole graph",
          "nothing here proves that gnome-shell or Xvfb RUNS: only a live boot does"]


def gnome_chain(layers, uid, gid, user, home):
    problems, warnings = [], []
    for d in ("/usr/share/gnome-shell", "/usr/share/glib-2.0/schemas"):
        if resolve(d, layers) is None:
            problems.append(f"{d} is not in the new root")
    r = resolve("/usr/share/glib-2.0/schemas/gschemas.compiled", layers)
    if r is None:
        problems.append("/usr/share/glib-2.0/schemas/gschemas.compiled is missing: GSettings (gnome-shell, gnome-session) cannot start without compiled schemas")
    if find_program("/usr/libexec/gnome-session-binary", layers) is None:
        problems.append("/usr/libexec/gnome-session-binary is not an executable in the new root (gnome-session starts it)")
    if resolve("/usr/share/gnome-session/sessions/ubuntu-xorg.session", layers) is None:
        problems.append("/usr/share/gnome-session/sessions/ubuntu-xorg.session is missing (the session `--session=ubuntu-xorg` names it)")
    pw = resolve("/etc/passwd", layers)
    line = None
    if pw:
        for l in open(os.path.join(pw[0], pw[1].lstrip("/")), errors="replace"):
            f = l.rstrip("\n").split(":")
            if len(f) >= 6 and f[0] == user:
                line = f
    if line is None:
        problems.append(f"user {user!r} is not in the new root's /etc/passwd (HOME, groups and GSettings need a real account)")
    else:
        if (int(line[2]) if line[2].isdigit() else -1) != uid or (int(line[3]) if line[3].isdigit() else -1) != gid:
            problems.append(f"/etc/passwd has {user} as uid {line[2]} gid {line[3]}, not {uid}/{gid}")
        if home and line[5] != home:
            problems.append(f"/etc/passwd has {user}'s home as {line[5]}, not {home}")
    warnings.append("no system bus and no logind/accountsservice/polkit run inside the session (dbus-daemon --session only); whether gnome-shell tolerates that is UNKNOWN until a live boot")
    warnings.append(f"XDG_RUNTIME_DIR=/run/user/{uid} is created by the init (mode 0700, owned by the user) and not by logind")
    return problems, warnings


def lib_search_dirs(layers):
    dirs = list(LIB_DIRS)
    confd = resolve("/etc/ld.so.conf.d", layers)
    if confd:
        for L in layers:
            d = os.path.join(L, "etc/ld.so.conf.d")
            for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                try:
                    dirs += [l.strip() for l in open(os.path.join(d, fn)) if l.strip().startswith("/")]
                except OSError:
                    pass
    return dirs


def missing_libraries(real_path, layers, seen=None):
    """Sonames in the shared-library closure of the file at `real_path` that are found in NO library directory of the union (ld.so.conf.d entries included; ld.so.cache is not read, so this can
    flag a library that lives in an unlisted directory). Libraries are searched through the union, never the host."""
    seen = set() if seen is None else seen
    out = []
    dirs = lib_search_dirs(layers)
    for so in elf_needed(real_path):
        if so in seen:
            continue
        seen.add(so)
        hit = next((r for d in dirs for r in [resolve(f"{d}/{so}", layers)] if r), None)
        if hit is None:
            out.append(so)
        else:
            out += missing_libraries(os.path.join(hit[0], hit[1].lstrip("/")), layers, seen)
    return out


def find_program(name, layers):
    cands = [name] if name.startswith("/") else [f"{d}/{name}" for d in PATH_DIRS]
    for c in cands:
        r = resolve(c, layers)
        if r and os.path.isfile(os.path.join(r[0], r[1].lstrip("/"))) and os.access(os.path.join(r[0], r[1].lstrip("/")), os.X_OK):
            return r[1]
    return None


def _minor(text):
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _python_minor_problems(kit, layers, interp):
    """The kit's venv was built for ONE python minor (pyvenv.cfg `version`, lib/pythonX.Y); the interpreter it resolves to in the new root must be that minor, or 3.12 site-packages would be
    loaded by a 3.10 guest. Nothing is converted or mixed: a mismatch is a refusal (rebuild the kit for the root's python in the template layer)."""
    out, got = [], _minor(os.path.basename(interp).replace("python", ""))
    cfg = resolve(f"{kit}/venv/pyvenv.cfg", layers)
    if cfg is None:
        return [f"the kit's {kit}/venv/pyvenv.cfg is not visible in the new root, so its python version cannot be checked"]
    want = None
    for line in open(os.path.join(cfg[0], cfg[1].lstrip("/")), errors="replace"):
        k, _, v = line.partition("=")
        if k.strip() in ("version", "version_info"):
            want = _minor(v)
    if want is None or got is None:
        return [f"cannot read the python minor version (kit venv {want}, interpreter {interp}): refusing rather than guessing"]
    if want != got:
        out.append(f"the kit's venv was built for python {want[0]}.{want[1]} but its interpreter in the new root is {interp} ({got[0]}.{got[1]}): host site-packages are never mixed into a different python; rebuild the kit for the root's python in the template layer")
    lib = resolve(f"{kit}/venv/lib/python{want[0]}.{want[1]}", layers)
    if lib is None:
        out.append(f"the kit's {kit}/venv/lib/python{want[0]}.{want[1]} (site-packages) is not visible in the new root")
    return out


def check_userland(layers, kit=None, programs=None, server_main_source=None, desktop="openbox", xvfb="Xvfb", post_pivot=None, uid=-1, gid=-1, user="", home=None):
    """{"problems": [...], "warnings": [...]}. Problems are things the session needs at start-up and would not find; warnings are per-task needs."""
    layers = [L for L in layers if L and os.path.isdir(L)]
    problems, warnings = [], []
    if not layers:
        return {"problems": ["no layer to look in"], "warnings": []}
    if resolve(LOADER, layers) is None:
        problems.append(f"the dynamic loader {LOADER} is not in the new root: no dynamically linked program can start")
    # EVERYTHING that runs after the pivot_root: the desktop programs (read from start_real_desktop), what setup_full_root runs after its pivot (the final `umount`), and the X server, which is
    # started from this root too and whose name is a runtime argument the AST cannot see. The init's own python (mapped from the host) is covered by session_init pre-importing its lazy modules.
    wanted = list(programs if programs is not None else desktop_programs(desktop=desktop))
    wanted += [p for p in (post_pivot if post_pivot is not None else post_pivot_setup_programs()) if p not in wanted]
    if xvfb not in wanted:
        wanted.append(xvfb)
    seen_libs = set()
    for prog in wanted:
        found = find_program(prog, layers)
        if found is None:
            problems.append(f"{prog} is not an executable in the new root (the session starts it after the pivot); add it deliberately or use a different base root"
                            + (" (an X server: the image has none unless it is added to the writable layer; a 24.04 host Xvfb is NOT copied in)" if prog == xvfb else ""))
            continue
        r = resolve(found, layers)
        gone = [so for so in missing_libraries(os.path.join(r[0], r[1].lstrip("/")), layers) if so not in seen_libs]
        seen_libs.update(gone)
        if gone:
            problems.append(f"{prog} needs shared libraries that are not in the new root: {', '.join(gone)}")
        problems += interpreter_problems(prog, found, layers)
        base = os.path.basename(prog)
        for kind, path, why in RUNTIME_FILES.get(base, []):
            ok = (find_program(path, layers) is not None) if kind == "exe" else (resolve(path, layers) is not None)
            if not ok:
                problems.append(f"{path} is missing from the new root ({why}; needed by {base} at run time)")
            elif kind == "exe":
                x = resolve(find_program(path, layers), layers)
                lost = missing_libraries(os.path.join(x[0], x[1].lstrip("/")), layers)
                problems += [f"{path} needs shared libraries that are not in the new root: {', '.join(lost)}"] if lost else []
        dirs = lib_search_dirs(layers)
        for so in DLOPEN_NAMES.get(base, []):
            if not any(resolve(f"{d}/{so}", layers) for d in dirs):
                warnings.append(f"{base} may dlopen {so}, which is not in the new root (not proven needed)")
    if kit:
        if resolve(f"{kit}/server/main.py", layers) is None:
            problems.append(f"the kit's server ({kit}/server/main.py) is not visible in the new root: after the pivot the HOST's {kit} is gone unless it is placed in the template layer")
        py = f"{kit}/venv/bin/python"
        r = resolve(py, layers)
        if r is None:
            problems.append(f"the kit's python ({py}) does not resolve in the new root (a missing file or a symlink to an interpreter the new root does not have, such as the host's /usr/bin/python3.12)")
        elif not os.access(os.path.join(r[0], r[1].lstrip("/")), os.X_OK):
            problems.append(f"the kit's python resolves to {r[1]} but it is not executable")
        else:
            problems += _python_minor_problems(kit, layers, r[1])
    if server_main_source:
        for prog in server_programs(server_main_source):
            if find_program(prog, layers) is None:
                warnings.append(f"the pinned server runs `{prog}` for some tasks and it is not in the new root: those tasks would fail")
    if desktop == "gnome" and uid >= 0:
        gp, gw = gnome_chain(layers, uid, gid, user, home)
        problems += gp
        warnings += gw
        for so in DLOPEN_NAMES["gnome-shell"]:
            if not any(resolve(f"{d}/{so}", layers) for d in lib_search_dirs(layers)):
                warnings.append(f"gnome-shell may dlopen {so}, which is not in the new root (not proven needed)")
    return {"problems": problems, "warnings": warnings, "limits": LIMITS}
