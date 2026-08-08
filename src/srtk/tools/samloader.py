"""Samsung firmware downloader wrapper.

Isolates the exact ``samloader-rs`` CLI contract so the rest of the toolkit
never depends on its argument shapes. Run ``srtk preflight`` to learn the
installed interface (the wrapper logs the ``--help`` output) and confirm the
binary works on the client's machine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import ErrorCode, SrtkError
from ..core.transport import CommandRunner, SubprocessResult


@dataclass
class FirmwareResult:
    ok: bool
    files: list[Path] = field(default_factory=list)
    version: str = ""
    output: str = ""


class Samloader:
    """Wrapper around ``samloader`` (topjohnwu/samloader-rs)."""

    def __init__(self, runner: CommandRunner, binary: str | Path = "samloader"):
        self.runner = runner
        self.binary = str(binary)

    def _run(self, args: list[str], timeout: float = 900, cwd: Path | None = None) -> SubprocessResult:
        return self.runner.run([self.binary, *args], timeout=timeout, cwd=cwd)

    def version(self) -> str:
        result = self._run(["--version"], timeout=15)
        return result.stdout.strip() or result.stderr.strip()

    def check_update(self, model: str, region: str, timeout: float = 60) -> SubprocessResult:
        return self._run(["check-update", model, region], timeout=timeout)

    def download(
        self,
        model: str,
        region: str,
        out_dir: Path,
        timeout: float = 1800,
    ) -> FirmwareResult:
        """Download the latest firmware for model/region into out_dir."""
        out_dir.mkdir(parents=True, exist_ok=True)
        # samloader-rs downloads into CWD by default; pass -o if supported.
        result = self._run(["download", model, region], timeout=timeout, cwd=out_dir)
        return FirmwareResult(
            ok=result.ok,
            files=sorted(out_dir.rglob("*.tar*")),
            output=result.stdout,
        )

    def verify_md5(self, tar: Path, timeout: float = 300) -> bool:
        result = self._run(["verify-md5", str(tar)], timeout=timeout)
        return result.ok


def require_samloader(samloader: Samloader) -> str:
    try:
        v = samloader.version()
        if v:
            return v
    except Exception:
        pass
    raise SrtkError(
        ErrorCode.TOOL_NOT_FOUND,
        context="samloader",
        details="`samloader --version` did not respond; run bootstrap.ps1",
    )
