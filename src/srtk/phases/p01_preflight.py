"""Phase 01 — preflight.

Verifies the host is ready before any device is touched: Python version,
admin rights, disk space, required tools, Samsung driver presence, module
assets, and an initial read of what state the phone is in. Also checks the
USB cable sanity heuristic (Samsung device visible to Windows but not to adb
usually means charging-only cable or USB debugging off).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.errors import ErrorCode, SrtkError
from ..core.transport import DeviceState
from ..tools.host import (
    free_disk_gb,
    is_admin,
    python_version_ok,
    samsung_driver_packages,
    samsung_entities,
)
from ..tools.samloader import Samloader, require_samloader
from .base import Phase


class P01Preflight(Phase):
    name = "preflight"
    requires: tuple[str, ...] = ()
    produces = ("hardware_report",)

    def execute(self) -> list[str]:
        log = self.log
        report: dict = {"ok": True, "checks": {}}

        # --- Python ---------------------------------------------------------
        py_ok = python_version_ok()
        report["checks"]["python"] = {
            "ok": py_ok,
            "found": __import__("sys").version.split()[0],
            "required": ">=3.11",
        }
        if not py_ok:
            raise SrtkError(ErrorCode.PYTHON_VERSION, context=self.name)

        # --- admin ----------------------------------------------------------
        admin = is_admin(self.ctx.runner)
        report["checks"]["admin"] = {"ok": admin}
        if not admin:
            log.warn("not running as Administrator; driver installs will require elevation")

        # --- disk -----------------------------------------------------------
        drive = (self.ctx.workspace.drive or "C:").rstrip("\\") + "\\"
        free = free_disk_gb(drive)
        report["checks"]["disk_gb"] = {"free": round(free, 1), "needed": 8}
        if free < 8:
            raise SrtkError(ErrorCode.DISK_SPACE, context=self.name,
                            need_gb=8, have_gb=round(free, 1))

        # --- tools ----------------------------------------------------------
        tool_checks: dict = {}
        for key in ("adb_path", "samloader_path", "odin_path", "scrcpy_path"):
            path = getattr(self.ctx.config, key)
            tool_checks[key] = str(path)
            if not Path(path).exists():
                log.warn(f"{key} missing at {path} — run scripts/bootstrap.ps1")
        report["checks"]["tools"] = tool_checks

        report["checks"]["tools"]["samloader_version"] = "unknown"
        try:
            sl = Samloader(self.ctx.runner, self.ctx.config.samloader_path)
            report["checks"]["tools"]["samloader_version"] = require_samloader(sl)
        except SrtkError as exc:
            log.warn(str(exc))

        # --- module assets (needed in later phases) --------------------------
        assets: dict = {}
        for key, rel in (
            ("magisk_apk", "Magisk.apk"),
            ("pif_zip", "PlayIntegrityFork.zip"),
            ("tricky_store_zip", "TrickyStore.zip"),
            ("integrity_checker_apk", "IntegrityChecker.apk"),
        ):
            path = self.ctx.config.modules_dir / rel
            assets[key] = str(path)
            if not path.exists():
                log.warn(f"module asset missing: {path.name}")
        report["checks"]["module_assets"] = assets

        # --- Samsung driver + device presence --------------------------------
        drivers = samsung_driver_packages(self.ctx.runner)
        report["checks"]["samsung_driver"] = {
            "ok": bool(drivers),
            "packages": drivers[:5],
        }
        if not drivers:
            log.warn("Samsung USB driver package not found in driver store (DRIVER_MISSING risk)")

        entities = samsung_entities(self.ctx.runner)
        report["checks"]["samsung_entity"] = {
            "present": bool(entities),
            "names": entities[:5],
        }

        # --- device state ----------------------------------------------------
        state, detail = self.ctx.detector.detect()
        report["checks"]["device_state"] = {"state": state.value, "detail": detail}
        if state in (DeviceState.MISSING,):
            if entities:
                log.warn(
                    "Samsung device visible to Windows but not ADB — charging-only cable, "
                    "missing driver, or USB debugging off"
                )
            else:
                log.warn("no device detected; connect the phone with a data-capable cable")
        elif state is DeviceState.DOWNLOAD:
            log.info(f"device already in Download Mode: {detail}")
        elif state is DeviceState.DEVICE:
            log.info(f"device online via ADB: {detail}")
        elif state is DeviceState.UNAUTHORIZED:
            raise SrtkError(
                ErrorCode.USB_DEBUG_NOT_AUTHORIZED,
                context=self.name,
                details="accept the RSA fingerprint prompt on the phone",
            )

        artifact = self.ctx.artifact("hardware.json")
        artifact.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self.ctx.state.set("hardware_report", report)
        return [str(artifact)]
