"""Small Risk process-boundary projection used by replay orchestration."""

from __future__ import annotations

from pathlib import Path
from decimal import Decimal
import sys
from typing import Any, Mapping, cast

from kairospy.application.risk import RiskStatus, RiskViolation
from kairospy.domain_types import AccountId
from kairospy.infrastructure.contracts.base import MmapSnapshotReader
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos
from kairospy.infrastructure.transport.commands import UnixJsonCommandClient


class RiskContractClient:
    def __init__(self, socket_path: str | Path, *, timeout: float = 5.0) -> None:
        self._client = UnixJsonCommandClient(socket_path, timeout=timeout)

    def snapshot(self) -> Mapping[str, Any]:
        status, value = self._client.request("GET", "/v1/snapshot")
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"Risk snapshot failed: HTTP {status}"))
            )
        return value

    def advance_time(self, event_time_unix_nanos: int) -> Mapping[str, Any]:
        status, value = self._client.request(
            "POST",
            "/v1/time/advance",
            {"event_time_unix_nanos": event_time_unix_nanos},
        )
        if status >= 400:
            raise RuntimeError(
                str(value.get("error", f"Risk time advance failed: HTTP {status}"))
            )
        return value


class RiskMmapProjection:
    """Synchronous Risk status projection decoded from Risk-owned mmap."""

    def __init__(self, path: str | Path) -> None:
        sys.modules.setdefault("kairos", _generated_kairos)
        from kairospy.infrastructure.transport.generated.kairos.risk.v1.RiskSnapshot import (
            RiskSnapshot,
        )

        self._reader = MmapSnapshotReader(
            path, file_identifier=b"PRK1", root_type=RiskSnapshot
        )

    @property
    def path(self) -> Path:
        return self._reader.path

    def status(self, account_id: AccountId) -> RiskStatus:
        contract = self._reader.read()
        from kairospy.infrastructure.transport.generated.kairos.risk.v1.RiskSnapshot import (
            RiskSnapshot,
        )

        root = RiskSnapshot.GetRootAs(contract.payload, 0)
        payload = cast(Any, root.Payload())
        if payload is None:
            raise ValueError("Risk snapshot payload is missing")
        budgets = tuple(
            value
            for value in _table_items(payload, "Budgets")
            if (_text(value.AccountId()) or "") in {"", str(account_id)}
        )
        circuits = tuple(
            value
            for value in _table_items(payload, "Circuits")
            if (_text(value.AccountId()) or "") in {"", str(account_id)}
        )
        notional = tuple(
            value
            for value in budgets
            if "notional" in (_text(value.Metric()) or "").lower()
        )
        available = sum(
            (_decimal64(value.Available()) or Decimal("0") for value in notional),
            Decimal("0"),
        )
        reserved = sum(
            (_decimal64(value.Reserved()) or Decimal("0") for value in notional),
            Decimal("0"),
        )
        limit = sum(
            (_decimal64(value.Limit()) or Decimal("0") for value in notional),
            Decimal("0"),
        )
        violations = tuple(
            RiskViolation(
                code="budget_unavailable",
                message=f"Risk budget {_text(value.BudgetId()) or ''} is not active",
                limit=_decimal64(value.Limit()),
                actual=(_decimal64(value.Used()) or Decimal("0"))
                + (_decimal64(value.Reserved()) or Decimal("0")),
            )
            for value in budgets
            if (_text(value.Status()) or "active").lower()
            not in {"active", "available"}
        ) + tuple(
            RiskViolation(
                code="circuit_open",
                message=_text(value.Reason()) or "Risk circuit is open",
                limit=None,
                actual=None,
            )
            for value in circuits
            if (_text(value.State()) or "closed").lower() not in {"closed", "normal"}
        )
        return RiskStatus(
            account_id=account_id,
            trading_allowed=not violations,
            available_notional=available if notional else None,
            reserved_notional=reserved,
            utilization=None if limit == 0 else (limit - available) / limit,
            violations=violations,
            generation=contract.metadata.generation,
            event_sequence=contract.metadata.event_sequence,
        )


def _table_items(value: object, name: str) -> tuple[Any, ...]:
    table = cast(Any, value)
    result = tuple(
        getattr(table, name)(index)
        for index in range(int(getattr(table, f"{name}Length")()))
    )
    if any(item is None for item in result):
        raise ValueError(f"Risk snapshot contains an empty {name} entry")
    return cast(tuple[Any, ...], result)


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode("utf-8")


def _decimal64(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = cast(Any, value)
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))


__all__ = ["RiskContractClient", "RiskMmapProjection"]
