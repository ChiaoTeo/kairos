from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from kairospy.domain.account import AccountBalance, AccountRuntimeContext, AccountSnapshot, AccountSource
from kairospy.infrastructure.integrations.application.account import ConnectionAccountReadData, ConnectionAccountReadRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection

from .spot.client import BinanceSpotRestClient


class BinanceFundingAccountConnection(Connection):
    """Read-only Binance Funding Wallet connection."""

    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.client = BinanceSpotRestClient(credential_id=spec.credential.id if spec.credential else None)
        super().__init__(spec, components=())

    def read_account(self, request: ConnectionAccountReadRequest) -> ConnectionAccountReadData:
        payload = self.client.post("/sapi/v1/asset/get-funding-asset", params={}, signed=True)
        rows = payload if isinstance(payload, list) else ()
        balances = tuple(
            AccountBalance.from_free_locked(
                str(row.get("asset")),
                _decimal(row.get("free")),
                _decimal(row.get("locked") or row.get("freeze")),
                source=AccountSource.VENUE,
            )
            for row in rows
            if isinstance(row, Mapping) and str(row.get("asset") or "").strip()
        )
        return ConnectionAccountReadData(
            snapshot=AccountSnapshot(
                request.context,
                balances=balances,
                observed_at=request.observed_at,
                source=AccountSource.VENUE,
            )
        )


class BinanceFundingAccountGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceFundingAccountConnection:
        if spec.product is not None or spec.access is not AccessScope.PRIVATE or spec.transport is not TransportKind.REST:
            raise ValueError("Binance Funding Wallet gateway received an incompatible connection spec")
        return BinanceFundingAccountConnection(spec)


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (TypeError, ValueError):
        return Decimal("0")


__all__ = ["BinanceFundingAccountConnection", "BinanceFundingAccountGateway"]
