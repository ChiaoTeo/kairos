from __future__ import annotations

from datetime import timedelta
from math import ceil
from pathlib import Path

from trading.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from trading.data.models import DatasetRelease, QualityLevel
from trading.data.products import BTC_OPTION_QUOTES_HOURLY, BTC_SPOT_DAILY, capabilities_payload
from trading.data.publishing import content_release_id, merge_release_rows, publish_release, release_path
from trading.storage.data_lake import utc_midnight, write_daily_dataset, write_intraday_dataset

from .historical_archive import BinanceSpotArchiveProvider
from .options_archive import BinanceOptionsEohArchiveProvider, normalize_eoh_rows


class BinanceSpotDatasetConnector:
    provider = "binance"

    def __init__(self, root: str | Path = "data", archive: BinanceSpotArchiveProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or BinanceSpotArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_SPOT_DAILY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Binance spot connector received an unsupported acquisition request")
        values = {}
        for missing in request.missing:
            last = (missing.end - timedelta(microseconds=1)).date()
            values.update(self.archive.fetch_daily("BTCUSDT", missing.start.date(), last, self.root / "source"))
        if not values:
            raise RuntimeError("Binance spot archive returned no rows")
        rows = merge_release_rows(
            self.root, request.base_release_id, _ohlcv(values),
            primary_key=("venue", "instrument_id", "period_start", "interval"), order_by=("period_start", "instrument_id"),
        )
        release_id = content_release_id(BTC_SPOT_DAILY, rows)
        lineage = {
            "lineage_version": 2, "dataset_id": release_id,
            "producer": {"name": type(self).__name__, "transform": "binance_spot_kline_to_market_ohlcv", "version": "2"},
            "source": {"provider": "binance", "venue": "binance", "dataset": "spot_klines",
                       "transport": "public_archive", "authentication": "none"},
            "request_windows": [{"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                                for item in request.missing],
            "point_in_time_safe": True,
        }
        manifest = write_daily_dataset(
            self.root / release_path(BTC_SPOT_DAILY, release_id), rows, dataset_id=release_id,
            schema=_schema(), lineage=lineage, capabilities=capabilities_payload(BTC_SPOT_DAILY, release_id),
        )
        return publish_release(
            self.root, BTC_SPOT_DAILY, release_id, manifest, provider="binance", venue="binance",
            transform_id="binance.spot_kline.ohlcv", transform_version="2", quality_level=QualityLevel.BACKTEST,
        )


class BinanceOptionQuotesDatasetConnector:
    provider = "binance"

    def __init__(self, root: str | Path = "data", archive: BinanceOptionsEohArchiveProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or BinanceOptionsEohArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_OPTION_QUOTES_HOURLY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")

    def acquire(self, request: AcquisitionRequest) -> DatasetRelease:
        if not self.supports(request.logical_key) or request.source.provider != self.provider:
            raise ValueError("Binance option quote connector received an unsupported acquisition request")
        raw = []
        for missing in request.missing:
            last = (missing.end - timedelta(microseconds=1)).date()
            raw.extend(self.archive.fetch("BTCUSDT", missing.start.date(), last, self.root / "source"))
        rows = normalize_eoh_rows(raw)
        if not rows:
            raise RuntimeError("Binance option archive returned no rows")
        rows = merge_release_rows(
            self.root, request.base_release_id, rows, primary_key=("period_start", "instrument_id"),
            order_by=("period_start", "instrument_id"),
        )
        release_id = content_release_id(BTC_OPTION_QUOTES_HOURLY, rows)
        lineage = {
            "lineage_version": 2, "dataset_id": release_id,
            "producer": {"name": type(self).__name__, "transform": "binance_option_eoh_to_canonical_quotes", "version": "2"},
            "source": {"provider": "binance", "venue": "binance", "dataset": "option_eoh_summary",
                       "transport": "public_archive", "authentication": "none"},
            "request_windows": [{"start": item.start.isoformat(), "end": item.end.isoformat(), "boundary": "[start,end)"}
                                for item in request.missing],
            "pricing_fields": {"implied_volatility": "vendor", "greeks": "vendor"},
            "point_in_time_safe": True,
        }
        manifest = write_intraday_dataset(
            self.root / release_path(BTC_OPTION_QUOTES_HOURLY, release_id), rows, dataset_id=release_id,
            schema=_option_quote_schema(), lineage=lineage, interval=timedelta(hours=1),
            capabilities=capabilities_payload(BTC_OPTION_QUOTES_HOURLY, release_id),
        )
        return publish_release(
            self.root, BTC_OPTION_QUOTES_HOURLY, release_id, manifest, provider="binance", venue="binance",
            transform_id="binance.option_eoh.quotes", transform_version="2", quality_level=QualityLevel.RESEARCH,
        )


def _ohlcv(values):
    return [
        {"period_start": utc_midnight(day), "period_end": utc_midnight(day + timedelta(days=1)),
         "event_time": utc_midnight(day + timedelta(days=1)), "available_time": utc_midnight(day + timedelta(days=1)),
         "venue": "binance", "instrument_id": "BTC-USDT", "interval": "P1D",
         "open": value["open"], "high": value["high"], "low": value["low"], "close": value["close"],
         "volume": value.get("volume", "")}
        for day in sorted(values) for value in [values[day]]
    ]


def _days(request: AcquisitionRequest) -> int:
    return sum(max(1, ceil((item.end - item.start).total_seconds() / 86400)) for item in request.missing)


def _schema():
    return {
        "schema_id": BTC_SPOT_DAILY.schema_id, "schema_version": 1,
        "time_boundary": "[period_start,period_end)",
        "primary_key": ["venue", "instrument_id", "period_start", "interval"],
        "columns": {
            "period_start": {"type": "datetime", "timezone": "UTC"},
            "period_end": {"type": "datetime", "timezone": "UTC"},
            "event_time": {"type": "datetime", "timezone": "UTC"},
            "available_time": {"type": "datetime", "timezone": "UTC"},
            "venue": {"type": "string"}, "instrument_id": {"type": "string"},
            "interval": {"type": "duration"}, "open": {"type": "number", "unit": "USDT_per_BTC"},
            "high": {"type": "number", "unit": "USDT_per_BTC"},
            "low": {"type": "number", "unit": "USDT_per_BTC"},
            "close": {"type": "number", "unit": "USDT_per_BTC"},
            "volume": {"type": "number", "unit": "BTC"},
        },
    }


def _option_quote_schema():
    return {
        "schema_id": BTC_OPTION_QUOTES_HOURLY.schema_id, "schema_version": 1,
        "time_boundary": "[period_start,period_end)", "primary_key": ["period_start", "instrument_id"],
        "columns": {
            "period_start": {"type": "datetime", "timezone": "UTC"},
            "period_end": {"type": "datetime", "timezone": "UTC"},
            "event_time": {"type": "datetime", "timezone": "UTC"},
            "available_time": {"type": "datetime", "timezone": "UTC"},
            "venue": {"type": "string"}, "underlying_id": {"type": "string"},
            "instrument_id": {"type": "string"}, "expiry": {"type": "datetime", "timezone": "UTC"},
            "option_right": {"type": "enum", "values": ["call", "put"]},
            "strike": {"type": "number", "unit": "USDT_per_BTC"},
            "best_bid_price": {"type": "nullable_number"}, "best_ask_price": {"type": "nullable_number"},
            "bid_iv": {"type": "nullable_number", "unit": "absolute_volatility"},
            "ask_iv": {"type": "nullable_number", "unit": "absolute_volatility"},
            "mark_price": {"type": "number"}, "mark_iv": {"type": "number", "unit": "absolute_volatility"},
            "vendor_delta": {"type": "number"}, "vendor_gamma": {"type": "number"},
            "vendor_vega": {"type": "number"}, "vendor_theta": {"type": "number"},
            "volume_contracts": {"type": "number"}, "open_interest_contracts": {"type": "number"},
        },
    }
