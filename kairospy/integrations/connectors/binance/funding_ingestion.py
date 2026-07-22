from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from kairospy.runtime.clock import Clock, SystemClock
from kairospy.identity import AccountRef
from kairospy.execution.ingestion import DurableAccountingIngestionService

from .funding_settlement import BinanceFundingSettlementClient


@dataclass(frozen=True, slots=True)
class FundingBackfillReport:
    start: datetime
    end: datetime
    observed: int
    committed: int
    complete: bool = True


class BinanceDurableFundingBackfill:
    """Overlap-safe periodic Venue funding-history ingestion for a supervised runtime."""

    def __init__(
        self, account: AccountRef, funding_client: BinanceFundingSettlementClient,
        ingestion: DurableAccountingIngestionService, *, clock: Clock | None = None,
        initial_lookback: timedelta = timedelta(days=7), overlap: timedelta = timedelta(hours=1),
    ) -> None:
        if initial_lookback <= timedelta(0) or overlap < timedelta(0):
            raise ValueError("funding lookback must be positive and overlap cannot be negative")
        self.account, self.funding_client, self.ingestion = account, funding_client, ingestion
        self.clock = clock or SystemClock()
        self.initial_lookback, self.overlap = initial_lookback, overlap
        self.last_end: datetime | None = None

    def start(self) -> FundingBackfillReport:
        return self.backfill()

    def backfill(self) -> FundingBackfillReport:
        end = self.clock.now()
        start = (self.last_end - self.overlap) if self.last_end is not None else (end - self.initial_lookback)
        payments = self.funding_client.funding_history(self.account, start, end)
        committed = self.ingestion.ingest_funding_history(payments, source="binance")
        self.last_end = end
        return FundingBackfillReport(start, end, len(payments), committed)

    def stop(self) -> None:
        return None
