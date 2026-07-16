from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from trading.adapters.base import AccountState, ComboOrderRequest, Environment, OrderAck, OrderRequest, ReferenceDataRequest, VenueBalance
from trading.adapters.ibkr.research import decimal_or_none
from trading.domain.capability import (
    ExecutionCapabilities, MarketDataCapabilities, MarketDataKind, MarginMode,
    OrderType, PositionMode, ReferenceCapabilities,
)
from trading.domain.execution import TradeExecution, TradeSide
from trading.domain.identity import AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.market_data import Bar, Quote, Trade
from trading.domain.product import ExerciseStyle, EquitySpec, IndexSpec, ListedOptionSpec, OptionRight, ProductType, SettlementSession, SettlementType


IBKR_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.EQUITY, ProductType.ETF, ProductType.LISTED_OPTION}),
)
IBKR_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.BAR, MarketDataKind.GREEKS, MarketDataKind.INDEX_PRICE}),
    product_types=frozenset({ProductType.INDEX, ProductType.EQUITY, ProductType.ETF, ProductType.LISTED_OPTION}),
    supports_native_greeks=True,
)
IBKR_EXECUTION_CAPABILITIES = ExecutionCapabilities(
    frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}),
    product_types=frozenset({ProductType.EQUITY, ProductType.ETF, ProductType.LISTED_OPTION}),
    supports_combo_orders=True,
    margin_modes=frozenset({MarginMode.NONE, MarginMode.SECURITIES}),
    position_modes=frozenset({PositionMode.ONE_WAY}),
)


class IbkrSession:
    def __init__(self, host="127.0.0.1", port=4001, client_id=51, readonly=True) -> None:
        from ib_async import IB
        self.ib = IB()
        self.host, self.port, self.client_id, self.readonly = host, port, client_id, readonly
        self.contracts = {}

    def connect(self):
        if not self.ib.isConnected():
            self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=self.readonly)

    def disconnect(self):
        if self.ib.isConnected(): self.ib.disconnect()


class IbkrReferenceAdapter:
    venue_id = VenueId("ibkr")
    capabilities = IBKR_REFERENCE_CAPABILITIES

    def __init__(self, session: IbkrSession) -> None:
        self.session = session

    def sync(self, request: ReferenceDataRequest) -> tuple[InstrumentDefinition, ...]:
        if request.product_type in {ProductType.EQUITY, ProductType.ETF}:
            return self.sync_equities(request.symbols, product_type=request.product_type)
        if request.product_type is ProductType.LISTED_OPTION:
            return self.sync_listed_options(request.symbols)
        raise ValueError(f"IBKR reference sync does not support {request.product_type}")

    def sync_equities(self, symbols: tuple[str, ...], *, product_type: ProductType = ProductType.EQUITY) -> tuple[InstrumentDefinition, ...]:
        from ib_async import Stock
        self.session.connect()
        definitions = []
        for symbol in symbols:
            qualified = self.session.ib.qualifyContracts(Stock(symbol, "SMART", "USD"))
            if not qualified:
                raise LookupError(f"IBKR stock not found: {symbol}")
            contract = qualified[0]
            instrument_id = InstrumentId(f"equity:us:{symbol.upper()}")
            self.session.contracts[instrument_id] = contract
            listing = VenueListing(self.venue_id, str(contract.conId), contract.localSymbol or symbol, Decimal("0.01"), Decimal("1"), Decimal("1"))
            definitions.append(InstrumentDefinition(
                instrument_id, product_type, symbol.upper(), AssetId(symbol), AssetId("USD"),
                EquitySpec(contract.primaryExchange or "SMART", "US", AssetId("USD")),
                (listing,), datetime.now(timezone.utc),
            ))
        return tuple(definitions)

    def sync_listed_options(self, descriptors: tuple[str, ...]) -> tuple[InstrumentDefinition, ...]:
        from ib_async import Option
        self.session.connect()
        definitions = []
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
            definitions.append(InstrumentDefinition(
                instrument_id, ProductType.LISTED_OPTION, contract.tradingClass or symbol.upper(), None, AssetId("USD"),
                ListedOptionSpec(
                    underlying_id, expiry, strike, right, ExerciseStyle.AMERICAN,
                    SettlementType.PHYSICAL, SettlementSession.PM, multiplier, expiry,
                ),
                (VenueListing(self.venue_id, str(contract.conId), contract.localSymbol or descriptor, Decimal("0.01"), Decimal("1"), Decimal("1")),),
                datetime.now(timezone.utc),
            ))
        return tuple(definitions)

    def bind_definition(self, definition: InstrumentDefinition, catalog=None) -> None:
        from ib_async import Index, Option, Stock
        if definition.product_type in {ProductType.EQUITY, ProductType.ETF}:
            contract = Stock(definition.symbol, "SMART", definition.quote_asset.value)
        elif definition.product_type is ProductType.INDEX:
            spec = definition.product_spec
            if not isinstance(spec, IndexSpec) or not spec.primary_exchange:
                raise ValueError("binding an IBKR index requires IndexSpec.primary_exchange")
            contract = Index(definition.symbol, spec.primary_exchange, definition.quote_asset.value)
        elif isinstance(definition.product_spec, ListedOptionSpec):
            spec = definition.product_spec
            if catalog is None:
                raise ValueError("binding a listed option requires its Catalog to resolve the underlying")
            underlying = catalog.get(spec.underlying, datetime.now(timezone.utc))
            contract = Option(
                underlying.symbol, spec.expiry.strftime("%Y%m%d"), float(spec.strike),
                "C" if spec.right is OptionRight.CALL else "P", "SMART",
                currency=definition.quote_asset.value, multiplier=format(spec.multiplier, "f"),
                tradingClass=definition.symbol,
            )
        else:
            raise ValueError(f"IBKR execution cannot bind {definition.product_type}")
        self.session.connect()
        qualified = self.session.ib.qualifyContracts(contract)
        if not qualified:
            raise LookupError(f"IBKR contract not found for {definition.instrument_id}")
        self.session.contracts[definition.instrument_id] = qualified[0]


class IbkrMarketDataAdapter:
    venue_id = VenueId("ibkr")
    capabilities = IBKR_MARKET_DATA_CAPABILITIES

    def __init__(self, session: IbkrSession, market_data_type=3) -> None:
        self.session, self.market_data_type = session, market_data_type

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]:
        for definition in instruments:
            self.capabilities.require_product(definition.product_type)
        self.session.connect()
        self.session.ib.reqMarketDataType(self.market_data_type)
        contracts = [self.session.contracts[item.instrument_id] for item in instruments]
        tickers = self.session.ib.reqTickers(*contracts)
        result = []
        for definition, ticker in zip(instruments, tickers):
            event_time = ticker.time if isinstance(ticker.time, datetime) else datetime.now(timezone.utc)
            if event_time.tzinfo is None: event_time = event_time.replace(tzinfo=timezone.utc)
            result.append(Quote(definition.instrument_id, decimal_or_none(ticker.bid), decimal_or_none(ticker.ask), decimal_or_none(ticker.bidSize), decimal_or_none(ticker.askSize), event_time))
        return tuple(result)

    def recent_trades(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Trade, ...]:
        self.capabilities.require_market_data(MarketDataKind.TRADE)
        for definition in instruments:
            self.capabilities.require_product(definition.product_type)
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
        self.capabilities.require_product(instrument.product_type)
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


class IbkrExecutionAdapter:
    venue_id = VenueId("ibkr")
    capabilities = IBKR_EXECUTION_CAPABILITIES

    def __init__(self, session: IbkrSession, environment: Environment) -> None:
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise ValueError("IBKR supports paper or live environment")
        self.session, self.environment = session, environment

    def place_order(self, request: OrderRequest) -> OrderAck:
        from ib_async import LimitOrder, MarketOrder, StopLimitOrder, StopOrder
        if self.environment is Environment.LIVE and self.session.readonly:
            raise PermissionError("readonly IBKR session cannot place live orders")
        self.capabilities.require_order_type(request.instructions.order_type)
        contract = self.session.contracts[request.instrument_id]
        action = request.side.value.upper()
        if request.instructions.order_type is OrderType.MARKET:
            order = MarketOrder(action, float(request.quantity), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.LIMIT:
            order = LimitOrder(action, float(request.quantity), float(request.instructions.limit_price), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.STOP:
            order = StopOrder(action, float(request.quantity), float(request.instructions.stop_price), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.STOP_LIMIT:
            order = StopLimitOrder(action, float(request.quantity), float(request.instructions.limit_price), float(request.instructions.stop_price), orderRef=request.client_order_id)
        else:
            raise ValueError(f"unsupported IBKR order type: {request.instructions.order_type}")
        trade = self.session.ib.placeOrder(contract, order)
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, str(trade.order.orderId), datetime.now(timezone.utc),
        )

    def cancel_order(self, account, venue_order_id):
        trade = next((item for item in self.session.ib.openTrades() if str(item.order.orderId) == venue_order_id), None)
        if trade is None: raise LookupError(f"open IBKR order not found: {venue_order_id}")
        self.session.ib.cancelOrder(trade.order)

    def open_orders(self, account):
        return tuple(str(item.order.orderId) for item in self.session.ib.openTrades())

    def place_combo_order(self, request: ComboOrderRequest) -> OrderAck:
        from ib_async import ComboLeg, Contract, LimitOrder, MarketOrder
        if self.environment is Environment.LIVE and self.session.readonly:
            raise PermissionError("readonly IBKR session cannot place live orders")
        if len(request.legs) < 2:
            raise ValueError("combo order requires at least two legs")
        contracts = [self.session.contracts[leg.instrument_id] for leg in request.legs]
        combo = Contract(
            symbol=contracts[0].symbol, secType="BAG", currency=contracts[0].currency,
            exchange="SMART", comboLegs=[
                ComboLeg(contract.conId, leg.ratio, leg.side.value.upper(), contract.exchange or "SMART")
                for leg, contract in zip(request.legs, contracts)
            ],
        )
        if request.instructions.order_type is OrderType.MARKET:
            order = MarketOrder("BUY", float(request.quantity), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.LIMIT:
            order = LimitOrder("BUY", float(request.quantity), float(request.instructions.limit_price), orderRef=request.client_order_id)
        else:
            raise ValueError("IBKR combo supports market or limit orders")
        trade = self.session.ib.placeOrder(combo, order)
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, str(trade.order.orderId), datetime.now(timezone.utc),
        )


class IbkrAccountAdapter:
    venue_id = VenueId("ibkr")

    def __init__(self, session: IbkrSession, environment: Environment) -> None:
        self.session, self.environment = session, environment

    def account_state(self, account) -> AccountState:
        self.session.connect()
        summary = self.session.ib.accountSummary(account.account_id)
        balances_by_asset = {}
        for item in summary:
            if item.tag in {"CashBalance", "TotalCashValue"} and item.currency and item.currency != "BASE":
                value = decimal_or_none(item.value)
                if value is not None:
                    asset = AssetId(item.currency)
                    if item.tag == "TotalCashValue" or asset not in balances_by_asset:
                        balances_by_asset[asset] = value
        positions = []
        for position in self.session.ib.positions(account.account_id):
            matched = next((instrument_id for instrument_id, contract in self.session.contracts.items() if contract.conId == position.contract.conId), None)
            if matched: positions.append((matched, Decimal(str(position.position))))
        return AccountState(account, tuple(VenueBalance(asset, amount, amount) for asset, amount in balances_by_asset.items()), tuple(positions), tuple(str(item.order.orderId) for item in self.session.ib.openTrades()), datetime.now(timezone.utc))


def normalize_ibkr_execution(*, execution_id: str, timestamp: datetime, account, instrument_id: InstrumentId, side: str, quantity, price, commission, commission_currency: str, order_id: str) -> TradeExecution:
    """Normalize IBKR execution/commission callbacks without exposing SDK objects."""
    return TradeExecution(
        uuid5(NAMESPACE_URL, f"ibkr-execution:{execution_id}"), timestamp, account, instrument_id,
        TradeSide.BUY if side.upper() in {"BOT", "BUY"} else TradeSide.SELL,
        Decimal(str(quantity)), Decimal(str(price)), AssetId(commission_currency),
        abs(Decimal(str(commission))), order_id,
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
