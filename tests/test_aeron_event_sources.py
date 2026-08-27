from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.infrastructure.contracts.account import AccountClient
from kairospy.infrastructure.contracts.capital import CapitalClient
from kairospy.infrastructure.contracts.execution import ExecutionClient
from kairospy.infrastructure.contracts.market import MarketClient
from kairospy.infrastructure.contracts.risk import RiskClient
from kairospy.infrastructure.protocol.generated_spec import (
    ACCOUNT_EVENTS,
    CAPITAL_EVENTS,
    EXECUTION_EVENTS,
    MARKET_EVENTS,
    RISK_EVENTS,
)


def _clients(socket: Path):
    common = {"workspace_id": "workspace", "aeron_dir": "/workspace/run/aeron/media"}
    return (
        (MarketClient(socket, **common), MARKET_EVENTS),
        (AccountClient(socket, account_id="main", **common), ACCOUNT_EVENTS),
        (ExecutionClient(socket, **common), EXECUTION_EVENTS),
        (RiskClient(socket, actor_id="risk:main", **common), RISK_EVENTS),
        (CapitalClient(socket, capital_group_id="main", **common), CAPITAL_EVENTS),
    )


@pytest.mark.parametrize("index", range(5))
def test_owner_clients_supply_native_event_sources(index: int, tmp_path: Path) -> None:
    client, stream_id = _clients(tmp_path / "control.sock")[index]
    source = client.events

    assert source._aeron_dir == "/workspace/run/aeron/media"
    assert source._spec.stream_id == stream_id
    assert callable(source._decoder)
