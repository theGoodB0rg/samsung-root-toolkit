"""Phase 08 — finalize.

Backs up sensitive partitions (EFS/NVRAM/modem) now that root is available,
writes the client-facing final report, and bundles all evidence + logs into a
single zip the operator can hand off.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from .base import Phase

_EFS_SCRIPT = """#!/system/bin/sh
OUT=/data/local/tmp/srtk_backup
rm -rf "$OUT"
mkdir -p "$OUT"
for p in /dev/block/by-name/efs /dev/block/by-name/modemst1 /dev/block/by-name/modemst2 \\
         /dev/block/by-name/nvram /dev/block/by-name/modem /dev/block/by-name/ap_nvram; do
  if [ -e "$p" ]; then
    dd if="$p" of="$OUT/$(basename "$p").img" bs=4k 2>/dev/null
  fi
done
ls -1 "$OUT" 2>/dev/null
"""


class P08Finalize(Phase):
    name = "finalize"
    requires = ("preflight", "device_info", "flash", "play_integrity")
    produces = ("final_report", "evidence_bundle")

    def execute(self) -> list[str]:
        log = self.log
        workspace = self.ctx.workspace
        backup_dir = self.ctx.artifacts_dir / "08-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # ---- partition backup (best effort) -------------------------------------
        res = self._root_backup()
        pulled: list[str] = []
        for line in (res.stdout or "").splitlines():
            name = line.strip()
            if not name or name.startswith("total"):
                continue
            remote = f"/data/local/tmp/srtk_backup/{name}"
            local = backup_dir / name
            if self.ctx.adb.pull(remote, local, timeout=600).ok and local.exists():
                pulled.append(local.name)
        if pulled:
            log.info(f"backed up: {', '.join(pulled)}")
        else:
            log.warn("no partition backups produced (path names may differ on this build)")

        # ---- final report ---------------------------------------------------------
        report_md = self._build_report(pulled)
        report_path = self.ctx.artifact("final-report.md")
        report_path.write_text(report_md, encoding="utf-8")
        self.ctx.state.set("evidence", pulled + [str(report_path)])

        # ---- evidence bundle ------------------------------------------------------
        bundle = workspace / "evidence-bundle.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:

            def _add(path: Path, arcname: str) -> None:
                # Fixed timestamp: extracted firmware members can carry pre-1980
                # mtimes which zipfile refuses to encode.
                info = zipfile.ZipInfo(arcname)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.date_time = (1980, 1, 1, 0, 0, 0)
                with path.open("rb") as fh:
                    zf.writestr(info, fh.read())

            for root_dir, arc_prefix in (
                (self.ctx.evidence_dir, "evidence"),
                (self.ctx.logs_dir, "logs"),
                (self.ctx.artifacts_dir, "artifacts"),
            ):
                if not root_dir.exists():
                    continue
                for path in sorted(root_dir.rglob("*")):
                    if path.is_file():
                        _add(path, f"{arc_prefix}/{path.relative_to(root_dir)}")

        self.ctx.state.set("evidence_bundle", str(bundle))
        log.info(f"evidence bundle: {bundle}")
        return [str(report_path), str(bundle)]

    # -- helpers -------------------------------------------------------------
    def _root_backup(self):
        tmp = self.ctx.workspace / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        local = tmp / "efs_backup.sh"
        local.write_text(_EFS_SCRIPT, encoding="utf-8", newline="\n")
        self.ctx.adb.push(local, "/data/local/tmp/efs_backup.sh", timeout=60)
        return self.ctx.adb.shell("su", "-c", "sh /data/local/tmp/efs_backup.sh", timeout=900)

    def _build_report(self, backups: list[str]) -> str:
        s = self.ctx.state
        dev = s.get("device_report") or {}
        fw = s.get("firmware") or {}
        patch = s.get("magisk_patch") or {}
        pi = s.get("play_integrity") or {}
        flash = s.get("flash") or {}
        consent = s.get("consent") or {}

        lines = [
            "# SRTK — Remote Root Report",
            "",
            f"- Job: `{self.ctx.job_id}`",
            f"- Model: {dev.get('model')}",
            f"- Android: {dev.get('android_version')}  One UI: {dev.get('one_ui') or 'n/a'}",
            f"- Security patch: {dev.get('security_patch')}",
            f"- Bootloader binary: {dev.get('binary')}  CSC: {dev.get('csc')}",
            f"- Bootloader unlocked: {dev.get('bootloader_unlocked')}",
            "",
            "## Firmware",
            f"- Model/CSC: {fw.get('model')}/{fw.get('csc')}",
            f"- Parts: {', '.join(fw.get('parts', {}).values())}",
            f"- AP binary token: {fw.get('ap_binary')}",
            f"- Magisk mode hint: {fw.get('magisk_mode_hint')}",
            "",
            "## Magisk patch",
            f"- Patched tar SHA-256: `{patch.get('patched_sha256', 'n/a')}`",
            f"- Members: {patch.get('bootish_members')}",
            "",
            "## Flash",
            f"- Slots: {json.dumps(flash.get('slots', {}), indent=2)}",
            f"- Magisk active: v{flash.get('magisk_version')}",
            f"- Baseband: {flash.get('baseband')}",
            "",
            "## Play Integrity",
            f"- BASIC: {'PASS' if pi.get('basic') else 'FAIL'}",
            f"- DEVICE: {'PASS' if pi.get('device') else 'FAIL'}",
            f"- Attempts: {pi.get('attempts')}",
            "",
            "## Backups",
            f"- Partitions: {', '.join(backups) or 'none captured'}",
            "",
            "## Consent",
            f"- Unlock + Knox + data-wipe acknowledged at: {consent.get('signed_utc', 'n/a')}",
            "",
            "## Operator notes",
            "- This device is rooted via Magisk with an **unlocked bootloader**.",
            "- **Knox is tripped**: Samsung Wallet/Pass/Secure Folder will not work; warranty is void.",
            "- **Do NOT take OTA updates** — OS upgrades must be done by re-patching a new AP with Magisk.",
            "- **Do NOT relock the bootloader** while modified firmware is installed (risk of brick).",
            "- Play Integrity fingerprints expire (~6 weeks): re-run the PIF refresh action when "
            "apps start failing.",
            "- Restore from EFS/NVRAM backups above if IMEI/baseband ever appears missing.",
            "",
        ]
        return "\n".join(lines)
