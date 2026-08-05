from __future__ import annotations

from kairospy.application.support.diagnostics.application import redact


def test_redact_removes_api_keys_from_exception_text() -> None:
    value = "ProxyError url=https://example.test/data?apiKey=secret-value&limit=1"

    result = redact(value)

    assert "secret-value" not in result
    assert "apiKey=<redacted>" in result
