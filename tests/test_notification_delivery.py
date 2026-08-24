from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from kairospy.strategy.apps.notification.application import (
    NotificationApplication,
    NotificationRequest,
)
from kairospy.strategy.apps.notification.application.models import (
    NotificationDestination,
    RenderedNotification,
    SenderResult,
)
from kairospy.strategy.apps.notification.services import NotificationDeliveryRuntime
from kairospy.strategy.apps.notification.services.senders import (
    AppriseSender,
)
from kairospy.strategy import StrategyLogger
from kairospy.strategy import NotificationReceipt as StrategyNotificationReceipt
from kairospy.strategy import NotificationRequest as StrategyNotificationRequest
from io import StringIO
from kairospy.infrastructure.observability import redact_http_url


class _Sender:
    def __init__(self, outcomes: dict[str, SenderResult] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[str] = []

    async def send(self, destination, message):
        self.calls.append(destination.destination_id)
        return self.outcomes.get(destination.destination_id, SenderResult("delivered"))


class _SequenceSender:
    def __init__(self, outcomes: list[SenderResult]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def send(self, destination, message):
        result = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return result


class _RaisingSender:
    def __init__(self) -> None:
        self.calls = 0

    async def send(self, destination, message):
        self.calls += 1
        raise RuntimeError("sender bug with bot_token=must-not-leak")


class _BlockingSender:
    async def send(self, destination, message):
        await asyncio.Event().wait()
        return SenderResult("delivered")


def _runtime(
    sender: _Sender,
    *,
    capacity: int = 8,
    journal: Path | None = None,
    health: Path | None = None,
) -> NotificationDeliveryRuntime:
    return NotificationDeliveryRuntime(
        identity={
            "workspace_id": "workspace",
            "launch_id": "launch",
            "instance_id": "instance",
            "strategy_id": "strategy",
            "mode": "paper",
        },
        routes={
            "signals": ("feishu", "telegram"),
            "urgent": ("telegram",),
        },
        default_routes=("signals",),
        destinations={
            "feishu": NotificationDestination("feishu", "feishu"),
            "telegram": NotificationDestination(
                "telegram", "telegram", settings={"chat_id": "1"}
            ),
        },
        senders={"feishu": sender, "telegram": sender},
        queue_capacity=capacity,
        journal_path=journal,
        health_path=health,
    )


def test_request_validation_and_immutable_attributes() -> None:
    assert StrategyNotificationRequest is NotificationRequest
    assert StrategyNotificationReceipt.__name__ == "NotificationReceipt"
    request = NotificationRequest(
        title=" Signal ",
        body=" Body ",
        routes=("signals", "signals"),
        occurred_at=datetime.now(timezone.utc),
        attributes={"underlying": "SPY"},
    )
    assert request.title == "Signal"
    assert request.routes == ("signals",)
    with pytest.raises(TypeError):
        request.attributes["underlying"] = "QQQ"  # type: ignore[index]
    with pytest.raises(ValueError, match="timezone-aware"):
        NotificationRequest(title="x", body="y", occurred_at=datetime(2026, 1, 1))


def test_publish_is_nonblocking_bounded_and_deduplicated() -> None:
    sender = _Sender()
    runtime = _runtime(sender, capacity=1)
    app = NotificationApplication(runtime)

    first = app.publish(title="one", body="body", dedupe_key="opportunity")
    duplicate = app.publish(title="one", body="body", dedupe_key="opportunity")
    full = app.publish(title="two", body="body")

    assert first.status == "accepted"
    assert first.accepted_destinations == 2
    assert duplicate.status == "duplicate"
    assert full.status == "rejected"
    assert full.reason == "queue_full"
    assert sender.calls == []


def test_unknown_and_disabled_routes_are_explicit_receipts() -> None:
    disabled = NotificationApplication.disabled()
    assert disabled.publish(title="x", body="y").reason == "notifications_disabled"

    runtime = _runtime(_Sender())
    receipt = NotificationApplication(runtime).publish(
        title="x", body="y", routes=("missing",)
    )
    assert receipt.status == "rejected"
    assert receipt.reason == "unknown_route:missing"
    invalid = NotificationApplication(runtime).publish(title="", body="")
    assert invalid.status == "rejected"
    assert invalid.reason == "invalid_request"


def test_delivery_fans_out_once_and_isolates_destination_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[list[str], dict[str, object]]:
        sender = _Sender({"telegram": SenderResult("failed", "permission_rejected")})
        runtime = _runtime(
            sender,
            journal=tmp_path / "delivery.jsonl",
            health=tmp_path / "health.json",
        )
        await runtime.start()
        NotificationApplication(runtime).publish(
            title="spread",
            body="sell 590P / buy 585P",
            routes=("signals", "urgent"),
        )
        assert await runtime.flush(timeout=1)
        current = runtime.health()
        await runtime.close()
        return sender.calls, current

    calls, health = asyncio.run(scenario())
    assert calls == ["feishu", "telegram"]
    assert health["delivered_total"] == 1
    assert health["failed_total"] == 1
    assert health["state"] == "degraded"
    journal = (tmp_path / "delivery.jsonl").read_text(encoding="utf-8")
    assert "sell 590P" not in journal
    assert "body_sha256" in journal
    persisted = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert persisted["destinations"]["telegram"]["last_error_code"] == (
        "permission_rejected"
    )


def test_delivered_dedupe_key_survives_runtime_restart(tmp_path: Path) -> None:
    journal = tmp_path / "delivery.jsonl"

    async def deliver_once() -> str:
        runtime = _runtime(_Sender(), journal=journal)
        await runtime.start()
        receipt = NotificationApplication(runtime).publish(
            title="terminal intent",
            body="intent-1 satisfied",
            dedupe_key="intent:intent-1:lifecycle:7",
        )
        assert await runtime.flush(timeout=1)
        await runtime.close()
        return receipt.notification_id

    notification_id = asyncio.run(deliver_once())
    restored = _runtime(_Sender(), journal=journal)
    receipt = NotificationApplication(restored).publish(
        title="terminal intent",
        body="intent-1 satisfied",
        dedupe_key="intent:intent-1:lifecycle:7",
    )

    assert receipt.status == "duplicate"
    records = restored.delivery_records((notification_id,))
    assert len(records) == 2
    assert all(record["dedupe_key_sha256"] for record in records)
    assert all("intent:intent-1" not in json.dumps(record) for record in records)


def test_route_supports_one_feishu_and_multiple_telegram_destinations() -> None:
    async def scenario() -> list[str]:
        sender = _Sender()
        destinations = {
            "feishu": NotificationDestination("feishu", "feishu"),
            "telegram-primary": NotificationDestination(
                "telegram-primary", "telegram", settings={"chat_id": "1"}
            ),
            "telegram-backup": NotificationDestination(
                "telegram-backup", "telegram", settings={"chat_id": "2"}
            ),
        }
        runtime = NotificationDeliveryRuntime(
            identity={},
            routes={"signals": tuple(destinations)},
            default_routes=("signals",),
            destinations=destinations,
            senders={destination_id: sender for destination_id in destinations},
        )
        await runtime.start()
        NotificationApplication(runtime).publish(title="x", body="y")
        await runtime.flush(timeout=1)
        await runtime.close()
        return sender.calls

    assert asyncio.run(scenario()) == [
        "feishu",
        "telegram-primary",
        "telegram-backup",
    ]


def test_event_time_is_bound_into_notification() -> None:
    sender = _Sender()
    runtime = _runtime(sender)
    app = NotificationApplication(runtime)
    occurred_at = datetime(2026, 8, 18, 1, 2, tzinfo=timezone.utc)
    app.bind_event(occurred_at)
    receipt = app.publish(title="x", body="y")
    assert receipt.status == "accepted"
    queued = runtime.queue.get_nowait()
    assert queued.message.occurred_at == occurred_at


def test_provider_failure_is_recorded_and_worker_sender_exception_isolated() -> None:
    async def failure_scenario() -> tuple[int, dict[str, object]]:
        sender = _SequenceSender(
            [SenderResult("failed", "limited"), SenderResult("delivered")]
        )
        runtime = NotificationDeliveryRuntime(
            identity={},
            routes={"signals": ("one",)},
            default_routes=("signals",),
            destinations={"one": NotificationDestination("one", "feishu")},
            senders={"one": sender},
        )
        await runtime.start()
        NotificationApplication(runtime).publish(title="x", body="y")
        await runtime.flush(timeout=1)
        health = runtime.health()
        await runtime.close()
        return sender.calls, health

    calls, health = asyncio.run(failure_scenario())
    assert calls == 1
    assert health["failed_total"] == 1

    async def exception_scenario() -> dict[str, object]:
        sender = _RaisingSender()
        runtime = NotificationDeliveryRuntime(
            identity={},
            routes={"signals": ("one",)},
            default_routes=("signals",),
            destinations={"one": NotificationDestination("one", "feishu")},
            senders={"one": sender},
        )
        await runtime.start()
        NotificationApplication(runtime).publish(title="first", body="y")
        NotificationApplication(runtime).publish(title="second", body="y")
        await runtime.flush(timeout=1)
        health = runtime.health()
        await runtime.close()
        return health

    failed = asyncio.run(exception_scenario())
    assert failed["failed_total"] == 2
    assert failed["state"] == "degraded"


def test_notifications_preserve_order_for_each_destination() -> None:
    class _OrderedSender:
        def __init__(self) -> None:
            self.titles: list[str] = []

        async def send(self, destination, message):
            await asyncio.sleep(0)
            self.titles.append(message.title)
            return SenderResult("delivered")

    async def scenario() -> list[str]:
        sender = _OrderedSender()
        runtime = NotificationDeliveryRuntime(
            identity={},
            routes={"signals": ("one",)},
            default_routes=("signals",),
            destinations={"one": NotificationDestination("one", "feishu")},
            senders={"one": sender},
        )
        await runtime.start()
        application = NotificationApplication(runtime)
        application.publish(title="first", body="y")
        application.publish(title="second", body="y")
        await runtime.flush(timeout=1)
        await runtime.close()
        return sender.titles

    assert asyncio.run(scenario()) == ["first", "second"]


def test_shutdown_grace_cancels_and_counts_undelivered_work() -> None:
    async def scenario() -> dict[str, object]:
        runtime = NotificationDeliveryRuntime(
            identity={},
            routes={"signals": ("one",)},
            default_routes=("signals",),
            destinations={"one": NotificationDestination("one", "feishu")},
            senders={"one": _BlockingSender()},
            shutdown_grace_seconds=0.01,
        )
        await runtime.start()
        NotificationApplication(runtime).publish(title="x", body="y")
        await asyncio.sleep(0)
        await runtime.close()
        return runtime.health()

    health = asyncio.run(scenario())
    assert health["state"] == "unhealthy"
    assert health["failed_total"] == 1
    assert health["queue_depth"] == 0


def _message() -> RenderedNotification:
    return RenderedNotification(
        "notification",
        "title",
        "body",
        "warning",
        datetime.now(timezone.utc),
        {},
        {"mode": "paper"},
    )


def test_apprise_sender_delegates_feishu_and_telegram_protocols(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []

    class _Apprise:
        def __init__(self, **kwargs) -> None:
            pass

        def add(self, service: dict[str, object]) -> bool:
            captured.append(service)
            return True

        async def async_notify(self, **kwargs) -> bool:
            calls.append(kwargs)
            return True

    monkeypatch.setattr(
        "kairospy.strategy.apps.notification.services.senders.apprise.Apprise", _Apprise
    )
    feishu = NotificationDestination(
        "feishu",
        "feishu",
        secrets={
            "webhook_url": ("https://open.feishu.cn/open-apis/bot/v2/hook/feishu-token")
        },
    )
    telegram = NotificationDestination(
        "telegram",
        "telegram",
        settings={"chat_id": "-10042"},
        secrets={"bot_token": "123456:secret-token"},
    )
    feishu_sender = AppriseSender(feishu, max_attempts=3)
    telegram_sender = AppriseSender(telegram, max_attempts=3)

    async def scenario() -> tuple[SenderResult, SenderResult]:
        return (
            await feishu_sender.send(feishu, _message()),
            await telegram_sender.send(telegram, _message()),
        )

    results = asyncio.run(scenario())
    assert all(result.delivered for result in results)
    assert captured[0]["schema"] == "feishu"
    assert captured[0]["token"] == "feishu-token"
    assert captured[0]["retry"] == 2
    assert captured[1]["schema"] == "tgram"
    assert captured[1]["targets"] == ["-10042"]
    assert captured[1]["preview"] is False
    assert str(calls[0]["body"]).startswith("[WARNING] title")


def test_apprise_sender_rejects_nonofficial_feishu_webhook() -> None:
    with pytest.raises(ValueError, match="official custom-bot webhook"):
        AppriseSender(
            NotificationDestination(
                "feishu",
                "feishu",
                secrets={"webhook_url": "https://example.test/hook"},
            )
        )


@pytest.mark.parametrize("outcome", [False, None])
def test_apprise_sender_maps_non_success_to_one_sanitized_failure(
    monkeypatch, outcome: bool | None
) -> None:
    class _Apprise:
        def __init__(self, **kwargs) -> None:
            pass

        def add(self, service: dict[str, object]) -> bool:
            return True

        async def async_notify(self, **kwargs) -> bool | None:
            return outcome

    monkeypatch.setattr(
        "kairospy.strategy.apps.notification.services.senders.apprise.Apprise", _Apprise
    )
    destination = NotificationDestination(
        "telegram",
        "telegram",
        settings={"chat_id": "42"},
        secrets={"bot_token": "123456:secret-token"},
    )
    result = asyncio.run(AppriseSender(destination).send(destination, _message()))
    assert result.outcome == "failed"
    assert result.error_code == "apprise_delivery_failed"


def test_apprise_exception_is_sanitized_by_worker() -> None:
    class _ExplodingSender:
        async def send(self, destination, message):
            raise RuntimeError("secret-token-must-not-leak")

    async def scenario() -> dict[str, object]:
        runtime = NotificationDeliveryRuntime(
            identity={},
            routes={"signals": ("telegram",)},
            default_routes=("signals",),
            destinations={
                "telegram": NotificationDestination(
                    "telegram", "telegram", settings={"chat_id": "42"}
                )
            },
            senders={"telegram": _ExplodingSender()},
        )
        await runtime.start()
        NotificationApplication(runtime).publish(title="x", body="y")
        await runtime.flush(timeout=1)
        health = runtime.health()
        await runtime.close()
        return health

    health = asyncio.run(scenario())
    destination = health["destinations"]["telegram"]
    assert destination["last_error_code"] == "RuntimeError"
    assert "secret-token-must-not-leak" not in repr(health)


def test_notification_secrets_are_redacted_by_structured_logging() -> None:
    stream = StringIO()
    StrategyLogger(stream=stream).error(
        "failed",
        webhook_url="https://example.test/secret-path",
        bot_token="bot-token-value",
        signing_secret="signing-value",
    )
    rendered = stream.getvalue()
    assert "secret-path" not in rendered
    assert "bot-token-value" not in rendered
    assert "signing-value" not in rendered
    assert rendered.count("[REDACTED]") == 3


def test_notification_tokens_are_redacted_from_telemetry_urls() -> None:
    telegram = redact_http_url(
        "https://api.telegram.org/bot123456:secret-token/sendMessage?token=query-secret"
    )
    feishu = redact_http_url(
        "https://open.feishu.cn/open-apis/bot/v2/hook/webhook-secret"
    )
    assert "123456" not in telegram
    assert "secret-token" not in telegram
    assert "query-secret" not in telegram
    assert "webhook-secret" not in feishu
    assert "[REDACTED]" in telegram
    assert "[REDACTED]" in feishu
