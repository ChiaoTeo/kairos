from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics

from trading.data import CanonicalDatasetRepository, DataCatalog
from trading.storage.data_lake import write_json


def prepare_study_panel(rows, development_fraction=0.70, percentile=0.80):
    panel = [{key: _number(value) for key, value in row.items()} for row in rows]
    closes = [row["spot_close"] for row in panel]
    returns = [math.nan] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    horizon = 7
    for index, row in enumerate(panel):
        future = returns[index + 1:index + horizon + 1]
        future_rv = statistics.stdev(future) * math.sqrt(365) * 100 if len(future) == horizon else math.nan
        row["future_rv7"] = future_rv
        row["future_variance_edge"] = row["dvol_close"] - future_rv if _finite(future_rv) else math.nan
        row["future_dvol_change7"] = panel[index + horizon]["dvol_close"] - row["dvol_close"] if index + horizon < len(panel) else math.nan
    eligible = [row for row in panel if _finite(row["iv_rv_spread"]) and _finite(row["future_variance_edge"])]
    split = int(len(eligible) * development_fraction)
    development, test = eligible[:split], eligible[split:]
    threshold = _percentile([row["iv_rv_spread"] for row in development], percentile)
    boundary = development[-1]["period_start"]
    for row in panel:
        row["sample"] = "development" if row["period_start"] <= boundary else "test"
        row["high_premium"] = int(_finite(row["iv_rv_spread"]) and row["iv_rv_spread"] >= threshold)
    return panel, threshold, development, [row for row in test if row["iv_rv_spread"] >= threshold]


def analyze(rows, threshold, development, high_test, seed=20260714):
    test = [row for row in rows if row["sample"] == "test" and _finite(row["future_variance_edge"])]
    edge = [row["future_variance_edge"] for row in high_test]
    change = [row["future_dvol_change7"] for row in high_test]
    edge_ci, change_ci = _block_bootstrap_ci(edge, seed=seed), _block_bootstrap_ci(change, seed=seed + 1)
    enough = len(high_test) >= 20
    return {"data": {"feature_window": {"start": rows[0]["period_start"], "end": rows[-1]["period_end"], "boundary": "[start,end)"},
                     "label_complete_end": test[-1]["period_start"] if test else None, "observations": len(rows),
                     "development_observations": len(development), "test_observations": len(test),
                     "feature_dataset_id": DataCatalog.BTC_IV_RV_DAILY.dataset_id},
            "pre_registered_rule": {"rv_lookback_days": 30, "forecast_horizon_days": 7, "development_fraction": 0.70,
                                    "high_premium_percentile": 0.80, "frozen_threshold_vol_points": threshold},
            "test_results": {"high_premium_observations": len(high_test), "minimum_required_high_premium_observations": 20,
                "mean_future_variance_edge_vol_points": _mean(edge), "mean_future_dvol_change_vol_points": _mean(change),
                "unconditional_future_variance_edge_vol_points": _mean([row["future_variance_edge"] for row in test]),
                "variance_edge_block_bootstrap_95_ci": edge_ci, "dvol_change_block_bootstrap_95_ci": change_ci,
                "conclusion_status": "TESTED" if enough else "INSUFFICIENT_DATA",
                "h1_iv_exceeds_subsequent_realized_vol": bool(enough and edge_ci[0] > 0),
                "h2_high_premium_dvol_mean_reverts": bool(enough and change_ci[1] < 0)},
            "interpretation": "Signal-level evidence only; executable option quotes and hedging costs are not modeled."}


def _number(value):
    if isinstance(value, str) and value.lower() in {"nan", ""}:
        return math.nan if value.lower() == "nan" else value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def _block_bootstrap_ci(values, block=7, samples=4000, seed=0):
    values = [float(value) for value in values if _finite(value)]
    if not values:
        return [math.nan, math.nan]
    rng, n, means = random.Random(seed), len(values), []
    for _ in range(samples):
        draw = []
        while len(draw) < n:
            start = rng.randrange(n); draw.extend(values[(start + offset) % n] for offset in range(block))
        means.append(statistics.fmean(draw[:n]))
    means.sort()
    return [means[int(samples * 0.025)], means[int(samples * 0.975)]]


def _percentile(values, quantile):
    values = sorted(values); position = (len(values) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] if lower == upper else values[lower] * (upper - position) + values[upper] * (position - lower)


def _mean(values):
    values = [value for value in values if _finite(value)]
    return statistics.fmean(values) if values else math.nan


def write_svg(path, rows):
    usable = [row for row in rows if _finite(row["rv30"])]
    width, height, left, top, bottom = 1200, 620, 75, 45, 70
    plot_w, plot_h = width-left-30, height-top-bottom; ymax = max(max(row["dvol_close"], row["rv30"]) for row in usable) * 1.08
    def point(index, value): return left + index*plot_w/max(1, len(usable)-1), top + plot_h*(1-value/ymax)
    def line(key, color):
        points = " ".join(f"{x:.1f},{y:.1f}" for i, row in enumerate(usable) for x, y in [point(i, row[key])])
        return f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{points}"/>'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="#fbfaf7"/><text x="75" y="28" font-family="sans-serif" font-size="20" font-weight="bold">BTC implied vs realized volatility</text><line x1="{left}" y1="{top+plot_h}" x2="{width-30}" y2="{top+plot_h}" stroke="#777"/>{line('dvol_close','#6b4eff')}{line('rv30','#ef7d32')}<text x="85" y="55" fill="#6b4eff">DVOL close</text><text x="190" y="55" fill="#ef7d32">30d realized vol</text></svg>'''
    path.write_text(svg, encoding="utf-8")


def write_report(path, result):
    test, rule, window = result["test_results"], result["pre_registered_rule"], result["data"]["feature_window"]
    fallback = "INSUFFICIENT DATA" if test["conclusion_status"] == "INSUFFICIENT_DATA" else "NOT SUPPORTED"
    verdict1 = "SUPPORTED" if test["h1_iv_exceeds_subsequent_realized_vol"] else fallback
    verdict2 = "SUPPORTED" if test["h2_high_premium_dvol_mean_reverts"] else fallback
    path.write_text(f"""# BTC 期权波动率风险溢价：冻结样本外检验

输入数据集：`{result['data']['feature_dataset_id']}`。数据窗口为 `{window['start']}` 至 `{window['end']}`，边界 `{window['boundary']}`；开发集 80% 分位阈值为 {rule['frozen_threshold_vol_points']:.2f}。

1. IV 高于未来 7 日 RV：**{verdict1}**。
2. 高溢价后的 DVOL 均值回归：**{verdict2}**。

测试集高溢价样本 {test['high_premium_observations']} 个，最低要求 {test['minimum_required_high_premium_observations']} 个，总体结论：**{test['conclusion_status']}**。

这是信号层验证，不是包含 bid/ask、动态对冲和费用的可交易 PnL。
""", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="BTC VRP study over a governed feature dataset")
    parser.add_argument("--data-root", type=Path, default=Path("data")); args = parser.parse_args(argv)
    repository = CanonicalDatasetRepository(args.data_root)
    rows = repository.load_rows(DataCatalog.BTC_IV_RV_DAILY.dataset_id)
    panel, threshold, development, high_test = prepare_study_panel(rows)
    result = analyze(panel, threshold, development, high_test)
    output = args.data_root / "studies" / "btc_options_vrp_v1"; output.mkdir(parents=True, exist_ok=True)
    write_json(output / "study_spec.json", {"study_id": "btc_options_vrp_v1", "feature_dataset_id": DataCatalog.BTC_IV_RV_DAILY.dataset_id,
               "feature_window": result["data"]["feature_window"], "label_horizon": "P7D", "warmup": "P30D", "test_embargo": "P7D"})
    write_json(output / "results.json", result); write_svg(output / "btc_dvol_vs_rv.svg", panel); write_report(output / "REPORT.md", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
