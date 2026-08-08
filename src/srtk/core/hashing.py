"""SHA-256 manifest helpers.

Every file that crosses a phase boundary (firmware parts, patched AP tar,
module zips, backups) is pinned in a manifest so later phases can assert the
bytes on disk are exactly the bytes that were verified earlier.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .errors import ErrorCode, SrtkError

_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(path: Path, entries: dict[str, str]) -> None:
    """entries: relative-or-absolute path -> sha256 hex."""
    manifest = {str(k): v for k, v in sorted(entries.items())}
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_manifest(path: Path, base: Path | None = None) -> dict[str, str]:
    """Return the manifest; raise HASH_MISMATCH if any entry on disk differs.

    ``base`` optionally resolves relative manifest keys against a root dir.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for rel, expected in manifest.items():
        target = Path(rel)
        if not target.is_absolute():
            if base is None:
                raise SrtkError(
                    ErrorCode.HASH_MISMATCH,
                    context=f"manifest key is relative but no base given: {rel}",
                )
            target = base / target
        actual = sha256_file(target)
        if actual != expected:
            raise SrtkError(
                ErrorCode.HASH_MISMATCH,
                context=str(target),
                details=f"expected {expected}, got {actual}",
            )
    return manifest
