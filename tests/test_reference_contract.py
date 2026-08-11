from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairospy.infrastructure.contracts.reference import read_manifest


def test_reference_manifest_requires_complete_view_set(tmp_path) -> None:
    path = tmp_path / "reference.manifest"
    path.write_text(
        json.dumps(
            {
                "generation": 3,
                "event_sequence": 7,
                "views": [
                    "reference.catalog",
                    "reference.entities",
                    "reference.assets",
                    "reference.instruments",
                    "reference.listings",
                    "reference.markets",
                    "reference.financial_products",
                    "reference.execution_accesses",
                ],
            }
        ),
        encoding="utf-8",
    )
    assert read_manifest(path)["generation"] == 3

    path.write_text(
        '{"generation": 3, "event_sequence": 7, "views": []}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="invalid Reference snapshot manifest"):
        read_manifest(path)


def test_reference_catalog_golden_fixture_has_cross_language_shape() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "reference_catalog_empty.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(fixture) == {
        "entities",
        "assets",
        "instruments",
        "listings",
        "markets",
        "financial_products",
        "execution_accesses",
        "lifecycle_events",
        "generation",
        "event_sequence",
    }


def test_python_reads_the_rust_python_flatbuffers_golden_fixture() -> None:
    from kairospy.infrastructure.transport.generated.kairos.reference.v1.CatalogSnapshot import (
        CatalogSnapshot,
    )

    payload = bytes.fromhex(
        (
            Path(__file__).parent / "fixtures" / "reference_catalog_empty.prc1.hex"
        ).read_text(encoding="utf-8")
    )
    assert CatalogSnapshot.CatalogSnapshotBufferHasIdentifier(payload, 0)
    snapshot = CatalogSnapshot.GetRootAs(payload, 0)
    assert snapshot.Header().SnapshotId() == b"reference:0"
    assert snapshot.Header().ViewKey() == b"reference.catalog"
    assert snapshot.Payload().EntityCount() == 0
    assert snapshot.Payload().MarketCount() == 0
