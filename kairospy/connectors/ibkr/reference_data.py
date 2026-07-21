from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from kairospy.connectors.ibkr.option_chain_provider import decimal_or_none
from kairospy.trading.capability import ReferenceCapabilities
from kairospy.trading.identity import AssetId, InstrumentId, VenueId
from kairospy.trading.product import (
    EquitySpec,
    ExerciseStyle,
    IndexSpec,
    ListedOptionSpec,
    OptionRight,
    ProductType,
    SettlementSession,
    SettlementType,
)
from kairospy.ports import ReferenceDataRequest
from kairospy.reference import (
    AssetDefinition,
    AssetType,
    InstrumentDefinition,
    ListingDefinition,
    ListingId,
    MappingTargetType,
    ProviderId,
    ProviderSymbolMapping,
    ReferenceCatalog,
    TradingRules,
    VenueDefinition,
    VenueType,
)
from kairospy.reference.access import contract_spec
from kairospy.reference.factory import publish_instrument

from .session import IbkrSession


IBKR_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.EQUITY, ProductType.ETF, ProductType.LISTED_OPTION}),
)


class IbkrReferenceDataClient:
    venue_id = VenueId("ibkr")
    capabilities = IBKR_REFERENCE_CAPABILITIES

    def __init__(self, session: IbkrSession) -> None:
        self.session = session

    def sync(self, request: ReferenceDataRequest) -> ReferenceCatalog:
        if request.product_type in {ProductType.EQUITY, ProductType.ETF}:
            return self.sync_equities(request.symbols, product_type=request.product_type)
        if request.product_type is ProductType.LISTED_OPTION:
            return self.sync_listed_options(request.symbols)
        raise ValueError(f"IBKR reference sync does not support {request.product_type}")

    def sync_equities(self, symbols: tuple[str, ...], *, product_type: ProductType = ProductType.EQUITY) -> ReferenceCatalog:
        from ib_async import Stock
        self.session.connect()
        catalog = ReferenceCatalog()
        for symbol in symbols:
            qualified = self.session.ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
            if not qualified:
                raise LookupError(f"IBKR stock not found: {symbol}")
            contract = qualified[0]
            instrument_id = InstrumentId(f"equity:us:{symbol.upper()}")
            self.session.contracts[instrument_id] = contract
            effective_from = datetime.now(timezone.utc)
            venue_id = VenueId((contract.primaryExchange or "smart").lower())
            publish_instrument(
                catalog, instrument_id=instrument_id, instrument_type=product_type, display_name=symbol.upper(),
                contract_spec=EquitySpec(contract.primaryExchange or "SMART", "US", AssetId("USD")),
                trading_currency=AssetId("USD"), listings=(ListingDefinition(
                    ListingId(f"listing:{venue_id.value}:{instrument_id.value}"), instrument_id, venue_id,
                    contract.localSymbol or symbol, AssetId("USD"), TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")),
                    effective_from, venue_instrument_id=str(contract.conId),
                ),), effective_from=effective_from,
                asset_definitions=(
                    AssetDefinition(AssetId("USD"), AssetType.FIAT, "US Dollar", effective_from, decimals=2),
                    AssetDefinition(AssetId(symbol), AssetType.SECURITY, symbol.upper(), effective_from),
                ),
                venue_definitions=(VenueDefinition(venue_id, VenueType.EXCHANGE, venue_id.value, "UTC", effective_from),),
            )
            catalog.add_mapping(ProviderSymbolMapping(
                ProviderId("ibkr"), "conid", str(contract.conId), MappingTargetType.INSTRUMENT,
                instrument_id.value, effective_from,
            ))
        return catalog

    def sync_listed_options(self, descriptors: tuple[str, ...]) -> ReferenceCatalog:
        from ib_async import Option
        self.session.connect()
        catalog = ReferenceCatalog()
        for descriptor in descriptors:
            try:
                symbol, expiry_text, strike_text, right_text = descriptor.split(":")
                expiry = datetime.strptime(expiry_text, "%Y%m%d").replace(hour=16, tzinfo=timezone.utc)
                right = OptionRight.CALL if right_text.upper() == "C" else OptionRight.PUT if right_text.upper() == "P" else None
                if right is None:
                    raise ValueError
                strike = Decimal(strike_text)
            except (ValueError, TypeError) as error:
                raise ValueError("IBKR option descriptor must be SYMBOL:YYYYMMDD:STRIKE:C|P") from error
            qualified = self.session.ib.qualifyContracts(Option(symbol.upper(), expiry_text, float(strike), right_text.upper(), "SMART", currency="USD"))
            if not qualified:
                raise LookupError(f"IBKR option not found: {descriptor}")
            contract = qualified[0]
            underlying_id = InstrumentId(f"equity:us:{symbol.upper()}")
            instrument_id = InstrumentId(f"listed-option:us:{symbol.upper()}:{expiry_text}:{format(strike, 'f')}:{right.value}")
            self.session.contracts[instrument_id] = contract
            multiplier = decimal_or_none(contract.multiplier) or Decimal("100")
            effective_from = datetime.now(timezone.utc)
            venue_id = VenueId((getattr(contract, "exchange", None) or "smart").lower())
            publish_instrument(
                catalog, instrument_id=instrument_id, instrument_type=ProductType.LISTED_OPTION,
                display_name=contract.tradingClass or symbol.upper(),
                contract_spec=ListedOptionSpec(
                    underlying_id, expiry, strike, right, ExerciseStyle.AMERICAN,
                    SettlementType.PHYSICAL, SettlementSession.PM, multiplier, expiry,
                ),
                trading_currency=AssetId("USD"), listings=(ListingDefinition(
                    ListingId(f"listing:{venue_id.value}:{instrument_id.value}"), instrument_id, venue_id,
                    contract.localSymbol or descriptor, AssetId("USD"), TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")),
                    effective_from, venue_instrument_id=str(contract.conId),
                ),), effective_from=effective_from, trading_class=contract.tradingClass or symbol.upper(),
                asset_definitions=(
                    AssetDefinition(AssetId("USD"), AssetType.FIAT, "US Dollar", effective_from, decimals=2),
                    AssetDefinition(AssetId(symbol), AssetType.SECURITY, symbol.upper(), effective_from),
                ),
                venue_definitions=(VenueDefinition(venue_id, VenueType.EXCHANGE, venue_id.value, "UTC", effective_from),),
                physical_deliverable_asset=AssetId(symbol),
            )
            catalog.add_mapping(ProviderSymbolMapping(
                ProviderId("ibkr"), "conid", str(contract.conId), MappingTargetType.INSTRUMENT,
                instrument_id.value, effective_from,
            ))
        return catalog

    def bind_definition(self, definition: InstrumentDefinition, catalog=None) -> None:
        from ib_async import Index, Option, Stock
        if catalog is None:
            raise ValueError("binding an IBKR definition requires its ReferenceCatalog")
        product = catalog.products.get(definition.product_id, datetime.now(timezone.utc))
        currency = product.currency.value if product.currency is not None else "USD"
        spec = contract_spec(definition)
        if definition.instrument_type in {ProductType.EQUITY, ProductType.ETF}:
            contract = Stock(definition.display_name, "SMART", currency)
        elif definition.instrument_type is ProductType.INDEX:
            if not isinstance(spec, IndexSpec) or not spec.primary_exchange:
                raise ValueError("binding an IBKR index requires IndexSpec.primary_exchange")
            contract = Index(definition.display_name, spec.primary_exchange, currency)
        elif isinstance(spec, ListedOptionSpec):
            underlying = catalog.instruments.get(spec.underlying, datetime.now(timezone.utc))
            contract = Option(
                underlying.display_name, spec.expiry.strftime("%Y%m%d"), float(spec.strike),
                "C" if spec.right is OptionRight.CALL else "P", "SMART",
                currency=currency, multiplier=format(spec.multiplier, "f"),
                tradingClass=definition.display_name,
            )
        else:
            raise ValueError(f"IBKR execution cannot bind {definition.instrument_type}")
        self.session.connect()
        qualified = self.session.ib.qualifyContracts(contract)
        if not qualified:
            raise LookupError(f"IBKR contract not found for {definition.instrument_id}")
        self.session.contracts[definition.instrument_id] = qualified[0]
