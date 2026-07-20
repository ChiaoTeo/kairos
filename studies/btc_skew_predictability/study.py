from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

from kairos import __version__
from studies.btc_options_stats import block_bootstrap_ci, percentile
from kairos.data import DatasetClient
from kairos.data.products import BTC_DERIBIT_TERM_SKEW_DAILY, BTC_SPOT_DAILY
from kairos.storage.data_lake import write_json


HORIZONS = (7, 14, 30, 60, 90)


def execute(root: str | Path = "data"):
    repository = DatasetClient(root)
    rows = repository.load_rows(BTC_DERIBIT_TERM_SKEW_DAILY.product)
    spot = {row["period_start"][:10]: float(row["close"]) for row in repository.load_rows(BTC_SPOT_DAILY.product)}
    rows = sorted(rows, key=lambda row: row["period_start"]); split = int(len(rows)*0.70); development, test = rows[:split], rows[split:]
    results = {}
    for horizon in HORIZONS:
        key = f"put_skew25_{horizon}d"; dev_values = [_float(row.get(key)) for row in development]
        valid_dev = [value for value in dev_values if math.isfinite(value)]
        threshold = percentile(valid_dev, 0.80) if valid_dev else math.nan
        observations, returns, drawdowns = [], [], []
        for index in range(split, len(rows)-horizon):
            current, future = _float(rows[index].get(key)), _float(rows[index+horizon].get(key))
            if math.isfinite(current) and math.isfinite(future) and current >= threshold:
                observations.append(future-current)
                dates = [rows[offset]["period_start"][:10] for offset in range(index, index+horizon+1)]
                prices = [spot.get(day) for day in dates]
                if all(price is not None for price in prices):
                    returns.append(prices[-1]/prices[0]-1)
                    drawdowns.append(min(price/prices[0]-1 for price in prices[1:]))
        event_block = min(horizon, max(2, int(math.sqrt(len(observations))))) if observations else 1
        ci = block_bootstrap_ci(observations, event_block, seed=20260800+horizon)
        enough = len(observations) >= 20
        results[f"{horizon}d"] = {"development_80pct_threshold": threshold, "high_skew_test_observations": len(observations),
            "minimum_required": 20, "mean_future_skew_change": statistics.fmean(observations) if observations else math.nan,
            "event_block_length": event_block, "block_bootstrap_95_ci": ci,
            "mean_future_spot_return": statistics.fmean(returns) if returns else math.nan,
            "mean_future_max_drawdown": statistics.fmean(drawdowns) if drawdowns else math.nan,
            "h1_high_skew_mean_reverts": bool(enough and ci[1] < 0),
            "status": "TESTED" if enough else "DATA_NOT_READY"}
    ready = all(item["status"] == "TESTED" for item in results.values())
    return {"study_id": "btc_skew_predictability_v1",
            "input_dataset": repository.catalog.release(BTC_DERIBIT_TERM_SKEW_DAILY.key).release_id,
            "metric": "25delta put IV minus ATM IV", "horizons": results,
            "conclusion_status": "TESTED" if ready else "DATA_NOT_READY"}


def _float(value):
    try: return float(value)
    except (TypeError, ValueError): return math.nan


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--data-root",type=Path,default=Path("data")); args=parser.parse_args(argv)
    result=execute(args.data_root); output=args.data_root/"studies"/"btc_skew_predictability_v1"; output.mkdir(parents=True,exist_ok=True)
    DatasetClient(args.data_root).freeze_products(output/"data_snapshot.json", "btc_skew_predictability_v1",
        (BTC_DERIBIT_TERM_SKEW_DAILY.product, BTC_SPOT_DAILY.product), code_version=__version__)
    write_json(output/"study_spec.json", {"study_id":"btc_skew_predictability_v1","metric":"put_skew25","horizons":list(HORIZONS),
        "high_skew_threshold":"development 80th percentile","minimum_test_observations":20})
    write_json(output/"results.json",result); print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
