from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from ..domain import NotificationLevel, NotificationRequest
from ..protocol import NotificationSender
from ..services.deduplication import NotificationDeduplication
from ..services.retry import deliver_with_retry
from ..services.routing import enabled_for_channel
from ..services.resilience import ChannelResilience

_LOGGER = logging.getLogger("kairospy.notifications")


@dataclass(frozen=True, slots=True)
class NotificationResult:
    request: NotificationRequest
    delivered_channels: tuple[str, ...]
    failed_channels: tuple[str, ...]
    deduplicated: bool = False

    @property
    def delivered(self) -> bool:
        return bool(self.delivered_channels) and not self.failed_channels


class NotificationApplication:
    """Route business notifications to injected channel senders.

    This service is deliberately independent from the runtime queue. Callers
    that must not block a business event should enqueue through NotificationActor;
    this application owns only routing, retries, and deduplication.
    """

    def __init__(
        self,
        senders: Iterable[NotificationSender] = (),
        *,
        enabled_categories: Mapping[str, Iterable[str]] | None = None,
        max_attempts: int = 3,
        minimum_level: NotificationLevel = NotificationLevel.INFO,
        rate_limit_per_minute: int | None = None,
        circuit_breaker_failures: int = 5,
        circuit_recovery_seconds: float = 30.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("notification max_attempts must be positive")
        self._senders = tuple(senders)
        self._enabled_categories = {
            str(channel): frozenset(str(category) for category in categories)
            for channel, categories in (enabled_categories or {}).items()
        }
        self._max_attempts = max_attempts
        self._minimum_level = minimum_level
        self._resilience = {
            sender.channel: ChannelResilience(
                rate_limit_per_minute=rate_limit_per_minute,
                failure_threshold=circuit_breaker_failures,
                recovery_seconds=circuit_recovery_seconds,
            )
            for sender in self._senders
        }
        self._channel_health: dict[str, dict[str, object]] = {
            sender.channel: {"status": "ready", "last_error": None, "last_success_at": None}
            for sender in self._senders
        }
        self._deduplication = NotificationDeduplication()

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(sender.channel for sender in self._senders)

    @property
    def processed_keys(self) -> frozenset[str]:
        return self._deduplication.keys

    @property
    def channel_health(self) -> Mapping[str, Mapping[str, object]]:
        return {channel: dict(value) for channel, value in self._channel_health.items()}

    async def send(self, request: NotificationRequest) -> NotificationResult:
        key = request.deduplication_key
        if _level_rank(request.level) < _level_rank(self._minimum_level):
            return NotificationResult(request, (), ())
        if key is not None and self._deduplication.contains(key):
            return NotificationResult(request, (), (), deduplicated=True)

        delivered: list[str] = []
        failed: list[str] = []
        for sender in self._senders:
            if not enabled_for_channel(sender.channel, request, self._enabled_categories):
                continue
            guard = self._resilience[sender.channel]
            if not guard.allow():
                failed.append(sender.channel)
                self._channel_health[sender.channel] = {"status": guard.state, "last_error": "channel_guard", "last_success_at": self._channel_health[sender.channel].get("last_success_at")}
                continue
            try:
                await deliver_with_retry(sender, request, max_attempts=self._max_attempts)
            except Exception as error:
                failed.append(sender.channel)
                guard.failure()
                self._channel_health[sender.channel] = {
                    "status": guard.state,
                    "last_error": type(error).__name__,
                    "last_success_at": self._channel_health.get(sender.channel, {}).get("last_success_at"),
                }
                _LOGGER.warning(
                    "notification channel=%s category=%s state=failed error_type=%s",
                    sender.channel,
                    request.category.value,
                    type(error).__name__,
                )
            else:
                delivered.append(sender.channel)
                guard.success()
                self._channel_health[sender.channel] = {
                    "status": guard.state,
                    "last_error": None,
                    "last_success_at": datetime.now(timezone.utc),
                }

        if key is not None and delivered and not failed:
            self._deduplication.record(key)
        return NotificationResult(request, tuple(delivered), tuple(failed))

    async def close(self) -> None:
        for sender in self._senders:
            transport = getattr(sender, "transport", None)
            close = getattr(transport, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result


def notification_now() -> datetime:
    return datetime.now(timezone.utc)


def _level_rank(level: NotificationLevel) -> int:
    return {NotificationLevel.INFO: 0, NotificationLevel.WARNING: 1, NotificationLevel.ERROR: 2}[level]




__all__ = ["NotificationApplication", "NotificationResult", "notification_now"]
