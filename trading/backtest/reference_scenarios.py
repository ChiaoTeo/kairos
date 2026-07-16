from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from trading.accounting.ledger import LedgerService
from trading.backtest.fill import CryptoOrderBookFillModel, EquityTopOfBookFillModel, SingleAssetOrder, StressWrapperFillModel
from trading.catalog.service import InstrumentCatalog
from trading.domain.execution import TradeExecution, TradeSide
from trading.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.ledger import Ledger, LedgerBook
from trading.domain.market_data import OrderBookLevel, OrderBookSnapshot, Quote
from trading.domain.product import (
    ContractType, CryptoSpotSpec, EquitySpec, ExerciseStyle, ListedOptionSpec, OptionRight,
    PerpetualSpec, ProductType, SettlementSession, SettlementType,
)
from trading.products.equity.corporate_actions import CorporateActionService
from trading.products.listed_option.lifecycle import OptionLifecycleService, PhysicalOptionEvent, PhysicalOptionEventType
from trading.products.perpetual.funding import FundingEngine
from trading.domain.corporate_action import CashDividendEvent
from trading.storage.codec import to_primitive
from trading.strategies.cash_and_carry import CashAndCarryConfig, CashAndCarryStrategy
from trading.strategies.covered_call import CoveredCallStrategy


@dataclass(frozen=True, slots=True)
class ReferenceScenarioResult:
    strategy: str
    model: str
    final_cash: Decimal
    ledger_transactions: int
    audit_hash: str
    strategy_spec_hash: str
    execution_policy_id: str


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
    stock = InstrumentDefinition(
        stock_id, ProductType.EQUITY, "AAPL", AssetId("AAPL"), AssetId("USD"),
        EquitySpec("NASDAQ", "US", AssetId("USD")),
        (VenueListing(venue, "AAPL", "AAPL", Decimal("0.01"), Decimal("1"), Decimal("1")),),
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    option = InstrumentDefinition(
        option_id, ProductType.LISTED_OPTION, "AAPL-CALL", None, AssetId("USD"),
        ListedOptionSpec(stock_id, expiry, Decimal("105"), OptionRight.CALL, ExerciseStyle.AMERICAN, SettlementType.PHYSICAL, SettlementSession.PM, Decimal("100"), expiry),
        (VenueListing(venue, "AAPL-CALL", "AAPL-CALL", Decimal("0.01"), Decimal("1"), Decimal("1")),),
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    catalog, ledger = InstrumentCatalog(), Ledger()
    catalog.add(stock); catalog.add(option)
    service = LedgerService(ledger, catalog)
    account = AccountKey(venue, "covered-call", AccountType.SECURITIES_MARGIN)
    service.deposit(account, AssetId("USD"), Decimal("20000"), now, "initial")
    strategy = CoveredCallStrategy(stock_id, option_id)
    stock_intent = strategy.intents(Decimal("0"), Decimal("0"))[0]
    base = EquityTopOfBookFillModel(Decimal("0.005"))
    fill_model = StressWrapperFillModel(base, adverse_bps=Decimal("10"), fee_multiplier=Decimal("2")) if model == "stress" else base
    stock_fill = fill_model.attempt(
        SingleAssetOrder(_id("cc-stock"), stock_id, TradeSide.BUY, stock_intent.target_quantity, now),
        Quote(stock_id, Decimal("100"), Decimal("100.10"), Decimal("1000"), Decimal("1000"), now),
    ).fill
    service.trade(TradeExecution(_id("cc-stock-exec"), now + timedelta(seconds=1), account, stock_id, TradeSide.BUY, stock_fill.quantity, stock_fill.price, AssetId("USD"), stock_fill.fee, "stock-order"))
    option_intent = strategy.intents(stock_fill.quantity, Decimal("0"))[0]
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
    spot = InstrumentDefinition(
        spot_id, ProductType.CRYPTO_SPOT, "BTCUSDT", AssetId("BTC"), AssetId("USDT"),
        CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")),
        (VenueListing(venue, "BTCUSDT", "BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("10")),),
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    perp = InstrumentDefinition(
        perp_id, ProductType.PERPETUAL, "BTCUSDT-PERP", AssetId("BTC"), AssetId("USDT"),
        PerpetualSpec(AssetId("BTC"), AssetId("USDT"), "BTCUSDT", Decimal("1"), ContractType.LINEAR, 28800),
        (VenueListing(venue, "BTCUSDT-PERP", "BTCUSDT-PERP", Decimal("0.1"), Decimal("0.001"), Decimal("0.001")),),
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    catalog, ledger = InstrumentCatalog(), Ledger()
    catalog.add(spot); catalog.add(perp)
    service = LedgerService(ledger, catalog)
    spot_account = AccountKey(venue, "spot", AccountType.CRYPTO_SPOT)
    derivative_account = AccountKey(venue, "perp", AccountType.DERIVATIVES)
    service.deposit(spot_account, AssetId("USDT"), Decimal("10000"), now, "initial")
    service.transfer(spot_account, derivative_account, AssetId("USDT"), Decimal("2000"), now + timedelta(seconds=1), "collateral")
    base = CryptoOrderBookFillModel(Decimal("0.001"))
    fill_model = StressWrapperFillModel(base, adverse_bps=Decimal("10"), fee_multiplier=Decimal("2")) if model == "stress" else base
    carry_intent = CashAndCarryStrategy(
        spot_id, perp_id, CashAndCarryConfig(minimum_annualized_basis=Decimal("0.001")),
    ).intent(Decimal("50001"), Decimal("50100"), Decimal("0"), Decimal("0"))
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


def _id(value):
    return uuid5(NAMESPACE_URL, f"reference-scenario:{value}")


def _result(strategy, model, ledger, accounts, asset):
    final_cash = sum((ledger.book_balance(account, LedgerBook.CASH, asset) for account in accounts), Decimal("0"))
    material = json.dumps(to_primitive(ledger.transactions), sort_keys=True, separators=(",", ":"))
    from trading.strategies.specs import builtin_strategy_specs
    canonical={"covered-call":"covered-call-v1","spot-perp-carry":"spot-perpetual-carry-v1"}[strategy]
    spec,policy=next(value for value in builtin_strategy_specs() if value[0].strategy_id==canonical)
    return ReferenceScenarioResult(strategy, model, final_cash, len(ledger.transactions), sha256(material.encode()).hexdigest(),spec.spec_hash,policy.policy_id)
