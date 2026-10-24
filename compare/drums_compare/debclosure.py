"""Offline dependency-closure check of a set of .deb files against the pinned image's package list (the real run's `image_manifest`, `package=version` per line, or a JSON list of the same).
Reads each deb's control with `dpkg-deb -f` and compares versions with `dpkg --compare-versions`; installs nothing and downloads nothing. LIMITS: the manifest carries no `Provides`, so an
alternative that is only a virtual package is reported as unresolved (verify by hand); `Breaks`/`Conflicts` are not evaluated; a deb whose package is ALREADY in the image is reported (same
version: informational; different version: a problem, since the pinned image must not be upgraded)."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

REL = re.compile(r"^\s*([^\s(:]+)(?::\w+)?\s*(?:\(\s*(<<|<=|=|>=|>>|<|>)\s*([^)\s]+)\s*\))?")


def parse_depends(text):
    """'a (>= 1) | b, c' -> [[('a','>=','1'), ('b',None,None)], [('c',None,None)]]"""
    out = []
    for grp in (text or "").split(","):
        alts = []
        for alt in grp.split("|"):
            m = REL.match(alt)
            if m:
                alts.append((m.group(1), m.group(2), m.group(3)))
        if alts:
            out.append(alts)
    return out


def version_ok(have, op, want, cmp=None):
    if op is None:
        return True
    op = {"<": "lt", ">": "gt", "<<": "lt", ">>": "gt", "<=": "le", ">=": "ge", "=": "eq"}[op]
    cmp = cmp or (lambda a, o, b: subprocess.run(["dpkg", "--compare-versions", a, o, b]).returncode == 0)
    return cmp(have, op, want)


def check(image, debs, cmp=None):
    """image: {package: version}; debs: [{"Package","Version","Depends","Pre-Depends"}]. -> {"problems": [...], "notes": [...]}"""
    problems, notes = [], []
    new = {d["Package"]: d["Version"] for d in debs}
    for d in debs:
        pk = d["Package"]
        if pk in image and image[pk] != d["Version"]:
            problems.append(f"{pk} {d['Version']} would REPLACE the image's {pk} {image[pk]} (the pinned image must not be upgraded or downgraded)")
        elif pk in image:
            notes.append(f"{pk} {d['Version']} is already in the image at the same version (its files must be identical; add_debs_to_overlay.sh checks that)")
        for field in ("Pre-Depends", "Depends"):
            for alts in parse_depends(d.get(field)):
                ok = False
                for name, op, want in alts:
                    have = new.get(name, image.get(name))
                    if have is not None and version_ok(have, op, want, cmp):
                        ok = True
                        break
                if not ok:
                    problems.append(f"{pk} {field}: unresolved `{' | '.join(n + (f' ({o} {v})' if o else '') for n, o, v in alts)}` (image has: {', '.join(f'{n}={image[n]}' for n, _, _ in alts if n in image) or 'none of them'}; the manifest lists no Provides, so a virtual package needs a manual check)")
    return {"problems": problems, "notes": notes}


def read_image(path):
    text = open(path).read()
    try:
        items = json.loads(text)
        lines = [x if isinstance(x, str) else f"{x[0]}={x[1]}" for x in (items.get("dpkg") if isinstance(items, dict) else items)]
    except ValueError:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
    return dict(l.split("=", 1) for l in lines if "=" in l)


def read_debs(directory, runner=lambda cmd: subprocess.run(cmd, capture_output=True, text=True, check=True).stdout):
    out = []
    for f in sorted(os.listdir(directory)):
        if f.endswith(".deb"):
            p = os.path.join(directory, f)
            d = {k: runner(["dpkg-deb", "-f", p, k]).strip() for k in ("Package", "Version", "Depends", "Pre-Depends", "Architecture")}
            d["file"], d["sha256"] = f, hashlib.sha256(open(p, "rb").read()).hexdigest()
            out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-dpkg", required=True)
    ap.add_argument("--debs", required=True)
    a = ap.parse_args(argv)
    debs = read_debs(a.debs)
    r = check(read_image(a.image_dpkg), debs)
    r["debs"] = [{k: d[k] for k in ("file", "Package", "Version", "sha256")} for d in debs]
    print(json.dumps(r, indent=1))
    return 1 if r["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
