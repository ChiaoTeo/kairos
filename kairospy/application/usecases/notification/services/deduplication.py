from __future__ import annotations


class NotificationDeduplication:
    """Process-local deduplication state owned by the notification use case."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def contains(self, key: str) -> bool:
        return key in self._keys

    def record(self, key: str) -> None:
        self._keys.add(key)

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(self._keys)


__all__ = ["NotificationDeduplication"]
