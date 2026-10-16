"""Write a file in one go (T14): never leaves a half-written file behind.

The text goes into a temporary file in the same folder first and only replaces
the real file once it is complete.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from othello.io.errors import OutputWriteError


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` safely; raises ``OutputWriteError`` if it cannot."""
    return _atomic_write(path, text.encode(encoding))


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    """Write ``data`` (for example a picture) to ``path`` safely."""
    return _atomic_write(path, data)


def _atomic_write(path: str | Path, data: bytes) -> Path:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    except OSError as problem:
        raise OutputWriteError(f"could not write {target}: {problem}") from problem
    return target
