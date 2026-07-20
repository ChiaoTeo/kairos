from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

from studies.btc_options_stats import block_bootstrap_ci
from kairospy.storage.data_lake import write_json


THRESHOLDS=(0.0,.03,.05,.10,.15,.20,.30)


def execute(root: str | Path="data",cost_bps=7.0):
    root=Path(root);source=root/"studies"/"btc_deribit_skew_spread_daily_delta_hedged_v1"/"trades.json"
    trades=json.loads(source.read_text());grid=[];details={}
    for threshold in THRESHOLDS:
        cases=[]
        for trade in trades:
            path=trade["delta_path"];position=0.0;previous=None;futures_pnl=0.0;cost=0.0;turnover=0.0;adjustments=0
            for index,row in enumerate(path):
                spot=float(row["spot"])
                if previous is not None:futures_pnl+=position*(spot-previous)
                if index==len(path)-1:new_position=0.0
                else:
                    net=float(row["option_net_delta"]);new_position=-net if abs(net+position)>threshold else position
                change=abs(new_position-position)
                if change>1e-12:adjustments+=1
                turnover+=change;cost+=change*spot*cost_bps/10000;position=new_position;previous=spot
            pnl=trade["unhedged_pnl_usd"]+futures_pnl-cost
            cases.append({"entry_date":trade["entry_date"],"exit_date":trade["exit_date"],"spot_return":path[-1]["spot"]/path[0]["spot"]-1,
                "unhedged_pnl_usd":trade["unhedged_pnl_usd"],"futures_pnl_usd":futures_pnl,"hedge_cost_usd":cost,
                "hedged_pnl_usd":pnl,"turnover_btc":turnover,"adjustments":adjustments})
        pnl=[item["hedged_pnl_usd"] for item in cases];ci=block_bootstrap_ci(pnl,max(2,int(math.sqrt(len(pnl)))),seed=20261100+int(threshold*100))
        row={"delta_threshold_btc":threshold,"trades":len(cases),"mean_pnl_usd":statistics.fmean(pnl),"total_pnl_usd":sum(pnl),
            "win_rate":sum(value>0 for value in pnl)/len(pnl),"total_hedge_cost_usd":sum(item["hedge_cost_usd"] for item in cases),
            "mean_turnover_btc":statistics.fmean(item["turnover_btc"] for item in cases),
            "mean_adjustments":statistics.fmean(item["adjustments"] for item in cases),"worst_trade_pnl_usd":min(pnl),
            "expected_shortfall_20pct_usd":statistics.fmean(sorted(pnl)[:max(1,math.ceil(len(pnl)*.20))]),
            "block_bootstrap_95_ci":ci}
        grid.append(row);details[str(threshold)]=cases
    best=max(grid,key=lambda row:row["mean_pnl_usd"])
    result={"study_id":"btc_deribit_skew_spread_delta_threshold_sensitivity_v1","cost_bps":cost_bps,"thresholds":grid,
        "best_in_sample_threshold_btc":best["delta_threshold_btc"],"best_in_sample_mean_pnl_usd":best["mean_pnl_usd"],
        "conclusion_status":"EXPLORATORY_ONLY","warning":"Thresholds were compared on the same test trades; the best threshold is not out-of-sample evidence."}
    return details,result


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument("--data-root",type=Path,default=Path("data"));parser.add_argument("--cost-bps",type=float,default=7);args=parser.parse_args(argv)
    details,result=execute(args.data_root,args.cost_bps);output=args.data_root/"studies"/"btc_deribit_skew_spread_delta_threshold_sensitivity_v1";output.mkdir(parents=True,exist_ok=True)
    write_json(output/"trades_by_threshold.json",details);write_json(output/"results.json",result);print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
