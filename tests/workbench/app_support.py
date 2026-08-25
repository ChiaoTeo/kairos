"""Shared fixtures for Workbench interaction tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from textual.widgets import RichLog

from kairospy.investment.apps.reference.application.models import (
    InstrumentRef,
    Market,
    MarketStatus,
)
from kairospy.primitives.reference import ExchangeId, InstrumentId, MarketId
from kairospy.surface.workbench import WorkbenchState
from kairospy.system.apps.observe.application import ObserveSnapshot


def workbench_state() -> WorkbenchState:
    owner = SimpleNamespace(
        workspace_id="trader",
        paths=SimpleNamespace(
            root=Path("/workspace/trader/.kairos"),
            project_root=Path("/workspace/trader"),
        ),
    )
    return WorkbenchState(
        owner=owner,
        workspace_arg=owner.paths.root,
        snapshot=ObserveSnapshot(
            workspace_id="trader",
            shared_services={
                "reference": {"status": "running", "operating_mode": "continuous"},
                "market": {"status": "not_running", "operating_mode": "stopped"},
            },
            support_processes={
                "system-supervisor": {"status": "running"},
                "aeron": {"status": "running"},
            },
        ),
    )


def market() -> Market:
    return Market(
        id=MarketId("market:aapl-nasdaq"),
        instrument=InstrumentRef(InstrumentId("instrument:aapl"), "AAPL"),
        listing_id=None,
        exchange_id=ExchangeId("exchange:nasdaq"),
        instrument_kind="equity",
        venue_symbol="AAPL",
        quote_asset="USD",
        status=MarketStatus.ACTIVE,
    )


def log_text(log: RichLog) -> str:
    return "\n".join(line.text for line in log.lines)


__all__ = ["log_text", "market", "workbench_state"]
