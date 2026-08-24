import io
import json

from hypothesis import given, strategies as st
import pytest

from kairospy.infrastructure.observability import resolve_otlp_endpoint
from kairospy.infrastructure.observability.telemetry import _trace_sample_ratio
from kairospy.strategy.logging import StrategyLogger


@given(message=st.text(min_size=1, max_size=200))
def test_strategy_logger_emits_valid_json_for_arbitrary_messages(message: str) -> None:
    stream = io.StringIO()
    StrategyLogger(stream=stream).info(message)

    record = json.loads(stream.getvalue())
    assert record["level"] == "info"
    assert record["message"] == message


def test_strategy_logger_redacts_secret_fields() -> None:
    stream = io.StringIO()
    StrategyLogger(stream=stream).info(
        "credential loaded",
        api_key="key-value",
        nested={"api_secret": "secret-value"},
        authorization="Bearer access-value",
    )
    output = stream.getvalue()
    assert "key-value" not in output
    assert "secret-value" not in output
    assert "access-value" not in output
    assert output.count("[REDACTED]") == 3


def test_strategy_logger_redacts_secrets_embedded_in_exception_text() -> None:
    stream = io.StringIO()
    StrategyLogger(stream=stream).error(
        "provider failed: Authorization: Bearer access-value",
        cause="upstream rejected api_secret=secret-value",
    )

    output = stream.getvalue()
    assert "access-value" not in output
    assert "secret-value" not in output
    assert output.count("[REDACTED]") == 2


def test_otlp_signal_endpoint_takes_precedence() -> None:
    assert (
        resolve_otlp_endpoint(
            "http://collector:4318/custom/traces",
            "http://collector:4318",
            True,
            "/v1/traces",
        )
        == "http://collector:4318/custom/traces"
    )


def test_otlp_generic_endpoint_is_a_base_url() -> None:
    assert (
        resolve_otlp_endpoint(None, "http://collector:4318/", False, "/v1/metrics")
        == "http://collector:4318/v1/metrics"
    )


def test_trace_sample_ratio_defaults_for_invalid_or_out_of_range_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for value in ("invalid", "-0.1", "1.1"):
        monkeypatch.setenv("KAIROS_OTEL_TRACE_SAMPLE_RATIO", value)
        assert _trace_sample_ratio() == 0.1
    monkeypatch.setenv("KAIROS_OTEL_TRACE_SAMPLE_RATIO", "0.25")
    assert _trace_sample_ratio() == 0.25
