from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from kairospy.identity import AssetId, InstrumentId
from kairospy.reference.contracts import ListedOptionSpec, OptionRight
from kairospy.reference import ReferenceCatalog, SettlementMethod
from kairospy.reference import ReferenceRole


@dataclass(frozen=True, slots=True)
class AssetFlow:
    asset_id: AssetId
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PositionFlow:
    instrument_id: InstrumentId
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class SettlementResolution:
    instrument_id: InstrumentId
    position_quantity: Decimal
    settlement_price: Decimal
    intrinsic_value: Decimal
    flows: tuple[AssetFlow, ...]
    position_flows: tuple[PositionFlow, ...]
    settles_at: datetime | None


class SettlementResolver:
    def __init__(self, catalog: ReferenceCatalog) -> None:
        self.catalog = catalog

    def resolve(self, instrument_id: InstrumentId, position_quantity: Decimal, settlement_price: Decimal, at: datetime) -> SettlementResolution:
        if settlement_price < 0:
            raise ValueError("settlement price cannot be negative")
        definition = self.catalog.instruments.get(instrument_id, at)
        if not isinstance(definition.contract_spec, ListedOptionSpec):
            raise TypeError("settlement resolver currently requires listed option")
        if definition.settlement_terms_id is None:
            raise LookupError(f"instrument has no settlement terms: {instrument_id}")
        terms = self.catalog.settlements.get(definition.settlement_terms_id, at).terms
        spec = definition.contract_spec
        intrinsic = max(
            settlement_price - spec.strike if spec.right is OptionRight.CALL else spec.strike - settlement_price,
            Decimal("0"),
        )
        if position_quantity == 0 or intrinsic == 0:
            return SettlementResolution(instrument_id, position_quantity, settlement_price, intrinsic, (), (), terms.settlement_at)
        if terms.method is SettlementMethod.CASH:
            if terms.settlement_asset is None:
                raise ValueError("cash settlement has no settlement asset")
            flows = (AssetFlow(terms.settlement_asset, position_quantity * intrinsic * spec.multiplier),)
            position_flows = ()
        else:
            if terms.settlement_asset is None:
                raise ValueError("physical option settlement requires strike currency")
            direction = Decimal("1") if spec.right is OptionRight.CALL else Decimal("-1")
            flows = tuple(AssetFlow(item.asset_id, position_quantity * item.quantity * direction) for item in terms.deliverables)
            strike_cash = -position_quantity * spec.strike * spec.multiplier * direction
            flows = (*flows, AssetFlow(terms.settlement_asset, strike_cash))
            references = self.catalog.references(instrument_id, ReferenceRole.ECONOMIC_UNDERLYING, at)
            underlying = [item.target.instrument_id for item in references if item.target.instrument_id is not None]
            if len(underlying) != 1:
                raise LookupError(f"physical option requires one underlying instrument: {instrument_id}")
            delivered = sum((item.quantity for item in terms.deliverables), Decimal("0"))
            position_flows = (PositionFlow(underlying[0], position_quantity * delivered * direction),)
        return SettlementResolution(instrument_id, position_quantity, settlement_price, intrinsic, flows, position_flows, terms.settlement_at)
