"""Exact image bytes WITHOUT downloading the image: the zip's end-of-central-directory and central directory are read with HTTP range requests (a few KB to MB), giving the
compressed size and the true UNCOMPRESSED size of every member, plus the server's checksum header. Owner-run BEFORE approval, from any machine that can reach the source; it is a
metadata read, not an image download, and it never touches AWS.

    python -m drums_compare.imagesize https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip

The disk budget is then arithmetic over MEASURED numbers plus clearly named ALLOWANCES for what cannot be measured beforehand (never presented as measurements)."""

import json
import math
import struct
import sys
import urllib.request

EOCD, EOCD64, LOC64, CDFH = 0x06054B50, 0x06064B50, 0x07064B50, 0x02014B50


def _u(fmt, b, off=0):
    return struct.unpack_from(fmt, b, off)


def parse_tail(tail):
    """From the last bytes of a zip: (central directory offset, size, entries), reading the zip64 records when present."""
    i = tail.rfind(struct.pack("<I", EOCD))
    if i < 0:
        raise ValueError("no end-of-central-directory record in the tail")
    _, _, _, _, entries, cd_size, cd_off, _ = _u("<IHHHHIIH", tail, i)
    if 0xFFFFFFFF in (cd_size, cd_off) or entries == 0xFFFF:
        j = tail.rfind(struct.pack("<I", LOC64), 0, i)
        if j < 0:
            raise ValueError("zip64 markers without a zip64 locator in the tail")
        z = tail.rfind(struct.pack("<I", EOCD64), 0, j)
        if z < 0:
            raise ValueError("zip64 end record is outside the fetched tail")
        _, _, _, _, _, _, entries, _, cd_size, cd_off = _u("<IQHHIIQQQQ", tail, z)
    return cd_off, cd_size, entries


def parse_central_directory(cd):
    out, p = [], 0
    while p + 46 <= len(cd) and _u("<I", cd, p)[0] == CDFH:
        (_, _, _, _, _, _, _, _, comp, unc, n, m, k, _, _, _, off) = _u("<IHHHHHHIIIHHHHHII", cd, p)
        name = cd[p + 46:p + 46 + n].decode("utf-8", "replace")
        extra = cd[p + 46 + n:p + 46 + n + m]
        q = 0
        while q + 4 <= len(extra):  # zip64 extended information: values appear only for the fields that were 0xFFFFFFFF, in this order
            hid, sz = _u("<HH", extra, q)
            if hid == 1:
                body, r = extra[q + 4:q + 4 + sz], 0
                if unc == 0xFFFFFFFF:
                    unc, r = _u("<Q", body, r)[0], r + 8
                if comp == 0xFFFFFFFF:
                    comp = _u("<Q", body, r)[0]
            q += 4 + sz
        out.append({"name": name, "compressed": comp, "uncompressed": unc})
        p += 46 + n + m + k
    return out


def sizing(url, head, fetch, tail_bytes=1 << 17):
    """`head(url) -> {'size': int, headers...}`; `fetch(url, start, end) -> bytes` (inclusive range)."""
    h = head(url)
    size = h["size"]
    tail = fetch(url, max(0, size - tail_bytes), size - 1)
    cd_off, cd_size, entries = parse_tail(tail)
    cd = fetch(url, cd_off, cd_off + cd_size - 1)
    members = parse_central_directory(cd)
    if len(members) != entries:
        raise ValueError(f"central directory lists {len(members)} entries, end record says {entries}")
    return {"url": url, "zip_bytes": size, "members": members, "uncompressed_total_bytes": sum(m["uncompressed"] for m in members),
            "checksum_headers": {k: v for k, v in h.items() if k in ("x-linked-etag", "etag", "x-linked-size")},
            "note": "the checksum header is the server's claim (a sha256 for LFS-backed files); it is recorded, and verified only against the downloaded bytes on the host"}


DEFAULT_ALLOWANCES = {"os_and_packages_gb": 20, "docker_image_extracted_gb": 2, "container_writable_gb": 32, "drums_state_gb": 5, "margin_fraction": 0.15}


def disk_budget(zip_bytes, uncompressed_bytes, allowances=None, keep_zip_until_verified=True):
    """gp3 GB to request. MEASURED: the zip and its uncompressed contents (peak holds both while extracting). ALLOWANCES (named, owner-adjustable, NOT measurements): the OS plus the
    apt packages, the extracted Docker image, the container's writable ceiling (the stock provider's DISK_SIZE), Drums template and session state. Disk is cheap relative to the
    run, so every allowance is a ceiling, rounded up to a whole 10 GB."""
    a = dict(DEFAULT_ALLOWANCES, **(allowances or {}))
    gb = 1e9
    measured = {"zip_gb": zip_bytes / gb if keep_zip_until_verified else 0.0, "extracted_gb": uncompressed_bytes / gb}
    subtotal = sum(measured.values()) + sum(v for k, v in a.items() if k.endswith("_gb"))
    total = subtotal * (1 + a["margin_fraction"])
    return {"measured": measured, "allowances": a, "subtotal_gb": subtotal, "total_gb": total, "ebs_gb_to_request": int(math.ceil(total / 10.0) * 10),
            "unit": "decimal GB; EBS sizes are GiB, so the rounded figure carries at least a 7% extra margin"}


def _head(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        h = {k.lower(): v for k, v in r.getheaders()}
    h["size"] = int(h.get("x-linked-size") or h["content-length"])
    return h


def _fetch(url, start, end):
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main(argv=None):
    url = (argv or sys.argv[1:])[0]
    s = sizing(url, _head, _fetch)
    s["disk_budget"] = disk_budget(s["zip_bytes"], s["uncompressed_total_bytes"])
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
