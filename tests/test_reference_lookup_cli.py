from __future__ import annotations

from datetime import datetime, timezone
import json

from typer.testing import CliRunner

from kairospy.application.service.domain.reference import ReferenceStore, catalog_from_market_rows
from kairospy.surface.products.reference import reference_app


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
        ["search", "BTC", "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    show = CliRunner().invoke(
        reference_app,
        ["show", market_id, "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    resolve = CliRunner().invoke(
        reference_app,
        ["resolve", "BTC/USDT", "--venue", "binance", "--market", "spot", "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )
    status = CliRunner().invoke(
        reference_app,
        ["catalog", "status", "--root", str(root), "--as-of", as_of.isoformat()],
        catch_exceptions=False,
    )

    assert search.exit_code == 0
    assert any(json.loads(line)["kind"] == "market" for line in search.output.splitlines())
    assert json.loads(show.output)["market_id"] == market_id
    assert json.loads(resolve.output)["source_symbol"] == "BTC/USDT"
    status_payload = json.loads(status.output)
    assert status_payload["markets"] == 1
    assert status_payload["active_markets"] == 1
