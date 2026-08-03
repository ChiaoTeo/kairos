from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone

from kairospy.infrastructure.integrations.application.account import ConnectionAccountStreamRequest
from kairospy.domain.account import AccountSnapshot
from kairospy.domain.execution import ExecutionUpdate
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.services.clients.binance_spot import BinanceSpotRestClient
from kairospy.infrastructure.integrations.services.connections.base import ConnectionService
from kairospy.infrastructure.integrations.services.endpoints.binance_spot import BinanceSpotEndpoint, BinanceSpotEndpointKind
from kairospy.infrastructure.integrations.services.operations.binance_spot import BinanceSpotAccountOperations
from kairospy.infrastructure.integrations.services.streams.binance_spot import BinanceSpotUserStream
from kairospy.infrastructure.integrations.services.translators.binance_spot import BinanceSpotPayloadTranslator


class BinanceSpotPrivateStreamConnection(ConnectionService):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        client = BinanceSpotRestClient(
            credential_id=spec.credential.id if spec.credential else None,
            endpoint=BinanceSpotEndpoint(BinanceSpotEndpointKind.PRIVATE_ACCOUNT_REST, "https://api.binance.com"),
        )
        self.account_operations = BinanceSpotAccountOperations(client)
        self.user_stream = BinanceSpotUserStream()
        self.translator = BinanceSpotPayloadTranslator()
        super().__init__(spec, components=(self.user_stream,))

    async def _user_events(self):
        listen_key = self.account_operations.create_listen_key()
        async for event in self.user_stream.events(listen_key):
            yield event

    async def account_snapshots(self, request: ConnectionAccountStreamRequest) -> AsyncIterator[AccountSnapshot]:
        async for event in self._user_events():
            if str(event.get("e") or "") not in {"outboundAccountPosition", "balanceUpdate"}:
                continue
            yield self.translator.balance_snapshot(request.context, _balance_event(event), at=datetime.now(timezone.utc), open_orders=request.open_orders)

    async def execution_updates(self, request: ConnectionAccountStreamRequest, *, trades_only: bool = False) -> AsyncIterator[ExecutionUpdate]:
        async for event in self._user_events():
            if str(event.get("e") or "") != "executionReport":
                continue
            if request.symbol is not None and str(event.get("s") or "") != request.symbol:
                continue
            if trades_only and event.get("x") != "TRADE":
                continue
            yield self.translator.execution_update(event, context=request.context)


def _balance_event(event: Mapping[str, object]) -> dict[str, object]:
    balances = []
    for value in event.get("B", ()) if isinstance(event.get("B"), list) else ():
        if isinstance(value, Mapping):
            balances.append({"asset": value.get("a"), "free": value.get("f"), "locked": value.get("l")})
    return {"balances": balances}


__all__ = ["BinanceSpotPrivateStreamConnection"]
