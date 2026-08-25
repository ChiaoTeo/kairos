"""Pure text and structured-value credential redaction rules."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "authorization",
        "bot_token",
        "password",
        "passphrase",
        "private_key",
        "secret",
        "token",
        "access_token",
        "webhook_url",
    }
)
_SENSITIVE_LINE = re.compile(
    r"(?i)(?:--)?"
    r"(api[_ -]?key|authorization|bearer|credential|password|secret|token)"
    r"(?:[ \t]*[:=][ \t]*|[ \t]+)([^\s,;]+)"
)
_AUTHORIZATION_LINE = re.compile(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+")
_FEISHU_WEBHOOK = re.compile(
    r"(?i)(https://open\.feishu\.cn/open-apis/bot/v2/hook/)[^\s/?#]+"
)
_TELEGRAM_BOT_URL = re.compile(r"(?i)(https://api\.telegram\.org/bot)[^\s/]+")
_TELEGRAM_BOT_TOKEN = re.compile(r"(?<![\w-])\d{6,12}:[A-Za-z0-9_-]{20,}(?![\w-])")


def redact_text(value: str) -> str:
    """Remove common credential assignments and credential-bearing URLs."""

    value = _AUTHORIZATION_LINE.sub(r"\1<redacted>", value)
    value = _SENSITIVE_LINE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    value = _FEISHU_WEBHOOK.sub(r"\1<redacted>", value)
    value = _TELEGRAM_BOT_URL.sub(r"\1<redacted>", value)
    return _TELEGRAM_BOT_TOKEN.sub("<redacted>", value)


def redact_value(value: Any, *, placeholder: str = "<redacted>") -> Any:
    """Recursively redact credential fields and strings without owner knowledge."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): (
                placeholder
                if _is_sensitive_key(str(key))
                else redact_value(item, placeholder=placeholder)
            )
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_value(item, placeholder=placeholder) for item in value)
    if isinstance(value, list):
        return [redact_value(item, placeholder=placeholder) for item in value]
    return value


def redact_cli_arguments(arguments: tuple[str, ...]) -> tuple[str, ...]:
    """Redact values paired with credential-shaped CLI flags."""

    sensitive_names = {
        "api-key",
        "apikey",
        "authorization",
        "bearer",
        "credential",
        "password",
        "secret",
        "token",
    }
    redacted: list[str] = []
    hide_next = False
    for argument in arguments:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        normalized = argument.lstrip("-").lower().replace("_", "-")
        name = normalized.partition("=")[0]
        if name in sensitive_names:
            if "=" in argument:
                redacted.append(f"{argument.partition('=')[0]}=<redacted>")
            else:
                redacted.append(argument)
                hide_next = True
            continue
        redacted.append(redact_text(argument))
    return tuple(redacted)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in _SENSITIVE_KEYS or normalized.startswith("secret_")


__all__ = ["redact_cli_arguments", "redact_text", "redact_value"]
