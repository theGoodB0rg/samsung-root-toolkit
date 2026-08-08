"""Firmware package file handling: part discovery + tar.md5 verification."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..core.errors import ErrorCode, SrtkError
from ..core.versioning import SamsungVersion, parse_version

PARTS = ("BL", "AP", "CP", "CSC", "HOME_CSC")

_PART_RE = re.compile(r"^(BL|AP|CP|CSC|HOME_CSC)_([A-Za-z0-9_]+)\.tar")


def find_firmware_parts(directory: Path) -> dict[str, Path]:
    """Return {slot: path} for BL/AP/CP/CSC/HOME_CSC found under directory."""
    found: dict[str, Path] = {}
    for path in directory.rglob("*.tar*"):
        name = path.name
        match = _PART_RE.match(name)
        if not match:
            continue
        slot = match.group(1)
        if slot not in found:
            found[slot] = path
    return found


def verify_tar_md5(path: Path) -> bool:
    """Verify the MD5 trailer of a ``.tar.md5`` file.

    Returns True when the trailer is valid or absent (plain ``.tar``). Raises
    HASH_MISMATCH when the embedded trailer does not match. The common layout
    is ``<tar bytes>\\n<32-hex>`` with no trailing newline; a bare
    ``<tar bytes><32-hex>`` is also accepted.
    """
    data = path.read_bytes()
    if not re.search(rb"[0-9a-fA-F]{32}$", data):
        # Not md5-tagged (plain .tar) -> nothing to verify.
        return True
    expected = data[-32:].decode().lower()
    body = data[:-32]
    if body.endswith(b"\n"):  # separator between tar data and the hash
        body = body[:-1]
    actual = hashlib.md5(body).hexdigest()
    if actual != expected:
        raise SrtkError(
            ErrorCode.HASH_MISMATCH,
            context=str(path),
            details=f"md5 trailer {expected} != computed {actual}",
        )
    return True


def firmware_version_from_ap(ap: Path) -> SamsungVersion | None:
    """Parse the SW version out of an AP filename, e.g. AP_A325FXXU3BVE1_..."""
    match = re.search(r"AP_([A-Z0-9a-z]{12,16})_", ap.name)
    if not match:
        return None
    return parse_version(match.group(1))


def extract_binary_from_ap(ap: Path) -> str | None:
    ver = firmware_version_from_ap(ap)
    return ver.binary if ver else None
