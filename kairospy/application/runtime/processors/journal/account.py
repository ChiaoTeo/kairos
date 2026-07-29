from __future__ import annotations

from datetime import datetime

from kairospy.application.runtime.ports import AccountJournalSink
from kairospy.core.views import ViewStore


class AccountJournalProcessor:
    def __init__(self, sink: AccountJournalSink, *, account_view_keys: tuple[str, ...]) -> None:
        self.sink = sink
        self.account_view_keys = account_view_keys
        self._last_written: dict[str, object] = {}

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for key in self.account_view_keys:
            account_view = views.get(key)
            if account_view is None:
                continue
            marker = _write_marker(account_view, as_of=as_of)
            if self._last_written.get(key) == marker:
                continue
            self.sink.record_account_view(account_view)
            self._last_written[key] = marker


def _write_marker(account_view: object, *, as_of: datetime | None) -> object:
    return (
        as_of,
        getattr(account_view, "event_count", None),
        getattr(account_view, "last_event_time", None),
        getattr(account_view, "equity", None),
        getattr(account_view, "cash", None),
        getattr(account_view, "net_profit", None),
        getattr(account_view, "total_return", None),
        len(tuple(getattr(account_view, "positions", ()) or ())),
        len(tuple(getattr(account_view, "open_orders", ()) or ())),
        len(tuple(getattr(account_view, "pending_orders", ()) or ())),
    )


__all__ = ["AccountJournalProcessor"]
