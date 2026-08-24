"""Version-checked facade for the private Rust transport extension."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from kairospy.infrastructure.protocol.generated_spec import TRANSPORT_FINGERPRINT


native: Any = import_module("kairospy._native_transport")

EXPECTED_API_VERSION = 1
_build = native.build_info()


def _validate_build_info(build: object) -> None:
    api_version = getattr(build, "api_version")
    fingerprint = getattr(build, "transport_fingerprint")
    package_version = getattr(build, "package_version")
    if api_version != EXPECTED_API_VERSION:
        raise ImportError(
            "kairospy native transport API mismatch: "
            f"python={EXPECTED_API_VERSION}, native={api_version}, "
            f"native_package={package_version}"
        )
    if fingerprint != TRANSPORT_FINGERPRINT:
        raise ImportError(
            "kairospy native transport fingerprint mismatch: "
            f"python={TRANSPORT_FINGERPRINT}, "
            f"native={fingerprint}, native_package={package_version}"
        )


_validate_build_info(_build)


__all__ = ["EXPECTED_API_VERSION", "native"]
