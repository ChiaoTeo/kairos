from __future__ import annotations

import json
from pathlib import Path
from io import StringIO
from types import SimpleNamespace

import pytest

from kairospy.application.reference import (
    ReferenceApplication,
    ReferenceNotFoundError,
    validate_reference_runtime,
)
from kairospy.domain_types import MarketId
from kairospy.infrastructure.contracts.reference import ReferenceClient


class _Market:
    MarketId = lambda self: b"market:binance:spot:BTCUSDT"
    MarketKey = lambda self: b"BTCUSDT"
    InstrumentId = lambda self: b"instrument:spot:BTC"
    ListingId = lambda self: b"listing:binance:spot:BTCUSDT"
    ExchangeId = lambda self: b"exchange:binance"
    MarketType = lambda self: b"spot"
    AssetType = lambda self: None
    SourceSymbol = lambda self: b"BTCUSDT"
    BaseAssetId = lambda self: None
    QuoteAssetId = lambda self: None
    UnderlyingInstrumentId = lambda self: None
    Status = lambda self: 2
    PriceTick = lambda self: None
    QuantityTick = lambda self: None
    MinimumQuantity = lambda self: None
    MinimumNotional = lambda self: None
    ContractSize = lambda self: None


class _State:
    MarketsLength = lambda self: 1
    Markets = lambda self, index: _Market()
    EntitiesLength = AssetsLength = InstrumentsLength = ListingsLength = lambda self: 0
    FinancialProductsLength = ExecutionAccessesLength = MarketDataAccessesLength = lambda self: 0
    ProviderHealthLength = OptionUnderlyingsLength = LifecycleEventsLength = lambda self: 0


class _Client(ReferenceClient):
    def _view(self):
        return SimpleNamespace(
            generation=3,
            event_sequence=7,
            value=SimpleNamespace(State=lambda: _State()),
        )


def test_reference_mmap_client_reads_watermark_and_scoped_markets() -> None:
    client = _Client()
    assert client.catalog()["generation"] == 3
    assert client.catalog()["catalog"]["market_count"] == 1
    assert (
        client.resolve_market(symbol="BTCUSDT")["instrument_id"]
        == "instrument:spot:BTC"
    )


def test_reference_application_reads_concrete_mmap_client() -> None:
    application = ReferenceApplication(_Client())

    markets = application.find_markets(
        symbol="BTCUSDT", exchange="binance", market_type="spot"
    )

    assert len(markets) == 1
    assert markets[0].id == MarketId("market:binance:spot:BTCUSDT")
    assert application.require_market(markets[0].id) == markets[0]
    assert application.market(MarketId("market:missing")) is None
    with pytest.raises(ReferenceNotFoundError):
        application.require_market(
            symbol="ETHUSDT", exchange="binance", market_type="spot"
        )


def test_reference_application_has_no_callable_or_compatibility_facade() -> None:
    root = Path(__file__).parents[1]
    application = (root / "kairospy/application/reference/application.py").read_text(
        encoding="utf-8"
    )
    strategy_services = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (root / "kairospy/application/strategy/services").glob("*.py")
        )
    )
    public_api = (root / "kairospy/application/reference/__init__.py").read_text(
        encoding="utf-8"
    )

    assert "Callable" not in application
    assert "Protocol" not in (
        root / "kairospy/application/reference/validation.py"
    ).read_text(encoding="utf-8")
    assert "reference.markets" not in strategy_services
    assert "ReferenceClient" not in public_api
    assert not (root / "kairospy/application/reference/client.py").exists()
    assert not (root / "kairospy/infrastructure/contracts/reference.py").exists()


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


def test_reference_client_reads_lifecycle_events_by_sequence(
    tmp_path,
) -> None:
    result = _Client().events(
        sequence_from=4, sequence_to=8, limit=9
    )

    assert result["event_sequence"] == 7
    assert result["events"] == []


def test_reference_client_scopes_refresh_and_provider_controls(
    tmp_path, monkeypatch
) -> None:
    observed: list[tuple[str, str, float]] = []

    def request_sync(socket_path, method, target, body=None, *, timeout):
        assert socket_path == tmp_path / "reference.sock"
        observed.append((method, target, timeout))
        return 200, {"status": "ok"}

    monkeypatch.setattr(
        "kairospy.infrastructure.transport.commands.request_sync", request_sync
    )
    client = _Client(socket_path=tmp_path / "reference.sock")

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
        ("POST", "/v1/options/coverage/add?underlying=SPY", 120.0),
        ("POST", "/v1/options/coverage/remove?underlying=SPY", 120.0),
    ]


def test_reference_client_filters_execution_accesses_by_provider_and_product() -> None:
    class Client(ReferenceClient):
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
    class Client(ReferenceClient):
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
    assert len(result["checks"]) == 6


def test_reference_runtime_validation_reports_missing_provider_and_pending_outbox() -> (
    None
):
    class Client(ReferenceClient):
        def health(self):
            return {
                "status": "degraded",
                "generation": 1,
                "event_sequence": 0,
                "market_count": 0,
                "outbox_depth": 2,
                "providers": [],
            }

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
    class Client(ReferenceClient):
        def health(self):
            return {
                "status": "degraded",
                "generation": 0,
                "event_sequence": 0,
                "market_count": 0,
                "outbox_depth": 1,
                "providers": [],
            }

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
