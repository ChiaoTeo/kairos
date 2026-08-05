from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.domain.account import AccountModel, AccountSegment, ProductFamily


@dataclass(frozen=True, slots=True)
class AccountSegmentRoute:
    segment: AccountSegment
    balance_params: Mapping[str, object]
    order_params: Mapping[str, object]
    can_trade: bool


@dataclass(frozen=True, slots=True)
class AccountCapabilityPolicy:
    broker: str | None = None

    def can_trade(self, segment: AccountSegment | str) -> bool:
        kind = _segment_model(segment)
        return _can_trade(kind, broker=_broker_key(self.broker) if self.broker is not None else None)


@dataclass(frozen=True, slots=True)
class AccountSegmentRoutingService:
    broker: str | None = None
    base_params: Mapping[str, object] | None = None

    def route(
        self,
        segment: AccountSegment,
        *,
        broker: str | None = None,
        base_params: Mapping[str, object] | None = None,
    ) -> AccountSegmentRoute:
        broker_key = _broker_key(broker or self.broker or str(segment.broker))
        params = dict(self.base_params or {})
        params.update(dict(base_params or {}))
        return AccountSegmentRoute(
            segment,
            balance_params=params,
            order_params=dict(params),
            can_trade=AccountCapabilityPolicy(broker_key).can_trade(segment),
        )


def account_segment_route(
    segment: AccountSegment,
    *,
    broker: str | None = None,
    base_params: Mapping[str, object] | None = None,
) -> AccountSegmentRoute:
    return AccountSegmentRoutingService(broker=broker, base_params=base_params).route(segment)


def _normalize_kind(kind: str) -> str:
    value = kind.strip().lower()
    aliases = {
        "spot": ProductFamily.SPOT.value,
        # Equity is an asset type; a cash equity account uses SPOT.
        "equity": ProductFamily.SPOT.value,
        "stocks": ProductFamily.SPOT.value,
        "stock": ProductFamily.SPOT.value,
        "swap": "swap",
        "perp": "perp",
        "perpetual": "perp",
        "future": "future",
        "futures": ProductFamily.USD_M_FUTURES.value,
        "usd_m": ProductFamily.USD_M_FUTURES.value,
        "usdm": ProductFamily.USD_M_FUTURES.value,
        "usd_m_futures": ProductFamily.USD_M_FUTURES.value,
        "coin_m": ProductFamily.COIN_M_FUTURES.value,
        "coinm": ProductFamily.COIN_M_FUTURES.value,
        "coin_m_futures": ProductFamily.COIN_M_FUTURES.value,
        "margin": AccountModel.MARGIN.value,
        "cross_margin": AccountModel.MARGIN.value,
        "isolated_margin": AccountModel.MARGIN.value,
    }
    return aliases.get(value, value or AccountModel.NO_MARGIN.value)


def _can_trade(kind: str, *, broker: str | None = None) -> bool:
    normalized = _normalize_kind(kind)
    return normalized not in {"funding", "earn"}


def _segment_model(segment: AccountSegment | str) -> str:
    if not isinstance(segment, AccountSegment):
        return str(segment)
    value = segment.product_family or segment.model
    return value.value


def _broker_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")


__all__ = [
    "AccountSegmentRoute",
    "AccountSegmentRoutingService",
    "AccountCapabilityPolicy",
    "account_segment_route",
]
