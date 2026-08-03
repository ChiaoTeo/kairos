from __future__ import annotations

from pathlib import Path
import re


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def safe_name(value: str) -> str:
    if not _NAME_PATTERN.fullmatch(value):
        raise ValueError(f"invalid storage resource name: {value!r}")
    return value


def safe_relative_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"storage path must be relative: {value}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"invalid storage path: {value}")
    return path


__all__ = ["safe_name", "safe_relative_path"]
