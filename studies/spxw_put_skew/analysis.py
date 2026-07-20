from __future__ import annotations

from decimal import Decimal
import json
from math import sqrt

import numpy as np
import pandas as pd

from kairos.backtest.feed import HistoricalDataset
from kairos.reference import ReferenceCatalog
from kairos.domain.product import ListedOptionSpec
from kairos.pricing import ValuationService


def load_study_context(root):
    from kairos.data import DatasetClient
    from studies.spxw_put_skew.study import StudyConfig, execute_study
    study_dir = root / "studies" / "spxw_put_skew"
    raw = json.loads((study_dir / "config.json").read_text(encoding="utf-8"))
    dataset_id = raw.pop("dataset_id")
    decimal_fields = {
        "target_short_delta", "target_long_delta", "high_skew_percentile", "minimum_quote_coverage",
        "maximum_stale_rate", "minimum_surface_calibration_rate", "profit_target",
        "stop_loss_multiple", "commission_per_contract",
    }
    config = StudyConfig(**{key: Decimal(str(value)) if key in decimal_fields else value for key, value in raw.items()})
    data = DatasetClient(root / "data")
    feed = data.replay_slices(dataset_id)
    dataset = feed.dataset
    collection = data.collection(dataset_id)
    panel, readiness, conclusion = execute_study(dataset, config, collection)
    return dataset, config, collection, panel, readiness, conclusion


def data_quality_report(dataset: HistoricalDataset) -> tuple[pd.DataFrame, pd.DataFrame]:
    catalog = _catalog(dataset)
    service = ValuationService(catalog)
    rows = []
    for market in dataset.slices:
        _, valuation = service.value(market)
        quotes = [item.quote for item in market.instruments]
        two_sided = [item for item in quotes if item and item.bid is not None and item.ask is not None]
        crossed = [item for item in two_sided if item.bid > item.ask]
        spreads = [float((item.ask - item.bid) / ((item.ask + item.bid) / 2)) for item in two_sided if item.ask + item.bid > 0]
        rows.append({
            "timestamp": market.timestamp,
            "universe_size": len(market.instrument_universe),
            "quote_count": len([item for item in quotes if item]),
            "two_sided_count": len(two_sided),
            "crossed_count": len(crossed),
            "median_relative_spread": float(np.median(spreads)) if spreads else np.nan,
            "iv_solver_success_rate": sum(item.pricing is not None for item in valuation.instruments) / max(1, len(valuation.instruments)),
            "surface_calibrated": bool(valuation.surface and valuation.surface.calibration_status.value == "calibrated"),
            "surface_arbitrage_passed": bool(valuation.surface and valuation.surface.diagnostics.passed),
            "valuation_failure_count": len(valuation.failures),
            "quality_issue_count": len(market.quality_issues),
        })
    detail = pd.DataFrame(rows)
    summary = pd.DataFrame({
        "metric": [
            "slice_count", "trading_days", "contract_coverage", "quote_coverage", "vendor_greeks_coverage",
            "stale_rate", "mean_iv_solver_success", "surface_calibration_rate", "surface_arbitrage_pass_rate",
            "crossed_quotes", "median_relative_spread",
        ],
        "value": [
            dataset.manifest.slice_count, dataset.manifest.trading_days, float(dataset.manifest.contract_coverage),
            float(dataset.manifest.quote_coverage), float(dataset.manifest.greeks_coverage), float(dataset.manifest.stale_rate),
            detail["iv_solver_success_rate"].mean() if len(detail) else 0.0,
            detail["surface_calibrated"].mean() if len(detail) else 0.0,
            detail["surface_arbitrage_passed"].mean() if len(detail) else 0.0,
            detail["crossed_count"].sum() if len(detail) else 0,
            detail["median_relative_spread"].median() if len(detail) else np.nan,
        ],
    })
    return summary, detail


def surface_observations(dataset: HistoricalDataset) -> pd.DataFrame:
    catalog = _catalog(dataset)
    service = ValuationService(catalog)
    rows = []
    for market in dataset.slices:
        _, valuation = service.value(market)
        for item in valuation.instruments:
            if item.pricing is None or item.implied_vol.volatility is None:
                continue
            definition = catalog.instruments.get(item.instrument_id, market.timestamp)
            spec = definition.contract_spec
            if not isinstance(spec, ListedOptionSpec):
                continue
            rows.append({
                "timestamp": market.timestamp, "expiry": spec.expiry, "dte": (spec.expiry.date() - market.timestamp.date()).days,
                "strike": float(spec.strike), "forward": float(item.inputs.underlying),
                "log_moneyness": float(np.log(float(spec.strike / item.inputs.underlying))),
                "iv": float(item.implied_vol.volatility), "surface_iv": float(item.inputs.volatility),
                "delta": float(item.pricing.delta), "total_variance": float(item.inputs.volatility ** 2 * item.inputs.time_to_expiry),
                "source": item.source,
                "surface_calibrated": bool(valuation.surface and valuation.surface.calibration_status.value == "calibrated"),
                "arbitrage_passed": bool(valuation.surface and valuation.surface.diagnostics.passed),
            })
    return pd.DataFrame(rows)


def predictability_report(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    columns = [
        "put25_atm_skew", "forward_skew_change", "forward_spot_return", "forward_realized_vol",
        "forward_max_drawdown", "spread_pnl",
    ]
    usable = panel.dropna(subset=columns).copy() if not panel.empty else pd.DataFrame(columns=columns)
    correlations = usable[columns].corr(method="spearman") if len(usable) else pd.DataFrame()
    if len(usable) >= 5:
        usable["skew_quintile"] = pd.qcut(usable["put25_atm_skew"], 5, labels=False, duplicates="drop")
        quintiles = usable.groupby("skew_quintile").agg(
            observations=("put25_atm_skew", "size"),
            mean_skew=("put25_atm_skew", "mean"),
            future_skew_change=("forward_skew_change", "mean"),
            future_spot_return=("forward_spot_return", "mean"),
            future_realized_vol=("forward_realized_vol", "mean"),
            future_max_drawdown=("forward_max_drawdown", "mean"),
            spread_pnl=("spread_pnl", "mean"),
        ).reset_index()
    else:
        quintiles = pd.DataFrame()
    regressions = []
    for target in ("forward_skew_change", "forward_spot_return", "forward_realized_vol", "forward_max_drawdown", "spread_pnl"):
        regression_data = panel[[target, "put25_atm_skew", "atm_iv", "spot_trend"]].dropna() if not panel.empty else pd.DataFrame()
        if len(regression_data) < 10:
            regressions.append({"target": target, "observations": len(regression_data), "skew_coefficient": np.nan, "r_squared": np.nan})
            continue
        y = regression_data[target].to_numpy(float)
        x = np.column_stack((np.ones(len(regression_data)), regression_data[["put25_atm_skew", "atm_iv", "spot_trend"]].to_numpy(float)))
        coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
        fitted = x @ coefficients
        total = ((y - y.mean()) ** 2).sum()
        r_squared = 1.0 - ((y - fitted) ** 2).sum() / total if total else 0.0
        regressions.append({"target": target, "observations": len(y), "skew_coefficient": coefficients[1], "r_squared": r_squared})
    return correlations, quintiles, pd.DataFrame(regressions)


def strategy_comparison(panel: pd.DataFrame, *, threshold: float = 0.80) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    eligible = panel.dropna(subset=["strategy_pnl"]).copy() if not panel.empty else pd.DataFrame()
    if eligible.empty:
        names = ["no_trade", "daily_spread", "high_skew", "high_skew_vol_filter", "high_skew_trend_filter"]
        return pd.DataFrame([_strategy_metrics(name, pd.Series(dtype=float), pd.Series(dtype=float)) for name in names]), {}
    masks = {
        "no_trade": pd.Series(False, index=eligible.index),
        "daily_spread": pd.Series(True, index=eligible.index),
        "high_skew": eligible["skew_rank"] >= threshold,
        "high_skew_vol_filter": (eligible["skew_rank"] >= threshold) & (eligible["atm_iv_rank"] >= 0.50),
        "high_skew_trend_filter": (eligible["skew_rank"] >= threshold) & (eligible["spot_trend"] >= 0.0),
    }
    trades = {name: eligible[mask].copy() for name, mask in masks.items()}
    rows = []
    for name, selected in trades.items():
        pnl = selected["strategy_pnl"] if name != "no_trade" else pd.Series(0.0, index=eligible.index)
        risk = ((selected["short_strike"] - selected["long_strike"]).abs() * selected["multiplier"] - selected["entry_credit"] * selected["multiplier"]).clip(lower=0) if len(selected) else pd.Series(dtype=float)
        rows.append(_strategy_metrics(name, pnl, risk))
    return pd.DataFrame(rows), trades


def frozen_parameter_validation(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"skew_rank", "atm_iv_rank", "spot_trend", "sample", "strategy_pnl"}
    if panel.empty or not required <= set(panel.columns):
        return pd.DataFrame(), pd.DataFrame()
    rows = []
    for threshold in (0.70, 0.75, 0.80, 0.85, 0.90):
        for vol_filter in (False, True):
            for trend_filter in (False, True):
                masks = panel["skew_rank"] >= threshold
                if vol_filter:
                    masks &= panel["atm_iv_rank"] >= 0.50
                if trend_filter:
                    masks &= panel["spot_trend"] >= 0.0
                for sample in ("development", "validation"):
                    selected = panel[masks & (panel["sample"] == sample)].dropna(subset=["strategy_pnl"])
                    rows.append({
                        "threshold": threshold, "vol_filter": vol_filter, "trend_filter": trend_filter,
                        "sample": sample, "trades": len(selected), "mean_pnl": selected["strategy_pnl"].mean() if len(selected) else np.nan,
                        "expected_shortfall": _expected_shortfall(selected["strategy_pnl"]) if len(selected) else np.nan,
                    })
    grid = pd.DataFrame(rows)
    development = grid[(grid["sample"] == "development") & (grid["trades"] >= 20)].copy()
    if development.empty:
        return grid, pd.DataFrame()
    development["score"] = development["mean_pnl"] + development["expected_shortfall"] * 0.25
    candidates = development.nlargest(min(5, len(development)), "score")
    validation = grid[grid["sample"] == "validation"].merge(
        candidates[["threshold", "vol_filter", "trend_filter"]], on=["threshold", "vol_filter", "trend_filter"]
    )
    validation = validation[validation["trades"] >= 10]
    if validation.empty:
        return grid, pd.DataFrame()
    frozen = validation.sort_values(["mean_pnl", "expected_shortfall"], ascending=False).iloc[0]
    mask = panel["skew_rank"] >= frozen["threshold"]
    if frozen["vol_filter"]:
        mask &= panel["atm_iv_rank"] >= 0.50
    if frozen["trend_filter"]:
        mask &= panel["spot_trend"] >= 0.0
    test = panel[mask & (panel["sample"] == "test")].dropna(subset=["strategy_pnl"])
    result = pd.DataFrame([{
        "threshold": frozen["threshold"], "vol_filter": frozen["vol_filter"], "trend_filter": frozen["trend_filter"],
        "validation_trades": int(frozen["trades"]), "validation_mean_pnl": frozen["mean_pnl"],
        "test_trades": len(test), "test_mean_pnl": test["strategy_pnl"].mean() if len(test) else np.nan,
        "test_expected_shortfall": _expected_shortfall(test["strategy_pnl"]) if len(test) else np.nan,
    }])
    return grid, result


def robustness_sensitivity(panel: pd.DataFrame, commission_per_contract: float = 0.68) -> pd.DataFrame:
    selected = panel[(panel["sample"] == "test") & (panel["high_skew"])].dropna(subset=["strategy_pnl"]) if not panel.empty else pd.DataFrame()
    if selected.empty:
        return pd.DataFrame()
    cases = {
        "base": selected["strategy_pnl"],
        "double_commission": selected["strategy_pnl"] - commission_per_contract * 4.0,
        "extra_slippage_0.05": selected["strategy_pnl"] - 0.05 * selected["multiplier"],
        "entry_delay_2_slices": selected["strategy_pnl_delay_2"],
        "combined_stress": selected["strategy_pnl_delay_2"] - commission_per_contract * 4.0 - 0.05 * selected["multiplier"],
    }
    return pd.DataFrame([
        {
            "case": name, "trades": int(values.notna().sum()), "mean_pnl": values.mean(),
            "total_pnl": values.sum(), "worst_trade": values.min(), "expected_shortfall_95": _expected_shortfall(values.dropna()),
        }
        for name, values in cases.items()
    ])


def risk_decomposition(panel: pd.DataFrame, mask: pd.Series | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    required = {
        "strategy_pnl", "forward_spot_return", "forward_skew_change", "spot", "net_delta", "net_gamma",
        "net_theta", "net_vega", "holding_slices", "timestamp",
    }
    if panel.empty or not required <= set(panel.columns):
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    selected = panel[mask].copy() if mask is not None else panel.copy()
    selected = selected.dropna(subset=["strategy_pnl", "forward_spot_return", "forward_skew_change"])
    if selected.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    spot_change = selected["spot"] * selected["forward_spot_return"]
    selected["delta_pnl"] = selected["net_delta"] * spot_change
    selected["gamma_pnl"] = 0.5 * selected["net_gamma"] * spot_change ** 2
    selected["theta_pnl"] = selected["net_theta"] * selected["holding_slices"].fillna(0) / (390.0 * 365.25)
    selected["vega_pnl"] = selected["net_vega"] * selected["forward_skew_change"]
    selected["explained_pnl"] = selected[["delta_pnl", "gamma_pnl", "theta_pnl", "vega_pnl"]].sum(axis=1)
    selected["residual_pnl"] = selected["strategy_pnl"] - selected["explained_pnl"]
    selected["equity"] = selected["strategy_pnl"].cumsum()
    selected["drawdown"] = selected["equity"] - selected["equity"].cummax()
    summary = pd.DataFrame([{
        "trades": len(selected), "total_pnl": selected["strategy_pnl"].sum(), "mean_pnl": selected["strategy_pnl"].mean(),
        "profit_factor": _profit_factor(selected["strategy_pnl"]), "max_drawdown": selected["drawdown"].min(),
        "max_drawdown_duration_trades": _max_drawdown_duration(selected["equity"]),
        "worst_trade": selected["strategy_pnl"].min(), "expected_shortfall_95": _expected_shortfall(selected["strategy_pnl"]),
        "top5_absolute_pnl_contribution": selected["strategy_pnl"].abs().nlargest(5).sum() / selected["strategy_pnl"].abs().sum() if selected["strategy_pnl"].abs().sum() else 0.0,
        "delta_pnl": selected["delta_pnl"].sum(), "gamma_pnl": selected["gamma_pnl"].sum(),
        "theta_pnl": selected["theta_pnl"].sum(), "vega_pnl": selected["vega_pnl"].sum(),
        "residual_pnl": selected["residual_pnl"].sum(),
    }])
    selected["year"] = selected["timestamp"].map(lambda value: value.year)
    by_year = selected.groupby("year").agg(trades=("strategy_pnl", "size"), pnl=("strategy_pnl", "sum"), worst=("strategy_pnl", "min")).reset_index()
    return summary, selected, by_year


def _strategy_metrics(name: str, pnl: pd.Series, risk: pd.Series) -> dict:
    cumulative = pnl.cumsum() if len(pnl) else pnl
    drawdown = cumulative - cumulative.cummax() if len(cumulative) else cumulative
    return {
        "strategy": name, "trades": 0 if name == "no_trade" else len(pnl), "total_pnl": pnl.sum() if len(pnl) else 0.0,
        "expectancy": pnl.mean() if len(pnl) else 0.0, "win_rate": (pnl > 0).mean() if len(pnl) else 0.0,
        "per_trade_sharpe": pnl.mean() / pnl.std(ddof=0) * sqrt(len(pnl)) if len(pnl) > 1 and pnl.std(ddof=0) else 0.0,
        "profit_factor": _profit_factor(pnl), "max_drawdown": drawdown.min() if len(drawdown) else 0.0,
        "worst_trade": pnl.min() if len(pnl) else 0.0, "expected_shortfall_95": _expected_shortfall(pnl) if len(pnl) else 0.0,
        "return_on_max_risk": pnl.sum() / risk.sum() if len(risk) and risk.sum() else np.nan,
    }


def _profit_factor(pnl: pd.Series) -> float | None:
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return float(gains / losses) if losses else None


def _expected_shortfall(pnl: pd.Series, confidence: float = 0.95) -> float:
    if not len(pnl):
        return 0.0
    count = max(1, int(np.ceil(len(pnl) * (1.0 - confidence))))
    return float(pnl.nsmallest(count).mean())


def _max_drawdown_duration(equity: pd.Series) -> int:
    longest = current = 0
    peak = float("-inf")
    for value in equity:
        if value >= peak:
            peak, current = value, 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _catalog(dataset: HistoricalDataset) -> ReferenceCatalog:
    return dataset.reference_catalog()
