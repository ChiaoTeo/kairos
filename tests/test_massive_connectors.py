from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kairospy.integrations.connectors.massive import MassiveClient, MassiveConfig, MassiveFlatFileBatchDownloader, MassiveFlatFileClient, MassiveResponse, MassiveVendorArchiveClient, OutsideDownloadWindow
from kairospy.integrations.connectors.massive.client import MassiveError


class StubTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.headers = []

    def request(self, url, headers, timeout):
        self.urls.append(url); self.headers.append(headers)
        return self.responses.pop(0)


def response(value, status=200):
    return MassiveResponse(status, {}, json.dumps(value).encode())


class MassiveConnectorTests(unittest.TestCase):
    def test_config_accepts_legacy_massive_api_key_env(self):
        old = os.environ.copy()
        try:
            os.environ.pop("KAIROS_MASSIVE_MARKETDATA_PRIMARY_API_KEY", None)
            os.environ["MASSIVE_API_KEY"] = "legacy-secret"
            self.assertEqual(MassiveConfig.from_env().api_key, "legacy-secret")
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_config_rejects_non_private_hosts(self):
        with self.assertRaises(ValueError):
            MassiveConfig("secret", rest_base="https://api.massive.com")
        with self.assertRaises(ValueError):
            MassiveConfig("secret", rest_base="http://api.massiveprivateserver.site")
        with self.assertRaises(ValueError):
            MassiveConfig("secret", socket_base="ws://socket.massiveprivateserver.site")

    def test_private_http_next_url_is_upgraded_to_https(self):
        transport = StubTransport([response({"request_id": "one", "next_url": "http://api.massiveprivateserver.site/v3/next"}), response({"request_id": "two"})])
        list(MassiveClient(MassiveConfig("secret"), transport).pages("/v3/start"))
        self.assertEqual(transport.urls[1], "https://api.massiveprivateserver.site/v3/next")

    def test_client_authenticates_by_header_and_follows_private_next_url(self):
        transport = StubTransport([
            response({"request_id": "one", "results": [{"x": 1}], "next_url": "https://api.massiveprivateserver.site/v3/next?cursor=a"}),
            response({"request_id": "two", "results": [{"x": 2}]}),
        ])
        client = MassiveClient(MassiveConfig("secret"), transport)
        pages = list(client.pages("/v3/reference/options/contracts", {"underlying_ticker": "SPX"}))
        self.assertEqual(len(pages), 2)
        self.assertNotIn("secret", "".join(transport.urls))
        self.assertEqual(transport.headers[0]["Authorization"], "Bearer secret")

    def test_client_rewrites_known_upstream_next_url_to_private_host(self):
        transport = StubTransport([response({"request_id": "one", "next_url": "https://api.massive.com/v3/next?cursor=a&apiKey=leaked"}), response({"request_id": "two"})])
        client = MassiveClient(MassiveConfig("secret"), transport)
        list(client.pages("/v3/start"))
        self.assertEqual(transport.urls[1], "https://api.massiveprivateserver.site/v3/next?cursor=a")

    def test_client_rejects_unknown_next_url_host(self):
        transport = StubTransport([response({"request_id": "one", "next_url": "https://evil.example/v3/next"})])
        client = MassiveClient(MassiveConfig("secret"), transport)
        with self.assertRaises(MassiveError):
            list(client.pages("/v3/start"))

    def test_source_archive_is_idempotent_and_does_not_persist_key(self):
        transport = StubTransport([response({"request_id": "one", "results": [{"ticker": "O:SPXW"}]})])
        client = MassiveClient(MassiveConfig("secret"), transport)
        with TemporaryDirectory() as temporary:
            archive = MassiveVendorArchiveClient(temporary, client, now=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc))
            first = archive.fetch_pages("/v3/reference/options/contracts", {"underlying_ticker": "SPX"})
            second = archive.fetch_pages("/v3/reference/options/contracts", {"underlying_ticker": "SPX"})
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertEqual(len(transport.urls), 1)
            self.assertNotIn("secret", (first.directory / "receipt.json").read_text())
            self.assertEqual(first.receipt["transport_scheme"], "https")

    def test_non_https_and_incomplete_source_cache_is_quarantined(self):
        with TemporaryDirectory() as temporary:
            request = Path(temporary) / "source" / "provider=massive" / "resource=test" / "request_id=old"
            request.mkdir(parents=True); (request / "page-00000.json.gz").write_bytes(b"old")
            moved = MassiveVendorArchiveClient.quarantine_non_https(temporary)
            self.assertEqual(len(moved), 1)
            self.assertFalse(request.exists())
            self.assertTrue((moved[0] / "page-00000.json.gz").exists())

    def test_flat_files_are_blocked_during_new_york_regular_session(self):
        client = MassiveClient(MassiveConfig("secret"), StubTransport([]))
        downloader = MassiveFlatFileClient("/tmp/unused", client, now=lambda: datetime(2026, 7, 15, 14, tzinfo=timezone.utc))
        with self.assertRaises(OutsideDownloadWindow):
            downloader.download("us_stocks_sip/day_aggs_v1/2026/07/2026-07-14.csv.gz")

    def test_flat_file_streams_outside_session_and_writes_receipt(self):
        transport = StubTransport([response({"usedGB": 0.0000001, "limitGB": 100, "remainingGB": 99.9999999})])
        client = MassiveClient(MassiveConfig("secret"), transport)
        def stream(file_key, target):
            target.write_bytes(b"payload")
            return 200, {}, 7, "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"
        with TemporaryDirectory() as temporary:
            downloader = MassiveFlatFileClient(temporary, client, now=lambda: datetime(2026, 7, 15, 22, tzinfo=timezone.utc), stream_download=stream)
            target = downloader.download("us_stocks_sip/day_aggs_v1/2026/07/2026-07-14.csv.gz")
            self.assertEqual(target.read_bytes(), b"payload")
            self.assertNotIn("secret", (target.parent / "receipt.json").read_text())
            receipt = json.loads((target.parent / "receipt.json").read_text())
            self.assertEqual(receipt["server_monthly_limit_bytes"], 100_000_000_000)
            self.assertEqual(receipt["effective_monthly_limit_bytes"], 100_000_000_000)

    def test_flat_file_batch_plans_right_open_trading_days_with_a_bound(self):
        transport = StubTransport([response({"cached": True, "downloading": False})])
        with TemporaryDirectory() as temporary:
            flat = MassiveFlatFileClient(temporary, MassiveClient(MassiveConfig("secret"), transport))
            report = MassiveFlatFileBatchDownloader(flat).download_range(
                datetime(2026, 1, 1).date(), datetime(2026, 1, 6).date(), max_files=1, dry_run=True,
            )
            self.assertEqual(report["trading_days"], 2)
            self.assertEqual(report["counts"], {"planned": 1, "deferred_by_batch_limit": 1})
            self.assertEqual(report["items"][0]["key"], "us_options_opra/day_aggs_v1/2026/01/2026-01-02.csv.gz")
            self.assertTrue(Path(report["report_path"]).exists())

    def test_flat_file_batch_resumes_without_redownloading_completed_files(self):
        key1 = "us_options_opra/day_aggs_v1/2026/01/2026-01-02.csv.gz"
        key2 = "us_options_opra/day_aggs_v1/2026/01/2026-01-05.csv.gz"
        def stream(file_key, target):
            target.write_bytes(file_key.encode())
            return 200, {}, target.stat().st_size, "hash"
        with TemporaryDirectory() as temporary:
            first_transport = StubTransport([
                response({"cached": True, "downloading": False}), response({"usedGB": 0, "limitGB": 100}),
            ])
            first_flat = MassiveFlatFileClient(
                temporary, MassiveClient(MassiveConfig("secret"), first_transport),
                now=lambda: datetime(2026, 7, 15, 22, tzinfo=timezone.utc), stream_download=stream,
            )
            first = MassiveFlatFileBatchDownloader(first_flat).download_range(
                datetime(2026, 1, 1).date(), datetime(2026, 1, 6).date(), max_files=1,
            )
            self.assertEqual(first["counts"], {"downloaded": 1, "deferred_by_batch_limit": 1})
            self.assertIsNotNone(first_flat.local_file(key1)); self.assertIsNone(first_flat.local_file(key2))

            second_transport = StubTransport([
                response({"cached": True, "downloading": False}), response({"usedGB": 0, "limitGB": 100}),
            ])
            second_flat = MassiveFlatFileClient(
                temporary, MassiveClient(MassiveConfig("secret"), second_transport),
                now=lambda: datetime(2026, 7, 15, 22, tzinfo=timezone.utc), stream_download=stream,
            )
            second = MassiveFlatFileBatchDownloader(second_flat).download_range(
                datetime(2026, 1, 1).date(), datetime(2026, 1, 6).date(), max_files=1,
            )
            self.assertEqual(second["counts"], {"already_downloaded": 1, "downloaded": 1})
            self.assertIsNotNone(second_flat.local_file(key2))
            self.assertEqual(len(second_transport.urls), 2)

    def test_flat_file_batch_reports_server_caching_without_failure(self):
        transport = StubTransport([response({"cached": False, "downloading": True})])
        with TemporaryDirectory() as temporary:
            flat = MassiveFlatFileClient(temporary, MassiveClient(MassiveConfig("secret"), transport))
            report = MassiveFlatFileBatchDownloader(flat).download_range(
                datetime(2026, 1, 2).date(), datetime(2026, 1, 3).date(), max_files=1,
            )
            self.assertEqual(report["counts"], {"caching": 1})



if __name__ == "__main__":
    unittest.main()
