"""Frame helpers ported from FrameHash.swift. Frames are raw 4-bytes-per-pixel buffers
(X11 gives B,G,R,pad), rows from the top, no row padding."""

FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
MAX_REGION = 100_000.0


def parse_region(raw):
    parts = raw.split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (float(p) for p in parts)
    except ValueError:
        return None
    for v in (x, y, w, h):
        if v != v or abs(v) == float("inf") or v > MAX_REGION:
            return None
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return None
    return x, y, w, h


def fnv1a64_hex(buf):
    h = FNV_OFFSET
    for b in buf:  # fine for a spike; a C-speed hash is a later optimisation
        h = ((h ^ b) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def fast_hash_hex(buf):
    """Content hash used for stale-frame detection. Equal buffers give equal hashes; it is
    not byte-compatible with envd's FNV-1a (the header only needs equality between frames)."""
    import hashlib
    return hashlib.blake2b(buf, digest_size=8).hexdigest()


def is_black(raw, width, height):
    total = width * height
    if width <= 0 or height <= 0 or len(raw) < total * 4:
        return True
    step = max(1, total // 10_000)
    sampled = dark = 0
    for p in range(0, total, step):
        i = p * 4
        sampled += 1
        if raw[i] < 8 and raw[i + 1] < 8 and raw[i + 2] < 8:
            dark += 1
    return dark >= sampled * 0.995
