"""Exact image bytes WITHOUT downloading the image: the zip's end-of-central-directory and central directory are read with HTTP range requests (a few KB to MB), giving the
compressed size and the true UNCOMPRESSED size of every member, plus the server's checksum header. Owner-run BEFORE approval, from any machine that can reach the source; it is a
metadata read, not an image download, and it never touches AWS.

    python -m drums_compare.imagesize [url]      # default: the commit-pinned URL in KNOWN_ARTIFACT

The disk budget is then arithmetic over MEASURED numbers plus clearly named ALLOWANCES for what cannot be measured beforehand (never presented as measurements)."""

import json
import math
import re
import struct
import sys
import urllib.request

MAX_RANGE = 16 << 20  # no single range read may exceed this; the central directory of the known artifact is 102 bytes
# The artifact the small stage is pinned to: the repository COMMIT in the URL (never the mutable `main`), the exact bytes, and the LFS sha256 of the zip. Read by the independent review
# through this tool's range read (end 131072 bytes and a 102-byte central directory, both answered 206) and cross-checked against the LFS pointer file.
KNOWN_ARTIFACT = {
    "commit": "a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133",
    "url": "https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133/Ubuntu.qcow2.zip",
    "zip_bytes": 12273896463,
    "zip_sha256": "b795b6cd4c69b252c1b4f10150a347795555032501b60fd031751ed09b896712",
    "member": "Ubuntu.qcow2", "member_compressed_bytes": 12273896173, "member_uncompressed_bytes": 24460197888,
    "central_directory": {"offset": 12273896263, "size": 102, "entries": 1},
}
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


def check_known(result, known=KNOWN_ARTIFACT):
    """Mismatches between a sizing result and the pinned artifact (empty list = identical bytes and layout)."""
    bad = []
    if result["url"] != known["url"]:
        bad.append(f"url {result['url']} is not the commit-pinned {known['url']}")
    if result["zip_bytes"] != known["zip_bytes"]:
        bad.append(f"zip bytes {result['zip_bytes']} != {known['zip_bytes']}")
    m = {x["name"]: x for x in result["members"]}.get(known["member"])
    if m is None or len(result["members"]) != 1:
        bad.append(f"members {[x['name'] for x in result['members']]} are not exactly [{known['member']!r}]")
    else:
        if m["uncompressed"] != known["member_uncompressed_bytes"]:
            bad.append(f"uncompressed {m['uncompressed']} != {known['member_uncompressed_bytes']}")
        if m["compressed"] != known["member_compressed_bytes"]:
            bad.append(f"compressed {m['compressed']} != {known['member_compressed_bytes']}")
    return bad


def sizing(url, head, fetch, tail_bytes=1 << 17):
    """`head(url) -> {'size': int, headers...}`; `fetch(url, start, end) -> bytes` (inclusive range)."""
    h = head(url)
    size = h["size"]
    tail = fetch(url, max(0, size - tail_bytes), size - 1)
    cd_off, cd_size, entries = parse_tail(tail)
    if cd_size > MAX_RANGE or cd_size <= 0:
        raise ValueError(f"central directory size {cd_size} is outside the allowed range read (1 to {MAX_RANGE} bytes)")
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


def _fetch(url, start, end, opener=urllib.request.urlopen):
    """One BOUNDED range read. The reply must be 206 with a Content-Range that names exactly this range, and at most length+1 bytes are ever read: a server that ignores Range and
    answers 200 with the whole file (12 GB here) is refused before the body is read."""
    length = end - start + 1
    if length <= 0 or length > MAX_RANGE:
        raise ValueError(f"refusing a range read of {length} bytes (allowed 1 to {MAX_RANGE})")
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with opener(req, timeout=120) as r:
        status = getattr(r, "status", None) or r.getcode()
        if status != 206:
            raise ValueError(f"the server answered {status}, not 206: it did not honour the range, and the body was NOT read")
        m = re.match(r"^bytes (\d+)-(\d+)/(\d+|\*)$", r.headers.get("Content-Range", "") if hasattr(r, "headers") else r.getheader("Content-Range", ""))
        if not m or (int(m.group(1)), int(m.group(2))) != (start, end):
            raise ValueError(f"Content-Range {r.headers.get('Content-Range') if hasattr(r, 'headers') else None!r} does not name the requested bytes {start}-{end}")
        cl = r.headers.get("Content-Length") if hasattr(r, "headers") else None
        if cl is not None and int(cl) != length:
            raise ValueError(f"Content-Length {cl} != requested length {length}")
        data = r.read(length + 1)
    if len(data) != length:
        raise ValueError(f"read {len(data)} bytes for a {length}-byte range")
    return data


def main(argv=None):
    url = (argv or sys.argv[1:] or [KNOWN_ARTIFACT["url"]])[0]
    s = sizing(url, _head, _fetch)
    s["disk_budget"] = disk_budget(s["zip_bytes"], s["uncompressed_total_bytes"])
    s["matches_pinned_artifact"] = check_known(s)  # [] means the pinned commit URL, bytes and layout are identical
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
