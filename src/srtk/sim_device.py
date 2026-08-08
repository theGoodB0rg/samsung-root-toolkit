"""Simulated device + runner for ``--sim`` mode and the test suite.

Models a realistic SM-A325F session through the full pipeline: bootloader
locked → official unlock (props flip) → firmware download (real tar files are
written) → Magisk patch (a valid patched tar is produced) → flash → recovery
boot to arm Magisk → modules install → Play Integrity verdicts pass.

The simulator exercises the *same* phase code paths that run against a real
phone; only the transport responses are fabricated.
"""
from __future__ import annotations

import random
import tarfile
import time
from io import BytesIO
from pathlib import Path

from .core.transport import SubprocessResult

_SERIAL = "R58M20SIMSER0"

_ANDROID_HEADER = (
    b"ANDROID!" + b"\x00" * 40
).ljust(48, b"\x00")


def _android_boot_image(ramdisk: bool = False) -> bytes:
    hdr = bytearray(_ANDROID_HEADER)
    if ramdisk:
        hdr[16:20] = (4096).to_bytes(4, "little")  # ramdisk_size
    hdr[36:40] = (2048).to_bytes(4, "little")  # page_size
    return bytes(hdr)


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, BytesIO(data))


class SimDevice:
    """In-memory state machine for one simulated phone session."""

    def __init__(self) -> None:
        self.state = "device"  # device | recovery | download | offline | unauthorized
        self.com_port = "COM7"
        self.magisk_installed = False
        self.magisk_armed = False
        self.unlocked = False
        self.ap_pushed = False
        self.download_ticks = 0
        self.recovery_ticks = 0
        self.module_installs: set[str] = set()
        self.pushed_scripts: set[str] = set()
        self.pi_xml_dumped = False

    # ---- base props ---------------------------------------------------------
    def props(self) -> dict[str, str]:
        p = {
            "ro.product.model": "SM-A325F",
            "ro.product.device": "a32",
            "ro.product.manufacturer": "samsung",
            "ro.build.version.release": "13",
            "ro.build.version.sdk": "33",
            "ro.build.version.security_patch": "2025-01-01",
            "ro.build.display.id": "A325FXXSCDYB2",
            "ro.build.fingerprint": (
                "samsung/a32xx/a32:13/TP1A.220624.014/A325FXXSCDYB2:user/release-keys"
            ),
            "ro.build.version.oneui": "5.1",
            "ro.bootloader": "A325FXXSCDYB2",
            "ro.frp.pst": "/dev/block/persistent",
            "ro.boot.other.locked": "",
            "ro.oem_unlock_supported": "1",
            "ro.csc.sales_code": "EUX",
            "ro.csc.country_iso_code": "EU",
            "gsm.version.baseband": "A325FXXSCDYB2",
            "sys.boot_completed": "1",
        }
        if self.unlocked:
            p.update(
                {
                    "ro.boot.warranty_bit": "1",
                    "ro.secureboot.lockstate": "unlocked",
                    "ro.boot.flash.locked": "0",
                    "ro.boot.vbmeta.device_state": "unlocked",
                }
            )
        else:
            p.update(
                {
                    "ro.boot.warranty_bit": "0",
                    "ro.secureboot.lockstate": "locked",
                    "ro.boot.flash.locked": "1",
                    "ro.boot.vbmeta.device_state": "locked",
                }
            )
        return p

    def _apply_unlock(self) -> None:
        self.unlocked = True

    def tick(self) -> None:
        """Advance the state machine (called on each adb/USB probe)."""
        if self.state == "download":
            self.download_ticks += 1
            if self.download_ticks >= 2:
                # models unlock + wipe + setup completing between UI steps
                self._apply_unlock()
                self.magisk_armed = False
                self.state = "device"
        elif self.state == "recovery":
            self.recovery_ticks += 1
            if self.recovery_ticks >= 2:
                # recovery boot completed; Magisk now armed
                self.magisk_armed = True
                self.state = "device"


class SimRunner:
    """CommandRunner that routes tool calls to simulated behaviors."""

    def __init__(self, device: SimDevice | None = None):
        self.device = device or SimDevice()
        self.adb = SimAdb(self.device)
        self.samloader = SimSamloader()

    def run(
        self,
        args: list[str],
        timeout: float = 30.0,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> SubprocessResult:
        program = Path(str(args[0])).name.lower()
        rest = [str(a) for a in args[1:]]
        try:
            if program == "adb":
                self.device.tick()
                return self.adb.handle(rest)
            if "samloader" in program:
                return self.samloader.handle(rest, cwd)
            if program in ("pwsh", "powershell"):
                return self._pwsh(rest)
            if "scrcpy" in program:
                return _ok(args, "attached (interactive)")
            return _ok(args, "")
        except Exception as exc:  # noqa: BLE001 - surface as a failed command
            return SubprocessResult(
                args=[str(a) for a in args],
                stdout="",
                stderr=f"sim error: {exc}",
                returncode=1,
            )

    def _pwsh(self, args: list[str]) -> SubprocessResult:
        script = " ".join(args)
        lower = script.lower()
        if "win32_ppnentity" in lower and "samsubs" in lower:
            return _ok(args, "SAMSUNG Mobile USB Composite Device")
        if "pnputil /enum-drivers" in lower:
            return _ok(args, "Published Name: oem42.inf\nOriginal Name: ssusbdriver.inf")
        if "windowsbuiltinrole" in lower:
            return _ok(args, "True")
        if "com\\d+" in lower:
            if self.device.state == "download":
                return _ok(args, f"MATCH\t{self.device.com_port}")
            return _ok(args, "")
        if "getportnames" in lower:
            return _ok(args, self.device.com_port if self.device.state == "download" else "")
        return _ok(args, "")


class SimAdb:
    """Fake adb CLI. Handles the exact command shapes the toolkit issues."""

    def __init__(self, device: SimDevice):
        self.device = device

    def handle(self, args: list[str]) -> SubprocessResult:
        # strip leading -s <serial>
        if args and args[0] == "-s":
            args = args[2:]
        if not args:
            return _ok(args, "")
        cmd = args[0]
        if cmd == "devices":
            return self._devices(args)
        if cmd == "shell":
            return self._shell(args[1:])
        if cmd == "push":
            return self._push(args[1:])
        if cmd == "pull":
            return self._pull(args[1:])
        if cmd == "install":
            self.device.magisk_installed = True
            return _ok(args, "Success")
        if cmd == "reboot":
            return self._reboot(args[1:])
        return _ok(args, "")

    def _devices(self, args: list[str]) -> SubprocessResult:
        state = self.device.state
        head = "List of devices attached\n"
        if state == "download":
            return _ok(args, head)
        if state == "offline":
            return _ok(args, head + f"{_SERIAL}\toffline usb:1-1\n")
        if state == "unauthorized":
            return _ok(args, head + f"{_SERIAL}\tunauthorized usb:1-1\n")
        if state == "recovery":
            return _ok(args, head + f"{_SERIAL}\trecovery usb:1-1 product:a32 model:SM_A325F\n")
        return _ok(args, head + f"{_SERIAL}\tdevice usb:1-1 product:a32 model:SM_A325F\n")

    def _shell(self, args: list[str]) -> SubprocessResult:
        if not args:
            return _ok(args, "")
        if args[0] == "getprop":
            if len(args) == 1:
                return _ok(args, "".join(f"{k}: {v}\n" for k, v in self.device.props().items()))
            return _ok(args, self.device.props().get(args[1], "") + "\n")
        if args[0] == "ls":
            files: list[str] = []
            if self.device.ap_pushed and self.device.magisk_installed:
                files.append("magisk_patched_SIM.tar")
            if self.device.ap_pushed:
                files.append("srtk_ap.tar")
            return _ok(args, " ".join(files) + "\n")
        if args[0] == "rm":
            if "/sdcard/Download/srtk_ap.tar" in args:
                self.device.ap_pushed = False
            return _ok(args, "")
        if args[0] == "input":
            return _ok(args, "")
        if args[0] == "su":
            return self._su(args[1:])
        if args[0] == "monkey":
            return _ok(args, "Events injected: 1")
        if args[0] == "uiautomator":
            self.device.pi_xml_dumped = True
            return _ok(args, "UI hierchary dumped to: /sdcard/pi.xml")
        if args[0] == "cat":
            if "/sdcard/pi.xml" in args:
                return _ok(args, _VERDICT_XML)
            return _ok(args, "")
        if args[0] == "screencap":
            return SubprocessResult(
                args=["screencap"], stdout="\x89PNG sim", stderr="", returncode=0
            )
        if args[0] == "am":
            return _ok(args, "")
        return _ok(args, "")

    def _su(self, args: list[str]) -> SubprocessResult:
        joined = " ".join(args)
        if not self.device.magisk_armed and not self.device.unlocked:
            # pre-root: su is unavailable until after flash+recovery boot
            return SubprocessResult(
                args=["su"], stdout="", stderr="su: not found", returncode=1
            )
        if "magisk -v" in joined:
            if self.device.magisk_armed:
                return _ok(args, "30100\n")
            return SubprocessResult(args=args, stdout="", stderr="not yet", returncode=1)
        if "magisk --install-module" in joined:
            module = joined.rsplit("/", 1)[-1]
            self.device.module_installs.add(module)
            return _ok(args, f"- Installing {module}\nDone!")
        if "tricky_targets.sh" in joined or "pif_refresh.sh" in joined:
            return _ok(args, "")
        if "efs_backup.sh" in joined:
            return _ok(args, "efs.img\nmodemst1.img\nmodemst2.img\n")
        return _ok(args, "")

    def _push(self, args: list[str]) -> SubprocessResult:
        local, remote = args[-2], args[-1]
        if remote == "/sdcard/Download/srtk_ap.tar":
            self.device.ap_pushed = True
        if remote.startswith("/data/local/tmp/"):
            self.device.pushed_scripts.add(Path(remote).name)
        return _ok(args, f"{local}: 1 file pushed.")

    def _pull(self, args: list[str]) -> SubprocessResult:
        remote, local = args[-2], args[-1]
        local_path = Path(local)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if remote == "/sdcard/Download/magisk_patched_SIM.tar":
            _write_tar(
                local_path,
                {
                    "boot.img": _android_boot_image(ramdisk=False),
                    "recovery.img": _android_boot_image(ramdisk=False),
                    "magiskinit": b"# sim magiskinit",
                },
            )
            return _ok(args, f"{remote}: 1 file pulled.")
        if remote.startswith("/data/local/tmp/srtk_backup/"):
            local_path.write_bytes(random.randbytes(1024))
            return _ok(args, f"{remote}: 1 file pulled.")
        return SubprocessResult(
            args=args, stdout="", stderr=f"remote object '{remote}' does not exist", returncode=1
        )

    def _reboot(self, args: list[str]) -> SubprocessResult:
        mode = args[0] if args else "system"
        if mode == "download":
            self.device.state = "download"
            self.device.download_ticks = 0
        elif mode == "recovery":
            self.device.state = "recovery"
            self.device.recovery_ticks = 0
        else:
            self.device.state = "device"
        return _ok(args, "")


class SimSamloader:
    """Fake samloader: writes valid firmware tars into the cwd."""

    def handle(self, args: list[str], cwd: Path | None) -> SubprocessResult:
        if not args:
            return _ok(args, "")
        if args[0] == "--version":
            return _ok(args, "samloader 0.4.0 (simulation)")
        if args[0] == "check-update":
            return _ok(args, f"Version: A325FXXSCDYB2\n{args[1]}/{args[2]}")
        if args[0] == "download":
            out = Path(cwd) if cwd else Path(".")
            self._write_firmware(out)
            return _ok(args, "Firmware downloaded")
        if args[0] == "verify-md5":
            return _ok(args, "OK")
        return _ok(args, "")

    @staticmethod
    def _write_firmware(out: Path) -> None:
        for slot in ("BL", "CP", "CSC", "HOME_CSC"):
            _write_tar(out / f"{slot}_A325FXXSCDYB2.tar", {f"{slot.lower()}.img": b"\x00" * 512})
        ap = out / "AP_A325FXXSCDYB2_CL123456.tar"
        _write_tar(
            ap,
            {
                "boot.img": _android_boot_image(ramdisk=False),
                "recovery.img": _android_boot_image(ramdisk=False),
                "vbmeta.img": b"VBmeta simulated",
                "system.img": b"\x00" * 4096,
            },
        )


_VERDICT_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><hierarchy rotation="0">'
    '<node index="0" text="Play Integrity" resource-id="title"/>'
    '<node index="1" text="MEETS_BASIC_INTEGRITY ✓"/>'
    '<node index="2" text="MEETS_DEVICE_INTEGRITY ✓"/>'
    '<node index="3" text="MEETS_STRONG_INTEGRITY ✗"/>'
    "</hierarchy>"
)


def _ok(args: list[str], stdout: str) -> SubprocessResult:
    return SubprocessResult(
        args=[str(a) for a in args], stdout=stdout, stderr="", returncode=0
    )
