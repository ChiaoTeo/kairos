"""Account contract facade."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from kairospy.infrastructure.transport.commands import UnixJsonRpcClient
from kairospy.primitives.account import AccountId

from .view_contract import AccountIndexedViewReader


class AccountContractClient:
    """Low-frequency Account health and simulation-control facade."""

    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonRpcClient(socket_path, timeout=timeout)

    def health(self) -> Mapping[str, Any]:
        return self._call("account_health")

    def refresh(self, request: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return self._call("account_refresh", [dict(request or {})])

    def reconcile(self, request: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        return self._call("account_reconcile", [dict(request or {})])

    def mark_to_market(self, update: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._call("account_mark_to_market", [update])

    def advance_time(self, event_time_unix_nanos: int) -> Mapping[str, Any]:
        return self._call("account_advance_time", [{"event_time_unix_nanos": event_time_unix_nanos}])

    def _call(self, method: str, params: list[object] | None = None) -> Mapping[str, Any]:
        return self._client.call(method, params)


class AccountCurrentViewReader:
    """Synchronous Account application reader over its native owner contract."""

    def __init__(self, view_root: str | Path, *, account_id: AccountId, workspace_id: str, launch_id: str | None, instance_id: str | None) -> None:
        self._account_id = account_id
        self._reader = AccountIndexedViewReader(view_root, account_id=str(account_id), workspace_id=workspace_id, launch_id=launch_id, instance_id=instance_id)

    @property
    def path(self) -> Path:
        return self._reader.path

    def snapshot(self, account_id: AccountId) -> dict[str, object]:
        self._check_account(account_id)
        snapshot = self._reader.snapshot()
        return {
            "account_id": snapshot.account_id or str(account_id),
            "segments": [_segment(value) for value in snapshot.segments],
            "collateral": [_collateral(value) for value in snapshot.collateral],
            "generation": snapshot.generation,
            "event_sequence": snapshot.event_sequence,
        }

    def _check_account(self, account_id: AccountId) -> None:
        if account_id != self._account_id:
            raise ValueError("Account reader identity does not match requested account")


class AccountObservedOrdersViewReader:
    """Synchronous Account observed-orders query over the same native snapshot."""

    def __init__(self, view_root: str | Path, *, account_id: AccountId, workspace_id: str, launch_id: str | None, instance_id: str | None) -> None:
        self._account_id = account_id
        self._reader = AccountIndexedViewReader(view_root, account_id=str(account_id), workspace_id=workspace_id, launch_id=launch_id, instance_id=instance_id)

    @property
    def path(self) -> Path:
        return self._reader.path

    def open_orders(self, account_id: AccountId) -> dict[str, object]:
        if account_id != self._account_id:
            raise ValueError("Account reader identity does not match requested account")
        snapshot = self._reader.snapshot()
        return {
            "account_id": snapshot.account_id or str(account_id),
            "generation": snapshot.event_sequence,
            "event_sequence": snapshot.event_sequence,
            "open_orders": [_observed_order(value) for value in snapshot.observed_orders],
        }


def _segment(value: Any) -> dict[str, object]:
    return {
        "account_id": value.account_id,
        "segment_key": value.segment_key,
        "broker": value.broker,
        "environment": value.environment,
        "account_model": value.account_model,
        "equity": _decimal_text(value.equity),
        "balances": [{"asset": item.asset, "total": _required_decimal_text(item.total), "available": _required_decimal_text(item.available), "reserved": _required_decimal_text(item.reserved)} for item in value.balances],
        "positions": [{"instrument_id": item.instrument_id, "quantity": _required_decimal_text(item.quantity), "position_side": item.position_side, "average_price": _decimal_text(item.average_price), "market_value": _decimal_text(item.market_value), "unrealized_pnl": _decimal_text(item.unrealized_pnl)} for item in value.positions],
        "earn_holdings": [{"holding_key": item.holding_key, "participant_position_id": item.participant_position_id, "product_id": item.product_id, "asset": item.asset, "principal": _required_decimal_text(item.principal), "redeemable": _decimal_text(item.redeemable), "state": item.state, "participant_state": item.participant_state, "liquidity": item.liquidity, "notice_seconds": item.notice_seconds, "matures_at_unix_nanos": item.matures_at_unix_nanos, "observed_at_unix_nanos": item.observed_at_unix_nanos} for item in value.earn_holdings],
        "earn_watermark_unix_nanos": value.earn_watermark_unix_nanos,
        "freshness": value.freshness,
        "generation": value.generation,
        "sync_mode": value.sync_mode,
        "sync_lifecycle": value.sync_lifecycle,
        "completeness": value.completeness,
        "snapshot_watermark": value.snapshot_watermark,
        "event_watermark": value.event_watermark,
        "channel_epoch": value.channel_epoch,
        "last_event_at_unix_nanos": value.last_event_at_unix_nanos,
        "last_success_at_unix_nanos": value.last_success_at_unix_nanos,
        "last_error": value.last_error,
        "recovery_buffer_depth": value.recovery_buffer_depth,
    }


def _observed_order(value: Any) -> dict[str, object]:
    return {"observation_id": value.observation_id, "source_id": value.source_id, "execution_order_id": value.execution_order_id, "remote_order_id": value.remote_order_id, "instrument_id": value.instrument_id, "market_id": value.market_id, "side": value.side, "quantity": _decimal(value.quantity), "filled_quantity": _decimal(value.filled_quantity), "status": value.status, "observed_at_unix_nanos": value.observed_at_unix_nanos, "segment_key": value.segment_key}


def _collateral(value: Any) -> dict[str, object]:
    return {
        "account_id": value.account_id,
        "segment_key": value.segment_key,
        "asset": value.asset,
        "total": _required_decimal_text(value.total),
        "available": _decimal_text(value.available),
        "locked": _decimal_text(value.locked),
        "borrowed": _decimal_text(value.borrowed),
        "interest": _decimal_text(value.interest),
    }


def _decimal(value: Any | None) -> Decimal | None:
    return None if value is None else Decimal(value.mantissa).scaleb(-value.scale)


def _decimal_text(value: Any | None) -> str | None:
    result = _decimal(value)
    return None if result is None else format(result, "f")


def _required_decimal_text(value: Any) -> str:
    result = _decimal_text(value)
    if result is None:
        raise ValueError("Account native contract omitted a required decimal")
    return result


__all__ = ["AccountContractClient", "AccountCurrentViewReader", "AccountObservedOrdersViewReader"]
