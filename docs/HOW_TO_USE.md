# SRTK — Operator Guide (HOW_TO_USE.md)

This is the operator-facing manual for running the remote SM-A325F unlock +
root service with SRTK. The client does a handful of physical steps; you drive
everything else. Read [CLIENT_RUNBOOK.md](CLIENT_RUNBOOK.md) once and hand the
client the relevant excerpt when you get there.

## 0. Before the session

1. **Consent**: tell the client exactly what happens (factory reset, permanent
   Knox trip, warranty void, no more OTA updates). Record their agreement
   before starting — the `unlock` phase will prompt for it again.
2. **Environment**: a Windows 10/11 machine with Python 3.11+. Clone the
   toolkit and run bootstrap **as Administrator**:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
   ```

   The script downloads adb/fastboot, samloader-rs and scrcpy from official
   sources. Two components have no stable auto source and are printed for
   manual placement in `tools\bin\`:
   - **Samsung USB driver** — `SAMSUNG_USB_Driver_for_Mobile_Phones.exe`
   - **Odin3 v3.14.x** — `Odin3.exe`
3. **Module assets** in `src\srtk\modules\` (bootstrap attempts them; verify
   they exist):
   - `Magisk.apk` (from the Magisk GitHub release, the `-apk` asset)
   - `PlayIntegrityFork.zip`
   - `TrickyStore.zip`
   - `IntegrityChecker.apk`
4. **Connectivity check** with the phone plugged in:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\driver_check.ps1
   ```

   You want `adb devices` to list the phone as `device`. If it shows nothing
   but Windows sees the phone: data-capable cable / USB debugging on. If it
   shows `unauthorized`: accept the RSA prompt on the phone.

## 1. Try the pipeline offline first

Nothing touches a real phone in `--sim`:

```powershell
python -m srtk --root . --sim --yes run all
```

This drives a simulated device through all 8 phases and produces a full
evidence bundle under `runs/job-default/`. If it passes, the toolkit is sound.

## 2. Starting a real job

```powershell
python -m srtk --root . init job-001
python -m srtk --root . --job job-001 run preflight
```

`preflight` is non-destructive; it validates the host and tells you what state
the phone is in. Fix anything it flags before continuing. `device_info` is also
read-only. Use `srtk runbook <phase>` to see the exact operator + client steps
for each phase before you reach it.

## 3. Running the phases

```powershell
python -m srtk --root . --job job-001 run unlock      # destructive
python -m srtk --root . --job job-001 run firmware    # big download
python -m srtk --root . --job job-001 run magisk_patch
python -m srtk --root . --job job-001 run flash       # destructive
python -m srtk --root . --job job-001 run play_integrity
python -m srtk --root . --job job-001 run finalize
```

The plan auto-includes prerequisites, so `srtk run all` on a fresh job does
everything in order. `srtk status` shows what passed; `--resume` skips passed
phases after an interruption.

### 3.1 `unlock` — the risky one

- The toolkit drives Developer options + USB debugging + OEM unlock via scrcpy
  and enters Download Mode with `adb reboot download`.
- **Refuses to continue** when the Download Mode screen shows **REACTIVATION
  LOCK: ON** (disable Find My Mobile first) or **KG: Prenormal/Checking**
  (typically needs ~7 days of network uptime before the OEM toggle is allowed).
- The single physical step the client must do: **long-press Volume Up**, then
  **Volume Up again** to confirm. The phone wipes and reboots.
- After the wipe the client re-enables USB debugging; you verify the OEM
  unlocking toggle is now **grayed out** (VaultKeeper released). Do not proceed
  to flash until it is — Odin will reject the patched AP otherwise.

### 3.2 `firmware`

Downloads the latest firmware for the detected model/CSC (FUS via samloader).
Verifies tar.md5, writes a SHA-256 manifest, and refuses a binary that is lower
than the device's current bootloader (downgrade protection). Expect ~4-6 GiB.

### 3.3 `magisk_patch`

Pushes the stock AP tar (~3 GiB, adb only — MTP corrupts large files) and has
you patch it in the Magisk app via scrcpy: *Install → Select and Patch a File →
srtk_ap.tar*. The patched tar is pulled back and its SHA-256 is pinned for the
flash phase.

### 3.4 `flash`

Flashes **BL + patched AP + CP + CSC** through Odin3 (full data wipe — never
HOME_CSC on the initial install). Only *Auto Reboot* + *F. Reset Time* checked.
After the green PASS the toolkit boots the device, then boots to recovery once
to **arm Magisk** (the A32's boot partition carries no ramdisk, so Magisk lives
in recovery), and verifies baseband is intact (IMEI proxy). A missing baseband
means stop and restore stock — do not leave the session.

### 3.5 `play_integrity`

Enables Zygisk, installs PlayIntegrityFix + Tricky Store, writes the spoof
targets, refreshes the PIF fingerprint, reboots, and reads BASIC + DEVICE
verdicts from the Integrity Checker app. On a DEVICE failure it refreshes the
fingerprint and retries up to 3 times, then fails with exit `61`.

> Verdicts are an arms race. Fingerprints expire roughly every 6 weeks — the
> client will eventually see DEVICE fail in apps; the fix is a PIF refresh +
> GMS stop, exactly what this phase automates.

### 3.6 `finalize`

Backs up EFS/NVRAM/modem partitions (needed to recover IMEI/baseband), writes
`final-report.md`, and bundles evidence + logs + screenshots into
`runs/<job>/evidence-bundle.zip`. Hand that zip and the report to the client.

## 4. Failure handling

Every failure maps to a code in `src/srtk/core/errors.py`. The phase that
failed is recorded in state; fix the cause and re-run with `--resume`. Common
ones:

| Exit | Meaning | Action |
|---|---|---|
| 10 | Tool/module missing | `bootstrap.ps1`; place module assets |
| 13 | Disk space | Free ~8 GiB on the run drive |
| 22 | adb RSA not accepted | Accept the prompt on the phone |
| 32 | KG Prenormal | Wait for ~7 days of uptime, re-run unlock |
| 33 | FRP / Reactivation Lock ON | Disable Find My Mobile on the phone |
| 40 | Binary downgrade blocked | Let `firmware` pick the matching version |
| 51 | Flash did not PASS / boot failed | Check Odin log; re-enter Download Mode, re-run |
| 53 | Baseband missing | **Stop.** Restore stock of the same binary |
| 61 | DEVICE verdict failed | Re-run `play_integrity` (auto PIF refresh) |
| 71 | Dry-run abort | Remove `--dry-run` |

## 5. Remote-session specifics

- **scrcpy** gives you the phone screen; use it for the Magisk app, developer
  options, and Odin-file selection.
- **Odin3** must run on the same PC the phone is plugged into (the download
  mode COM port is local). The operator clicks Start remotely.
- The **single physical client step** is the unlock Vol-UP press. Everything
  else is guided on-screen by `srtk runbook`.
- Reboots: prefer `adb reboot download` / `adb reboot recovery`; the physical
  fallback (Vol-Up + Vol-Down, plug in) is printed when adb can't drive it.

## 6. Security notes

- Logs redact secrets (`password=`, `token=`, `api_key=`, `auth`, `secret`).
- `runs/*/` holds device data (EFS backup, IMEI proxies, serials) — keep the
  workspace off shared storage; the evidence bundle is the handoff artifact.
- Do **not** relock the bootloader with modified firmware installed.
