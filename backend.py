"""PEP 517 backend that adds native Kairos commands to the wheel."""

from __future__ import annotations

import base64
import hashlib
import os
import platform
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

from setuptools import build_meta as _setuptools

ROOT = Path(__file__).resolve().parent
BINARIES = (
    "kairos-aeron-driver",
    "kairos-aeron-bridge",
    "kairos-reference-server", "kairos-reference-cli",
    "kairos-market-server", "kairos-market-cli",
    "kairos-risk-server", "kairos-risk-cli",
    "kairos-execution-server", "kairos-execution-cli",
    "kairos-account-server", "kairos-account-cli",
)


def _binary_filename(name: str) -> str:
    """Return the native filename used by the current operating system."""
    return f"{name}.exe" if os.name == "nt" else name


def _platform_tag() -> str:
    """Return a conservative wheel platform tag for the build host.

    The package contains executable Rust programs rather than a CPython
    extension, so setuptools otherwise treats it as a universal pure-Python
    wheel.  That tag would allow pip to install, for example, an x86_64
    executable on an arm64 machine.
    """
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return {
            "amd64": "win_amd64",
            "x86_64": "win_amd64",
            "arm64": "win_arm64",
            "aarch64": "win_arm64",
            "x86": "win32",
            "i386": "win32",
        }.get(machine, f"win_{machine}")
    if sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "macosx_11_0_arm64"
        if machine in {"x86_64", "amd64"}:
            return "macosx_10_15_x86_64"
        return f"macosx_10_15_{machine}"
    if sys.platform == "linux":
        # The release runner uses Ubuntu 22.04 (glibc 2.35).  Keep the tag
        # conservative for Linux and let pip restrict installation to Linux
        # of the matching CPU architecture.
        return {
            "x86_64": "linux_x86_64",
            "amd64": "linux_x86_64",
            "aarch64": "linux_aarch64",
            "arm64": "linux_aarch64",
        }.get(machine, f"linux_{machine}")
    return f"{sys.platform}_{machine}"


def _build_binaries(output: Path) -> None:
    print(
        "kairospy: building Rust binaries for the wheel...",
        file=sys.stderr,
        flush=True,
    )
    subprocess.run(
        [os.environ.get("PYTHON", sys.executable), str(ROOT / "scripts" / "build" / "build_rust_binaries.py"), "--output", str(output)],
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
    platform_tag = _platform_tag()
    for binary in BINARIES:
        filename = _binary_filename(binary)
        data = (binaries / filename).read_bytes()
        for name in (f"kairospy/_bin/{filename}", f"{wheel_data}/{filename}"):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o100755 << 16
            files[name] = (data, info)
    wheel_text = files[wheel_metadata].decode()
    tags = [line.removeprefix("Tag: ") for line in wheel_text.splitlines() if line.startswith("Tag: ")]
    python_tag, abi_tag, _ = tags[0].split("-", 2)
    # The extension is compiled with PyO3's abi3-py311 feature. setuptools on
    # newer CPython versions may still report the build interpreter tag; make
    # the wheel metadata reflect the actual limited ABI. Free-threaded builds
    # deliberately retain their version-specific tag.
    if not sysconfig.get_config_var("Py_GIL_DISABLED"):
        python_tag, abi_tag = "cp311", "abi3"
    wheel_text = "\n".join(
        f"Tag: {python_tag}-{abi_tag}-{platform_tag}" if line.startswith("Tag: ") else line
        for line in wheel_text.splitlines()
    ) + "\n"
    files[wheel_metadata] = wheel_text.encode()
    record = f"{dist_info}/RECORD"
    rows = [
        f"{name},{_digest(data if isinstance(data, bytes) else data[0])},{len(data if isinstance(data, bytes) else data[0])}"
        for name, data in files.items()
        if name != record
    ]
    rows.append(f"{record},,")
    files[record] = ("\n".join(rows) + "\n").encode()
    temporary = wheel.with_suffix(".tmp.whl")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in files.items():
            if isinstance(data, tuple):
                target.writestr(data[1], data[0])
            else:
                target.writestr(name, data)
    wheel_stem = wheel.stem
    distribution_version = wheel_stem.rsplit("-", 3)[0]
    platform_wheel = wheel.with_name(
        f"{distribution_version}-{python_tag}-{abi_tag}-{platform_tag}.whl"
    )
    temporary.replace(platform_wheel)
    if platform_wheel != wheel:
        wheel.unlink()
    return platform_wheel.name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    with tempfile.TemporaryDirectory(prefix="kairospy-native-") as temporary:
        binaries = Path(temporary)
        _build_binaries(binaries)
        filename = _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)
        return _rewrite_wheel(Path(wheel_directory) / filename, binaries)


def build_sdist(sdist_directory, config_settings=None):
    return _setuptools.build_sdist(sdist_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    """Delegate editable installs to setuptools without rebuilding native files."""
    return _setuptools.build_editable(wheel_directory, config_settings, metadata_directory)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _setuptools.prepare_metadata_for_build_editable(metadata_directory, config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _setuptools.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def get_requires_for_build_wheel(config_settings=None):
    return _setuptools.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _setuptools.get_requires_for_build_sdist(config_settings)
