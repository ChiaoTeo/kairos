from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading.adapters.base import ComboOrderRequest, ExecutionAdapter, OrderRequest
from trading.reference.catalog import ReferenceCatalog


@dataclass(frozen=True, slots=True)
class ExecutionRiskLimits:
    maximum_order_quantity: Decimal = Decimal("1000000")
    maximum_order_notional: Decimal = Decimal("10000000")


class ExecutionRouter:
    def __init__(self, catalog: ReferenceCatalog, adapters: tuple[ExecutionAdapter, ...], limits: ExecutionRiskLimits = ExecutionRiskLimits()) -> None:
        self.catalog = catalog
        self.adapters = {adapter.venue_id: adapter for adapter in adapters}
        self.institution_adapters = {adapter.institution_id: adapter for adapter in adapters}
        self.limits = limits

    def submit(self, request: OrderRequest, at):
        definition, listing, adapter = self._resolve(request.account, request.instrument_id, at)
        if adapter is None:
            raise LookupError(f"no execution adapter for account institution {request.account.institution_id}")
        adapter.capabilities.require_product(_product_type(definition))
        adapter.capabilities.require_order_type(request.instructions.order_type)
        minimum_quantity, quantity_step, price_tick, minimum_notional = _rules(listing)
        if request.quantity > self.limits.maximum_order_quantity:
            raise ValueError("order quantity exceeds execution risk limit")
        if request.quantity < minimum_quantity or request.quantity % quantity_step != 0:
            raise ValueError("order quantity violates venue lot rules")
        if request.instructions.limit_price is not None:
            if request.quantity * request.instructions.limit_price > self.limits.maximum_order_notional:
                raise ValueError("order notional exceeds execution risk limit")
            if request.instructions.limit_price % price_tick != 0:
                raise ValueError("order price violates venue tick rule")
            if minimum_notional is not None and request.quantity * request.instructions.limit_price < minimum_notional:
                raise ValueError("order notional is below venue minimum")
        if request.instructions.reduce_only and not adapter.capabilities.supports_reduce_only:
            raise ValueError("venue does not support reduce-only")
        if request.instructions.post_only and not adapter.capabilities.supports_post_only:
            raise ValueError("venue does not support post-only")
        return adapter.place_order(request)

    def submit_combo(self, request: ComboOrderRequest, at):
        resolved = [self._resolve(request.account, leg.instrument_id, at) for leg in request.legs]
        adapters = {id(item[2]): item[2] for item in resolved}
        if len(adapters) != 1:
            raise ValueError("combo legs do not resolve to one execution route")
        adapter = next(iter(adapters.values()))
        if adapter is None:
            raise LookupError(f"no execution adapter for account institution {request.account.institution_id}")
        if not adapter.capabilities.supports_combo_orders or not hasattr(adapter, "place_combo_order"):
            raise ValueError("venue does not support native combo orders")
        adapter.capabilities.require_order_type(request.instructions.order_type)
        if request.quantity > self.limits.maximum_order_quantity:
            raise ValueError("combo quantity exceeds execution risk limit")
        for leg, (definition, listing, _) in zip(request.legs, resolved):
            adapter.capabilities.require_product(_product_type(definition))
            minimum_quantity, quantity_step, _, _ = _rules(listing)
            leg_quantity = request.quantity * leg.ratio
            if leg_quantity < minimum_quantity or leg_quantity % quantity_step != 0:
                raise ValueError("combo leg quantity violates venue lot rules")
        if request.instructions.reduce_only and not adapter.capabilities.supports_reduce_only:
            raise ValueError("venue does not support reduce-only combo orders")
        if request.instructions.post_only and not adapter.capabilities.supports_post_only:
            raise ValueError("venue does not support post-only combo orders")
        return adapter.place_combo_order(request)

    def cancel(self, account, venue_order_id: str) -> None:
        adapter = self.institution_adapters.get(account.institution_id)
        if adapter is None:
            raise LookupError(f"no execution adapter for account institution {account.institution_id}")
        adapter.cancel_order(account, venue_order_id)

    def _resolve(self, account, instrument_id, at):
        definition = self.catalog.instruments.get(instrument_id, at)
        route = self.catalog.resolve_execution_route(account, instrument_id, at)
        listing = self.catalog.listings.get(route.listing_id, at)
        adapter = self.institution_adapters.get(account.institution_id)
        if adapter is None:
            raise LookupError(f"no execution adapter for route broker {route.broker_id}")
        return definition, listing, adapter


def _product_type(definition):
    return definition.instrument_type


def _rules(listing):
    rules = listing.trading_rules
    return rules.minimum_quantity, rules.quantity_increment, rules.price_increment, rules.minimum_notional
