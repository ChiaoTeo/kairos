from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import overload

from kairospy.domain_types import ExchangeId, InstrumentId, ListingId, MarketId
from kairospy.infrastructure.contracts.reference_client import ReferenceClient

from .models import InstrumentRef, Market, MarketStatus, TradingRules


class ReferenceNotFoundError(LookupError):
    pass


class AmbiguousReferenceError(LookupError):
    pass


class ReferenceApplication:
    """Read-only strategy-safe facade over Reference's current projection."""

    def __init__(self, client: ReferenceClient | None = None) -> None:
        self._client = client

    def find_markets(
        self,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        market_type: str | None = None,
        active_only: bool = True,
    ) -> tuple[Market, ...]:
        if self._client is None:
            raise RuntimeError("Reference application is unavailable")
        rows = self._client.markets(
            symbol=symbol,
            exchange_id=exchange,
            market_type=market_type,
            active_only=active_only,
        )
        return tuple(_market_from_row(row) for row in rows)

    @overload
    def require_market(self, market_id: MarketId, /) -> Market: ...

    @overload
    def require_market(
        self,
        *,
        symbol: str,
        exchange: str,
        market_type: str,
    ) -> Market: ...

    def require_market(
        self,
        market_id: MarketId | None = None,
        *,
        symbol: str | None = None,
        exchange: str | None = None,
        market_type: str | None = None,
    ) -> Market:
        matches = self.find_markets(
            symbol=symbol,
            exchange=exchange,
            market_type=market_type,
            active_only=False if market_id is not None else True,
        )
        if market_id is not None:
            matches = tuple(value for value in matches if value.id == market_id)
        filters = {
            "market_id": None if market_id is None else str(market_id),
            "symbol": symbol,
            "exchange": exchange,
            "market_type": market_type,
        }
        if not matches:
            raise ReferenceNotFoundError(f"Reference market not found: {filters}")
        if len(matches) != 1:
            raise AmbiguousReferenceError(
                f"Reference market is ambiguous ({len(matches)} matches): {filters}"
            )
        return matches[0]

    def market(self, market_id: MarketId) -> Market | None:
        try:
            return self.require_market(market_id)
        except ReferenceNotFoundError:
            return None


def _market_from_row(row: Mapping[str, object]) -> Market:
    def required(name: str) -> str:
        value = row.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Reference market is missing {name}")
        return value

    def optional_decimal(*names: str) -> Decimal | None:
        for name in names:
            value = row.get(name)
            if value is not None:
                return Decimal(str(value))
        return None

    instrument_id = required("instrument_id")
    symbol = required("symbol") if row.get("symbol") else required("source_symbol")
    raw_status = str(row.get("status", "unknown")).lower()
    status = (
        MarketStatus(raw_status)
        if raw_status in MarketStatus._value2member_map_
        else MarketStatus.UNKNOWN
    )
    return Market(
        id=MarketId(required("market_id")),
        instrument=InstrumentRef(InstrumentId(instrument_id), symbol),
        listing_id=ListingId(required("listing_id")),
        exchange_id=ExchangeId(required("exchange_id")),
        symbol=symbol,
        market_type=required("market_type"),
        base_asset=_optional_text(row.get("base_asset")),
        quote_asset=_optional_text(row.get("quote_asset")),
        status=status,
        trading_rules=TradingRules(
            price_increment=optional_decimal("price_increment", "tick_size"),
            quantity_increment=optional_decimal("quantity_increment", "step_size"),
            minimum_quantity=optional_decimal("minimum_quantity", "min_quantity"),
            minimum_notional=optional_decimal("minimum_notional", "min_notional"),
            contract_multiplier=optional_decimal("contract_multiplier"),
        ),
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
