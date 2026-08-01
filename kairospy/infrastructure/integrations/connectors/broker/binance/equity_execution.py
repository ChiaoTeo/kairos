from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .sapi import BinanceSapiClient
from kairospy.infrastructure.integrations.payloads.binance_equity_execution import (
    binance_equity_cancel_order_params,
    binance_equity_create_order_params,
)
from kairospy.infrastructure.integrations.payloads.types import IntegrationParams, OrderSubmissionResponse, RawPayload, RawPayloadRows


@dataclass(frozen=True, slots=True)
class BinanceEquityBroker:
    client: BinanceSapiClient = field(default_factory=BinanceSapiClient)
    name: str = "binance_equity"

    @classmethod
    def from_credential(cls, credential: str | None) -> "BinanceEquityBroker":
        return cls(BinanceSapiClient.from_credential(credential))

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
        payload = self.client.post(
            "/sapi/v1/equity/order/place",
            params=binance_equity_create_order_params(symbol, side=side, type=type, amount=amount, price=price, params=params),
            signed=True,
            api_key=True,
        )
        return _order_response(payload)

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: IntegrationParams | None = None,
    ) -> OrderSubmissionResponse:
        payload = self.client.post(
            "/sapi/v1/equity/order/cancel",
            params=binance_equity_cancel_order_params(id, symbol=symbol, params=params),
            signed=True,
            api_key=True,
        )
        return _order_response(payload, fallback_id=id)

    def fetch_balance(self, *, params: IntegrationParams | None = None) -> RawPayload:
        return {}

    def fetch_open_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        query = {"symbol": None if symbol is None else str(symbol).strip().upper(), **dict(params or {})}
        payload = self.client.get("/sapi/v1/equity/order/open", params=query, signed=True, api_key=True)
        if isinstance(payload, list | tuple):
            return tuple(item for item in payload if isinstance(item, Mapping))
        if isinstance(payload, Mapping):
            rows = payload.get("orders") or payload.get("data")
            if isinstance(rows, list | tuple):
                return tuple(item for item in rows if isinstance(item, Mapping))
        return ()

    def fetch_closed_orders(
        self,
        symbol: str | None = None,
        *,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        query = {"symbol": None if symbol is None else str(symbol).strip().upper(), **dict(params or {})}
        if since is not None:
            query.setdefault("startTime", since)
        if limit is not None:
            query.setdefault("limit", limit)
        payload = self.client.get("/sapi/v1/equity/order/history", params=query, signed=True, api_key=True)
        if isinstance(payload, list | tuple):
            return tuple(item for item in payload if isinstance(item, Mapping))
        if isinstance(payload, Mapping):
            rows = payload.get("orders") or payload.get("data")
            if isinstance(rows, list | tuple):
                return tuple(item for item in rows if isinstance(item, Mapping))
        return ()

    def fetch_order_detail(
        self,
        order_id: str,
        *,
        symbol: str | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayload:
        query = {"orderId": str(order_id).strip(), "symbol": None if symbol is None else str(symbol).strip().upper(), **dict(params or {})}
        payload = self.client.get("/sapi/v1/equity/order/detail", params=query, signed=True, api_key=True)
        return dict(payload) if isinstance(payload, Mapping) else {"raw": payload}

    def fetch_trade_history(
        self,
        symbol: str | None = None,
        *,
        order_id: str | None = None,
        since: object | None = None,
        limit: int | None = None,
        params: IntegrationParams | None = None,
    ) -> RawPayloadRows:
        query = {
            "symbol": None if symbol is None else str(symbol).strip().upper(),
            "orderId": None if order_id is None else str(order_id).strip(),
            **dict(params or {}),
        }
        if since is not None:
            query.setdefault("startTime", since)
        if limit is not None:
            query.setdefault("limit", limit)
        payload = self.client.get("/sapi/v1/equity/trade/history", params=query, signed=True, api_key=True)
        if isinstance(payload, list | tuple):
            return tuple(item for item in payload if isinstance(item, Mapping))
        if isinstance(payload, Mapping):
            rows = payload.get("trades") or payload.get("data")
            if isinstance(rows, list | tuple):
                return tuple(item for item in rows if isinstance(item, Mapping))
        return ()

    def sign_disclaimer(self) -> RawPayload:
        payload = self.client.post("/sapi/v1/equity/account/disclaimer", signed=True, api_key=True)
        return dict(payload) if isinstance(payload, Mapping) else {"raw": payload}


def _order_response(payload: object, *, fallback_id: str = "") -> OrderSubmissionResponse:
    if isinstance(payload, Mapping):
        response = dict(payload)
    else:
        response = {"raw": payload}
    order_id = str(response.get("orderId") or response.get("id") or fallback_id).strip()
    if order_id:
        response.setdefault("id", order_id)
        response.setdefault("orderId", order_id)
    return response


__all__ = ["BinanceEquityBroker"]
