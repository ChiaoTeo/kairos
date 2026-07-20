from __future__ import annotations

from kairos.application.clock import Clock, SystemClock
from kairos.execution.recovery import OrderRecoveryReport, VenueOrderRecoveryService

from .session import IbkrSession


class IbkrDurableFillIngestion:
    """Drive durable IBKR fill/commission backfill from synchronized session events.

    Commission report is the trigger because IBKR can emit execution details before
    final commission data. Explicit ``backfill`` is also used on connect/reconnect.
    """

    def __init__(self, session: IbkrSession, recovery: VenueOrderRecoveryService, *, clock: Clock | None = None) -> None:
        self.session = session
        self.recovery = recovery
        self.clock = clock or SystemClock()
        self.started = False
        self.last_report: OrderRecoveryReport | None = None
        self.last_error: str | None = None

    def start(self) -> OrderRecoveryReport:
        if self.started:
            return self.backfill()
        self.session.connect()
        self._subscribe("commissionReportEvent", self._on_commission)
        self._subscribe("connectedEvent", self._on_connected)
        self.started = True
        return self.backfill()

    def stop(self) -> None:
        if not self.started:
            return
        self._unsubscribe("commissionReportEvent", self._on_commission)
        self._unsubscribe("connectedEvent", self._on_connected)
        self.started = False

    def backfill(self) -> OrderRecoveryReport:
        report = self.recovery.recover(self.clock.now())
        self.last_report = report
        self.last_error = None
        return report

    @property
    def healthy(self) -> bool:
        return self.started and self.last_error is None and self.last_report is not None and self.last_report.complete

    def _on_commission(self, *args) -> None:
        self._event_backfill()

    def _on_connected(self, *args) -> None:
        self._event_backfill()

    def _event_backfill(self) -> None:
        try:
            self.backfill()
        except Exception as error:
            self.last_error = str(error)

    def _subscribe(self, name: str, callback) -> None:
        event = getattr(self.session.ib, name, None)
        if event is None:
            raise RuntimeError(f"IBKR session is missing {name}")
        event += callback

    def _unsubscribe(self, name: str, callback) -> None:
        event = getattr(self.session.ib, name, None)
        if event is not None:
            event -= callback
