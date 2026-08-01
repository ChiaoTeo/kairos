from __future__ import annotations

from dataclasses import dataclass, field

from kairospy.infrastructure.integrations.connectors.exchange.okx.market_data import okx_ccxt_driver
from kairospy.infrastructure.integrations.drivers import CcxtDriver
from kairospy.infrastructure.integrations.types import IntegrationParams, OrderSubmissionResponse, RawPayload, RawPayloadRows, RawPayloadStream


@dataclass(frozen=True, slots=True)
class OkxBroker:
    driver: CcxtDriver = field(default_factory=okx_ccxt_driver)
    name: str = "okx"
    exchange_id: str = "okx"

    @classmethod
    def from_credential(cls, credential: str | None) -> "OkxBroker":
        return cls(okx_ccxt_driver(credential))

    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        return self.driver.create_order(
            self.exchange_id,
            symbol,
            side=side,
            type=type,
            amount=amount,
            price=price,
            params=params,
        )

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        return self.driver.cancel_order(self.exchange_id, id, symbol=symbol, params=params)

    def fetch_balance(self, *, params: IntegrationParams | None = None) -> RawPayload:
        return self.driver.fetch_balance(self.exchange_id, params=params)

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self.driver.fetch_open_orders(
            self.exchange_id,
            symbol,
            since=since,
            limit=limit,
            params=params,
        )

    def fetch_closed_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        return self.driver.fetch_closed_orders(
            self.exchange_id,
            symbol,
            since=since,
            limit=limit,
            params=params,
        )

    def watch_balance(
        self,
        *,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        return self.driver.watch_balance(self.exchange_id, params=params)

    def watch_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        return self.driver.watch_orders(
            self.exchange_id,
            symbol,
            since=since,
            limit=limit,
            params=params,
        )

    def watch_my_trades(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadStream:
        return self.driver.watch_my_trades(
            self.exchange_id,
            symbol,
            since=since,
            limit=limit,
            params=params,
        )


__all__ = ["OkxBroker"]
