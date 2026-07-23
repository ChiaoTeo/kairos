from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from kairospy.environment import Environment
from kairospy.identity import AssetId, InstitutionId, InstrumentId, VenueId
from kairospy.portfolio.account_ports import AccountState, VenueBalance

from .symbol_mapper import CcxtSymbolMapper


class CcxtAccountGateway:
    service_id = "account"
    service_kind = "account"

    def __init__(
        self,
        exchange: Any,
        *,
        provider: str,
        environment: Environment,
        symbol_mapper: CcxtSymbolMapper | None = None,
    ) -> None:
        self.exchange = exchange
        self.institution_id = InstitutionId(provider)
        self.venue_id = VenueId(provider)
        self.environment = environment
        self.symbol_mapper = symbol_mapper or CcxtSymbolMapper({})

    def account_state(self, account) -> AccountState:
        row = self.exchange.fetch_balance()
        balances = tuple(_balance(asset, values) for asset, values in sorted(_balance_rows(row).items()))
        positions = tuple(_position(item, self.symbol_mapper) for item in row.get("info", {}).get("positions", ()) if _position_size(item) != 0)
        orders = self.exchange.fetch_open_orders() if hasattr(self.exchange, "fetch_open_orders") else ()
        return AccountState(
            account,
            balances,
            tuple(item for item in positions if item is not None),
            tuple(str(item.get("id") or item.get("orderId")) for item in orders if item.get("id") or item.get("orderId")),
            datetime.now(timezone.utc),
        )


def _balance_rows(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    free = row.get("free", {}) or {}
    used = row.get("used", {}) or {}
    total = row.get("total", {}) or {}
    assets = set(free) | set(used) | set(total)
    return {
        str(asset): {
            "free": free.get(asset, 0),
            "used": used.get(asset, 0),
            "total": total.get(asset, Decimal(str(free.get(asset, 0))) + Decimal(str(used.get(asset, 0)))),
        }
        for asset in assets
        if Decimal(str(total.get(asset, Decimal(str(free.get(asset, 0))) + Decimal(str(used.get(asset, 0)))))) != 0
    }


def _balance(asset: str, values: dict[str, Any]) -> VenueBalance:
    available = Decimal(str(values.get("free", 0) or 0))
    locked = Decimal(str(values.get("used", 0) or 0))
    total = Decimal(str(values.get("total", available + locked) or 0))
    return VenueBalance(AssetId(asset), total, available, locked)


def _position_size(item: dict[str, Any]) -> Decimal:
    return Decimal(str(item.get("contracts", item.get("positionAmt", item.get("size", 0))) or 0))


def _position(item: dict[str, Any], mapper: CcxtSymbolMapper) -> tuple[InstrumentId, Decimal] | None:
    symbol = item.get("symbol") or item.get("info", {}).get("symbol")
    if not symbol:
        return None
    try:
        instrument_id = mapper.instrument_for(str(symbol))
    except LookupError:
        instrument_id = InstrumentId(f"crypto:ccxt:{symbol}")
    return instrument_id, _position_size(item)
