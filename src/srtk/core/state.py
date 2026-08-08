"""Per-run persistent state.

One ``state.json`` lives at the root of each ``runs/<job-id>/`` workspace. It
tracks phase completion (so the pipeline is resumable), consent gates, device
reports, and artifact pointers. All values are plain JSON so the file is
human-reviewable and diffable.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PHASE_PASSED = "passed"
PHASE_FAILED = "failed"
PHASE_RUNNING = "running"
PHASE_PENDING = "pending"
PHASE_SKIPPED = "skipped"

_DEFAULTS: dict[str, Any] = {
    "job_id": None,
    "model": "SM-A325F",
    "created_utc": None,
    "phases": {},
    "device_report": None,
    "firmware": None,
    "magisk_patch": None,
    "flash": None,
    "play_integrity": None,
    "consent": None,
    "evidence": [],
    "notes": [],
}


@dataclass
class PhaseRecord:
    name: str
    status: str = PHASE_PENDING
    started_utc: str | None = None
    finished_utc: str | None = None
    artifacts: list[str] = field(default_factory=list)
    notes: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunState:
    """JSON-backed state store bound to one job workspace."""

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.data: dict[str, Any] = {k: _default(v) for k, v in _DEFAULTS.items()}
        self._dirty = False
        if state_path.exists():
            self.load()

    def load(self) -> None:
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.data = {**_DEFAULTS, **{k: v for k, v in raw.items()}}
        self._dirty = False

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.state_path)
        self._dirty = False

    def flush(self) -> None:
        if self._dirty:
            self.save()

    # ---- generic keys ------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self._dirty = True

    def note(self, text: str) -> None:
        self.data.setdefault("notes", []).append(f"[{_now()}] {text}")
        self._dirty = True

    # ---- phase records -----------------------------------------------------
    def phase_status(self, name: str) -> str:
        record = self.data["phases"].get(name)
        return record.get("status", PHASE_PENDING) if record else PHASE_PENDING

    def is_passed(self, name: str) -> bool:
        return self.phase_status(name) == PHASE_PASSED

    def start_phase(self, name: str) -> None:
        self.data["phases"].setdefault(name, asdict(PhaseRecord(name=name)))
        self.data["phases"][name].update(status=PHASE_RUNNING, started_utc=_now())
        self._dirty = True

    def finish_phase(
        self,
        name: str,
        status: str,
        artifacts: list[str] | None = None,
        notes: str = "",
    ) -> None:
        self.data["phases"].setdefault(name, asdict(PhaseRecord(name=name)))
        self.data["phases"][name].update(
            status=status, finished_utc=_now(), notes=notes
        )
        if artifacts:
            self.data["phases"][name]["artifacts"] = [
                str(a) for a in artifacts
            ]
        self._dirty = True

    def passed_phases(self) -> list[str]:
        return [name for name, rec in self.data["phases"].items()
                if rec.get("status") == PHASE_PASSED]


def _default(v: Any) -> Any:
    return v.copy() if isinstance(v, dict) else v
