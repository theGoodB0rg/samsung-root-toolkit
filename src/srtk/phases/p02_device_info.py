"""Phase 02 — device info.

Pulls the full device identity over adb and writes ``device-report.json``:
model, Android/One UI, bootloader binary, OEM-lock state, FRP indicators,
carrier/knox-guard flags. This is the runtime answer to "what version is the
phone on" and feeds every later phase's compatibility decisions.
"""
from __future__ import annotations

import json

from ..core.errors import ErrorCode, SrtkError
from ..core.transport import DeviceState
from ..core.versioning import extract_binary
from .base import Phase

_KEYS = [
    "ro.product.model",
    "ro.product.device",
    "ro.product.manufacturer",
    "ro.build.version.release",       # Android version
    "ro.build.version.sdk",
    "ro.build.version.security_patch",
    "ro.build.display.id",            # firmware build id
    "ro.build.fingerprint",
    "ro.build.version.oneui",         # One UI version when exposed
    "ro.bootloader",                  # bootloader version -> binary
    "ro.boot.warranty_bit",           # 0 locked / 1 unlocked (Knox)
    "ro.secureboot.lockstate",        # locked | unlocked
    "ro.boot.flash.locked",           # 1 locked / 0 unlocked
    "ro.boot.vbmeta.device_state",    # locked | unlocked
    "ro.frp.pst",                     # FRP partition path (empty => no FRP)
    "ro.boot.other.locked",           # 1 => bootloader unlock code stripped
    "ro.oem_unlock_supported",        # 1 when OEM unlock allowed
    "ro.csc.sales_code",              # CSC / sales region
    "ro.csc.country_iso_code",
    "gsm.version.baseband",           # baseband version (proxy for IMEI health)
    "sys.boot_completed",
]

_SAMSUNG_KNOX_HINTS = ("warranty_bit", "knox")


def _kv(text: str) -> tuple[str, str] | None:
    if ":" not in text:
        return None
    k, _, v = text.partition(":")
    return k.strip().strip("[]"), v.strip().strip("[]")


class P02DeviceInfo(Phase):
    name = "device_info"
    requires = ("preflight",)
    produces = ("device_report",)

    def execute(self) -> list[str]:
        log = self.log
        state, detail = self.ctx.detector.detect()
        if state not in (DeviceState.DEVICE, DeviceState.RECOVERY):
            raise SrtkError(
                ErrorCode.DEVICE_OFFLINE,
                context=self.name,
                details=f"need a booted device on adb; got {state.value}: {detail}",
            )

        report: dict = {"props": {}}
        for key in _KEYS:
            report["props"][key] = self.ctx.adb.getprop(key)

        # Multi-line props (device_model, carrier, etc.)
        full = self.ctx.adb.shell("getprop", timeout=20)
        for line in full.stdout.splitlines():
            if ":" in line:
                k, v = _kv(line)
                if k and k not in report["props"]:
                    report["props"][k] = v

        report["model"] = report["props"].get("ro.product.model", "SM-A325F")
        report["android_version"] = report["props"].get("ro.build.version.release")
        report["security_patch"] = report["props"].get("ro.build.version.security_patch")
        report["bootloader"] = report["props"].get("ro.bootloader")
        report["binary"] = extract_binary(report["bootloader"] or "") or extract_binary(
            report["props"].get("ro.build.display.id") or ""
        )
        report["csc"] = report["props"].get("ro.csc.sales_code")
        report["baseband"] = report["props"].get("gsm.version.baseband")

        # ---- derived lock state ---------------------------------------------
        lockstate = report["props"].get("ro.secureboot.lockstate")
        flash_locked = report["props"].get("ro.boot.flash.locked")
        warranty_bit = report["props"].get("ro.boot.warranty_bit")
        unlocked = (
            (lockstate or "").lower() == "unlocked"
            or flash_locked == "0"
            or warranty_bit == "1"
        )
        report["bootloader_unlocked"] = unlocked
        report["oem_unlock_supported"] = (
            report["props"].get("ro.oem_unlock_supported") == "1"
        )
        report["carrier_locked_hint"] = (
            report["props"].get("ro.boot.other.locked") == "1"
        )
        report["frp_pst"] = report["props"].get("ro.frp.pst")
        report["one_ui"] = report["props"].get("ro.build.version.oneui")

        log.info(
            f"model={report['model']} android={report['android_version']} "
            f"binary={report['binary']} csc={report['csc']} "
            f"unlocked={unlocked} baseband={'yes' if report['baseband'] else 'no'}"
        )
        if report["carrier_locked_hint"]:
            log.warn("ro.boot.other.locked=1 — OEM unlock may be stripped (carrier/US build)")

        artifact = self.ctx.artifact("device-report.json")
        artifact.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self.ctx.state.set("device_report", report)
        return [str(artifact)]
