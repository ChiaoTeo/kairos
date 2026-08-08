"""PEP 517 backend that adds native Kairos commands to the wheel."""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

from setuptools import build_meta as _setuptools

ROOT = Path(__file__).resolve().parent
BINARIES = (
    "kairos-reference-server", "kairos-reference-cli",
    "kairos-market-server", "kairos-market-cli",
    "kairos-risk-server", "kairos-risk-cli",
    "kairos-execution-server", "kairos-execution-cli",
    "kairos-account-server", "kairos-account-cli",
)


def _build_binaries(output: Path) -> None:
    subprocess.run(
        [os.environ.get("PYTHON", "python3"), str(ROOT / "scripts" / "build_rust_binaries.py"), "--output", str(output)],
        cwd=ROOT,
        check=True,
    )


def _digest(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"sha256={encoded.decode()}"


def _rewrite_wheel(wheel: Path, binaries: Path) -> None:
    with zipfile.ZipFile(wheel) as source:
        files = {name: source.read(name) for name in source.namelist()}
    dist_info = next(name.split("/", 1)[0] for name in files if name.endswith(".dist-info/WHEEL"))
    wheel_data = f"{dist_info.removesuffix('.dist-info')}.data/scripts"
    wheel_metadata = f"{dist_info}/WHEEL"
    files[wheel_metadata] = files[wheel_metadata].replace(b"Root-Is-Purelib: true", b"Root-Is-Purelib: false")
    for binary in BINARIES:
        data = (binaries / binary).read_bytes()
        files[f"kairospy/_bin/{binary}"] = data
        files[f"{wheel_data}/{binary}"] = data
    record = f"{dist_info}/RECORD"
    rows = [f"{name},{_digest(data)},{len(data)}" for name, data in files.items() if name != record]
    rows.append(f"{record},,")
    files[record] = ("\n".join(rows) + "\n").encode()
    temporary = wheel.with_suffix(".tmp.whl")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in files.items():
            target.writestr(name, data)
    temporary.replace(wheel)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    with tempfile.TemporaryDirectory(prefix="kairospy-native-") as temporary:
        binaries = Path(temporary)
        _build_binaries(binaries)
        filename = _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)
        _rewrite_wheel(Path(wheel_directory) / filename, binaries)
        return filename


def build_sdist(sdist_directory, config_settings=None):
    return _setuptools.build_sdist(sdist_directory, config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _setuptools.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def get_requires_for_build_wheel(config_settings=None):
    return _setuptools.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _setuptools.get_requires_for_build_sdist(config_settings)
