



from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kairospy import Workspace
from kairospy.data import DatasetClient, DatasetStore
from kairospy.data.acquisition.historical_service import HistoricalDataService


WORKSPACE = "market-print"
DATASET = "local.market_ticks"
BINDING = "market"


def main() -> None:
    source = _write_example_csv(Path(".kairos") / "examples" / "market_ticks.csv")
    workspace = Workspace.open_or_create(WORKSPACE)
    _import_dataset_once(source)
    workspace.attach(BINDING, dataset=DATASET, view="both")

    df = workspace.data.get(BINDING).collect("pandas")
    print(df)


def _import_dataset_once(source: Path) -> None:
    store = DatasetStore(".kairos/data")
    data_root = store.data_path(DATASET)
    if data_root.exists() and any(data_root.rglob("*.parquet")):
        return
    HistoricalDataService(".kairos/data").add(SimpleNamespace(
        source=source,
        name=DATASET,
        time="timestamp",
        protocol=None,
        start=None,
        end=None,
        instrument=[],
    ))
    DatasetClient(".kairos/data").metadata(DATASET)


def _write_example_csv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "timestamp,instrument_id,open,high,low,close,volume",
            "2026-01-02T14:30:00+00:00,equity:us:AAPL,187.20,187.65,186.90,187.42,125000",
            "2026-01-02T14:31:00+00:00,equity:us:AAPL,187.42,187.88,187.35,187.76,118500",
            "2026-01-02T14:32:00+00:00,equity:us:AAPL,187.76,188.10,187.51,187.94,132400",
            "2026-01-02T14:33:00+00:00,equity:us:AAPL,187.94,188.22,187.80,188.05,119300",
            "2026-01-02T14:34:00+00:00,equity:us:AAPL,188.05,188.36,187.91,188.18,141200",
            "2026-01-02T14:35:00+00:00,equity:us:AAPL,188.18,188.40,187.96,188.09,116700",
        ])
        + "\n",
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    main()
