from __future__ import annotations

import json
import unittest

from kairospy.connectors.massive import MassiveClient, MassiveConfig, MassiveResponse
from kairospy.connectors.massive.entitlement_diagnostics import MassiveEntitlementDiagnostics


class StubTransport:
    def __init__(self): self.urls = []
    def request(self, url, headers, timeout):
        self.urls.append(url)
        return MassiveResponse(200, {}, json.dumps({"status": "OK", "request_id": str(len(self.urls)), "results": []}).encode())


class IndexAggregateForbiddenTransport(StubTransport):
    def request(self, url, headers, timeout):
        self.urls.append(url)
        if "/v2/aggs/ticker/I%3ASPX/" in url or "/v2/aggs/ticker/I%253ASPX/" in url or "/v2/aggs/ticker/I:SPX/" in url:
            return MassiveResponse(403, {}, json.dumps({"status": "NOT_AUTHORIZED"}).encode())
        return MassiveResponse(200, {}, json.dumps({"status": "OK", "request_id": str(len(self.urls)), "results": []}).encode())


class IndexAndPairForbiddenTransport(IndexAggregateForbiddenTransport):
    def request(self, url, headers, timeout):
        if "O:SPXW251103P06000000" in url:
            self.urls.append(url)
            return MassiveResponse(403, {}, json.dumps({"status": "NOT_AUTHORIZED"}).encode())
        return super().request(url, headers, timeout)


class MassiveEntitlementDiagnosticsTests(unittest.TestCase):
    def test_all_probes_use_private_host_and_do_not_put_key_in_url(self):
        transport = StubTransport()
        report = MassiveEntitlementDiagnostics(MassiveClient(MassiveConfig("secret"), transport)).check(
            underlying="SPX", option_ticker="O:SPXW260717P06000000", date="2026-07-15")
        self.assertTrue(report.ready)
        self.assertEqual(report.api_host, "https://api.massiveprivateserver.site")
        self.assertEqual(len(transport.urls), 8)
        self.assertTrue(all(url.startswith("https://api.massiveprivateserver.site/") for url in transport.urls))
        self.assertNotIn("secret", "".join(transport.urls))

    def test_spx_is_study_ready_with_synthetic_forward_when_index_aggregates_are_forbidden(self):
        transport = IndexAggregateForbiddenTransport()
        report = MassiveEntitlementDiagnostics(MassiveClient(MassiveConfig("secret"), transport)).check(
            underlying="SPX", option_ticker="O:SPXW251103C06000000", date="2025-11-03")
        self.assertTrue(report.ready)
        self.assertFalse(report.official_underlying_history)
        self.assertEqual(report.valuation_reference_mode, "put_call_parity_synthetic_forward")
        self.assertFalse(report.checks["underlying_aggregates"]["accessible"])
        self.assertTrue(report.checks["paired_option_quotes"]["accessible"])

    def test_synthetic_forward_readiness_requires_the_opposite_call_put_quote(self):
        report = MassiveEntitlementDiagnostics(MassiveClient(MassiveConfig("secret"), IndexAndPairForbiddenTransport())).check(
            underlying="SPX", option_ticker="O:SPXW251103C06000000", date="2025-11-03")
        self.assertFalse(report.ready)
        self.assertEqual(report.valuation_reference_mode, "unavailable")
        self.assertFalse(report.checks["paired_option_quotes"]["accessible"])


if __name__ == "__main__":
    unittest.main()
