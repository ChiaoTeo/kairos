from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from kairospy.integrations.extensions.http import download_json
from kairospy.infrastructure.storage.data_lake import sha256_bytes, utc_midnight, write_json


class DeribitOptionTradeHistoryProvider:
    url = "https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"

    def fetch(self, currency: str, start: date, end: date, source_root: Path, workers: int = 16) -> list[dict[str, object]]:
        days = [start + timedelta(days=offset) for offset in range((end-start).days+1)]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            batches = executor.map(lambda day: self._day(currency, day, source_root), days)
            return [trade for batch in batches for trade in batch]

    def _day(self, currency: str, day: date, source_root: Path) -> list[dict[str, object]]:
        partition = source_root / "provider=deribit" / "dataset=option_trades" / f"currency={currency}" / f"event_year={day.year:04d}" / f"event_month={day.month:02d}" / f"event_day={day.day:02d}"
        payload = partition / "payload.json"
        if payload.exists():
            return json.loads(payload.read_text(encoding="utf-8"))
        start_ms, end_ms = _milliseconds(day), _milliseconds(day + timedelta(days=1))
        cursor, trades, seen = start_ms, [], set()
        while cursor < end_ms:
            params = {"currency": currency, "kind": "option", "start_timestamp": cursor,
                      "end_timestamp": end_ms, "count": 10000, "sorting": "asc"}
            result = download_json(self.url, params)["result"]
            page = result["trades"]
            if not page:
                break
            added = 0
            for trade in page:
                if trade["trade_id"] not in seen:
                    seen.add(trade["trade_id"]); trades.append(trade); added += 1
            last = int(page[-1]["timestamp"])
            if not result.get("has_more"):
                break
            cursor = last if added else last + 1
        partition.mkdir(parents=True, exist_ok=True)
        payload.write_text(json.dumps(trades, separators=(",", ":")), encoding="utf-8")
        content = payload.read_bytes()
        write_json(partition / "receipt.json", {"receipt_version": 1, "provider": "deribit", "dataset": "option_trades",
            "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "request": {"url": self.url, "parameters": {"currency": currency, "kind": "option", "sorting": "asc"},
                        "window": {"start": utc_midnight(day), "end": utc_midnight(day+timedelta(days=1)), "boundary": "[start,end)"}},
            "response": {"status": 200, "bytes": len(content), "sha256": sha256_bytes(content),
                         "download_status": "complete", "records": len(trades)}, "authentication": "none"})
        return trades


def normalize_deribit_trades(trades: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    for trade in trades:
        parts = str(trade["instrument_name"]).split("-")
        if len(parts) != 4:
            continue
        try:
            expiry = datetime.strptime(parts[1], "%d%b%y").replace(hour=8, tzinfo=timezone.utc)
            timestamp = datetime.fromtimestamp(int(trade["timestamp"])/1000, timezone.utc)
            iv = float(trade["iv"])/100
        except (TypeError, ValueError):
            continue
        if expiry <= timestamp or not 0 < iv < 5:
            continue
        rows.append({"event_time": timestamp.isoformat().replace("+00:00", "Z"),
            "available_time": timestamp.isoformat().replace("+00:00", "Z"), "venue": "deribit",
            "underlying_id": "BTC-USD", "instrument_id": trade["instrument_name"], "trade_id": trade["trade_id"],
            "expiry": expiry.isoformat().replace("+00:00", "Z"), "option_right": "call" if parts[3] == "C" else "put",
            "strike": float(parts[2]), "price_btc": float(trade["price"]), "amount_btc": float(trade.get("amount", trade.get("contracts", 0))),
            "direction": trade["direction"], "trade_iv": iv, "mark_price_btc": _optional_float(trade.get("mark_price")),
            "index_price_usd": _optional_float(trade.get("index_price")), "tick_direction": int(trade["tick_direction"])})
    return rows


def _milliseconds(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp()*1000)


def _optional_float(value):
    return float(value) if value is not None else ""
