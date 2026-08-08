"""Phase registry and plan computation (resume + prerequisites)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.errors import ErrorCode, SrtkError

from .p01_preflight import P01Preflight
from .p02_device_info import P02DeviceInfo
from .p03_unlock import P03Unlock
from .p04_firmware import P04Firmware
from .p05_magisk_patch import P05MagiskPatch
from .p06_flash import P06Flash
from .p07_modules_integrity import P07ModulesIntegrity
from .p08_finalize import P08Finalize

if TYPE_CHECKING:  # pragma: no cover
    from ..context import RunContext
    from ..core.state import RunState

PHASES: dict[str, type] = {
    P01Preflight.name: P01Preflight,
    P02DeviceInfo.name: P02DeviceInfo,
    P03Unlock.name: P03Unlock,
    P04Firmware.name: P04Firmware,
    P05MagiskPatch.name: P05MagiskPatch,
    P06Flash.name: P06Flash,
    P07ModulesIntegrity.name: P07ModulesIntegrity,
    P08Finalize.name: P08Finalize,
}

ORDER = [
    P01Preflight.name,
    P02DeviceInfo.name,
    P03Unlock.name,
    P04Firmware.name,
    P05MagiskPatch.name,
    P06Flash.name,
    P07ModulesIntegrity.name,
    P08Finalize.name,
]

DEPENDENCIES: dict[str, tuple[str, ...]] = {
    P01Preflight.name: (),
    P02DeviceInfo.name: ("preflight",),
    P03Unlock.name: ("preflight", "device_info"),
    P04Firmware.name: ("preflight", "device_info"),
    P05MagiskPatch.name: ("preflight", "firmware"),
    P06Flash.name: ("preflight", "unlock", "firmware", "magisk_patch"),
    P07ModulesIntegrity.name: ("preflight", "flash"),
    P08Finalize.name: ("preflight", "device_info", "flash", "play_integrity"),
}


def available_phases() -> list[str]:
    return list(ORDER)


def plan_for(
    state: "RunState",
    targets: list[str],
    resume: bool = False,
) -> list[tuple[str, str]]:
    """Return [(phase, action)] where action in {run, skip, block}.

    - ``resume``: phases already passed are skipped.
    - prerequisites that are not passed are added to the plan (unless blocked).
    """
    wanted = set(targets)
    plan: list[tuple[str, str]] = []
    added: set[str] = set()
    blocked: set[str] = set()

    def add(name: str, depth: int = 0) -> None:
        if depth > len(ORDER) + 2:  # cycle guard
            blocked.add(name)
            return
        if name in added:
            return
        cls = PHASES[name]
        for prereq in getattr(cls, "requires", ()):
            add(prereq, depth + 1)
        added.add(name)
        if blocked.intersection(name, *getattr(PHASES[name], "requires", ())):
            plan.append((name, "block"))
        elif resume and state.is_passed(name):
            plan.append((name, "skip"))
        else:
            plan.append((name, "run"))

    for name in ORDER:
        if name in wanted:
            add(name)
    return plan


def validate_targets(targets: list[str]) -> None:
    unknown = [t for t in targets if t not in PHASES]
    if unknown:
        raise SrtkError(
            ErrorCode.PHASE_PREREQ_FAIL,
            context="plan",
            details=f"unknown phase(s): {', '.join(unknown)}; known: {', '.join(ORDER)}",
        )
