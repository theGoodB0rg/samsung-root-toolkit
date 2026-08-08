"""Phase 04 — firmware acquisition.

Downloads the correct firmware for the detected model/CSC from Samsung's FUS
servers via samloader-rs, verifies every part, computes a SHA-256 manifest,
checks binary compatibility against the device bootloader, and (advisory)
inspects the AP's boot image to note whether it carries a ramdisk.
"""
from __future__ import annotations

import json
import tarfile
import zipfile
from pathlib import Path

from ..core.errors import ErrorCode, SrtkError
from ..core.hashing import sha256_file, write_manifest
from ..core.versioning import require_compatible
from ..tools.bootimg import inspect_boot_image
from ..tools.fwfile import (
    extract_binary_from_ap,
    find_firmware_parts,
    verify_tar_md5,
)
from ..tools.samloader import Samloader
from .base import Phase


class P04Firmware(Phase):
    name = "firmware"
    requires = ("preflight", "device_info")
    produces = ("firmware_manifest", "firmware")

    def execute(self) -> list[str]:
        log = self.log
        report = self.ctx.state.get("device_report") or {}
        model = report.get("model") or self.ctx.config.model
        csc = report.get("csc")
        device_binary = report.get("binary")

        if not csc:
            raise SrtkError(
                ErrorCode.FW_DOWNLOAD_FAILED,
                context=self.name,
                details="could not determine device CSC (ro.csc.sales_code empty)",
            )

        fw_dir = self.ctx.artifacts_dir / "03-firmware"
        fw_dir.mkdir(parents=True, exist_ok=True)

        log.info(f"model={model} csc={csc} device_binary={device_binary}")
        parts = find_firmware_parts(fw_dir)

        if not parts:
            if self.ctx.dry_run:
                self.dry_abort(f"would download firmware for {model}/{csc}")
            sl = Samloader(self.ctx.runner, self.ctx.config.samloader_path)
            check = sl.check_update(model, csc, timeout=90)
            log.raw(f"check-update rc={check.returncode}\n{check.stdout}")
            result = sl.download(model, csc, fw_dir)
            if not result.ok:
                raise SrtkError(
                    ErrorCode.FW_DOWNLOAD_FAILED,
                    context=self.name,
                    details=result.output[-2000:],
                )
            self._explode_zip(fw_dir)
            parts = find_firmware_parts(fw_dir)

        missing = [slot for slot in ("BL", "AP", "CP", "CSC") if slot not in parts]
        if missing:
            raise SrtkError(
                ErrorCode.FW_DOWNLOAD_FAILED,
                context=self.name,
                details=f"firmware incomplete; missing {', '.join(missing)}",
            )

        # ---- verify + hash ----------------------------------------------------
        for slot, path in parts.items():
            verify_tar_md5(path)
        manifest = {str(p.relative_to(fw_dir)): sha256_file(p) for p in parts.values()}
        manifest_path = fw_dir / "firmware-manifest.json"
        write_manifest(manifest_path, manifest)
        self.dump_manifest = manifest_path

        # ---- binary compatibility ----------------------------------------------
        ap_binary = extract_binary_from_ap(parts["AP"])
        log.info(f"AP version binary token: {ap_binary}")
        require_compatible(device_binary, ap_binary)

        # ---- boot image advisory -----------------------------------------------
        boot_info = None
        try:
            boot_info = self._inspect_ap_boot(parts["AP"])
        except Exception as exc:  # noqa: BLE001 - advisory only
            log.warn(f"boot image inspection skipped: {exc}")

        summary = {
            "model": model,
            "csc": csc,
            "device_binary": device_binary,
            "ap_binary": ap_binary,
            "parts": {slot: str(p.name) for slot, p in parts.items()},
            "manifest": str(manifest_path),
            "ap_ramdisk_present": boot_info.ramdisk_present if boot_info else None,
            "magisk_mode_hint": (
                "recovery" if boot_info and boot_info.ramdisk_present is False else "unknown"
            ),
        }
        self.ctx.state.set("firmware", summary)
        out = self.ctx.artifact("firmware-manifest.json")
        out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return [str(manifest_path), str(out)]

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _explode_zip(fw_dir: Path) -> None:
        for zip_path in fw_dir.glob("*.zip"):
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    _extractall(zf, fw_dir)
            except zipfile.BadZipFile:
                continue  # not a zip; tar files may already be present

    @staticmethod
    def _inspect_ap_boot(ap: Path) -> object:
        """Extract boot.img (or boot.img.lz4) from the AP tar and inspect it."""
        ap_dir = ap.parent / "_ap"
        ap_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(ap, mode="r") as tf:
            members = [
                m for m in tf.getmembers()
                if m.isfile() and m.name.startswith("boot")
            ]
            if not members:
                return None
            _extract(tf, members[0], ap_dir)
        target = ap_dir / members[0].name
        if target.name.endswith(".lz4"):
            lz4_target = target.with_suffix("")
            try:
                import lz4.block  # type: ignore

                lz4_target.write_bytes(
                    lz4.block.decompress(target.read_bytes(), uncompressed_size=64 * 1024 * 1024)
                )
                target = lz4_target
            except ImportError:
                pass
        return inspect_boot_image(str(target))


def _extract(tf: tarfile.TarFile, member, dest: Path) -> None:
    try:
        tf.extract(member, path=dest, filter="data")
    except TypeError:  # Python < 3.12 has no filter kwarg
        tf.extract(member, path=dest)


def _extractall(zf, dest: Path) -> None:
    zf.extractall(dest)
