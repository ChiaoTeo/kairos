"""Strategy-owned read model joining Reference option identities and Market quotes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from kairospy.domain.market import MarketViewReader
from kairospy.domain.reference import OptionContractRef

from ..domain.crash_put import CrashPutCandidate


@dataclass(frozen=True, slots=True)
class OptionChainMarketItem:
    contract: OptionContractRef
    bid: Decimal | None
    ask: Decimal | None
    observed_at: datetime | None
    implied_volatility: Decimal | None = None

    @property
    def midpoint(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / Decimal("2")


@dataclass(frozen=True, slots=True)
class OptionChainView:
    underlying: Decimal | None
    as_of: datetime | None
    contracts: tuple[OptionChainMarketItem, ...]

    def crash_put_candidates(self) -> tuple[CrashPutCandidate, ...]:
        if self.underlying is None:
            return ()
        return tuple(
            CrashPutCandidate(
                contract=str(item.contract.market.source_symbol),
                expiry=item.contract.expiry.date(),
                strike=item.contract.strike,
                premium=item.midpoint or Decimal("0"),
                bid=item.bid,
                ask=item.ask,
                underlying=self.underlying,
                implied_volatility=item.implied_volatility,
            )
            for item in self.contracts
            if item.contract.right == "put"
        )


def build_option_chain_view(
    contracts: Iterable[OptionContractRef],
    market: MarketViewReader,
    *,
    underlying: Decimal | None = None,
) -> OptionChainView:
    items: list[OptionChainMarketItem] = []
    as_of: datetime | None = None
    for contract in contracts:
        quote = market.quotes(contract.market).latest
        bar = None if quote is not None else market.bars(contract.market, timeframe="1d").latest
        if quote is None and bar is None:
            continue
        observed = quote.time if quote is not None else bar.time
        as_of = observed if as_of is None or observed > as_of else as_of
        items.append(OptionChainMarketItem(
            contract=contract,
            bid=quote.bid if quote is not None else bar.close,
            ask=quote.ask if quote is not None else bar.close,
            observed_at=observed,
        ))
    return OptionChainView(underlying=underlying, as_of=as_of, contracts=tuple(items))


__all__ = ["OptionChainMarketItem", "OptionChainView", "build_option_chain_view"]
