from __future__ import annotations

import asyncio
import struct
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from kairospy.application.risk.events import RiskEventRecord
from kairospy.infrastructure.transport.aeron_bridge import check_aeron_bridge
from kairospy.infrastructure.transport.generated import kairos as _generated_kairos

sys.modules.setdefault("kairos", _generated_kairos)


class AeronRiskEventSource:
    """Risk-owned subprocess adapter over the native Aeron subscription."""

    join_from_latest = True

    def __init__(
        self,
        *,
        aeron_dir: str | Path | None = None,
        channel: str = "aeron:udp?endpoint=localhost:40123",
        stream_id: int = 1601,
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
        check_aeron_bridge(self._command(), domain="Risk")

    async def events(self, after_sequence: int = 0) -> AsyncIterator[RiskEventRecord]:
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
                        raise ValueError("invalid Risk Aeron frame length")
                    payload = await process.stdout.readexactly(size)
                except asyncio.IncompleteReadError:
                    break
                record = decode_risk_event(payload)
                if record.sequence > after_sequence:
                    yield record
            status = await process.wait()
            if status != 0:
                assert process.stderr is not None
                error = (await process.stderr.read()).decode(errors="replace").strip()
                raise RuntimeError(error or f"Risk Aeron bridge exited with {status}")
            raise RuntimeError("Risk Aeron bridge ended unexpectedly")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()


def decode_risk_event(payload: bytes) -> RiskEventRecord:
    from kairospy.infrastructure.transport.generated.kairos.risk.v1.RiskEventMessage import (
        RiskEventMessage,
    )

    if not RiskEventMessage.RiskEventMessageBufferHasIdentifier(payload, 0):
        raise ValueError("Risk event has an invalid RKE1 identifier")
    root = cast(Any, RiskEventMessage.GetRootAs(payload, 0))
    header = root.Header()
    if header is None:
        raise ValueError("Risk event header is missing")
    sequence = int(header.Sequence())
    if sequence <= 0:
        raise ValueError("Risk event sequence must be positive")
    occurred_at = int(root.OccurredAtUnixNanos())
    kind = _required_text(root.Kind(), "kind")
    account_id = _optional_text(root.AccountId())
    strategy_id = _optional_text(root.StrategyId())
    if kind == "reservation_changed":
        body: object = {
            "reservation_id": _required_text(root.ReservationId(), "reservation_id"),
            "request_id": _required_text(root.RequestId(), "request_id"),
            "status": _required_text(root.ReservationStatus(), "reservation_status"),
        }
    elif kind == "decision_evaluated":
        body = {
            "decision_id": _required_text(root.DecisionId(), "decision_id"),
            "request_id": _required_text(root.RequestId(), "request_id"),
            "allowed": bool(root.Allowed()),
            "degraded": bool(root.Degraded()),
            "reason_codes": _text_vector(root.ReasonCodesLength, root.ReasonCodes),
            "violations": _text_vector(root.ViolationsLength, root.Violations),
        }
    elif kind == "circuit_changed":
        circuit = root.Circuit()
        if circuit is None:
            raise ValueError("Risk circuit event is missing circuit state")
        body = {
            "exchange_id": _optional_text(circuit.ExchangeId()),
            "state": _required_text(circuit.State(), "circuit.state"),
            "reason": _required_text(circuit.Reason(), "circuit.reason"),
            "opened_at_unix_nanos": int(circuit.OpenedAtUnixNanos()),
            "reset_at_unix_nanos": int(circuit.ResetAtUnixNanos()),
        }
    elif kind == "policy_activated":
        body = {}
    else:
        raise ValueError(f"unsupported Risk event kind: {kind}")
    return RiskEventRecord(
        stream_id=_required_text(header.StreamId(), "stream_id"),
        sequence=sequence,
        producer=_required_text(header.ProducerId(), "producer_id"),
        kind=kind,
        account_id=account_id,
        strategy_id=strategy_id,
        payload=body,
        occurred_at_unix_nanos=occurred_at,
        launch_id=_optional_text(header.LaunchId()),
        instance_id=_optional_text(header.InstanceId()),
    )


def _required_text(value: bytes | None, name: str) -> str:
    result = "" if value is None else value.decode()
    if not result.strip():
        raise ValueError(f"Risk event {name} is required")
    return result


def _optional_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    result = value.decode()
    return result if result.strip() else None


def _text_vector(length, value) -> tuple[str, ...]:
    return tuple(
        _required_text(value(index), "vector value") for index in range(length())
    )


__all__ = ["AeronRiskEventSource", "RiskEventRecord", "decode_risk_event"]
