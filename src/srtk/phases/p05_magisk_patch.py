"""Phase 05 — Magisk patch.

Pushes the stock AP tar to the device, installs the Magisk app, and drives the
on-device patch (operator via scrcpy; sim auto-confirms). Pulls the patched
tar over adb (never MTP — known to corrupt large files), validates it, and
pins its SHA-256 for the flash phase.
"""
from __future__ import annotations

import json
import re
import tarfile

from ..core.errors import ErrorCode, SrtkError
from ..core.hashing import sha256_file
from ..core.transport import DeviceState
from .base import Phase

_PATCHED_RE = re.compile(r"magisk_patched_([A-Za-z0-9_-]+)\.tar")


class P05MagiskPatch(Phase):
    name = "magisk_patch"
    requires = ("preflight", "firmware")
    produces = ("magisk_patch",)

    def execute(self) -> list[str]:
        log = self.log
        fw = self.ctx.state.get("firmware") or {}
        ap_name = (fw.get("parts") or {}).get("AP")
        if not ap_name:
            raise SrtkError(
                ErrorCode.PHASE_PREREQ_FAIL,
                context=self.name,
                details="firmware phase did not record an AP part",
            )
        ap = (self.ctx.artifacts_dir / "03-firmware" / ap_name)
        if not ap.exists():
            raise SrtkError(ErrorCode.FW_DOWNLOAD_FAILED, context=self.name,
                            details=f"AP not on disk: {ap}")

        magisk_apk = self.ctx.config.modules_dir / "Magisk.apk"
        if not magisk_apk.exists() and not self.ctx.sim:
            raise SrtkError(ErrorCode.TOOL_NOT_FOUND, context="Magisk.apk",
                            details=f"expected at {magisk_apk}")

        adb = self.ctx.adb
        state, detail = self.ctx.detector.detect()
        if state is not DeviceState.DEVICE:
            raise SrtkError(ErrorCode.DEVICE_OFFLINE, context=self.name,
                            details=f"need a booted device on adb; got {state.value}")

        patch_dir = self.ctx.artifacts_dir / "04-magisk"
        patch_dir.mkdir(parents=True, exist_ok=True)

        # ---- push AP + install Magisk app ------------------------------------
        if not self.ctx.sim:
            log.info(f"pushing AP ({ap.stat().st_size / 1e9:.2f} GiB) to device...")
        push = adb.push(ap, "/sdcard/Download/srtk_ap.tar", timeout=1800)
        if not push.ok:
            raise SrtkError(ErrorCode.PATCH_FAILED, context=self.name,
                            details=f"adb push failed: {push.stderr.strip()[:500]}")
        install = adb.install(magisk_apk, timeout=300)
        if not install.ok:
            raise SrtkError(ErrorCode.PATCH_FAILED, context=self.name,
                            details=f"adb install Magisk failed: {install.stderr.strip()[:500]}")

        # ---- operator patches in the Magisk app --------------------------------
        self.ctx.ui.instruct(
            "Patch the AP with Magisk",
            "In the scrcpy window:\n"
            "  1. Open the Magisk app.\n"
            "  2. Tap Install (top card) > 'Select and Patch a File'.\n"
            "  3. Choose 'srtk_ap.tar' in Downloads.\n"
            "  4. Wait for 'All done!' and note the output name magisk_patched_<rand>.tar.\n"
            "This takes several minutes.",
            evidence="patch-00-start",
        )

        # ---- wait for + pull patched tar ----------------------------------------
        remote = self._wait_for_patched(adb)
        patched = patch_dir / "magisk_patched.tar"
        pull = adb.pull(f"/sdcard/Download/{remote}", patched, timeout=1800)
        if not pull.ok or not patched.exists():
            raise SrtkError(ErrorCode.PATCH_FAILED, context=self.name,
                            details="adb pull of patched tar failed")

        # ---- validate + hash ------------------------------------------------------
        members = self._tar_members(patched)
        bootish = [m for m in members if m.startswith(("boot", "recovery"))]
        if not bootish:
            raise SrtkError(ErrorCode.PATCH_FAILED, context=self.name,
                            details=f"patched tar has no boot/recovery member: {members[:10]}")

        sha = sha256_file(patched)
        report = {
            "ap": str(ap),
            "ap_sha256": sha256_file(ap),
            "patched_tar": str(patched),
            "patched_sha256": sha,
            "patched_size": patched.stat().st_size,
            "members": members,
            "bootish_members": bootish,
        }
        manifest = patch_dir / "patch-manifest.json"
        manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self.ctx.state.set("magisk_patch", report)
        log.info(f"patched tar ready: {patched.name} sha256={sha[:12]}… members={members[:6]}")

        # best-effort cleanup of the 2+ GiB AP on device
        adb.shell("rm", "-f", "/sdcard/Download/srtk_ap.tar", timeout=30)
        return [str(patched), str(manifest)]

    # -- helpers -------------------------------------------------------------
    def _wait_for_patched(self, adb, timeout: float = 900, poll: float = 5) -> str:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = adb.shell("ls", "/sdcard/Download/", timeout=20)
            if result.ok:
                for name in result.stdout.split():
                    if _PATCHED_RE.match(name):
                        return name
            time.sleep(poll)
        raise SrtkError(
            ErrorCode.PATCH_FAILED,
            context=self.name,
            details=f"no magisk_patched_*.tar appeared after {timeout:.0f}s",
        )

    @staticmethod
    def _tar_members(path) -> list[str]:
        with tarfile.open(path, mode="r") as tf:
            return [m.name for m in tf.getmembers() if m.isfile()]
