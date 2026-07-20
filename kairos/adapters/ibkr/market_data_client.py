from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal

from kairos.adapters.ibkr.research import decimal_or_none
from kairos.domain.capability import MarketDataCapabilities, MarketDataKind
from kairos.domain.identity import VenueId
from kairos.domain.market_data import Bar, Quote, Trade
from kairos.domain.product import ProductType
from kairos.reference import InstrumentDefinition

from .session import IbkrSession


IBKR_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.BAR, MarketDataKind.GREEKS, MarketDataKind.INDEX_PRICE}),
    product_types=frozenset({ProductType.INDEX, ProductType.EQUITY, ProductType.ETF, ProductType.LISTED_OPTION}),
    supports_native_greeks=True,
)


class IbkrMarketDataClient:
    venue_id = VenueId("ibkr")
    capabilities = IBKR_MARKET_DATA_CAPABILITIES

    def __init__(self, session: IbkrSession, market_data_type=3) -> None:
        self.session, self.market_data_type = session, market_data_type

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]:
        for definition in instruments:
            self.capabilities.require_product(definition.instrument_type)
        self.session.connect()
        self.session.ib.reqMarketDataType(self.market_data_type)
        contracts = [self.session.contracts[item.instrument_id] for item in instruments]
        tickers = self.session.ib.reqTickers(*contracts)
        result = []
        for definition, ticker in zip(instruments, tickers):
            event_time = ticker.time if isinstance(ticker.time, datetime) else datetime.now(timezone.utc)
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            result.append(Quote(definition.instrument_id, decimal_or_none(ticker.bid), decimal_or_none(ticker.ask), decimal_or_none(ticker.bidSize), decimal_or_none(ticker.askSize), event_time))
        return tuple(result)

    def recent_trades(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Trade, ...]:
        self.capabilities.require_market_data(MarketDataKind.TRADE)
        for definition in instruments:
            self.capabilities.require_product(definition.instrument_type)
        self.session.connect()
        self.session.ib.reqMarketDataType(self.market_data_type)
        contracts = [self.session.contracts[item.instrument_id] for item in instruments]
        tickers = self.session.ib.reqTickers(*contracts)
        result = []
        for definition, ticker in zip(instruments, tickers):
            price, quantity = decimal_or_none(ticker.last), decimal_or_none(ticker.lastSize)
            if price is None or quantity is None or price <= 0 or quantity <= 0:
                continue
            result.append(Trade(definition.instrument_id, price, quantity, _aware_datetime(ticker.time)))
        return tuple(result)

    def historical_bars(
        self,
        instrument: InstrumentDefinition,
        *,
        end: datetime,
        duration: str,
        bar_size: str,
        what_to_show: str = "TRADES",
        regular_trading_hours: bool = True,
    ) -> tuple[Bar, ...]:
        self.capabilities.require_market_data(MarketDataKind.BAR)
        self.capabilities.require_product(instrument.instrument_type)
        if end.tzinfo is None:
            raise ValueError("historical bar end must be timezone-aware")
        self.session.connect()
        contract = self.session.contracts[instrument.instrument_id]
        rows = self.session.ib.reqHistoricalData(
            contract,
            endDateTime=end,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=regular_trading_hours,
            formatDate=2,
            keepUpToDate=False,
        )
        span = _bar_span(bar_size)
        return tuple(
            Bar(
                instrument.instrument_id,
                start := _aware_datetime(row.date),
                start + span,
                Decimal(str(row.open)),
                Decimal(str(row.high)),
                Decimal(str(row.low)),
                Decimal(str(row.close)),
                Decimal(str(row.volume)),
            )
            for row in rows
        )


def _aware_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime_time.min, tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _bar_span(value: str) -> timedelta:
    try:
        amount_text, unit = value.strip().lower().split(maxsplit=1)
        amount = int(amount_text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unsupported IBKR bar size: {value}") from error
    seconds = {
        "sec": 1, "secs": 1, "second": 1, "seconds": 1,
        "min": 60, "mins": 60, "minute": 60, "minutes": 60,
        "hour": 3600, "hours": 3600,
        "day": 86400, "days": 86400,
    }.get(unit)
    if seconds is None or amount <= 0:
        raise ValueError(f"unsupported IBKR bar size: {value}")
    return timedelta(seconds=amount * seconds)


