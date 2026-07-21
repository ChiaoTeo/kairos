from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from pathlib import Path

from kairospy.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairospy.data.contracts import DatasetRelease, QualityLevel
from kairospy.data.products import (
    BTC_DERIBIT_OPTION_QUOTES, BTC_DERIBIT_OPTION_TRADES, BTC_DVOL_DAILY, capabilities_payload,
)
from kairospy.data.publishing import content_release_id, merge_release_rows, publish_release, release_path
from kairospy.storage.data_lake import (
    utc_midnight, write_daily_dataset, write_event_dataset, write_json,
)

from .historical import DeribitDvolProvider
from .option_chain import DeribitOptionChainProvider
from .trade_history import DeribitOptionTradeHistoryProvider, normalize_deribit_trades


class DeribitDvolDatasetConnector:
    provider = "deribit"

    def __init__(self, root: str | Path = "data", archive: DeribitDvolProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or DeribitDvolProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_DVOL_DAILY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        _validate(request, self)
        values = {}
        for missing in request.missing:
            last = (missing.end - timedelta(microseconds=1)).date()
            values.update(self.archive.fetch_daily("BTC", missing.start.date(), last, self.root / "source"))
        if not values:
            raise RuntimeError("Deribit DVOL provider returned no rows")
        rows = merge_release_rows(
            self.root, request.base_release_id, _dvol_rows(values),
            primary_key=("venue", "instrument_id", "period_start", "interval"), order_by=("period_start", "instrument_id"),
        )
        release_id = content_release_id(BTC_DVOL_DAILY, rows)
        manifest = write_daily_dataset(
            self.root / release_path(BTC_DVOL_DAILY, release_id), rows, dataset_id=release_id,
            schema=_dvol_schema(), lineage=_lineage(self, request, release_id, "deribit_dvol_to_vendor_index"),
            capabilities=capabilities_payload(BTC_DVOL_DAILY, release_id),
        )
        return publish_release(
            self.root, BTC_DVOL_DAILY, release_id, manifest, provider="deribit", venue="deribit",
            transform_id="deribit.dvol.daily", transform_version="2", quality_level=QualityLevel.WORKSPACE,
        )


class DeribitOptionTradesDatasetConnector:
    provider = "deribit"

    def __init__(self, root: str | Path = "data", archive: DeribitOptionTradeHistoryProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or DeribitOptionTradeHistoryProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_DERIBIT_OPTION_TRADES.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        _validate(request, self)
        raw = []
        for missing in request.missing:
            last = (missing.end - timedelta(microseconds=1)).date()
            raw.extend(self.archive.fetch("BTC", missing.start.date(), last, self.root / "source"))
        rows = normalize_deribit_trades(raw)
        if not rows:
            raise RuntimeError("Deribit option trade provider returned no rows")
        rows = merge_release_rows(
            self.root, request.base_release_id, rows, primary_key=("venue", "trade_id"),
            order_by=("event_time", "trade_id"),
        )
        release_id = content_release_id(BTC_DERIBIT_OPTION_TRADES, rows)
        lineage = _lineage(self, request, release_id, "deribit_option_trade_to_canonical")
        lineage["limitations"] = ["trades_only", "not_a_complete_quote_chain"]
        manifest = write_event_dataset(
            self.root / release_path(BTC_DERIBIT_OPTION_TRADES, release_id), rows, dataset_id=release_id,
            schema=_trade_schema(), lineage=lineage, capabilities=capabilities_payload(BTC_DERIBIT_OPTION_TRADES, release_id),
        )
        return publish_release(
            self.root, BTC_DERIBIT_OPTION_TRADES, release_id, manifest, provider="deribit", venue="deribit",
            transform_id="deribit.option_trades", transform_version="2", quality_level=QualityLevel.WORKSPACE,
        )


class DeribitOptionSnapshotDatasetConnector:
    provider = "deribit"

    def __init__(self, root: str | Path = "data", source: DeribitOptionChainProvider | None = None) -> None:
        self.root, self.source = Path(root), source or DeribitOptionChainProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_DERIBIT_OPTION_QUOTES.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(1, cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        _validate(request, self)
        payload, rows = self.source.snapshot("BTC")
        if not rows:
            raise RuntimeError("Deribit option snapshot provider returned no rows")
        observed = datetime.fromisoformat(str(rows[0]["available_time"]).replace("Z", "+00:00"))
        if not any(item.start <= observed < item.end for item in request.missing):
            raise RuntimeError("Deribit only provides a current option snapshot and cannot backfill the requested historical gap")
        rows = merge_release_rows(
            self.root, request.base_release_id, rows, primary_key=("period_start", "instrument_id"),
            order_by=("period_start", "instrument_id"),
        )
        release_id = content_release_id(BTC_DERIBIT_OPTION_QUOTES, rows)
        target = self.root / release_path(BTC_DERIBIT_OPTION_QUOTES, release_id)
        write_json(target / "source_snapshot.json", payload)
        lineage = _lineage(self, request, release_id, "deribit_book_summary_to_option_chain")
        lineage["limitations"] = ["current_snapshot_only", "summary_bid_ask_has_no_size"]
        manifest = write_event_dataset(
            target, rows, dataset_id=release_id, schema=_snapshot_schema(), lineage=lineage,
            capabilities=capabilities_payload(BTC_DERIBIT_OPTION_QUOTES, release_id),
        )
        return publish_release(
            self.root, BTC_DERIBIT_OPTION_QUOTES, release_id, manifest, provider="deribit", venue="deribit",
            transform_id="deribit.option_chain.snapshot", transform_version="2", quality_level=QualityLevel.WORKSPACE,
        )


def _validate(request, connector) -> None:
    if not connector.supports(request.logical_key) or request.source.provider != connector.provider:
        raise ValueError(f"{type(connector).__name__} received an unsupported acquisition request")


def _days(request: AcquisitionRequest) -> int:
    return sum(max(1, ceil((item.end - item.start).total_seconds() / 86400)) for item in request.missing)


def _lineage(connector, request, release_id, transform):
    return {
        "lineage_version": 2, "dataset_id": release_id,
        "producer": {"name": type(connector).__name__, "transform": transform, "version": "2"},
        "source": {"provider": "deribit", "venue": "deribit", "authentication": "none"},
        "request_windows": [{"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                            for item in request.missing],
        "point_in_time_safe": True,
    }


def _dvol_rows(values):
    return [
        {"period_start": utc_midnight(day), "period_end": utc_midnight(day + timedelta(days=1)),
         "event_time": utc_midnight(day + timedelta(days=1)), "available_time": utc_midnight(day + timedelta(days=1)),
         "venue": "deribit", "instrument_id": "BTC-DVOL", "interval": "P1D",
         "open": value["open"], "high": value["high"], "low": value["low"], "close": value["close"],
         "volume": value.get("volume", "")}
        for day in sorted(values) for value in [values[day]]
    ]


def _dvol_schema():
    return {"schema_id": BTC_DVOL_DAILY.schema_id, "schema_version": 1,
            "time_boundary": "[period_start,period_end)",
            "primary_key": ["venue", "instrument_id", "period_start", "interval"],
            "columns": {name: {"type": "datetime", "timezone": "UTC"}
                        for name in ("period_start", "period_end", "event_time", "available_time")} | {
                "venue": {"type": "string"}, "instrument_id": {"type": "string"},
                "interval": {"type": "duration"},
                **{name: {"type": "number", "unit": "annualized_volatility_percent"}
                   for name in ("open", "high", "low", "close")},
                "volume": {"type": "nullable", "unit": "not_applicable"},
            }}


def _trade_schema():
    return {"schema_id": BTC_DERIBIT_OPTION_TRADES.schema_id, "schema_version": 1,
            "primary_key": ["venue", "trade_id"], "columns": {
                "event_time": {"type": "datetime", "timezone": "UTC"},
                "available_time": {"type": "datetime", "timezone": "UTC"},
                "venue": {"type": "string"}, "underlying_id": {"type": "string"},
                "instrument_id": {"type": "string"}, "trade_id": {"type": "string"},
                "expiry": {"type": "datetime", "timezone": "UTC"},
                "option_right": {"type": "enum", "values": ["call", "put"]},
                "strike": {"type": "number", "unit": "USD_per_BTC"},
                "price_btc": {"type": "number", "unit": "BTC"},
                "amount_btc": {"type": "number", "unit": "BTC"},
                "direction": {"type": "enum", "values": ["buy", "sell"]},
                "trade_iv": {"type": "number", "unit": "absolute_volatility"},
                "mark_price_btc": {"type": "number", "unit": "BTC"},
                "index_price_usd": {"type": "number", "unit": "USD_per_BTC"},
                "tick_direction": {"type": "integer"},
            }}


def _snapshot_schema():
    return {"schema_id": BTC_DERIBIT_OPTION_QUOTES.schema_id, "schema_version": 1,
            "primary_key": ["period_start", "instrument_id"], "columns": {
                "period_start": {"type": "datetime", "timezone": "UTC"},
                "event_time": {"type": "datetime", "timezone": "UTC"},
                "available_time": {"type": "datetime", "timezone": "UTC"},
                "instrument_id": {"type": "string"}, "expiry": {"type": "datetime", "timezone": "UTC"},
                "option_right": {"type": "enum"}, "strike": {"type": "number", "unit": "USD_per_BTC"},
                "bid_price_btc": {"type": "nullable_number"}, "ask_price_btc": {"type": "nullable_number"},
                "mark_iv": {"type": "number", "unit": "absolute_volatility"},
                "underlying_price_usd": {"type": "number"}, "open_interest": {"type": "number"},
            }}
