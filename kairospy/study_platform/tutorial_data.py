from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from kairospy.data import (
    DataCatalog, DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract, DatasetStorageKind,
    QualityLevel, SourceBinding,
)
from kairospy.data.publishing import publish_release, release_path
from kairospy.domain.identity import InstrumentId
from kairospy.domain.market_data import Bar
from kairospy.storage.data_lake import write_intraday_dataset


SMA_TUTORIAL_RELEASE_ID = "fixture:sma-bars-v1"

SMA_TUTORIAL_DATASET = DataProductContract(
    DataProductDefinition(
        DatasetKey("market.ohlcv.crypto.tutorial.btc-usdt.1h"),
        "Deterministic BTC/USDT hourly tutorial bars",
        DatasetLayer.CANONICAL,
        description="Synthetic point-in-time OHLCV bars for the first study tutorial.",
        dimensions={"asset_class": "crypto", "venue": "tutorial", "instrument": "BTC-USDT", "frequency": "1h"},
        primary_time="available_time",
        sources=(SourceBinding("synthetic-fixture", "tutorial", 100, QualityLevel.BACKTEST, ("bundled",)),),
        owner="study-platform",
    ),
    "canonical/tutorial/market/ohlcv/instrument=BTC-USDT/interval=1h",
    "market.ohlcv.v1",
    capabilities={"point_in_time_universe": True, "supported_products": ["spot"], "maximum_validation_level": 2},
    storage_kind=DatasetStorageKind.TABULAR,
    quality_profile="ohlcv",
    minimum_publication_level=QualityLevel.BACKTEST,
)


def tutorial_sma_bars() -> tuple[Bar, ...]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    instrument = InstrumentId("crypto:binance:spot:BTCUSDT")
    values = []
    for index in range(90):
        close = Decimal("100") + Decimal(index % 30) - Decimal((index // 30) * 8)
        values.append(Bar(
            instrument, start + timedelta(hours=index), start + timedelta(hours=index + 1),
            close - Decimal("0.5"), close + Decimal("1"), close - Decimal("1"), close,
            Decimal("10") + index,
        ))
    return tuple(values)


def ensure_sma_tutorial_dataset(root: str | Path):
    """Publish the bundled fixture through the same governed path used by real study data."""
    lake = Path(root)
    catalog = DataCatalog(lake)
    try:
        release = catalog.release(SMA_TUTORIAL_RELEASE_ID)
    except KeyError:
        release = None
    if release is not None:
        directory = lake / release.relative_path
        if not directory.exists():
            raise FileNotFoundError(f"tutorial Dataset Release directory is missing: {directory}")
        return release

    rows = [_bar_row(bar) for bar in tutorial_sma_bars()]
    directory = lake / release_path(SMA_TUTORIAL_DATASET, SMA_TUTORIAL_RELEASE_ID)
    schema = {
        "schema_id": SMA_TUTORIAL_DATASET.schema_id,
        "schema_version": 1,
        "primary_key": ["instrument_id", "period_start"],
        "primary_time": "available_time",
        "fields": {
            "instrument_id": {"type": "string"},
            "period_start": {"type": "datetime", "timezone": "UTC"},
            "period_end": {"type": "datetime", "timezone": "UTC"},
            "event_time": {"type": "datetime", "timezone": "UTC"},
            "available_time": {"type": "datetime", "timezone": "UTC"},
            "open": {"type": "decimal"}, "high": {"type": "decimal"},
            "low": {"type": "decimal"}, "close": {"type": "decimal"},
            "volume": {"type": "decimal"},
        },
    }
    lineage = {
        "lineage_version": 2, "dataset_id": SMA_TUTORIAL_RELEASE_ID,
        "producer": {"name": "kairospy.study_platform.tutorial_data", "version": "1"},
        "source": {"provider": "synthetic-fixture"}, "point_in_time_safe": True,
        "synthetic": True,
    }
    manifest = write_intraday_dataset(
        directory, rows, dataset_id=SMA_TUTORIAL_RELEASE_ID, schema=schema, lineage=lineage,
        interval=timedelta(hours=1), capabilities=dict(SMA_TUTORIAL_DATASET.capabilities),
    )
    return publish_release(
        lake, SMA_TUTORIAL_DATASET, SMA_TUTORIAL_RELEASE_ID, manifest,
        provider="synthetic-fixture", venue="tutorial", transform_id="bundled-sma-bars",
        transform_version="1", quality_level=QualityLevel.BACKTEST,
    )


def _bar_row(bar: Bar) -> dict[str, object]:
    return {
        "instrument_id": bar.instrument_id.value,
        "period_start": bar.start,
        "period_end": bar.end,
        "event_time": bar.end,
        "available_time": bar.end,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }
