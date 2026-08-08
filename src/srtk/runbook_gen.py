"""Operator/client runbook generation.

Renders, for each phase, the operator actions the toolkit drives or prompts for
and the client actions that must happen on the phone. Physical-only steps (the
ones adb cannot do) are flagged so a non-technical client can follow them with
the operator on a call.
"""
from __future__ import annotations

from .phases import ORDER

_RUNBOOKS: dict[str, dict[str, object]] = {
    "preflight": {
        "title": "Host readiness check (no device needed)",
        "operator": [
            "Run `srtk run preflight`. Fix any FAILED check (Python, admin, disk, tools).",
            "Confirm adb works: `adb devices` should list the phone when plugged in.",
            "If tools are missing, run scripts/bootstrap.ps1 from an elevated PowerShell.",
        ],
        "client": [
            "Have the phone ready with a data-capable USB cable (not charging-only).",
        ],
    },
    "device_info": {
        "title": "Read device identity + firmware state",
        "operator": [
            "Run `srtk run device_info`; review device-report.json (model, binary, CSC, lock state).",
            "Confirm the model is SM-A325F and the CSC matches the purchased region.",
        ],
        "client": [
            "Unlock the phone and accept the 'Allow USB debugging' prompt on the phone.",
        ],
    },
    "unlock": {
        "title": "Bootloader unlock (destructive: factory reset + permanent Knox trip)",
        "operator": [
            "Record client consent (the toolkit prompts; type the job id).",
            "Drive Developer options / USB debugging / OEM unlock via scrcpy.",
            "Enter Download Mode via `adb reboot download` (or the physical fallback).",
            "Read the Download Mode screen; refuse on REACTIVATION LOCK ON or KG Prenormal.",
            "Prompt the client for the single physical Vol-UP confirm.",
            "After the wipe: client completes setup and re-enables USB debugging.",
            "Verify the OEM unlocking toggle is present and grayed out (VaultKeeper released).",
        ],
        "client": [
            "*Physical*: long-press Volume Up until 'Unlock Bootloader?' appears, then press Volume Up again to confirm.",
            "Complete the setup wizard (Wi-Fi) after the wipe.",
            "Re-enable Developer options > USB debugging and accept the RSA prompt.",
        ],
    },
    "firmware": {
        "title": "Download + verify firmware (no client action)",
        "operator": [
            "Run `srtk run firmware`. Downloads the latest firmware for model/CSC via samloader-rs.",
            "Toolkit verifies tar.md5 checksums, writes a SHA-256 manifest, and checks binary compatibility.",
            "Expect ~4-6 GiB download; use a fast connection.",
        ],
        "client": [],
    },
    "magisk_patch": {
        "title": "Patch the AP with Magisk",
        "operator": [
            "Run `srtk run magisk_patch`. Toolkit pushes the stock AP tar (~3 GiB) over adb.",
            "In scrcpy: open Magisk > Install > 'Select and Patch a File' > srtk_ap.tar.",
            "Wait for 'All done!'; the toolkit pulls magisk_patched_*.tar and pins its SHA-256.",
        ],
        "client": [
            "Keep the phone awake and unlocked while the patch runs (several minutes).",
        ],
    },
    "flash": {
        "title": "Flash via Odin3 (full data wipe)",
        "operator": [
            "Run `srtk run flash`; the toolkit enters Download Mode and waits for the COM port.",
            "In Odin3: load BL/AP/CP/CSC with the PATCHED AP. Use CSC (full wipe), NOT HOME_CSC.",
            "Check only 'Auto Reboot' + 'F. Reset Time'; click Start; wait for green PASS!.",
            "Toolkit verifies the bootloader, arms Magisk via one recovery boot, and checks baseband.",
        ],
        "client": [
            "Do NOT unplug the phone during the flash. A red FAIL screen means stop and call the operator.",
        ],
    },
    "play_integrity": {
        "title": "Integrity modules + verdict verification",
        "operator": [
            "Run `srtk run play_integrity`. Toolkit enables Zygisk (scrcpy), installs PIF + Tricky Store, sets spoof targets, refreshes the fingerprint, and reboots.",
            "Toolkit installs Integrity Checker and reads BASIC + DEVICE verdicts from a uiautomator dump.",
            "On DEVICE failure it auto-refreshes the PIF fingerprint up to 3 times.",
        ],
        "client": [
            "Reboot if prompted; keep the phone connected until verdicts pass.",
        ],
    },
    "finalize": {
        "title": "Backups, report, evidence bundle",
        "operator": [
            "Run `srtk run finalize`. Backs up EFS/NVRAM/modem partitions over adb root.",
            "Writes final-report.md and evidence-bundle.zip under runs/<job>/.",
            "Hand evidence-bundle.zip + final-report.md to the client.",
        ],
        "client": [],
    },
}


def phase_runbook(name: str) -> str:
    """Render the runbook for one phase as markdown."""
    entry = _RUNBOOKS[name]
    lines = [f"## {name} — {entry['title']}", ""]
    if entry["operator"]:
        lines += ["### Operator actions", ""]
        lines += [f"{i}. {s}" for i, s in enumerate(entry["operator"], 1)] + [""]
    if entry["client"]:
        lines += ["### Client actions", ""]
        lines += [f"{i}. {s}" for i, s in enumerate(entry["client"], 1)] + [""]
    return "\n".join(lines).rstrip() + "\n"


def full_runbook() -> str:
    """Render the complete runbook over all phases as markdown."""
    lines = [
        "# SRTK Runbook",
        "",
        "Full pipeline order. *Physical* steps are the only ones a client must",
        "perform by hand; everything else is driven by the toolkit over adb/scrcpy",
        "by the operator.",
        "",
    ]
    for name in ORDER:
        lines.append(phase_runbook(name).rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
