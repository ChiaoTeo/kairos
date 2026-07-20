from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import statistics

from kairos import __version__
from studies.btc_options_stats import block_bootstrap_ci
from kairos.data import DatasetClient
from kairos.data.products import BTC_DERIBIT_OPTION_TRADES, BTC_SPOT_DAILY
from kairos.storage.data_lake import write_json


def execute(root: str | Path = "data", hedge_cost_bps=7.0):
    root=Path(root);repository=DatasetClient(root)
    base=json.loads((root/"studies"/"btc_deribit_skew_spread_trade_proxy_v1"/"trades.json").read_text())
    spot={date.fromisoformat(row["period_start"][:10]):float(row["close"]) for row in repository.load_rows(BTC_SPOT_DAILY.product)}
    wanted={trade[leg] for trade in base for leg in ("short","long")}; windows={}
    for trade in base:
        start,end=date.fromisoformat(trade["entry_date"]),date.fromisoformat(trade["exit_date"])
        for instrument in (trade["short"],trade["long"]): windows.setdefault(instrument,[]).append((start,end))
    observations={}
    for row in repository.iter_rows(BTC_DERIBIT_OPTION_TRADES.product):
        instrument=row["instrument_id"]
        if instrument not in wanted:continue
        day=date.fromisoformat(row["event_time"][:10])
        if not any(start<=day<=end for start,end in windows[instrument]):continue
        try:iv=float(row["trade_iv"]);amount=max(float(row["amount_btc"]),.0001)
        except (ValueError,TypeError):continue
        observations.setdefault((instrument,day),[]).append((iv,amount))
    ivs={key:_weighted(values) for key,values in observations.items()}
    details=[]
    for trade in base:
        start,end=date.fromisoformat(trade["entry_date"]),date.fromisoformat(trade["exit_date"])
        days=[start+timedelta(days=offset) for offset in range((end-start).days+1)]
        short_expiry,short_strike=_contract(trade["short"]);long_expiry,long_strike=_contract(trade["long"])
        last_short=last_long=None;stale_days=0;hedge_position=0;hedge_pnl=0;hedge_cost=0;path=[];previous_spot=None
        for index,day in enumerate(days):
            if day not in spot:continue
            current_spot=spot[day]
            if previous_spot is not None:hedge_pnl+=hedge_position*(current_spot-previous_spot)
            short_iv=ivs.get((trade["short"],day));long_iv=ivs.get((trade["long"],day))
            if short_iv is None:short_iv=last_short;stale_days+=1
            if long_iv is None:long_iv=last_long;stale_days+=1
            if short_iv is None or long_iv is None:
                previous_spot=current_spot;continue
            last_short,last_long=short_iv,long_iv
            if day==end:new_hedge=0.0;net_delta=0.0
            else:
                short_delta=_put_delta(current_spot,short_strike,short_expiry,day,short_iv)
                long_delta=_put_delta(current_spot,long_strike,long_expiry,day,long_iv)
                net_delta=-short_delta+long_delta;new_hedge=-net_delta
            turnover=abs(new_hedge-hedge_position);cost=turnover*current_spot*hedge_cost_bps/10000
            hedge_cost+=cost;hedge_position=new_hedge
            path.append({"date":day.isoformat(),"spot":current_spot,"short_iv":short_iv,"long_iv":long_iv,
                "option_net_delta":net_delta,"futures_position":hedge_position,"turnover_btc":turnover,"hedge_cost_usd":cost})
            previous_spot=current_spot
        hedged=trade["pnl_usd"]+hedge_pnl-hedge_cost
        details.append({**trade,"unhedged_pnl_usd":trade["pnl_usd"],"futures_hedge_pnl_usd":hedge_pnl,
            "hedge_cost_usd":hedge_cost,"hedged_pnl_usd":hedged,"stale_iv_leg_days":stale_days,"delta_path":path})
    unhedged=[item["unhedged_pnl_usd"] for item in details];hedged=[item["hedged_pnl_usd"] for item in details]
    difference=[after-before for before,after in zip(unhedged,hedged)];ci=block_bootstrap_ci(hedged,max(2,int(math.sqrt(len(hedged)))),seed=20261015)
    down=[item for item in details if item["delta_path"][-1]["spot"]<item["delta_path"][0]["spot"]]
    up=[item for item in details if item["delta_path"][-1]["spot"]>=item["delta_path"][0]["spot"]]
    result={"study_id":"btc_deribit_skew_spread_daily_delta_hedged_v1","trades":len(details),"hedge_frequency":"daily close",
        "hedge_instrument_proxy":"BTC linear futures/perpetual","hedge_cost_bps":hedge_cost_bps,
        "mean_unhedged_pnl_usd":statistics.fmean(unhedged),"mean_hedged_pnl_usd":statistics.fmean(hedged),
        "mean_hedging_improvement_usd":statistics.fmean(difference),"total_futures_hedge_pnl_usd":sum(item["futures_hedge_pnl_usd"] for item in details),
        "total_hedge_cost_usd":sum(item["hedge_cost_usd"] for item in details),"hedged_win_rate":sum(value>0 for value in hedged)/len(hedged),
        "zero_hedge_cost_mean_pnl_usd":statistics.fmean(item["hedged_pnl_usd"]+item["hedge_cost_usd"] for item in details),
        "spot_down_subsample":_group(down),"spot_up_subsample":_group(up),
        "stale_iv_leg_days":sum(item["stale_iv_leg_days"] for item in details),
        "hedged_block_bootstrap_95_ci":ci,"status":"TRADE_PROXY_TESTED" if len(details)>=30 else "DATA_NOT_READY",
        "limitations":["daily rather than intraday rehedging","funding not included","trade-IV carry-forward on missing days","spot close used as futures proxy"]}
    return details,result


def _group(items):
    return {"trades":len(items),"mean_unhedged_pnl_usd":statistics.fmean(item["unhedged_pnl_usd"] for item in items),
        "mean_hedged_pnl_usd":statistics.fmean(item["hedged_pnl_usd"] for item in items),
        "mean_futures_hedge_pnl_usd":statistics.fmean(item["futures_hedge_pnl_usd"] for item in items)} if items else {"trades":0}


def _put_delta(forward,strike,expiry,day,iv):
    as_of=datetime.combine(day+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc);years=max((expiry-as_of).total_seconds()/(365*86400),1e-8)
    d1=(math.log(forward/strike)+.5*iv*iv*years)/(iv*math.sqrt(years));return .5*(1+math.erf(d1/math.sqrt(2)))-1


def _contract(name):
    _,expiry,strike,_=name.split("-");return datetime.strptime(expiry,"%d%b%y").replace(hour=8,tzinfo=timezone.utc),float(strike)


def _weighted(values):
    ordered=sorted(values);total=sum(weight for _,weight in ordered);cumulative=0
    for value,weight in ordered:
        cumulative+=weight
        if cumulative>=total/2:return value
    return ordered[-1][0]


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--data-root",type=Path,default=Path("data"));parser.add_argument("--hedge-cost-bps",type=float,default=7);args=parser.parse_args(argv)
    details,result=execute(args.data_root,args.hedge_cost_bps);output=args.data_root/"studies"/"btc_deribit_skew_spread_daily_delta_hedged_v1";output.mkdir(parents=True,exist_ok=True)
    DatasetClient(args.data_root).freeze_products(output/"data_snapshot.json", "btc_deribit_skew_spread_daily_delta_hedged_v1",
        (BTC_SPOT_DAILY.product, BTC_DERIBIT_OPTION_TRADES.product), code_version=__version__)
    write_json(output/"trades.json",details);write_json(output/"results.json",result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
