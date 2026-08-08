"""Phase 03 — bootloader unlock.

Drives the official Samsung unlock path with adb-first transport:

1. Fail fast on carrier/knox-guard flags.
2. Record client consent for data-wipe + Knox trip.
3. Operator enables Developer Options + USB debugging + OEM unlock toggle
   (via scrcpy), toolkit verifies adb is authorized.
4. ``adb reboot download`` (primary) or physical runbook (fallback).
5. Read the Download Mode screen gates (OEM LOCK / REACTIVATION / KG) and
   refuse on FRP or Prenormal KG.
6. Client confirms unlock (the single unavoidable physical Vol-UP press),
   device wipes and reboots.
7. Client completes setup wizard and re-enables USB debugging (adapter step,
   since the wipe cleared it), then toolkit verifies bootloader_unlocked and
   the VaultKeeper-unleashed state.

Always safe to re-run: if the device is already unlocked it skips.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..core.errors import ErrorCode, SrtkError
from ..core.transport import DeviceState
from .base import Phase


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class P03Unlock(Phase):
    name = "unlock"
    requires = ("preflight", "device_info")
    produces = ("unlock_report", "consent")

    def execute(self) -> list[str]:
        log = self.log
        report = self.ctx.state.get("device_report") or {}
        ui = self.ctx.ui
        adb = self.ctx.adb
        detector = self.ctx.detector

        # ---- already unlocked? ----------------------------------------------
        if report.get("bootloader_unlocked"):
            log.info("bootloader already unlocked; skipping unlock phase")
            out = self.ctx.artifact("unlock-report.json")
            out.write_text(json.dumps({"unlocked": True, "skipped": True}) + "\n", encoding="utf-8")
            self.ctx.state.set("unlock_report", {"unlocked": True, "skipped": True})
            return [str(out)]

        # ---- carrier / knox-guard fail-fast ----------------------------------
        if report.get("carrier_locked_hint"):
            raise SrtkError(
                ErrorCode.OEM_TOGGLE_HIDDEN,
                context=self.name,
                details="ro.boot.other.locked=1; bootloader unlock code is stripped from this build",
            )
        if report.get("oem_unlock_supported") is False:
            log.warn("ro.oem_unlock_supported=0 — the OEM toggle may be hidden")

        # ---- consent ----------------------------------------------------------
        if not self.ctx.sim:
            ui.confirm_destructive(
                "Bootloader unlock will FACTORY RESET this device and PERMANENTLY trip "
                "the Knox fuse (Samsung Wallet/Pass/Secure Folder stop working, warranty void)."
            )
        self.ctx.state.set(
            "consent",
            {
                "unlock": True,
                "knox_disclosure": True,
                "data_wipe_ack": True,
                "signed_utc": _ts(),
            },
        )
        self.ctx.state.save()

        # ---- enable debugging + OEM toggle (scrcpy) ---------------------------
        ui.instruct(
            "Enable Developer Options + USB debugging + OEM unlock",
            "Using scrcpy on the remote session:\n"
            "  1. Settings > About phone > Software information\n"
            "  2. Tap 'Build number' 7-10x until 'You are now a developer'\n"
            "  3. Settings > Developer options > toggle 'USB debugging' ON\n"
            "  4. Toggle 'OEM unlocking' ON (confirm the dialog)\n"
            "  5. Accept the 'Allow USB debugging' RSA prompt if shown",
            evidence="unlock-00-devtoggle",
        )

        state, detail = detector.detect()
        if state is DeviceState.UNAUTHORIZED:
            raise SrtkError(
                ErrorCode.USB_DEBUG_NOT_AUTHORIZED,
                context=self.name,
                details="accept the RSA fingerprint prompt on the phone",
            )
        if state not in (DeviceState.DEVICE, DeviceState.RECOVERY):
            raise SrtkError(
                ErrorCode.DEVICE_OFFLINE,
                context=self.name,
                details=f"expected device on adb after enabling debugging; got {state.value}",
            )

        # ---- enter download mode (adb primary) --------------------------------
        transport_used = "adb"
        try:
            detail = detector.reboot_to_download(
                timeout_s=self.ctx.config.timeouts.get("download_mode", 180)
            )
            log.info(f"Download Mode via adb: {detail}")
        except SrtkError as exc:
            if exc.code is not ErrorCode.ADB_UNAVAILABLE:
                raise
            transport_used = "physical"
            self.ctx.state.note("ADB reboot download failed; using physical runbook path")
            ui.instruct(
                "Enter Download Mode (physical)",
                "With the phone OFF, hold **Volume Up + Volume Down** together and "
                "plug the USB cable into the PC. Release once the blue/black warning "
                "screen appears, then press Volume Up to accept.",
            )
            detail = detector.wait_for_download(
                timeout_s=self.ctx.config.timeouts.get("download_mode", 180)
            )
            log.warn(f"Download Mode via physical runbook: {detail}")

        # ---- read download-mode gates -----------------------------------------
        screen = self._read_download_screen(ui)
        if str(screen.get("reactivation", "")).strip().upper() == "ON":
            raise SrtkError(
                ErrorCode.FRP_LOCKED,
                context=self.name,
                details="REACTIVATION LOCK is ON; disable it in Settings > "
                "Biometrics & security > Find My Mobile, then re-run.",
            )
        kg = str(screen.get("kg", "")).strip().lower()
        if "prenormal" in kg or "checking" in kg:
            raise SrtkError(
                ErrorCode.KG_PRENORMAL,
                context=self.name,
                details="KG state is Prenormal/Checking; OEM unlock may require ~7 days "
                "of continuous uptime before the toggle is permitted.",
            )

        # ---- confirm unlock (single physical step) ----------------------------
        ui.instruct(
            "Confirm bootloader unlock (physical)",
            "On the phone, LONG-PRESS **Volume Up** until 'Unlock Bootloader?' appears, "
            "then press **Volume Up** again to confirm.\n"
            "The phone will wipe ALL data and reboot into the setup wizard.",
            evidence="unlock-01-before-unlock",
        )

        # ---- post-wipe adapter step: device is wiped, debugging is off ---------
        ui.instruct(
            "Post-wipe setup + re-enable USB debugging",
            "The wipe reset USB debugging. On the phone:\n"
            "  1. Complete the setup wizard (connect to Wi-Fi).\n"
            "  2. Settings > About phone > Software information > tap 'Build number' 7x.\n"
            "  3. Developer options > 'USB debugging' ON.\n"
            "  4. Accept the 'Allow USB debugging' RSA prompt when it appears.",
            evidence="unlock-02-setup",
        )

        # ---- wait for adb and verify unlocked ----------------------------------
        adb.wait_for_device(timeout_s=self.ctx.config.timeouts.get("boot", 240))
        self.ctx.adb.shell("input", "keyevent", "82", timeout=20)  # wake/unlock screen
        fresh = self._reprobe_lock_state()
        if not fresh["bootloader_unlocked"]:
            raise SrtkError(
                ErrorCode.ADB_UNAVAILABLE,
                context=self.name,
                details="device back on adb but bootloader still reports locked; "
                        "re-run this phase after confirming the unlock took effect",
            )

        ui.instruct(
            "Verify VaultKeeper unleash",
            "In Developer options the 'OEM unlocking' toggle must now be present and "
            "GRAYED OUT (this is VaultKeeper releasing the bootloader). Confirm via "
            "scrcpy so Odin will accept the patched firmware.",
            evidence="unlock-03-unleashed",
        )

        out = self.ctx.artifact("unlock-report.json")
        out.write_text(
            json.dumps({"unlocked": True, "transport": transport_used, "screen": screen}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        self.ctx.state.set("unlock_report", {"unlocked": True, "transport": transport_used})
        return [str(out)]

    # -- helpers -------------------------------------------------------------
    def _read_download_screen(self, ui) -> dict:
        """In real mode the operator reads the Download Mode screen (adb is off).

        Accepted as free text like ``OEM=ON REACTIVATION=OFF KG=Normal``.
        """
        if self.ctx.sim:
            return {"oem": "ON", "reactivation": "OFF", "kg": "Normal"}
        prompt = (
            "Read the Download Mode screen (OEM LOCK / REACTIVATION LOCK / KG STATE) "
            "and type, e.g.:  OEM=ON REACTIVATION=OFF KG=Normal\n> "
        )
        answer = input(prompt).strip() or ""
        screen: dict = {}
        for token in answer.replace(",", " ").split():
            if "=" in token:
                k, _, v = token.partition("=")
                screen[k.lower()] = v
        return screen

    def _reprobe_lock_state(self) -> dict:
        props = {
            k: self.ctx.adb.getprop(k)
            for k in ("ro.secureboot.lockstate", "ro.boot.flash.locked", "ro.boot.warranty_bit")
        }
        unlocked = (
            (props.get("ro.secureboot.lockstate") or "").lower() == "unlocked"
            or props.get("ro.boot.flash.locked") == "0"
            or props.get("ro.boot.warranty_bit") == "1"
        )
        return {"bootloader_unlocked": unlocked, "props": props}
