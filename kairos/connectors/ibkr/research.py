from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from time import sleep
from typing import Any, Protocol
from uuid import UUID

from kairos.domain.event import GreeksUpdated, MarketEvent, QuoteUpdated, TradeUpdated, UnderlyingPriceUpdated, envelope
from kairos.domain.identity import AssetId, InstrumentId, VenueId
from kairos.domain.market_data import Greeks, OptionChain, Quote, Trade
from kairos.domain.product import IndexSpec, ListedOptionSpec, OptionRight, ProductType
from kairos.research_platform.spec import OptionChainCaptureSpec
from kairos.reference import (
    AssetDefinition, AssetType, ListingDefinition, ListingId, ReferenceCatalog,
    TradingRules, VenueDefinition, VenueType,
)
from kairos.reference.factory import publish_instrument
from kairos.reference.contracts import InstrumentDefinition


class SpxwResearchProvider(Protocol):
    catalog: ReferenceCatalog
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def underlying(self, spec: OptionChainCaptureSpec) -> InstrumentDefinition: ...
    def qualify(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[InstrumentDefinition, ...]: ...
    def discover_option_chain(self, underlying: InstrumentDefinition, spec: OptionChainCaptureSpec) -> OptionChain: ...
    def snapshot(self, instruments: tuple[InstrumentDefinition, ...], correlation_id: UUID) -> list[MarketEvent]: ...


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation, OverflowError):
        return None


class IbkrSpxwResearchProvider:
    """IBKR research provider that exposes only normalized definitions and events."""

    venue_id = VenueId("ibkr")

    def __init__(self, spec: OptionChainCaptureSpec, *, host: str = "127.0.0.1", port: int = 4001, client_id: int = 21, readonly: bool = True) -> None:
        from ib_async import IB
        self.spec = spec
        self.host, self.port, self.client_id, self.readonly = host, port, client_id, readonly
        self._ib = IB()
        self._ib.RequestTimeout = spec.quote_timeout_seconds
        self._contracts: dict[InstrumentId, Any] = {}
        self.catalog = ReferenceCatalog()

    def connect(self) -> None:
        self._ib.connect(self.host, self.port, clientId=self.client_id, readonly=self.readonly)
        self._ib.reqMarketDataType(self.spec.market_data_type.ibkr_code)

    def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()

    def underlying(self, spec: OptionChainCaptureSpec) -> InstrumentDefinition:
        from ib_async import Index
        contract_request = Index(spec.underlying, spec.underlying_exchange, spec.currency)
        for attempt in range(3):
            try:
                qualified = self._ib.qualifyContracts(contract_request)
                break
            except TimeoutError as error:
                if attempt == 2:
                    raise TimeoutError(
                        f"IBKR timed out qualifying underlying {spec.underlying} after 3 attempts"
                    ) from error
                sleep(1)
        if not qualified:
            raise LookupError(f"underlying not found: {spec.underlying}")
        contract = qualified[0]
        instrument_id = InstrumentId(f"index:{spec.underlying.lower()}")
        self._contracts[instrument_id] = contract
        effective_from = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return publish_instrument(
            self.catalog, instrument_id=instrument_id, instrument_type=ProductType.INDEX,
            display_name=spec.underlying, contract_spec=IndexSpec(AssetId(spec.currency), spec.underlying_exchange),
            trading_currency=AssetId(spec.currency), listings=(ListingDefinition(
                ListingId(f"listing:{self.venue_id.value}:{instrument_id.value}"), instrument_id, self.venue_id,
                contract.localSymbol or spec.underlying, AssetId(spec.currency),
                TradingRules(Decimal("0.01"), Decimal("1"), Decimal("1")), effective_from,
                venue_instrument_id=str(contract.conId),
            ),), effective_from=effective_from,
            asset_definitions=(AssetDefinition(AssetId(spec.currency), AssetType.FIAT, spec.currency, effective_from, decimals=2),),
            venue_definitions=(VenueDefinition(self.venue_id, VenueType.EXCHANGE, "IBKR SMART", "UTC", effective_from),),
        )

    def qualify(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[InstrumentDefinition, ...]:
        contracts = [self._to_contract(item) for item in instruments]
        qualified = self._ib.qualifyContracts(*contracts)
        # IBKR option-chain strikes are aggregated across expirations. Some
        # requested expiry/strike combinations therefore do not exist, and
        # ib_async keeps a None placeholder for each failed qualification.
        by_signature = {
            _contract_signature(contract): contract
            for contract in qualified
            if contract is not None
        }
        results = []
        for definition in instruments:
            contract = by_signature.get(_definition_signature(definition))
            if contract is None:
                continue
            self._contracts[definition.instrument_id] = contract
            results.append(definition)
        return tuple(results)

    def discover_option_chain(self, underlying: InstrumentDefinition, spec: OptionChainCaptureSpec) -> OptionChain:
        contract = self._contracts.get(underlying.instrument_id)
        if contract is None:
            raise LookupError("underlying must be qualified before chain discovery")
        chains = self._ib.reqSecDefOptParams(spec.underlying, "", "IND", contract.conId)
        candidates = [item for item in chains if item.tradingClass == spec.trading_class]
        preferred = [item for item in candidates if item.exchange == spec.exchange]
        if not preferred and not candidates:
            raise LookupError(f"option chain not found: {spec.trading_class}")
        selected = (preferred or candidates)[0]
        return OptionChain(
            underlying.instrument_id, self.venue_id, selected.exchange, selected.tradingClass,
            decimal_or_none(selected.multiplier) or Decimal("100"),
            tuple(sorted(date.fromisoformat(value) for value in selected.expirations)),
            tuple(sorted(item for item in (decimal_or_none(value) for value in selected.strikes) if item is not None)),
        )

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...], correlation_id: UUID) -> list[MarketEvent]:
        contracts = []
        for definition in instruments:
            contract = self._contracts.get(definition.instrument_id)
            if contract is None:
                raise LookupError(f"instrument is not qualified: {definition.instrument_id}")
            contracts.append(contract)
        tickers = self._ib.reqTickers(*contracts)
        by_con_id = {contract.conId: definition for contract, definition in zip(contracts, instruments)}
        source = f"ibkr.{self.spec.market_data_type.value}"
        events: list[MarketEvent] = []
        for ticker in tickers:
            definition = by_con_id[ticker.contract.conId]
            event_time = ticker.time if isinstance(ticker.time, datetime) else datetime.now(timezone.utc)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            if definition.instrument_type is ProductType.INDEX:
                price = decimal_or_none(ticker.marketPrice())
                if price is not None and price > 0:
                    events.append(envelope(UnderlyingPriceUpdated(definition.instrument_id, price), source=source, event_time=event_time, correlation_id=correlation_id))
                continue
            events.append(envelope(QuoteUpdated(Quote(
                definition.instrument_id, decimal_or_none(ticker.bid), decimal_or_none(ticker.ask),
                decimal_or_none(ticker.bidSize), decimal_or_none(ticker.askSize), event_time,
            )), source=source, event_time=event_time, correlation_id=correlation_id))
            last = decimal_or_none(ticker.last)
            if last is not None:
                events.append(envelope(TradeUpdated(Trade(
                    definition.instrument_id, last, decimal_or_none(ticker.lastSize) or Decimal("0"), event_time,
                )), source=source, event_time=event_time, correlation_id=correlation_id))
            model = ticker.modelGreeks
            if model is not None:
                events.append(envelope(GreeksUpdated(Greeks(
                    definition.instrument_id, decimal_or_none(model.impliedVol), decimal_or_none(model.delta),
                    decimal_or_none(model.gamma), decimal_or_none(model.theta), decimal_or_none(model.vega), event_time,
                )), source=source, event_time=event_time, correlation_id=correlation_id))
        return events

    def _to_contract(self, definition: InstrumentDefinition) -> Any:
        if not isinstance(definition.contract_spec, ListedOptionSpec):
            raise ValueError("IBKR option qualification requires ListedOptionSpec")
        from ib_async import Option
        spec = definition.contract_spec
        product = self.catalog.products.get(definition.product_id, definition.effective_from)
        return Option(
            symbol="SPX", lastTradeDateOrContractMonth=spec.expiry.strftime("%Y%m%d"),
            strike=float(spec.strike), right="C" if spec.right is OptionRight.CALL else "P",
            exchange="SMART", currency=product.currency.value,
            multiplier=format(spec.multiplier, "f"), tradingClass=definition.display_name or "SPXW",
        )


def _definition_signature(definition: InstrumentDefinition) -> tuple[str, Decimal, str]:
    spec = definition.contract_spec
    if not isinstance(spec, ListedOptionSpec):
        raise ValueError("option definition required")
    return spec.expiry.strftime("%Y%m%d"), spec.strike, "C" if spec.right is OptionRight.CALL else "P"


def _contract_signature(contract: Any) -> tuple[str, Decimal, str]:
    return contract.lastTradeDateOrContractMonth[:8], Decimal(str(contract.strike)), contract.right
