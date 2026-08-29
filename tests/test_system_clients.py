from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairospy.system.apps.components.application import (
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
from kairospy.system.apps.launch.application.connections import (
    ComponentConnection,
    InstanceConnections,
)


def test_account_system_client_keeps_only_control_and_reconciliation_queries() -> None:
    class RecordingAccountControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def reconcile(self, request):
            self.calls.append(("reconcile", request))
            return SimpleNamespace(status="completed", account_id=None, segments=[])

    client = AccountSystemClient(Path("/tmp/account.sock"))
    control = RecordingAccountControl()
    object.__setattr__(client, "control", control)
    assert client.reconcile() == {
        "status": "completed",
        "account_id": None,
        "segments": [],
    }
    assert len(control.calls) == 1
    assert control.calls[0][0] == "reconcile"
    assert control.calls[0][1].segments == []


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
    submit_request = object()
    routes_query = object()
    client.submit_intent(submit_request)
    client.routes(routes_query)
    assert control.calls == [
        ("submit_intent", submit_request),
        ("routes", routes_query),
    ]


def test_execution_system_client_owns_backtest_market_dispatch(monkeypatch) -> None:
    import kairospy.investment.apps.execution.application.mapping as execution_mapping

    class RecordingExecutionControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def backtest_market(self, request):
            self.calls.append(("backtest_market", request))
            return response

    class Response:
        fills: list[object] = []

    request = object()
    response = Response()

    monkeypatch.setattr(
        execution_mapping,
        "backtest_market_request",
        lambda event: request,
    )
    client = ExecutionSystemClient(Path("/tmp/execution.sock"))
    control = RecordingExecutionControl()
    object.__setattr__(client, "control", control)

    assert client.backtest_market("instrument:BTCUSDT") is response
    assert control.calls == [("backtest_market", request)]


def test_account_system_client_owns_mark_to_market_dispatch(monkeypatch) -> None:
    import kairospy.investment.apps.account.application.mapping as account_mapping

    class RecordingAccountControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def mark_to_market(self, request):
            self.calls.append(("mark_to_market", request))
            return SimpleNamespace(status="applied")

    request = SimpleNamespace(segment_key="spot")
    monkeypatch.setattr(
        account_mapping,
        "backtest_mark_to_market_request",
        lambda event: request if event == "quote" else None,
    )
    client = AccountSystemClient(Path("/tmp/account.sock"))
    control = RecordingAccountControl()
    object.__setattr__(client, "control", control)

    assert client.mark_to_market_event("quote") == {
        "result": {"status": "applied"},
        "segment_key": "spot",
    }
    assert client.mark_to_market_event("ignored") is None
    assert control.calls == [("mark_to_market", request)]


def test_market_system_client_owns_current_data_route_query() -> None:
    class RecordingMarketControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def data_routes(self, **query):
            self.calls.append(("data_routes", query))
            return type("Routes", (), {"routes": []})()

    client = MarketSystemClient(Path("/tmp/market.sock"))
    control = RecordingMarketControl()
    object.__setattr__(client, "control", control)
    client.data_routes(market_id="market:btc", observation_kind="quote")
    assert control.calls == [
        ("data_routes", {"market_id": "market:btc", "observation_kind": "quote"})
    ]


def test_market_system_client_projects_owner_health() -> None:
    class RecordingMarketControl:
        def health(self):
            return SimpleNamespace(
                status="ready",
                actor_id="market-actor",
                event_sequence=91,
                feed_status="ready",
                current_view_commit_count=80,
                current_view_input_update_count=90,
                current_view_encoded_update_count=79,
                current_view_order_book_encode_count=8,
                last_current_view_commit_latency_nanos=2_500_000,
                notification_attempt_count=90,
                notification_failure_count=1,
            )

    client = MarketSystemClient(Path("/tmp/market.sock"))
    object.__setattr__(client, "control", RecordingMarketControl())

    assert client.health() == {
        "status": "ready",
        "actor_id": "market-actor",
        "event_sequence": 91,
        "feed_status": "ready",
        "current_view_commit_count": 80,
        "current_view_input_update_count": 90,
        "current_view_encoded_update_count": 79,
        "current_view_order_book_encode_count": 8,
        "last_current_view_commit_latency_nanos": 2_500_000,
        "notification_attempt_count": 90,
        "notification_failure_count": 1,
    }


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


def test_reference_system_client_exposes_owner_runtime_status() -> None:
    class RecordingReferenceReader:
        def runtime_status(self) -> dict[str, object]:
            return {"status": "ready", "sources": []}

        def plan_catalog_setup(self, goal: Mapping[str, object]) -> dict[str, object]:
            return {"goal": dict(goal), "availability": "not_configured"}

    client = ReferenceSystemClient(Path("/tmp/reference.sock"))
    object.__setattr__(client, "reader", RecordingReferenceReader())

    assert client.reference_status() == {"status": "ready", "sources": []}
    assert client.plan_reference_catalog(
        {
            "kind": "exchange_instruments",
            "exchange_id": "exchange:nasdaq",
            "instrument_kind": "equity",
        }
    ) == {
        "goal": {
            "kind": "exchange_instruments",
            "exchange_id": "exchange:nasdaq",
            "instrument_kind": "equity",
        },
        "availability": "not_configured",
    }


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
