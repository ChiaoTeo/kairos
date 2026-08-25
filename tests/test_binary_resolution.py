from pathlib import Path

import pytest

from kairospy.system.apps.components.application.binaries import resolve_binary


def test_binary_resolver_honors_explicit_override(tmp_path: Path) -> None:
    binary = tmp_path / "reference-server"
    binary.write_text("binary", encoding="utf-8")
    assert resolve_binary("kairos-reference-server", override=str(binary)) == str(
        binary
    )


def test_binary_resolver_finds_development_target() -> None:
    value = resolve_binary("kairos-reference-server")
    assert value.endswith("kairos-reference-server")


def test_binary_resolver_honors_canonical_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAIROS_MARKET_CLI", "/opt/kairos/market-cli")
    monkeypatch.setenv("KAIROS_KAIROS_MARKET_CLI", "/legacy/market-cli")

    assert resolve_binary("kairos-market-cli") == "/opt/kairos/market-cli"


def test_binary_resolver_keeps_legacy_environment_override_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAIROS_MARKET_CLI", raising=False)
    monkeypatch.setenv("KAIROS_KAIROS_MARKET_CLI", "/legacy/market-cli")

    assert resolve_binary("kairos-market-cli") == "/legacy/market-cli"


def test_binary_resolver_error_names_canonical_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KAIROS_MISSING_CLI", raising=False)
    monkeypatch.delenv("KAIROS_KAIROS_MISSING_CLI", raising=False)

    with pytest.raises(FileNotFoundError, match="set KAIROS_MISSING_CLI"):
        resolve_binary("kairos-missing-cli")
