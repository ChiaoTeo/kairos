from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from collections.abc import Iterable, Mapping

from kairospy.domain.market import Bar, MarketEvent, MarketSubject, OrderBookSnapshot, PriceLevel, Quote, TradePrint
from kairospy.domain.account import AccountBalance, AccountContext, AccountSnapshot, AccountSource, OpenOrderSnapshot
from kairospy.domain.execution import ExecutionUpdate
from kairospy.domain.order import OrderEventKind, OrderSide, OrderType, OrderState
from kairospy.domain.reference import ReferenceCatalog
from kairospy.domain.reference.identity import (
    AssetId,
    InstrumentId,
    ListingId,
    MarketId,
    MarketTypeId,
    SourceSymbol,
)
from kairospy.domain.reference.model import (
    Asset,
    AssetType,
    InstrumentDefinition,
    InstrumentType,
    ListingDefinition,
    MarketDefinition,
    MarketStatus,
)
from kairospy.domain.reference.markets import MarketRef


@dataclass(frozen=True, slots=True)
class BinanceTranslatedEvent:
    """Internal vendor event; never exported through Integration application."""

    kind: str
    observed_at: datetime
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("translated event kind is required")
        if self.observed_at.tzinfo is None:
            raise ValueError("translated event timestamp must be timezone-aware")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(slots=True)
class BinanceSpotNormalizers:
    """Private boundary for Binance payload-to-domain conversion."""

    def market_event(self, payload: Mapping[str, object]) -> BinanceTranslatedEvent:
        return _translate("market", payload)

    def market_domain_event(self, payload: Mapping[str, object], *, market: MarketRef, channel: str) -> MarketEvent:
        observed_at = _event_time(payload.get("E") or payload.get("T") or payload.get("t"))
        symbol = str(market.source_symbol)
        if channel == "ticker":
            value = Quote(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=observed_at,
                bid=_decimal(payload.get("b")),
                ask=_decimal(payload.get("a")),
                source="binance",
            )
        elif channel == "trade":
            value = TradePrint(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=observed_at,
                trade_id=_text(payload.get("t")) or None,
                side="sell" if payload.get("m") is True else "buy",
                price=_decimal(payload.get("p")),
                size=_decimal(payload.get("q")),
                cost=_multiply(_decimal(payload.get("p")), _decimal(payload.get("q"))),
                source="binance",
            )
        elif channel == "orderbook":
            value = OrderBookSnapshot(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=observed_at,
                bids=_levels(payload.get("b") or payload.get("bids")),
                asks=_levels(payload.get("a") or payload.get("asks")),
                nonce=payload.get("u") or payload.get("lastUpdateId"),
                source="binance",
            )
        else:
            raise ValueError(f"unsupported Binance Spot market channel: {channel}")
        return MarketEvent(
            subject=MarketSubject("market", market.market_id),
            observed_at=observed_at,
            value=value,
            available_at=observed_at,
            source="binance",
            metadata={"symbol": symbol, "channel": channel},
        )

    def account_event(self, payload: Mapping[str, object]) -> BinanceTranslatedEvent:
        return _translate("account", payload)

    def order_result(self, payload: Mapping[str, object]) -> BinanceTranslatedEvent:
        return _translate("order", payload)

    def execution_update(self, payload: Mapping[str, object], *, context: AccountContext) -> ExecutionUpdate:
        symbol = _text(payload.get("s")) or "UNKNOWN"
        market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol=symbol)
        status = _order_event_kind(payload.get("X"), payload.get("x"))
        quantity = _decimal(payload.get("q"))
        filled = _decimal(payload.get("z"))
        fill_quantity = _decimal(payload.get("l"))
        fill_price = _decimal(payload.get("L")) or _decimal(payload.get("p"))
        commission = _decimal(payload.get("n")) or Decimal("0")
        observed_at = datetime.fromtimestamp(float(payload.get("E") or payload.get("T") or 0) / 1000, tz=timezone.utc)
        if observed_at.timestamp() <= 0:
            observed_at = datetime.now(timezone.utc)
        return ExecutionUpdate(
            observed_at=observed_at,
            kind=status,
            order_venue_id=_text(payload.get("i")) or _text(payload.get("c")),
            order_id=_text(payload.get("C")) or None,
            context=context,
            instrument_id=market.instrument_id,
            market_id=market.market_id,
            side=_order_side(payload.get("S")),
            quantity=quantity,
            order_type=_order_type(payload.get("o"), fill_price),
            limit_price=_decimal(payload.get("p")),
            filled_quantity=filled,
            remaining_quantity=None if quantity is None or filled is None else max(quantity - filled, Decimal("0")),
            fill_quantity=fill_quantity if fill_quantity and fill_quantity > 0 else None,
            fill_price=fill_price if fill_quantity and fill_quantity > 0 else None,
            settlement_currency=_quote_currency(symbol),
            cash_delta=None,
            fee_currency=_text(payload.get("N")) or None,
            fee_amount=commission,
            reason=_text(payload.get("r")),
            source="binance",
            metadata={"symbol": symbol, "event_type": payload.get("x")},
        )

    def ingest_order_update(self, coordinator: object, context: AccountContext, raw: Mapping[str, object]) -> OrderState:
        return coordinator.apply_execution_update(self.execution_update(raw, context=context))

    def ingest_trade_update(self, coordinator: object, context: AccountContext, raw: Mapping[str, object]) -> OrderState:
        return coordinator.apply_execution_update(self.execution_update(raw, context=context))

    def bars(self, payload: object, *, symbol: str, timeframe: str) -> Iterable[Bar]:
        if not isinstance(payload, list):
            raise ValueError("Binance klines response must be a list")
        market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol=symbol)
        return (_bar(row, market=market, timeframe=timeframe) for row in payload)

    def account_snapshot(self, payload: object, *, context: AccountContext, observed_at: datetime, open_orders: object = ()) -> AccountSnapshot:
        if not isinstance(payload, Mapping):
            raise ValueError("Binance account response must be an object")
        values = payload.get("balances")
        if not isinstance(values, list):
            raise ValueError("Binance account balances must be a list")
        balances: list[AccountBalance] = []
        for value in values:
            if not isinstance(value, Mapping):
                continue
            asset = _text(value.get("asset"))
            free = _decimal(value.get("free")) or Decimal("0")
            locked = _decimal(value.get("locked")) or Decimal("0")
            if asset and (free != 0 or locked != 0):
                balances.append(AccountBalance.from_free_locked(asset, free, locked, source=AccountSource.VENUE))
        orders = tuple(_open_order(value) for value in open_orders if isinstance(value, Mapping)) if isinstance(open_orders, (list, tuple)) else ()
        return AccountSnapshot(context=context, balances=tuple(balances), open_orders=orders, observed_at=observed_at, source=AccountSource.VENUE)

    def balance_snapshot(
        self,
        context: AccountContext,
        raw_balance: Mapping[str, object],
        *,
        at: datetime,
        open_orders: tuple,
    ) -> AccountSnapshot:
        """Translate a user-stream balance event through the same account model."""

        return self.account_snapshot(
            {"balances": raw_balance.get("balances", [])},
            context=context,
            observed_at=at,
            open_orders=open_orders,
        )

    def catalog(self, payload: object, *, as_of: datetime) -> ReferenceCatalog:
        if not isinstance(payload, Mapping):
            raise ValueError("Binance exchange info response must be an object")
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise ValueError("Binance exchange info symbols must be a list")
        assets: dict[str, Asset] = {}
        instruments: list[InstrumentDefinition] = []
        listings: list[ListingDefinition] = []
        markets: list[MarketDefinition] = []
        for value in symbols:
            if not isinstance(value, Mapping):
                continue
            symbol = _text(value.get("symbol"))
            base = _text(value.get("baseAsset"))
            quote = _text(value.get("quoteAsset"))
            if not symbol or not base or not quote:
                continue
            for asset_symbol in (base, quote):
                asset_id = AssetId(f"asset:crypto:{asset_symbol.lower()}")
                assets.setdefault(
                    str(asset_id),
                    Asset(asset_id=asset_id, asset_type=AssetType.CRYPTO, symbol=asset_symbol, effective_from=as_of),
                )
            instrument_id = InstrumentId(f"instrument:spot:{base.lower()}:{quote.lower()}")
            listing_id = ListingId(f"listing:binance:spot:{symbol.lower()}")
            market_id = MarketId(f"market:binance:spot:{symbol.lower()}")
            instruments.append(
                InstrumentDefinition(
                    instrument_id=instrument_id,
                    instrument_type=InstrumentType.SPOT,
                    base_asset_id=AssetId(f"asset:crypto:{base.lower()}"),
                    quote_asset_id=AssetId(f"asset:crypto:{quote.lower()}"),
                    display_name=symbol,
                    effective_from=as_of,
                )
            )
            status = MarketStatus.ACTIVE if value.get("status") == "TRADING" else MarketStatus.UNKNOWN
            listings.append(
                ListingDefinition(
                    listing_id=listing_id,
                    instrument_id=instrument_id,
                    venue="binance",
                    trading_symbol=SourceSymbol(symbol),
                    status=status,
                    effective_from=as_of,
                )
            )
            filters = value.get("filters")
            price_tick, amount_tick, min_amount, min_notional = _filters(filters)
            markets.append(
                MarketDefinition(
                    market_id=market_id,
                    instrument_id=instrument_id,
                    listing_id=listing_id,
                    venue="binance",
                    market=MarketTypeId("spot"),
                    source_symbol=SourceSymbol(symbol),
                    status=status,
                    price_tick=price_tick,
                    amount_tick=amount_tick,
                    min_amount=min_amount,
                    min_notional=min_notional,
                    effective_from=as_of,
                )
            )
        return ReferenceCatalog(assets=tuple(assets.values()), instruments=tuple(instruments), listings=tuple(listings), markets=tuple(markets))


def _translate(kind: str, payload: Mapping[str, object]) -> BinanceTranslatedEvent:
    return BinanceTranslatedEvent(kind, datetime.now(timezone.utc), payload)


def _bar(row: object, *, market: MarketRef, timeframe: str) -> Bar:
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        raise ValueError("Binance kline row must contain at least six values")
    return Bar(
        instrument_id=market.instrument_id,
        market_id=market.market_id,
        market_key=market.market_key,
        time=datetime.fromtimestamp(float(row[0]) / 1000, tz=timezone.utc),
        timeframe=timeframe,
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        source="binance",
    )


def _filters(value: object) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    price_tick = amount_tick = min_amount = min_notional = None
    if not isinstance(value, list):
        return price_tick, amount_tick, min_amount, min_notional
    for item in value:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("filterType")
        if kind == "PRICE_FILTER":
            price_tick = _decimal(item.get("tickSize"))
        elif kind == "LOT_SIZE":
            amount_tick = _decimal(item.get("stepSize"))
            min_amount = _decimal(item.get("minQty"))
        elif kind in {"MIN_NOTIONAL", "NOTIONAL"}:
            min_notional = _decimal(item.get("minNotional"))
    return price_tick, amount_tick, min_amount, min_notional


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _open_order(value: Mapping[str, object]) -> OpenOrderSnapshot:
    symbol = _text(value.get("symbol")) or "UNKNOWN"
    market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol=symbol)
    quantity = _decimal(value.get("origQty")) or _decimal(value.get("executedQty")) or Decimal("0")
    remaining = _decimal(value.get("origQty")) or quantity
    if _decimal(value.get("executedQty")) is not None:
        remaining = max(remaining - (_decimal(value.get("executedQty")) or Decimal("0")), Decimal("0"))
    if remaining <= 0:
        remaining = quantity
    return OpenOrderSnapshot(
        order_id=_text(value.get("orderId")) or _text(value.get("clientOrderId")) or "unknown",
        instrument_id=market.instrument_id,
        side=_text(value.get("side")).lower() or "unknown",
        quantity=remaining,
        source=AccountSource.VENUE,
    )


def _order_event_kind(status: object, event_type: object) -> OrderEventKind:
    value = str(status or event_type or "").upper()
    return {
        "NEW": OrderEventKind.ACKNOWLEDGED,
        "PARTIALLY_FILLED": OrderEventKind.PARTIALLY_FILLED,
        "FILLED": OrderEventKind.FILLED,
        "CANCELED": OrderEventKind.CANCELED,
        "REJECTED": OrderEventKind.REJECTED,
        "EXPIRED": OrderEventKind.EXPIRED,
    }.get(value, OrderEventKind.UNKNOWN)


def _order_side(value: object) -> OrderSide | None:
    try:
        return None if value is None else OrderSide(str(value).lower())
    except ValueError:
        return None


def _order_type(value: object, price: Decimal | None) -> OrderType | None:
    value = str(value or "").lower()
    if value in {"limit", "market"}:
        return OrderType(value)
    return OrderType.LIMIT if price is not None and price > 0 else OrderType.MARKET


def _quote_currency(symbol: str) -> str:
    for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote):
            return quote
    return "USD"


def _event_time(value: object) -> datetime:
    try:
        millis = float(value or 0)
    except (TypeError, ValueError):
        millis = 0
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc) if millis > 0 else datetime.now(timezone.utc)


def _multiply(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    return None if left is None or right is None else left * right


def _levels(value: object) -> tuple[PriceLevel, ...]:
    if not isinstance(value, list):
        return ()
    levels: list[PriceLevel] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        price, size = _decimal(item[0]), _decimal(item[1])
        if price is not None and size is not None:
            levels.append(PriceLevel(price, size))
    return tuple(levels)


__all__ = ["BinanceSpotNormalizers", "BinanceTranslatedEvent"]
