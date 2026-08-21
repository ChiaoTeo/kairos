from __future__ import annotations

from pathlib import Path

import pytest

from kairospy.application.system import (
    AccountSystemClient,
    CapitalSystemClient,
    ComponentControlApplication,
    ComponentProcessApplication,
    ExecutionSystemClient,
    InstanceSystemClients,
    MarketSystemClient,
    ReferenceSystemClient,
    RiskSystemClient,
)
from kairospy.application.launch.application.connections import (
    ComponentConnection,
    InstanceConnections,
)


def test_account_system_client_keeps_only_control_and_reconciliation_queries() -> None:
    class RecordingAccountControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def reconcile(self, request):
            self.calls.append(("reconcile", request))
            return {"status": "ok"}

    client = AccountSystemClient(Path("/tmp/account.sock"))
    control = RecordingAccountControl()
    object.__setattr__(client, "control", control)
    client.reconcile()
    assert control.calls == [("reconcile", {})]


def test_system_rpc_client_rejects_path_like_methods() -> None:
    client = AccountSystemClient(Path("/tmp/account.sock"))
    with pytest.raises(ValueError, match="path-free"):
        client.call("/v1/orders")


def test_execution_system_client_owns_intent_connection() -> None:
    class RecordingExecutionControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def submit_intent(self, request):
            self.calls.append(("submit_intent", request))
            return {"status": "ok"}

        def routes(self, query):
            self.calls.append(("routes", query))
            return {"status": "ok"}

    client = ExecutionSystemClient(Path("/tmp/execution.sock"))
    control = RecordingExecutionControl()
    object.__setattr__(client, "control", control)
    client.submit_intent({"intent_id": "i-1"})
    client.routes({"account_id": "main", "order_type": "limit"})
    assert control.calls == [
        ("submit_intent", {"intent_id": "i-1"}),
        ("routes", {"account_id": "main", "order_type": "limit"}),
    ]


def test_execution_system_client_owns_backtest_market_dispatch(monkeypatch) -> None:
    import kairospy.infrastructure.contracts.execution as execution_contract

    class RecordingExecutionControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def backtest_market(self, payload):
            self.calls.append(("backtest_market", payload))
            return {"fills": []}

    monkeypatch.setattr(
        execution_contract,
        "backtest_market_payload",
        lambda event: {"Quote": {"instrument_id": event}},
    )
    client = ExecutionSystemClient(Path("/tmp/execution.sock"))
    control = RecordingExecutionControl()
    object.__setattr__(client, "control", control)

    assert client.backtest_market("instrument:BTCUSDT") == {"fills": []}
    assert control.calls == [
        ("backtest_market", {"Quote": {"instrument_id": "instrument:BTCUSDT"}})
    ]


def test_account_system_client_owns_mark_to_market_dispatch(monkeypatch) -> None:
    import kairospy.infrastructure.contracts.account as account_contract

    class RecordingAccountControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def mark_to_market(self, request):
            self.calls.append(("mark_to_market", request))
            return {"status": "accepted"}

    request = {
        "segment_key": "spot",
        "instrument_id": "instrument:BTCUSDT",
        "quote_asset": "USDT",
        "mark_price": "100",
        "observed_at_unix_nanos": 1,
    }
    monkeypatch.setattr(
        account_contract,
        "backtest_mark_to_market_request",
        lambda event: request if event == "quote" else None,
    )
    client = AccountSystemClient(Path("/tmp/account.sock"))
    control = RecordingAccountControl()
    object.__setattr__(client, "control", control)

    assert client.mark_to_market_event("quote") == {
        "result": {"status": "accepted"},
        "segment_key": "spot",
    }
    assert client.mark_to_market_event("ignored") is None
    assert control.calls == [("mark_to_market", request)]


def test_market_system_client_owns_current_data_source_query() -> None:
    class RecordingMarketControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def data_sources(self, query):
            self.calls.append(("data_sources", query))
            return {"data_sources": []}

    client = MarketSystemClient(Path("/tmp/market.sock"))
    control = RecordingMarketControl()
    object.__setattr__(client, "control", control)
    client.data_sources({"market_id": "market:btc", "observation_kind": "quote"})
    assert control.calls == [
        ("data_sources", {"market_id": "market:btc", "observation_kind": "quote"})
    ]


def test_system_process_factory_returns_typed_business_clients() -> None:
    socket = Path("/tmp/component.sock")
    factory = ComponentProcessApplication.client

    assert isinstance(factory("account", socket), AccountSystemClient)
    assert isinstance(factory("execution", socket), ExecutionSystemClient)
    assert isinstance(factory("market", socket), MarketSystemClient)
    assert isinstance(factory("risk", socket), RiskSystemClient)
    assert isinstance(factory("capital", socket), CapitalSystemClient)
    assert isinstance(factory("reference", socket), ReferenceSystemClient)
    assert isinstance(factory("control", socket), ComponentControlApplication)


def test_instance_system_clients_are_built_from_connection_manifest_facts() -> None:
    connections = InstanceConnections(
        accounts={
            "main": ComponentConnection(
                "account:main",
                Path("/tmp/account.sock"),
                view_root=Path("/tmp/account-view"),
            ),
        },
        market=ComponentConnection("market", Path("/tmp/market.sock")),
        reference=ComponentConnection(
            "reference",
            Path("/tmp/reference.sock"),
            database=Path("/tmp/reference.sqlite"),
            actor_id="reference-actor",
        ),
        risk=ComponentConnection(
            "risk",
            Path("/tmp/risk.sock"),
            view_root=Path("/tmp/risk-view"),
        ),
        execution=ComponentConnection(
            "execution",
            Path("/tmp/execution.sock"),
            view_root=Path("/tmp/execution-view"),
        ),
        capital=ComponentConnection(
            "capital",
            Path("/tmp/capital.sock"),
            view_root=Path("/tmp/capital-view"),
        ),
    )

    clients = InstanceSystemClients.from_connections(connections)

    assert isinstance(clients.accounts["main"], AccountSystemClient)
    assert clients.accounts["main"].view_root == Path("/tmp/account-view")
    assert isinstance(clients.market, MarketSystemClient)
    assert isinstance(clients.reference, ReferenceSystemClient)
    assert clients.reference.database_path == Path("/tmp/reference.sqlite")
    assert clients.reference.actor_id == "reference-actor"
    assert isinstance(clients.risk, RiskSystemClient)
    assert clients.risk.view_root == Path("/tmp/risk-view")
    assert isinstance(clients.execution, ExecutionSystemClient)
    assert clients.execution.view_root == Path("/tmp/execution-view")
    assert isinstance(clients.capital, CapitalSystemClient)
    assert clients.capital.view_root == Path("/tmp/capital-view")
