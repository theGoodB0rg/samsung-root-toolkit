"""Phase 06 — flash.

Flashes BL + patched AP + CP + CSC (full data wipe — never HOME_CSC on the
initial install) through Odin3, driven by the operator over the remote session
while the toolkit polls for completion, then arms Magisk (recovery-ramdisk
devices need one recovery boot) and verifies the device came back healthy.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..core.errors import ErrorCode, SrtkError
from ..core.transport import DeviceState
from .base import Phase


class P06Flash(Phase):
    name = "flash"
    requires = ("preflight", "unlock", "firmware", "magisk_patch")
    produces = ("flash_report", "flash")

    def execute(self) -> list[str]:
        log = self.log
        adb = self.ctx.adb
        detector = self.ctx.detector
        config = self.ctx.config

        fw = self.ctx.state.get("firmware") or {}
        patch = self.ctx.state.get("magisk_patch") or {}
        parts = (fw.get("parts") or {})
        ap_patched = patch.get("patched_tar")
        if not ap_patched:
            raise SrtkError(ErrorCode.PHASE_PREREQ_FAIL, context=self.name,
                            details="magisk_patch did not record patched_tar")
        missing = [s for s in ("BL", "AP", "CP", "CSC") if s not in parts]
        if missing:
            raise SrtkError(ErrorCode.PHASE_PREREQ_FAIL, context=self.name,
                            details=f"firmware parts missing: {missing}")

        fw_dir = self.ctx.artifacts_dir / "03-firmware"
        slots = {s: fw_dir / parts[s] for s in ("BL", "CP", "CSC")}
        slots["AP"] = Path(ap_patched)  # patched tar replaces stock AP

        # ---- gates ----------------------------------------------------------
        unlocked = (self.ctx.state.get("unlock_report") or {}).get("unlocked")
        if not unlocked:
            raise SrtkError(ErrorCode.PHASE_PREREQ_FAIL, context=self.name,
                            details="bootloader must be unlocked before flashing patched AP")

        # ---- enter download mode --------------------------------------------
        state, detail = detector.detect()
        if state is DeviceState.DOWNLOAD:
            log.info(f"already in Download Mode: {detail}")
        elif state in (DeviceState.DEVICE, DeviceState.RECOVERY):
            try:
                detail = detector.reboot_to_download(timeout_s=config.timeouts["download_mode"])
                log.info(f"Download Mode via adb: {detail}")
            except SrtkError as exc:
                if exc.code is not ErrorCode.ADB_UNAVAILABLE:
                    raise
                self.ctx.state.note("flash: physical download-mode entry used")
                self.ctx.ui.instruct(
                    "Enter Download Mode (physical)",
                    "With the phone OFF, hold Volume Up + Volume Down, plug in the USB "
                    "cable, and accept the warning screen.",
                )
                detector.wait_for_download(timeout_s=config.timeouts["download_mode"])
        else:
            raise SrtkError(ErrorCode.ODIN_NOT_DETECTED, context=self.name,
                            details=f"device state {state.value} cannot reach Download Mode")

        samsung_ports, _ = detector.com_ports(refresh=True)
        log.info(f"Samsung COM ports visible: {','.join(samsung_ports) or 'none'}")

        # ---- experimental CLI flasher? ----------------------------------------
        if getattr(config, "use_cli_flasher", False):
            raise SrtkError(
                ErrorCode.ODIN_NOT_DETECTED,
                context=self.name,
                details="CLI flasher is experimental and disabled; use Odin3 GUI path",
            )

        # ---- operator drives Odin3 ---------------------------------------------
        self.ctx.ui.instruct(
            "Flash with Odin3",
            "In the Odin3 window on the remote session:\n"
            "  1. Confirm the phone is detected (blue 'Added!!' in the log, COM in the box).\n"
            f"  2. BL = {slots['BL'].name}\n"
            f"  3. AP = {slots['AP'].name}   <-- PATCHED tar\n"
            f"  4. CP = {slots['CP'].name}\n"
            f"  5. CSC = {slots['CSC'].name}   (NOT HOME_CSC — full wipe is intentional)\n"
            "  6. Ensure only 'Auto Reboot' and 'F. Reset Time' are checked.\n"
            "  7. Click Start. Wait for a green 'PASS!' and automatic reboot.",
            evidence="flash-00-odin-loaded",
        )

        # ---- wait for completion -----------------------------------------------
        self._wait_flash_done(detector, timeout_s=config.timeouts["flash"])

        # ---- device back on adb -------------------------------------------------
        adb.wait_for_device(timeout_s=config.timeouts["boot"])
        time.sleep(10)  # let the framework finish booting
        adb.shell("input", "keyevent", "82", timeout=20)

        # ---- verify bootloader version survived ----------------------------------
        bootloader = adb.getprop("ro.bootloader")
        expected = (self.ctx.state.get("device_report") or {}).get("bootloader")
        if expected and bootloader and expected != bootloader:
            log.warn(f"bootloader changed: {expected} -> {bootloader}")

        # ---- arm Magisk (recovery-ramdisk) ----------------------------------------
        rooted = self._magisk_active()
        if not rooted:
            log.info("Magisk not active on first boot; rebooting to recovery to arm it")
            self.ctx.ui.instruct(
                "Recovery boot to arm Magisk",
                "Booting to recovery once so Magisk can activate (recovery-ramdisk device).",
                evidence="flash-01-arming",
            )
            try:
                detector.reboot_to_recovery(timeout_s=120)
            except SrtkError:
                self.ctx.ui.instruct(
                    "Boot to recovery (physical)",
                    "Hold Power + Volume Up, release on the splash, then RELEASE quickly "
                    "(short press) to boot Magisk instead of recovery.",
                )
                adb.wait_for_device(timeout_s=config.timeouts["boot"])
            rooted = self._magisk_active()
        if not rooted:
            raise SrtkError(
                ErrorCode.FLASH_FAILED,
                context=self.name,
                details="Magisk still not active after recovery boot; re-run flash or "
                        "boot recovery via key combo manually",
            )
        log.info(f"Magisk active (version {rooted})")

        # ---- baseband / IMEI proxy -----------------------------------------------
        baseband = adb.getprop("gsm.version.baseband")
        if not baseband:
            raise SrtkError(
                ErrorCode.IMEI_LOST,
                context=self.name,
                details="gsm.version.baseband is empty — IMEI/baseband wipe risk; "
                        "restore stock firmware of the matching binary",
            )
        log.info(f"baseband present: {baseband}")

        report = {
            "slots": {k: str(v) for k, v in slots.items()},
            "magisk_version": rooted,
            "baseband": baseband,
            "bootloader": bootloader,
        }
        out = self.ctx.artifact("flash-report.json")
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self.ctx.state.set("flash", report)
        return [str(out)]

    # -- helpers -------------------------------------------------------------
    def _wait_flash_done(self, detector, timeout_s: float) -> None:
        """Poll until the device drops off COM and (re)appears on adb."""
        deadline = time.monotonic() + timeout_s
        saw_com = False
        while time.monotonic() < deadline:
            state, _ = detector.detect()
            if state in (DeviceState.DEVICE, DeviceState.RECOVERY):
                self.log.info("device reappeared on adb — flash complete")
                return
            if state is DeviceState.DOWNLOAD:
                saw_com = True
            time.sleep(5)
        raise SrtkError(
            ErrorCode.FLASH_FAILED,
            context=self.name,
            details=f"device did not reboot to adb within {timeout_s:.0f}s "
                    f"(last state {state.value})",
        )

    def _magisk_active(self) -> str | None:
        try:
            result = self.ctx.adb.shell("su", "-c", "magisk -v", timeout=30)
            if result.ok:
                v = result.stdout.strip()
                return v or None
        except Exception:
            pass
        return None
