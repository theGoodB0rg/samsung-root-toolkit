"""Android boot image inspection (informational).

Reads the ``boot.img`` header to report whether the boot partition carries a
ramdisk. On Samsung system-as-root devices (including SM-A325F) the boot
partition typically has **no** ramdisk, which is why Magisk patches the
*recovery* partition instead and the device must boot to recovery once after
flashing.

LZ4-wrapped images (``boot.img.lz4``) are decompressed only if the optional
``lz4`` package is importable; otherwise the result is ``unknown`` and the
toolkit defers the decision to Magisk, which always sets the correct mode.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


@dataclass
class BootImageInfo:
    file: str
    magic: str = "unknown"
    header_version: int | None = None
    ramdisk_present: bool | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "magic": self.magic,
            "header_version": self.header_version,
            "ramdisk_present": self.ramdisk_present,
            "notes": self.notes,
        }


def inspect_boot_image(path: str, max_bytes: int = 4096) -> BootImageInfo:
    """Return boot image facts. Never raises on malformed input."""
    info = BootImageInfo(file=path)
    try:
        data = _decompress(path, max_bytes)
    except Exception as exc:  # noqa: BLE001 - informational only
        info.magic = "unreadable"
        info.notes.append(f"could not read image: {exc}")
        return info

    if len(data) < 8:
        info.notes.append("too small to contain a boot header")
        return info

    info.magic = data[:8].decode("latin1", errors="replace")
    if info.magic != "ANDROID!":
        info.notes.append("not an Android boot image (check for lz4/ext4 overlay)")
        return info

    if len(data) < 48:
        info.notes.append("truncated boot header")
        return info

    page_size = struct.unpack_from("<I", data, 36)[0] or 2048
    info.header_version = struct.unpack_from("<I", data, 44)[0]

    if info.header_version <= 2:
        ramdisk_size = struct.unpack_from("<I", data, 16)[0]
        info.ramdisk_present = ramdisk_size > 0
        info.notes.append(
            f"page_size={page_size} ramdisk_size={ramdisk_size}"
        )
    else:
        # v3/v4: no ramdisk in boot (system-as-root / GKI)
        info.ramdisk_present = False
        info.notes.append("v3/v4 header: ramdisk not carried in boot")
    return info


def _decompress(path: str, limit: int) -> bytes:
    with open(path, "rb") as fh:
        head = fh.read(4)
        fh.seek(0)
        if head == b"\x02\x21\x4c\x18":  # lz4 legacy frame magic
            try:
                import lz4.block  # type: ignore

                raw = fh.read()
                return lz4.block.decompress(raw, uncompressed_size=64 * 1024 * 1024)[:limit]
            except ImportError:
                raise RuntimeError("lz4-compressed image but 'lz4' package not installed")
        return fh.read(limit)
