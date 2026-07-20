from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

from kairospy import __version__
from kairospy.data import DatasetClient
from kairospy.data.products import BTC_DERIBIT_TERM_SKEW_DAILY, BTC_TERM_SKEW_HOURLY
from kairospy.storage.data_lake import write_json


def execute(root: str | Path = "data"):
    repository = DatasetClient(root)
    deribit = {row["period_start"][:10]: _float(row.get("put_skew25_30d"))
               for row in repository.load_rows(BTC_DERIBIT_TERM_SKEW_DAILY.product)}
    hourly = {}
    for row in repository.load_rows(BTC_TERM_SKEW_HOURLY.product):
        value = _float(row.get("put_skew25_30d"))
        if math.isfinite(value): hourly.setdefault(row["period_start"][:10], []).append(value)
    binance = {day: statistics.median(values) for day, values in hourly.items()}
    pairs = [(deribit[day], binance[day]) for day in sorted(set(deribit)&set(binance)) if math.isfinite(deribit[day])]
    differences = [left-right for left, right in pairs]
    ready = len(pairs) >= 90
    return {"study_id": "btc_skew_cross_validation_v1", "metric": "30D 25delta put IV minus ATM IV",
        "deribit_estimator": "daily trade-weighted smile", "binance_estimator": "median of hourly EOHSummary smiles",
        "paired_days": len(pairs), "minimum_required": 90, "pearson_correlation": _correlation(pairs),
        "mean_deribit_skew": statistics.fmean(left for left, _ in pairs) if pairs else math.nan,
        "mean_binance_skew": statistics.fmean(right for _, right in pairs) if pairs else math.nan,
        "mean_difference": statistics.fmean(differences) if differences else math.nan,
        "mean_absolute_difference": statistics.fmean(abs(value) for value in differences) if differences else math.nan,
        "status": "TESTED" if ready else "DATA_NOT_READY"}


def _correlation(pairs):
    if len(pairs) < 3: return math.nan
    left, right = [x for x,_ in pairs], [y for _,y in pairs]; lm, rm = statistics.fmean(left), statistics.fmean(right)
    numerator=sum((x-lm)*(y-rm) for x,y in pairs); denominator=(sum((x-lm)**2 for x in left)*sum((y-rm)**2 for y in right))**.5
    return numerator/denominator if denominator else math.nan


def _float(value):
    try: return float(value)
    except (TypeError,ValueError): return math.nan


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--data-root",type=Path,default=Path("data")); args=parser.parse_args(argv)
    result=execute(args.data_root); output=args.data_root/"studies"/"btc_skew_cross_validation_v1"; output.mkdir(parents=True,exist_ok=True)
    DatasetClient(args.data_root).freeze_products(output/"data_snapshot.json", "btc_skew_cross_validation_v1",
        (BTC_DERIBIT_TERM_SKEW_DAILY.product, BTC_TERM_SKEW_HOURLY.product), code_version=__version__)
    write_json(output/"results.json",result); print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__": main()
