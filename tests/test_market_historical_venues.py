"""Cross-process Quote evidence acceptance using local, credential-free fixtures."""

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import subprocess
from threading import Thread

import pytest


@pytest.fixture(scope="module")
def market_cli() -> Path:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["cargo", "build", "--locked", "-j", "1", "-p", "kairos-market", "--bin", "kairos-market-cli"],
        cwd=root,
        check=True,
    )
    return root / "target/debug/kairos-market-cli"


@pytest.fixture
def quote_endpoint() -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not self.path.startswith("/v3/quotes/AAPL?"):
                self.send_error(404)
                return
            body = json.dumps({"results": [{
                "bid_price": 100, "ask_price": 101,
                "bid_exchange": 19, "ask_exchange": 11,
                "sip_timestamp": 1_500_000, "tape": 3,
            }]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def reference_fixture(workspace: Path, reporting_ask: bool) -> None:
    """Minimal Reference-owned read schema needed by venue resolution, not a fake reader."""
    database = workspace / "state/reference/reference.sqlite"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE reference_meta(id INTEGER PRIMARY KEY, schema_version INTEGER,
                generation INTEGER, event_sequence INTEGER, committed_at_unix_nanos INTEGER);
            INSERT INTO reference_meta VALUES(1, 10, 3, 7, 123);
            CREATE TABLE reference_venues_current(venue_id TEXT PRIMARY KEY, status TEXT, payload TEXT);
            CREATE TABLE reference_venue_identifier_mappings_current(provider TEXT,
                provider_product TEXT, identifier_kind TEXT, identifier TEXT,
                venue_id TEXT, status TEXT, payload TEXT);
        """)
        for code, side in [("19", "bid"), ("11", "ask")]:
            reporting = reporting_ask and side == "ask"
            venue_id = f"venue:{side}"
            venue = {
                "venue_id": venue_id, "name": side,
                "venue_kind": "trade_reporting_facility" if reporting else "trading_platform",
                "roles": ["reporting" if reporting else "execution"],
                "mic": None, "operating_mic": None, "parent_venue_id": None,
                "jurisdiction": None, "status": "active",
            }
            mapping = {
                "source_id": "fixture", "provider": "massive", "provider_product": "equity",
                "identifier_kind": "exchange", "identifier": code,
                "venue_id": venue_id, "status": "active",
            }
            connection.execute("INSERT INTO reference_venues_current VALUES(?, ?, ?)",
                               (venue_id, "active", json.dumps(venue)))
            connection.execute("INSERT INTO reference_venue_identifier_mappings_current VALUES(?, ?, ?, ?, ?, ?, ?)",
                               ("massive", "equity", "exchange", code, venue_id, "active", json.dumps(mapping)))


@pytest.mark.rust_interop
@pytest.mark.parametrize("catalog", ["missing", "mapped", "reporting"])
@pytest.mark.parametrize("configured_connection", [False, True])
def test_historical_quote_download_resolves_only_execution_venues(
    tmp_path: Path, market_cli: Path, quote_endpoint: str, catalog: str,
    configured_connection: bool,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workspace.toml").write_text('version = 1\nworkspace_id = "test"\n', encoding="utf-8")
    if catalog != "missing":
        reference_fixture(workspace, reporting_ask=catalog == "reporting")
    connection_arguments = [
        "--api-key", "test-only-not-a-credential", "--endpoint", quote_endpoint,
    ]
    if configured_connection:
        profiles = workspace / "config/integration/provider-connections"
        profiles.mkdir(parents=True)
        (profiles / "private.toml").write_text(
            'version = 2\n[connection]\nconnection_id = "private"\n'
            'provider = "massive"\nenvironment = "private"\n'
            'endpoint = "https://private-fixture.invalid"\n'
            'credential_id = "fixture-custom"\nproducts = ["equity"]\n'
            'purposes = ["market-query"]\n', encoding="utf-8",
        )
        credentials = workspace / "config/credentials"
        credentials.mkdir(parents=True)
        (credentials / "fixture-custom.toml").write_text(
            '[credential]\nid = "fixture-custom"\nprovider = "massive"\n'
            'role = "readonly"\n[credential.values]\n'
            'api_key = "test-only-not-a-credential"\n', encoding="utf-8",
        )
        # Keep the configured HTTPS invariant. Only the network endpoint is
        # redirected to this credential-free HTTP fixture; key/credential
        # selection must still come from the private profile, with no legacy
        # Market source. Configured endpoint selection has a separate Rust test.
        connection_arguments = ["--endpoint", quote_endpoint]
    output = tmp_path / "quotes.jsonl"
    subprocess.run([
        str(market_cli), "--workspace", str(workspace), "standalone", "download",
        "--provider", "massive", *connection_arguments,
        "--symbol", "AAPL", "--market-type", "equity",
        "--data-kind", "quote", "--instrument-id", "instrument:fixture",
        "--start", "1", "--end", "2", "--file", str(output),
    ], check=True, capture_output=True, text=True, timeout=30)
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload: object = json.loads(lines[0])
    assert isinstance(payload, dict)
    quote = payload["Quote"]
    assert isinstance(quote, dict)
    assert quote["bid_venue_code"] == "19"
    assert quote["ask_venue_code"] == "11"
    assert quote["bid_venue_id"] == (None if catalog == "missing" else "venue:bid")
    assert quote["ask_venue_id"] == ("venue:ask" if catalog == "mapped" else None)
    assert quote["tape"] == 3
    if catalog == "missing":
        assert not (workspace / "state/reference/reference.sqlite").exists()
