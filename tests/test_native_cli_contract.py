from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.application.account.cli import AccountCliApplication
from kairospy.application.market.cli import MarketCliApplication
from kairospy.application.reference.cli import ReferenceCliApplication
from kairospy.application.workspace import WorkspaceApplication


def test_reference_adapter_owns_workspace_but_not_provider(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="test")
    command = ReferenceCliApplication(workspace, binary="reference").command(
        ["--provider", "binance-spot", "refresh"]
    )

    assert command == [
        "reference",
        "--workspace",
        str(workspace.paths.root),
        "--provider",
        "binance-spot",
        "refresh",
    ]


def test_native_adapters_reject_duplicate_owned_options(tmp_path: Path) -> None:
    workspace = WorkspaceApplication().init(tmp_path / "workspace", workspace_id="test")

    with pytest.raises(ValueError, match="--workspace"):
        ReferenceCliApplication(workspace, binary="reference").command(
            ["--workspace", str(workspace.paths.root), "status"]
        )
    with pytest.raises(ValueError, match="--output"):
        AccountCliApplication(workspace, binaries={"account": "account"}).run(
            ["--output", "text", "list"]
        )
    with pytest.raises(ValueError, match="--format"):
        MarketCliApplication(workspace, binary="market").run(["--format", "text", "validate"])
