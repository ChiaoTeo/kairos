from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, TextIOWrapper
from pathlib import Path
from zipfile import ZipFile

from kairospy.integrations.extensions.http import download
from kairospy.infrastructure.storage.data_lake import sha256_bytes, utc_midnight, write_json


class BinanceOptionsEohArchiveProvider:
    base_url = "https://data.binance.vision/data/option/daily/EOHSummary"

    def fetch(self, underlying: str, start: date, end: date, source_root: Path) -> list[dict[str, str]]:
        days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]

        def one_day(day):
            name = f"{underlying}-EOHSummary-{day}.zip"
            url = f"{self.base_url}/{underlying}/{name}"
            partition = source_root / "provider=binance" / "dataset=option_eoh_summary" / f"underlying={underlying}" / f"event_year={day.year:04d}" / f"event_month={day.month:02d}" / f"event_day={day.day:02d}"
            payload = partition / "payload.zip"
            try:
                content = payload.read_bytes() if payload.exists() else download(url)
            except Exception:
                return []
            if not payload.exists():
                partition.mkdir(parents=True, exist_ok=True); payload.write_bytes(content)
                write_json(partition / "receipt.json", {"receipt_version": 1, "provider": "binance", "dataset": "option_eoh_summary",
                    "requested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "request": {"url": url, "parameters": {"underlying": underlying, "date": day.isoformat()},
                                "window": {"start": utc_midnight(day), "end": utc_midnight(day + timedelta(days=1)), "boundary": "[start,end)"}},
                    "response": {"status": 200, "bytes": len(content), "sha256": sha256_bytes(content), "download_status": "complete"},
                    "authentication": "none"})
            with ZipFile(BytesIO(content)) as zipped:
                member = next(item for item in zipped.namelist() if item.endswith(".csv"))
                with zipped.open(member) as raw:
                    return list(csv.DictReader(TextIOWrapper(raw, encoding="utf-8")))

        rows = []
        with ThreadPoolExecutor(max_workers=8) as executor:
            for batch in executor.map(one_day, days):
                rows.extend(batch)
        return rows


def normalize_eoh_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    result = []
    for row in rows:
        as_of = datetime.strptime(f"{row['date']} {row['hour']}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        expiry_code, strike_text, right = row["symbol"].split("-")[1:]
        expiry = datetime.strptime(expiry_code, "%y%m%d").replace(hour=8, tzinfo=timezone.utc)
        if expiry <= as_of:
            continue
        result.append({
            "period_start": as_of.isoformat().replace("+00:00", "Z"),
            "period_end": (as_of + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "event_time": (as_of + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "available_time": (as_of + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "venue": "binance", "underlying_id": "BTC-USDT", "instrument_id": row["symbol"],
            "expiry": expiry.isoformat().replace("+00:00", "Z"), "option_right": "call" if right == "C" else "put",
            "strike": float(strike_text), "best_bid_price": _float(row["best_bid_price"]), "best_ask_price": _float(row["best_ask_price"]),
            "best_bid_size": _float(row["best_bid_qty"]), "best_ask_size": _float(row["best_ask_qty"]),
            "bid_iv": _float(row["best_buy_iv"]), "ask_iv": _float(row["best_sell_iv"]),
            "mark_price": _float(row["mark_price"]), "mark_iv": _float(row["mark_iv"]), "vendor_delta": _float(row["delta"]),
            "vendor_gamma": _float(row["gamma"]), "vendor_vega": _float(row["vega"]), "vendor_theta": _float(row["theta"]),
            "volume_contracts": _float(row["volume_contracts"]), "open_interest_contracts": _float(row["openinterest_contracts"]),
        })
    return result


def _float(value: str):
    return float(value) if value not in (None, "") else ""
