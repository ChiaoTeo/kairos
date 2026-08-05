from datetime import datetime, timezone
from decimal import Decimal

from kairospy.domain.account import AccountModel, AccountSegment, AccountRuntimeContext, Environment, ProductFamily
from kairospy.domain.reference import MarketRef
from kairospy.domain.order import OrderSide, OrderType
from kairospy.infrastructure.integrations.application.account import ConnectionAccountMarketProfileRequest, ConnectionAccountReadRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.application.execution import ConnectionOrderSubmissionRequest
from kairospy.infrastructure.integrations.domain import AccessScope, BrokerId, BrokerRef, IntegrationCapability, IntegrationRoute, ProductFamily as IntegrationProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.gateways.ccxt.private import CcxtAccountConnection, CcxtExecutionConnection


class FakeExchange:
    def __init__(self) -> None:
        self.orders = []

    def fetch_balance(self):
        return {
            "free": {"USDT": "100"},
            "used": {"USDT": "5"},
            "total": {"USDT": "105"},
        }

    def fetch_positions(self, symbols):
        assert symbols == ["BTC/USDT:USDT"]
        return [{"symbol": "BTC/USDT:USDT", "contracts": "0.01", "entryPrice": "60000", "side": "long"}]

    def fetch_open_orders(self, symbol):
        return [{"id": "open-1", "symbol": symbol, "side": "buy", "remaining": "0.01"}]

    def create_order(self, symbol, type_, side, amount, price, params):
        self.orders.append((symbol, type_, side, amount, price, params))
        return {"id": "order-1", "status": "open"}

    def fetch_trading_fee(self, symbol):
        assert symbol == "BTC/USDT:USDT"
        return {"maker": "0.0005", "taker": "0.0006", "currency": "USDT"}

    def fetch_my_trading_fee(self, symbol):
        assert symbol == "BTC/USDT:USDT"
        return {"maker": "0.0004", "taker": "0.0005", "tier": "vip1", "info": {"discountAsset": "BNB", "discountRate": "0.1", "discountEnabled": True}}


class FakeOkxExchange(FakeExchange):
    def privateGetAccountConfig(self, params):
        assert params == {}
        return {"code": "0", "data": [{"acctLv": "3", "posMode": "long_short_mode"}]}

    def privateGetAccountTradeFee(self, params):
        assert params == {"instType": "SWAP", "instFamily": "BTC-USDT"}
        return {"code": "0", "data": [{"maker": "-0.0001", "taker": "0.0005", "level": "Lv2", "feeGroup": [{"maker": "-0.00008", "taker": "0.0006"}]}]}


class FakeHyperliquidExchange(FakeExchange):
    walletAddress = "0x1111111111111111111111111111111111111111"

    def publicPostInfo(self, params):
        assert params == {"type": "userFees", "user": self.walletAddress}
        return {
            "feeSchedule": {"cross": "0.00045", "add": "0.00015", "spotCross": "0.0007", "spotAdd": "0.0004"},
            "userCrossRate": "0.000315", "userAddRate": "0.000105",
        }


def _spec(capability, broker=BrokerId.BINANCE):
    return IntegrationConnectionSpec(
        connection_id="test.ccxt.futures",
        route=IntegrationRoute(broker=BrokerRef(broker)),
        product=IntegrationProductFamily.USD_M_FUTURES,
        access=AccessScope.PRIVATE,
        transport=TransportKind.REST,
        capability=capability,
        mode="paper",
    )


def test_ccxt_account_connection_accepts_injected_exchange() -> None:
    account = AccountRuntimeContext(AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.PAPER)
    connection = CcxtAccountConnection(_spec(IntegrationCapability.ACCOUNT_READ), exchange=FakeExchange())
    snapshot = connection.read_account(ConnectionAccountReadRequest(account, datetime.now(timezone.utc), symbol="BTC/USDT:USDT")).snapshot
    assert snapshot.balances[0].total == Decimal("105")
    assert snapshot.positions[0].quantity == Decimal("0.01")
    assert snapshot.open_orders[0].order_id == "open-1"


def test_ccxt_futures_snapshot_exposes_each_collateral_asset() -> None:
    class MultiCollateralExchange(FakeExchange):
        def fetch_balance(self):
            return {
                "free": {"USDT": "100", "USDC": "50"},
                "used": {"USDT": "5", "USDC": "2"},
                "total": {"USDT": "105", "USDC": "52"},
            }

    account = AccountRuntimeContext(AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.PAPER)
    connection = CcxtAccountConnection(_spec(IntegrationCapability.ACCOUNT_READ), exchange=MultiCollateralExchange())
    snapshot = connection.read_account(ConnectionAccountReadRequest(account, datetime.now(timezone.utc), symbol="BTC/USDT:USDT")).snapshot

    assert [(item.asset, item.wallet, item.available) for item in snapshot.collaterals] == [
        ("USDC", Decimal("52"), Decimal("50")),
        ("USDT", Decimal("105"), Decimal("100")),
    ]


def test_ccxt_execution_connection_accepts_injected_exchange() -> None:
    fake = FakeExchange()
    connection = CcxtExecutionConnection(_spec(IntegrationCapability.ORDER_ENTRY), exchange=fake)
    result = connection.submit(ConnectionOrderSubmissionRequest(
        AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES),
        "BTC/USDT:USDT",
        OrderSide.BUY,
        OrderType.LIMIT,
        Decimal("0.01"),
        Decimal("60000"),
    ))
    assert result.order_venue_id == "order-1"
    assert fake.orders[0][1:] == ("limit", "buy", 0.01, 60000.0, {})


def test_ccxt_account_market_profile_combines_account_product_and_discount_rules() -> None:
    account = AccountRuntimeContext(AccountSegment("binance", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.PAPER)
    market = MarketRef.ephemeral(venue="binance", market="usd_m_futures", source_symbol="BTC/USDT:USDT")
    profile = CcxtAccountConnection(_spec(IntegrationCapability.ACCOUNT_MARKET_PROFILE_READ), exchange=FakeExchange()).read_market_profile(
        ConnectionAccountMarketProfileRequest(account, market, datetime.now(timezone.utc))
    ).profile
    assert profile.fee is not None
    assert profile.fee.maker == Decimal("0.00036")
    assert profile.fee.taker == Decimal("0.00045")
    assert profile.fee.account_rule is not None
    assert profile.fee.market_rule is not None
    assert profile.fee.payment is not None
    assert profile.fee.payment.discount is not None
    assert profile.fee.payment.discount.asset == "BNB"


def test_okx_account_config_and_instrument_fee_are_read_from_private_api() -> None:
    account = AccountRuntimeContext(AccountSegment("okx", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.PAPER)
    market = MarketRef.ephemeral(venue="okx", market="usd_m_futures", source_symbol="BTC/USDT:USDT")
    profile = CcxtAccountConnection(_spec(IntegrationCapability.ACCOUNT_MARKET_PROFILE_READ, BrokerId.OKX), exchange=FakeOkxExchange()).read_market_profile(
        ConnectionAccountMarketProfileRequest(account, market, datetime.now(timezone.utc))
    ).profile
    assert profile.account_type is AccountModel.UNIFIED
    assert profile.position_mode == "long_short_mode"
    assert profile.fee is not None
    assert profile.fee.maker == Decimal("-0.0001")
    assert profile.fee.tier == "Lv2"


def test_hyperliquid_user_fees_use_wallet_address_and_return_effective_rates() -> None:
    account = AccountRuntimeContext(AccountSegment("hyperliquid", "main", AccountModel.CONTRACT, ProductFamily.USD_M_FUTURES), Environment.PAPER)
    market = MarketRef.ephemeral(venue="hyperliquid", market="usd_m_futures", source_symbol="BTC/USDC:USDC")
    profile = CcxtAccountConnection(_spec(IntegrationCapability.ACCOUNT_MARKET_PROFILE_READ, BrokerId("hyperliquid")), exchange=FakeHyperliquidExchange()).read_market_profile(
        ConnectionAccountMarketProfileRequest(account, market, datetime.now(timezone.utc))
    ).profile
    assert profile.account_type == "unified"
    assert profile.fee is not None
    assert profile.fee.maker == Decimal("0.000105")
    assert profile.fee.taker == Decimal("0.000315")
    assert profile.fee.currency == "USDC"
