from __future__ import annotations

from datetime import datetime, timezone
import json

from typer.testing import CliRunner

from kairospy.application.usecases.reference import catalog_from_equity_rows, catalog_from_market_rows
from kairospy.infrastructure.persistence.reference.sqlite_store import ReferenceStore
from kairospy.core.reference import LifecycleEvent, LifecycleEventType
from kairospy.surface.cli.commands.reference import reference_app


class _FakeReferenceClient:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def fetch_catalog(self, *, as_of, market=None, params=None):
        if "ticker" in self.row:
            return catalog_from_equity_rows((self.row,), effective_from=as_of)
        return catalog_from_market_rows((self.row,), effective_from=as_of)


class _FakeEventProvider:
    def fetch_lifecycle_events(self, ticker: str, *, start, end, catalog, venue=None):
        return (LifecycleEvent(LifecycleEventType.DIVIDEND, start, venue=venue or "nasdaq", source_symbol=ticker.upper()),)


def test_reference_search_show_and_resolve_markets(tmp_path) -> None:
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    root = tmp_path / "reference"
    catalog = catalog_from_market_rows(
        (
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "status": "trading",
            },
        ),
        effective_from=as_of,
    )
    ReferenceStore(root).save_catalog(catalog)
    market_id = str(catalog.list_markets(at=as_of)[0].market_id)

    search = CliRunner().invoke(
        reference_app,
        ["search", "BTC", "--root", str(root), "--as-of", as_of.isoformat(), "--format", "json"],
        catch_exceptions=False,
    )
    show = CliRunner().invoke(
        reference_app,
        ["show", market_id, "--root", str(root), "--as-of", as_of.isoformat(), "--format", "json"],
        catch_exceptions=False,
    )
    resolve = CliRunner().invoke(
        reference_app,
        ["markets", "resolve", "BTC/USDT", "--venue", "binance", "--market", "spot", "--root", str(root), "--as-of", as_of.isoformat(), "--format", "json"],
        catch_exceptions=False,
    )
    status = CliRunner().invoke(
        reference_app,
        ["status", "--root", str(root), "--as-of", as_of.isoformat(), "--format", "json"],
        catch_exceptions=False,
    )
    view_status = CliRunner().invoke(
        reference_app,
        ["view", "--root", str(root), "--as-of", as_of.isoformat(), "--format", "json"],
        catch_exceptions=False,
    )
    view_market = CliRunner().invoke(
        reference_app,
        ["view", market_id, "--root", str(root), "--as-of", as_of.isoformat(), "--format", "json"],
        catch_exceptions=False,
    )
    query = CliRunner().invoke(
        reference_app,
        [
            "query",
            "BTC",
            "--kind",
            "market",
            "--venue",
            "binance",
            "--market",
            "spot",
            "--active-only",
            "--root",
            str(root),
            "--as-of",
            as_of.isoformat(),
            "--format",
            "json",
        ],
        catch_exceptions=False,
    )
    brokers = CliRunner().invoke(reference_app, ["participants", "brokers", "--format", "json"], catch_exceptions=False)
    exchanges = CliRunner().invoke(reference_app, ["participants", "exchanges", "--format", "json"], catch_exceptions=False)
    providers = CliRunner().invoke(reference_app, ["participants", "providers", "--format", "json"], catch_exceptions=False)

    assert search.exit_code == 0
    assert any(json.loads(line)["kind"] == "market" for line in search.output.splitlines())
    assert json.loads(show.output)["market_id"] == market_id
    assert json.loads(resolve.output)["source_symbol"] == "BTC/USDT"
    status_payload = json.loads(status.output)
    assert status_payload["markets"] == 1
    assert status_payload["active_markets"] == 1
    assert json.loads(view_status.output)["markets"] == 1
    assert json.loads(view_market.output)["market_id"] == market_id
    query_rows = [json.loads(line) for line in query.output.splitlines()]
    assert query_rows == [{"kind": "market", **json.loads(resolve.output)}]
    broker_rows = [json.loads(line) for line in brokers.output.splitlines()]
    exchange_rows = [json.loads(line) for line in exchanges.output.splitlines()]
    provider_rows = [json.loads(line) for line in providers.output.splitlines()]
    assert any(row["broker_id"] == "binance" and row["kind"] == "broker" for row in broker_rows)
    assert any(row["exchange_id"] == "hyperliquid" and row["kind"] == "exchange" for row in exchange_rows)
    assert any(row["provider_id"] == "massive" and row["kind"] == "provider" for row in provider_rows)
    assert all("capabilities" not in row and "driver" not in row for row in [*broker_rows, *exchange_rows, *provider_rows])


def test_reference_sync_output_distinguishes_exchange_and_provider_sources(tmp_path, monkeypatch) -> None:
    root = tmp_path / "reference"
    runner = CliRunner()
    monkeypatch.setattr(
        "kairospy.surface.cli.commands.reference.exchange",
        lambda exchange_name, driver_name: _FakeReferenceClient(
            {
                "venue": "binance",
                "market": "spot",
                "source_symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
            }
        ),
    )
    monkeypatch.setattr(
        "kairospy.surface.cli.commands.reference.provider",
        lambda provider_name, driver_name: _FakeReferenceClient(
            {
                "venue": "nasdaq",
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "primary_exchange": "NASDAQ",
                "currency": "USD",
            }
        ),
    )

    exchange_sync = runner.invoke(
        reference_app,
        ["sync", "binance", "--root", str(root), "--as-of", "2026-01-01T00:00:00+00:00", "--format", "json"],
        catch_exceptions=False,
    )
    provider_sync = runner.invoke(
        reference_app,
        ["sync", "massive", "--root", str(root), "--as-of", "2026-01-01T00:00:00+00:00", "--format", "json"],
        catch_exceptions=False,
    )

    exchange_payload = json.loads(exchange_sync.output)
    provider_payload = json.loads(provider_sync.output)
    assert exchange_payload["source_kind"] == "exchange"
    assert exchange_payload["source"] == "binance"
    assert exchange_payload["provider"] == "binance"
    assert provider_payload["source_kind"] == "provider"
    assert provider_payload["source"] == "massive"
    assert provider_payload["provider"] == "massive"


def test_reference_entity_lists_default_to_text_and_honor_cli_format(tmp_path, monkeypatch) -> None:
    text = CliRunner().invoke(reference_app, ["participants", "exchanges"], catch_exceptions=False)

    assert text.exit_code == 0
    assert "exchange_id" in text.output.splitlines()[0]
    assert "hyperliquid" in text.output

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text("[cli]\nformat = \"json\"\n", encoding="utf-8")

    configured = CliRunner().invoke(reference_app, ["participants", "exchanges"], catch_exceptions=False)

    assert configured.exit_code == 0
    rows = [json.loads(line) for line in configured.output.splitlines()]
    assert any(row["exchange_id"] == "hyperliquid" for row in rows)


def test_reference_root_surface_exposes_domain_contexts_only() -> None:
    help_result = CliRunner().invoke(reference_app, ["--help"], catch_exceptions=False)

    assert help_result.exit_code == 0
    assert "participants" in help_result.output
    assert "assets" in help_result.output
    assert "markets" in help_result.output
    assert "events" in help_result.output
    assert "lifecycle" not in help_result.output
    markets_help = CliRunner().invoke(reference_app, ["markets", "--help"], catch_exceptions=False)
    assert markets_help.exit_code == 0
    assert "stream" not in markets_help.output
    assert "refresh-binance" not in help_result.output
    assert "refresh-massive-equities" not in help_result.output
    assert "sync-massive-actions" not in help_result.output
    assert " exchanges " not in help_result.output


def test_reference_events_list_and_sync_replace_lifecycle_surface(tmp_path, monkeypatch) -> None:
    root = tmp_path / "reference"
    as_of = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ReferenceStore(root).append_events((LifecycleEvent(LifecycleEventType.LISTED, as_of, venue="binance", source_symbol="BTC/USDT"),))
    runner = CliRunner()

    listed = runner.invoke(reference_app, ["events", "--root", str(root), "--format", "json"], catch_exceptions=False)
    legacy = runner.invoke(reference_app, ["lifecycle", "events", "--root", str(root)], catch_exceptions=False)
    monkeypatch.setattr("kairospy.surface.cli.commands.reference.provider", lambda provider_name, driver_name: _FakeEventProvider())
    synced = runner.invoke(
        reference_app,
        [
            "events",
            "sync",
            "--ticker",
            "AAPL",
            "--start",
            "2026-01-01T00:00:00+00:00",
            "--end",
            "2026-01-02T00:00:00+00:00",
            "--venue",
            "nasdaq",
            "--root",
            str(root),
            "--format",
            "json",
        ],
        catch_exceptions=False,
    )

    assert listed.exit_code == 0
    assert json.loads(listed.output)["event_type"] == "listed"
    assert legacy.exit_code != 0
    assert "No such command" in legacy.output
    sync_payload = json.loads(synced.output)
    assert sync_payload["provider"] == "massive"
    assert sync_payload["events"] == 1


def test_reference_assets_can_be_added_listed_and_queried(tmp_path) -> None:
    root = tmp_path / "reference"
    as_of = "2026-01-01T00:00:00+00:00"
    runner = CliRunner()

    added = runner.invoke(
        reference_app,
        [
            "assets",
            "add",
            "--symbol",
            "BTC",
            "--type",
            "crypto",
            "--name",
            "Bitcoin",
            "--root",
            str(root),
            "--effective-from",
            as_of,
            "--format",
            "json",
        ],
        catch_exceptions=False,
    )
    listed = runner.invoke(
        reference_app,
        ["assets", "list", "--type", "crypto", "--root", str(root), "--as-of", as_of, "--format", "json"],
        catch_exceptions=False,
    )
    shown = runner.invoke(
        reference_app,
        ["assets", "show", "asset:crypto:btc", "--root", str(root), "--as-of", as_of, "--format", "json"],
        catch_exceptions=False,
    )
    searched = runner.invoke(
        reference_app,
        ["search", "BTC", "--root", str(root), "--as-of", as_of, "--format", "json"],
        catch_exceptions=False,
    )
    queried = runner.invoke(
        reference_app,
        ["query", "BTC", "--kind", "asset", "--root", str(root), "--as-of", as_of, "--format", "json"],
        catch_exceptions=False,
    )
    status = runner.invoke(
        reference_app,
        ["status", "--root", str(root), "--as-of", as_of, "--format", "json"],
        catch_exceptions=False,
    )

    assert added.exit_code == 0
    assert json.loads(added.output) == {
        "asset_id": "asset:crypto:btc",
        "asset_type": "crypto",
        "symbol": "BTC",
        "name": "Bitcoin",
        "issuer_id": None,
        "effective_from": as_of,
        "effective_to": None,
        "metadata": {},
    }
    assert [json.loads(line)["asset_id"] for line in listed.output.splitlines()] == ["asset:crypto:btc"]
    assert json.loads(shown.output)["symbol"] == "BTC"
    assert any(json.loads(line)["kind"] == "asset" for line in searched.output.splitlines())
    assert [json.loads(line)["asset_id"] for line in queried.output.splitlines()] == ["asset:crypto:btc"]
    assert json.loads(status.output)["assets"] == 1


def test_reference_assets_honor_workspace_text_format(tmp_path, monkeypatch) -> None:
    root = tmp_path / "reference"
    as_of = "2026-01-01T00:00:00+00:00"
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".kairos").mkdir()
    (tmp_path / ".kairos" / "kairos.toml").write_text('[cli]\nformat = "text"\n', encoding="utf-8")

    added = runner.invoke(
        reference_app,
        [
            "assets",
            "add",
            "--symbol",
            "BTC",
            "--type",
            "crypto",
            "--root",
            str(root),
            "--effective-from",
            as_of,
        ],
        catch_exceptions=False,
    )
    listed = runner.invoke(
        reference_app,
        ["assets", "list", "--root", str(root), "--as-of", as_of],
        catch_exceptions=False,
    )
    status = runner.invoke(
        reference_app,
        ["status", "--root", str(root)],
        catch_exceptions=False,
    )
    searched = runner.invoke(
        reference_app,
        ["search", "BTC", "--root", str(root), "--as-of", as_of],
        catch_exceptions=False,
    )

    assert added.exit_code == 0
    assert added.output.splitlines()[0].startswith("asset_id")
    assert listed.exit_code == 0
    assert listed.output.splitlines()[0].startswith("asset_id")
    assert "asset:crypto:btc" in listed.output
    assert status.exit_code == 0
    assert status.output.splitlines()[0].startswith("root")
    assert searched.exit_code == 0
    assert searched.output.splitlines()[0].startswith("kind")

    json_output = runner.invoke(
        reference_app,
        ["assets", "list", "--root", str(root), "--format", "json"],
        catch_exceptions=False,
    )
    assert json_output.exit_code == 0
    assert json.loads(json_output.output)["asset_id"] == "asset:crypto:btc"


def test_reference_asset_list_supports_jmespath_and_pagination(tmp_path) -> None:
    root = tmp_path / "reference"
    runner = CliRunner()
    for symbol, asset_type in (("BTC", "crypto"), ("ETH", "crypto"), ("AAPL", "equity")):
        result = runner.invoke(
            reference_app,
            [
                "assets", "add", "--symbol", symbol, "--type", asset_type,
                "--root", str(root), "--effective-from", "2026-01-01T00:00:00+00:00",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    listed = runner.invoke(
        reference_app,
        [
            "assets", "list", "--root", str(root), "--page", "2", "--page-size", "1",
            "--query", "[?asset_type == 'crypto']", "--format", "json",
        ],
        catch_exceptions=False,
    )

    assert listed.exit_code == 0
    payload = json.loads(listed.output)
    assert payload["page"] == {"page": 2, "page_size": 1, "total_rows": 2, "total_pages": 2}
    assert payload["rows"][0]["symbol"] == "ETH"


def test_reference_asset_list_page_two_is_not_capped_by_default_limit(tmp_path) -> None:
    root = tmp_path / "reference"
    runner = CliRunner()
    for index in range(3):
        result = runner.invoke(
            reference_app,
            [
                "assets", "add", "--symbol", f"COIN{index}", "--type", "crypto",
                "--root", str(root), "--effective-from", "2026-01-01T00:00:00+00:00",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    listed = runner.invoke(
        reference_app,
        ["assets", "list", "--root", str(root), "--page", "2", "--page-size", "2", "--format", "json"],
        catch_exceptions=False,
    )

    assert listed.exit_code == 0
    payload = json.loads(listed.output)
    assert payload["page"]["total_rows"] == 3
    assert len(payload["rows"]) == 1
