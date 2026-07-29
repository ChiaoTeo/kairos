from __future__ import annotations

from datetime import datetime, timezone
import json

from typer.testing import CliRunner

from kairospy.application.service.domain.reference import ReferenceStore, catalog_from_market_rows
from kairospy.surface.cli.commands.reference import reference_app


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
        ["catalog", "search", "BTC", "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    show = CliRunner().invoke(
        reference_app,
        ["catalog", "show", market_id, "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    resolve = CliRunner().invoke(
        reference_app,
        ["markets", "resolve", "BTC/USDT", "--venue", "binance", "--market", "spot", "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    status = CliRunner().invoke(
        reference_app,
        ["catalog", "status", "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    view_status = CliRunner().invoke(
        reference_app,
        ["catalog", "view", "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    view_market = CliRunner().invoke(
        reference_app,
        ["catalog", "view", market_id, "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    query = CliRunner().invoke(
        reference_app,
        [
            "catalog",
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
    assert "lifecycle" in help_result.output
    assert "catalog" in help_result.output
    assert "refresh-binance" not in help_result.output
    assert "refresh-massive-equities" not in help_result.output
    assert "sync-massive-actions" not in help_result.output
    assert " exchanges " not in help_result.output


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
        ],
        catch_exceptions=False,
    )
    listed = runner.invoke(
        reference_app,
        ["assets", "list", "--type", "crypto", "--root", str(root), "--as-of", as_of],
        catch_exceptions=False,
    )
    shown = runner.invoke(
        reference_app,
        ["assets", "show", "asset:crypto:btc", "--root", str(root), "--as-of", as_of],
        catch_exceptions=False,
    )
    searched = runner.invoke(
        reference_app,
        ["catalog", "search", "BTC", "--root", str(root), "--as-of", as_of],
        catch_exceptions=False,
    )
    queried = runner.invoke(
        reference_app,
        ["catalog", "query", "BTC", "--kind", "asset", "--root", str(root), "--as-of", as_of],
        catch_exceptions=False,
    )
    status = runner.invoke(
        reference_app,
        ["catalog", "status", "--root", str(root), "--as-of", as_of],
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
