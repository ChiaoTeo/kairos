from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairospy.contracts.reference import ReferenceRuntimeStatusResponse
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
    system_client,
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
    result = client.reconcile()
    assert result.status == "completed"
    assert result.account_id is None
    assert result.segments == []
    assert len(control.calls) == 1
    assert control.calls[0][0] == "reconcile"
    assert control.calls[0][1].segments == []


def test_system_rpc_client_rejects_path_like_methods() -> None:
    client = AccountSystemClient(Path("/tmp/account.sock"))
    with pytest.raises(ValueError, match="path-free"):
        client.call("/v1/orders")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"id": 1, "result": {}}, "missing version 2.0"),
        ({"jsonrpc": "2.0", "id": 2, "result": {}}, "id does not match"),
        ({"jsonrpc": "2.0", "id": 1, "result": []}, "result must be an object"),
    ],
)
def test_system_rpc_client_rejects_malformed_rpc_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    message: str,
) -> None:
    import kairospy.infrastructure.transport.json_rpc as json_rpc
    import kairospy.system.apps.components.application.clients as clients

    monkeypatch.setattr(
        json_rpc, "request_sync", lambda *_args, **_kwargs: (200, response)
    )

    with pytest.raises(ValueError, match=message):
        clients.SystemRpcClient(Path("/tmp/system.sock")).status()


def test_system_rpc_client_preserves_structured_remote_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import kairospy.infrastructure.transport.json_rpc as json_rpc
    import kairospy.system.apps.components.application.clients as clients

    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32017,
            "message": "risk command rejected",
            "data": {"reason": "limit_exceeded", "retryable": False},
        },
    }
    monkeypatch.setattr(
        json_rpc, "request_sync", lambda *_args, **_kwargs: (200, response)
    )

    with pytest.raises(json_rpc.JsonRpcCallError) as raised:
        clients.SystemRpcClient(Path("/tmp/system.sock")).status()

    assert raised.value.code == -32017
    assert raised.value.data == {"reason": "limit_exceeded", "retryable": False}


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

    result = client.mark_to_market_event("quote")
    assert result is not None
    assert result.result.status == "applied"
    assert result.segment_key == "spot"
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


def test_market_system_client_returns_owner_health_contract() -> None:
    health = SimpleNamespace(
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

    class RecordingMarketControl:
        def health(self):
            return health

    client = MarketSystemClient(Path("/tmp/market.sock"))
    object.__setattr__(client, "control", RecordingMarketControl())

    assert client.health() is health


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


def test_system_process_factory_rejects_unknown_component() -> None:
    with pytest.raises(ValueError, match="unsupported process component: typo"):
        ComponentProcessApplication.client("typo", Path("/tmp/component.sock"))


def test_business_system_client_factory_rejects_unknown_component() -> None:
    with pytest.raises(ValueError, match="unsupported business component: control"):
        system_client("control", Path("/tmp/component.sock"))


def test_reference_system_client_exposes_owner_runtime_status() -> None:
    status = ReferenceRuntimeStatusResponse.from_mapping(
        {
            "status": "ready",
            "app_runtime": {
                "phase": "serving",
                "actor_id": "reference",
                "source_id": "reference-default",
                "refresh_interval_millis": 60_000,
            },
            "catalog": {
                "readiness": "ready",
                "generation": 0,
                "event_sequence": 0,
                "market_count": 0,
            },
            "sources": [],
            "publication": {"pending_publication_count": 0},
            "diagnostics": [],
        }
    )

    class RecordingReferenceReader:
        def runtime_status(self) -> ReferenceRuntimeStatusResponse:
            return status

        def plan_catalog_setup(self, goal: Mapping[str, object]) -> dict[str, object]:
            return {"goal": dict(goal), "availability": "not_configured"}

    client = ReferenceSystemClient(Path("/tmp/reference.sock"))
    object.__setattr__(client, "reader", RecordingReferenceReader())

    assert client.reference_status() == status
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
