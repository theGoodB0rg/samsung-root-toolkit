"""Windows host introspection: admin, disk, Samsung driver/device presence."""
from __future__ import annotations

import sys

from ..core.transport import CommandRunner

_PNP_SAMSUNG_PS = r"""
Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'SAMSUNG|Samsung' } |
    ForEach-Object { $_.Name }
"""

_PNP_DRIVER_PS = r"""
pnputil /enum-drivers | Select-String -Pattern 'SAMSUNG|ssud|ssadadb' -CaseSensitive:$false |
    ForEach-Object { $_.Line }
"""

_ADMIN_PS = r"""
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
(New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
"""


def is_admin(runner: CommandRunner) -> bool:
    from ..core.transport import _find_pwsh

    result = runner.run([_find_pwsh(), "-NoProfile", "-Command", _ADMIN_PS], timeout=30)
    return result.stdout.strip().lower().startswith("true")


def free_disk_gb(path: str) -> float:
    import ctypes

    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        path, None, ctypes.byref(total), ctypes.byref(free)
    )
    return free.value / (1024 ** 3) if ok else 0.0


def python_version_ok(minimal: tuple[int, int] = (3, 11)) -> bool:
    return sys.version_info[:2] >= minimal


def samsung_driver_packages(runner: CommandRunner) -> list[str]:
    from ..core.transport import _find_pwsh

    result = runner.run([_find_pwsh(), "-NoProfile", "-Command", _PNP_DRIVER_PS], timeout=60)
    return [l.strip() for l in result.stdout.splitlines() if l.strip()]


def samsung_entities(runner: CommandRunner) -> list[str]:
    from ..core.transport import _find_pwsh

    result = runner.run([_find_pwsh(), "-NoProfile", "-Command", _PNP_SAMSUNG_PS], timeout=30)
    return [l.strip() for l in result.stdout.splitlines() if l.strip()]
