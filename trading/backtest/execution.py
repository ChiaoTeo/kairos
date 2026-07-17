from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum
from typing import Callable
from uuid import UUID, uuid4

from trading.domain.execution import TradeSide
from trading.domain.intent import CloseStructureIntent, Intent
from trading.domain.order import Order, OrderStatus
from trading.domain.product import CryptoOptionSpec,ListedOptionSpec
from trading.reference import ReferenceCatalog
from trading.reference.access import contract_spec, definition_at

from .feed import MarketSlice


@dataclass(frozen=True, slots=True)
class ComboQuote:
    natural: Decimal
    midpoint: Decimal
    max_spread: Decimal
    sufficient_size: bool


def combo_quote(legs, market: MarketSlice, quantity: int) -> ComboQuote | None:
    snapshots = {item.instrument_id: item for item in market.instruments}
    natural = Decimal("0")
    midpoint = Decimal("0")
    max_spread = Decimal("0")
    sufficient = True
    for leg in legs:
        item = snapshots.get(leg.instrument_id)
        quote = item.quote if item else None
        if not quote or quote.bid is None or quote.ask is None or quote.bid > quote.ask:
            return None
        mid = (quote.bid + quote.ask) / 2
        executable = quote.ask if leg.side is TradeSide.BUY else quote.bid
        natural += Decimal(-leg.side.sign) * executable * leg.ratio
        midpoint += Decimal(-leg.side.sign) * mid * leg.ratio
        max_spread = max(max_spread, quote.ask - quote.bid)
        available = quote.ask_size if leg.side is TradeSide.BUY else quote.bid_size
        if available is None or available < Decimal(quantity * leg.ratio):
            sufficient = False
    return ComboQuote(natural, midpoint, max_spread, sufficient)


class ExecutionPlanner:
    def __init__(self, catalog: ReferenceCatalog, *, tick_size: Decimal = Decimal("0.05"), order_lifetime: timedelta = timedelta(hours=6), id_factory: Callable[[], UUID] = uuid4) -> None:
        self.catalog = catalog
        self.tick_size = tick_size
        self.order_lifetime = order_lifetime
        self.id_factory = id_factory

    def plan(self, intent: Intent, now: datetime) -> Order:
        definitions = tuple(definition_at(self.catalog, leg.instrument_id, now) for leg in intent.legs)
        if not all(isinstance(contract_spec(item), (ListedOptionSpec,CryptoOptionSpec)) for item in definitions):
            raise ValueError("combo planner requires option legs")
        if len({contract_spec(item).expiry for item in definitions}) != 1:
            raise ValueError("combo legs must share expiry")
        limit = self._round_limit(intent.limit_price) if intent.limit_price is not None else None
        structure_id = intent.structure_id if isinstance(intent, CloseStructureIntent) else self.id_factory()
        return Order(
            self.id_factory(), intent.intent_id, intent.strategy_id, structure_id, intent.legs, intent.quantity,
            limit, intent.time_in_force, now, now + timedelta(microseconds=1), now + self.order_lifetime,
            isinstance(intent, CloseStructureIntent), OrderStatus.CREATED,
        )

    def _round_limit(self, value: Decimal) -> Decimal:
        return (value / self.tick_size).to_integral_value(rounding=ROUND_FLOOR) * self.tick_size
