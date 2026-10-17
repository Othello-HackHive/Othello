"""Safe file names and the output file layout for one plan (T11).

A plan id comes from outside, so it is never used as a file name directly: it is
cleaned so it can never contain a folder separator or climb out of the output folder.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
FALLBACK_NAME = "unknown_plan"
MAX_NAME_LENGTH = 100


def safe_name(plan_id: object, max_length: int = MAX_NAME_LENGTH) -> str:
    """A file-name-safe version of a plan id (letters, digits, dot, dash, underscore)."""
    text = _UNSAFE.sub("_", str(plan_id if plan_id is not None else "")).strip("._-")
    text = text[:max_length].strip("._-")
    return text or FALLBACK_NAME


def output_paths(out_dir: str | Path, plan_id: object) -> dict[str, Path]:
    """Where every output file of one plan goes (all inside ``out_dir``)."""
    folder = Path(out_dir)
    name = safe_name(plan_id)
    paths = {
        "official": folder / f"{name}.json",
        "baseline": folder / f"{name}.baseline.json",
        "overlay": folder / f"{name}.overlay.json",
        "picture": folder / f"{name}.png",
        "log": folder / f"{name}.log.txt",
        "failure": folder / f"{name}.failure.json",
        "settings": folder / f"{name}.settings.json",
    }
    resolved = folder.resolve()
    for path in paths.values():
        if path.resolve().parent != resolved:  # cannot happen with safe_name; belt and braces
            raise ValueError(f"unsafe output path for plan id {plan_id!r}")
    return paths
