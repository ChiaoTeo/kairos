from __future__ import annotations

from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from ..domain import NotificationRequest
from ..protocol import NotificationSender


async def deliver_with_retry(sender: NotificationSender, request: NotificationRequest, *, max_attempts: int) -> None:
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=0.25, min=0.25, max=5),
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    ):
        with attempt:
            await sender.send(request)


def _is_retryable(error: BaseException) -> bool:
    return bool(getattr(error, "retryable", True))


__all__ = ["deliver_with_retry"]
