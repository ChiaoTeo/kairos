from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable, Mapping

from kairospy.integrations.drivers import CcxtDriver


@dataclass(frozen=True, slots=True)
class BinanceBroker:
    driver: CcxtDriver = field(default_factory=CcxtDriver)
    name: str = "binance"
    exchange_id: str = "binance"

    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
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
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        return self.driver.cancel_order(self.exchange_id, id, symbol=symbol, params=params)

    def fetch_balance(self, *, params: Mapping[str, object] | None = None) -> Mapping[str, object]:
        return self.driver.fetch_balance(self.exchange_id, params=params)

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        return self.driver.fetch_open_orders(
            self.exchange_id,
            symbol,
            since=since,
            limit=limit,
            params=params,
        )

    def watch_balance(
        self,
        *,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self.driver.watch_balance(self.exchange_id, params=params)

    def watch_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
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
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        return self.driver.watch_my_trades(
            self.exchange_id,
            symbol,
            since=since,
            limit=limit,
            params=params,
        )


__all__ = ["BinanceBroker"]
