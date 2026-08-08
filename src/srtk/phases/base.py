"""Phase base class: state transitions, error mapping, dry-run guard."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..core.errors import SrtkError, wrap_internal
from ..core.logging import RunLogger
from ..core.state import PHASE_FAILED, PHASE_PASSED, PHASE_SKIPPED
from ..core.transport import SubprocessResult

if TYPE_CHECKING:  # pragma: no cover
    from ..context import RunContext


class Phase(ABC):
    name: str = ""
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()

    def __init__(self, ctx: "RunContext"):
        self.ctx = ctx
        self._log: RunLogger | None = None

    @property
    def log(self) -> RunLogger:
        if self._log is None:
            self._log = self.ctx.logger(self.name)
        return self._log

    def run(self) -> None:
        self.ctx.state.start_phase(self.name)
        try:
            artifacts = list(self.execute() or [])
        except SrtkError as exc:
            self.ctx.state.finish_phase(self.name, PHASE_FAILED, notes=str(exc))
            self.ctx.state.save()
            self.log.error(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - we wrap everything
            wrapped = wrap_internal(exc, context=self.name)
            self.ctx.state.finish_phase(self.name, PHASE_FAILED, notes=str(wrapped))
            self.ctx.state.save()
            self.log.error(str(wrapped))
            raise wrapped from exc
        self.ctx.state.finish_phase(self.name, PHASE_PASSED, artifacts=artifacts)
        self.ctx.state.save()
        self.log.info(f"phase passed (artifacts: {', '.join(artifacts) or 'none'})")

    def skip(self, reason: str) -> None:
        self.ctx.state.finish_phase(self.name, PHASE_SKIPPED, notes=reason)
        self.ctx.state.save()
        self.log.warn(f"phase skipped: {reason}")

    @abstractmethod
    def execute(self) -> list[str]:
        """Run the phase and return artifact paths produced."""

    # -- helpers -------------------------------------------------------------
    def dump(self, result: SubprocessResult, label: str = "") -> None:
        self.log.raw(f"{label}\n$ {' '.join(result.args)}\nrc={result.returncode}\n{result.stdout}")
        if result.stderr:
            self.log.raw(f"[stderr]\n{result.stderr}")

    def dry_abort(self, what: str) -> None:
        from ..core.errors import ErrorCode

        raise SrtkError(
            ErrorCode.DRY_RUN_ABORT,
            context=self.name,
            details=what,
        )
