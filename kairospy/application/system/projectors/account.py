from __future__ import annotations

from datetime import datetime

from kairospy.application.system.artifacts.output import RunOutput
from kairospy.core.views import ViewStore


class AccountCurrentProjector:
    def __init__(self, output: RunOutput) -> None:
        self.output = output
        self._last_written: dict[str, object] = {}

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        for key in _account_view_keys(views):
            account_view = views.get(key)
            if account_view is None:
                continue
            marker = _write_marker(account_view, as_of=as_of)
            if self._last_written.get(key) == marker:
                continue
            self.output.update_current("account", _current_account_payload(account_view))
            self._last_written[key] = marker


def _account_view_keys(views: ViewStore) -> tuple[str, ...]:
    return tuple(key for key in views.envelopes() if key.startswith("account.current."))


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


def _current_account_payload(account_view: object) -> dict[str, object]:
    return {
        "account_view": account_view,
        "equity": getattr(account_view, "equity", None),
        "net_profit": getattr(account_view, "net_profit", None),
        "total_return": getattr(account_view, "total_return", None),
    }


__all__ = ["AccountCurrentProjector"]
