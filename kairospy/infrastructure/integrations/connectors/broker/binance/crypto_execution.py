from __future__ import annotations

from dataclasses import dataclass, field

from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.drivers import CcxtDriver
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, OrderSubmissionResponse, RawPayload, RawPayloadRows, RawPayloadStream


@dataclass(frozen=True, slots=True)
class BinanceBroker:
    driver: CcxtDriver = field(default_factory=CcxtDriver)
    name: str = "binance"
    exchange_id: str = "binance"

    @classmethod
    def from_credential(cls, credential: str | None) -> "BinanceBroker":
        return cls(CcxtDriver(lambda exchange_id: _binance_exchange(exchange_id, credential=credential), lambda exchange_id: _binance_async_exchange(exchange_id, credential=credential)))

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


def _binance_config(credential: str | None = None) -> dict[str, object]:
    config: dict[str, object] = {"enableRateLimit": True}
    api_key = _credential_env(credential, "API_KEY", "BINANCE_API_KEY")
    secret = _credential_env(credential, "SECRET", "BINANCE_SECRET")
    if api_key:
        config["apiKey"] = api_key
    if secret:
        config["secret"] = secret
    return config


def _credential_env(credential: str | None, suffix: str, *fallbacks: str) -> str | None:
    return credential_value(credential, suffix, *fallbacks)


def _binance_exchange(exchange_id: str, *, credential: str | None = None):
    try:
        import ccxt
    except ImportError as error:
        raise RuntimeError("ccxt driver requires ccxt") from error
    return ccxt.binance(_binance_config(credential))


def _binance_async_exchange(exchange_id: str, *, credential: str | None = None):
    try:
        import ccxt.pro as ccxt_pro
    except ImportError:
        ccxt_pro = None
    if ccxt_pro is not None:
        return ccxt_pro.binance(_binance_config(credential))
    try:
        import ccxt.async_support as ccxt_async
    except ImportError as error:
        raise RuntimeError("ccxt live driver requires ccxt.pro or ccxt async_support") from error
    return ccxt_async.binance(_binance_config(credential))


__all__ = ["BinanceBroker"]
