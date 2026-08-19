#!/usr/bin/env python3
"""Inspect, install, and smoke-test a built kairospy distribution."""

from __future__ import annotations

import argparse
import email.parser
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


NATIVE_BINARIES = (
    "kairos-aeron-driver",
    "kairos-aeron-bridge",
    "kairos-reference-server",
    "kairos-reference-cli",
    "kairos-market-server",
    "kairos-market-cli",
    "kairos-risk-server",
    "kairos-risk-cli",
    "kairos-execution-server",
    "kairos-execution-cli",
    "kairos-account-server",
    "kairos-account-cli",
)


def _metadata_version(data: bytes) -> str:
    metadata = email.parser.BytesParser().parsebytes(data)
    version = metadata.get("Version")
    if not version:
        raise RuntimeError("distribution metadata does not contain Version")
    return version


def inspect_wheel(wheel: Path, expected_version: str) -> None:
    if wheel.name.endswith("none-any.whl"):
        raise RuntimeError(
            f"native distribution has a universal wheel tag: {wheel.name}"
        )
    suffix = ".exe" if "win_" in wheel.name else ""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_paths = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise RuntimeError(f"expected one METADATA file in {wheel.name}")
        actual_version = _metadata_version(archive.read(metadata_paths[0]))
        if actual_version != expected_version:
            raise RuntimeError(
                f"wheel version {actual_version!r} != {expected_version!r}"
            )
        missing = [
            binary
            for binary in NATIVE_BINARIES
            if f"kairospy/_bin/{binary}{suffix}" not in names
        ]
        if missing:
            raise RuntimeError(
                f"wheel is missing native binaries: {', '.join(missing)}"
            )


def inspect_sdist(sdist: Path, expected_version: str) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        metadata = [
            member
            for member in archive.getmembers()
            if member.name.endswith("/PKG-INFO")
        ]
        if len(metadata) != 1:
            raise RuntimeError(f"expected one PKG-INFO file in {sdist.name}")
        extracted = archive.extractfile(metadata[0])
        if extracted is None:
            raise RuntimeError(f"could not read PKG-INFO from {sdist.name}")
        actual_version = _metadata_version(extracted.read())
        if actual_version != expected_version:
            raise RuntimeError(
                f"sdist version {actual_version!r} != {expected_version!r}"
            )


def install_and_smoke(wheel: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            str(wheel),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import kairospy; import kairospy._native_transport",
        ],
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "kairospy", "--help"],
        check=True,
        timeout=30,
    )
    for binary in NATIVE_BINARIES:
        executable = shutil.which(binary)
        if executable is None:
            raise RuntimeError(
                f"installed native command is missing from PATH: {binary}"
            )
        subprocess.run([executable, "--help"], check=True, timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel, found {len(wheels)}")
    inspect_wheel(wheels[0], args.expected_version)
    sdists = sorted(args.directory.glob("*.tar.gz"))
    if len(sdists) > 1:
        raise RuntimeError(f"expected at most one sdist, found {len(sdists)}")
    for sdist in sdists:
        inspect_sdist(sdist, args.expected_version)
    install_and_smoke(wheels[0])
    print(f"distribution smoke test passed: {wheels[0].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
