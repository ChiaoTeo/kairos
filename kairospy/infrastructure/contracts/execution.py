"""Python-facing Execution contract."""

from __future__ import annotations

from pathlib import Path

from kairospy.infrastructure.transport.commands import (
    ExecutionIntentCommandPort,
    UnixJsonCommandClient,
)

from .base import CommandEnvelope, MmapSnapshotReader, QueryEnvelope


def snapshot_reader(path: str | Path) -> MmapSnapshotReader:
    from kairospy.infrastructure.transport.generated.kairos.execution.v1.OrdersSnapshot import OrdersSnapshot

    return MmapSnapshotReader(path, file_identifier=b"PEO1", root_type=OrdersSnapshot)


def intent_port(
    path: str | Path,
    *,
    allow_trading: bool = True,
    max_order_notional=None,
    require_limit_orders: bool = False,
    launch_id: str | None = None,
) -> ExecutionIntentCommandPort:
    return ExecutionIntentCommandPort(
        UnixJsonCommandClient(path),
        allow_trading=allow_trading,
        max_order_notional=max_order_notional,
        require_limit_orders=require_limit_orders,
        launch_id=launch_id,
    )


__all__ = ["CommandEnvelope", "ExecutionIntentCommandPort", "QueryEnvelope", "intent_port", "snapshot_reader"]
