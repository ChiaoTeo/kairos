from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.contracts.account import AccountClient
from kairospy.contracts.capital import CapitalClient
from kairospy.contracts.execution import ExecutionClient
from kairospy.contracts.market import MarketClient
from kairospy.contracts.risk import RiskClient
def _clients(socket: Path):
    common = {"workspace_id": "workspace"}
    return (
        MarketClient(socket, **common),
        AccountClient(socket, account_id="main", **common),
        ExecutionClient(socket, **common),
        RiskClient(socket, actor_id="risk:main", **common),
        CapitalClient(socket, capital_group_id="main", **common),
    )


@pytest.mark.parametrize("index", range(5))
def test_owner_clients_expose_only_direct_owner_live_subscriptions(
    index: int, tmp_path: Path
) -> None:
    client = _clients(tmp_path / "control.sock")[index]
    owner = type(client).__name__.removesuffix("Client")
    native = __import__(
        f"kairospy._native_{owner.lower()}_contract", fromlist=["*"]
    )

    subscription = getattr(native, f"{owner}LiveSubscription")
    assert subscription.__module__ == f"kairospy._native_{owner.lower()}_contract"
    assert not hasattr(native, "NativeEventSource")
