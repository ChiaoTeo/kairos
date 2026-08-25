"""Regression tests for presentation-layer credential redaction."""

from kairospy.surface.presentation.redaction import redact_text


def test_redaction_does_not_treat_next_line_as_an_api_key_value() -> None:
    text = "认证  本地连接，无需 API Key\n资源名称  ollama-main"

    assert redact_text(text) == text


def test_redaction_still_hides_same_line_api_key_assignments() -> None:
    assert redact_text("API Key: sk-secret\n状态: ready") == (
        "API Key=<redacted>\n状态: ready"
    )
