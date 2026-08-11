from __future__ import annotations

import json
from pathlib import Path
from io import StringIO

import pytest

from kairospy.infrastructure.contracts.reference import read_manifest
from kairospy.infrastructure.contracts.reference_client import ReferenceSnapshotClient
from kairospy.application.reference import validate_reference_runtime


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


def test_reference_client_reads_lifecycle_events_by_sequence(
    tmp_path, monkeypatch
) -> None:
    observed: dict[str, object] = {}

    def request_sync(socket_path, method, target, *, timeout):
        observed.update(
            socket_path=socket_path, method=method, target=target, timeout=timeout
        )
        return 200, {"generation": 3, "event_sequence": 7, "events": []}

    monkeypatch.setattr(
        "kairospy.infrastructure.contracts.reference_client.request_sync", request_sync
    )
    socket = tmp_path / "reference.sock"
    result = ReferenceSnapshotClient(socket_path=socket).events(
        sequence_from=4, sequence_to=8, limit=9
    )

    assert result["event_sequence"] == 7
    assert observed == {
        "socket_path": socket,
        "method": "GET",
        "target": "/v1/events?sequence_from=4&sequence_to=8&limit=9",
        "timeout": 120.0,
    }


def test_reference_client_scopes_refresh_and_provider_controls(tmp_path, monkeypatch) -> None:
    observed: list[tuple[str, str, float]] = []

    def request_sync(socket_path, method, target, *, timeout):
        assert socket_path == tmp_path / "reference.sock"
        observed.append((method, target, timeout))
        return 200, {"status": "ok"}

    monkeypatch.setattr(
        "kairospy.infrastructure.contracts.reference_client.request_sync", request_sync
    )
    client = ReferenceSnapshotClient(socket_path=tmp_path / "reference.sock")

    client.refresh(source="massive-options")
    client.set_source_paused("massive-options", True)
    client.set_source_paused("massive-options", False)
    client.option_coverage()
    client.set_option_underlying("SPY", True)
    client.set_option_underlying("SPY", False)

    assert observed == [
        ("POST", "/v1/refresh?source=massive-options", 120.0),
        ("POST", "/v1/sources/pause?source=massive-options", 5.0),
        ("POST", "/v1/sources/resume?source=massive-options", 5.0),
        ("GET", "/v1/options/coverage", 5.0),
        ("POST", "/v1/options/coverage/add?underlying=SPY", 120.0),
        ("POST", "/v1/options/coverage/remove?underlying=SPY", 120.0),
    ]


def test_reference_client_filters_execution_accesses_by_provider_and_product() -> None:
    class Client(ReferenceSnapshotClient):
        def collection(self, view: str):
            assert view == "execution-accesses"
            return [
                {
                    "accessId": "execution-access:binance:equity:AAPL",
                    "providerId": "binance",
                    "productFamily": "equity",
                    "providerSymbol": "AAPL",
                    "status": "active",
                },
                {
                    "accessId": "execution-access:ibkr:equity:AAPL",
                    "providerId": "ibkr",
                    "productFamily": "equity",
                    "providerSymbol": "AAPL",
                    "status": "active",
                },
            ]

    result = Client().execution_accesses(
        provider_id="binance",
        product_family="equity",
        provider_symbol="AAPL",
        active_only=True,
    )
    assert [value["accessId"] for value in result] == [
        "execution-access:binance:equity:AAPL"
    ]


def test_reference_runtime_validation_covers_provider_snapshot_and_event_tail() -> None:
    class Client:
        def health(self):
            return {
                "status": "ready",
                "generation": 3,
                "event_sequence": 7,
                "market_count": 2,
                "outbox_depth": 0,
                "providers": [
                    {"source_id": "provider-a", "status": "ready", "stale": False}
                ],
            }

        def snapshot_views(self):
            return [{"view": str(index), "exists": True} for index in range(8)]

        def catalog(self):
            return {
                "generation": 3,
                "event_sequence": 7,
                "catalog": {"market_count": 2},
            }

        def events(self, *, sequence_from=None, sequence_to=None, limit=256):
            assert (sequence_from, sequence_to, limit) == (7, None, 1)
            return {
                "generation": 3,
                "event_sequence": 7,
                "events": [{"event_id": "reference:00000000000000000007"}],
            }

    result = validate_reference_runtime(Client(), required_sources=("provider-a",))

    assert result["status"] == "passed"
    assert result["failed_checks"] == []
    assert len(result["checks"]) == 7


def test_reference_runtime_validation_reports_missing_provider_and_pending_outbox() -> None:
    class Client:
        def health(self):
            return {
                "status": "degraded",
                "generation": 1,
                "event_sequence": 0,
                "market_count": 0,
                "outbox_depth": 2,
                "providers": [],
            }

        def snapshot_views(self):
            return []

        def catalog(self):
            return {
                "generation": 1,
                "event_sequence": 0,
                "catalog": {"market_count": 0},
            }

        def events(self, **kwargs):
            raise AssertionError("zero watermark must not query the event tail")

    result = validate_reference_runtime(Client(), required_sources=("provider-a",))

    assert result["status"] == "failed"
    assert "required_providers_ready" in result["failed_checks"]
    assert "publication_outbox_drained" in result["failed_checks"]


def test_reference_validate_cli_returns_nonzero_when_a_required_gate_fails(
    monkeypatch,
) -> None:
    class Client:
        def health(self):
            return {
                "status": "degraded",
                "generation": 0,
                "event_sequence": 0,
                "market_count": 0,
                "outbox_depth": 1,
                "providers": [],
            }

        def snapshot_views(self):
            return []

        def catalog(self):
            return {
                "generation": 0,
                "event_sequence": 0,
                "catalog": {"market_count": 0},
            }

        def events(self, **kwargs):
            raise AssertionError("zero watermark must not query the event tail")

    monkeypatch.setattr(
        "kairospy.surface.cli.commands.reference._client", lambda workspace: Client()
    )
    from kairospy.surface.cli.app import execute_argv

    output = StringIO()
    exit_code = execute_argv(["reference", "validate", "--output", "json"], output)

    assert exit_code == 1
    assert '"status": "failed"' in output.getvalue()
