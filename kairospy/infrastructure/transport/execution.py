from __future__ import annotations

import asyncio
import struct
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from kairospy.application.execution.events import (
    ExecutionChangeRecord,
    ExecutionEventRecord,
)
from kairospy.infrastructure.transport.aeron_bridge import check_aeron_bridge
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


class AeronExecutionEventSource:
    join_from_latest = True

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = "aeron:udp?endpoint=localhost:40123",
        stream_id: int = 1501,
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
        check_aeron_bridge(self._command(), domain="Execution")

    async def events(
        self, after_sequence: int = 0
    ) -> AsyncIterator[ExecutionEventRecord]:
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
                        raise ValueError("invalid Execution Aeron frame length")
                    payload = await process.stdout.readexactly(size)
                except asyncio.IncompleteReadError:
                    break
                record = decode_execution_event(payload)
                if record.sequence > after_sequence:
                    yield record
            status = await process.wait()
            if status != 0:
                assert process.stderr is not None
                error = (await process.stderr.read()).decode(errors="replace").strip()
                raise RuntimeError(
                    error or f"Execution Aeron bridge exited with {status}"
                )
            raise RuntimeError("Execution Aeron bridge ended unexpectedly")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


def decode_execution_event(payload: bytes) -> ExecutionEventRecord:
    from kairospy.infrastructure.transport.generated.kairos.execution.v1.ExecutionEventMessage import (
        ExecutionEventMessage,
    )

    if payload[4:8] != b"EXE1":
        raise ValueError("invalid ExecutionEventMessage identifier")
    root = cast(Any, ExecutionEventMessage.GetRootAs(payload, 0))
    header = cast(Any, root.Header())
    if header is None:
        raise ValueError("Execution event header is missing")
    return ExecutionEventRecord(
        stream_id=_required_text(header.StreamId(), "stream_id"),
        sequence=int(header.Sequence()),
        producer=_required_text(header.ProducerId(), "producer_id"),
        instance_id=_text(header.InstanceId()),
        changes=tuple(
            _decode_change(cast(Any, root.Changes(index)))
            for index in range(root.ChangesLength())
        ),
        occurred_at_unix_nanos=int(root.OccurredAtUnixNanos()),
        launch_id=_text(header.LaunchId()),
    )


def _decode_change(change: Any) -> ExecutionChangeRecord:
    if change is None:
        raise ValueError("Execution event contains an empty change")
    kind = _required_text(change.Kind(), "change.kind")
    strategy_id = _required_text(change.StrategyId(), "strategy_id")
    account_id = _text(change.AccountId())
    if kind == "intent_update":
        value = cast(Any, change.Intent())
        if value is None:
            raise ValueError("Execution intent update payload is missing")
        payload: object = {
            "intent": {
                "intent_id": _required_text(value.IntentId(), "intent_id"),
                "strategy_id": strategy_id,
                "instrument_id": _required_text(value.InstrumentId(), "instrument_id"),
                "account_ids": [
                    _required_text(value.AccountIds(index), "account_id")
                    for index in range(value.AccountIdsLength())
                ],
                "target_quantity": _decimal(value.TargetQuantity()),
                "source_event_sequence": int(value.SourceEventSequence()) or None,
                "reason": _text(value.Reason()) or "",
            },
            "status": _required_text(value.Status(), "intent.status"),
            "order_ids": [
                _required_text(value.OrderIds(index), "order_id")
                for index in range(value.OrderIdsLength())
            ],
        }
    elif kind == "order_update":
        value = cast(Any, change.Order())
        if value is None:
            raise ValueError("Execution order update payload is missing")
        payload = {
            "order_id": _required_text(value.OrderId(), "order_id"),
            "strategy_id": strategy_id,
            "intent_id": _text(value.IntentId()),
            "account_id": _required_text(value.AccountId(), "account_id"),
            "instrument_id": _required_text(value.InstrumentId(), "instrument_id"),
            "side": _side(value.Side()),
            "quantity": _decimal(value.Quantity()),
            "filled_quantity": _decimal(value.FilledQuantity()),
            "limit_price": _decimal(value.LimitPrice()),
            "status": _required_text(value.Status(), "order.status"),
            "updated_at_unix_nanos": int(value.UpdatedAtUnixNanos()),
        }
    elif kind == "fill":
        value = cast(Any, change.Fill())
        if value is None:
            raise ValueError("Execution fill payload is missing")
        payload = {
            "fill_id": _required_text(value.FillId(), "fill_id"),
            "order_id": _required_text(value.OrderId(), "order_id"),
            "instrument_id": _required_text(value.InstrumentId(), "instrument_id"),
            "quantity": _decimal(value.Quantity()),
            "price": _decimal(value.Price()),
            "occurred_at_unix_nanos": int(value.OccurredAtUnixNanos()),
        }
    else:
        raise ValueError(f"unsupported Execution event kind: {kind}")
    return ExecutionChangeRecord(kind, strategy_id, account_id, payload)


def _side(value: int) -> str:
    if value == 1:
        return "buy"
    if value == 2:
        return "sell"
    raise ValueError("Execution event side is unspecified")


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


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


def _required_text(value: bytes | None, name: str) -> str:
    result = _text(value) or ""
    if not result.strip():
        raise ValueError(f"Execution event {name} is required")
    return result


__all__ = ["AeronExecutionEventSource", "decode_execution_event"]
