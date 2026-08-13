#!/usr/bin/env python3
"""Generate deterministic FlatBuffers fixtures for active Strategy wire roots.

The emitted JSON is textual so it can be reviewed and consumed by both Rust
and Python tests. Timestamps and identities are intentionally fixed.
"""

from __future__ import annotations

import json
import sys

import flatbuffers

from kairospy.infrastructure.transport.generated import kairos as generated_kairos


sys.modules.setdefault("kairos", generated_kairos)

from kairos.common.v1 import Decimal64, MessageHeader, SnapshotHeader
from kairos.account.v1 import AccountChange, AccountEvent, Accounts, AccountsSnapshot
from kairos.execution.v1 import ExecutionChange, ExecutionEventMessage, Order, Orders, OrdersSnapshot
from kairos.intent.v1 import IntentSnapshot, Intents
from kairos.market.v1 import (
    Bar,
    BarMessage,
    Greeks,
    GreeksMessage,
    MarketData,
    MarketDataSnapshot,
    Quote,
    QuoteMessage,
    Trade,
    TradeMessage,
    Rate,
    RateMessage,
    Ticker24h,
    Ticker24hMessage,
    MarkPrice,
    MarkPriceMessage,
    IndexPrice,
    IndexPriceMessage,
    FundingRate,
    FundingRateMessage,
    OpenInterest,
    OpenInterestMessage,
    InstrumentStatus,
    InstrumentStatusMessage,
    OrderBook,
    OrderBookMessage,
    OrderBooks,
    OrderBookSnapshot,
)
from kairos.risk.v1 import Risk, RiskEventMessage, RiskSnapshot


def _message_header(builder: flatbuffers.Builder, sequence: int) -> int:
    message_id = builder.CreateString(f"fixture:{sequence}")
    stream_id = builder.CreateString("market.events")
    producer_id = builder.CreateString("market:fixture")
    workspace_id = builder.CreateString("workspace:fixture")
    launch_id = builder.CreateString("launch:fixture")
    instance_id = builder.CreateString("instance:fixture")
    MessageHeader.MessageHeaderStart(builder)
    MessageHeader.MessageHeaderAddMessageId(builder, message_id)
    MessageHeader.MessageHeaderAddStreamId(builder, stream_id)
    MessageHeader.MessageHeaderAddProducerId(builder, producer_id)
    MessageHeader.MessageHeaderAddWorkspaceId(builder, workspace_id)
    MessageHeader.MessageHeaderAddLaunchId(builder, launch_id)
    MessageHeader.MessageHeaderAddInstanceId(builder, instance_id)
    MessageHeader.MessageHeaderAddSequence(builder, sequence)
    MessageHeader.MessageHeaderAddEventTimeUnixNanos(builder, 1_000 + sequence)
    MessageHeader.MessageHeaderAddPublishTimeUnixNanos(builder, 2_000 + sequence)
    return MessageHeader.MessageHeaderEnd(builder)


def _snapshot_header(builder: flatbuffers.Builder) -> int:
    snapshot_id = builder.CreateString("market:fixture:7")
    view_key = builder.CreateString("market.current")
    actor_id = builder.CreateString("market:fixture")
    workspace_id = builder.CreateString("workspace:fixture")
    launch_id = builder.CreateString("launch:fixture")
    instance_id = builder.CreateString("instance:fixture")
    SnapshotHeader.SnapshotHeaderStart(builder)
    SnapshotHeader.SnapshotHeaderAddSnapshotId(builder, snapshot_id)
    SnapshotHeader.SnapshotHeaderAddViewKey(builder, view_key)
    SnapshotHeader.SnapshotHeaderAddOwnerActorId(builder, actor_id)
    SnapshotHeader.SnapshotHeaderAddWorkspaceId(builder, workspace_id)
    SnapshotHeader.SnapshotHeaderAddLaunchId(builder, launch_id)
    SnapshotHeader.SnapshotHeaderAddInstanceId(builder, instance_id)
    SnapshotHeader.SnapshotHeaderAddVersion(builder, 1)
    SnapshotHeader.SnapshotHeaderAddGeneration(builder, 7)
    SnapshotHeader.SnapshotHeaderAddGeneratedAtUnixNanos(builder, 2_000)
    SnapshotHeader.SnapshotHeaderAddAsOfUnixNanos(builder, 1_000)
    SnapshotHeader.SnapshotHeaderAddComplete(builder, True)
    return SnapshotHeader.SnapshotHeaderEnd(builder)


def _generic_snapshot_header(
    builder: flatbuffers.Builder, *, owner: str, view: str
) -> int:
    snapshot_id = builder.CreateString(f"{view}:7")
    view_key = builder.CreateString(view)
    actor_id = builder.CreateString(owner)
    SnapshotHeader.SnapshotHeaderStart(builder)
    SnapshotHeader.SnapshotHeaderAddSnapshotId(builder, snapshot_id)
    SnapshotHeader.SnapshotHeaderAddViewKey(builder, view_key)
    SnapshotHeader.SnapshotHeaderAddOwnerActorId(builder, actor_id)
    SnapshotHeader.SnapshotHeaderAddVersion(builder, 1)
    SnapshotHeader.SnapshotHeaderAddGeneration(builder, 7)
    SnapshotHeader.SnapshotHeaderAddGeneratedAtUnixNanos(builder, 2_000)
    SnapshotHeader.SnapshotHeaderAddAsOfUnixNanos(builder, 1_000)
    SnapshotHeader.SnapshotHeaderAddComplete(builder, True)
    return SnapshotHeader.SnapshotHeaderEnd(builder)


def _business_header(builder: flatbuffers.Builder, domain: str) -> int:
    message_id = builder.CreateString(f"{domain}:fixture:1")
    stream_id = builder.CreateString(f"{domain}.events")
    producer_id = builder.CreateString(f"{domain}:fixture")
    workspace_id = builder.CreateString("workspace:fixture")
    launch_id = builder.CreateString("launch:fixture")
    instance_id = builder.CreateString("instance:fixture")
    MessageHeader.MessageHeaderStart(builder)
    MessageHeader.MessageHeaderAddMessageId(builder, message_id)
    MessageHeader.MessageHeaderAddStreamId(builder, stream_id)
    MessageHeader.MessageHeaderAddProducerId(builder, producer_id)
    MessageHeader.MessageHeaderAddWorkspaceId(builder, workspace_id)
    MessageHeader.MessageHeaderAddLaunchId(builder, launch_id)
    MessageHeader.MessageHeaderAddInstanceId(builder, instance_id)
    MessageHeader.MessageHeaderAddSequence(builder, 1)
    MessageHeader.MessageHeaderAddEventTimeUnixNanos(builder, 1_001)
    MessageHeader.MessageHeaderAddPublishTimeUnixNanos(builder, 2_001)
    return MessageHeader.MessageHeaderEnd(builder)


def _account_event() -> bytes:
    builder = flatbuffers.Builder(512)
    kind = builder.CreateString("status_changed")
    segment = builder.CreateString("spot")
    status = builder.CreateString("ready")
    AccountChange.AccountChangeStart(builder)
    AccountChange.AccountChangeAddKind(builder, kind)
    AccountChange.AccountChangeAddSegmentKey(builder, segment)
    AccountChange.AccountChangeAddStatus(builder, status)
    AccountChange.AccountChangeAddTradingEnabled(builder, True)
    change = AccountChange.AccountChangeEnd(builder)
    AccountEvent.AccountEventStartChangesVector(builder, 1)
    builder.PrependUOffsetTRelative(change)
    changes = builder.EndVector()
    account_id = builder.CreateString("account:fixture")
    header = _business_header(builder, "account")
    AccountEvent.AccountEventStart(builder)
    AccountEvent.AccountEventAddHeader(builder, header)
    AccountEvent.AccountEventAddAccountId(builder, account_id)
    AccountEvent.AccountEventAddChanges(builder, changes)
    AccountEvent.AccountEventAddOccurredAtUnixNanos(builder, 1_001)
    root = AccountEvent.AccountEventEnd(builder)
    builder.Finish(root, file_identifier=b"ACE1")
    return bytes(builder.Output())


def _account_snapshot() -> bytes:
    builder = flatbuffers.Builder(256)
    Accounts.AccountsStart(builder)
    payload = Accounts.AccountsEnd(builder)
    header = _generic_snapshot_header(
        builder, owner="account:fixture", view="account.current"
    )
    AccountsSnapshot.AccountsSnapshotStart(builder)
    AccountsSnapshot.AccountsSnapshotAddHeader(builder, header)
    AccountsSnapshot.AccountsSnapshotAddPayload(builder, payload)
    root = AccountsSnapshot.AccountsSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"AAC1")
    return bytes(builder.Output())


def _execution_event() -> bytes:
    builder = flatbuffers.Builder(768)
    order_id = builder.CreateString("order:fixture")
    intent_id = builder.CreateString("intent:fixture")
    strategy_id = builder.CreateString("strategy:fixture")
    account_id = builder.CreateString("account:fixture")
    instrument_id = builder.CreateString("instrument:fixture")
    market_id = builder.CreateString("market:fixture")
    status = builder.CreateString("accepted")
    Order.OrderStart(builder)
    Order.OrderAddOrderId(builder, order_id)
    Order.OrderAddIntentId(builder, intent_id)
    Order.OrderAddStrategyId(builder, strategy_id)
    Order.OrderAddAccountId(builder, account_id)
    Order.OrderAddInstrumentId(builder, instrument_id)
    Order.OrderAddMarketId(builder, market_id)
    Order.OrderAddStatus(builder, status)
    Order.OrderAddSide(builder, 1)
    Order.OrderAddOrderType(builder, 1)
    Order.OrderAddQuantity(builder, Decimal64.CreateDecimal64(builder, 2, 0))
    Order.OrderAddFilledQuantity(builder, Decimal64.CreateDecimal64(builder, 0, 0))
    Order.OrderAddRemainingQuantity(builder, Decimal64.CreateDecimal64(builder, 2, 0))
    Order.OrderAddCreatedAtUnixNanos(builder, 1_001)
    Order.OrderAddUpdatedAtUnixNanos(builder, 1_001)
    order = Order.OrderEnd(builder)
    kind = builder.CreateString("order_update")
    change_strategy = builder.CreateString("strategy:fixture")
    change_account = builder.CreateString("account:fixture")
    ExecutionChange.ExecutionChangeStart(builder)
    ExecutionChange.ExecutionChangeAddKind(builder, kind)
    ExecutionChange.ExecutionChangeAddStrategyId(builder, change_strategy)
    ExecutionChange.ExecutionChangeAddAccountId(builder, change_account)
    ExecutionChange.ExecutionChangeAddOrder(builder, order)
    change = ExecutionChange.ExecutionChangeEnd(builder)
    ExecutionEventMessage.ExecutionEventMessageStartChangesVector(builder, 1)
    builder.PrependUOffsetTRelative(change)
    changes = builder.EndVector()
    header = _business_header(builder, "execution")
    ExecutionEventMessage.ExecutionEventMessageStart(builder)
    ExecutionEventMessage.ExecutionEventMessageAddHeader(builder, header)
    ExecutionEventMessage.ExecutionEventMessageAddChanges(builder, changes)
    ExecutionEventMessage.ExecutionEventMessageAddOccurredAtUnixNanos(builder, 1_001)
    root = ExecutionEventMessage.ExecutionEventMessageEnd(builder)
    builder.Finish(root, file_identifier=b"EXE1")
    return bytes(builder.Output())


def _orders_snapshot() -> bytes:
    builder = flatbuffers.Builder(256)
    Orders.OrdersStart(builder)
    payload = Orders.OrdersEnd(builder)
    header = _generic_snapshot_header(
        builder, owner="execution:fixture", view="execution.orders"
    )
    OrdersSnapshot.OrdersSnapshotStart(builder)
    OrdersSnapshot.OrdersSnapshotAddHeader(builder, header)
    OrdersSnapshot.OrdersSnapshotAddPayload(builder, payload)
    root = OrdersSnapshot.OrdersSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"PEO1")
    return bytes(builder.Output())


def _intents_snapshot() -> bytes:
    builder = flatbuffers.Builder(256)
    Intents.IntentsStart(builder)
    payload = Intents.IntentsEnd(builder)
    header = _generic_snapshot_header(
        builder, owner="execution:fixture", view="execution.intents"
    )
    IntentSnapshot.IntentSnapshotStart(builder)
    IntentSnapshot.IntentSnapshotAddHeader(builder, header)
    IntentSnapshot.IntentSnapshotAddPayload(builder, payload)
    root = IntentSnapshot.IntentSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"PIJ1")
    return bytes(builder.Output())


def _risk_event() -> bytes:
    builder = flatbuffers.Builder(512)
    kind = builder.CreateString("decision_evaluated")
    account_id = builder.CreateString("account:fixture")
    strategy_id = builder.CreateString("strategy:fixture")
    decision_id = builder.CreateString("decision:fixture")
    request_id = builder.CreateString("request:fixture")
    reason = builder.CreateString("limit_exceeded")
    RiskEventMessage.RiskEventMessageStartReasonCodesVector(builder, 1)
    builder.PrependUOffsetTRelative(reason)
    reasons = builder.EndVector()
    violation = builder.CreateString("notional limit")
    RiskEventMessage.RiskEventMessageStartViolationsVector(builder, 1)
    builder.PrependUOffsetTRelative(violation)
    violations = builder.EndVector()
    header = _business_header(builder, "risk")
    RiskEventMessage.RiskEventMessageStart(builder)
    RiskEventMessage.RiskEventMessageAddHeader(builder, header)
    RiskEventMessage.RiskEventMessageAddKind(builder, kind)
    RiskEventMessage.RiskEventMessageAddAccountId(builder, account_id)
    RiskEventMessage.RiskEventMessageAddStrategyId(builder, strategy_id)
    RiskEventMessage.RiskEventMessageAddDecisionId(builder, decision_id)
    RiskEventMessage.RiskEventMessageAddRequestId(builder, request_id)
    RiskEventMessage.RiskEventMessageAddAllowed(builder, False)
    RiskEventMessage.RiskEventMessageAddDegraded(builder, True)
    RiskEventMessage.RiskEventMessageAddReasonCodes(builder, reasons)
    RiskEventMessage.RiskEventMessageAddViolations(builder, violations)
    RiskEventMessage.RiskEventMessageAddOccurredAtUnixNanos(builder, 1_001)
    root = RiskEventMessage.RiskEventMessageEnd(builder)
    builder.Finish(root, file_identifier=b"RKE1")
    return bytes(builder.Output())


def _risk_snapshot() -> bytes:
    builder = flatbuffers.Builder(256)
    Risk.RiskStart(builder)
    payload = Risk.RiskEnd(builder)
    header = _generic_snapshot_header(builder, owner="risk:fixture", view="risk.budgets")
    RiskSnapshot.RiskSnapshotStart(builder)
    RiskSnapshot.RiskSnapshotAddHeader(builder, header)
    RiskSnapshot.RiskSnapshotAddPayload(builder, payload)
    root = RiskSnapshot.RiskSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"PRK1")
    return bytes(builder.Output())


def _quote() -> bytes:
    builder = flatbuffers.Builder(512)
    instrument = builder.CreateString("instrument:fixture")
    market = builder.CreateString("market:fixture")
    source = builder.CreateString("fixture")
    Quote.QuoteStart(builder)
    Quote.QuoteAddInstrumentId(builder, instrument)
    Quote.QuoteAddMarketId(builder, market)
    Quote.QuoteAddBidPrice(builder, Decimal64.CreateDecimal64(builder, 10025, 2))
    Quote.QuoteAddBidQuantity(builder, Decimal64.CreateDecimal64(builder, 3, 0))
    Quote.QuoteAddAskPrice(builder, Decimal64.CreateDecimal64(builder, 10075, 2))
    Quote.QuoteAddAskQuantity(builder, Decimal64.CreateDecimal64(builder, 4, 0))
    Quote.QuoteAddEventTimeUnixNanos(builder, 1_001)
    Quote.QuoteAddSourceId(builder, source)
    payload = Quote.QuoteEnd(builder)
    header = _message_header(builder, 1)
    QuoteMessage.QuoteMessageStart(builder)
    QuoteMessage.QuoteMessageAddHeader(builder, header)
    QuoteMessage.QuoteMessageAddPayload(builder, payload)
    root = QuoteMessage.QuoteMessageEnd(builder)
    builder.Finish(root, file_identifier=b"MQT1")
    return bytes(builder.Output())


def _trade() -> bytes:
    builder = flatbuffers.Builder(512)
    trade_id = builder.CreateString("trade:fixture")
    instrument = builder.CreateString("instrument:fixture")
    market = builder.CreateString("market:fixture")
    source = builder.CreateString("fixture")
    Trade.TradeStart(builder)
    Trade.TradeAddTradeId(builder, trade_id)
    Trade.TradeAddInstrumentId(builder, instrument)
    Trade.TradeAddMarketId(builder, market)
    Trade.TradeAddPrice(builder, Decimal64.CreateDecimal64(builder, 10050, 2))
    Trade.TradeAddQuantity(builder, Decimal64.CreateDecimal64(builder, 2, 0))
    Trade.TradeAddEventTimeUnixNanos(builder, 1_002)
    Trade.TradeAddSourceId(builder, source)
    payload = Trade.TradeEnd(builder)
    header = _message_header(builder, 2)
    TradeMessage.TradeMessageStart(builder)
    TradeMessage.TradeMessageAddHeader(builder, header)
    TradeMessage.TradeMessageAddPayload(builder, payload)
    root = TradeMessage.TradeMessageEnd(builder)
    builder.Finish(root, file_identifier=b"MTR1")
    return bytes(builder.Output())


def _bar() -> bytes:
    builder = flatbuffers.Builder(512)
    market = builder.CreateString("market:fixture")
    instrument = builder.CreateString("instrument:fixture")
    timeframe = builder.CreateString("1m")
    source = builder.CreateString("fixture")
    derivation = builder.CreateString("completed")
    bar_kind = builder.CreateString("trade_bar")
    Bar.BarStart(builder)
    Bar.BarAddMarketId(builder, market)
    Bar.BarAddInstrumentId(builder, instrument)
    Bar.BarAddTimeframe(builder, timeframe)
    Bar.BarAddOpen(builder, Decimal64.CreateDecimal64(builder, 10000, 2))
    Bar.BarAddHigh(builder, Decimal64.CreateDecimal64(builder, 10100, 2))
    Bar.BarAddLow(builder, Decimal64.CreateDecimal64(builder, 9950, 2))
    Bar.BarAddClose(builder, Decimal64.CreateDecimal64(builder, 10050, 2))
    Bar.BarAddVolume(builder, Decimal64.CreateDecimal64(builder, 25, 0))
    Bar.BarAddEventTimeUnixNanos(builder, 1_003)
    Bar.BarAddSourceId(builder, source)
    Bar.BarAddDerivation(builder, derivation)
    Bar.BarAddBarKind(builder, bar_kind)
    payload = Bar.BarEnd(builder)
    header = _message_header(builder, 3)
    BarMessage.BarMessageStart(builder)
    BarMessage.BarMessageAddHeader(builder, header)
    BarMessage.BarMessageAddPayload(builder, payload)
    root = BarMessage.BarMessageEnd(builder)
    builder.Finish(root, file_identifier=b"MBA1")
    return bytes(builder.Output())


def _greeks() -> bytes:
    builder = flatbuffers.Builder(512)
    market = builder.CreateString("market:fixture")
    instrument = builder.CreateString("instrument:option:fixture")
    source = builder.CreateString("fixture")
    derivation = builder.CreateString("provider")
    Greeks.GreeksStart(builder)
    Greeks.GreeksAddMarketId(builder, market)
    Greeks.GreeksAddInstrumentId(builder, instrument)
    Greeks.GreeksAddExpiryUnixNanos(builder, 9_999)
    Greeks.GreeksAddStrike(builder, Decimal64.CreateDecimal64(builder, 10000, 2))
    Greeks.GreeksAddDelta(builder, Decimal64.CreateDecimal64(builder, 525, 3))
    Greeks.GreeksAddGamma(builder, Decimal64.CreateDecimal64(builder, 15, 3))
    Greeks.GreeksAddVega(builder, Decimal64.CreateDecimal64(builder, 120, 3))
    Greeks.GreeksAddTheta(builder, Decimal64.CreateDecimal64(builder, -25, 3))
    Greeks.GreeksAddImpliedVolatility(
        builder, Decimal64.CreateDecimal64(builder, 225, 3)
    )
    Greeks.GreeksAddEventTimeUnixNanos(builder, 1_004)
    Greeks.GreeksAddSourceId(builder, source)
    Greeks.GreeksAddDerivation(builder, derivation)
    payload = Greeks.GreeksEnd(builder)
    header = _message_header(builder, 4)
    GreeksMessage.GreeksMessageStart(builder)
    GreeksMessage.GreeksMessageAddHeader(builder, header)
    GreeksMessage.GreeksMessageAddPayload(builder, payload)
    root = GreeksMessage.GreeksMessageEnd(builder)
    builder.Finish(root, file_identifier=b"MGR1")
    return bytes(builder.Output())


def _market_snapshot() -> bytes:
    builder = flatbuffers.Builder(512)
    MarketData.MarketDataStart(builder)
    payload = MarketData.MarketDataEnd(builder)
    header = _snapshot_header(builder)
    MarketDataSnapshot.MarketDataSnapshotStart(builder)
    MarketDataSnapshot.MarketDataSnapshotAddHeader(builder, header)
    MarketDataSnapshot.MarketDataSnapshotAddPayload(builder, payload)
    root = MarketDataSnapshot.MarketDataSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"PMC1")
    return bytes(builder.Output())


def _aux_market_message(
    *, payload_module, message_module, name: str, identifier: bytes, sequence: int
) -> bytes:
    builder = flatbuffers.Builder(512)
    market = builder.CreateString("market:fixture")
    instrument = builder.CreateString("instrument:fixture")
    source = builder.CreateString("fixture")
    rate_id = builder.CreateString("rate:fixture") if name == "Rate" else None
    basis = builder.CreateString("annualized") if name == "Rate" else None
    status = builder.CreateString("trading") if name == "InstrumentStatus" else None
    getattr(payload_module, f"{name}Start")(builder)
    if name == "Rate":
        Rate.RateAddRateId(builder, rate_id)
        Rate.RateAddBasis(builder, basis)
        Rate.RateAddValue(builder, Decimal64.CreateDecimal64(builder, 25, 4))
    else:
        getattr(payload_module, f"{name}AddMarketId")(builder, market)
        getattr(payload_module, f"{name}AddInstrumentId")(builder, instrument)
        if name == "MarkPrice":
            MarkPrice.MarkPriceAddMarkPrice(
                builder, Decimal64.CreateDecimal64(builder, 10050, 2)
            )
        elif name == "FundingRate":
            FundingRate.FundingRateAddFundingRate(
                builder, Decimal64.CreateDecimal64(builder, 25, 4)
            )
        elif name == "OpenInterest":
            OpenInterest.OpenInterestAddContracts(
                builder, Decimal64.CreateDecimal64(builder, 100, 0)
            )
        elif name == "InstrumentStatus":
            InstrumentStatus.InstrumentStatusAddStatus(builder, status)
    if name == "Rate":
        Rate.RateAddMarketId(builder, market)
        Rate.RateAddInstrumentId(builder, instrument)
    getattr(payload_module, f"{name}AddEventTimeUnixNanos")(builder, 1_000 + sequence)
    getattr(payload_module, f"{name}AddSourceId")(builder, source)
    payload = getattr(payload_module, f"{name}End")(builder)
    header = _message_header(builder, sequence)
    message_name = f"{name}Message"
    getattr(message_module, f"{message_name}Start")(builder)
    getattr(message_module, f"{message_name}AddHeader")(builder, header)
    getattr(message_module, f"{message_name}AddPayload")(builder, payload)
    root = getattr(message_module, f"{message_name}End")(builder)
    builder.Finish(root, file_identifier=identifier)
    return bytes(builder.Output())


def _orderbook_message() -> bytes:
    builder = flatbuffers.Builder(512)
    market = builder.CreateString("market:fixture")
    instrument = builder.CreateString("instrument:fixture")
    source = builder.CreateString("fixture")
    policy = builder.CreateString("top_n:10")
    OrderBook.OrderBookStart(builder)
    OrderBook.OrderBookAddMarketId(builder, market)
    OrderBook.OrderBookAddInstrumentId(builder, instrument)
    OrderBook.OrderBookAddSequence(builder, 8)
    OrderBook.OrderBookAddEventTimeUnixNanos(builder, 1_008)
    OrderBook.OrderBookAddSourceId(builder, source)
    OrderBook.OrderBookAddDepthPolicy(builder, policy)
    OrderBook.OrderBookAddFirstSequence(builder, 8)
    OrderBook.OrderBookAddLastSequence(builder, 8)
    OrderBook.OrderBookAddSynchronized(builder, True)
    payload = OrderBook.OrderBookEnd(builder)
    header = _message_header(builder, 8)
    OrderBookMessage.OrderBookMessageStart(builder)
    OrderBookMessage.OrderBookMessageAddHeader(builder, header)
    OrderBookMessage.OrderBookMessageAddPayload(builder, payload)
    root = OrderBookMessage.OrderBookMessageEnd(builder)
    builder.Finish(root, file_identifier=b"MOB1")
    return bytes(builder.Output())


def _orderbook_snapshot() -> bytes:
    builder = flatbuffers.Builder(256)
    OrderBooks.OrderBooksStart(builder)
    payload = OrderBooks.OrderBooksEnd(builder)
    header = _generic_snapshot_header(
        builder, owner="market:fixture", view="market.orderbook"
    )
    OrderBookSnapshot.OrderBookSnapshotStart(builder)
    OrderBookSnapshot.OrderBookSnapshotAddHeader(builder, header)
    OrderBookSnapshot.OrderBookSnapshotAddPayload(builder, payload)
    root = OrderBookSnapshot.OrderBookSnapshotEnd(builder)
    builder.Finish(root, file_identifier=b"PMB1")
    return bytes(builder.Output())


def main() -> None:
    fixtures = {
        "account.event.ACE1": _account_event(),
        "account.current.AAC1": _account_snapshot(),
        "execution.event.EXE1": _execution_event(),
        "execution.orders.PEO1": _orders_snapshot(),
        "execution.intents.PIJ1": _intents_snapshot(),
        "market.quote.MQT1": _quote(),
        "market.trade.MTR1": _trade(),
        "market.bar.MBA1": _bar(),
        "market.greeks.MGR1": _greeks(),
        "market.rate.MRA1": _aux_market_message(payload_module=Rate, message_module=RateMessage, name="Rate", identifier=b"MRA1", sequence=5),
        "market.ticker_24h.MT24": _aux_market_message(payload_module=Ticker24h, message_module=Ticker24hMessage, name="Ticker24h", identifier=b"MT24", sequence=6),
        "market.mark_price.MMP1": _aux_market_message(payload_module=MarkPrice, message_module=MarkPriceMessage, name="MarkPrice", identifier=b"MMP1", sequence=7),
        "market.orderbook.MOB1": _orderbook_message(),
        "market.index_price.MIP1": _aux_market_message(payload_module=IndexPrice, message_module=IndexPriceMessage, name="IndexPrice", identifier=b"MIP1", sequence=9),
        "market.funding_rate.MFR1": _aux_market_message(payload_module=FundingRate, message_module=FundingRateMessage, name="FundingRate", identifier=b"MFR1", sequence=10),
        "market.open_interest.MOI1": _aux_market_message(payload_module=OpenInterest, message_module=OpenInterestMessage, name="OpenInterest", identifier=b"MOI1", sequence=11),
        "market.instrument_status.MIS1": _aux_market_message(payload_module=InstrumentStatus, message_module=InstrumentStatusMessage, name="InstrumentStatus", identifier=b"MIS1", sequence=12),
        "market.current.PMC1": _market_snapshot(),
        "market.orderbook.current.PMB1": _orderbook_snapshot(),
        "risk.event.RKE1": _risk_event(),
        "risk.current.PRK1": _risk_snapshot(),
    }
    print(
        json.dumps(
            {name: payload.hex() for name, payload in fixtures.items()},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
