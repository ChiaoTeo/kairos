from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from kairos.ports import (
    AccountState, ComboOrderRequest, Environment, OrderAck, OrderRequest, VenueBalance,
    VenueOrderRecovery, VenueOrderStatus,
)
from kairos.domain.capability import ExecutionCapabilities, MarginMode, OrderType, PositionMode
from kairos.domain.identity import AccountKey, AssetId, InstitutionId, InstrumentId, VenueId
from kairos.domain.product import ProductType
class _Clock(Protocol):
    def now(self) -> datetime: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimulatedExecutionAccountGateway:
    capabilities = ExecutionCapabilities(
        frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}),
        product_types=frozenset(ProductType),
        supports_combo_orders=True, supports_reduce_only=True, supports_post_only=True,
        margin_modes=frozenset({MarginMode.NONE, MarginMode.SECURITIES, MarginMode.CROSS, MarginMode.ISOLATED}),
        position_modes=frozenset({PositionMode.ONE_WAY, PositionMode.HEDGE}),
    )

    def __init__(self, venue_id: VenueId, account: AccountKey, balances=(), positions=(), environment=Environment.TESTNET,
                 clock: _Clock | None = None) -> None:
        self.venue_id, self.institution_id, self.account, self.environment = (
            venue_id, account.institution_id, account, environment,
        )
        self.balances = dict(balances); self.positions = dict(positions)
        self.orders = {}; self.client_ids = {}
        self.connected = True
        self.clock = clock or _SystemClock()

    def place_order(self, request: OrderRequest) -> OrderAck:
        if not self.connected: raise ConnectionError("simulated venue disconnected")
        if request.client_order_id in self.client_ids:
            ack = self.client_ids[request.client_order_id]
            if self.orders.get(ack.venue_order_id) != request:
                raise ValueError("client order id was already used for a different request")
            return ack
        order_id = str(uuid5(NAMESPACE_URL, f"simulated:{self.venue_id}:{request.client_order_id}"))
        ack = OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, order_id, self.clock.now(),
        )
        self.orders[order_id] = request; self.client_ids[request.client_order_id] = ack
        return ack

    def cancel_order(self, account, venue_order_id):
        if venue_order_id not in self.orders: raise LookupError(venue_order_id)
        del self.orders[venue_order_id]

    def open_orders(self, account):
        return tuple(self.orders)

    def recover_order(self, account, request, venue_order_id=None):
        ack = self.client_ids.get(request.client_order_id)
        if ack is None:
            return VenueOrderRecovery(VenueOrderStatus.UNKNOWN, "simulated client order id not found")
        if venue_order_id is not None and venue_order_id != ack.venue_order_id:
            return VenueOrderRecovery(VenueOrderStatus.UNKNOWN, "simulated venue order id mismatch")
        if ack.venue_order_id in self.orders:
            return VenueOrderRecovery(
                VenueOrderStatus.ACKNOWLEDGED,
                "simulated durable client-id lookup",
                acknowledgement=ack,
            )
        return VenueOrderRecovery(
            VenueOrderStatus.CANCELLED,
            "simulated order absent after recorded cancellation",
            acknowledgement=ack,
        )

    def account_state(self, account):
        if not self.connected: raise ConnectionError("simulated venue disconnected")
        return AccountState(account, tuple(VenueBalance(asset, amount, amount) for asset, amount in self.balances.items()), tuple(self.positions.items()), tuple(self.orders), self.clock.now())

    def place_combo_order(self, request: ComboOrderRequest) -> OrderAck:
        if not self.connected:
            raise ConnectionError("simulated venue disconnected")
        if request.client_order_id in self.client_ids:
            return self.client_ids[request.client_order_id]
        order_id = str(uuid5(NAMESPACE_URL, f"simulated-combo:{self.venue_id}:{request.client_order_id}"))
        ack = OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, order_id, self.clock.now(),
        )
        self.orders[order_id] = request
        self.client_ids[request.client_order_id] = ack
        return ack

    def disconnect(self): self.connected = False
    def reconnect(self): self.connected = True


