from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from kairospy.environment import Environment
from kairospy.execution.events import TradeSide
from kairospy.execution.orders import ExecutionCapabilities, OrderType
from kairospy.execution.ports import OrderAck, OrderRequest, VenueOrderRecovery, VenueOrderStatus
from kairospy.identity import AccountRef, AssetId, InstitutionId, InstrumentId, VenueId
from kairospy.portfolio.account_ports import AccountState, VenueBalance
from kairospy.reference.contracts import ProductType


HYPERLIQUID_PERP_EXECUTION_CAPABILITIES = ExecutionCapabilities(
    frozenset({OrderType.MARKET, OrderType.LIMIT}),
    product_types=frozenset({ProductType.PERPETUAL}),
    supports_reduce_only=True,
    supports_post_only=True,
)


class HyperliquidSdkExecutionGateway:
    service_id = "hyperliquid_perp_execution"
    service_kind = "execution"
    institution_id = InstitutionId("hyperliquid")
    venue_id = VenueId("hyperliquid")
    capabilities = HYPERLIQUID_PERP_EXECUTION_CAPABILITIES

    def __init__(
        self,
        exchange: object,
        info: object,
        *,
        account_address: str,
        environment: Environment,
        instrument_symbols: dict[InstrumentId, str] | None = None,
    ) -> None:
        if environment is not Environment.LIVE:
            raise ValueError("Hyperliquid execution gateway is live-only")
        if not account_address.strip():
            raise ValueError("Hyperliquid execution gateway requires an account address")
        self.exchange = exchange
        self.info = info
        self.account_address = account_address
        self.environment = environment
        self.instrument_symbols = dict(instrument_symbols or {})
        self._client_to_venue_order: dict[str, str] = {}

    def place_order(self, request: OrderRequest) -> OrderAck:
        self.capabilities.require_order_type(request.instructions.order_type)
        coin = self._coin(request.instrument_id)
        is_buy = request.side is TradeSide.BUY
        order_type = _hyperliquid_order_type(request)
        price = _hyperliquid_price(request)
        response = self.exchange.order(
            coin,
            is_buy,
            float(request.quantity),
            price,
            order_type,
            reduce_only=request.instructions.reduce_only,
            cloid=request.client_order_id,
        )
        venue_order_id = _accepted_order_id(response)
        self._client_to_venue_order[request.client_order_id] = venue_order_id
        return OrderAck(
            request.internal_order_id,
            request.client_order_id,
            request.strategy_id,
            request.intent_id,
            request.correlation_id,
            venue_order_id,
            datetime.now(timezone.utc),
        )

    def cancel_order(self, account: AccountRef, venue_order_id: str) -> None:
        coin = self._coin_from_open_order(venue_order_id)
        self.exchange.cancel(coin, int(venue_order_id) if str(venue_order_id).isdigit() else venue_order_id)

    def open_orders(self, account: AccountRef) -> tuple[str, ...]:
        rows = _list_of_dicts(self.info.open_orders(self.account_address))
        return tuple(str(item.get("oid") or item.get("orderId")) for item in rows if item.get("oid") or item.get("orderId"))

    def recover_order(self, account: AccountRef, request: OrderRequest, venue_order_id: str | None = None) -> VenueOrderRecovery:
        target = str(venue_order_id or self._client_to_venue_order.get(request.client_order_id) or "")
        if not target:
            return VenueOrderRecovery(VenueOrderStatus.UNKNOWN, "Hyperliquid order id is unavailable")
        open_order_ids = set(self.open_orders(account))
        if target in open_order_ids:
            return VenueOrderRecovery(
                VenueOrderStatus.ACKNOWLEDGED,
                f"Hyperliquid open order query contains oid={target}",
                acknowledgement=OrderAck(
                    request.internal_order_id,
                    request.client_order_id,
                    request.strategy_id,
                    request.intent_id,
                    request.correlation_id,
                    target,
                    datetime.now(timezone.utc),
                ),
            )
        return VenueOrderRecovery(
            VenueOrderStatus.UNKNOWN,
            f"Hyperliquid open order query does not contain oid={target}; fill history verification requires live key run",
        )

    def _coin(self, instrument_id: InstrumentId) -> str:
        return self.instrument_symbols.get(instrument_id) or _coin_from_instrument(instrument_id)

    def _coin_from_open_order(self, venue_order_id: str) -> str:
        for item in _list_of_dicts(self.info.open_orders(self.account_address)):
            if str(item.get("oid") or item.get("orderId")) == str(venue_order_id):
                return str(item.get("coin") or item.get("symbol") or "").upper()
        raise LookupError(f"Hyperliquid open order coin unavailable for oid={venue_order_id}")


class HyperliquidSdkAccountGateway:
    institution_id = InstitutionId("hyperliquid")
    venue_id = VenueId("hyperliquid")

    def __init__(
        self,
        info: object,
        *,
        account_address: str,
        environment: Environment,
        instrument_lookup: dict[str, InstrumentId] | None = None,
    ) -> None:
        if environment is not Environment.LIVE:
            raise ValueError("Hyperliquid account gateway is live-only")
        if not account_address.strip():
            raise ValueError("Hyperliquid account gateway requires an account address")
        self.info = info
        self.account_address = account_address
        self.environment = environment
        self.instrument_lookup = dict(instrument_lookup or {})

    def account_state(self, account: AccountRef) -> AccountState:
        state = dict(self.info.user_state(self.account_address))
        margin = dict(state.get("marginSummary") or state.get("crossMarginSummary") or {})
        account_value = Decimal(str(margin.get("accountValue") or "0"))
        withdrawable = Decimal(str(state.get("withdrawable") or margin.get("totalRawUsd") or account_value))
        balances = (
            VenueBalance(
                AssetId("USDC"),
                account_value,
                withdrawable,
                max(account_value - withdrawable, Decimal("0")),
                collateral=Decimal(str(margin.get("totalMarginUsed") or "0")),
            ),
        )
        positions = tuple(
            (self.instrument_lookup.get(str(item.get("coin") or "").upper(), InstrumentId(f"crypto:hyperliquid:perpetual:{str(item.get('coin') or '').upper()}")), Decimal(str(item.get("szi") or "0")))
            for item in _asset_positions(state)
            if Decimal(str(item.get("szi") or "0")) != 0
        )
        open_orders = tuple(str(item.get("oid") or item.get("orderId")) for item in _list_of_dicts(self.info.open_orders(self.account_address)) if item.get("oid") or item.get("orderId"))
        return AccountState(account, balances, positions, open_orders, datetime.now(timezone.utc))


def _hyperliquid_order_type(request: OrderRequest) -> dict[str, object]:
    if request.instructions.order_type is OrderType.MARKET:
        return {"market": {"tif": "Ioc"}}
    if request.instructions.order_type is OrderType.LIMIT:
        tif = "Alo" if request.instructions.post_only else _time_in_force(request)
        return {"limit": {"tif": tif}}
    raise ValueError("Hyperliquid SDK gateway supports market and limit orders only")


def _hyperliquid_price(request: OrderRequest) -> float:
    if request.instructions.limit_price is not None:
        return float(request.instructions.limit_price)
    return 0.0


def _time_in_force(request: OrderRequest) -> str:
    value = request.instructions.time_in_force.value
    return {"gtc": "Gtc", "ioc": "Ioc", "fok": "Ioc", "day": "Gtc"}[value]


def _accepted_order_id(response: object) -> str:
    payload = dict(response) if isinstance(response, dict) else {"response": response}
    statuses = (
        payload.get("response", {})
        .get("data", {})
        .get("statuses", ())
        if isinstance(payload.get("response"), dict)
        else ()
    )
    if statuses:
        first = statuses[0]
        if isinstance(first, dict):
            if "error" in first:
                raise RuntimeError(f"Hyperliquid order rejected: {first['error']}")
            resting = first.get("resting") if isinstance(first.get("resting"), dict) else {}
            filled = first.get("filled") if isinstance(first.get("filled"), dict) else {}
            order_id = resting.get("oid") or filled.get("oid")
            if order_id is not None:
                return str(order_id)
    raise RuntimeError(f"Hyperliquid order response did not include an accepted order id: {payload!r}")


def _asset_positions(state: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    positions = []
    for item in state.get("assetPositions", ()):
        if isinstance(item, dict):
            position = item.get("position")
            if isinstance(position, dict):
                positions.append(position)
    return tuple(positions)


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _coin_from_instrument(instrument_id: InstrumentId) -> str:
    text = str(instrument_id)
    return text.rsplit(":", 1)[-1].replace("-PERP", "").upper()


__all__ = [
    "HYPERLIQUID_PERP_EXECUTION_CAPABILITIES",
    "HyperliquidSdkAccountGateway",
    "HyperliquidSdkExecutionGateway",
]
