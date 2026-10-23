"""Minimal PNG writer (stdlib only)."""

import struct
import zlib


def _chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_rgb(rgb, width, height, level=1):
    """rgb: bytes, 3 bytes per pixel, rows from the top."""
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), level)) + _chunk(b"IEND", b""))


def encode_bgrx(bgrx, width, height, level=1):
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgrx[2::4]
    rgb[1::3] = bgrx[1::4]
    rgb[2::3] = bgrx[0::4]
    return encode_rgb(bytes(rgb), width, height, level)
