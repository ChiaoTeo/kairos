from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from kairospy.application.system import (
    AccountSystemClient,
    ComponentControlApplication,
    ComponentProcessApplication,
    ExecutionSystemClient,
    MarketSystemClient,
    ReferenceSystemClient,
    RiskSystemClient,
)


@dataclass(frozen=True, slots=True)
class RecordingAccountClient(AccountSystemClient):
    calls: list[tuple[str, str, object]] = field(default_factory=list)

    def __init__(self) -> None:
        super().__init__(Path("/tmp/account.sock"))
        object.__setattr__(self, "calls", [])

    def request(self, method: str, path: str, body: object = None) -> dict[str, object]:
        self.calls.append((method, path, body))
        return {"status": "ok"}


def test_account_system_client_keeps_only_control_and_reconciliation_queries() -> None:
    client = RecordingAccountClient()
    client.reconcile()
    assert client.calls == [("POST", "/v1/reconcile", None)]


def test_system_rest_client_rejects_business_get_queries() -> None:
    client = AccountSystemClient(Path("/tmp/account.sock"))
    with pytest.raises(ValueError, match="only REST query"):
        client.request("GET", "/v1/orders")


def test_execution_system_client_owns_intent_endpoint() -> None:
    @dataclass(frozen=True, slots=True)
    class RecordingExecutionClient(ExecutionSystemClient):
        calls: list[tuple[str, str, object]] = field(default_factory=list)

        def __init__(self) -> None:
            super().__init__(Path("/tmp/execution.sock"))
            object.__setattr__(self, "calls", [])

        def request(self, method, path, body=None):
            self.calls.append((method, path, body))
            return {"status": "ok"}

    client = RecordingExecutionClient()
    client.submit_intent({"intent_id": "i-1"})
    assert client.calls == [("POST", "/v1/intents", {"intent_id": "i-1"})]


def test_system_process_factory_returns_typed_business_clients() -> None:
    socket = Path("/tmp/component.sock")
    factory = ComponentProcessApplication.client

    assert isinstance(factory("account", socket), AccountSystemClient)
    assert isinstance(factory("execution", socket), ExecutionSystemClient)
    assert isinstance(factory("market", socket), MarketSystemClient)
    assert isinstance(factory("risk", socket), RiskSystemClient)
    assert isinstance(factory("reference", socket), ReferenceSystemClient)
    assert isinstance(factory("control", socket), ComponentControlApplication)
