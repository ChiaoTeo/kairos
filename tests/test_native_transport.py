from __future__ import annotations

from dataclasses import dataclass

import pytest

from kairospy.infrastructure.transport import native as native_facade
from kairospy.infrastructure.protocol.generated_spec import TRANSPORT_FINGERPRINT


def test_build_info_matches_transport_only_fingerprint() -> None:
    build = native_facade.native.build_info()
    assert build.api_version == 1
    assert build.transport_fingerprint == TRANSPORT_FINGERPRINT


@dataclass
class _Build:
    api_version: int = 1
    transport_fingerprint: str = TRANSPORT_FINGERPRINT
    package_version: str = "test"


def test_build_info_mismatch_fails_fast() -> None:
    with pytest.raises(ImportError, match="API mismatch"):
        native_facade._validate_build_info(_Build(api_version=2))
    with pytest.raises(ImportError, match="fingerprint mismatch"):
        native_facade._validate_build_info(_Build(transport_fingerprint="wrong"))
