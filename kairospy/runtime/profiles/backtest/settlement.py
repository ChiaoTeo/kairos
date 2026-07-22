from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5, NAMESPACE_URL

from kairospy.execution.fills import Settlement
from kairospy.reference.contracts import OptionRight, is_option_spec, option_multiplier
from kairospy.reference.access import contract_spec, definition_at

from .portfolio import BacktestPortfolio

from kairospy.market.snapshots import InstrumentLifecycleSnapshot


def intrinsic_value(right: OptionRight, strike: Decimal, settlement_price: Decimal) -> Decimal:
    if right is OptionRight.CALL:
        return max(Decimal("0"), settlement_price - strike)
    return max(Decimal("0"), strike - settlement_price)


def due_settlements(portfolio: BacktestPortfolio, metadata: tuple[InstrumentLifecycleSnapshot, ...], now: datetime) -> tuple[Settlement, ...]:
    by_id = {item.instrument_id: item for item in metadata}
    results = []
    for structure_id, structure in list(portfolio.structures.items()):
        for instrument_id, _ in structure.legs:
            position = portfolio.positions.get(instrument_id)
            if not position or position.quantity == 0:
                continue
            contract = by_id.get(instrument_id)
            if contract is None or now < contract.settlement_at:
                continue
            if contract.official_settlement is None:
                raise ValueError(f"missing official settlement: {instrument_id.value}")
            if not contract.settlement_confirmed:
                raise ValueError(f"unconfirmed settlement metadata: {instrument_id.value}")
            definition = definition_at(portfolio.catalog, instrument_id, now)
            if not is_option_spec(contract_spec(definition)):
                raise ValueError("option settlement requires right and strike")
            spec = contract_spec(definition)
            intrinsic = intrinsic_value(spec.right, spec.strike, contract.official_settlement)
            multiplier = option_multiplier(spec)
            cash_delta = Decimal(position.quantity) * intrinsic * multiplier
            settlement_id = uuid5(NAMESPACE_URL, f"settlement:{structure_id}:{instrument_id.value}:{now.isoformat()}")
            results.append(Settlement(settlement_id, structure_id, instrument_id, now, contract.official_settlement, intrinsic, position.quantity, cash_delta))
    return tuple(results)
