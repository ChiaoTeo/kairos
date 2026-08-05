from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.actor.support.base import BusinessActor
from kairospy.application.actor.support.lifecycle import ActorLifecycleEvent, SupervisorLifecycleEvent
from kairospy.application.support.messaging import Message, MessageBus
from kairospy.application.usecases.notification import (
    NotificationApplication,
    NotificationCategory,
    NotificationLevel,
    NotificationRequest,
)

_LOGGER = logging.getLogger("kairospy.notification_actor")


class NotificationActor(BusinessActor):
    """Own the bounded notification queue and delivery worker."""

    def __init__(
        self,
        application: NotificationApplication,
        *,
        bus: MessageBus | None = None,
        queue_size: int = 256,
        summary_interval_seconds: float | None = None,
    ) -> None:
        super().__init__("notification", bus=bus)
        if queue_size < 1:
            raise ValueError("notification queue size must be positive")
        self.application = application
        self.queue: asyncio.Queue[NotificationRequest] = asyncio.Queue(maxsize=queue_size)
        self.summary_interval_seconds = summary_interval_seconds
        self._delivery_task: asyncio.Task[None] | None = None
        self._summary_task: asyncio.Task[None] | None = None
        self._views: object | None = None
        self._dropped_count = 0
        self._delivered_count = 0
        self._failed_count = 0
        self._last_error: str | None = None

    def bind_views(self, views: object) -> None:
        self._views = views

    async def on_start(self) -> None:
        self._delivery_task = asyncio.create_task(self._deliver(), name="actor:notification.delivery")
        if self.summary_interval_seconds is not None:
            self._summary_task = asyncio.create_task(self._summaries(), name="actor:notification.summary")
        await self.enqueue(
            NotificationRequest(
                NotificationCategory.SYSTEM_LIFECYCLE,
                "系统已启动",
                "通知服务已启动。",
                deduplication_key=f"system.started:{datetime.now(timezone.utc).date()}",
            )
        )

    async def on_stop(self) -> None:
        await self.enqueue(
            NotificationRequest(
                NotificationCategory.SYSTEM_LIFECYCLE,
                "系统即将停止",
                "通知服务正在关闭。",
                deduplication_key=f"system.stopping:{datetime.now(timezone.utc).isoformat()}",
            )
        )
        await self.queue.join()
        if self._summary_task is not None:
            self._summary_task.cancel()
            await asyncio.gather(self._summary_task, return_exceptions=True)
            self._summary_task = None
        if self._delivery_task is not None:
            self._delivery_task.cancel()
            await asyncio.gather(self._delivery_task, return_exceptions=True)
            self._delivery_task = None
        close = getattr(self.application, "close", None)
        if callable(close):
            await close()

    async def process(self, message: Message) -> None:
        request = self._request_for(message)
        if request is not None:
            await self.enqueue(request)

    async def enqueue(self, request: NotificationRequest) -> bool:
        try:
            self.queue.put_nowait(request)
        except asyncio.QueueFull:
            if request.level is NotificationLevel.INFO:
                self._dropped_count += 1
                _LOGGER.warning("notification queue=full category=%s action=drop_info", request.category.value)
                return False
            await self.queue.put(request)
        return True

    def runtime_metrics(self) -> dict[str, object]:
        metrics = super().runtime_metrics()
        metrics.update(
            {
                "queue_depth": self.queue.qsize(),
                "queue_capacity": self.queue.maxsize,
                "dropped_count": self._dropped_count,
                "delivered_count": self._delivered_count,
                "failed_count": self._failed_count,
                "last_error": self._last_error,
                "channels": self.application.channels,
                "channel_health": self.application.channel_health,
            }
        )
        return metrics

    async def _deliver(self) -> None:
        while True:
            request = await self.queue.get()
            try:
                result = await self.application.send(request)
                if result.failed_channels:
                    self._failed_count += len(result.failed_channels)
                self._delivered_count += len(result.delivered_channels)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failed_count += 1
                self._last_error = str(error)
                _LOGGER.exception("notification delivery state=failed error_type=%s", type(error).__name__)
            finally:
                self.queue.task_done()

    async def _summaries(self) -> None:
        assert self.summary_interval_seconds is not None
        while True:
            await asyncio.sleep(self.summary_interval_seconds)
            request = self._account_summary()
            if request is not None:
                await self.enqueue(request)

    def _account_summary(self) -> NotificationRequest | None:
        views = self._views
        if views is None or not callable(getattr(views, "envelopes", None)):
            return None
        keys = tuple(name for name in views.envelopes() if str(name).startswith("account.current."))
        if not keys:
            return None
        lines: list[str] = []
        for key in keys:
            view = views.get(key)
            if view is None:
                continue
            equity = getattr(view, "equity", None)
            selected_balance = getattr(view, "selected_balance", None)
            profit = getattr(view, "net_profit", None)
            positions = len(tuple(getattr(view, "positions", ()) or ()))
            open_orders = len(tuple(getattr(view, "open_orders", ()) or ()))
            lines.append(f"- {key}: equity={_text(equity)} selected_balance={_text(selected_balance)} profit={_text(profit)} positions={positions} open_orders={open_orders}")
        if not lines:
            return None
        return NotificationRequest(
            NotificationCategory.ACCOUNT_SNAPSHOT,
            "账户状态摘要",
            "\n".join(lines),
            deduplication_key=f"account.snapshot:{int(datetime.now(timezone.utc).timestamp() // self.summary_interval_seconds)}" if self.summary_interval_seconds else None,
        )

    @staticmethod
    def _request_for(message: Message) -> NotificationRequest | None:
        topic = message.topic
        if topic == "system.monitor.supervisor":
            return None
        if topic == "monitor.lifecycle":
            if isinstance(message.payload, ActorLifecycleEvent):
                failed = message.payload.state == "failed"
                return NotificationRequest(
                    NotificationCategory.SYSTEM_ERROR if failed else NotificationCategory.SYSTEM_LIFECYCLE,
                    "Actor 运行异常" if failed else "Actor 状态变化",
                    f"actor={message.payload.actor} state={message.payload.state} error={message.payload.error or '-'}",
                    NotificationLevel.ERROR if failed else NotificationLevel.INFO,
                    deduplication_key=f"{topic}:{message.payload.actor}:{message.payload.state}:{message.sequence}",
                    created_at=message.time,
                )
            if isinstance(message.payload, SupervisorLifecycleEvent):
                return NotificationRequest(
                    NotificationCategory.SYSTEM_LIFECYCLE,
                    "Actor Supervisor 状态变化",
                    f"state={message.payload.state} actors={','.join(message.payload.actors)}",
                    deduplication_key=f"{topic}:supervisor:{message.payload.state}:{message.sequence}",
                    created_at=message.time,
                )
            return None
        if topic == "execution.update":
            status = str(getattr(message.payload, "status", None) or _value(message.payload, "status") or "updated")
            level = NotificationLevel.WARNING if status in {"rejected", "cancelled", "canceled"} else NotificationLevel.INFO
            category = NotificationCategory.EXECUTION_FILL if status in {"filled", "partially_filled"} else NotificationCategory.EXECUTION_ORDER
            order_id = str(getattr(message.payload, "order_id", None) or _value(message.payload, "order_id") or "unknown")
            return NotificationRequest(category, f"订单状态：{status}", f"order_id={order_id}", level=level, deduplication_key=f"{topic}:{order_id}:{status}:{message.sequence}", created_at=message.time)
        if topic.startswith("system.") and topic.endswith("failed"):
            return NotificationRequest(NotificationCategory.SYSTEM_ERROR, "系统运行异常", _payload_text(message.payload), NotificationLevel.ERROR, deduplication_key=f"{topic}:{message.sequence}", created_at=message.time)
        if topic.startswith("connection."):
            return NotificationRequest(NotificationCategory.CONNECTION_HEALTH, "连接状态变化", _payload_text(message.payload), NotificationLevel.WARNING, deduplication_key=f"{topic}:{message.sequence}", created_at=message.time)
        if topic.startswith("risk."):
            return NotificationRequest(NotificationCategory.RISK_ALERT, "风险状态变化", _payload_text(message.payload), NotificationLevel.WARNING, deduplication_key=f"{topic}:{message.sequence}", created_at=message.time)
        return None


def _value(payload: object, name: str) -> object | None:
    return payload.get(name) if isinstance(payload, dict) else None


def _payload_text(payload: object) -> str:
    if isinstance(payload, dict):
        return ", ".join(f"{key}={value}" for key, value in payload.items())
    return str(payload)


def _text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return "-" if value is None else str(value)


__all__ = ["NotificationActor"]
