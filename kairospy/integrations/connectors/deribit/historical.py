from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from kairospy.integrations.extensions.http import download_json
from kairospy.infrastructure.storage.data_lake import sha256_bytes, utc_midnight, write_json


class DeribitDvolProvider:
    url = "https://deribit.com/api/v2/public/get_volatility_index_data"

    def fetch_daily(self, currency: str, start: date, end: date, source_root: Path) -> dict[date, dict[str, float]]:
        values, cursor = {}, start
        while cursor <= end:
            chunk_end = min(end + timedelta(days=1), cursor + timedelta(days=900))
            params = {"currency": currency, "start_timestamp": _ms(cursor), "end_timestamp": _ms(chunk_end), "resolution": 86400}
            partition = source_root / "provider=deribit" / "dataset=volatility_index" / f"currency={currency}" / "resolution=1d" / f"request_start={cursor}_end={chunk_end}"
            payload_path = partition / "payload.json"
            payload = json.loads(payload_path.read_text()) if payload_path.exists() else download_json(self.url, params)
            if not payload_path.exists():
                partition.mkdir(parents=True, exist_ok=True)
                payload_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
                content = payload_path.read_bytes()
                write_json(partition / "receipt.json", {"receipt_version": 1, "provider": "deribit", "dataset": "volatility_index",
                    "request": {"url": self.url, "parameters": params, "window": {"start": utc_midnight(cursor), "end": utc_midnight(chunk_end), "boundary": "[start,end)"}},
                    "response": {"status": 200, "bytes": len(content), "sha256": sha256_bytes(content), "download_status": "complete"}, "authentication": "none"})
            for timestamp, open_, high, low, close in payload["result"]["data"]:
                values[datetime.fromtimestamp(timestamp / 1000, timezone.utc).date()] = {
                    "open": float(open_), "high": float(high), "low": float(low), "close": float(close)}
            cursor = chunk_end
        return values


def _ms(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
