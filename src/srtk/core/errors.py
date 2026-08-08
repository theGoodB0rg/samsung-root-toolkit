"""Typed error taxonomy for SRTK.

Every failure is raised as :class:`SrtkError` carrying an :class:`ErrorCode`.
The code drives the process exit status, the runbook/troubleshooting lookup,
and the remediation hint surfaced to the operator. Unknown/unexpected errors
are wrapped into ``INTERNAL_ERROR`` so the pipeline always fails loudly with a
reason rather than a bare traceback.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(Enum):
    # --- environment / host -------------------------------------------------
    TOOL_NOT_FOUND = (10, "Required tool was not found on the host: {tool}")
    DRIVER_MISSING = (11, "Samsung USB driver is not installed or not active")
    ADMIN_REQUIRED = (12, "This action requires an elevated (Administrator) shell")
    DISK_SPACE = (13, "Insufficient free disk space: need {need_gb} GB, have {have_gb} GB")
    PYTHON_VERSION = (14, "Python {version} or newer is required (got {found})")

    # --- device connectivity ------------------------------------------------
    DEVICE_OFFLINE = (20, "Device is not reachable over ADB")
    ADB_UNAVAILABLE = (21, "ADB transport is unavailable; falling back to physical runbook")
    USB_DEBUG_NOT_AUTHORIZED = (22, "USB debugging is enabled but the host is not authorized")
    USB_CHARGING_ONLY = (23, "USB cable is charging-only (no data lines) or bad port")
    DOWNLOAD_MODE_TIMEOUT = (24, "Timed out waiting for Download Mode (COM port) to appear")
    DOWNLOAD_TRANSPORT_DEGRADED = (25, "ADB reboot download failed; physical runbook path was used")

    # --- device policy gates ------------------------------------------------
    CARRIER_LOCKED = (30, "Device or ROM is carrier-locked / KnoxGuard blocks custom ROM")
    OEM_TOGGLE_HIDDEN = (31, "OEM unlock toggle is hidden (US/carrier build or One UI 8+)")
    KG_PRENORMAL = (32, "KG state is Prenormal/Checking; unlock may need ~7 days of uptime")
    FRP_LOCKED = (33, "FRP / Reactivation Lock is ON; unlock is blocked")
    CONSENT_MISSING = (34, "Client consent for data-wipe/Knox was not recorded")
    UNSUPPORTED_DEVICE = (35, "Unsupported device: expected {model}")

    # --- firmware / patch ----------------------------------------------------
    BINARY_MISMATCH = (40, "Firmware binary is not compatible with the device bootloader")
    FW_DOWNLOAD_FAILED = (41, "Firmware download failed: {reason}")
    HASH_MISMATCH = (42, "Checksum mismatch for {file}: expected {expected}, got {actual}")
    PATCH_FAILED = (43, "Magisk patch failed or produced no patched tar")

    # --- flash ---------------------------------------------------------------
    ODIN_NOT_DETECTED = (50, "Odin did not detect the device in Download Mode")
    FLASH_FAILED = (51, "Flash operation failed or did not report PASS")
    BOOTLOOP = (52, "Device did not reach a usable boot state after flashing")
    IMEI_LOST = (53, "IMEI/baseband is missing or Unknown after flash")

    # --- Play Integrity ------------------------------------------------------
    PI_BASIC_FAIL = (60, "Play Integrity BASIC verdict did not pass")
    PI_DEVICE_FAIL = (61, "Play Integrity DEVICE verdict did not pass")

    # --- pipeline ------------------------------------------------------------
    PHASE_PREREQ_FAIL = (70, "Phase prerequisites not satisfied: {reason}")
    DRY_RUN_ABORT = (71, "Aborting: mutating action blocked in dry-run mode")
    INTERNAL_ERROR = (90, "Unexpected internal error: {detail}")
    PHASE_SKIPPED = (91, "Phase skipped: {reason}")


@dataclass
class SrtkError(Exception):
    """An operation failure with a stable, machine-checkable code."""

    code: ErrorCode
    context: str = ""
    details: str | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        try:
            msg = self.code.value[1]
        except Exception:
            msg = str(self.code)
        if self.context:
            msg = f"{msg} [{self.context}]"
        if self.details:
            msg = f"{msg}: {self.details}"
        return msg

    @property
    def exit_code(self) -> int:
        return self.code.value[0]


def wrap_internal(exc: BaseException, context: str = "") -> SrtkError:
    """Wrap an unexpected exception into INTERNAL_ERROR, preserving detail."""
    return SrtkError(
        ErrorCode.INTERNAL_ERROR,
        context=context,
        details=f"{type(exc).__name__}: {exc}",
    )
