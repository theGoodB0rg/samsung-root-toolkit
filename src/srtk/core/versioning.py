"""Samsung firmware / bootloader version handling.

Samsung software versions look like ``A325FXXU3BVE1`` and break down as::

    A325F | XXU | 3 | BVE1
    model | csc | binary | build

The **binary token** (one char after the 3-letter CSC code) is the bootloader
version. Samsung encodes 0-9 numerically and then continues A, B, C, ... so
ordering needs a mixed numeric/alpha ordering. Downgrade protection means a
device only accepts firmware whose binary >= its current binary.

This module parses both the full version string and the ``ro.bootloader`` prop
(which may be the full string) and answers the one question that matters before
flashing: *is this firmware safe to flash on this device?*
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ErrorCode, SrtkError

# A325F XXU 3 BVE1 | A325F XXS A DXG1
_VERSION_RE = re.compile(
    r"^(?P<model>[A-Za-z0-9]{5})(?P<csc>[A-Za-z]{3})(?P<binary>[0-9A-Za-z])(?P<build>[A-Za-z0-9]{4,5})$"
)

_MODEL_RE = re.compile(r"^(?P<model>SM-[A-Za-z0-9]{3,5})$", re.I)

_BINARY_ORDER = {c: i for i, c in enumerate("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")}


def _binary_value(token: str) -> int:
    token = token.upper()
    if token not in _BINARY_ORDER:
        raise ValueError(f"unparseable binary token: {token}")
    return _BINARY_ORDER[token]


@dataclass(frozen=True)
class SamsungVersion:
    model: str
    csc: str
    binary: str
    build: str
    raw: str

    @property
    def binary_int(self) -> int:
        return _binary_value(self.binary)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.raw


def parse_version(text: str) -> SamsungVersion | None:
    """Parse a Samsung SW version string, tolerant of whitespace / case."""
    match = _VERSION_RE.match(text.strip().upper())
    if not match:
        return None
    return SamsungVersion(
        model=match.group("model"),
        csc=match.group("csc"),
        binary=match.group("binary"),
        build=match.group("build"),
        raw=text.strip(),
    )


def extract_binary(text: str) -> str | None:
    """Best-effort binary token extraction from an arbitrary prop string."""
    parsed = parse_version(text)
    if parsed:
        return parsed.binary
    # Fallback: trailing <binary><build> token anchored to a 3-letter CSC code,
    # e.g. "XXU3BVE1" -> "3". Refuses strings that are not Samsung-like.
    match = re.search(r"[A-Z]{3}([0-9A-Z])([A-Z0-9]{4,5})$", text.strip().upper())
    return match.group(1) if match else None


def binary_is_compatible(device_binary: str | None, fw_binary: str | None) -> tuple[bool, str]:
    """Return (ok, reason). Firmware binary must be >= device binary."""
    if device_binary is None:
        return False, "device bootloader version unknown; refusing to guess"
    if fw_binary is None:
        return False, "firmware binary unknown; refusing to guess"
    try:
        dev = _binary_value(device_binary)
        fw = _binary_value(fw_binary)
    except ValueError as exc:  # pragma: no cover - defensive
        return False, str(exc)
    if fw < dev:
        return (
            False,
            f"firmware binary {fw_binary} < device binary {device_binary}; downgrade blocked",
        )
    if fw > dev:
        return True, f"firmware binary {fw_binary} > device binary {device_binary} (upgrade ok)"
    return True, f"binary match ({device_binary})"


def require_compatible(device_binary: str | None, fw_binary: str | None) -> None:
    ok, reason = binary_is_compatible(device_binary, fw_binary)
    if not ok:
        raise SrtkError(ErrorCode.BINARY_MISMATCH, context=reason)


def normalize_model(text: str) -> str:
    """Normalize a device model string (e.g. 'a32' -> 'SM-A325F' must be explicit)."""
    match = _MODEL_RE.match(text.strip().upper())
    if match:
        return match.group("model")
    return text.strip().upper()
