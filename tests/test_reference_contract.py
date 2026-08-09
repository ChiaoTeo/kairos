from __future__ import annotations

import json

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

    path.write_text('{"generation": 3, "event_sequence": 7, "views": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Reference snapshot manifest"):
        read_manifest(path)
