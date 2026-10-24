"""Pre-mount checks for a CUSTOM base root (`Supervisor(base_root=...)`): will the programs the session starts actually exist and be executable INSIDE the new root? After `pivot_root`
the session sees only `overlay(template : base_root)`: the host's `/opt/drums-osworld-real` kit and the host's python 3.12 are NOT there unless the owner put them in the template layer
(and the kit venv's `python` is a symlink to the host's python 3.12, whose interpreter and libraries must exist in the new root). Nothing is injected or assumed: every path is resolved
THROUGH the layers (symlinks followed inside the union, never out to the host), and anything missing is reported so the Supervisor can refuse BEFORE any mount. PROTOTYPE, tested on
synthetic trees only. The required programs are read from `session_init.start_real_desktop` itself, not guessed."""

import ast
import os
import re

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


def desktop_programs(source=None):
    """What `start_real_desktop` starts inside the session root, read from its source (default: session_init.py next to this file)."""
    if source is None:
        source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_init.py")).read()
    for n in ast.parse(source).body:
        if isinstance(n, ast.FunctionDef) and n.name == "start_real_desktop":
            return sorted(set(_call_first_literals(n)))
    raise ValueError("start_real_desktop not found")


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


def check_userland(layers, kit=None, programs=None, server_main_source=None):
    """{"problems": [...], "warnings": [...]}. Problems are things the session needs at start-up and would not find; warnings are per-task needs."""
    layers = [L for L in layers if L and os.path.isdir(L)]
    problems, warnings = [], []
    if not layers:
        return {"problems": ["no layer to look in"], "warnings": []}
    if resolve(LOADER, layers) is None:
        problems.append(f"the dynamic loader {LOADER} is not in the new root: no dynamically linked program can start")
    for prog in (programs if programs is not None else desktop_programs()):
        if find_program(prog, layers) is None:
            problems.append(f"{prog} is not an executable in the new root (the session starts it after the pivot); add it deliberately or use a different base root")
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
    return {"problems": problems, "warnings": warnings}
