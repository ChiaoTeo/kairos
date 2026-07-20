from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable

from kairos.backtest.feed import MarketSnapshot
from kairos.contracts import CanonicalEventEnvelope, MarketEventKind, QuotePayload
from kairos.domain.market_data import Quote
from kairos.domain.intent import Intent
from kairos.market_data.stream import EventSource
from kairos.research_platform.snapshot import InstrumentSnapshot
from kairos.storage.codec import to_primitive

from .strategy_protocols import Strategy, StrategyContext, StrategyDecision


@dataclass(frozen=True, slots=True)
class StrategyEventSessionResult:
    event_message_ids: tuple[str, ...]
    decisions: tuple[StrategyDecision, ...]
    intents: tuple[Intent, ...]
    projection_hash: str
    decision_hash: str
    intent_hash: str
    audit_hash: str


class CanonicalQuoteSliceProjection:
    """Build point-in-time MarketSnapshot values shared by live and replay StrategyContext."""

    def __init__(self) -> None:
        self._quotes: dict = {}
        self._sequence = 0

    def apply(self, event: CanonicalEventEnvelope) -> MarketSnapshot | None:
        if event.kind is not MarketEventKind.QUOTE:
            return None
        if not isinstance(event.payload, QuotePayload):
            raise TypeError("canonical quote slice projection requires QuotePayload")
        payload = event.payload
        if payload.bid is not None and payload.bid <= 0 or payload.ask is not None and payload.ask <= 0:
            raise ValueError("strategy quote projection rejects non-positive quote")
        if payload.bid is not None and payload.ask is not None and payload.bid > payload.ask:
            raise ValueError("strategy quote projection rejects crossed quote")
        quote = Quote(
            event.instrument_id, payload.bid, payload.ask, payload.bid_size, payload.ask_size,
            event.event_time,
        )
        self._quotes[event.instrument_id] = InstrumentSnapshot(
            event.instrument_id, quote, event.event_time, None, None, None, None,
        )
        self._sequence += 1
        snapshots = tuple(self._quotes[key] for key in sorted(self._quotes, key=lambda item: item.value))
        references = tuple(
            (item.instrument_id, (item.quote.bid + item.quote.ask) / 2)
            for item in snapshots
            if item.quote is not None and item.quote.bid is not None and item.quote.ask is not None
        )
        return MarketSnapshot(
            event.available_time, snapshots, references, sequence=self._sequence,
            available_instruments=tuple(item.instrument_id for item in snapshots),
        )


class CanonicalStrategyEventSession:
    """Run the formal Strategy interface from Canonical Events and emit replay audit evidence."""

    def __init__(
        self,
        source: EventSource[CanonicalEventEnvelope],
        strategy: Strategy,
        context_factory: Callable[[MarketSnapshot], StrategyContext],
    ) -> None:
        self.source = source
        self.strategy = strategy
        self.context_factory = context_factory

    async def run(self) -> StrategyEventSessionResult:
        projection = CanonicalQuoteSliceProjection()
        event_ids: list[str] = []
        intents: list[Intent] = []
        slices: list[MarketSnapshot] = []
        last_context: StrategyContext | None = None
        started = False
        async for event in self.source.events():
            market = projection.apply(event)
            if market is None:
                continue
            event_ids.append(str(event.message_id))
            slices.append(market)
            context = self.context_factory(market)
            if not started:
                intents.extend(self.strategy.on_start(context))
                started = True
            intents.extend(self.strategy.on_market(context))
            last_context = context
        if last_context is not None:
            intents.extend(self.strategy.on_end(last_context))
        decisions = tuple(self.strategy.decisions)
        projection_hash = _hash(slices)
        decision_hash = _hash(decisions)
        intent_hash = _hash(intents)
        material = {
            "events": event_ids,
            "projection_hash": projection_hash,
            "decision_hash": decision_hash,
            "intent_hash": intent_hash,
        }
        return StrategyEventSessionResult(
            tuple(event_ids), decisions, tuple(intents), projection_hash, decision_hash, intent_hash,
            _hash(material),
        )


def _hash(value: object) -> str:
    encoded = json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()
    return sha256(encoded).hexdigest()
