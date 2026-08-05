"""CCXT-backed private account and execution connections.

CCXT is deliberately kept behind the Integration application ports.  The
exchange object and its unified payloads never cross this module boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from kairospy.domain.account import AccountBalance, AccountRuntimeContext, AccountFeeResolution, AccountFeeRule, AccountFeeSchedule, AccountMarketProfile, AccountModel, AccountSegment, AccountSnapshot, AccountSource, CollateralBalance, FeeDiscountRule, FeePaymentRule, MarginState, MarketFeeRule, OpenOrderSnapshot, PositionSnapshot, ProductFamily as AccountProductFamily
from kairospy.domain.reference import MarketRef
from kairospy.infrastructure.integrations.application.account import ConnectionAccountMarketProfileData, ConnectionAccountMarketProfileRequest, ConnectionAccountReadData, ConnectionAccountReadRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderCancelRequest, ConnectionOrderCancelResult, ConnectionOrderSubmissionRequest, ConnectionOrderSubmissionResult
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.infrastructure.integrations.services.credentials import credential_value


class CcxtPrivateConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec, *, exchange: object | None = None) -> None:
        self._exchange = exchange
        self._wallet_address = credential_value(spec.credential.id if spec.credential else None, "WALLET_ADDRESS")
        super().__init__(spec, components=())

    def _client(self) -> Any:
        if self._exchange is None:
            try:
                import ccxt  # type: ignore[import-not-found]
            except ImportError as error:
                raise RuntimeError("CCXT private support requires the crypto extra") from error
            exchange_id = _exchange_id(self.spec)
            exchange_type = getattr(ccxt, exchange_id, None)
            if exchange_type is None:
                raise ValueError(f"unsupported CCXT exchange: {exchange_id}")
            credential = self.spec.credential.id if self.spec.credential else None
            config: dict[str, object] = {
                "enableRateLimit": True,
                "options": {"defaultType": _market_type(self.spec.product, exchange_id)},
            }
            api_key = credential_value(credential, "API_KEY")
            secret = credential_value(credential, "SECRET")
            private_key = credential_value(credential, "PRIVATE_KEY")
            wallet_address = credential_value(credential, "WALLET_ADDRESS")
            if api_key:
                config["apiKey"] = api_key
            if secret:
                config["secret"] = secret
            if credential_value(credential, "PASSPHRASE"):
                config["password"] = credential_value(credential, "PASSPHRASE")
            if exchange_id == "hyperliquid":
                if private_key:
                    config["privateKey"] = private_key
                if wallet_address:
                    config.setdefault("walletAddress", wallet_address)
            self._exchange = exchange_type(config)
        return self._exchange


class CcxtAccountConnection(CcxtPrivateConnection):
    def __init__(self, spec: IntegrationConnectionSpec, *, exchange: object | None = None) -> None:
        super().__init__(spec, exchange=exchange)

    def read_account(self, request: ConnectionAccountReadRequest) -> ConnectionAccountReadData:
        exchange = self._client()
        balance = exchange.fetch_balance()
        positions = _fetch_positions(exchange, request.symbol, product=self.spec.product)
        orders = exchange.fetch_open_orders(request.symbol) if request.fetch_orders else ()
        return ConnectionAccountReadData(_account_snapshot(balance, positions, orders, context=request.context, observed_at=request.observed_at, product=self.spec.product))

    def read_market_profile(self, request: ConnectionAccountMarketProfileRequest) -> ConnectionAccountMarketProfileData:
        exchange = self._client()
        symbol = str(request.market.source_symbol)
        exchange_id = _exchange_id(self.spec)
        market_payload, account_payload, account_info = _venue_fee_payloads(
            exchange,
            exchange_id=exchange_id,
            request=request,
            symbol=symbol,
            wallet_address=self._wallet_address,
        )
        market_rule = _market_fee_rule(request.market, market_payload, observed_at=request.observed_at)
        account_rule = _account_fee_rule(request.context, account_payload, observed_at=request.observed_at)
        payment = _payment_rule(account_payload or market_payload)
        schedule = _effective_fee_schedule(
            request.context,
            request.market,
            account_rule=account_rule,
            market_rule=market_rule,
            payment=payment,
            observed_at=request.observed_at,
        )
        resolution = None if schedule is None else AccountFeeResolution(schedule, account_rule, market_rule, payment, combination="account_override_then_discount")
        info = account_info or account_payload or market_payload
        return ConnectionAccountMarketProfileData(
            AccountMarketProfile(
                request.context,
                request.market,
                fee=resolution,
                account_type=_account_type(info, exchange_id) or _account_segment_type(request.context.segment),
                margin_mode=_text(info, "marginMode"),
                leverage=_decimal_or_none(_value(info, "leverage")),
                position_mode=_text(info, "positionMode") or _text(info, "posMode"),
                settlement_currency=_text(info, "settlementCurrency"),
                source="ccxt",
                observed_at=request.observed_at,
            )
        )


class CcxtExecutionConnection(CcxtPrivateConnection):
    def __init__(self, spec: IntegrationConnectionSpec, *, exchange: object | None = None) -> None:
        super().__init__(spec, exchange=exchange)

    def submit(self, request: ConnectionOrderSubmissionRequest) -> ConnectionOrderSubmissionResult:
        params: dict[str, object] = {}
        if request.options is not None:
            if request.options.reduce_only is not None:
                params["reduceOnly"] = request.options.reduce_only
            if request.options.position_side is not None:
                params["positionSide"] = request.options.position_side
            if request.options.close_position is not None:
                params["closePosition"] = request.options.close_position
            if request.options.working_type is not None:
                params["workingType"] = request.options.working_type
            if request.options.stop_price is not None:
                params["stopPrice"] = float(request.options.stop_price)
            if request.options.time_in_force is not None:
                params["timeInForce"] = request.options.time_in_force
        result = self._client().create_order(
            request.symbol,
            request.order_type.value,
            request.side.value,
            float(request.quantity),
            None if request.limit_price is None else float(request.limit_price),
            params,
        )
        if not isinstance(result, Mapping):
            raise ValueError("CCXT order response must be an object")
        return ConnectionOrderSubmissionResult(str(result.get("id") or ""), str(result.get("status") or ""))

    def cancel(self, request: ConnectionOrderCancelRequest) -> ConnectionOrderCancelResult:
        result = self._client().cancel_order(request.order_venue_id, request.symbol)
        if not isinstance(result, Mapping):
            raise ValueError("CCXT cancel response must be an object")
        return ConnectionOrderCancelResult(str(result.get("id") or request.order_venue_id), str(result.get("status") or ""))


class CcxtAccountGateway:
    def __init__(self, *, exchange: object | None = None) -> None:
        self.exchange = exchange

    def open(self, spec: IntegrationConnectionSpec) -> CcxtAccountConnection:
        _validate(spec)
        return CcxtAccountConnection(spec, exchange=self.exchange)


class CcxtExecutionGateway:
    def __init__(self, *, exchange: object | None = None) -> None:
        self.exchange = exchange

    def open(self, spec: IntegrationConnectionSpec) -> CcxtExecutionConnection:
        _validate(spec)
        return CcxtExecutionConnection(spec, exchange=self.exchange)


def _account_snapshot(balance: object, positions: object, orders: object, *, context: AccountRuntimeContext, observed_at: datetime, product: ProductFamily | None) -> AccountSnapshot:
    values = balance if isinstance(balance, Mapping) else {}
    free = values.get("free") if isinstance(values.get("free"), Mapping) else {}
    used = values.get("used") if isinstance(values.get("used"), Mapping) else {}
    totals = values.get("total") if isinstance(values.get("total"), Mapping) else {}
    currencies = set(free) | set(used) | set(totals)
    balances = tuple(
        AccountBalance.from_free_locked(str(currency), _decimal(free.get(currency)), _decimal(used.get(currency)), source=AccountSource.VENUE)
        for currency in sorted(currencies)
        if _decimal(totals.get(currency)) != 0 or _decimal(free.get(currency)) != 0 or _decimal(used.get(currency)) != 0
    )
    venue = str(context.segment.broker)
    position_rows = tuple(_position(row, venue=venue, product=product) for row in positions if isinstance(row, Mapping) and _decimal(row.get("contracts")) not in (None, Decimal("0"))) if isinstance(positions, (list, tuple)) else ()
    order_rows = tuple(_order(row, venue=venue, product=product) for row in orders if isinstance(row, Mapping)) if isinstance(orders, (list, tuple)) else ()
    margin = _margin(values)
    collaterals = _collateral_balances(values) if product in {ProductFamily.USD_M_FUTURES, ProductFamily.COIN_M_FUTURES} else ()
    return AccountSnapshot(context, balances=balances, margins=() if margin is None else (margin,), collaterals=collaterals, positions=position_rows, open_orders=order_rows, observed_at=observed_at, source=AccountSource.VENUE)


def _position(row: Mapping[str, object], *, venue: str, product: ProductFamily | None) -> PositionSnapshot:
    symbol = str(row.get("symbol") or row.get("info", {}).get("symbol") or "UNKNOWN") if isinstance(row.get("info"), Mapping) else str(row.get("symbol") or "UNKNOWN")
    contracts = _decimal(row.get("contracts")) or Decimal("0")
    if str(row.get("side") or "").lower() == "short":
        contracts = -abs(contracts)
    return PositionSnapshot(MarketRef.ephemeral(venue=venue, market=str(product or ProductFamily.USD_M_FUTURES), source_symbol=symbol).instrument_id, contracts, AccountSource.VENUE, average_price=_decimal(row.get("entryPrice")), mark_price=_decimal(row.get("markPrice")), unrealized_pnl=_decimal(row.get("unrealizedPnl")), liquidation_price=_decimal(row.get("liquidationPrice")), margin_currency="USDT")


def _order(row: Mapping[str, object], *, venue: str, product: ProductFamily | None) -> OpenOrderSnapshot:
    symbol = str(row.get("symbol") or "UNKNOWN")
    remaining = _decimal(row.get("remaining")) or _decimal(row.get("amount")) or Decimal("0")
    return OpenOrderSnapshot(str(row.get("id") or "unknown"), MarketRef.ephemeral(venue=venue, market=str(product or ProductFamily.SPOT), source_symbol=symbol).instrument_id, str(row.get("side") or "unknown"), remaining, AccountSource.VENUE)


def _margin(values: Mapping[str, object]) -> MarginState | None:
    free = values.get("free") if isinstance(values.get("free"), Mapping) else {}
    used = values.get("used") if isinstance(values.get("used"), Mapping) else {}
    total = values.get("total") if isinstance(values.get("total"), Mapping) else {}
    if not any(key in values for key in ("free", "used", "total")) or not any(_decimal(value) for value in total.values()):
        return None
    return MarginState("USDT", _decimal(used.get("USDT")), _decimal(used.get("USDT")), AccountSource.VENUE, available=_decimal(free.get("USDT")))


def _collateral_balances(values: Mapping[str, object]) -> tuple[CollateralBalance, ...]:
    free = values.get("free") if isinstance(values.get("free"), Mapping) else {}
    total = values.get("total") if isinstance(values.get("total"), Mapping) else {}
    assets = sorted(set(free) | set(total))
    return tuple(
        CollateralBalance(
            str(asset),
            _decimal(total.get(asset)) or Decimal("0"),
            _decimal(free.get(asset)) or Decimal("0"),
            source=AccountSource.VENUE,
        )
        for asset in assets
        if (_decimal(total.get(asset)) or Decimal("0")) > 0
    )


def _fetch_positions(exchange: Any, symbol: str | None, *, product: ProductFamily | None) -> object:
    if product not in {ProductFamily.USD_M_FUTURES, ProductFamily.COIN_M_FUTURES}:
        return ()
    method = getattr(exchange, "fetch_positions", None)
    return () if not callable(method) else method() if symbol is None else method([symbol])


def _call_fee(exchange: Any, name: str, symbol: str) -> Mapping[str, object] | None:
    method = getattr(exchange, name, None)
    if not callable(method):
        return None
    value = method(symbol)
    return value if isinstance(value, Mapping) else None


def _venue_fee_payloads(
    exchange: Any,
    *,
    exchange_id: str,
    request: ConnectionAccountMarketProfileRequest,
    symbol: str,
    wallet_address: str | None,
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None, Mapping[str, object] | None]:
    """Read venue-specific private account metadata behind the CCXT adapter.

    CCXT's unified fee methods are useful fallbacks, but they do not expose
    OKX account mode or Hyperliquid's post-discount user rates consistently.
    These calls stay here so vendor payloads never cross the integration port.
    """
    if exchange_id == "okx":
        config = _call_raw(exchange, ("privateGetAccountConfig", "private_get_account_config"))
        params = _okx_fee_params(request, symbol)
        fee = _call_raw(exchange, ("privateGetAccountTradeFee", "private_get_account_trade_fee"), params=params)
        row = _first_data_row(fee)
        if row is not None:
            group = row.get("feeGroup")
            market = group[0] if isinstance(group, list) and group and isinstance(group[0], Mapping) else row
            return _as_mapping(market), _as_mapping(row), _first_data_row(config) or config
        return _call_fee(exchange, "fetch_trading_fee", symbol), _call_fee(exchange, "fetch_my_trading_fee", symbol), _first_data_row(config) or config
    if exchange_id == "hyperliquid":
        # Hyperliquid reads are address-based.  The address is public, while
        # the private key is only needed by signed exchange actions.
        address = wallet_address or getattr(exchange, "walletAddress", None) or getattr(exchange, "wallet_address", None)
        if not address:
            address = getattr(exchange, "walletAddressAddress", None)
        fee = _call_raw(exchange, ("publicPostInfo", "public_post_info"), params={"type": "userFees", "user": address} if address else None)
        fee = _as_mapping(fee)
        schedule = fee.get("feeSchedule") if isinstance(fee, Mapping) else None
        if isinstance(schedule, Mapping):
            spot = request.context.segment.product_family is AccountProductFamily.SPOT
            market_maker = _decimal_or_none(schedule.get("spotAdd" if spot else "add"))
            market_taker = _decimal_or_none(schedule.get("spotCross" if spot else "cross"))
            account_maker = _decimal_or_none(fee.get("userSpotAddRate" if spot else "userAddRate"))
            account_taker = _decimal_or_none(fee.get("userSpotCrossRate" if spot else "userCrossRate"))
            market_payload = {"maker": market_maker, "taker": market_taker, "currency": "USDC"}
            account_payload = {"maker": account_maker, "taker": account_taker, "tier": "hyperliquid.user", "currency": "USDC"}
            return market_payload, account_payload, {"accountType": "unified", "marginMode": "cross", "positionMode": "one_way"}
    return _call_fee(exchange, "fetch_trading_fee", symbol), _call_fee(exchange, "fetch_my_trading_fee", symbol), None


def _call_raw(exchange: Any, names: tuple[str, ...], *, params: Mapping[str, object] | None = None) -> object | None:
    for name in names:
        method = getattr(exchange, name, None)
        if callable(method):
            try:
                return method(params or {}) if params is not None else method()
            except TypeError:
                return method(params or {})
    return None


def _okx_fee_params(request: ConnectionAccountMarketProfileRequest, symbol: str) -> Mapping[str, object]:
    product = str(request.context.segment.product_family or request.context.segment.model)
    if product in {"spot", "equity"}:
        return {"instType": "SPOT", "instId": symbol.replace("/", "-")}
    return {"instType": "SWAP", "instFamily": symbol.split(":", 1)[0].replace("/", "-")}


def _as_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _first_data_row(value: object) -> Mapping[str, object] | None:
    payload = _as_mapping(value)
    if payload is None:
        return None
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], Mapping):
        return data[0]
    return None


def _account_type(payload: Mapping[str, object] | None, exchange_id: str) -> AccountModel | None:
    value = _text(payload, "accountType") or _text(payload, "acctLv")
    if exchange_id == "okx":
        value = {"1": "spot", "2": "futures", "3": "multi_currency_margin", "4": "portfolio_margin"}.get(value or "", value)
    return {
        "spot": AccountModel.NO_MARGIN,
        "futures": AccountModel.CONTRACT,
        "multi_currency_margin": AccountModel.UNIFIED,
        "unified": AccountModel.UNIFIED,
        "portfolio_margin": AccountModel.PORTFOLIO_MARGIN,
        "margin": AccountModel.MARGIN,
    }.get(value or "")


def _account_segment_type(scope: AccountSegment) -> AccountModel | None:
    return {
        "spot": AccountModel.NO_MARGIN,
        "usd_m_futures": AccountModel.CONTRACT,
        "coin_m_futures": AccountModel.CONTRACT,
        "cross_margin": AccountModel.MARGIN,
        "isolated_margin": AccountModel.MARGIN,
        "portfolio_margin": AccountModel.PORTFOLIO_MARGIN,
    }.get(str(scope.product_family or scope.model))


def _market_fee_rule(market: MarketRef, payload: Mapping[str, object] | None, *, observed_at: datetime) -> MarketFeeRule | None:
    if payload is None:
        return None
    maker = _decimal_or_none(_value(payload, "maker"))
    taker = _decimal_or_none(_value(payload, "taker"))
    if maker is None or taker is None:
        return None
    return MarketFeeRule(market, maker, taker, currency=_text(payload, "currency"), source="ccxt.market", updated_at=observed_at)


def _account_fee_rule(context: AccountRuntimeContext, payload: Mapping[str, object] | None, *, observed_at: datetime) -> AccountFeeRule | None:
    if payload is None:
        return None
    maker = _decimal_or_none(_value(payload, "maker"))
    taker = _decimal_or_none(_value(payload, "taker"))
    tier = _text(payload, "tier") or _text(payload, "vipLevel") or _text(payload, "level")
    if maker is None and taker is None and tier is None:
        return None
    return AccountFeeRule(context.segment, maker, taker, tier=tier, source="ccxt.account", updated_at=observed_at)


def _payment_rule(payload: Mapping[str, object] | None) -> FeePaymentRule | None:
    if payload is None:
        return None
    currency = _text(payload, "currency") or _text(payload, "feeCurrency")
    info = payload.get("info") if isinstance(payload.get("info"), Mapping) else payload
    discount_asset = _text(info, "discountAsset") or _text(info, "bnbAsset")
    discount_rate = _decimal_or_none(_value(info, "discountRate"))
    enabled = bool(_value(info, "discountEnabled") or _value(info, "bnbDiscount"))
    discount = None
    if discount_asset and discount_rate is not None:
        discount = FeeDiscountRule(discount_asset, discount_rate, enabled=enabled, source="ccxt.account")
    if currency is None and discount is None:
        return None
    return FeePaymentRule(currency=currency, currency_mode="explicit" if currency else "venue_default", discount=discount)


def _effective_fee_schedule(context: AccountRuntimeContext, market: MarketRef, *, account_rule: AccountFeeRule | None, market_rule: MarketFeeRule | None, payment: FeePaymentRule | None, observed_at: datetime) -> AccountFeeSchedule | None:
    if account_rule is None and market_rule is None:
        return None
    maker = account_rule.maker if account_rule is not None and account_rule.maker is not None else market_rule.maker if market_rule is not None else Decimal("0")
    taker = account_rule.taker if account_rule is not None and account_rule.taker is not None else market_rule.taker if market_rule is not None else Decimal("0")
    currency = (payment.currency if payment is not None else None) or (market_rule.currency if market_rule is not None else None)
    if payment is not None and payment.discount is not None and payment.discount.enabled:
        multiplier = Decimal("1") - payment.discount.rate
        maker *= multiplier
        taker *= multiplier
        source = "ccxt.account+market+discount"
    else:
        source = "ccxt.account+market"
    return AccountFeeSchedule(context.segment, maker, taker, market=market, currency=currency, tier=None if account_rule is None else account_rule.tier, source=source, updated_at=observed_at, account_rule=account_rule, market_rule=market_rule, payment=payment)


def _value(payload: Mapping[str, object] | None, key: str) -> object | None:
    if payload is None:
        return None
    if key in payload:
        return payload[key]
    info = payload.get("info")
    return info.get(key) if isinstance(info, Mapping) else None


def _text(payload: Mapping[str, object] | None, key: str) -> str | None:
    value = _value(payload, key)
    text = "" if value is None else str(value).strip()
    return text or None


def _decimal_or_none(value: object | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


def _market_type(product: ProductFamily | None, exchange_id: str) -> str:
    if product not in {ProductFamily.USD_M_FUTURES, ProductFamily.COIN_M_FUTURES}:
        return "spot"
    return "future" if exchange_id == "binance" else "swap"


def _exchange_id(spec: IntegrationConnectionSpec) -> str:
    for participant in spec.participants:
        if participant.kind.value in {"exchange", "broker"}:
            return str(participant.id)
    raise ValueError("CCXT private connection requires an exchange or broker")


def _validate(spec: IntegrationConnectionSpec) -> None:
    if spec.access is not AccessScope.PRIVATE or spec.transport is not TransportKind.REST:
        raise ValueError("CCXT private gateway requires private REST")


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (TypeError, ValueError):
        return Decimal("0")


__all__ = ["CcxtAccountConnection", "CcxtAccountGateway", "CcxtExecutionConnection", "CcxtExecutionGateway"]
