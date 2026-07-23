from __future__ import annotations

from decimal import Decimal
import unittest

from kairospy.environment import Environment
from kairospy.execution.events import TradeSide
from kairospy.execution.orders import ExecutionInstructions, OrderType, TimeInForce
from kairospy.execution.ports import OrderRequest, VenueOrderStatus
from kairospy.identity import AccountRef, AccountType, AssetId, InstitutionId, InstrumentId
from kairospy.integrations.connectors.hyperliquid import (
    HyperliquidSdkAccountGateway,
    HyperliquidSdkExecutionGateway,
)


ACCOUNT = AccountRef(InstitutionId("hyperliquid"), "main", AccountType.DERIVATIVES)


class HyperliquidExecutionGatewayTests(unittest.TestCase):
    def test_sdk_execution_gateway_places_limit_order_without_owning_signing(self) -> None:
        exchange = _FakeExchange()
        info = _FakeInfo()
        gateway = HyperliquidSdkExecutionGateway(
            exchange,
            info,
            account_address="0xabc",
            environment=Environment.LIVE,
            instrument_symbols={InstrumentId("crypto:hyperliquid:perpetual:BTC"): "BTC"},
        )

        ack = gateway.place_order(_order())

        self.assertEqual(ack.venue_order_id, "12345")
        self.assertEqual(exchange.orders[0]["coin"], "BTC")
        self.assertEqual(exchange.orders[0]["is_buy"], False)
        self.assertEqual(exchange.orders[0]["sz"], 0.01)
        self.assertEqual(exchange.orders[0]["limit_px"], 65000.0)
        self.assertEqual(exchange.orders[0]["order_type"], {"limit": {"tif": "Alo"}})
        self.assertEqual(exchange.orders[0]["reduce_only"], True)
        self.assertEqual(exchange.orders[0]["cloid"], "client-1")

    def test_sdk_execution_gateway_open_cancel_and_recover_order(self) -> None:
        exchange = _FakeExchange()
        info = _FakeInfo(open_orders=[{"oid": 12345, "coin": "BTC"}])
        gateway = HyperliquidSdkExecutionGateway(
            exchange,
            info,
            account_address="0xabc",
            environment=Environment.LIVE,
        )

        self.assertEqual(gateway.open_orders(ACCOUNT), ("12345",))
        gateway.cancel_order(ACCOUNT, "12345")
        recovered = gateway.recover_order(ACCOUNT, _order(), "12345")

        self.assertEqual(exchange.cancelled, [("BTC", 12345)])
        self.assertEqual(recovered.status, VenueOrderStatus.ACKNOWLEDGED)
        self.assertEqual(recovered.acknowledgement.venue_order_id, "12345")

    def test_sdk_account_gateway_maps_user_state(self) -> None:
        info = _FakeInfo(
            open_orders=[{"oid": 12345, "coin": "BTC"}],
            user_state={
                "marginSummary": {"accountValue": "1000", "totalMarginUsed": "100"},
                "withdrawable": "850",
                "assetPositions": [
                    {"position": {"coin": "BTC", "szi": "-0.01"}},
                ],
            },
        )
        gateway = HyperliquidSdkAccountGateway(
            info,
            account_address="0xabc",
            environment=Environment.LIVE,
            instrument_lookup={"BTC": InstrumentId("crypto:hyperliquid:perpetual:BTC")},
        )

        state = gateway.account_state(ACCOUNT)

        self.assertEqual(state.balances[0].asset, AssetId("USDC"))
        self.assertEqual(state.balances[0].total, Decimal("1000"))
        self.assertEqual(state.balances[0].available, Decimal("850"))
        self.assertEqual(state.positions, ((InstrumentId("crypto:hyperliquid:perpetual:BTC"), Decimal("-0.01")),))
        self.assertEqual(state.open_order_ids, ("12345",))


def _order() -> OrderRequest:
    return OrderRequest(
        internal_order_id="internal-1",
        client_order_id="client-1",
        strategy_id="strategy-1",
        intent_id="intent-1",
        correlation_id="corr-1",
        account=ACCOUNT,
        instrument_id=InstrumentId("crypto:hyperliquid:perpetual:BTC"),
        side=TradeSide.SELL,
        quantity=Decimal("0.01"),
        instructions=ExecutionInstructions(
            OrderType.LIMIT,
            TimeInForce.GTC,
            limit_price=Decimal("65000"),
            post_only=True,
            reduce_only=True,
        ),
    )


class _FakeExchange:
    def __init__(self) -> None:
        self.orders: list[dict[str, object]] = []
        self.cancelled: list[tuple[str, object]] = []

    def order(self, coin, is_buy, sz, limit_px, order_type, *, reduce_only=False, cloid=None):
        self.orders.append({
            "coin": coin,
            "is_buy": is_buy,
            "sz": sz,
            "limit_px": limit_px,
            "order_type": order_type,
            "reduce_only": reduce_only,
            "cloid": cloid,
        })
        return {"response": {"data": {"statuses": [{"resting": {"oid": 12345}}]}}}

    def cancel(self, coin, oid):
        self.cancelled.append((coin, oid))
        return {"status": "ok"}


class _FakeInfo:
    def __init__(self, *, open_orders=None, user_state=None) -> None:
        self._open_orders = list(open_orders or [])
        self._user_state = dict(user_state or {})

    def open_orders(self, address):
        return list(self._open_orders)

    def user_state(self, address):
        return dict(self._user_state)


if __name__ == "__main__":
    unittest.main()
