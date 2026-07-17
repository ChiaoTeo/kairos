from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Iterable, Mapping
from uuid import NAMESPACE_URL, uuid5

from trading.domain.corporate_action import CashDividendEvent, SplitEvent, SymbolChangeEvent
from trading.domain.identity import AssetId, InstrumentId
from trading.reference import ProviderId, ReferenceCatalog


class MassiveCorporateActionDecoder:
    def __init__(self, mappings: ReferenceCatalog) -> None:
        self.mappings = mappings

    def splits(self, rows: Iterable[Mapping[str, object]]) -> tuple[SplitEvent, ...]:
        values = []
        for row in rows:
            ticker = str(row["ticker"])
            effective_at = _date(row.get("execution_date") or row.get("ex_date"))
            instrument_id = self._resolve("stocks", ticker, effective_at)
            ratio = Decimal(str(row["split_to"])) / Decimal(str(row["split_from"]))
            if ratio <= 0:
                raise ValueError("Massive split ratio must be positive")
            source_id = str(row.get("id") or f"{ticker}:{effective_at.date()}:{ratio}")
            values.append(SplitEvent(uuid5(NAMESPACE_URL, f"massive:split:{source_id}"), instrument_id, effective_at, ratio))
        return tuple(values)

    def dividends(self, rows: Iterable[Mapping[str, object]]) -> tuple[CashDividendEvent, ...]:
        values = []
        for row in rows:
            ticker = str(row["ticker"])
            ex_date = _date(row["ex_dividend_date"])
            pay_date = _date(row.get("pay_date") or row["ex_dividend_date"])
            instrument_id = self._resolve("stocks", ticker, ex_date)
            amount = Decimal(str(row["cash_amount"]))
            if amount < 0:
                raise ValueError("Massive dividend cash amount cannot be negative")
            source_id = str(row.get("id") or f"{ticker}:{ex_date.date()}:{amount}")
            values.append(CashDividendEvent(uuid5(NAMESPACE_URL, f"massive:dividend:{source_id}"), instrument_id, ex_date, pay_date,
                                             AssetId(str(row.get("currency") or "USD")), amount))
        return tuple(values)

    def ticker_events(self, rows: Iterable[Mapping[str, object]]) -> tuple[SymbolChangeEvent, ...]:
        values = []
        for row in rows:
            event_type = str(row.get("type") or row.get("event_type") or "").lower()
            if event_type not in {"ticker_change", "symbol_change"}:
                continue
            old_ticker = str(row.get("ticker") or row.get("old_ticker"))
            new_ticker = str(row.get("new_ticker") or row.get("ticker_change", {}).get("ticker"))
            effective_at = _date(row.get("date") or row.get("effective_date"))
            instrument_id = self._resolve("stocks", old_ticker, effective_at)
            source_id = str(row.get("id") or f"{old_ticker}:{new_ticker}:{effective_at.date()}")
            values.append(SymbolChangeEvent(uuid5(NAMESPACE_URL, f"massive:ticker-event:{source_id}"), instrument_id, effective_at, new_ticker, new_ticker))
        return tuple(values)

    def _resolve(self, namespace: str, external_id: str, at: datetime) -> InstrumentId:
        mapping = self.mappings.resolve_provider_symbol(ProviderId("massive"), namespace, external_id, at)
        return InstrumentId(mapping.target_id)


def _date(value: object) -> datetime:
    if value is None:
        raise ValueError("Massive corporate action is missing an effective date")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Massive corporate action datetime must be timezone-aware")
        return value
    return datetime.combine(datetime.fromisoformat(str(value)).date(), time.min, tzinfo=timezone.utc)
