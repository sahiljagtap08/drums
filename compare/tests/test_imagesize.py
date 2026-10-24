"""Zip central-directory sizing against local zips (zip and zip64); no network."""
import os
import zipfile

import pytest

from drums_compare import imagesize as im


def local(path):
    size = os.path.getsize(path)
    head = lambda url: {"size": size, "etag": '"abc"'}

    def fetch(url, start, end):
        with open(path, "rb") as f:
            f.seek(start)
            return f.read(end - start + 1)
    return head, fetch


@pytest.mark.parametrize("zip64", [False, True])
def test_sizes_are_read_from_the_central_directory_without_the_data(tmp_path, zip64):
    p = tmp_path / "a.zip"
    zi = zipfile.ZipInfo("Ubuntu.qcow2")
    zi.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        with z.open(zi, "w", force_zip64=zip64) as f:
            f.write(b"\0" * 300_000)
        z.writestr("README", b"hello")
    head, fetch = local(str(p))
    s = im.sizing("u", head, fetch, tail_bytes=1 << 12)
    assert [m["name"] for m in s["members"]] == ["Ubuntu.qcow2", "README"]
    assert s["uncompressed_total_bytes"] == 300_005 and s["zip_bytes"] == os.path.getsize(p) and s["checksum_headers"] == {"etag": '"abc"'}
    assert s["members"][0]["compressed"] < s["members"][0]["uncompressed"]


def test_a_truncated_or_non_zip_tail_is_refused(tmp_path):
    with pytest.raises(ValueError):
        im.parse_tail(b"not a zip" * 100)


def test_the_disk_budget_separates_measured_from_allowances_and_rounds_up_to_ten():
    b = im.disk_budget(12.3e9, 24.0e9)
    assert b["measured"] == {"zip_gb": 12.3, "extracted_gb": 24.0}
    assert set(b["allowances"]) == set(im.DEFAULT_ALLOWANCES) and b["ebs_gb_to_request"] % 10 == 0 and b["ebs_gb_to_request"] >= b["total_gb"]
    assert abs(b["subtotal_gb"] - (12.3 + 24.0 + 20 + 2 + 32 + 5)) < 1e-9
    assert im.disk_budget(12.3e9, 24.0e9, keep_zip_until_verified=False)["measured"]["zip_gb"] == 0.0
    assert im.disk_budget(1e9, 1e9, {"container_writable_gb": 0})["subtotal_gb"] < b["subtotal_gb"]


# ---- bounded range reads ----
class Resp:
    """A fake urlopen response that counts how many body bytes were actually read."""

    def __init__(self, status, headers, body):
        self.status, self.headers, self._body, self.read_bytes = status, headers, body, 0

    def read(self, n=-1):
        chunk = self._body if n < 0 else self._body[:n]
        self._body = self._body[len(chunk):]
        self.read_bytes += len(chunk)
        return chunk

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(resp):
    return lambda req, timeout=0: resp


def test_a_206_with_the_matching_content_range_is_accepted():
    r = Resp(206, {"Content-Range": "bytes 100-199/1000", "Content-Length": "100"}, b"x" * 100)
    assert im._fetch("https://example.invalid/z", 100, 199, opener=opener_for(r)) == b"x" * 100


def test_a_server_that_ignores_range_is_refused_without_reading_the_body():
    r = Resp(200, {"Content-Length": str(12_000_000_000)}, b"y" * 5000)
    with pytest.raises(ValueError, match="not 206"):
        im._fetch("https://example.invalid/z", 0, 99, opener=opener_for(r))
    assert r.read_bytes == 0


@pytest.mark.parametrize("hdr", [{"Content-Range": "bytes 0-98/1000"}, {"Content-Range": "bytes 1-100/1000"}, {"Content-Range": ""}, {}, {"Content-Range": "bytes 0-99/1000", "Content-Length": "5000"}])
def test_a_wrong_or_missing_content_range_or_length_is_refused(hdr):
    r = Resp(206, hdr, b"z" * 100)
    with pytest.raises(ValueError):
        im._fetch("https://example.invalid/z", 0, 99, opener=opener_for(r))


def test_a_body_longer_or_shorter_than_the_range_is_refused_and_never_read_past_one_extra_byte():
    long = Resp(206, {"Content-Range": "bytes 0-99/1000"}, b"a" * 5000)
    with pytest.raises(ValueError, match="read 101 bytes"):
        im._fetch("https://example.invalid/z", 0, 99, opener=opener_for(long))
    assert long.read_bytes == 101
    with pytest.raises(ValueError, match="read 50 bytes"):
        im._fetch("https://example.invalid/z", 0, 99, opener=opener_for(Resp(206, {"Content-Range": "bytes 0-99/1000"}, b"a" * 50)))


def test_an_oversize_range_or_central_directory_is_refused_before_any_request():
    called = []
    with pytest.raises(ValueError, match="refusing a range read"):
        im._fetch("https://example.invalid/z", 0, im.MAX_RANGE, opener=lambda *a, **k: called.append(1))
    assert called == []
    with pytest.raises(ValueError, match="central directory size"):
        import struct
        tail = struct.pack("<IHHHHIIH", im.EOCD, 0, 0, 1, 1, im.MAX_RANGE + 1, 0, 0)
        im.sizing("u", lambda u: {"size": len(tail)}, lambda u, a, b: tail, tail_bytes=1000)


def test_the_pinned_artifact_is_commit_pinned_and_the_real_numbers_give_a_120_gb_disk():
    k = im.KNOWN_ARTIFACT
    assert "/resolve/a5d9c3eaae98eebf6e3a0beb84e7e47cf72ae133/" in k["url"] and "/main/" not in k["url"] and len(k["commit"]) == 40 and len(k["zip_sha256"]) == 64
    assert k["zip_bytes"] == 12273896463 and k["member_uncompressed_bytes"] == 24460197888 and k["central_directory"] == {"offset": 12273896263, "size": 102, "entries": 1}
    assert k["central_directory"]["offset"] + k["central_directory"]["size"] < k["zip_bytes"]  # the directory sits before the end record
    b = im.disk_budget(k["zip_bytes"], k["member_uncompressed_bytes"])
    assert b["ebs_gb_to_request"] == 120 and abs(b["subtotal_gb"] - 95.734094) < 1e-5 and abs(b["total_gb"] - 110.09) < 0.01  # 110.09 rounds UP to 120, not the illustrative 110


def test_check_known_reports_every_difference_from_the_pinned_artifact():
    k = im.KNOWN_ARTIFACT
    good = {"url": k["url"], "zip_bytes": k["zip_bytes"], "members": [{"name": "Ubuntu.qcow2", "compressed": k["member_compressed_bytes"], "uncompressed": k["member_uncompressed_bytes"]}]}
    assert im.check_known(good) == []
    bad = dict(good, url=k["url"].replace(k["commit"], "main"), zip_bytes=1, members=[{"name": "other", "compressed": 1, "uncompressed": 2}])
    assert len(im.check_known(bad)) == 3
