from __future__ import annotations

from kairos.domain.identity import InstitutionId

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
from uuid import uuid4

from kairos.accounting.ledger import LedgerService
from kairos.ports import Environment, OrderRequest, ReferenceDataRequest, VenueOrderStatus
from kairos.connectors.binance.account_gateway import (
    BinanceAccountGateway,
    BinanceOptionsAccountGateway,
)
from kairos.connectors.binance.execution_gateway import (
    BINANCE_FUTURES_EXECUTION_CAPABILITIES,
    BINANCE_OPTIONS_EXECUTION_CAPABILITIES,
    BINANCE_SPOT_EXECUTION_CAPABILITIES,
    BinanceExecutionGateway,
    BinanceOptionsExecutionGateway,
)
from kairos.connectors.binance.funding_settlement import BinanceFundingSettlementClient
from kairos.connectors.binance.market_data_client import (
    BINANCE_FUTURES_MARKET_DATA_CAPABILITIES,
    BINANCE_OPTIONS_MARKET_DATA_CAPABILITIES,
    BINANCE_SPOT_MARKET_DATA_CAPABILITIES,
    BinanceMarketDataClient,
)
from kairos.connectors.binance.market_stream import BinanceStreamSession, parse_market_stream_event, websocket_url
from kairos.connectors.binance.option_market_snapshot import parse_option_market_snapshot
from kairos.connectors.binance.order_recovery import BinanceRecoveryService
from kairos.connectors.binance.reference_data import (
    BinanceFuturesReferenceDataClient,
    BinanceOptionsReferenceDataClient,
    BinanceSpotReferenceDataClient,
)
from kairos.connectors.binance.request_signing import BinanceSigner, synchronize_clock
from kairos.connectors.binance.user_data_stream import (
    BinanceUserDataStreamService,
    BinanceUserStreamProcessor,
    parse_user_stream_event,
)
from kairos.domain.capability import MarginMode, OrderType
from kairos.domain.execution import TradeExecution, TradeSide
from kairos.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairos.domain.ledger import Ledger, LedgerBook
from kairos.domain.order import ExecutionInstructions, TimeInForce
from kairos.domain.market_data import DerivativeMarketState, OrderBookDelta, Quote, Trade
from kairos.domain.product import ProductType
from kairos.reference import ReferenceCatalog
from kairos.reference.access import contract_spec
from kairos.execution.ingestion import DurableAccountingIngestionService, ExecutionIngestionService
from kairos.execution.strategy_planner import plan_strategy_intent
from kairos.products.crypto_option.settlement import (
    CryptoOptionSettlementService, DurableCryptoOptionSettlementService,
)
from kairos.orchestration.runtime_store import SQLiteRuntimeStore
from kairos.products.perpetual.funding import FundingEngine
from kairos.risk.margin import CryptoCrossMarginPolicy
from kairos.strategies.cash_and_carry import CashAndCarryConfig, CashAndCarryStrategy


NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _merge_reference(target: ReferenceCatalog, source: ReferenceCatalog) -> None:
    for name in (
        "assets", "entities", "venues", "benchmarks", "products", "series", "instruments", "listings",
        "routes", "networks", "network_assets", "rails", "locations", "settlements",
    ):
        destination = getattr(target, name)
        for value in getattr(source, name).values():
            try:
                destination.add(value)
            except ValueError as error:
                if "overlapping reference definition" not in str(error):
                    raise
    for value in source.mappings():
        target.add_mapping(value)
    for value in source.all_references():
        target.add_reference(value)


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, params=None, headers=None):
        self.calls.append((method, path, params or {}, headers or {}))
        response = self.responses[path]
        return response() if callable(response) else response


class CountingLimiter:
    def __init__(self): self.count = 0
    def acquire(self): self.count += 1


class FakeSocket:
    def __init__(self, values): self.values, self.closed = iter(values), False
    def receive(self):
        value = next(self.values)
        if isinstance(value, Exception): raise value
        return value
    def close(self): self.closed = True


class FakeConnector:
    def __init__(self, sessions): self.sessions, self.calls = iter(sessions), 0
    def connect(self, url): self.calls += 1; return FakeSocket(next(self.sessions))


SPOT_INFO = {"symbols": [{
    "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
    ],
}]}
FUTURES_INFO = {"symbols": [{
    "symbol": "BTCUSDT", "contractType": "PERPETUAL", "baseAsset": "BTC", "quoteAsset": "USDT", "marginAsset": "USDT", "pair": "BTCUSDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
    ],
}]}
OPTIONS_INFO = {"optionSymbols": [{
    "symbol": "BTC-250628-60000-C", "underlying": "BTCUSDT", "quoteAsset": "USDT", "settleAsset": "USDT",
    "expiryDate": 1845763200000, "strikePrice": "60000", "side": "CALL", "unit": "1",
    "priceScale": "0.1", "quantityScale": "0.01", "minQty": "0.01",
}]}
DELIVERY_INFO = {"symbols": [{
    "symbol": "BTCUSDT_260925", "contractType": "CURRENT_QUARTER", "baseAsset": "BTC", "quoteAsset": "USDT",
    "marginAsset": "USDT", "pair": "BTCUSDT", "deliveryDate": 1790294400000,
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
    ],
}]}


class CryptoProductTests(unittest.TestCase):
    def setUp(self):
        responses = {
            "/api/v3/exchangeInfo": SPOT_INFO,
            "/fapi/v1/exchangeInfo": FUTURES_INFO,
            "/eapi/v1/exchangeInfo": OPTIONS_INFO,
            "/api/v3/ticker/bookTicker": [{"symbol": "BTCUSDT", "bidPrice": "50000", "askPrice": "50001", "bidQty": "2", "askQty": "3"}],
            "/api/v3/order": {"orderId": 123},
            "/fapi/v1/order": {"orderId": 456},
            "/api/v3/openOrders": [{"orderId": 123}],
            "/api/v3/account": {"balances": [{"asset": "USDT", "free": "1000", "locked": "5"}]},
            "/api/v3/time": {"serverTime": 1100},
            "/api/v3/myTrades": [{"symbol": "BTCUSDT", "id": 7, "orderId": 123, "isBuyer": True, "qty": "0.01", "price": "50000", "commission": "0.1", "commissionAsset": "BNB", "time": 1735689600000}],
            "/fapi/v1/leverage": {},
            "/fapi/v1/marginType": {},
            "/fapi/v1/positionSide/dual": {},
            "/eapi/v1/order": {"orderId": 789},
            "/eapi/v1/openOrders": [{"orderId": 789}],
            "/eapi/v1/account": {"asset": [{"asset": "USDT", "marginBalance": "1200", "available": "1000", "locked": "200"}]},
            "/eapi/v1/position": [{"symbol": "BTC-250628-60000-C", "quantity": "2"}],
            "/api/v3/userDataStream": {"listenKey": "listen-spot"},
            "/fapi/v1/income": [{
                "symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "-5", "asset": "USDT",
                "time": 1735689601000, "tranId": 991,
            }],
        }
        self.transport = FakeTransport(responses)

    def test_binance_capabilities_are_scoped_by_port_and_product_family(self):
        self.assertEqual(BINANCE_SPOT_MARKET_DATA_CAPABILITIES.product_types, frozenset({ProductType.CRYPTO_SPOT}))
        self.assertEqual(BINANCE_FUTURES_MARKET_DATA_CAPABILITIES.product_types, frozenset({ProductType.PERPETUAL, ProductType.FUTURE}))
        self.assertEqual(BINANCE_OPTIONS_MARKET_DATA_CAPABILITIES.product_types, frozenset({ProductType.CRYPTO_OPTION}))
        self.assertEqual(BINANCE_SPOT_EXECUTION_CAPABILITIES.product_types, frozenset({ProductType.CRYPTO_SPOT}))
        self.assertTrue(BINANCE_FUTURES_EXECUTION_CAPABILITIES.supports_reduce_only)
        self.assertEqual(BINANCE_OPTIONS_EXECUTION_CAPABILITIES.order_types, frozenset({OrderType.LIMIT}))
        self.assertFalse(hasattr(BINANCE_SPOT_MARKET_DATA_CAPABILITIES, "order_types"))
        self.assertFalse(hasattr(BINANCE_SPOT_EXECUTION_CAPABILITIES, "market_data"))
        futures_market = BinanceMarketDataClient(self.transport, ProductType.PERPETUAL)
        self.assertIs(futures_market.capabilities, BINANCE_FUTURES_MARKET_DATA_CAPABILITIES)
        self.assertEqual(futures_market.path, "/fapi/v1/ticker/bookTicker")
        with self.assertRaisesRegex(ValueError, "does not support"):
            BinanceMarketDataClient(self.transport, ProductType.EQUITY)

    def test_binance_reference_market_execution_and_account_gateways(self):
        spot = BinanceSpotReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",))).instruments.values()[0]
        perpetual = BinanceFuturesReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.PERPETUAL, ("BTCUSDT",))).instruments.values()[0]
        option = BinanceOptionsReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.CRYPTO_OPTION, ("BTC-250628-60000-C",))).instruments.values()[0]
        self.assertEqual(spot.instrument_type.value, "crypto_spot")
        self.assertEqual(perpetual.instrument_type.value, "perpetual")
        self.assertEqual(option.instrument_type.value, "crypto_option")
        quote = BinanceMarketDataClient(self.transport).snapshot((spot,))[0]
        self.assertEqual(quote.bid, Decimal("50000"))
        signer = BinanceSigner("key", "secret")
        account = AccountKey(InstitutionId("binance"), "test", AccountType.CRYPTO_SPOT)
        request = OrderRequest(
            "order-1", "client-1", "strategy-1", "intent-1", "correlation-1",
            account, spot.instrument_id, TradeSide.BUY, Decimal("0.01"),
            ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("49000"), post_only=True),
        )
        execution_gateway = BinanceExecutionGateway(self.transport, signer, Environment.TESTNET, instrument_symbols={spot.instrument_id: "BTCUSDT"})
        ack = execution_gateway.place_order(request)
        self.assertEqual(ack.venue_order_id, "123")
        call = self.transport.calls[-1]
        self.assertIn("signature", call[2])
        self.assertEqual(call[3]["X-MBX-APIKEY"], "key")
        self.assertEqual(call[2]["type"], "LIMIT_MAKER")
        self.assertEqual(execution_gateway.open_orders(account), ("123",))
        state = BinanceAccountGateway(self.transport, signer, Environment.TESTNET).account_state(account)
        self.assertEqual(state.balances[0].total, Decimal("1005"))
        self.assertEqual(state.balances[0].locked, Decimal("5"))
        self.assertIn("testnet.binance.vision", websocket_url(Environment.TESTNET, "btcusdt@bookTicker"))
        self.assertEqual(signer.synchronize(1100, 1000), 100)
        update = parse_user_stream_event({
            "e": "executionReport", "x": "TRADE", "s": "BTCUSDT", "t": 7, "i": 123,
            "S": "BUY", "l": "0.01", "L": "50000", "n": "0.1", "N": "BNB", "E": 1735689600000,
        }, account, {"BTCUSDT": spot.instrument_id})
        self.assertEqual(update.execution_id, "7")
        self.assertEqual(update.commission_asset, AssetId("BNB"))

    def test_binance_rest_order_and_fill_recovery_returns_durable_execution_facts(self):
        spot = BinanceSpotReferenceDataClient(self.transport).sync(
            ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",)),
        ).instruments.values()[0]
        self.transport.responses["/api/v3/order"] = {
            "symbol": "BTCUSDT",
            "orderId": 123,
            "clientOrderId": "client-recovery",
            "status": "FILLED",
            "transactTime": 1735689599000,
        }
        account = AccountKey(InstitutionId("binance"), "test", AccountType.CRYPTO_SPOT)
        request = OrderRequest(
            "order-recovery", "client-recovery", "strategy-1", "intent-1", "correlation-1",
            account, spot.instrument_id, TradeSide.BUY, Decimal("0.01"),
            ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("50000")),
        )
        execution_gateway = BinanceExecutionGateway(
            self.transport,
            BinanceSigner("key", "secret"),
            Environment.TESTNET,
            instrument_symbols={spot.instrument_id: "BTCUSDT"},
        )

        recovered = execution_gateway.recover_order(account, request)

        self.assertEqual(recovered.status, VenueOrderStatus.FILLED)
        self.assertEqual(recovered.acknowledgement.venue_order_id, "123")  # type: ignore[union-attr]
        self.assertEqual(len(recovered.executions), 1)
        self.assertTrue(recovered.executions[0].fully_filled)
        self.assertEqual(recovered.executions[0].execution.quantity, Decimal("0.01"))
        self.assertEqual(recovered.executions[0].execution.fee_asset, AssetId("BNB"))
        self.assertEqual(recovered.executions[0].cursor_value, "1735689600000:7")
        order_call, trades_call = self.transport.calls[-2:]
        self.assertEqual(order_call[:2], ("GET", "/api/v3/order"))
        self.assertEqual(order_call[2]["origClientOrderId"], "client-recovery")
        self.assertEqual(trades_call[:2], ("GET", "/api/v3/myTrades"))
        self.assertEqual(trades_call[2]["orderId"], "123")

    def test_rate_limit_clock_sync_private_dedup_recovery_and_futures_configuration(self):
        limiter = CountingLimiter()
        spot = BinanceSpotReferenceDataClient(self.transport, limiter).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",))).instruments.values()[0]
        self.assertEqual(limiter.count, 1)
        signer = BinanceSigner("key", "secret")
        self.assertEqual(synchronize_clock(self.transport, signer, limiter, local_time_ms=1000), 100)
        self.assertEqual(limiter.count, 2)
        account = AccountKey(InstitutionId("binance"), "test", AccountType.CRYPTO_SPOT)
        processor = BinanceUserStreamProcessor(account, {"BTCUSDT": spot.instrument_id})
        row = {"e": "executionReport", "x": "TRADE", "s": "BTCUSDT", "t": 7, "i": 123, "S": "BUY", "l": "0.01", "L": "50000", "n": "0.1", "N": "BNB", "E": 1735689600000}
        self.assertIsNotNone(processor.process(row))
        self.assertIsNone(processor.process(row))
        execution = BinanceExecutionGateway(self.transport, signer, Environment.TESTNET, limiter=limiter)
        account_gateway = BinanceAccountGateway(self.transport, signer, Environment.TESTNET, limiter=limiter)
        recovered = BinanceRecoveryService(self.transport, signer, execution, account_gateway, BinanceUserStreamProcessor(account, {"BTCUSDT": spot.instrument_id}), limiter).recover(account, since_ms=0)
        self.assertEqual(recovered.open_order_ids, ("123",))
        self.assertEqual(len(recovered.fills), 1)
        futures = BinanceExecutionGateway(self.transport, signer, Environment.TESTNET, futures=True, limiter=limiter, instrument_symbols={InstrumentId("crypto:binance:perpetual:BTCUSDT"): "BTCUSDT"})
        futures.set_leverage("BTCUSDT", 5)
        futures.set_margin_mode("BTCUSDT", isolated=True)
        futures.set_position_mode(hedge_mode=True)
        self.assertEqual([call[1] for call in self.transport.calls[-3:]], ["/fapi/v1/leverage", "/fapi/v1/marginType", "/fapi/v1/positionSide/dual"])

    def test_option_greeks_parser_and_futures_stream_normalization(self):
        option = BinanceOptionsReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.CRYPTO_OPTION, ("BTC-250628-60000-C",))).instruments.values()[0]
        snapshot = parse_option_market_snapshot({
            "symbol": "BTC-250628-60000-C", "bidPrice": "100", "askPrice": "101", "markPrice": "100.5",
            "indexPrice": "60000", "volatility": "0.55", "delta": "0.4", "gamma": "0.01",
            "theta": "-2", "vega": "10", "eventTime": 1735689600000,
        }, {"BTC-250628-60000-C": option.instrument_id})
        self.assertEqual(snapshot.implied_volatility, Decimal("0.55"))
        account = AccountKey(InstitutionId("binance"), "futures", AccountType.DERIVATIVES)
        processor = BinanceUserStreamProcessor(account, {"BTCUSDT": InstrumentId("crypto:binance:perpetual:BTCUSDT")})
        update = processor.process({
            "e": "ORDER_TRADE_UPDATE", "E": 1735689600000,
            "o": {"x": "TRADE", "s": "BTCUSDT", "t": 9, "i": 456, "S": "SELL", "l": "1", "L": "51000", "n": "2", "N": "USDT"},
        })
        self.assertEqual(update.side, "sell")
        self.assertIsNone(processor.process({
            "e": "ORDER_TRADE_UPDATE", "E": 1735689600000,
            "o": {"x": "TRADE", "s": "BTCUSDT", "t": 9, "i": 456, "S": "SELL", "l": "1", "L": "51000", "n": "2", "N": "USDT"},
        }))

    def test_public_streams_normalize_to_shared_market_types_and_manage_listen_key(self):
        lookup = {"BTCUSDT": InstrumentId("crypto:binance:perpetual:BTCUSDT")}
        quote = parse_market_stream_event({
            "e": "bookTicker", "E": 1735689600000, "s": "BTCUSDT",
            "b": "50000", "B": "2", "a": "50001", "A": "3",
        }, lookup)
        trade = parse_market_stream_event({
            "stream": "btcusdt@aggTrade", "data": {
                "e": "aggTrade", "E": 1735689600000, "s": "BTCUSDT", "p": "50000.5", "q": "0.1",
            },
        }, lookup)
        depth = parse_market_stream_event({
            "e": "depthUpdate", "E": 1735689600000, "s": "BTCUSDT", "U": 10, "u": 12,
            "b": [["50000", "1"]], "a": [["50001", "2"]],
        }, lookup)
        derivative = parse_market_stream_event({
            "e": "markPriceUpdate", "E": 1735689600000, "s": "BTCUSDT",
            "p": "50010", "i": "50000", "r": "0.0001", "T": 1735718400000,
        }, lookup)
        self.assertIsInstance(quote, Quote)
        self.assertIsInstance(trade, Trade)
        self.assertIsInstance(depth, OrderBookDelta)
        self.assertEqual((depth.first_sequence, depth.last_sequence), (10, 12))
        self.assertIsInstance(derivative, DerivativeMarketState)
        self.assertEqual(derivative.funding_rate, Decimal("0.0001"))

        stream = BinanceUserDataStreamService(self.transport, "key")
        listen_key = stream.create()
        stream.keepalive(listen_key)
        stream.close(listen_key)
        self.assertEqual(listen_key, "listen-spot")
        self.assertEqual([call[0] for call in self.transport.calls[-3:]], ["POST", "PUT", "DELETE"])
        self.assertTrue(all(call[3]["X-MBX-APIKEY"] == "key" for call in self.transport.calls[-3:]))

    def test_crypto_option_execution_and_account_are_explicitly_live_only(self):
        option = BinanceOptionsReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.CRYPTO_OPTION, ("BTC-250628-60000-C",))).instruments.values()[0]
        signer = BinanceSigner("key", "secret")
        account = AccountKey(InstitutionId("binance"), "options", AccountType.DERIVATIVES)
        with self.assertRaisesRegex(ValueError, "live-only"):
            BinanceOptionsExecutionGateway(self.transport, signer, Environment.TESTNET)
        execution = BinanceOptionsExecutionGateway(self.transport, signer, Environment.LIVE, instrument_symbols={option.instrument_id: "BTC-250628-60000-C"})
        request = OrderRequest(
            "option-order", "option-client", "option-hedge", "option-intent", "option-correlation",
            account, option.instrument_id, TradeSide.BUY, Decimal("1"),
            ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("100"), post_only=True),
        )
        ack = execution.place_order(request)
        self.assertEqual(ack.venue_order_id, "789")
        self.assertEqual(self.transport.calls[-1][1], "/eapi/v1/order")
        state = BinanceOptionsAccountGateway(
            self.transport, signer, Environment.LIVE,
            instrument_lookup={"BTC-250628-60000-C": option.instrument_id},
        ).account_state(account)
        self.assertEqual(state.balances[0].available, Decimal("1000"))
        self.assertEqual(state.positions, ((option.instrument_id, Decimal("2")),))
        self.assertEqual(state.open_order_ids, ("789",))

    def test_futures_reduce_only_order_and_cancel_use_the_futures_contract(self):
        perpetual = BinanceFuturesReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.PERPETUAL, ("BTCUSDT",))).instruments.values()[0]
        signer = BinanceSigner("key", "secret")
        account = AccountKey(InstitutionId("binance"), "futures", AccountType.DERIVATIVES)
        request = OrderRequest(
            "order-f", "client-f", "carry", "intent-f", "correlation-f", account,
            perpetual.instrument_id, TradeSide.BUY, Decimal("0.01"),
            ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("49000"), reduce_only=True),
        )
        execution_gateway = BinanceExecutionGateway(self.transport, signer, Environment.TESTNET, futures=True, instrument_symbols={perpetual.instrument_id: "BTCUSDT"})
        ack = execution_gateway.place_order(request)
        self.assertEqual(ack.venue_order_id, "456")
        self.assertEqual(self.transport.calls[-1][2]["reduceOnly"], "true")
        execution_gateway.cancel_order(account, ack.venue_order_id)
        self.assertEqual(self.transport.calls[-1][0:2], ("DELETE", "/fapi/v1/order"))

    def test_normalized_private_fills_use_the_same_idempotent_ledger_reducer(self):
        catalog = BinanceSpotReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",)))
        spot = catalog.instruments.values()[0]
        ledger, account = Ledger(), AccountKey(InstitutionId("binance"), "test", AccountType.CRYPTO_SPOT)
        service = LedgerService(ledger, catalog)
        start = spot.effective_from + timedelta(seconds=1)
        service.deposit(account, AssetId("USDT"), Decimal("1000"), start, "capital")
        update = parse_user_stream_event({
            "e": "executionReport", "x": "TRADE", "s": "BTCUSDT", "t": 7, "i": 123,
            "S": "BUY", "l": "0.01", "L": "50000", "n": "1", "N": "BNB", "E": int((start + timedelta(seconds=1)).timestamp() * 1000),
        }, account, {"BTCUSDT": spot.instrument_id})
        ingestion = ExecutionIngestionService(service)
        self.assertIsNotNone(ingestion.ingest_binance(update))
        self.assertIsNone(ingestion.ingest_binance(update))
        self.assertIsNone(ExecutionIngestionService(service).ingest_binance(update))
        self.assertEqual(ledger.book_balance(account, LedgerBook.POSITION, AssetId(f"POSITION:{spot.instrument_id.value}")), Decimal("0.01"))
        self.assertEqual(ledger.book_balance(account, LedgerBook.CASH, AssetId("BNB")), Decimal("-1"))

    def test_websocket_disconnect_reconnect_invokes_backfill_hook(self):
        connector = FakeConnector(((ConnectionError("lost"),), ({"e": "bookTicker", "s": "BTCUSDT"},)))
        messages, reconnects = [], []
        count = BinanceStreamSession(connector, "wss://fixture", maximum_reconnects=1).consume(messages.append, message_limit=1, on_reconnect=reconnects.append)
        self.assertEqual(count, 1)
        self.assertEqual(connector.calls, 2)
        self.assertEqual(reconnects, [1])
        self.assertEqual(messages[0]["s"], "BTCUSDT")

    def test_linear_perpetual_realized_pnl_funding_margin_and_carry(self):
        spot_catalog = BinanceSpotReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.CRYPTO_SPOT, ("BTCUSDT",)))
        perpetual_catalog = BinanceFuturesReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.PERPETUAL, ("BTCUSDT",)))
        spot, perpetual = spot_catalog.instruments.values()[0], perpetual_catalog.instruments.values()[0]
        catalog = ReferenceCatalog()
        _merge_reference(catalog, spot_catalog); _merge_reference(catalog, perpetual_catalog)
        ledger = Ledger(); service = LedgerService(ledger, catalog)
        account = AccountKey(InstitutionId("binance"), "futures", AccountType.DERIVATIVES)
        start = max(spot.effective_from, perpetual.effective_from) + timedelta(seconds=1)
        service.deposit(account, AssetId("USDT"), Decimal("10000"), start, "margin")
        service.trade(TradeExecution(uuid4(), start + timedelta(seconds=1), account, perpetual.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("50000"), AssetId("USDT"), Decimal("2"), "open"))
        service.trade(TradeExecution(uuid4(), start + timedelta(seconds=2), account, perpetual.instrument_id, TradeSide.SELL, Decimal("1"), Decimal("51000"), AssetId("USDT"), Decimal("2"), "close"))
        self.assertEqual(ledger.book_balance(account, LedgerBook.CASH, AssetId("USDT")), Decimal("10996"))
        service.trade(TradeExecution(uuid4(), start + timedelta(seconds=3), account, perpetual.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("50000"), AssetId("USDT"), Decimal("0"), "open2"))
        payment = FundingEngine(service).apply(account, perpetual.instrument_id, Decimal("1"), Decimal("50000"), Decimal("0.0001"), start + timedelta(seconds=4))
        self.assertEqual(payment.amount, Decimal("-5.0000"))
        negative = FundingEngine(service).apply(account, perpetual.instrument_id, Decimal("-1"), Decimal("50000"), Decimal("-0.0001"), start + timedelta(seconds=5))
        self.assertEqual(negative.amount, Decimal("-5.0000"))
        margin = CryptoCrossMarginPolicy().calculate(equity=Decimal("10000"), quantity=Decimal("1"), price=Decimal("50000"), leverage=Decimal("10"), direction=1)
        self.assertEqual(margin.initial_margin, Decimal("5000"))
        carry = CashAndCarryStrategy(spot.instrument_id, perpetual.instrument_id, CashAndCarryConfig(minimum_annualized_basis=Decimal("0.01"))).intent(Decimal("50000"), Decimal("51000"), Decimal("0"), Decimal("0"))
        self.assertEqual(carry.spot_quantity, Decimal("0.1"))
        self.assertEqual(carry.derivative_quantity, Decimal("-0.1"))
        plan = plan_strategy_intent(
            carry, accounts={spot.instrument_id: account, perpetual.instrument_id: account}, current_positions={},
            instructions={
                spot.instrument_id: ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("50000")),
                perpetual.instrument_id: ExecutionInstructions(OrderType.LIMIT, TimeInForce.GTC, Decimal("51000"), reduce_only=False),
            },
        )
        self.assertEqual(tuple(item.side for item in plan.orders), (TradeSide.BUY, TradeSide.SELL))

    def test_crypto_option_cash_settlement(self):
        catalog = BinanceOptionsReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.CRYPTO_OPTION, ("BTC-250628-60000-C",)))
        option = catalog.instruments.values()[0]
        ledger = Ledger(); service = LedgerService(ledger, catalog)
        account = AccountKey(InstitutionId("binance"), "options", AccountType.DERIVATIVES)
        start = option.effective_from + timedelta(seconds=1)
        service.deposit(account, AssetId("USDT"), Decimal("10000"), start, "options")
        service.trade(TradeExecution(uuid4(), start + timedelta(seconds=1), account, option.instrument_id, TradeSide.BUY, Decimal("1"), Decimal("100"), AssetId("USDT"), Decimal("1"), "option-open"))
        expiry = contract_spec(option).expiry
        CryptoOptionSettlementService(service).settle(account, option.instrument_id, Decimal("61000"), expiry)
        self.assertEqual(ledger.book_balance(account, LedgerBook.CASH, AssetId("USDT")), Decimal("10899"))
        position_asset = AssetId(f"POSITION:{option.instrument_id.value}")
        self.assertEqual(ledger.book_balance(account, LedgerBook.POSITION, position_asset), Decimal("0"))

    def test_crypto_option_settlement_is_durable_across_restart(self):
        catalog = BinanceOptionsReferenceDataClient(self.transport).sync(
            ReferenceDataRequest(ProductType.CRYPTO_OPTION, ("BTC-250628-60000-C",)),
        )
        option = catalog.instruments.values()[0]
        ledger = Ledger(); service = LedgerService(ledger, catalog)
        account = AccountKey(InstitutionId("binance"), "options", AccountType.DERIVATIVES)
        start = option.effective_from + timedelta(seconds=1)
        service.deposit(account, AssetId("USDT"), Decimal("10000"), start, "options-durable")
        service.trade(TradeExecution(
            uuid4(), start + timedelta(seconds=1), account, option.instrument_id, TradeSide.BUY,
            Decimal("1"), Decimal("100"), AssetId("USDT"), Decimal("1"), "option-open-durable",
        ))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            store.import_ledger(ledger)
            expiry = contract_spec(option).expiry
            durable = DurableCryptoOptionSettlementService(
                CryptoOptionSettlementService(service),
                DurableAccountingIngestionService(service, store),
            )
            self.assertIsNotNone(durable.settle(account, option.instrument_id, Decimal("61000"), expiry))
            rebuilt = store.load_ledger()
            position_asset = AssetId(f"POSITION:{option.instrument_id.value}")
            self.assertEqual(rebuilt.book_balance(account, LedgerBook.POSITION, position_asset), Decimal("0"))
            self.assertEqual(rebuilt.book_balance(account, LedgerBook.CASH, AssetId("USDT")), Decimal("10899"))

            restarted_ledger = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3").load_ledger()
            restarted_service = LedgerService(restarted_ledger, catalog)
            duplicate = DurableCryptoOptionSettlementService(
                CryptoOptionSettlementService(restarted_service),
                DurableAccountingIngestionService(restarted_service, SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")),
            ).settle(account, option.instrument_id, Decimal("61000"), expiry)
            self.assertIsNone(duplicate)
            self.assertEqual(len(restarted_ledger.transactions), len(rebuilt.transactions))

    def test_venue_funding_history_replays_idempotently_into_ledger(self):
        catalog = BinanceFuturesReferenceDataClient(self.transport).sync(ReferenceDataRequest(ProductType.PERPETUAL, ("BTCUSDT",)))
        perpetual = catalog.instruments.values()[0]
        ledger = Ledger()
        account = AccountKey(InstitutionId("binance"), "futures", AccountType.DERIVATIVES)
        service = LedgerService(ledger, catalog)
        service.deposit(account, AssetId("USDT"), Decimal("1000"), NOW, "margin")
        funding_client = BinanceFundingSettlementClient(
            self.transport, BinanceSigner("key", "secret"), Environment.TESTNET,
            instrument_lookup={"BTCUSDT": perpetual.instrument_id},
        )
        payments = funding_client.funding_history(account, NOW, NOW + timedelta(days=1))
        self.assertEqual((len(payments), payments[0].amount), (1, Decimal("-5")))
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRuntimeStore(Path(directory) / "runtime.sqlite3")
            store.import_ledger(ledger)
            ingestion = DurableAccountingIngestionService(service, store)
            self.assertEqual(ingestion.ingest_funding_history(payments, source="binance"), 1)
            self.assertEqual(ingestion.ingest_funding_history(payments, source="binance"), 0)
            rebuilt = store.load_ledger()
            self.assertEqual(rebuilt.book_balance(account, LedgerBook.CASH, AssetId("USDT")), Decimal("995"))
            self.assertEqual(
                store.cursor(f"binance:funding:{account.value}"),
                f"{payments[0].timestamp.isoformat()}:{payments[0].payment_id}",
            )

    def test_binance_delivery_future_has_explicit_expiry_and_future_spec(self):
        transport = FakeTransport({"/fapi/v1/exchangeInfo": DELIVERY_INFO})
        future = BinanceFuturesReferenceDataClient(transport).sync(ReferenceDataRequest(ProductType.FUTURE, ("BTCUSDT_260925",))).instruments.values()[0]
        self.assertEqual(future.instrument_type, ProductType.FUTURE)
        self.assertEqual(contract_spec(future).expiry, datetime.fromtimestamp(1790294400, timezone.utc))


if __name__ == "__main__": unittest.main()
