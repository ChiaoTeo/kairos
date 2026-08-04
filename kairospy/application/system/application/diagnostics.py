from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from kairospy.application.support.system.domain.config import find_manifest_path


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api_secret",
    "secret",
    "passphrase",
    "password",
    "private_key",
    "wallet_private_key",
    "token",
    "access_token",
    "refresh_token",
}


def record_exception(
    error: BaseException,
    *,
    operation: str,
    command: str | None = None,
    context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    diagnostic_id = f"diag-{uuid4().hex[:12]}"
    path = diagnostic_log_path()
    record = {
        "id": diagnostic_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "error",
        "operation": operation,
        "command": command,
        "context": redact(context or {}),
        "error_type": error.__class__.__name__,
        "error": str(error),
        "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
    }
    _append_jsonl(path, record)
    return {
        "diagnostic_id": diagnostic_id,
        "diagnostic_path": str(path),
        "error_type": error.__class__.__name__,
    }


def diagnostic_log_path(start: str | Path | None = None) -> Path:
    base = _project_root(start) / ".kairos" / "logs" / "cli"
    return base / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"


def redact(value: object) -> object:
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            text_key = str(key)
            redacted[text_key] = "<redacted>" if _is_sensitive_key(text_key) else redact(item)
        return redacted
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int, float, bool)):
        return getattr(value, "value")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _project_root(start: str | Path | None) -> Path:
    manifest = find_manifest_path(start)
    if manifest is None:
        return Path.cwd().resolve()
    root = manifest.parent
    if root.name == ".kairos":
        root = root.parent
    return root.resolve()


def _append_jsonl(path: Path, record: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_KEYS or normalized.endswith("_secret") or normalized.endswith("_token")


__all__ = ["diagnostic_log_path", "record_exception", "redact"]
