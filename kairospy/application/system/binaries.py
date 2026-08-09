"""Resolve packaged or development Rust component binaries."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Sequence


def reject_owned_options(arguments: Sequence[str], owned: set[str]) -> None:
    """Reject options owned by a native CLI adapter before spawning it.

    Native adapters add their owned infrastructure options exactly once. A
    caller passing one of those options would create an ambiguous command
    contract, so fail at the Python boundary with a precise error.
    """
    for argument in arguments:
        option = argument.split("=", 1)[0]
        if option in owned:
            names = ", ".join(sorted(owned))
            raise ValueError(f"native CLI adapter owns {names}; do not pass {option}")


def resolve_binary(name: str, *, override: str | None = None) -> str:
    """Return an executable path for a component binary.

    A configured override is useful for development and tests. Installed
    wheels use package data first; a PATH binary remains a development
    fallback.
    """
    if override:
        return override
    env_name = "KAIROS_" + name.upper().replace("-", "_")
    if value := os.environ.get(env_name):
        return value
    package_root = Path(__file__).resolve().parents[2]
    packaged_candidates = [package_root / "_bin" / name]
    if os.name == "nt":
        packaged_candidates.insert(0, package_root / "_bin" / f"{name}.exe")
    for packaged in packaged_candidates:
        if packaged.is_file() and os.access(packaged, os.X_OK):
            return str(packaged)
    development_names = [name]
    if os.name == "nt":
        development_names.insert(0, f"{name}.exe")
    for development in (
        package_root.parent / "target" / profile / filename
        for profile in ("debug", "release")
        for filename in development_names
    ):
        if development.is_file():
            return str(development)
    if value := shutil.which(name):
        return value
    raise FileNotFoundError(
        f"{name} is not installed; install a Kairos wheel with bundled components "
        f"or set {env_name}"
    )


__all__ = ["reject_owned_options", "resolve_binary"]
