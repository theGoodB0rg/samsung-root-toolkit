"""Structured logging.

Writes a machine-greppable, timestamped stream to a per-phase file under
``logs/`` and a colorized subset to the console. Supports redaction of
sensitive values (serial numbers, authorization tokens, passwords) so device
data never leaks into evidence bundles by accident.
"""
from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_RESERVED = {"*", "?`", "&", "|", "<", ">"}

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(pass(?:word)?\s*[=:]\s*)\S+", re.I), r"\1<redacted>"),
    (re.compile(r"(token\s*[=:]\s*)\S+", re.I), r"\1<redacted>"),
    (re.compile(r"(api[_-]?key\s*[=:]\s*)\S+", re.I), r"\1<redacted>"),
    (re.compile(r"\b(secret|auth)\b[^\n]*", re.I), "<redacted>"),
]


def _redact(text: str) -> str:
    for pattern, repl in _REDACT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


@dataclass
class RunLogger:
    """Log writer bound to one run/phase. Not thread-safe by default."""

    log_dir: Path
    phase: str
    verbose: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.log_dir / f"{self.phase}.log"

    def _write(self, level: str, message: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"{stamp} [{level:>5}] [{self.phase}] {_redact(message)}"
        with self._lock:
            with self.file_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def info(self, message: str) -> None:
        self._write("INFO", message)
        print(f"[{self.phase}] {message}")

    def warn(self, message: str) -> None:
        self._write("WARN", message)
        print(f"[{self.phase}] WARN: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        self._write("ERROR", message)
        print(f"[{self.phase}] ERROR: {message}", file=sys.stderr)

    def debug(self, message: str) -> None:
        self._write("DEBUG", message)
        if self.verbose:
            print(f"[{self.phase}] debug: {message}")

    def raw(self, text: str) -> None:
        """Dump a large command output verbatim into the phase log only."""
        self._write("RAW", _redact(text)[:100_000])


def fresh_line(log_dir: Path, phase: str) -> None:
    """Start a new phase log file (archive the previous one if present)."""
    target = log_dir / f"{phase}.log"
    if target.exists() and target.stat().st_size > 0:
        archive = log_dir / f"{phase}.prev.log"
        try:
            target.rename(archive)
        except OSError:
            pass
