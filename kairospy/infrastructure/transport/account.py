from __future__ import annotations

import asyncio
import struct
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from kairospy.application.account.events import AccountChangeRecord, AccountEventRecord
from kairospy.infrastructure.transport.aeron_bridge import check_aeron_bridge
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


class AeronAccountEventSource:
    """Account-owned subprocess adapter over the native Aeron subscription."""

    join_from_latest = True

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = "aeron:udp?endpoint=localhost:40123",
        stream_id: int = 1401,
        binary: str,
    ) -> None:
        self.aeron_dir = None if aeron_dir is None else str(aeron_dir)
        self.channel = channel
        self.stream_id = stream_id
        self.binary = binary

    def _command(self) -> list[str]:
        command = [
            self.binary,
            "--aeron-channel",
            self.channel,
            "--stream-id",
            str(self.stream_id),
        ]
        if self.aeron_dir is not None:
            command.extend(("--aeron-dir", self.aeron_dir))
        return command

    def check_ready(self) -> None:
        check_aeron_bridge(self._command(), domain="Account")

    async def events(
        self, after_sequence: int = 0
    ) -> AsyncIterator[AccountEventRecord]:
        process = await asyncio.create_subprocess_exec(
            *self._command(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            while True:
                try:
                    size = struct.unpack(">I", await process.stdout.readexactly(4))[0]
                    if size == 0 or size > 4 * 1024 * 1024:
                        raise ValueError("invalid Account Aeron frame length")
                    payload = await process.stdout.readexactly(size)
                except asyncio.IncompleteReadError:
                    break
                record = decode_account_event(payload)
                if record.sequence > after_sequence:
                    yield record
            status = await process.wait()
            if status != 0:
                assert process.stderr is not None
                error = (await process.stderr.read()).decode(errors="replace").strip()
                raise RuntimeError(
                    error or f"Account Aeron bridge exited with {status}"
                )
            raise RuntimeError("Account Aeron bridge ended unexpectedly")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


def decode_account_event(payload: bytes) -> AccountEventRecord:
    from kairospy.infrastructure.transport.generated.kairos.account.v1.AccountEvent import (
        AccountEvent,
    )

    if payload[4:8] != b"ACE1":
        raise ValueError("invalid AccountEvent identifier")
    root = cast(Any, AccountEvent.GetRootAs(payload, 0))
    header = cast(Any, root.Header())
    if header is None:
        raise ValueError("Account event header is missing")
    changes = tuple(
        _decode_change(cast(Any, root.Changes(index)))
        for index in range(root.ChangesLength())
    )
    return AccountEventRecord(
        stream_id=_required_text(header.StreamId(), "stream_id"),
        sequence=int(header.Sequence()),
        producer=_required_text(header.ProducerId(), "producer_id"),
        account_id=_required_text(root.AccountId(), "account_id"),
        changes=changes,
        occurred_at_unix_nanos=int(root.OccurredAtUnixNanos()),
        launch_id=_optional_text(header.LaunchId()),
        instance_id=_optional_text(header.InstanceId()),
    )


def _decode_change(change: Any) -> AccountChangeRecord:
    if change is None:
        raise ValueError("Account event contains an empty change")
    kind = _required_text(change.Kind(), "change.kind")
    segment_key = _required_text(change.SegmentKey(), "change.segment_key")
    if kind == "balance_changed":
        value = cast(Any, change.Balance())
        if value is None:
            raise ValueError("Account balance change payload is missing")
        payload: object = {
            "asset": _required_text(value.AssetCode(), "balance.asset_code"),
            "total": _decimal(value.Total()),
            "available": _decimal(value.Available()),
            "reserved": _decimal(value.Locked()),
        }
    elif kind == "position_changed":
        value = cast(Any, change.Position())
        if value is None:
            raise ValueError("Account position change payload is missing")
        payload = {
            "instrument_id": _required_text(
                value.InstrumentId(), "position.instrument_id"
            ),
            "quantity": _decimal(value.Quantity()),
            "average_price": _decimal(value.AveragePrice()),
            "market_value": _market_value(value),
            "unrealized_pnl": _decimal(value.UnrealizedPnl()),
        }
    elif kind == "equity_changed":
        payload = {"equity": _decimal(change.Equity())}
    elif kind == "status_changed":
        payload = {
            "status": _required_text(change.Status(), "status"),
            "stale": bool(change.Stale()),
            "trading_enabled": bool(change.TradingEnabled()),
        }
    else:
        raise ValueError(f"unsupported Account event kind: {kind}")
    return AccountChangeRecord(kind, segment_key, payload)


def _decimal(value: object | None) -> str | None:
    if value is None:
        return None
    mantissa = int(getattr(value, "Mantissa")())
    scale = int(getattr(value, "Scale")())
    sign = "-" if mantissa < 0 else ""
    digits = str(abs(mantissa)).rjust(scale + 1, "0")
    return (
        f"{sign}{digits}"
        if scale == 0
        else f"{sign}{digits[:-scale]}.{digits[-scale:]}"
    )


def _optional_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None


def _market_value(value: Any) -> str | None:
    quantity = _decimal(value.Quantity())
    mark = _decimal(value.MarkPrice())
    if quantity is None or mark is None:
        return None
    from decimal import Decimal

    return str(Decimal(quantity) * Decimal(mark))


def _required_text(value: bytes | None, name: str) -> str:
    result = "" if value is None else value.decode()
    if not result.strip():
        raise ValueError(f"Account event {name} is required")
    return result


__all__ = ["AeronAccountEventSource", "decode_account_event"]
