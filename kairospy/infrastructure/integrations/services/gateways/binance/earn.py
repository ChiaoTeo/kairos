from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.earn.domain import EarnPosition, EarnProduct, EarnRedeemRequest, EarnReward, EarnSubscribeRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.infrastructure.integrations.services.gateways.binance.spot.client import BinanceSpotRestClient


class BinanceSimpleEarnConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.client = BinanceSpotRestClient(credential_id=spec.credential.id if spec.credential else None)
        super().__init__(spec, components=())

    def products(self, *, asset: str | None = None, product_type: str | None = None) -> Sequence[EarnProduct]:
        payload = self.client.get("/sapi/v1/simple-earn/products", params={"asset": asset, "productType": product_type}, signed=True)
        rows = payload.get("rows", ()) if isinstance(payload, Mapping) else ()
        return tuple(_product(row) for row in rows if isinstance(row, Mapping))

    def positions(self, *, asset: str | None = None) -> Sequence[EarnPosition]:
        payload = self.client.get("/sapi/v1/simple-earn/positions", params={"asset": asset}, signed=True)
        rows = payload.get("rows", ()) if isinstance(payload, Mapping) else ()
        return tuple(_position(row) for row in rows if isinstance(row, Mapping))

    def rewards(self, *, asset: str | None = None) -> Sequence[EarnReward]:
        payload = self.client.get("/sapi/v1/simple-earn/rewardsRecord", params={"asset": asset}, signed=True)
        rows = payload.get("rows", ()) if isinstance(payload, Mapping) else ()
        return tuple(_reward(row) for row in rows if isinstance(row, Mapping))

    def subscribe(self, request: EarnSubscribeRequest) -> object:
        return self.client.post("/sapi/v1/simple-earn/flexible/subscribe", params={"productId": request.product_id, "amount": str(request.amount)}, signed=True)

    def redeem(self, request: EarnRedeemRequest) -> object:
        return self.client.post("/sapi/v1/simple-earn/flexible/redeem", params={"productId": request.product_id, "amount": None if request.amount is None else str(request.amount), "destAccount": request.dest_account}, signed=True)


class BinanceSimpleEarnGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceSimpleEarnConnection:
        if spec.product is not ProductFamily.EARN or spec.access is not AccessScope.PRIVATE or spec.transport is not TransportKind.REST:
            raise ValueError("Binance Simple Earn gateway received an incompatible connection spec")
        return BinanceSimpleEarnConnection(spec)


def _product(row: Mapping[str, object]) -> EarnProduct:
    return EarnProduct(str(row.get("productId") or ""), str(row.get("asset") or ""), str(row.get("productType") or ""), _decimal(row.get("latestAnnualPercentageRate")), _decimal(row.get("minAmount")), _decimal(row.get("maxAmount")), str(row.get("status") or "unknown"))


def _position(row: Mapping[str, object]) -> EarnPosition:
    return EarnPosition(str(row.get("productId") or ""), str(row.get("asset") or ""), _decimal(row.get("totalAmount")), _decimal(row.get("latestAnnualPercentageRate")), str(row.get("status") or "unknown"), _time(row.get("updateTime")))


def _reward(row: Mapping[str, object]) -> EarnReward:
    return EarnReward(str(row.get("asset") or ""), _decimal(row.get("rewardsAmount")), str(row.get("productId")) if row.get("productId") is not None else None, _time(row.get("time")))


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (TypeError, ValueError):
        return Decimal("0")


def _time(value: object) -> datetime | None:
    try:
        return None if value is None else datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


__all__ = ["BinanceSimpleEarnConnection", "BinanceSimpleEarnGateway"]
