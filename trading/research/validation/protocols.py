from __future__ import annotations

from dataclasses import dataclass

from .models import DataCapabilities, ProductProtocol, ReturnDriver, ValidationLevel


@dataclass(frozen=True, slots=True)
class ProtocolDecision:
    passed: bool
    missing_capabilities: tuple[str, ...]


def validate_product_protocol(products: tuple[ProductProtocol, ...], data: DataCapabilities,
                              target_level: ValidationLevel) -> ProtocolDecision:
    missing: list[str] = []
    for product in products:
        if data.supported_products and product not in data.supported_products:
            missing.append(f"product:{product.value}")
        if product is ProductProtocol.SPOT:
            if not data.point_in_time_universe: missing.append("point_in_time_universe")
            if target_level >= ValidationLevel.L4_EXECUTABLE and not data.synchronous_quotes: missing.append("synchronous_quotes")
        elif product in (ProductProtocol.FUTURE, ProductProtocol.PERPETUAL):
            if not data.point_in_time_universe: missing.append("point_in_time_contract_universe")
            if target_level >= ValidationLevel.L4_EXECUTABLE and not data.lifecycle_events: missing.append("derivative_lifecycle_events")
            if product is ProductProtocol.PERPETUAL and target_level >= ValidationLevel.L4_EXECUTABLE and not data.funding: missing.append("funding")
            if product is ProductProtocol.FUTURE and target_level >= ValidationLevel.L4_EXECUTABLE and not data.settlement_price: missing.append("settlement_price")
        elif product is ProductProtocol.OPTION:
            if not data.point_in_time_universe: missing.append("point_in_time_option_universe")
            if target_level >= ValidationLevel.L4_EXECUTABLE:
                if not data.synchronous_quotes: missing.append("synchronous_multi_leg_quotes")
                if not data.settlement_price: missing.append("settlement_price")
                if not data.lifecycle_events: missing.append("option_lifecycle_events")
    return ProtocolDecision(not missing, tuple(dict.fromkeys(missing)))


def validate_return_driver_protocol(drivers: tuple[ReturnDriver, ...], data: DataCapabilities,
                                    target_level: ValidationLevel) -> ProtocolDecision:
    missing: list[str] = []
    for driver in drivers:
        if driver in (ReturnDriver.CARRY, ReturnDriver.BASIS) and target_level >= ValidationLevel.L4_EXECUTABLE:
            if ProductProtocol.PERPETUAL in data.supported_products and not data.funding: missing.append("funding")
        if driver in (ReturnDriver.VOLATILITY, ReturnDriver.SKEW, ReturnDriver.TAIL_RISK):
            if target_level >= ValidationLevel.L4_EXECUTABLE and not data.synchronous_quotes: missing.append("synchronous_quotes")
        if driver is ReturnDriver.LIQUIDITY:
            if not data.quote_size: missing.append("quote_size")
            if target_level >= ValidationLevel.L4_EXECUTABLE and data.order_book_depth < 2: missing.append("multi_level_order_book")
    return ProtocolDecision(not missing, tuple(dict.fromkeys(missing)))
