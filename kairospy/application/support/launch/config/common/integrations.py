from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeVar

ConfigErrorT = TypeVar("ConfigErrorT", bound=Exception)


@dataclass(frozen=True, slots=True)
class FeedConfig:
    feed_id: str
    credential: str | None = None
    values: Mapping[str, object] | None = None


def parse_feeds(value: object, *, error_type: type[ConfigErrorT]) -> Mapping[str, FeedConfig]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise error_type("[feeds] must be a table")
    feeds: dict[str, FeedConfig] = {}
    for raw_id, raw in value.items():
        feed_id = str(raw_id).strip()
        if not feed_id:
            raise error_type("feeds id cannot be empty")
        if raw is None:
            raw_values: Mapping[str, object] = {}
        elif isinstance(raw, Mapping):
            raw_values = raw
        else:
            raise error_type(f"feeds.{feed_id} must be a table")
        credential = _optional_text(raw_values.get("credential"), f"feeds.{feed_id}.credential", error_type)
        feeds[_feed_key(feed_id)] = FeedConfig(feed_id=feed_id, credential=credential, values=dict(raw_values))
    return feeds


def _feed_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _optional_text(value: object, source: str, error_type: type[ConfigErrorT]) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise error_type(f"{source} must be a non-empty string")
    return text


__all__ = [
    "FeedConfig",
    "parse_feeds",
]
