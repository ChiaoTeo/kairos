from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone

from kairospy.infrastructure.integrations.application.account import ConnectionAccountStreamRequest
from kairospy.domain.account import AccountSnapshot
from kairospy.domain.execution import ExecutionUpdate
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from .client import BinanceSpotRestClient
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from .endpoints import BinanceSpotEndpoint, BinanceSpotEndpointKind
from .operations import BinanceSpotAccountOperations
from .stream import BinanceSpotUserStream
from kairospy.infrastructure.integrations.services.gateways.binance.spot.normalizers import BinanceSpotNormalizers


class _BinanceSpotUserStreamConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        client = BinanceSpotRestClient(
            credential_id=spec.credential.id if spec.credential else None,
            endpoint=BinanceSpotEndpoint(BinanceSpotEndpointKind.PRIVATE_REST, "https://api.binance.com"),
        )
        self.account_operations = BinanceSpotAccountOperations(client)
        self.user_stream = BinanceSpotUserStream()
        self.normalizers = BinanceSpotNormalizers()
        super().__init__(spec, components=(self.user_stream,))

    async def _user_events(self):
        listen_key = self.account_operations.create_listen_key()
        async for event in self.user_stream.events(listen_key):
            yield event

    async def _account_snapshots(self, request: ConnectionAccountStreamRequest) -> AsyncIterator[AccountSnapshot]:
        async for event in self._user_events():
            if str(event.get("e") or "") not in {"outboundAccountPosition", "balanceUpdate"}:
                continue
            yield self.normalizers.balance_snapshot(request.context, _balance_event(event), at=datetime.now(timezone.utc), open_orders=request.open_orders)

    async def _execution_updates(self, request: ConnectionAccountStreamRequest, *, trades_only: bool = False) -> AsyncIterator[ExecutionUpdate]:
        async for event in self._user_events():
            if str(event.get("e") or "") != "executionReport":
                continue
            if request.symbol is not None and str(event.get("s") or "") != request.symbol:
                continue
            if trades_only and event.get("x") != "TRADE":
                continue
            yield self.normalizers.execution_update(event, context=request.context)


class BinanceSpotAccountStreamConnection(_BinanceSpotUserStreamConnection):
    async def account_snapshots(self, request: ConnectionAccountStreamRequest) -> AsyncIterator[AccountSnapshot]:
        async for snapshot in self._account_snapshots(request):
            yield snapshot


class BinanceSpotExecutionStreamConnection(_BinanceSpotUserStreamConnection):
    async def execution_updates(self, request: ConnectionAccountStreamRequest, *, trades_only: bool = False) -> AsyncIterator[ExecutionUpdate]:
        async for update in self._execution_updates(request, trades_only=trades_only):
            yield update


class BinanceSpotAccountStreamGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceSpotAccountStreamConnection:
        if spec.product is not ProductFamily.SPOT or spec.access is not AccessScope.PRIVATE or spec.transport is not TransportKind.USER_STREAM:
            raise ValueError("Binance Spot account stream gateway received an incompatible connection spec")
        return BinanceSpotAccountStreamConnection(spec)


class BinanceSpotExecutionStreamGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceSpotExecutionStreamConnection:
        if spec.product is not ProductFamily.SPOT or spec.access is not AccessScope.PRIVATE or spec.transport is not TransportKind.USER_STREAM:
            raise ValueError("Binance Spot execution stream gateway received an incompatible connection spec")
        return BinanceSpotExecutionStreamConnection(spec)


def _balance_event(event: Mapping[str, object]) -> dict[str, object]:
    balances = []
    for value in event.get("B", ()) if isinstance(event.get("B"), list) else ():
        if isinstance(value, Mapping):
            balances.append({"asset": value.get("a"), "free": value.get("f"), "locked": value.get("l")})
    return {"balances": balances}


__all__ = [
    "BinanceSpotAccountStreamConnection",
    "BinanceSpotAccountStreamGateway",
    "BinanceSpotExecutionStreamConnection",
    "BinanceSpotExecutionStreamGateway",
]
