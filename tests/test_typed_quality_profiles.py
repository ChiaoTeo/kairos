from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from kairospy.data.catalog import DataCatalog
from kairospy.data.contracts import (
    DatasetKey, DatasetLayer, DataProductDefinition, DataProductContract,
    DatasetRelease, DatasetStatus, DatasetStorageKind, QualityLevel,
)
from kairospy.data.quality.services import DatasetQualityService
from kairospy.infrastructure.storage.data_lake import write_daily_dataset, write_json


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=1)
EXPIRY = NOW + timedelta(days=30)


class TypedQualityProfileTests(unittest.TestCase):
    def _assess(self, profile: str, row: dict[str, object]):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        key = DatasetKey(f"quality.{profile}.fixture")
        product = DataProductDefinition(
            key, f"{profile} fixture", DatasetLayer.CANONICAL,
            f"Governed {profile} quality fixture", {"profile": profile}, owner="test",
        )
        relative = f"canonical/{profile}/release=fixture"
        primary = next((name for name in (
            "trade_id", "source_order", "instrument_id", "period_start", "effective_from",
        ) if name in row), next(iter(row)))
        partition_time = next(name for name in ("period_start", "event_time", "effective_from") if name in row)
        lineage = {"inputs": [{"release_id": "input-release", "content_hash": "abc"}]} if profile == "feature" else {"source": {"provider": "fixture"}}
        manifest = write_daily_dataset(
            root / relative,
            [row],
            dataset_id=f"{profile}-release",
            schema={"schema_id": f"quality.{profile}.v1", "primary_key": [primary]},
            lineage=lineage,
            period_start_field=partition_time,
        )
        release = DatasetRelease(
            f"{profile}-release", key, "1", f"quality.{profile}.v1", "1", "fixture", "1",
            relative, "parquet", str(manifest["dataset_sha256"]), "fixture", None, (),
            DatasetStatus.APPROVED_FOR_WORKSPACE, QualityLevel.WORKSPACE,
            storage_kind=DatasetStorageKind.TABULAR,
        )
        catalog = DataCatalog(root)
        catalog.register_product_spec(DataProductContract(
            product, f"canonical/{profile}", release.schema_id, {}, DatasetStorageKind.TABULAR,
            "1", profile, QualityLevel.WORKSPACE,
        ))
        catalog.register_release(release)
        catalog.save()
        return DatasetQualityService(root).assess(release.release_id)

    def test_quote_profile_rejects_crossed_market(self) -> None:
        row = {
            "instrument_id": "BTC-USDT", "event_time": NOW.isoformat(), "available_time": LATER.isoformat(),
            "bid": 100, "ask": 101,
        }
        self.assertTrue(self._assess("quote", row).passed)
        invalid = self._assess("quote", {**row, "bid": 102})
        self.assertFalse(invalid.passed)
        self.assertFalse(next(item for item in invalid.checks if item.name == "non_crossed_quotes").passed)

    def test_trade_profile_rejects_non_positive_trade(self) -> None:
        row = {
            "instrument_id": "BTC-OPTION", "trade_id": "t1", "event_time": NOW.isoformat(),
            "available_time": LATER.isoformat(), "price": 10, "quantity": 2, "direction": "buy",
        }
        self.assertTrue(self._assess("trade", row).passed)
        self.assertTrue(next(item for item in self._assess("trade", row).checks if item.name == "streaming_execution").passed)
        invalid = self._assess("trade", {**row, "quantity": -1})
        self.assertFalse(next(item for item in invalid.checks if item.name == "positive_trade_values").passed)

    def test_market_event_profile_rejects_future_visibility(self) -> None:
        row = {
            "source": "massive", "source_namespace": "opra", "source_instrument_id": "O:SPXW",
            "record_type": "quote", "event_time": NOW.isoformat(), "available_time": LATER.isoformat(),
            "source_order": 1,
        }
        self.assertTrue(self._assess("market_event", row).passed)
        self.assertTrue(next(item for item in self._assess("market_event", row).checks if item.name == "streaming_execution").passed)
        invalid = self._assess("market_event", {**row, "available_time": (NOW - timedelta(seconds=1)).isoformat()})
        self.assertFalse(next(item for item in invalid.checks if item.name == "event_point_in_time").passed)

    def test_option_snapshot_profile_rejects_expired_or_crossed_contract(self) -> None:
        row = {
            "instrument_id": "BTC-CALL", "period_start": NOW.isoformat(), "event_time": NOW.isoformat(),
            "available_time": LATER.isoformat(), "expiry": EXPIRY.isoformat(), "strike": 100,
            "best_bid_price": 10, "best_ask_price": 11, "mark_iv": 0.5,
        }
        self.assertTrue(self._assess("option_snapshot", row).passed)
        invalid = self._assess("option_snapshot", {
            **row, "expiry": (NOW - timedelta(days=1)).isoformat(), "best_bid_price": 12,
        })
        self.assertFalse(next(item for item in invalid.checks if item.name == "valid_option_contract").passed)
        self.assertFalse(next(item for item in invalid.checks if item.name == "non_crossed_quotes").passed)

    def test_feature_profile_rejects_future_data(self) -> None:
        row = {
            "period_start": NOW.isoformat(), "period_end": LATER.isoformat(),
            "event_time": LATER.isoformat(), "available_time": LATER.isoformat(), "feature_value": 1.5,
        }
        self.assertTrue(self._assess("feature", row).passed)
        invalid = self._assess("feature", {**row, "available_time": NOW.isoformat()})
        self.assertFalse(next(item for item in invalid.checks if item.name == "no_future_data").passed)

    def test_reference_profile_rejects_invalid_effective_range(self) -> None:
        row = {
            "instrument_id": "AAPL", "effective_from": NOW.isoformat(), "effective_to": EXPIRY.isoformat(),
        }
        self.assertTrue(self._assess("reference", row).passed)
        invalid = self._assess("reference", {**row, "effective_to": (NOW - timedelta(days=1)).isoformat()})
        self.assertFalse(next(item for item in invalid.checks if item.name == "valid_effective_range").passed)

    def test_corporate_action_profile_validates_split_and_dividend_events(self) -> None:
        events = [
            {
                "ticker": "NVDA",
                "instrument_id": "equity:us:NVDA",
                "effective_at": {"$datetime": NOW.isoformat()},
                "ratio": {"$decimal": "2"},
            },
            {
                "ticker": "NVDA",
                "instrument_id": "equity:us:NVDA",
                "ex_date": {"$datetime": NOW.isoformat()},
                "amount_per_share": {"$decimal": "1"},
            },
        ]
        assessment = self._assess_corporate_actions(events)
        self.assertTrue(assessment.passed)
        self.assertTrue(next(item for item in assessment.checks if item.name == "source_receipts").passed)

        invalid = self._assess_corporate_actions([{**events[0], "ratio": {"$decimal": "0"}}])
        self.assertFalse(invalid.passed)
        self.assertFalse(next(item for item in invalid.checks if item.name == "positive_split_ratios").passed)

    def test_equity_identity_profile_requires_clean_mappings(self) -> None:
        assessment = self._assess_equity_identity(quarantine=[])
        self.assertTrue(assessment.passed)
        self.assertTrue(next(item for item in assessment.checks if item.name == "identity_quarantine_clear").passed)

        invalid = self._assess_equity_identity(quarantine=[{"reason": "ticker_event_unmapped_symbol"}])
        self.assertFalse(invalid.passed)
        self.assertFalse(next(item for item in invalid.checks if item.name == "identity_quarantine_clear").passed)

    def _assess_equity_identity(self, quarantine: list[dict[str, object]]):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        relative = "reference/provider=massive/equity_identity/version=fixture"
        directory = root / relative
        payload = {
            "mappings": [{
                "provider_id": "massive",
                "namespace": "stocks",
                "external_id": "NVDA",
                "target_type": "instrument",
                "target_id": "equity:us:NVDA",
                "effective_from": NOW.isoformat(),
                "effective_to": None,
            }],
            "instruments": [{"instrument_id": "equity:us:NVDA", "listing_date": "1999-01-22"}],
            "quarantine": quarantine,
        }
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        write_json(directory / "mappings.json", payload["mappings"])
        write_json(directory / "instruments.json", payload["instruments"])
        write_json(directory / "quarantine.json", payload["quarantine"])
        write_json(directory / "manifest.json", {"sha256": digest, "mapping_count": 1, "instrument_count": 1, "quarantine_count": len(quarantine)})
        product = DataProductDefinition(
            DatasetKey("reference.identity.fixture"),
            "Equity identity fixture",
            DatasetLayer.REFERENCE,
            "Governed identity quality fixture",
            {"profile": "equity_identity"},
            primary_time="effective_from",
            owner="test",
        )
        release = DatasetRelease(
            "identity-release", product.key, "1", "reference.identity.v1", "1",
            "fixture", "1", relative, "json", digest, "fixture", None, (),
            DatasetStatus.APPROVED_FOR_WORKSPACE, QualityLevel.WORKSPACE,
            storage_kind=DatasetStorageKind.REFERENCE,
        )
        catalog = DataCatalog(root)
        catalog.register_product_spec(DataProductContract(
            product, "reference/provider=massive/equity_identity", release.schema_id, {},
            DatasetStorageKind.REFERENCE, "1", "equity_identity", QualityLevel.WORKSPACE,
        ))
        catalog.register_release(release)
        catalog.save()
        return DatasetQualityService(root).assess(release.release_id)

    def _assess_corporate_actions(self, events: list[dict[str, object]]):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        relative = "reference/provider=massive/corporate_actions/scope=test/version=fixture"
        directory = root / relative
        digest = sha256(json.dumps(events, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        write_json(directory / "events.json", events)
        write_json(directory / "manifest.json", {
            "sha256": digest,
            "event_count": len(events),
            "source_receipts": ["source/provider=massive/fake/receipt.json"],
        })
        product = DataProductDefinition(
            DatasetKey("reference.corporate_actions.fixture"),
            "Corporate action fixture",
            DatasetLayer.SOURCE,
            "Governed corporate action quality fixture",
            {"profile": "corporate_action"},
            primary_time="effective_at",
            owner="test",
        )
        release = DatasetRelease(
            "corporate-action-release", product.key, "1", "reference.corporate_actions.v1", "1",
            "fixture", "1", relative, "json", digest, "fixture", None, (),
            DatasetStatus.APPROVED_FOR_WORKSPACE, QualityLevel.WORKSPACE,
            storage_kind=DatasetStorageKind.REFERENCE,
        )
        catalog = DataCatalog(root)
        catalog.register_product_spec(DataProductContract(
            product, "reference/provider=massive/corporate_actions", release.schema_id, {},
            DatasetStorageKind.REFERENCE, "1", "corporate_action", QualityLevel.WORKSPACE,
        ))
        catalog.register_release(release)
        catalog.save()
        return DatasetQualityService(root).assess(release.release_id)


if __name__ == "__main__":
    unittest.main()
