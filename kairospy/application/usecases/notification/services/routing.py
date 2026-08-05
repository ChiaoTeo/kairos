from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..domain import NotificationRequest


def enabled_for_channel(channel: str, request: NotificationRequest, rules: Mapping[str, Iterable[str]]) -> bool:
    categories = rules.get(channel)
    if categories is None:
        return True
    allowed = frozenset(str(value) for value in categories)
    category = request.category.value
    return request.category.value in allowed or "*" in allowed or any(
        pattern.endswith(".*") and category.startswith(pattern[:-1]) for pattern in allowed
    )


__all__ = ["enabled_for_channel"]
