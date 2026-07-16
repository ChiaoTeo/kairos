from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import BytesIO, TextIOWrapper
from pathlib import Path
from zipfile import ZipFile

from trading.data.http import download
from trading.storage.data_lake import sha256_bytes, utc_midnight, write_json


class BinanceSpotArchiveProvider:
    base_url = "https://data.binance.vision/data/spot/monthly/klines"

    def fetch_daily(self, symbol: str, start: date, end: date, source_root: Path) -> dict[date, dict[str, float]]:
        months, cursor = [], date(start.year, start.month, 1)
        while cursor <= end:
            months.append(cursor)
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)

        def month_rows(month):
            name = f"{symbol}-1d-{month:%Y-%m}.zip"
            partition = source_root / "provider=binance" / "dataset=spot_klines" / f"symbol={symbol}" / "interval=1d" / f"event_year={month.year:04d}" / f"event_month={month.month:02d}"
            payload = partition / "payload.zip"
            try:
                content = payload.read_bytes() if payload.exists() else download(f"{self.base_url}/{symbol}/1d/{name}")
            except Exception:
                return []
            if not payload.exists():
                partition.mkdir(parents=True, exist_ok=True); payload.write_bytes(content)
                next_month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
                write_json(partition / "receipt.json", _receipt("binance", "spot_klines", f"{self.base_url}/{symbol}/1d/{name}",
                           {"symbol": symbol, "interval": "1d"}, content, month, next_month))
            with ZipFile(BytesIO(content)) as zipped:
                member = next(item for item in zipped.namelist() if item.endswith(".csv"))
                with zipped.open(member) as raw:
                    return list(csv.reader(TextIOWrapper(raw, encoding="utf-8")))

        values = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            for rows in executor.map(month_rows, months):
                for row in rows:
                    timestamp = int(row[0]); divisor = 1_000_000 if timestamp > 10_000_000_000_000 else 1000
                    day = datetime.fromtimestamp(timestamp / divisor, timezone.utc).date()
                    if start <= day <= end:
                        values[day] = {"open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                                       "close": float(row[4]), "volume": float(row[5])}
        return values


def _receipt(provider, dataset, url, params, content, start, end):
    return {"receipt_version": 1, "provider": provider, "dataset": dataset,
            "request": {"url": url, "parameters": params, "window": {"start": utc_midnight(start), "end": utc_midnight(end), "boundary": "[start,end)"}},
            "response": {"status": 200, "bytes": len(content), "sha256": sha256_bytes(content), "download_status": "complete"},
            "authentication": "none"}
