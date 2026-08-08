"""Run context, configuration, and the human-in-the-loop UI abstraction."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .core.errors import ErrorCode, SrtkError
from .core.logging import RunLogger, fresh_line
from .core.state import RunState
from .core.transport import (
    Adb,
    CommandRunner,
    DeviceDetector,
    HostRunner,
    Scrcpy,
)


@dataclass
class Config:
    root: Path
    model: str = "SM-A325F"
    assume_yes: bool = False
    timeouts: dict = field(
        default_factory=lambda: {
            "download_mode": 180,
            "flash": 900,
            "patch": 300,
            "boot": 240,
        }
    )

    @property
    def tools_dir(self) -> Path:
        return self.root / "tools" / "bin"

    @property
    def modules_dir(self) -> Path:
        return self.root / "src" / "srtk" / "modules"

    @property
    def adb_path(self) -> Path:
        return self.tools_dir / "adb.exe"

    @property
    def samloader_path(self) -> Path:
        return self.tools_dir / "samloader.exe"

    @property
    def odin_path(self) -> Path:
        return self.tools_dir / "Odin3.exe"

    @property
    def scrcpy_path(self) -> Path:
        return self.tools_dir / "scrcpy.exe"

    def require_tool(self, key: str) -> Path:
        path = getattr(self, key, None)
        if path is None:
            raise SrtkError(
                ErrorCode.TOOL_NOT_FOUND,
                context=key,
                details="no path configured; run bootstrap.ps1 first",
            )
        if not Path(path).exists():
            raise SrtkError(
                ErrorCode.TOOL_NOT_FOUND,
                context=key,
                details=f"expected at {path}",
            )
        return Path(path)


@dataclass
class RunContext:
    """Everything a phase needs, wired by the CLI for real or sim runs."""

    job_id: str
    workspace: Path
    state: RunState
    runner: CommandRunner
    adb: Adb
    detector: DeviceDetector
    scrcpy: Scrcpy
    config: Config
    sim: bool = False
    dry_run: bool = False
    log: RunLogger | None = None
    ui: "UI" = field(init=False)

    def __post_init__(self) -> None:
        self.ui = UI(self)

    @property
    def artifacts_dir(self) -> Path:
        return self.workspace / "artifacts"

    @property
    def evidence_dir(self) -> Path:
        return self.workspace / "evidence"

    @property
    def logs_dir(self) -> Path:
        return self.workspace / "logs"

    @property
    def state_path(self) -> Path:
        return self.workspace / "state.json"

    def logger(self, phase: str) -> RunLogger:
        fresh_line(self.logs_dir, phase)
        return RunLogger(self.logs_dir, phase, verbose=os.environ.get("SRTK_VERBOSE") == "1")

    def artifact(self, name: str) -> Path:
        return self.artifacts_dir / name

    def ensure_dirs(self) -> None:
        for d in (self.artifacts_dir, self.evidence_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


class UI:
    """Human-in-the-loop interactions.

    In ``sim`` mode every interaction auto-confirms so the pipeline can be
    exercised end-to-end. In real mode the operator is prompted on the remote
    session; ``--yes`` suppresses prompts for scripted operation.
    """

    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self._log: RunLogger | None = None

    def _logger(self) -> RunLogger:
        if self._log is None:
            self._log = RunLogger(self.ctx.logs_dir, "ui")
        return self._log

    def instruct(self, title: str, body: str, evidence: str | None = None) -> None:
        """Show instructions and block until the operator confirms (or sim)."""
        log = self._logger()
        log.info(f"[UI] {title}\n{body}")
        if evidence:
            self.capture_evidence(evidence)
        if self.ctx.sim:
            log.info("[UI] sim: auto-confirmed")
            return
        if self.ctx.config.assume_yes:
            log.info("[UI] assume_yes: continuing")
            return
        input(f"\n=== {title} ===\n{body}\n\nPress Enter when done... ")

    def confirm_destructive(self, what: str) -> None:
        if self.ctx.config.assume_yes and not self.ctx.sim:
            return
        if self.ctx.sim:
            self._logger().info(f"[UI] sim: destructive action accepted: {what}")
            return
        answer = input(
            f"\n!!! DESTRUCTIVE ACTION !!!\n{what}\n\n"
            f"Type the client's job id '{self.ctx.job_id}' to confirm: "
        ).strip()
        if answer != self.ctx.job_id:
            raise SrtkError(
                ErrorCode.CONSENT_MISSING,
                context=what,
                details="confirmation string did not match the job id",
            )

    def capture_evidence(self, name: str) -> Path:
        """Save a device screenshot into the evidence dir (best effort)."""
        out = self.ctx.evidence_dir / f"{name}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            res = self.ctx.adb.shell_exec("screencap -p", timeout=30)
            if res.ok and res.stdout:
                out.write_bytes(res.stdout.encode("utf-8", "surrogateescape"))
            else:
                out.write_text("screenshot unavailable (device not on adb)\n")
        except Exception:
            out.write_text("screenshot unavailable (device not on adb)\n")
        return out
