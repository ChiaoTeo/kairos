from __future__ import annotations

from datetime import datetime, timezone

from kairospy.data.http import download_json


class DeribitOptionChainProvider:
    url = "https://deribit.com/api/v2/public/get_book_summary_by_currency"

    def snapshot(self, currency="BTC"):
        payload = download_json(self.url, {"currency": currency, "kind": "option"})
        collected = datetime.now(timezone.utc)
        return payload, normalize_chain(payload["result"], collected)


def normalize_chain(items, collected):
    timestamp = collected.isoformat().replace("+00:00", "Z")
    rows=[]
    for item in items:
        parts=item["instrument_name"].split("-")
        if len(parts)!=4: continue
        expiry=datetime.strptime(parts[1],"%d%b%y").replace(hour=8,tzinfo=timezone.utc)
        rows.append({"period_start":timestamp,"period_end":timestamp,"event_time":timestamp,"available_time":timestamp,
            "venue":"deribit","underlying_id":"BTC-USD","instrument_id":item["instrument_name"],
            "expiry":expiry.isoformat().replace("+00:00","Z"),"option_right":"call" if parts[3]=="C" else "put",
            "strike":float(parts[2]),"bid_price_btc":_value(item.get("bid_price")),"ask_price_btc":_value(item.get("ask_price")),
            "mid_price_btc":_value(item.get("mid_price")),"mark_price_btc":_value(item.get("mark_price")),
            "mark_iv":float(item["mark_iv"])/100,"underlying_price_usd":_value(item.get("underlying_price")),
            "estimated_delivery_price_usd":_value(item.get("estimated_delivery_price")),
            "open_interest":_value(item.get("open_interest")),"volume":_value(item.get("volume"))})
    return rows


def _value(value): return float(value) if value is not None else ""
