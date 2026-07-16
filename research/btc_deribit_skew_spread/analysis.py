from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import math
from pathlib import Path
import statistics

from trading.data import CanonicalDatasetRepository, DataCatalog
from trading.storage.data_lake import write_json


def analyze(root: str | Path = "data"):
    root=Path(root); repository=CanonicalDatasetRepository(root)
    trades=json.loads((root/"studies"/"btc_deribit_skew_spread_trade_proxy_v1"/"trades.json").read_text())
    spot={row["period_start"][:10]:float(row["close"]) for row in repository.load_rows(DataCatalog.BTC_SPOT_DAILY.dataset_id)}
    features={row["period_start"][:10]:row for row in repository.load_rows(DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id)}
    details=[]
    for trade in trades:
        entry,exit_=trade["entry_date"],trade["exit_date"]; days=_days(date.fromisoformat(entry),date.fromisoformat(exit_))
        prices=[spot[day] for day in days if day in spot]; short_strike=float(trade["short"].split("-")[2])
        entry_feature,exit_feature=features[entry],features[exit_]
        gross=trade["entry_credit_usd"]-trade["exit_debit_usd"]
        item={**trade,"gross_pnl_usd":gross,"fee_drag_usd":-trade["commission_usd"],
            "entry_spot":prices[0],"exit_spot":prices[-1],"spot_return":prices[-1]/prices[0]-1,
            "maximum_drawdown":min(price/prices[0]-1 for price in prices),"short_strike":short_strike,
            "short_moneyness_entry":short_strike/prices[0]-1,"short_moneyness_exit":short_strike/prices[-1]-1,
            "short_itm_at_exit":prices[-1]<short_strike,
            "atm_iv_change_30d":float(exit_feature["atm_iv_30d"])-float(entry_feature["atm_iv_30d"]),
            "put_skew_change_30d":float(exit_feature["put_skew25_30d"])-float(entry_feature["put_skew25_30d"])}
        item["dominant_loss_driver"]=_driver(item);details.append(item)
    losses=[item for item in details if item["pnl_usd"]<0]; winners=[item for item in details if item["pnl_usd"]>=0]
    drivers={}
    for item in losses:
        bucket=drivers.setdefault(item["dominant_loss_driver"],{"trades":0,"total_pnl_usd":0});bucket["trades"]+=1;bucket["total_pnl_usd"]+=item["pnl_usd"]
    summary={"study_id":"btc_deribit_skew_spread_loss_attribution_v1","trades":len(details),"losing_trades":len(losses),
        "gross_pnl_usd":sum(item["gross_pnl_usd"] for item in details),"fees_usd":sum(item["commission_usd"] for item in details),
        "net_pnl_usd":sum(item["pnl_usd"] for item in details),"loss_drivers":drivers,
        "loser_means":_means(losses),"winner_means":_means(winners),
        "pnl_correlations":{"spot_return":_correlation(details,"spot_return"),"maximum_drawdown":_correlation(details,"maximum_drawdown"),
            "atm_iv_change_30d":_correlation(details,"atm_iv_change_30d"),"put_skew_change_30d":_correlation(details,"put_skew_change_30d")},
        "worst_trades":sorted(details,key=lambda item:item["pnl_usd"])[:5],
        "limitation":"Empirical factor attribution over daily trade proxies, not an exact Greeks PnL explain."}
    return details,summary


def _driver(item):
    if item["short_itm_at_exit"] or item["spot_return"]<=-.05:return "downside_move_or_short_itm"
    if item["atm_iv_change_30d"]>=.03:return "atm_iv_expansion"
    if item["put_skew_change_30d"]>=.02:return "skew_widening"
    if item["gross_pnl_usd"]>0 and item["pnl_usd"]<0:return "fees"
    return "pricing_residual_or_path"


def _means(items):
    keys=("pnl_usd","spot_return","maximum_drawdown","atm_iv_change_30d","put_skew_change_30d")
    return {key:statistics.fmean(item[key] for item in items) if items else math.nan for key in keys}


def _correlation(items,key):
    if len(items)<3:return math.nan
    x=[item[key] for item in items];y=[item["pnl_usd"] for item in items];xm,ym=statistics.fmean(x),statistics.fmean(y)
    denominator=(sum((v-xm)**2 for v in x)*sum((v-ym)**2 for v in y))**.5
    return sum((a-xm)*(b-ym) for a,b in zip(x,y))/denominator if denominator else math.nan


def _days(start,end):return [(start+timedelta(days=offset)).isoformat() for offset in range((end-start).days+1)]


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--data-root",type=Path,default=Path("data"));args=parser.parse_args(argv)
    details,summary=analyze(args.data_root);output=args.data_root/"studies"/"btc_deribit_skew_spread_trade_proxy_v1"
    write_json(output/"attribution_trades.json",details);write_json(output/"risk_decomposition.json",summary);print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
