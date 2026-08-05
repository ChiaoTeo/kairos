"""Serialization of account facts for journals and external output."""

from __future__ import annotations

from decimal import Decimal

from kairospy.domain.account import AccountBalance, AccountSnapshot, CollateralBalance, OpenOrderSnapshot, PositionSnapshot


def snapshot_payload(snapshot: AccountSnapshot) -> dict[str, object]:
    """Convert a domain snapshot into the stable account journal shape."""
    return {
        "event_time": snapshot.observed_at,
        "balances": tuple(balance_payload(balance) for balance in snapshot.balances),
        "collaterals": tuple(collateral_payload(collateral) for collateral in snapshot.collaterals),
        "positions": tuple(position_payload(position) for position in snapshot.positions),
        "open_orders": tuple(open_order_payload(order) for order in snapshot.open_orders),
        "source": snapshot.source.value,
    }


def balance_payload(balance: AccountBalance) -> dict[str, object]:
    return {
        "currency": str(balance.currency),
        "free": str(balance.free),
        "locked": str(balance.locked),
        "total": str(balance.total),
        "source": balance.source.value,
    }


def collateral_payload(collateral: CollateralBalance) -> dict[str, object]:
    return {
        "asset": str(collateral.asset),
        "wallet": str(collateral.wallet),
        "available": str(collateral.available),
        "valuation": None if collateral.valuation is None else str(collateral.valuation),
        "haircut": str(collateral.haircut),
        "source": collateral.source.value,
    }


def position_payload(position: PositionSnapshot) -> dict[str, object]:
    return {
        "instrument_id": str(position.instrument_id),
        "quantity": str(position.quantity),
        "average_price": decimal_text(position.average_price),
        "mark_price": decimal_text(position.mark_price),
        "unrealized_pnl": decimal_text(position.unrealized_pnl),
        "source": position.source.value,
    }


def open_order_payload(order: OpenOrderSnapshot) -> dict[str, object]:
    return {
        "order_id": order.order_id,
        "instrument_id": str(order.instrument_id),
        "side": order.side,
        "quantity": str(order.quantity),
        "reserved_currency": str(order.reserved_currency),
        "reserved_amount": str(order.reserved_amount),
        "source": order.source.value,
    }


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "balance_payload",
    "collateral_payload",
    "decimal_text",
    "open_order_payload",
    "position_payload",
    "snapshot_payload",
]
