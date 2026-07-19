from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from trading.accounting.ledger import LedgerService
from trading.backtest.fill import CryptoOrderBookFillModel, EquityTopOfBookFillModel, SingleAssetOrder, StressWrapperFillModel
from trading.domain.execution import TradeExecution, TradeSide
from trading.domain.identity import AccountKey, AccountType, AssetId, InstitutionId, InstrumentId, VenueId
from trading.domain.ledger import Ledger, LedgerBook
from trading.domain.market_data import OrderBookLevel, OrderBookSnapshot, Quote
from trading.domain.product import (
    ContractType, CryptoSpotSpec, EquitySpec, ExerciseStyle, ListedOptionSpec, OptionRight,
    PerpetualSpec, ProductType, SettlementSession, SettlementType,
)
from trading.reference import (
    AssetDefinition, AssetType, ListingDefinition, ListingId, ReferenceCatalog,
    TradingRules, VenueDefinition, VenueType,
)
from trading.reference.factory import publish_instrument
from trading.products.equity.corporate_actions import CorporateActionService
from trading.products.listed_option.lifecycle import OptionLifecycleService, PhysicalOptionEvent, PhysicalOptionEventType
from trading.products.perpetual.funding import FundingEngine
from trading.domain.corporate_action import CashDividendEvent
from trading.storage.codec import to_primitive
from trading.strategies.cash_and_carry import CashAndCarryConfig, CashAndCarryStrategy
from trading.strategies.covered_call import CoveredCallStrategy
from trading.strategies import GovernedStrategyRuntime,StrategyContext
from trading.strategies.specs import builtin_strategy_specs
from trading.backtest.feed import MarketSlice
from trading.research.snapshot import InstrumentSnapshot


@dataclass(frozen=True, slots=True)
class ReferenceScenarioResult:
    strategy: str
    model: str
    final_cash: Decimal
    ledger_transactions: int
    audit_hash: str
    strategy_spec_hash: str
    execution_policy_id: str


def _publish(catalog, instrument_id, product_type, name, spec, currency, venue, symbol, effective_from, *, minimum_notional=None, deliverable_asset=None):
    equity_like = product_type in {ProductType.EQUITY, ProductType.LISTED_OPTION}
    asset_ids = {currency}
    for field in ("base_asset", "quote_asset", "underlying_asset", "settlement_asset", "premium_asset"):
        value = getattr(spec, field, None)
        if isinstance(value, AssetId):
            asset_ids.add(value)
    if deliverable_asset is not None:
        asset_ids.add(deliverable_asset)
    assets = tuple(AssetDefinition(
        asset, AssetType.SECURITY if asset == deliverable_asset else AssetType.FIAT if asset.value == "USD" else AssetType.CRYPTO,
        asset.value, effective_from, decimals=2 if asset.value == "USD" else 8,
    ) for asset in asset_ids if not any(item.asset_id == asset for item in catalog.assets.values()))
    return publish_instrument(
        catalog, instrument_id=instrument_id, instrument_type=product_type, display_name=name,
        contract_spec=spec, trading_currency=currency,
        listings=(ListingDefinition(
            ListingId(f"listing:{venue.value}:{instrument_id.value}"), instrument_id, venue, symbol, currency,
            TradingRules(
                Decimal("0.01") if equity_like else Decimal("0.1"),
                Decimal("1") if equity_like else Decimal("0.001"),
                Decimal("1") if equity_like else Decimal("0.001"),
                minimum_notional=minimum_notional,
            ), effective_from,
        ),), effective_from=effective_from,
        asset_definitions=assets,
        venue_definitions=() if catalog.venues.values() else (VenueDefinition(
            venue, VenueType.EXCHANGE if equity_like else VenueType.CRYPTO_EXCHANGE,
            venue.value, "UTC", effective_from,
        ),),
        physical_deliverable_asset=deliverable_asset,
    )


def run_reference_scenario(strategy: str, model: str) -> ReferenceScenarioResult:
    if model not in {"conservative", "stress"}:
        raise ValueError("reference scenario model must be conservative or stress")
    if strategy == "covered-call":
        return _covered_call(model)
    if strategy == "spot-perp-carry":
        return _spot_perp_carry(model)
    raise ValueError(f"unsupported reference scenario strategy: {strategy}")


def _covered_call(model: str) -> ReferenceScenarioResult:
    now, venue = datetime(2026, 7, 14, 14, tzinfo=timezone.utc), VenueId("simulation")
    stock_id, option_id = InstrumentId("equity:aapl"), InstrumentId("option:aapl:call")
    expiry = now + timedelta(days=30)
    catalog, ledger = ReferenceCatalog(), Ledger()
    effective_from = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _publish(catalog, stock_id, ProductType.EQUITY, "AAPL", EquitySpec("NASDAQ", "US", AssetId("USD")), AssetId("USD"), venue, "AAPL", effective_from)
    _publish(catalog, option_id, ProductType.LISTED_OPTION, "AAPL-CALL", ListedOptionSpec(stock_id, expiry, Decimal("105"), OptionRight.CALL, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), expiry), AssetId("USD"), venue, "AAPL-CALL", effective_from, deliverable_asset=AssetId("AAPL"))
    service = LedgerService(ledger, catalog)
    account = AccountKey(InstitutionId("backtest"), "covered-call", AccountType.SECURITIES_MARGIN)
    service.deposit(account, AssetId("USD"), Decimal("20000"), now, "initial")
    strategy = CoveredCallStrategy(stock_id, option_id)
    runtime=_runtime(strategy);market=_slice(now,((stock_id,"100","100.10"),(option_id,"2","2.10")))
    stock_intent = runtime.on_market(_context(market,(),catalog)).intents[0]
    base = EquityTopOfBookFillModel(Decimal("0.005"))
    fill_model = StressWrapperFillModel(base, adverse_bps=Decimal("10"), fee_multiplier=Decimal("2")) if model == "stress" else base
    stock_fill = fill_model.attempt(
        SingleAssetOrder(_id("cc-stock"), stock_id, TradeSide.BUY, stock_intent.target_quantity, now),
        Quote(stock_id, Decimal("100"), Decimal("100.10"), Decimal("1000"), Decimal("1000"), now),
    ).fill
    service.trade(TradeExecution(_id("cc-stock-exec"), now + timedelta(seconds=1), account, stock_id, TradeSide.BUY, stock_fill.quantity, stock_fill.price, AssetId("USD"), stock_fill.fee, "stock-order"))
    option_intent = runtime.on_market(_context(market,((stock_id,stock_fill.quantity),),catalog)).intents[0]
    option_fill = fill_model.attempt(
        SingleAssetOrder(_id("cc-option"), option_id, TradeSide.SELL, option_intent.contracts, now),
        Quote(option_id, Decimal("2"), Decimal("2.10"), Decimal("10"), Decimal("10"), now),
    ).fill
    service.trade(TradeExecution(_id("cc-option-exec"), now + timedelta(seconds=2), account, option_id, TradeSide.SELL, option_fill.quantity, option_fill.price, AssetId("USD"), option_fill.fee, "option-order"))
    CorporateActionService(service).apply_dividend(account, CashDividendEvent(_id("cc-dividend"), stock_id, now + timedelta(days=10), now + timedelta(days=12), AssetId("USD"), Decimal("0.50")))
    OptionLifecycleService(service).apply(PhysicalOptionEvent(
        _id("cc-assignment"), PhysicalOptionEventType.ASSIGNMENT, account, option_id,
        Decimal("1"), expiry, Decimal("110"),
    ))
    return _result("covered-call", model, ledger, (account,), AssetId("USD"))


def _spot_perp_carry(model: str) -> ReferenceScenarioResult:
    now, venue = datetime(2026, 7, 14, 14, tzinfo=timezone.utc), VenueId("simulation")
    spot_id, perp_id = InstrumentId("crypto:spot:btcusdt"), InstrumentId("crypto:perp:btcusdt")
    catalog, ledger = ReferenceCatalog(), Ledger()
    effective_from = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _publish(catalog, spot_id, ProductType.CRYPTO_SPOT, "BTCUSDT", CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")), AssetId("USDT"), venue, "BTCUSDT", effective_from, minimum_notional=Decimal("10"))
    _publish(catalog, perp_id, ProductType.PERPETUAL, "BTCUSDT-PERP", PerpetualSpec(AssetId("BTC"), AssetId("USDT"), "BTCUSDT", Decimal("1"), ContractType.LINEAR, 28800), AssetId("USDT"), venue, "BTCUSDT-PERP", effective_from)
    service = LedgerService(ledger, catalog)
    spot_account = AccountKey(InstitutionId("backtest"), "spot", AccountType.CRYPTO_SPOT)
    derivative_account = AccountKey(InstitutionId("backtest"), "perp", AccountType.DERIVATIVES)
    service.deposit(spot_account, AssetId("USDT"), Decimal("10000"), now, "initial")
    service.transfer(spot_account, derivative_account, AssetId("USDT"), Decimal("2000"), now + timedelta(seconds=1), "collateral")
    base = CryptoOrderBookFillModel(Decimal("0.001"))
    fill_model = StressWrapperFillModel(base, adverse_bps=Decimal("10"), fee_multiplier=Decimal("2")) if model == "stress" else base
    carry_strategy = CashAndCarryStrategy(
        spot_id, perp_id, CashAndCarryConfig(minimum_annualized_basis=Decimal("0.001")),
    )
    carry_market=_slice(now,((spot_id,"49999","50001"),(perp_id,"50100","50102")))
    carry_intent = _runtime(carry_strategy).on_market(_context(carry_market,(),catalog)).intents[0]
    events = (
        ("spot-open", spot_account, spot_id, TradeSide.BUY, _book(spot_id, now + timedelta(seconds=2), "49999", "50001")),
        ("perp-open", derivative_account, perp_id, TradeSide.SELL, _book(perp_id, now + timedelta(seconds=3), "50100", "50102")),
        ("spot-close", spot_account, spot_id, TradeSide.SELL, _book(spot_id, now + timedelta(hours=8), "50500", "50502")),
        ("perp-close", derivative_account, perp_id, TradeSide.BUY, _book(perp_id, now + timedelta(hours=8, seconds=1), "50398", "50400")),
    )
    opening_quantities = {spot_id: abs(carry_intent.spot_quantity), perp_id: abs(carry_intent.derivative_quantity)}
    for label, account, instrument_id, side, book in events[:2]:
        fill = fill_model.attempt(SingleAssetOrder(_id(label), instrument_id, side, opening_quantities[instrument_id], book.event_time), book).fill
        service.trade(TradeExecution(_id(f"{label}-exec"), book.event_time, account, instrument_id, side, fill.quantity, fill.price, AssetId("USDT"), fill.fee, label))
    FundingEngine(service).apply(derivative_account, perp_id, Decimal("-0.1"), Decimal("50200"), Decimal("0.0001"), now + timedelta(hours=4))
    for label, account, instrument_id, side, book in events[2:]:
        fill = fill_model.attempt(SingleAssetOrder(_id(label), instrument_id, side, Decimal("0.1"), book.event_time), book).fill
        service.trade(TradeExecution(_id(f"{label}-exec"), book.event_time, account, instrument_id, side, fill.quantity, fill.price, AssetId("USDT"), fill.fee, label))
    return _result("spot-perp-carry", model, ledger, (spot_account, derivative_account), AssetId("USDT"))


def _book(instrument_id, timestamp, bid, ask):
    return OrderBookSnapshot(
        instrument_id, (OrderBookLevel(Decimal(bid), Decimal("10")),),
        (OrderBookLevel(Decimal(ask), Decimal("10")),), 1, timestamp,
    )


def _slice(at,values):
    snapshots=tuple(InstrumentSnapshot(instrument,Quote(instrument,Decimal(bid),Decimal(ask),Decimal("10"),Decimal("10"),at),at,None,None,None,None)
        for instrument,bid,ask in values)
    return MarketSlice(at,snapshots,sequence=1,available_instruments=tuple(item[0] for item in values))
def _context(market,positions,catalog):return StrategyContext(market,object(),(),catalog,approved_capital=Decimal("10000"),strategy_positions=positions)
def _runtime(strategy):
    spec,policy=next(item for item in builtin_strategy_specs() if item[0].strategy_id==strategy.strategy_id)
    return GovernedStrategyRuntime(strategy,spec,execution_policy_id=policy.policy_id)


def _id(value):
    return uuid5(NAMESPACE_URL, f"reference-scenario:{value}")


def _result(strategy, model, ledger, accounts, asset):
    final_cash = sum((ledger.book_balance(account, LedgerBook.CASH, asset) for account in accounts), Decimal("0"))
    material = json.dumps(to_primitive(ledger.transactions), sort_keys=True, separators=(",", ":"))
    from trading.strategies.specs import builtin_strategy_specs
    canonical={"covered-call":"covered-call-v1","spot-perp-carry":"spot-perpetual-carry-v1"}[strategy]
    spec,policy=next(value for value in builtin_strategy_specs() if value[0].strategy_id==canonical)
    return ReferenceScenarioResult(strategy, model, final_cash, len(ledger.transactions), sha256(material.encode()).hexdigest(),spec.spec_hash,policy.policy_id)
