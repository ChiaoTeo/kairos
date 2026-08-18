from __future__ import annotations

from datetime import datetime
from typing import Mapping

from .models import NotificationReceipt, NotificationRequest, NotificationSeverity
from .services.delivery import NotificationDeliveryRuntime


class NotificationApplication:
    """Public provider-neutral notification facade exposed to strategies."""

    def __init__(self, runtime: NotificationDeliveryRuntime) -> None:
        self._runtime = runtime
        self._event_time: datetime | None = None

    @classmethod
    def disabled(
        cls, *, strategy_id: str = "", launch_id: str = "", instance_id: str = ""
    ) -> "NotificationApplication":
        return cls(
            NotificationDeliveryRuntime(
                identity={
                    "strategy_id": strategy_id,
                    "launch_id": launch_id,
                    "instance_id": instance_id,
                },
                routes={},
                default_routes=(),
                destinations={},
                senders={},
                enabled=False,
            )
        )

    def publish(
        self,
        request: NotificationRequest | None = None,
        *,
        title: str = "",
        body: str = "",
        routes: tuple[str, ...] = (),
        severity: NotificationSeverity = "info",
        dedupe_key: str | None = None,
        occurred_at: datetime | None = None,
        attributes: Mapping[str, str] | None = None,
    ) -> NotificationReceipt:
        if request is not None and any(
            (
                title,
                body,
                routes,
                dedupe_key,
                occurred_at,
                attributes,
                severity != "info",
            )
        ):
            raise ValueError("pass a NotificationRequest or keyword fields, not both")
        if request is None:
            try:
                request = NotificationRequest(
                    title=title,
                    body=body,
                    routes=routes,
                    severity=severity,
                    dedupe_key=dedupe_key,
                    occurred_at=occurred_at or self._event_time,
                    attributes=attributes or {},
                )
            except (TypeError, ValueError) as error:
                return self._runtime.reject_invalid(routes, str(error))
        return self._runtime.submit(request)

    def health(self) -> dict[str, object]:
        return self._runtime.health()

    def deliveries(
        self, notification_ids: tuple[str, ...]
    ) -> tuple[dict[str, object], ...]:
        return self._runtime.delivery_records(notification_ids)

    def bind_event(self, occurred_at: datetime | None) -> None:
        if occurred_at is not None:
            self._event_time = occurred_at


__all__ = ["NotificationApplication"]
