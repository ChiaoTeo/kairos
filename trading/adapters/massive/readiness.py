from __future__ import annotations

from dataclasses import dataclass
import re

from .client import MassiveClient


@dataclass(frozen=True, slots=True)
class MassiveReadinessReport:
    ready: bool
    api_host: str
    checks: dict[str, object]
    official_underlying_history: bool
    valuation_reference_mode: str


class MassiveReadinessChecker:
    def __init__(self, client: MassiveClient) -> None:
        self.client = client

    def check(self, *, underlying: str, option_ticker: str, date: str) -> MassiveReadinessReport:
        reference_ticker = f"I:{underlying}" if underlying.upper() in {"SPX", "VIX", "NDX", "RUT", "DJI"} and not underlying.startswith("I:") else underlying
        paired_option_ticker = _paired_option_ticker(option_ticker)
        probes = {
            "usage": ("/usage", {}),
            "underlying_reference": (f"/v3/reference/tickers/{reference_ticker}", {"date": date}),
            "underlying_aggregates": (f"/v2/aggs/ticker/{reference_ticker}/range/1/minute/{date}/{date}", {"adjusted": True, "limit": 1}),
            "option_contracts": ("/v3/reference/options/contracts", {"underlying_ticker": underlying, "as_of": date, "limit": 10}),
            "option_quotes": (f"/v3/quotes/{option_ticker}", {"timestamp": date, "limit": 1}),
            "paired_option_quotes": (f"/v3/quotes/{paired_option_ticker}", {"timestamp": date, "limit": 1}),
            "option_trades": (f"/v3/trades/{option_ticker}", {"timestamp": date, "limit": 1}),
            "option_chain": (f"/v3/snapshot/options/{underlying}", {"limit": 1}),
        }
        checks = {}
        for name, (path, params) in probes.items():
            try:
                payload = self.client.get(path, params).json()
                if not isinstance(payload, dict):
                    raise ValueError("readiness probe expected an object response")
                results = payload.get("results")
                checks[name] = {"accessible": True, "request_id": payload.get("request_id"),
                                "result_count": len(results) if isinstance(results, list) else int(results is not None),
                                "status": payload.get("status")}
            except Exception as error:
                checks[name] = {"accessible": False, "error_type": type(error).__name__, "error": str(error)}
        index_underlying = reference_ticker.startswith("I:")
        required = {"usage", "underlying_reference", "option_contracts", "option_quotes", "option_trades", "option_chain"}
        if not index_underlying:
            required.add("underlying_aggregates")
        base_ready = all(bool(checks[name]["accessible"]) for name in required)
        official = bool(checks["underlying_aggregates"]["accessible"])
        paired = bool(checks["paired_option_quotes"]["accessible"])
        ready = base_ready and (official or index_underlying and paired)
        mode = "official_underlying" if official else "put_call_parity_synthetic_forward" if index_underlying and base_ready and paired else "unavailable"
        return MassiveReadinessReport(ready, "https://api.massiveprivateserver.site", checks, official, mode)


def _paired_option_ticker(ticker: str) -> str:
    match = re.match(r"^(O:[A-Z0-9.]+\d{6})([CP])(\d{8})$", ticker)
    if not match:
        raise ValueError("Massive readiness requires an OCC option ticker with O: prefix")
    opposite = "P" if match.group(2) == "C" else "C"
    return f"{match.group(1)}{opposite}{match.group(3)}"
