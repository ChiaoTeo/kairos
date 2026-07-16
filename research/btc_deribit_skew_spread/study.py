from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import statistics

from research.btc_options_stats import block_bootstrap_ci, percentile
from trading.data import CanonicalDatasetRepository, DataCatalog
from trading.storage.data_lake import write_json


def execute(root: str | Path = "data", holding_days=7, commission_per_leg_usd=5.0):
    repository=CanonicalDatasetRepository(root)
    features=sorted(repository.load_rows(DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id),key=lambda row:row["period_start"])
    split=int(len(features)*.70); threshold=percentile([_float(row["put_skew25_30d"]) for row in features[:split]],.80)
    signals=[]; last_exit=None
    for row in features[split:]:
        day=date.fromisoformat(row["period_start"][:10]); skew=_float(row["put_skew25_30d"])
        if skew>=threshold and (last_exit is None or day>last_exit):
            signals.append((day,day+timedelta(days=holding_days),skew)); last_exit=day+timedelta(days=holding_days)
    needed={day for entry,exit_,_ in signals for day in (entry,exit_)}; daily={day:[] for day in needed}
    for trade in repository.iter_rows(DataCatalog.BTC_DERIBIT_OPTION_TRADES.dataset_id):
        day=date.fromisoformat(trade["event_time"][:10])
        if day in daily: daily[day].append(trade)
    trades=[]
    for entry,exit_,skew in signals:
        entry_book=_contracts(daily.get(entry,[]),entry); exit_book=_contracts(daily.get(exit_,[]),exit_)
        expiry=_target_expiry(entry_book,entry,30)
        candidates=[item for item in entry_book.values() if item["right"]=="put" and item["expiry"]==expiry]
        short=_nearest(candidates,-.25,"sell"); long=_nearest(candidates,-.10,"buy")
        if not short or not long or long["strike"]>=short["strike"]: continue
        short_exit=exit_book.get(short["instrument"]); long_exit=exit_book.get(long["instrument"])
        if not short_exit or not long_exit or not short_exit["buy"] or not long_exit["sell"]: continue
        credit=short["sell"]-long["buy"]; debit=short_exit["buy"]-long_exit["sell"]
        if credit<=0: continue
        pnl=credit-debit-commission_per_leg_usd*4
        trades.append({"entry_date":entry.isoformat(),"exit_date":exit_.isoformat(),"short":short["instrument"],"long":long["instrument"],
            "entry_credit_usd":credit,"exit_debit_usd":debit,"commission_usd":commission_per_leg_usd*4,"pnl_usd":pnl,"signal_skew":skew})
    pnl=[trade["pnl_usd"] for trade in trades]; ci=block_bootstrap_ci(pnl, max(2,int(math.sqrt(len(pnl)))) if pnl else 1,seed=20261001)
    result={"study_id":"btc_deribit_skew_spread_trade_proxy_v1","pricing":"direction-filtered daily trade-price proxy",
        "development_80pct_threshold":threshold,"signals":len(signals),"completed_trades":len(trades),
        "mean_pnl_usd":statistics.fmean(pnl) if pnl else math.nan,"total_pnl_usd":sum(pnl),
        "win_rate":sum(value>0 for value in pnl)/len(pnl) if pnl else math.nan,"block_bootstrap_95_ci":ci,
        "status":"TRADE_PROXY_TESTED" if len(trades)>=30 else "DATA_NOT_READY",
        "limitations":["not synchronous bid/ask","only contracts that traded are observable","daily aggregation","index price converts BTC premium to USD"]}
    return trades,result


def _contracts(rows, day):
    grouped={}
    for row in rows:
        try:
            expiry=datetime.fromisoformat(row["expiry"].replace("Z","+00:00")).date(); iv=float(row["trade_iv"]); strike=float(row["strike"]); forward=float(row["index_price_usd"])
            timestamp=datetime.fromisoformat(row["event_time"].replace("Z","+00:00")); years=(datetime.fromisoformat(row["expiry"].replace("Z","+00:00"))-timestamp).total_seconds()/(365*86400)
            if years<=0: continue
            d1=(math.log(forward/strike)+.5*iv*iv*years)/(iv*math.sqrt(years)); call=.5*(1+math.erf(d1/math.sqrt(2)))
            delta=call if row["option_right"]=="call" else call-1; usd=float(row["price_btc"])*forward; amount=max(float(row["amount_btc"]),.0001)
        except (ValueError,TypeError,KeyError): continue
        item=grouped.setdefault(row["instrument_id"],{"instrument":row["instrument_id"],"expiry":expiry,"right":row["option_right"],"strike":strike,"deltas":[],"buy_prices":[],"sell_prices":[]})
        item["deltas"].append((delta,amount)); item[f"{row['direction']}_prices"].append((usd,amount))
    for item in grouped.values():
        item["delta"]=_weighted(item["deltas"]); item["buy"]=_weighted(item["buy_prices"]); item["sell"]=_weighted(item["sell_prices"])
    return grouped


def _weighted(values):
    if not values:return None
    ordered=sorted(values); total=sum(weight for _,weight in ordered); cumulative=0
    for value,weight in ordered:
        cumulative+=weight
        if cumulative>=total/2:return value
    return ordered[-1][0]


def _target_expiry(book,day,target):
    expiries={item["expiry"] for item in book.values() if item["expiry"]>day}
    return min(expiries,key=lambda expiry:abs((expiry-day).days-target),default=None)


def _nearest(items,target,direction):
    valid=[item for item in items if item[direction] is not None]
    return min(valid,key=lambda item:abs(item["delta"]-target),default=None)


def _float(value):
    try:return float(value)
    except (TypeError,ValueError):return math.nan


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--data-root",type=Path,default=Path("data"));args=parser.parse_args(argv)
    trades,result=execute(args.data_root);output=args.data_root/"studies"/"btc_deribit_skew_spread_trade_proxy_v1";output.mkdir(parents=True,exist_ok=True)
    write_json(output/"trades.json",trades);write_json(output/"results.json",result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
