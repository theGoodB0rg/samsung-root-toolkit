import hashlib
from pathlib import Path

import pytest

from srtk.core.errors import ErrorCode, SrtkError
from srtk.tools.bootimg import inspect_boot_image
from srtk.tools.fwfile import (
    extract_binary_from_ap,
    find_firmware_parts,
    verify_tar_md5,
)


def _android_header(ramdisk: bool = False) -> bytes:
    hdr = bytearray(b"ANDROID!" + b"\x00" * 40)
    if ramdisk:
        hdr[16:20] = (4096).to_bytes(4, "little")
    hdr[36:40] = (2048).to_bytes(4, "little")
    return bytes(hdr)


def test_bootimg_ramdisk_presence(tmp_path):
    f = tmp_path / "boot.img"
    f.write_bytes(_android_header(ramdisk=False))
    info = inspect_boot_image(str(f))
    assert info.magic == "ANDROID!"
    assert info.ramdisk_present is False
    f.write_bytes(_android_header(ramdisk=True))
    info2 = inspect_boot_image(str(f))
    assert info2.ramdisk_present is True


def test_bootimg_garbage():
    info = inspect_boot_image(__file__)
    assert info.magic != "ANDROID!"


def test_find_firmware_parts(tmp_path):
    (tmp_path / "BL_A325FXXSCDYB2.tar").write_bytes(b"x")
    (tmp_path / "AP_A325FXXSCDYB2_CL1.tar").write_bytes(b"x")
    (tmp_path / "CP_A325FXXSCDYB2.tar").write_bytes(b"x")
    (tmp_path / "CSC_EUX_A325FXXSCDYB2.tar").write_bytes(b"x")
    (tmp_path / "HOME_CSC_EUX_A325FXXSCDYB2.tar").write_bytes(b"x")
    parts = find_firmware_parts(tmp_path)
    assert set(parts) == {"BL", "AP", "CP", "CSC", "HOME_CSC"}


def test_verify_tar_md5_ok_and_absent(tmp_path):
    plain = tmp_path / "AP_a.tar"
    plain.write_bytes(b"data")
    assert verify_tar_md5(plain) is True
    body = b"payload"
    trailer = hashlib.md5(body).hexdigest().encode()
    tagged = tmp_path / "AP_b.tar.md5"
    tagged.write_bytes(body + b"\n" + trailer)
    assert verify_tar_md5(tagged) is True


def test_verify_tar_md5_mismatch(tmp_path):
    tagged = tmp_path / "AP_c.tar.md5"
    tagged.write_bytes(b"payload\n" + b"0" * 32)
    with pytest.raises(SrtkError) as e:
        verify_tar_md5(tagged)
    assert e.value.code is ErrorCode.HASH_MISMATCH


def test_extract_ap_binary():
    assert extract_binary_from_ap(Path("AP_A325FXXSCDYB2_CL123.tar")) == "C"
    assert extract_binary_from_ap(Path("AP_A325FXXU3BVE1_CL1.tar")) == "3"
    assert extract_binary_from_ap(Path("BL_A325FXXSCDYB2.tar")) is None
