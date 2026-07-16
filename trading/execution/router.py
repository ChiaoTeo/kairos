from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading.adapters.base import ComboOrderRequest, ExecutionAdapter, OrderRequest
from trading.catalog.service import InstrumentCatalog


@dataclass(frozen=True, slots=True)
class ExecutionRiskLimits:
    maximum_order_quantity: Decimal = Decimal("1000000")
    maximum_order_notional: Decimal = Decimal("10000000")


class ExecutionRouter:
    def __init__(self, catalog: InstrumentCatalog, adapters: tuple[ExecutionAdapter, ...], limits: ExecutionRiskLimits = ExecutionRiskLimits()) -> None:
        self.catalog = catalog
        self.adapters = {adapter.venue_id: adapter for adapter in adapters}
        self.limits = limits

    def submit(self, request: OrderRequest, at):
        definition = self.catalog.get(request.instrument_id, at)
        adapter = self.adapters.get(request.account.venue_id)
        if adapter is None:
            raise LookupError(f"no execution adapter for account venue {request.account.venue_id}")
        adapter.capabilities.require_product(definition.product_type)
        adapter.capabilities.require_order_type(request.instructions.order_type)
        listing = definition.listing(adapter.venue_id, at)
        if request.quantity > self.limits.maximum_order_quantity:
            raise ValueError("order quantity exceeds execution risk limit")
        if request.quantity < listing.minimum_quantity or request.quantity % listing.quantity_step != 0:
            raise ValueError("order quantity violates venue lot rules")
        if request.instructions.limit_price is not None:
            if request.quantity * request.instructions.limit_price > self.limits.maximum_order_notional:
                raise ValueError("order notional exceeds execution risk limit")
            if request.instructions.limit_price % listing.price_tick != 0:
                raise ValueError("order price violates venue tick rule")
            if listing.minimum_notional is not None and request.quantity * request.instructions.limit_price < listing.minimum_notional:
                raise ValueError("order notional is below venue minimum")
        if request.instructions.reduce_only and not adapter.capabilities.supports_reduce_only:
            raise ValueError("venue does not support reduce-only")
        if request.instructions.post_only and not adapter.capabilities.supports_post_only:
            raise ValueError("venue does not support post-only")
        return adapter.place_order(request)

    def submit_combo(self, request: ComboOrderRequest, at):
        adapter = self.adapters.get(request.account.venue_id)
        if adapter is None:
            raise LookupError(f"no execution adapter for account venue {request.account.venue_id}")
        if not adapter.capabilities.supports_combo_orders or not hasattr(adapter, "place_combo_order"):
            raise ValueError("venue does not support native combo orders")
        adapter.capabilities.require_order_type(request.instructions.order_type)
        if request.quantity > self.limits.maximum_order_quantity:
            raise ValueError("combo quantity exceeds execution risk limit")
        for leg in request.legs:
            definition = self.catalog.get(leg.instrument_id, at)
            adapter.capabilities.require_product(definition.product_type)
            listing = definition.listing(adapter.venue_id, at)
            leg_quantity = request.quantity * leg.ratio
            if leg_quantity < listing.minimum_quantity or leg_quantity % listing.quantity_step != 0:
                raise ValueError("combo leg quantity violates venue lot rules")
        if request.instructions.reduce_only and not adapter.capabilities.supports_reduce_only:
            raise ValueError("venue does not support reduce-only combo orders")
        if request.instructions.post_only and not adapter.capabilities.supports_post_only:
            raise ValueError("venue does not support post-only combo orders")
        return adapter.place_combo_order(request)

    def cancel(self, account, venue_order_id: str) -> None:
        adapter = self.adapters.get(account.venue_id)
        if adapter is None:
            raise LookupError(f"no execution adapter for account venue {account.venue_id}")
        adapter.cancel_order(account, venue_order_id)
