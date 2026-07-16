from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import statistics

from research.btc_options_stats import block_bootstrap_ci, percentile
from trading.data import CanonicalDatasetRepository, DataCatalog
from trading.storage.data_lake import write_json


def execute(root: str | Path = "data", target_dte=30, holding_days=7, lookback=60, commission=1.0):
    repository = CanonicalDatasetRepository(root)
    quotes = repository.load_rows(DataCatalog.BTC_OPTION_QUOTES_HOURLY.dataset_id)
    features = repository.load_rows(DataCatalog.BTC_TERM_SKEW_HOURLY.dataset_id)
    books = {}
    for quote in quotes:
        books.setdefault(quote["period_start"], {})[quote["instrument_id"]] = quote
    daily = [row for row in features if row["period_start"][11:13] == "00" and _float(row.get("put_skew25_30d")) is not None]
    trades = []
    for index in range(lookback, len(daily)):
        history = [_float(row.get("put_skew25_30d")) for row in daily[index-lookback:index]]
        history = [value for value in history if value is not None]
        current = _float(daily[index].get("put_skew25_30d"))
        if len(history) < lookback*.8 or current < percentile(history, .80):
            continue
        timestamp = daily[index]["period_start"]; book = books.get(timestamp, {})
        expiry = _target_expiry(book.values(), timestamp, target_dte)
        puts = [quote for quote in book.values() if quote["option_right"] == "put" and quote["expiry"] == expiry]
        short = _delta_contract(puts, -.25); long = _delta_contract(puts, -.10)
        if not short or not long or float(long["strike"]) >= float(short["strike"]):
            continue
        short_bid, long_ask = _positive(short.get("best_bid_price")), _positive(long.get("best_ask_price"))
        exit_time = (datetime.fromisoformat(timestamp.replace("Z", "+00:00"))+timedelta(days=holding_days)).isoformat().replace("+00:00", "Z")
        exit_book = books.get(exit_time, {}); short_exit, long_exit = exit_book.get(short["instrument_id"]), exit_book.get(long["instrument_id"])
        if None in (short_bid, long_ask) or not short_exit or not long_exit:
            continue
        short_ask, long_bid = _positive(short_exit.get("best_ask_price")), _nonnegative(long_exit.get("best_bid_price"))
        if short_ask is None or long_bid is None:
            continue
        credit, debit = short_bid-long_ask, short_ask-long_bid
        if credit <= 0:
            continue
        pnl = credit-debit-commission*4
        width = float(short["strike"])-float(long["strike"])
        trades.append({"entry_time": timestamp, "exit_time": exit_time, "short_instrument": short["instrument_id"],
            "long_instrument": long["instrument_id"], "entry_credit": credit, "exit_debit": debit,
            "commission": commission*4, "pnl": pnl, "maximum_risk": width-credit, "skew": current})
    pnl = [trade["pnl"] for trade in trades]; ci = block_bootstrap_ci(pnl, 7, seed=20260901)
    ready = len({trade["entry_time"][:10] for trade in trades}) >= 20 and len(daily) >= 252
    result = {"study_id": "btc_skew_spread_backtest_v1", "execution": "short bid / long ask entry; short ask / long bid exit",
        "holding_days": holding_days, "trades": len(trades), "mean_pnl_usdt": statistics.fmean(pnl) if pnl else math.nan,
        "total_pnl_usdt": sum(pnl), "win_rate": sum(value > 0 for value in pnl)/len(pnl) if pnl else math.nan,
        "block_bootstrap_95_ci": ci, "status": "TESTED" if ready else "DATA_NOT_READY",
        "limitation": "Binance EOHSummary public archive spans only 2023-05-18 to 2023-10-23"}
    return trades, result


def _target_expiry(quotes, timestamp, target):
    as_of=datetime.fromisoformat(timestamp.replace("Z","+00:00")); expiries={quote["expiry"] for quote in quotes}
    return min(expiries,key=lambda value:abs((datetime.fromisoformat(value.replace("Z","+00:00"))-as_of).total_seconds()/86400-target),default=None)


def _delta_contract(quotes, target):
    valid=[quote for quote in quotes if _float(quote.get("vendor_delta")) is not None]
    return min(valid,key=lambda quote:abs(float(quote["vendor_delta"])-target),default=None)


def _float(value):
    try: return float(value) if value != "" else None
    except (TypeError,ValueError): return None


def _positive(value):
    value=_float(value); return value if value is not None and value>0 else None


def _nonnegative(value):
    value=_float(value); return value if value is not None and value>=0 else None


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--data-root",type=Path,default=Path("data")); args=parser.parse_args(argv)
    trades,result=execute(args.data_root); output=args.data_root/"studies"/"btc_skew_spread_backtest_v1"; output.mkdir(parents=True,exist_ok=True)
    write_json(output/"study_spec.json", {"study_id":"btc_skew_spread_backtest_v1","short_delta":-.25,"long_delta":-.10,
        "target_dte":30,"holding_days":7,"signal":"30D put skew above trailing 60-day 80th percentile"})
    write_json(output/"trades.json",trades); write_json(output/"results.json",result); print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
