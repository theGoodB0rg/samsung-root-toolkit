"""Device transport.

Implements the transport hierarchy from the design:

1. **ADB** — the primary transport. ``adb reboot download`` / ``adb reboot
   recovery`` eliminate physical button combos whenever the device is booted
   with USB debugging authorized.
2. **Physical runbook** — fallback for bootloop / soft-brick / pre-setup
   states, where adb cannot drive the device. The toolkit emits exact button
   instructions and *waits* for the device to appear in the expected state.

Everything here is injected with a :class:`CommandRunner` so tests and
``--sim`` mode can substitute a simulated device without touching real USB.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from .errors import ErrorCode, SrtkError


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

@dataclass
class SubprocessResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        timeout: float = 30.0,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> SubprocessResult: ...


class HostRunner:
    """Real subprocess runner for the client's Windows host."""

    def __init__(self, create_no_window: bool = True):
        self._flags = subprocess.CREATE_NO_WINDOW if create_no_window else 0

    def run(
        self,
        args: list[str],
        timeout: float = 30.0,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> SubprocessResult:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                [str(a) for a in args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
                env=env,
                creationflags=self._flags,
            )
            return SubprocessResult(
                args=[str(a) for a in args],
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                returncode=proc.returncode,
                duration_s=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            return SubprocessResult(
                args=[str(a) for a in args],
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=f"timed out after {timeout}s",
                returncode=-1,
                duration_s=time.monotonic() - started,
            )


# ---------------------------------------------------------------------------
# Device states
# ---------------------------------------------------------------------------

class DeviceState(str, Enum):
    MISSING = "missing"                # nothing seen on adb or USB
    OFFLINE = "offline"                # adb shows offline
    UNAUTHORIZED = "unauthorized"      # adb needs the host RSA key accepted
    DEVICE = "device"                  # normal boot, booted
    RECOVERY = "recovery"              # adb in recovery
    DOWNLOAD = "download"              # Odin/LOKE download mode (COM port)
    BOOTLOADER = "bootloader"          # fastboot-style enumeration


@dataclass
class AdbDevice:
    serial: str
    state: str
    extra: dict[str, str] = field(default_factory=dict)


class Adb:
    """Thin wrapper around the platform-tools ``adb`` binary."""

    def __init__(
        self,
        runner: CommandRunner,
        adb_path: str | Path = "adb",
        serial: str | None = None,
        timeout: float = 20.0,
    ):
        self.runner = runner
        self.adb_path = Path(adb_path) if str(adb_path).lower().endswith((".exe", ".bin")) else adb_path
        self.serial = serial
        self.timeout = timeout

    def _base(self) -> list[str]:
        base = [str(self.adb_path)]
        if self.serial:
            base += ["-s", self.serial]
        return base

    def _run(self, *args: str, timeout: float | None = None) -> SubprocessResult:
        return self.runner.run(
            self._base() + list(args),
            timeout=timeout or self.timeout,
        )

    def devices(self) -> list[AdbDevice]:
        result = self._run("devices", "-l", timeout=15)
        devices: list[AdbDevice] = []
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            extra = {}
            for chunk in parts[2:]:
                if ":" in chunk:
                    k, _, v = chunk.partition(":")
                    extra[k] = v
            devices.append(AdbDevice(serial, state, extra))
        return devices

    def has_device(self, state: str = "device") -> AdbDevice | None:
        for d in self.devices():
            if d.state == state:
                return d
        return None

    def shell(self, *args: str, timeout: float | None = None) -> SubprocessResult:
        return self._run("shell", *args, timeout=timeout)

    def getprop(self, key: str) -> str | None:
        result = self.shell("getprop", key, timeout=15)
        value = result.stdout.strip()
        return value or None

    def reboot(self, mode: str = "system", timeout: float = 20) -> SubprocessResult:
        return self._run("reboot", mode, timeout=timeout)

    def wait_for_device(self, timeout_s: float = 180, poll_s: float = 3) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.has_device("device"):
                return
            time.sleep(poll_s)
        raise SrtkError(
            ErrorCode.DEVICE_OFFLINE,
            context="wait_for_device",
            details=f"no device state after {timeout_s:.0f}s",
        )

    def pull(self, remote: str, local: Path, timeout: float = 600) -> SubprocessResult:
        local.parent.mkdir(parents=True, exist_ok=True)
        return self._run("pull", remote, str(local), timeout=timeout)

    def push(self, local: Path, remote: str, timeout: float = 600) -> SubprocessResult:
        return self._run("push", str(local), remote, timeout=timeout)

    def install(self, apk: Path, timeout: float = 600) -> SubprocessResult:
        return self._run("install", "-r", str(apk), timeout=timeout)

    def shell_exec(self, command: str, timeout: float = 60) -> SubprocessResult:
        """Run an arbitrary shell command on the device (no quoting surprises)."""
        return self.shell(command, timeout=timeout)


# ---------------------------------------------------------------------------
# Windows download-mode detection (COM port probe)
# ---------------------------------------------------------------------------

_SAMSUNG_COM_PS = r"""
$rows = Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'COM\d+' } |
    Select-Object Name
$rows | ForEach-Object {
    $name = $_.Name
    $match = [regex]::Match($name, 'COM\d+')
    if ($match.Success) {
        if ($name -match 'Samsung|Download|Odin|CDC|Android|Mobile') {
            "MATCH`t" + $match.Value
        } else {
            "OTHER`t" + $match.Value
        }
    }
}
"""

_ALL_COM_PS = r"""
[System.IO.Ports.SerialPort]::GetPortNames()
"""


def probe_com_ports(runner: CommandRunner) -> tuple[list[str], list[str]]:
    """Return (samsung_like_com_ports, all_com_ports).

    ``samsung_like`` is the set the flasher should try first; ``all`` is the
    full COM inventory for diagnostics.
    """
    pwsh = _find_pwsh()
    result = runner.run([pwsh, "-NoProfile", "-Command", _SAMSUNG_COM_PS], timeout=30)
    samsung: list[str] = []
    others: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        kind, _, port = line.partition("\t")
        (samsung if kind == "MATCH" else others).append(port)
    return dedupe(samsung), dedupe(others)


def list_all_com_ports(runner: CommandRunner) -> list[str]:
    pwsh = _find_pwsh()
    result = runner.run([pwsh, "-NoProfile", "-Command", _ALL_COM_PS], timeout=30)
    return dedupe(l for l in result.stdout.splitlines() if l.strip())


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _find_pwsh() -> str:
    # Prefer pwsh (PowerShell 7), fall back to Windows PowerShell 5.1.
    for candidate in ("pwsh", "powershell"):
        if _which(candidate):
            return candidate
    return "pwsh"


def _which(name: str) -> bool:
    import shutil

    return shutil.which(name) is not None


# ---------------------------------------------------------------------------
# Combined device state detection
# ---------------------------------------------------------------------------

class DeviceDetector:
    """Determines what state the phone is in, combining adb + USB probes."""

    def __init__(self, adb: Adb, runner: CommandRunner):
        self.adb = adb
        self.runner = runner
        self._com_cache: tuple[list[str], list[str]] | None = None

    def com_ports(self, refresh: bool = False) -> tuple[list[str], list[str]]:
        if refresh or self._com_cache is None:
            self._com_cache = probe_com_ports(self.runner)
        return self._com_cache

    def detect(self) -> tuple[DeviceState, str]:
        """Return (state, detail). Never raises; prefers safe defaults."""
        try:
            devices = self.adb.devices()
        except Exception:
            devices = []
        if not devices:
            samsung, _ = self.com_ports(refresh=True)
            if samsung:
                return DeviceState.DOWNLOAD, f"no adb device; Samsung COM ports: {','.join(samsung)}"
            return DeviceState.MISSING, "no adb device and no Samsung COM port"

        entry = devices[0]
        if entry.state == "device":
            try:
                booted = self.adb.getprop("sys.boot_completed") == "1"
            except Exception:
                booted = False
            if booted:
                return DeviceState.DEVICE, f"adb {entry.serial}"
            return DeviceState.RECOVERY, f"adb {entry.serial} not booted (recovery/pre-init)"
        if entry.state == "recovery":
            return DeviceState.RECOVERY, f"adb {entry.serial} recovery"
        if entry.state == "offline":
            return DeviceState.OFFLINE, f"adb {entry.serial} offline"
        if entry.state == "unauthorized":
            return DeviceState.UNAUTHORIZED, f"adb {entry.serial} unauthorized"
        if entry.state == "bootloader":
            return DeviceState.BOOTLOADER, f"adb {entry.serial} bootloader"
        return DeviceState.OFFLINE, f"unhandled adb state: {entry.state}"

    # -- transport helpers ----------------------------------------------------
    def reboot_to_download(self, timeout_s: float = 180, poll_s: float = 3) -> str:
        """adb-driven download mode; raises ADB_UNAVAILABLE if adb can't drive it."""
        state, detail = self.detect()
        if state not in (DeviceState.DEVICE, DeviceState.RECOVERY):
            raise SrtkError(
                ErrorCode.ADB_UNAVAILABLE,
                context="reboot_to_download",
                details=f"current state {state.value}: {detail}",
            )
        result = self.adb.reboot("download")
        if not result.ok:
            raise SrtkError(
                ErrorCode.ADB_UNAVAILABLE,
                context="reboot_to_download",
                details=f"adb reboot download rc={result.returncode}: {result.stderr.strip()}",
            )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state, detail = self.detect()
            if state is DeviceState.DOWNLOAD:
                return detail
            time.sleep(poll_s)
        raise SrtkError(
            ErrorCode.DOWNLOAD_MODE_TIMEOUT,
            context="reboot_to_download",
            details=f"adb reboot issued but no Download Mode after {timeout_s:.0f}s",
        )

    def wait_for_download(self, timeout_s: float = 300, poll_s: float = 4) -> str:
        """Wait for Download Mode regardless of how the device got there."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state, detail = self.detect()
            if state is DeviceState.DOWNLOAD:
                return detail
            time.sleep(poll_s)
        raise SrtkError(
            ErrorCode.DOWNLOAD_MODE_TIMEOUT,
            context="wait_for_download",
            details=f"no Download Mode after {timeout_s:.0f}s (last state {state.value})",
        )

    def reboot_to_recovery(self, timeout_s: float = 120, poll_s: float = 3) -> str:
        state, detail = self.detect()
        if state not in (DeviceState.DEVICE, DeviceState.RECOVERY):
            raise SrtkError(
                ErrorCode.ADB_UNAVAILABLE,
                context="reboot_to_recovery",
                details=f"current state {state.value}: {detail}",
            )
        self.adb.reboot("recovery")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state, detail = self.detect()
            if state is DeviceState.RECOVERY:
                return detail
            if state is DeviceState.DEVICE:
                return detail  # already booted; caller decides
            time.sleep(poll_s)
        raise SrtkError(
            ErrorCode.DEVICE_OFFLINE,
            context="reboot_to_recovery",
            details=f"device did not reappear in {timeout_s:.0f}s",
        )


# ---------------------------------------------------------------------------
# scrcpy launcher
# ---------------------------------------------------------------------------

class Scrcpy:
    """Launches scrcpy (screen mirror/control) against the connected device.

    scrcpy is an interactive window the operator drives; the toolkit only
    needs to launch it with the right adb + serial arguments.
    """

    def __init__(self, runner: CommandRunner, scrcpy_path: str | Path = "scrcpy"):
        self.runner = runner
        self.scrcpy_path = scrcpy_path

    def launch(
        self,
        serial: str | None = None,
        extra: list[str] | None = None,
        wait_ms: float = 8000,
    ) -> SubprocessResult:
        args = [str(self.scrcpy_path)]
        if serial:
            args += ["-s", serial]
        args += extra or []
        # Non-blocking: start scrcpy detached; it is interactive.
        result = self.runner.run(args, timeout=wait_ms)
        if result.returncode == -1:
            # timeout is expected because scrcpy stays attached; treat as success
            result = SubprocessResult(args, "", "attached (interactive)", 0, result.duration_s)
        return result
