# SRTK — Samsung Remote Root Toolkit

Remote-operates an **SM-A325F (Galaxy A32 4G, EU CSC)** from locked stock to
bootloader-unlocked + Magisk root + Play Integrity BASIC/DEVICE passing, with a
human client doing the handful of steps adb cannot do and an operator driving
everything else over a remote session.

**Disclaimers.** This tool exists for the owner of the device. Unlocking a
Samsung bootloader is **destructive and irreversible**: it factory-resets the
phone and **permanently trips the Knox fuse** (Samsung Wallet/Pass/Secure
Folder stop working, warranty void, certain banking apps refuse to run). OTA
updates must never be taken on the rooted device, and the bootloader must never
be relocked while modified firmware is installed. The operator must obtain
recorded, signed consent before phase `unlock`.

## What it does

| Phase | What happens | Client interaction needed |
|---|---|---|
| `preflight` | Host checks: Python ≥3.11, admin, disk, adb/samloader/Odin/scrcpy, Samsung driver, device state | Plug the phone in |
| `device_info` | Reads model, Android/One UI, bootloader binary, CSC, OEM-lock & FRP flags | Accept the USB-debugging RSA prompt |
| `unlock` | Official bootloader unlock via Download Mode | **One physical Vol-UP press** + setup-wizard + re-enable debugging |
| `firmware` | Downloads latest matching firmware from Samsung FUS via samloader-rs, verifies tar.md5, SHA-256 manifest, binary compatibility | none |
| `magisk_patch` | Pushes AP tar, patches it on-device with the Magisk app, pulls + hashes the result | Keep the phone awake |
| `flash` | Operator flashes BL + patched AP + CP + CSC (full wipe) through Odin3; arms Magisk via one recovery boot; verifies baseband | Don't unplug during flash |
| `play_integrity` | Installs Zygisk + PlayIntegrityFix + Tricky Store, sets spoof targets, refreshes PIF, verifies BASIC + DEVICE verdicts | none |
| `finalize` | Backs up EFS/NVRAM/modem, writes `final-report.md`, bundles all evidence | none |

## Quick start

```powershell
# 1. install host tooling (elevated)
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1

# 2. sanity-check the connection
powershell -ExecutionPolicy Bypass -File scripts\driver_check.ps1

# 3. try the whole pipeline against a simulated phone (no device needed)
python -m srtk --root . --sim --yes run all

# 4. real run, phase by phase (resumable)
python -m srtk --root . init job-001
python -m srtk --root . --job job-001 run preflight
python -m srtk --root . --job job-001 run device_info
# ... `srtk runbook` shows the operator/client steps for each phase
```

See [docs/HOW_TO_USE.md](docs/HOW_TO_USE.md) for the full operator guide and
[docs/CLIENT_RUNBOOK.md](docs/CLIENT_RUNBOOK.md) for the client-facing sheet.

## CLI

```
srtk init <job-id>       create a job workspace + state
srtk run [phases]        run one or more phases (default: all)
srtk status              per-phase status for a job (resumable)
srtk runbook [phase]     operator/client runbook
srtk bootstrap           host-tooling setup instructions
srtk verify-only         read-only preflight + device_info

Global:  --root <dir>   toolkit root (default: repo dir)
         --job <id>     workspace under runs/ (default: job-default)
         --sim          simulate the device (no real USB)
         --yes          auto-confirm prompts (scripted mode)
Run:     --dry-run      abort before mutating actions
         --resume       skip phases already passed
         --plan         print the plan and exit
```

Exit codes map to the [error taxonomy](src/srtk/core/errors.py): e.g. `32` =
KG Prenormal, `61` = DEVICE verdict failed after 3 PIF refreshes, `90` =
internal error, `71` = dry-run abort.

## Layout

```
src/srtk/
  cli.py, context.py            entry point + run context / UI / config
  phases/                       p01..p08 pipeline phases + registry
  core/                         errors, state, transport, hashing, versioning
  tools/                        host introspection, samloader, boot.img, tar.md5
  sim_device.py                 simulated device for --sim and tests
  runbook_gen.py                operator/client runbook renderer
scripts/                        bootstrap.ps1, driver_check.ps1
tests/                          31 tests incl. a full simulated pipeline
```

`runs/<job>/` holds per-job `state.json` (resume point + consent), artifacts,
evidence screenshots, logs, and the final evidence bundle.

## Design notes

- **Transport hierarchy**: adb-first (`adb reboot download` / `reboot recovery`);
  a physical button runbook is the fallback and the toolkit *waits* for the
  expected state instead of assuming.
- **Human-in-the-loop**: `UI.instruct` blocks until the operator/client confirms
  each step; destructive actions require typing the job id. `--sim`/`--yes` skip
  the prompts.
- **Pinned hashes**: every cross-phase file (firmware parts, patched AP, module
  zips) is SHA-256 pinned; tar.md5 is verified; patched AP and baseband are
  re-checked after flash.
- **Gate checks**: fails fast on FRP (Reactivation Lock), KG Prenormal, and
  carrier-stripped OEM unlock; refuses a downgrade to a lower binary.
- **Knox-aware**: `warranty_bit`/`vbmeta`/`flash.locked` are read before and
  after; consent + evidence screenshots are captured into the bundle.

## Requirements

- Windows 10/11, Python 3.11+, an *elevated* PowerShell for `bootstrap.ps1`.
- Tools installed by bootstrap: platform-tools (adb), samloader-rs, scrcpy,
  Odin3, Samsung USB driver. Module assets (Magisk.apk, PlayIntegrityFork.zip,
  TrickyStore.zip, IntegrityChecker.apk) go in `src/srtk/modules/`.
- ~8 GiB free disk, a data-capable USB cable, and a Samsung ID that has had
  network uptime (KG-normal) — see the unlock caveats in the docs.
