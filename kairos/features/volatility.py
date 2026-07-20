from __future__ import annotations

from datetime import datetime, timedelta
from itertools import groupby
import math
import statistics
from pathlib import Path

from kairos.data.client import DatasetClient
from kairos.data.contracts import QualityLevel
from kairos.data.products import (
    BTC_DERIBIT_OPTION_TRADES, BTC_DERIBIT_TERM_SKEW_DAILY, BTC_DVOL_DAILY, BTC_IV_RV_DAILY,
    BTC_OPTION_QUOTES_HOURLY, BTC_SPOT_DAILY, BTC_TERM_SKEW_HOURLY, capabilities_payload,
)
from kairos.data.publishing import content_release_id, publish_release, release_path
from kairos.storage.data_lake import write_daily_dataset, write_intraday_dataset


def _input(release):
    if release.content_hash is None:
        raise ValueError(f"feature input release {release.release_id!r} has no frozen content hash")
    return {"release_id": release.release_id, "logical_key": str(release.product_key),
            "content_hash": release.content_hash, "schema_version": release.schema_version}


def build_iv_rv_panel(spot_rows, dvol_rows, lookback=30):
    spot = {row["period_start"]: float(row["close"]) for row in spot_rows}
    dvol = {row["period_start"]: float(row["close"]) for row in dvol_rows}
    times = sorted(set(spot) & set(dvol)); closes = [spot[t] for t in times]; ivs = [dvol[t] for t in times]
    returns = [math.nan] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    rows = []
    for i, timestamp in enumerate(times):
        past = returns[max(1, i-lookback+1):i+1]
        rv = statistics.stdev(past) * math.sqrt(365) * 100 if len(past) >= lookback else math.nan
        end = (datetime.fromisoformat(timestamp.replace("Z", "+00:00")) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        rows.append({"period_start": timestamp, "period_end": end, "event_time": end, "available_time": end,
                     "spot_close": closes[i], "dvol_close": ivs[i], "rv30": rv,
                     "iv_rv_spread": ivs[i]-rv if math.isfinite(rv) else math.nan})
    return rows


class BtcIvRvFeatureBuilder:
    def __init__(self, root: str | Path = "data") -> None:
        self.root, self.data = Path(root), DatasetClient(root)

    def build(self):
        spot_release = self.data.catalog.release(BTC_SPOT_DAILY.key)
        dvol_release = self.data.catalog.release(BTC_DVOL_DAILY.key)
        spot = self.data.load_rows(spot_release.release_id)
        dvol = self.data.load_rows(dvol_release.release_id)
        rows = build_iv_rv_panel(spot, dvol)
        inputs = [_input(item) for item in (spot_release, dvol_release)]
        release_id = content_release_id(BTC_IV_RV_DAILY, {"inputs": inputs, "rows": rows, "transform_version": 2})
        lineage = {"lineage_version": 2, "dataset_id": release_id,
                   "producer": {"name": "kairos.features.volatility", "transform": "btc_iv_rv_features", "version": 2},
                   "inputs": inputs, "parameters": {"rv_lookback": "P30D", "annualization_days": 365},
                   "point_in_time_safe": True, "contains_forward_labels": False}
        schema = {"schema_id": BTC_IV_RV_DAILY.schema_id, "schema_version": 1, "time_boundary": "[period_start,period_end)",
                  "primary_key": ["period_start"], "point_in_time_safe": True, "contains_forward_labels": False,
                  "columns": {
                      "period_start": {"type": "datetime", "timezone": "UTC"}, "period_end": {"type": "datetime", "timezone": "UTC"},
                      "event_time": {"type": "datetime", "timezone": "UTC"}, "available_time": {"type": "datetime", "timezone": "UTC"},
                      "spot_close": {"type": "number", "unit": "USDT_per_BTC"},
                      "dvol_close": {"type": "number", "unit": "annualized_volatility_percent"},
                      "rv30": {"type": "number", "unit": "annualized_volatility_percent", "lookback": "P30D"},
                      "iv_rv_spread": {"type": "number", "unit": "volatility_points"}}}
        manifest = write_daily_dataset(self.root / release_path(BTC_IV_RV_DAILY, release_id), rows,
                                       dataset_id=release_id, schema=schema, lineage=lineage,
                                       capabilities=capabilities_payload(BTC_IV_RV_DAILY, release_id))
        return publish_release(self.root, BTC_IV_RV_DAILY, release_id, manifest, provider="internal", venue=None,
                               transform_id="btc_iv_rv_features", transform_version="2",
                               quality_level=QualityLevel.RESEARCH)


class BtcTermSkewFeatureBuilder:
    TARGETS = (7, 14, 30, 60, 90)

    def __init__(self, root: str | Path = "data") -> None:
        self.root, self.data = Path(root), DatasetClient(root)

    def build(self):
        source_release = self.data.catalog.release(BTC_OPTION_QUOTES_HOURLY.key)
        quotes = self.data.load_rows(source_release.release_id)
        rows = build_term_skew_panel(quotes, self.TARGETS)
        inputs = [_input(source_release)]
        release_id = content_release_id(BTC_TERM_SKEW_HOURLY, {"inputs": inputs, "rows": rows, "transform_version": 2})
        lineage = {"lineage_version": 2, "dataset_id": release_id,
                   "producer": {"name": "kairos.features.volatility", "transform": "fixed_maturity_delta_skew", "version": 2},
                   "inputs": inputs,
                   "parameters": {"target_dte": list(self.TARGETS), "delta_nodes": [0.10, 0.25, 0.50],
                                  "term_interpolation": "linear_total_variance", "extrapolation": "none",
                                  "iv_source": "binance_mark_iv", "delta_source": "binance_vendor_delta"},
                   "point_in_time_safe": True, "contains_forward_labels": False}
        columns = {"period_start": {"type": "datetime", "timezone": "UTC"}, "period_end": {"type": "datetime", "timezone": "UTC"},
                   "event_time": {"type": "datetime", "timezone": "UTC"}, "available_time": {"type": "datetime", "timezone": "UTC"},
                   "quote_count": {"type": "integer"}, "expiry_count": {"type": "integer"}}
        for dte in self.TARGETS:
            for name in ("atm_iv", "put25_iv", "call25_iv", "put10_iv", "call10_iv", "put_skew25", "rr25", "bf25", "put_skew10", "rr10"):
                columns[f"{name}_{dte}d"] = {"type": "nullable_number", "unit": "absolute_volatility"}
        schema = {"schema_id": BTC_TERM_SKEW_HOURLY.schema_id, "schema_version": 1,
                  "time_boundary": "[period_start,period_end)", "primary_key": ["period_start"],
                  "point_in_time_safe": True, "contains_forward_labels": False, "columns": columns}
        manifest = write_intraday_dataset(self.root / release_path(BTC_TERM_SKEW_HOURLY, release_id), rows,
            dataset_id=release_id, schema=schema, lineage=lineage, interval=timedelta(hours=1),
            capabilities=capabilities_payload(BTC_TERM_SKEW_HOURLY, release_id))
        return publish_release(self.root, BTC_TERM_SKEW_HOURLY, release_id, manifest, provider="internal", venue=None,
                               transform_id="fixed_maturity_delta_skew", transform_version="2",
                               quality_level=QualityLevel.RESEARCH)


class BtcDeribitTradeSkewFeatureBuilder:
    TARGETS = (7, 14, 30, 60, 90)

    def __init__(self, root: str | Path = "data") -> None:
        self.root, self.data = Path(root), DatasetClient(root)

    def build(self):
        source_release = self.data.catalog.release(BTC_DERIBIT_OPTION_TRADES.key)
        stream = self.data.iter_rows(source_release.release_id)
        rows = []
        for _, daily_trades in groupby(stream, key=lambda row: row["event_time"][:10]):
            rows.extend(build_deribit_trade_skew_panel(daily_trades, self.TARGETS))
        inputs = [_input(source_release)]
        release_id = content_release_id(BTC_DERIBIT_TERM_SKEW_DAILY, {"inputs": inputs, "rows": rows, "transform_version": 2})
        lineage = {"lineage_version": 2, "dataset_id": release_id,
            "producer": {"name": "kairos.features.volatility", "transform": "deribit_trade_fixed_maturity_skew", "version": 2},
            "inputs": inputs,
            "parameters": {"target_dte": list(self.TARGETS), "delta_nodes": [0.10, 0.25, 0.50],
                "delta_model": "Black-76, zero rate, trade index as forward proxy", "maximum_delta_distance": 0.12,
                "minimum_trades_per_node": 2, "node_estimator": "amount-weighted median", "term_interpolation": "linear_total_variance",
                "extrapolation": "none", "iv_source": "deribit_trade_iv"},
            "point_in_time_safe": True, "contains_forward_labels": False,
            "limitations": ["trade_sampled_smile", "not_a_complete_quote_chain", "index_price_used_as_forward_proxy"]}
        columns = {"period_start": {"type": "datetime", "timezone": "UTC"}, "period_end": {"type": "datetime", "timezone": "UTC"},
                   "event_time": {"type": "datetime", "timezone": "UTC"}, "available_time": {"type": "datetime", "timezone": "UTC"},
                   "trade_count": {"type": "integer"}, "expiry_count": {"type": "integer"}}
        for dte in self.TARGETS:
            for name in ("atm_iv", "put25_iv", "call25_iv", "put10_iv", "call10_iv", "put_skew25", "rr25", "bf25", "put_skew10", "rr10"):
                columns[f"{name}_{dte}d"] = {"type": "nullable_number", "unit": "absolute_volatility"}
        schema = {"schema_id": BTC_DERIBIT_TERM_SKEW_DAILY.schema_id, "schema_version": 1,
                  "time_boundary": "[period_start,period_end)", "primary_key": ["period_start"],
                  "point_in_time_safe": True, "contains_forward_labels": False, "columns": columns}
        manifest = write_daily_dataset(self.root / release_path(BTC_DERIBIT_TERM_SKEW_DAILY, release_id), rows,
            dataset_id=release_id, schema=schema, lineage=lineage,
            capabilities=capabilities_payload(BTC_DERIBIT_TERM_SKEW_DAILY, release_id))
        return publish_release(self.root, BTC_DERIBIT_TERM_SKEW_DAILY, release_id, manifest,
                               provider="internal", venue=None, transform_id="deribit_trade_fixed_maturity_skew",
                               transform_version="2", quality_level=QualityLevel.RESEARCH)


def build_deribit_trade_skew_panel(trades, target_dtes=(7, 14, 30, 60, 90)):
    days = {}
    for trade in trades:
        try:
            timestamp = datetime.fromisoformat(trade["event_time"].replace("Z", "+00:00"))
            expiry = datetime.fromisoformat(trade["expiry"].replace("Z", "+00:00"))
            iv, strike, forward = float(trade["trade_iv"]), float(trade["strike"]), float(trade["index_price_usd"])
            amount = max(float(trade["amount_btc"]), 0.0001)
        except (TypeError, ValueError, KeyError):
            continue
        years = (expiry-timestamp).total_seconds()/(365*86400)
        if years <= 0 or not (0 < iv < 5 and strike > 0 and forward > 0):
            continue
        d1 = (math.log(forward/strike) + 0.5*iv*iv*years)/(iv*math.sqrt(years))
        call_delta = 0.5*(1+math.erf(d1/math.sqrt(2)))
        delta = call_delta if trade["option_right"] == "call" else call_delta-1
        days.setdefault(timestamp.date(), []).append((expiry, trade["option_right"], iv, delta, amount))
    rows = []
    for day, items in sorted(days.items()):
        as_of = datetime.combine(day+timedelta(days=1), datetime.min.time(), tzinfo=items[0][0].tzinfo)
        expiries = {}
        for expiry, right, iv, delta, amount in items:
            if expiry > as_of:
                expiries.setdefault(expiry, []).append((right, iv, delta, amount))
        smiles = []
        for expiry, points in expiries.items():
            nodes = {"call50": _trade_node(points, "call", 0.50), "put50": _trade_node(points, "put", -0.50),
                     "call25": _trade_node(points, "call", 0.25), "put25": _trade_node(points, "put", -0.25),
                     "call10": _trade_node(points, "call", 0.10), "put10": _trade_node(points, "put", -0.10)}
            if all(value is not None for value in nodes.values()):
                nodes["atm"] = (nodes["call50"]+nodes["put50"])/2
                smiles.append(((expiry-as_of).total_seconds()/86400, nodes))
        start = datetime.combine(day, datetime.min.time(), tzinfo=as_of.tzinfo)
        row = {"period_start": start.isoformat().replace("+00:00", "Z"), "period_end": as_of.isoformat().replace("+00:00", "Z"),
               "event_time": as_of.isoformat().replace("+00:00", "Z"), "available_time": as_of.isoformat().replace("+00:00", "Z"),
               "trade_count": len(items), "expiry_count": len(smiles)}
        for target in target_dtes:
            values = {name: _fixed_maturity(smiles, target, name) for name in ("atm", "put25", "call25", "put10", "call10")}
            row.update(_skew_fields(target, values))
        rows.append(row)
    return rows


def _trade_node(points, right, target, max_distance=0.12, minimum=2):
    candidates = [(iv, amount) for option_right, iv, delta, amount in points
                  if option_right == right and abs(delta-target) <= max_distance]
    if len(candidates) < minimum:
        return None
    return _weighted_median(candidates)


def _weighted_median(values):
    ordered = sorted(values); total = sum(weight for _, weight in ordered); cumulative = 0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= total/2:
            return value
    return ordered[-1][0]


def build_term_skew_panel(quotes, target_dtes=(7, 14, 30, 60, 90)):
    snapshots = {}
    for quote in quotes:
        try:
            iv, delta = float(quote["mark_iv"]), float(quote["vendor_delta"])
        except (TypeError, ValueError):
            continue
        if not (0 < iv < 5 and -1 <= delta <= 1):
            continue
        snapshots.setdefault(quote["period_start"], []).append((quote, iv, delta))
    output = []
    for timestamp, items in sorted(snapshots.items()):
        as_of = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        expiries = {}
        for quote, iv, delta in items:
            expiry = datetime.fromisoformat(quote["expiry"].replace("Z", "+00:00"))
            dte = (expiry - as_of).total_seconds() / 86400
            if dte <= 0:
                continue
            expiries.setdefault(expiry, []).append((quote["option_right"], iv, delta))
        smiles = []
        for expiry, points in expiries.items():
            nodes = {"call50": _nearest(points, "call", 0.50), "put50": _nearest(points, "put", -0.50),
                     "call25": _nearest(points, "call", 0.25), "put25": _nearest(points, "put", -0.25),
                     "call10": _nearest(points, "call", 0.10), "put10": _nearest(points, "put", -0.10)}
            if all(value is not None for value in nodes.values()):
                nodes["atm"] = (nodes["call50"] + nodes["put50"]) / 2
                smiles.append(((expiry-as_of).total_seconds()/86400, nodes))
        row = {"period_start": timestamp,
               "period_end": (as_of + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
               "event_time": (as_of + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
               "available_time": (as_of + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
               "quote_count": len(items), "expiry_count": len(smiles)}
        for target in target_dtes:
            values = {name: _fixed_maturity(smiles, target, name) for name in ("atm", "put25", "call25", "put10", "call10")}
            row.update(_skew_fields(target, values))
        output.append(row)
    return output


def _nearest(points, right, target):
    candidates = [(abs(delta-target), iv) for option_right, iv, delta in points if option_right == right]
    return min(candidates)[1] if candidates else None


def _fixed_maturity(smiles, target, node):
    ordered = sorted((dte, values[node]) for dte, values in smiles if values.get(node) is not None)
    lower = max((item for item in ordered if item[0] <= target), default=None)
    upper = min((item for item in ordered if item[0] >= target), default=None)
    if lower is None or upper is None:
        return ""
    if lower[0] == upper[0]:
        return lower[1]
    weight = (target-lower[0])/(upper[0]-lower[0])
    variance = lower[1]**2*(lower[0]/365)*(1-weight) + upper[1]**2*(upper[0]/365)*weight
    return math.sqrt(variance/(target/365))


def _skew_fields(target, values):
    atm, p25, c25, p10, c10 = (values[name] for name in ("atm", "put25", "call25", "put10", "call10"))
    valid25, valid10 = all(value != "" for value in (atm, p25, c25)), all(value != "" for value in (atm, p10, c10))
    return {f"atm_iv_{target}d": atm, f"put25_iv_{target}d": p25, f"call25_iv_{target}d": c25,
            f"put10_iv_{target}d": p10, f"call10_iv_{target}d": c10,
            f"put_skew25_{target}d": p25-atm if valid25 else "", f"rr25_{target}d": c25-p25 if valid25 else "",
            f"bf25_{target}d": (p25+c25)/2-atm if valid25 else "",
            f"put_skew10_{target}d": p10-atm if valid10 else "", f"rr10_{target}d": c10-p10 if valid10 else ""}
