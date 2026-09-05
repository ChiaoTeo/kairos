from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RUST_FIXTURE_TARGET = _REPOSITORY_ROOT / "target" / "pytest-rust-fixtures"
_RUST_CONTRACT_PACKAGES = (
    "kairos-account-contract",
    "kairos-capital-contract",
    "kairos-execution-contract",
    "kairos-market-contract",
    "kairos-risk-contract",
)


@pytest.fixture(scope="session")
def rust_contract_example() -> Callable[[str], Path]:
    """Build every Rust contract example once for the interop test layer."""

    environment = {**os.environ, "CARGO_TARGET_DIR": str(_RUST_FIXTURE_TARGET)}
    command = ["cargo", "build", "--locked", "--quiet"]
    for package in _RUST_CONTRACT_PACKAGES:
        command.extend(("-p", package))
    command.append("--examples")
    subprocess.run(command, cwd=_REPOSITORY_ROOT, env=environment, check=True)

    def resolve(name: str) -> Path:
        executable = f"{name}.exe" if os.name == "nt" else name
        path = _RUST_FIXTURE_TARGET / "debug" / "examples" / executable
        if not path.is_file():
            raise FileNotFoundError(f"Cargo did not produce Rust fixture: {path}")
        return path

    return resolve
