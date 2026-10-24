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
