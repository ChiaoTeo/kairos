from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kairospy.investment.apps.market.application.cli import (
    MarketCliApplication,
    parse_market_command_line,
)
from kairospy.surface.workbench.screens.commands import normalize


def _state(workspace: Path) -> SimpleNamespace:
    owner = SimpleNamespace(paths=SimpleNamespace(root=workspace))
    return SimpleNamespace(owner=owner, load_error=None)


def test_market_shell_command_uses_the_public_kairos_entrypoint(tmp_path: Path) -> None:
    state = _state(tmp_path / ".kairos")

    assert MarketCliApplication(state.owner).shell_command(
        ("standalone", "once", "--symbol", "AAPL")
    ) == [
        "kairos",
        "market",
        "--workspace",
        str(state.owner.paths.root),
        "standalone",
        "once",
        "--symbol",
        "AAPL",
    ]


def test_market_command_line_separates_surface_options() -> None:
    command = parse_market_command_line(
        ("--format=json", "standalone", "once", "--symbol", "AAPL")
    )

    assert command.output == "json"
    assert command.arguments == ("standalone", "once", "--symbol", "AAPL")


def test_market_command_line_rejects_unknown_output_format() -> None:
    with pytest.raises(ValueError, match="must be text, json, or table"):
        parse_market_command_line(("--format", "yaml", "standalone", "once"))


def test_pasted_public_market_command_is_normalized(tmp_path: Path) -> None:
    workspace = tmp_path / ".kairos"

    assert normalize(
        _state(workspace),
        (
            "kairos",
            "market",
            "--workspace",
            str(workspace),
            "standalone",
            "once",
            "--symbol",
            "AAPL",
        ),
    ) == ("market", "standalone", "once", "--symbol", "AAPL")


def test_public_market_output_option_is_owned_by_the_active_surface(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / ".kairos"

    assert normalize(
        _state(workspace),
        (
            "kairos",
            "market",
            f"--workspace={workspace}",
            "--format=json",
            "standalone",
            "once",
        ),
    ) == ("market", "standalone", "once")


def test_pasted_market_command_cannot_switch_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="当前 Workbench workspace"):
        normalize(
            _state(tmp_path / "current"),
            (
                "kairos",
                "market",
                "--workspace",
                str(tmp_path / "other"),
                "standalone",
                "once",
            ),
        )


def test_public_kairos_command_does_not_gain_a_second_prefix(tmp_path: Path) -> None:
    assert normalize(_state(tmp_path), ("kairos", "market", "routes")) == (
        "market",
        "routes",
    )
