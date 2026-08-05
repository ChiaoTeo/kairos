from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kairospy.application.usecases.notification import (
    NotificationApplication,
    NotificationCategory,
    NotificationRequest,
    notification_settings,
)
from kairospy.application.actor.notification import NotificationActor
from kairospy.application.support.messaging import Message
from kairospy.infrastructure.notifications import (
    FeishuNotificationSender,
    TelegramNotificationSender,
    WeComNotificationSender,
)


@dataclass
class FakeTransport:
    response: dict[str, object]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((url, payload))
        return self.response


@pytest.fixture
def notification_request() -> NotificationRequest:
    return NotificationRequest(
        NotificationCategory.EXECUTION_FILL,
        "订单成交",
        "BTC_USDT 价格=100.5 数量=1.0",
        deduplication_key="fill:1",
    )


def test_notification_settings_resolves_environment_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token-value")
    settings = notification_settings(
        {
            "enabled": True,
            "summary_interval": "5m",
            "queue_size": 10,
            "max_attempts": 2,
            "telegram": {"bot_token": "${BOT_TOKEN}", "chat_id": "chat-1"},
        }
    )
    assert settings.summary_interval_seconds == 300
    assert settings.queue_size == 10
    assert settings.channels[0].values["bot_token"] == "token-value"


def test_feishu_sender_builds_signed_text_payload(notification_request: NotificationRequest) -> None:
    transport = FakeTransport({"code": 0})
    sender = FeishuNotificationSender("https://feishu.invalid", secret="secret", transport=transport)  # type: ignore[arg-type]

    import asyncio

    asyncio.run(sender.send(notification_request))
    url, payload = transport.calls[0]
    assert url == "https://feishu.invalid"
    assert payload["msg_type"] == "text"
    assert payload["timestamp"]
    assert payload["sign"]
    assert "订单成交" in payload["content"]["text"]  # type: ignore[index]


def test_wecom_sender_builds_markdown_payload(notification_request: NotificationRequest) -> None:
    transport = FakeTransport({"errcode": 0})
    sender = WeComNotificationSender("https://wecom.invalid", transport=transport)  # type: ignore[arg-type]

    import asyncio

    asyncio.run(sender.send(notification_request))
    assert transport.calls[0][1]["msgtype"] == "markdown"
    assert "订单成交" in transport.calls[0][1]["markdown"]["content"]  # type: ignore[index]


def test_telegram_sender_escapes_markdown_v2(notification_request: NotificationRequest) -> None:
    transport = FakeTransport({"ok": True})
    sender = TelegramNotificationSender("token", "chat", parse_mode="MarkdownV2", transport=transport)  # type: ignore[arg-type]

    import asyncio

    asyncio.run(sender.send(notification_request))
    payload = transport.calls[0][1]
    assert payload["chat_id"] == "chat"
    assert "价格\\=" in payload["text"]
    assert payload["parse_mode"] == "MarkdownV2"


def test_notification_application_routes_and_deduplicates(notification_request: NotificationRequest) -> None:
    class Sender:
        channel = "test"

        def __init__(self) -> None:
            self.requests: list[NotificationRequest] = []

        async def send(self, value: NotificationRequest) -> None:
            self.requests.append(value)

    sender = Sender()
    application = NotificationApplication((sender,), max_attempts=1)

    import asyncio

    first = asyncio.run(application.send(notification_request))
    second = asyncio.run(application.send(notification_request))
    assert first.delivered_channels == ("test",)
    assert second.deduplicated is True
    assert len(sender.requests) == 1


def test_notification_application_isolates_channel_failure(notification_request: NotificationRequest) -> None:
    class FailingSender:
        channel = "failing"

        async def send(self, value: NotificationRequest) -> None:
            raise RuntimeError("offline")

    class HealthySender:
        channel = "healthy"

        async def send(self, value: NotificationRequest) -> None:
            return None

    application = NotificationApplication((FailingSender(), HealthySender()), max_attempts=1)

    import asyncio

    result = asyncio.run(application.send(notification_request))
    assert result.failed_channels == ("failing",)
    assert result.delivered_channels == ("healthy",)


def test_notification_actor_enqueues_events_and_exposes_metrics() -> None:
    class Sender:
        channel = "test"

        def __init__(self) -> None:
            self.requests: list[NotificationRequest] = []

        async def send(self, value: NotificationRequest) -> None:
            self.requests.append(value)

    sender = Sender()
    actor = NotificationActor(NotificationApplication((sender,), max_attempts=1), queue_size=4)
    event = Message(
        "execution.update",
        SimpleNamespace(status="filled", order_id="order-1"),
        datetime.now(timezone.utc),
        "execution",
        1,
    )

    import asyncio

    async def run() -> None:
        await actor.start()
        await actor.process(event)
        await actor.queue.join()
        await actor.stop()

    asyncio.run(run())
    assert {request.category for request in sender.requests} >= {
        NotificationCategory.SYSTEM_LIFECYCLE,
        NotificationCategory.EXECUTION_FILL,
    }
    assert actor.runtime_metrics()["failed_count"] == 0
