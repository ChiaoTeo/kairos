from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.service.runtime import RuntimeAccountService
from kairospy.application.views import (
    DECISION_TRACE_SCHEMA,
    RISK_SNAPSHOTS_SCHEMA,
    DecisionTraceRecord,
    DecisionTraceView,
    FundingRateSnapshot,
    RiskPositionSnapshot,
    RiskSnapshot,
    RiskSnapshotsView,
    TraceViewKeys,
)
from kairospy.core.market import Bar, MarketEvent, OptionGreeks, Quote, RateObservation, TradePrint
from kairospy.core.views import ViewStore


class TraceProcessor:
    decision_key = TraceViewKeys.decision_trace
    risk_key = TraceViewKeys.risk_snapshots

    def __init__(
        self,
        *,
        service: RuntimeAccountService | None = None,
        cash_currency: str = "USD",
    ) -> None:
        self.service = service
        self.account = None if service is None else service.account
        self.cash_currency = cash_currency if service is None else service.cash_currency
        self._decision_records: list[DecisionTraceRecord] = []
        self._risk_snapshots: list[RiskSnapshot] = []
        self._marks: dict[str, Decimal] = {}
        self._funding_rates: dict[str, FundingRateSnapshot] = {}
        self._last_risk_marker: object | None = None
        self.decision_schema = DECISION_TRACE_SCHEMA
        self.risk_schema = RISK_SNAPSHOTS_SCHEMA

    def on_event(self, event: RuntimeEnvelope) -> None:
        self._update_market_state(event.payload)
        self._record_risk(event.time)

    def on_intents(self, intents: tuple[object, ...], context: object, hook: str) -> None:
        traces = tuple(getattr(context, "emitted_traces", ()) or ())
        emitted_intent_ids = tuple(str(getattr(intent, "intent_id", "")) for intent in intents if getattr(intent, "intent_id", None) is not None)
        for trace in traces:
            payload = _mapping(trace.get("payload") if isinstance(trace, Mapping) else None)
            trace_intent_ids = tuple(str(item) for item in payload.get("intent_ids", ()) or ()) if isinstance(payload.get("intent_ids"), (tuple, list)) else ()
            self._decision_records.append(
                DecisionTraceRecord(
                    time=trace.get("time") if isinstance(trace, Mapping) else getattr(context, "now", None),
                    strategy_id=str(trace.get("strategy_id") or getattr(context, "strategy_id", "")) if isinstance(trace, Mapping) else str(getattr(context, "strategy_id", "")),
                    name=str(trace.get("name") or hook or "decision") if isinstance(trace, Mapping) else hook or "decision",
                    payload=payload,
                    intent_ids=trace_intent_ids or emitted_intent_ids,
                )
            )
        if traces or intents:
            now = getattr(context, "now", None)
            if now is not None:
                self._record_risk(now)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.decision_schema.key) is None:
            views.register(self.decision_schema)
        if views.registry.get(self.risk_schema.key) is None:
            views.register(self.risk_schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.decision_key, DecisionTraceView(tuple(self._decision_records)), as_of=as_of, available_time=as_of)
        views.put_runtime(self.risk_key, RiskSnapshotsView(tuple(self._risk_snapshots)), as_of=as_of, available_time=as_of)

    def _update_market_state(self, payload: object) -> None:
        value = payload.value if isinstance(payload, MarketEvent) else payload
        instrument_id = getattr(value, "instrument_id", None)
        price = _mark_price(value)
        if instrument_id is not None and price is not None and price > 0:
            self._marks[str(instrument_id)] = price
        if isinstance(value, RateObservation) and value.basis.strip().lower() == "funding_rate":
            key = str(value.market_id or value.instrument_id or value.rate_id)
            self._funding_rates[key] = FundingRateSnapshot(
                market_id=None if value.market_id is None else str(value.market_id),
                instrument_id=None if value.instrument_id is None else str(value.instrument_id),
                rate=value.rate,
                mark_price=value.mark_price,
                time=value.time,
                basis=value.basis,
            )

    def _record_risk(self, at: datetime) -> None:
        if self.service is None or self.account is None:
            return
        cash = self.service.cash(self.cash_currency)
        raw_positions = self.service.positions()
        positions: list[RiskPositionSnapshot] = []
        gross_notional = Decimal("0")
        net_notional = Decimal("0")
        equity = cash
        for instrument_id, quantity in sorted(raw_positions.items()):
            mark = self._marks.get(str(instrument_id))
            notional = None if mark is None else quantity * mark
            if notional is not None:
                gross_notional += abs(notional)
                net_notional += notional
                equity += notional
            positions.append(RiskPositionSnapshot(str(instrument_id), quantity, mark, notional))
        marker = (at, cash, equity, gross_notional, net_notional, tuple(positions), tuple(sorted(self._funding_rates.items())))
        if marker == self._last_risk_marker:
            return
        self._risk_snapshots.append(
            RiskSnapshot(
                time=at,
                account_id=str(self.account.book.value),
                cash=cash,
                equity=equity,
                gross_notional=gross_notional,
                net_notional=net_notional,
                positions=tuple(positions),
                funding_rates=tuple(item for _, item in sorted(self._funding_rates.items())),
            )
        )
        self._last_risk_marker = marker


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mark_price(value: object) -> Decimal | None:
    if isinstance(value, Bar):
        return value.close
    if isinstance(value, TradePrint):
        return value.price
    if isinstance(value, RateObservation):
        return value.mark_price
    if isinstance(value, OptionGreeks):
        return value.mark_price or value.underlying_price
    if isinstance(value, Quote):
        return value.midpoint or value.bid or value.ask
    return None


__all__ = ["TraceProcessor"]
