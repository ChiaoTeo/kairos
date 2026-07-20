from __future__ import annotations

import gzip
from hashlib import sha256
import json
from pathlib import Path
from decimal import Decimal
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from kairos.connectors.massive import MassiveEquityDailyOhlcvPipeline, SpxwDailyOhlcvPipeline
from kairos.connectors.massive.vendor_archive import MassiveFlatFileBatchDownloader, request_fingerprint
from kairos.data import DataCatalog, DatasetQualityService, QualityLevel, ResearchDataClient
from kairos.features.us_equity_momentum import UsEquityMomentumDatasetBuilder, UsEquityMomentumPolicy


HEADER = "ticker,volume,open,close,high,low,window_start,transactions\n"


class MassiveDailyOhlcvTests(unittest.TestCase):
    def test_inventory_conversion_and_rolling_representatives_are_deterministic(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _flat(root, "2026-01-02", [
                "O:SPXW260102C06000000,100,11,12,13,10,1767330000000000000,20",
                "O:SPXW260102P06000000,90,8,9,10,7,1767330000000000000,18",
                "O:SPY260102C00600000,200,1,2,2,1,1767330000000000000,30",
            ])
            _flat(root, "2026-01-05", [
                "O:SPXW260105C06100000,120,10,11,12,9,1767589200000000000,24",
                "O:SPXW260105P06100000,110,7,8,9,6,1767589200000000000,22",
            ])
            pipeline = SpxwDailyOhlcvPipeline(root)
            first = pipeline.prepare("spxw.day.test.v1", _date("2026-01-02"), _date("2026-01-06"))
            second = pipeline.prepare("spxw.day.test.v1", _date("2026-01-02"), _date("2026-01-06"))
            self.assertEqual(first["dataset_sha256"], second["dataset_sha256"])
            self.assertEqual(first["rows"], 4)
            inventory = json.loads((root / first["inventory_path"]).read_text())
            self.assertEqual([item["trading_date"] for item in inventory["entries"]], ["2026-01-02", "2026-01-05"])
            quality = json.loads((root / "curated/provider=massive/dataset=spxw.day.test.v1/quality.json").read_text())
            self.assertTrue(quality["publishable"]); self.assertEqual(quality["observed_trading_days"], 2)
            import pyarrow.parquet as pq
            monthly = pq.read_table(root / "curated/provider=massive/dataset=spxw.day.test.v1/year=2026/month=01" / first["files"][0]["path"].split("/")[-1]).to_pylist()
            self.assertEqual(monthly[0]["available_time"].isoformat(), "2026-01-03T16:00:00+00:00")
            representatives = pq.read_table(root / "curated/provider=massive/dataset=spxw.day.test.v1/daily_representatives.parquet").to_pylist()
            self.assertEqual(len(representatives), 2)
            self.assertEqual(representatives[0]["synthetic_forward_0dte"], 6003)
            self.assertEqual(representatives[0]["top_call_ticker"], "O:SPXW260102C06000000")

    def test_inventory_fails_closed_on_a_missing_trading_day(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _flat(root, "2026-01-02", ["O:SPXW260102C06000000,1,1,1,1,1,1767330000000000000,1"])
            with self.assertRaisesRegex(FileNotFoundError, "missing 1 trading days"):
                SpxwDailyOhlcvPipeline(root).build_inventory(_date("2026-01-02"), _date("2026-01-06"))

    def test_equity_daily_ohlcv_preserves_raw_and_vendor_adjusted_views(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            source = _EquitySource(root)
            pipeline.source = source

            raw = pipeline.prepare("equity.raw.test.v1", "nvda", _date("2026-01-02"), _date("2026-01-03"), view="raw")
            adjusted = pipeline.prepare(
                "equity.adjusted.test.v1", "NVDA", _date("2026-01-02"), _date("2026-01-03"),
                view="vendor_adjusted",
            )

            self.assertFalse(source.calls[0]["params"]["adjusted"])
            self.assertTrue(source.calls[1]["params"]["adjusted"])
            self.assertEqual(raw["view"], "raw")
            self.assertEqual(adjusted["view"], "vendor_adjusted")
            raw_dir = root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=equity.raw.test.v1"
            adjusted_dir = root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=vendor_adjusted/dataset=equity.adjusted.test.v1"
            self.assertTrue((raw_dir / "manifest.json").exists())
            self.assertTrue((adjusted_dir / "manifest.json").exists())
            raw_quality = json.loads((raw_dir / "quality.json").read_text())
            self.assertEqual(raw_quality["quality_level"], "Q2")
            self.assertIn("bounded single-ticker", raw_quality["known_limitations"][0])

            import pyarrow.parquet as pq
            row = pq.read_table(raw_dir / raw["file"]).to_pylist()[0]
            self.assertEqual(row["ticker"], "NVDA")
            self.assertEqual(row["price_view"], "raw")

    def test_us_equity_momentum_builder_materializes_derived_datasets_without_future_data(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767589200000, "o": 110, "h": 111, "l": 109, "c": 110, "v": 100000, "n": 5, "vw": 110},
                {"t": 1767675600000, "o": 121, "h": 122, "l": 120, "c": 121, "v": 100000, "n": 5, "vw": 121},
            ])
            pipeline.source = source
            pipeline.prepare("equity.raw.test.v1", "NVDA", _date("2026-01-02"), _date("2026-01-07"), view="raw")
            source_dir = "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=equity.raw.test.v1"

            manifest = UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                source_dir, dataset_id="us-equity-momentum.test.v1",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
            )

            self.assertEqual(set(manifest["outputs"]), {"returns", "universe", "liquidity", "momentum"})
            import pyarrow.parquet as pq
            returns_release = manifest["outputs"]["returns"]["release_id"]
            returns_dir = root / f"curated/market/returns/asset_class=equity/region=us/interval=1d/dataset={returns_release}"
            returns_rows = pq.read_table(returns_dir / manifest["outputs"]["returns"]["file"]).to_pylist()
            self.assertIsNone(returns_rows[0]["simple_return"])
            self.assertEqual(returns_rows[1]["simple_return"], Decimal("0.1"))
            momentum_release = manifest["outputs"]["momentum"]["release_id"]
            momentum_dir = root / f"features/equity/region=us/feature_set=momentum-v1/frequency=1d/dataset={momentum_release}"
            momentum_rows = pq.read_table(momentum_dir / manifest["outputs"]["momentum"]["file"]).to_pylist()
            self.assertIsNone(momentum_rows[0]["short_term_reversal_1m"])
            self.assertIsNone(momentum_rows[1]["short_term_reversal_1m"])
            self.assertEqual(momentum_rows[2]["short_term_reversal_1m"], Decimal("0.1"))
            universe_release = manifest["outputs"]["universe"]["release_id"]
            universe_dir = root / f"curated/market/universe/asset_class=equity/region=us/frequency=1d/dataset={universe_release}"
            universe_rows = pq.read_table(universe_dir / manifest["outputs"]["universe"]["file"]).to_pylist()
            self.assertFalse(universe_rows[0]["eligible"])
            self.assertTrue(universe_rows[2]["eligible"])
            catalog = DataCatalog(root)
            self.assertEqual(catalog.release("features.momentum.equity.us.1d").release_id, momentum_release)
            queried = ResearchDataClient(root).load_rows("features.momentum.equity.us.1d")
            self.assertEqual(len(queried), 3)
            self.assertEqual(queried[2]["short_term_reversal_1m"], Decimal("0.1"))
            assessment = DatasetQualityService(root).assess(momentum_release)
            self.assertTrue(assessment.passed)
            self.assertEqual(assessment.profile, "equity_feature")
            self.assertEqual(assessment.level, QualityLevel.BACKTEST)

    def test_us_equity_momentum_builder_uses_manifest_files_not_adjacent_parquet(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767589200000, "o": 110, "h": 111, "l": 109, "c": 110, "v": 100000, "n": 5, "vw": 110},
            ])
            pipeline.source = source
            manifest = pipeline.prepare("equity.raw.test.v1", "NVDA", _date("2026-01-02"), _date("2026-01-06"), view="raw")
            source_dir = root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=equity.raw.test.v1"

            import pyarrow as pa
            import pyarrow.parquet as pq
            pq.write_table(pa.Table.from_pylist([{
                "ticker": "BAD",
                "instrument_id": "equity:us:BAD",
                "event_date": _date("2026-01-05"),
                "available_time": "2026-01-05T21:00:00+00:00",
                "close": Decimal("999"),
            }]), source_dir / "adjacent-helper.parquet")

            built = UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                source_dir, dataset_id="us-equity-momentum.test.v1",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
            )

            self.assertEqual(built["outputs"]["returns"]["rows"], manifest["rows"])

    def test_us_equity_momentum_builder_materializes_missing_trading_sessions(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767675600000, "o": 121, "h": 122, "l": 120, "c": 121, "v": 100000, "n": 5, "vw": 121},
            ])
            pipeline.source = source
            pipeline.prepare("equity.raw.test.v1", "NVDA", _date("2026-01-02"), _date("2026-01-07"), view="raw")

            manifest = UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=equity.raw.test.v1",
                dataset_id="us-equity-momentum.test.v1",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
            )

            import pyarrow.parquet as pq
            universe_release = manifest["outputs"]["universe"]["release_id"]
            universe_dir = root / f"curated/market/universe/asset_class=equity/region=us/frequency=1d/dataset={universe_release}"
            universe_rows = pq.read_table(universe_dir / manifest["outputs"]["universe"]["file"]).to_pylist()
            self.assertEqual([item["event_date"].isoformat() for item in universe_rows], ["2026-01-02", "2026-01-05", "2026-01-06"])
            self.assertEqual(universe_rows[1]["price_observation_status"], "missing_bar")
            self.assertEqual(universe_rows[1]["missing_reason"], "expected_trading_session_without_bar")
            self.assertIn("expected_trading_session_without_bar", universe_rows[1]["exclusion_reasons"])
            self.assertFalse(universe_rows[1]["eligible"])
            self.assertEqual(universe_rows[1]["history_observations"], 1)
            self.assertTrue(universe_rows[1]["critical_gap"])

            coverage = json.loads((universe_dir / "coverage.json").read_text())
            self.assertEqual(coverage["observed_rows"], 2)
            self.assertEqual(coverage["missing_bar_rows"], 1)
            assessment = DatasetQualityService(root).assess(universe_release)
            self.assertTrue(assessment.passed)

    def test_us_equity_momentum_builder_classifies_missing_sessions_with_reference_dates(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767675600000, "o": 121, "h": 122, "l": 120, "c": 121, "v": 100000, "n": 5, "vw": 121},
            ])
            pipeline.source = source
            pipeline.prepare("equity.raw.test.v1", "NVDA", _date("2026-01-02"), _date("2026-01-07"), view="raw")
            reference = root / "reference/provider=massive/equity_identity/version=test"
            reference.mkdir(parents=True)
            (reference / "instruments.json").write_text(json.dumps([{
                "instrument_id": "equity:us:NVDA",
                "security_type": "CS",
                "listing_date": "1999-01-22",
                "delisting_date": "2026-01-05",
                "active": False,
            }]), encoding="utf-8")

            manifest = UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=equity.raw.test.v1",
                dataset_id="us-equity-momentum.test.v1",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
                reference_directory=reference,
            )

            import pyarrow.parquet as pq
            universe_release = manifest["outputs"]["universe"]["release_id"]
            universe_dir = root / f"curated/market/universe/asset_class=equity/region=us/frequency=1d/dataset={universe_release}"
            universe_rows = pq.read_table(universe_dir / manifest["outputs"]["universe"]["file"]).to_pylist()
            self.assertEqual(universe_rows[1]["missing_reason"], "delisted_after_reference_end")
            self.assertIn("delisted_after_reference_end", universe_rows[1]["exclusion_reasons"])
            self.assertTrue(manifest["reference"]["content_sha256"])
            self.assertEqual(manifest["reference"]["record_count"], 1)

    def test_us_equity_momentum_builder_applies_supplied_split_and_dividend_events(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = MassiveEquityDailyOhlcvPipeline(root, client=object())
            source = _EquitySource(root, rows=[
                {"t": 1767330000000, "o": 100, "h": 101, "l": 99, "c": 100, "v": 100000, "n": 5, "vw": 100},
                {"t": 1767589200000, "o": 110, "h": 111, "l": 109, "c": 110, "v": 100000, "n": 5, "vw": 110},
                {"t": 1767675600000, "o": 60, "h": 61, "l": 59, "c": 60, "v": 200000, "n": 5, "vw": 60},
            ])
            pipeline.source = source
            pipeline.prepare("equity.raw.test.v1", "NVDA", _date("2026-01-02"), _date("2026-01-07"), view="raw")
            actions = root / "reference/provider=massive/corporate_actions/ticker=NVDA/version=test"
            actions.mkdir(parents=True)
            (actions / "events.json").write_text(json.dumps([
                {
                    "instrument_id": "equity:us:NVDA",
                    "effective_at": {"$datetime": "2026-01-06T00:00:00+00:00"},
                    "ratio": {"$decimal": "2"},
                },
                {
                    "instrument_id": "equity:us:NVDA",
                    "ex_date": {"$datetime": "2026-01-06T00:00:00+00:00"},
                    "amount_per_share": {"$decimal": "1"},
                },
            ]), encoding="utf-8")

            manifest = UsEquityMomentumDatasetBuilder(root).build_from_ohlcv_directory(
                "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d/view=raw/dataset=equity.raw.test.v1",
                dataset_id="us-equity-momentum.test.v1",
                policy=UsEquityMomentumPolicy(minimum_adv20=Decimal("1"), minimum_history=2),
                corporate_actions_directory=actions,
            )

            import pyarrow.parquet as pq
            release = manifest["outputs"]["returns"]["release_id"]
            returns_dir = root / f"curated/market/returns/asset_class=equity/region=us/interval=1d/dataset={release}"
            rows = pq.read_table(returns_dir / manifest["outputs"]["returns"]["file"]).to_pylist()
            self.assertEqual(rows[2]["split_ratio"], Decimal("2"))
            self.assertEqual(rows[2]["cash_dividend"], Decimal("1"))
            self.assertEqual(rows[2]["simple_return"].quantize(Decimal("0.000001")), Decimal("-0.454545"))
            self.assertEqual(rows[2]["split_adjusted_return"].quantize(Decimal("0.000001")), Decimal("0.090909"))
            self.assertEqual(rows[2]["total_return"].quantize(Decimal("0.000001")), Decimal("0.109091"))
            quality = json.loads((returns_dir / "quality.json").read_text())
            self.assertIn("supplied split and cash dividend", " ".join(quality["known_limitations"]))


def _date(value: str):
    from datetime import date
    return date.fromisoformat(value)


def _flat(root: Path, value: str, rows: list[str]) -> None:
    trading_day = _date(value)
    key = MassiveFlatFileBatchDownloader.file_key(trading_day)
    directory = root / "source/provider=massive/resource=flat-files" / f"request_id={request_fingerprint(key, {})}"
    directory.mkdir(parents=True)
    path = directory / f"{value}.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(HEADER)
        handle.write("\n".join(rows) + "\n")
    digest = sha256(path.read_bytes()).hexdigest()
    (directory / "receipt.json").write_text(json.dumps({
        "status": "complete", "file_key": key, "bytes": path.stat().st_size, "sha256": digest,
    }), encoding="utf-8")


class _EquitySource:
    def __init__(self, root: Path, rows: list[dict[str, object]] | None = None) -> None:
        self.root = root
        self.rows = rows or [{"t": 1767330000000, "o": 100, "h": 110, "l": 90, "c": 105, "v": 1000, "n": 5, "vw": 103}]
        self.calls: list[dict[str, object]] = []

    def fetch_pages(self, resource, params):
        self.calls.append({"resource": resource, "params": dict(params)})
        directory = self.root / "source/provider=massive/resource=fake" / f"request_id={len(self.calls)}"
        directory.mkdir(parents=True, exist_ok=True)
        receipt = {"resource": resource, "parameters": dict(params), "status": "complete"}
        (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        return SimpleNamespace(directory=directory, receipt=receipt)

    def iter_results(self, _archive):
        yield from self.rows


if __name__ == "__main__":
    unittest.main()
