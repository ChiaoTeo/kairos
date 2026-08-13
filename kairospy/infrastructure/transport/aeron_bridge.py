"""Concrete subprocess utilities shared by Kairos Aeron event bridges."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


def check_aeron_bridge(
    command: Sequence[str], *, domain: str, timeout: float = 2.0
) -> None:
    """Prove that a bridge can attach its configured Aeron subscription."""

    try:
        result = subprocess.run(
            [*command, "--check"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"{domain} Aeron event source readiness timed out"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(
            detail
            or f"{domain} Aeron event source readiness failed with {result.returncode}"
        )
