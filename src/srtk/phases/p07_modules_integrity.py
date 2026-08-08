"""Phase 07 — integrity modules + Play Integrity verification.

Installs the Play Integrity module stack (Zygisk + PlayIntegrityFix fork +
Tricky Store), configures Tricky Store's spoof targets, refreshes the PIF
fingerprint, and verifies BASIC + DEVICE verdicts using the Integrity Checker
app driven over adb (uiautomator text extraction + screenshot evidence).

Verdicts are an arms race: fingerprints expire ~every 6 weeks. A failed DEVICE
check triggers a PIF refresh + GMS force-stop retry before failing.
"""
from __future__ import annotations

import json
import re
import time

from ..core.errors import ErrorCode, SrtkError
from ..core.transport import DeviceState
from .base import Phase

_CHECKER_PKG = "gr.nikolasspyr.integritycheck"

_TRICKY_TARGETS = """#!/system/bin/sh
mkdir -p /data/adb/tricky_store
printf 'com.google.android.gms\\ncom.android.vending\\n' > /data/adb/tricky_store/target.txt
chmod 755 /data/adb/tricky_store
"""

_PIF_REFRESH = """#!/system/bin/sh
for f in /data/adb/modules/playintegrityfix/autopif*.sh; do
  [ -f "$f" ] && sh "$f" && break
done
exit 0
"""


class P07ModulesIntegrity(Phase):
    name = "play_integrity"
    requires = ("preflight", "flash")
    produces = ("play_integrity",)

    def execute(self) -> list[str]:
        log = self.log
        adb = self.ctx.adb
        modules = self.ctx.config.modules_dir

        pif_zip = modules / "PlayIntegrityFork.zip"
        tricky_zip = modules / "TrickyStore.zip"
        checker_apk = modules / "IntegrityChecker.apk"
        for path, label in ((pif_zip, "PlayIntegrityFork.zip"),
                            (tricky_zip, "TrickyStore.zip"),
                            (checker_apk, "IntegrityChecker.apk")):
            if not path.exists() and not self.ctx.sim:
                raise SrtkError(ErrorCode.TOOL_NOT_FOUND, context=label,
                                details=f"expected at {path}")

        state, detail = self.ctx.detector.detect()
        if state is not DeviceState.DEVICE:
            raise SrtkError(ErrorCode.DEVICE_OFFLINE, context=self.name,
                            details=f"need a booted device on adb; got {state.value}")

        # ---- Zygisk (required by PIF) ------------------------------------------
        self.ctx.ui.instruct(
            "Enable Zygisk",
            "In the Magisk app: Settings > toggle 'Zygisk' ON, then reboot the phone.",
            evidence="pi-00-zygisk",
        )
        adb.wait_for_device(timeout_s=self.ctx.config.timeouts["boot"])
        adb.shell("input", "keyevent", "82", timeout=20)

        # ---- install modules ----------------------------------------------------
        log.info("installing PlayIntegrityFix + TrickyStore modules...")
        for remote, local in (("/data/local/tmp/pif.zip", pif_zip),
                              ("/data/local/tmp/tricky.zip", tricky_zip)):
            adb.push(local, remote, timeout=300)
            res = adb.shell("su", "-c", f"magisk --install-module {remote}", timeout=300)
            if not res.ok:
                raise SrtkError(ErrorCode.FLASH_FAILED, context=self.name,
                                details=f"module install failed: {res.stderr.strip()[:400]}")

        self._root_sh(_TRICKY_TARGETS, "tricky_targets")
        self._root_sh(_PIF_REFRESH, "pif_refresh")

        log.info("rebooting to apply modules...")
        adb.reboot("system")
        adb.wait_for_device(timeout_s=self.ctx.config.timeouts["boot"])
        adb.shell("input", "keyevent", "82", timeout=20)

        # ---- install checker app --------------------------------------------------
        install = adb.install(checker_apk, timeout=300)
        if not install.ok:
            raise SrtkError(ErrorCode.PI_BASIC_FAIL, context=self.name,
                            details=f"could not install Integrity Checker: {install.stderr.strip()[:400]}")

        # ---- verify ----------------------------------------------------------------
        attempts = self.ctx.state.get("play_integrity_attempts") or 0
        verdicts, xml = self._run_check(attempt=attempts + 1)
        if not verdicts.get("basic"):
            raise SrtkError(ErrorCode.PI_BASIC_FAIL, context=self.name,
                            details=f"verdicts after attempt {attempts + 1}: {verdicts}")
        if not verdicts.get("device"):
            if attempts < 3:
                log.warn("DEVICE verdict failed; refreshing fingerprint + retrying")
                self.ctx.state.set("play_integrity_attempts", attempts + 1)
                adb.shell("am", "force-stop", "com.google.android.gms", timeout=30)
                adb.shell("am", "force-stop", "com.android.vending", timeout=30)
                self._root_sh(_PIF_REFRESH, "pif_refresh")
                return self.execute()
            raise SrtkError(ErrorCode.PI_DEVICE_FAIL, context=self.name,
                            details=f"DEVICE verdict failed after 3 refreshes: {verdicts}")

        evidence = self.ctx.ui.capture_evidence("pi-final-verdict")
        report = {
            "basic": verdicts.get("basic"),
            "device": verdicts.get("device"),
            "attempts": attempts + 1,
            "evidence": str(evidence),
        }
        out = self.ctx.artifact("play-integrity-report.json")
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        self.ctx.state.set("play_integrity", report)
        return [str(out)]

    # -- helpers -------------------------------------------------------------
    def _root_sh(self, script: str, name: str):
        tmp = self.ctx.workspace / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        local = tmp / f"{name}.sh"
        local.write_text(script, encoding="utf-8", newline="\n")
        self.ctx.adb.push(local, f"/data/local/tmp/{name}.sh", timeout=60)
        return self.ctx.adb.shell("su", "-c", f"sh /data/local/tmp/{name}.sh", timeout=300)

    def _run_check(self, attempt: int) -> tuple[dict, str]:
        adb = self.ctx.adb
        adb.shell("input", "keyevent", "82", timeout=20)
        adb.shell("monkey", "-p", _CHECKER_PKG, "-c", "android.intent.category.LAUNCHER", "1",
                  timeout=60)
        time.sleep(8)
        adb.shell("uiautomator", "dump", "/sdcard/pi.xml", timeout=120)
        cat = adb.shell("cat", "/sdcard/pi.xml", timeout=60)
        xml = cat.stdout
        verdicts = self._parse_verdicts(xml)
        self.log.info(f"attempt {attempt}: verdicts={verdicts}")
        self.ctx.ui.capture_evidence(f"pi-attempt-{attempt}")
        return verdicts, xml

    @staticmethod
    def _parse_verdicts(xml: str) -> dict[str, bool | None]:
        """Best-effort parse of uiautomator dump text nodes.

        Verdict rows carry the MEETS_* token; pass/fail is inferred from a
        nearby check/cross marker. Ambiguous rows return None (the evidence
        screenshot is always captured for operator confirmation).
        """
        tokens = {
            "basic": "MEETS_BASIC_INTEGRITY",
            "device": "MEETS_DEVICE_INTEGRITY",
        }
        pass_markers = ("✅", "✓", "✔", "PASS", "pass", "true")
        fail_markers = ("❌", "✗", "✘", "FAIL", "fail", "false")
        result: dict[str, bool | None] = {}
        text = xml
        for key, token in tokens.items():
            idx = text.find(token)
            if idx < 0:
                result[key] = None
                continue
            window = text[idx : idx + 300]
            if any(m in window for m in pass_markers):
                result[key] = True
            elif any(m in window for m in fail_markers):
                result[key] = False
            else:
                result[key] = None
        return result
