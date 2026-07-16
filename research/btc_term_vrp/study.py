from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import statistics

from research.btc_options_stats import block_bootstrap_ci, hac_mean_t
from trading.data import CanonicalDatasetRepository, DataCatalog
from trading.storage.data_lake import write_json


HORIZONS = (7, 14, 30, 60, 90)


def execute(root: str | Path = "data"):
    repository = CanonicalDatasetRepository(root)
    surface = repository.load_rows(DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id)
    spot = repository.load_rows(DataCatalog.BTC_SPOT_DAILY.dataset_id)
    closes = {row["period_start"][:10]: float(row["close"]) for row in spot}
    rows = sorted(surface, key=lambda row: row["period_start"])
    dates = [row["period_start"][:10] for row in rows]
    panel = []
    for index, row in enumerate(rows):
        item = {"date": dates[index]}
        for horizon in HORIZONS:
            iv = _float(row.get(f"atm_iv_{horizon}d"))
            future_dates = dates[index+1:index+horizon+1]
            returns = [math.log(closes[day]/closes[dates[index+j]]) for j, day in enumerate(future_dates, 0)
                       if day in closes and dates[index+j] in closes]
            rv = statistics.stdev(returns)*math.sqrt(365)*100 if len(returns) == horizon and horizon > 1 else math.nan
            iv_percent = iv*100 if math.isfinite(iv) else math.nan
            item[f"iv_{horizon}d"] = iv_percent; item[f"future_rv_{horizon}d"] = rv
            item[f"vrp_{horizon}d"] = iv_percent-rv if math.isfinite(iv_percent) and math.isfinite(rv) else math.nan
            item[f"variance_vrp_{horizon}d"] = (iv_percent/100)**2-(rv/100)**2 if math.isfinite(iv_percent) and math.isfinite(rv) else math.nan
        panel.append(item)
    return panel, summarize(panel)


def summarize(panel):
    split = int(len(panel)*0.70); test = panel[split:]
    results = {}
    for horizon in HORIZONS:
        usable = [row for row in test if math.isfinite(row[f"vrp_{horizon}d"])]
        vrp = [row[f"vrp_{horizon}d"] for row in usable]; variance = [row[f"variance_vrp_{horizon}d"] for row in usable]
        ci = block_bootstrap_ci(vrp, horizon, seed=20260715+horizon)
        enough = len(usable) >= 50
        results[f"{horizon}d"] = {"observations": len(usable), "minimum_required": 50,
            "mean_atm_iv_percent": _mean([row[f"iv_{horizon}d"] for row in usable]),
            "mean_forward_rv_percent": _mean([row[f"future_rv_{horizon}d"] for row in usable]),
            "mean_vrp_vol_points": _mean(vrp), "mean_variance_risk_premium": _mean(variance),
            "iv_above_rv_fraction": sum(value > 0 for value in vrp)/len(vrp) if vrp else math.nan,
            "block_bootstrap_95_ci": ci, "newey_west_t": hac_mean_t(vrp, horizon-1),
            "h1_iv_exceeds_forward_rv": bool(enough and ci[0] > 0),
            "status": "TESTED" if enough else "DATA_NOT_READY"}
    ready = all(item["status"] == "TESTED" for item in results.values())
    return {"study_id": "btc_term_vrp_v1", "input_dataset": DataCatalog.BTC_DERIBIT_TERM_SKEW_DAILY.dataset_id,
            "split": {"method": "chronological", "development_fraction": 0.70},
            "horizons": results, "conclusion_status": "TESTED" if ready else "DATA_NOT_READY"}


def _float(value):
    try: return float(value)
    except (TypeError, ValueError): return math.nan


def _mean(values):
    values = [value for value in values if math.isfinite(value)]
    return statistics.fmean(values) if values else math.nan


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--data-root", type=Path, default=Path("data")); args = parser.parse_args(argv)
    panel, result = execute(args.data_root); output = args.data_root/"studies"/"btc_term_vrp_v1"; output.mkdir(parents=True, exist_ok=True)
    write_json(output/"study_spec.json", {"study_id": "btc_term_vrp_v1", "horizons": list(HORIZONS),
        "hypothesis": "fixed-maturity ATM IV exceeds same-horizon forward realized volatility", "test_fraction": 0.30})
    write_json(output/"results.json", result); print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
