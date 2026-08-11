#!/usr/bin/env python3
"""Build the Rust processes that are shipped inside the Python wheel."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARIES = {
    "kairos-transport": ("kairos-aeron-driver",),
    "kairos-reference-service": ("kairos-reference-server", "kairos-reference-cli"),
    "kairos-market-service": ("kairos-market-server", "kairos-market-cli"),
    "kairos-risk-service": ("kairos-risk-server", "kairos-risk-cli"),
    "kairos-execution-service": ("kairos-execution-server", "kairos-execution-cli"),
    "kairos-account-service": ("kairos-account-server", "kairos-account-cli"),
}


def _binary_filename(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cargo = os.environ.get("CARGO", "cargo")
    total = sum(len(binaries) for binaries in BINARIES.values())
    completed = 0
    started = time.monotonic()
    print(
        f"kairospy: compiling {total} Rust binaries (release mode)",
        file=sys.stderr,
        flush=True,
    )
    for package, binaries in BINARIES.items():
        for binary in binaries:
            completed += 1
            print(
                f"kairospy: [{completed}/{total}] cargo build --release --bin {binary} "
                f"(package {package})",
                file=sys.stderr,
                flush=True,
            )
            binary_started = time.monotonic()
            subprocess.run(
                [cargo, "build", "--release", "-p", package, "--bin", binary],
                cwd=ROOT,
                check=True,
            )
            filename = _binary_filename(binary)
            source = ROOT / "target" / "release" / filename
            if not source.is_file():
                raise FileNotFoundError(f"cargo did not produce {source}")
            destination = output / filename
            shutil.copy2(source, destination)
            destination.chmod(destination.stat().st_mode | 0o111)
            print(
                f"kairospy: [{completed}/{total}] finished {binary} "
                f"({time.monotonic() - binary_started:.1f}s)",
                file=sys.stderr,
                flush=True,
            )
    print(
        f"kairospy: Rust binaries ready ({time.monotonic() - started:.1f}s)",
        file=sys.stderr,
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
