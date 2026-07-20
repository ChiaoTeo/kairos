from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from random import Random

import pandas as pd

from kairos.backtest.feed import HistoricalDataset
from kairos.domain.product import ListedOptionSpec, OptionRight
from kairos.pricing import ValuationService
from kairos.research.data_store import CollectionManifest


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    min_dte: int = 7
    max_dte: int = 45
    target_short_delta: Decimal = Decimal("-0.25")
    target_long_delta: Decimal = Decimal("-0.10")
    evaluation_time: time = time(15, 30)
    forward_horizon_days: int = 5
    minimum_rank_history: int = 252
    high_skew_percentile: Decimal = Decimal("0.80")
    minimum_observations: int = 252
    minimum_trading_days: int = 60
    minimum_quote_coverage: Decimal = Decimal("0.95")
    maximum_stale_rate: Decimal = Decimal("0.05")
    minimum_surface_calibration_rate: Decimal = Decimal("0.80")
    profit_target: Decimal = Decimal("0.50")
    stop_loss_multiple: Decimal = Decimal("2")
    exit_dte: int = 3
    commission_per_contract: Decimal = Decimal("0.68")
    bootstrap_samples: int = 2000
    bootstrap_block_size: int = 5
    random_seed: int = 7

    def __post_init__(self) -> None:
        if self.min_dte < 0 or self.max_dte < self.min_dte:
            raise ValueError("invalid DTE range")
        if isinstance(self.evaluation_time, str):
            object.__setattr__(self, "evaluation_time", time.fromisoformat(self.evaluation_time))
        if self.forward_horizon_days < 1 or self.minimum_rank_history < 2:
            raise ValueError("horizon and rank history must be positive")
        if not Decimal("0") < self.high_skew_percentile < Decimal("1"):
            raise ValueError("high skew percentile must be between zero and one")
        if self.minimum_trading_days < 1 or self.minimum_observations < 1:
            raise ValueError("minimum data requirements must be positive")
        if not Decimal("0") <= self.minimum_quote_coverage <= Decimal("1"):
            raise ValueError("quote coverage threshold must be between zero and one")
        if not Decimal("0") <= self.maximum_stale_rate <= Decimal("1"):
            raise ValueError("stale rate threshold must be between zero and one")
        if not Decimal("0") <= self.minimum_surface_calibration_rate <= Decimal("1"):
            raise ValueError("surface calibration threshold must be between zero and one")


@dataclass(frozen=True, slots=True)
class ResearchConclusion:
    status: str
    observation_count: int
    high_skew_count: int
    message: str
    statistics: dict[str, float | int | None]


@dataclass(frozen=True, slots=True)
class ResearchReadiness:
    ready: bool
    reasons: tuple[str, ...]
    metrics: dict[str, float | int | bool]


def assess_readiness(
    dataset: HistoricalDataset,
    panel: pd.DataFrame,
    config: ResearchConfig,
    collection: CollectionManifest | None,
) -> ResearchReadiness:
    reasons = []
    real_sessions = collection.real_session_count if collection else 0
    allowed_sources = bool(collection and collection.sessions and all(
        not item.synthetic and (
            item.source.startswith("ibkr.series") or item.source.startswith("massive.canonical:")
        ) for item in collection.sessions
    ))
    usable = panel.dropna(subset=["skew_rank", "forward_skew_change", "strategy_pnl"]) if not panel.empty else panel
    test_high = usable[(usable["sample"] == "test") & usable["high_skew"]] if not usable.empty else usable
    surface_rate = float(panel["surface_calibrated"].mean()) if len(panel) else 0.0
    if dataset.manifest.synthetic:
        reasons.append("dataset_is_synthetic")
    if real_sessions < 1:
        reasons.append("missing_real_collection_session")
    if not allowed_sources:
        reasons.append("unverified_collection_source")
    if dataset.manifest.quote_coverage < config.minimum_quote_coverage:
        reasons.append("quote_coverage_below_threshold")
    if dataset.manifest.stale_rate > config.maximum_stale_rate:
        reasons.append("stale_rate_above_threshold")
    if dataset.manifest.trading_days < config.minimum_trading_days:
        reasons.append("insufficient_trading_days")
    if len(usable) < config.minimum_observations:
        reasons.append("insufficient_usable_observations")
    if len(test_high) < 20:
        reasons.append("insufficient_high_skew_test_observations")
    if Decimal(str(surface_rate)) < config.minimum_surface_calibration_rate:
        reasons.append("surface_calibration_below_threshold")
    metrics = {
        "synthetic": dataset.manifest.synthetic,
        "real_collection_sessions": real_sessions,
        "source_verified": allowed_sources,
        "quote_coverage": float(dataset.manifest.quote_coverage),
        "stale_rate": float(dataset.manifest.stale_rate),
        "trading_days": dataset.manifest.trading_days,
        "usable_observations": len(usable),
        "high_skew_test_observations": len(test_high),
        "surface_calibration_rate": surface_rate,
    }
    return ResearchReadiness(not reasons, tuple(reasons), metrics)


def execute_research(
    dataset: HistoricalDataset,
    config: ResearchConfig,
    collection: CollectionManifest | None,
) -> tuple[pd.DataFrame, ResearchReadiness, ResearchConclusion]:
    panel = build_panel(dataset, config)
    readiness = assess_readiness(dataset, panel, config, collection)
    if not readiness.ready:
        conclusion = ResearchConclusion(
            "DATA_NOT_READY", len(panel), 0,
            "Research conclusion is locked until every real-data readiness gate passes.",
            {key: value for key, value in readiness.metrics.items() if isinstance(value, (int, float))},
        )
    else:
        conclusion = analyze_hypothesis(panel, config)
    return panel, readiness, conclusion


def build_panel(dataset: HistoricalDataset, config: ResearchConfig = ResearchConfig()) -> pd.DataFrame:
    """Build point-in-time skew observations and forward labels.

    Contract selection and internal valuation use only the current slice. Forward
    labels are attached in a separate pass and are never used to form the signal.
    """
    catalog = dataset.reference_catalog()
    valuation_service = ValuationService(
        catalog,
        max_quote_age_seconds=Decimal(str(max(5, dataset.manifest.sampling_seconds))),
    )
    rows = []
    for market in dataset.slices:
        _, valuation = valuation_service.value(market)
        references = dict(market.reference_prices)
        candidates_by_expiry: dict[object, list] = {}
        for item in valuation.instruments:
            definition = catalog.instruments.get(item.instrument_id, market.timestamp)
            spec = definition.contract_spec
            if not isinstance(spec, ListedOptionSpec) or spec.right is not OptionRight.PUT or item.pricing is None or item.implied_vol.volatility is None:
                continue
            dte = (spec.expiry.date() - market.timestamp.date()).days
            if config.min_dte <= dte <= config.max_dte:
                candidates_by_expiry.setdefault(spec.expiry, []).append((item, spec))
        eligible_expiries = [expiry for expiry, values in candidates_by_expiry.items() if len(values) >= 3]
        if not eligible_expiries:
            continue
        expiry = min(eligible_expiries)
        candidates = candidates_by_expiry[expiry]
        underlying_id = candidates[0][1].underlying
        spot = references.get(underlying_id)
        if spot is None:
            continue
        short, short_spec = min(candidates, key=lambda pair: abs(pair[0].pricing.delta - config.target_short_delta))
        long, long_spec = min(candidates, key=lambda pair: abs(pair[0].pricing.delta - config.target_long_delta))
        atm, atm_spec = min(candidates, key=lambda pair: abs(pair[1].strike - spot))
        if short.instrument_id == long.instrument_id:
            continue
        snapshots = {item.instrument_id: item for item in market.instruments}
        short_quote, long_quote = snapshots[short.instrument_id].quote, snapshots[long.instrument_id].quote
        if not short_quote or not long_quote or None in (short_quote.bid, short_quote.ask, long_quote.bid, long_quote.ask):
            continue
        entry_credit = short_quote.bid - long_quote.ask
        rows.append({
            "timestamp": market.timestamp,
            "expiry": expiry,
            "dte": (expiry.date() - market.timestamp.date()).days,
            "spot": float(spot),
            "atm_iv": float(atm.implied_vol.volatility),
            "put25_iv": float(short.implied_vol.volatility),
            "put10_iv": float(long.implied_vol.volatility),
            "put25_atm_skew": float(short.implied_vol.volatility - atm.implied_vol.volatility),
            "put25_put10_slope": float(short.implied_vol.volatility - long.implied_vol.volatility),
            "short_delta": float(short.pricing.delta),
            "long_delta": float(long.pricing.delta),
            "short_strike": float(short_spec.strike),
            "long_strike": float(long_spec.strike),
            "net_delta": float((-short.pricing.delta + long.pricing.delta) * short_spec.multiplier),
            "net_gamma": float((-short.pricing.gamma + long.pricing.gamma) * short_spec.multiplier),
            "net_theta": float((-short.pricing.theta + long.pricing.theta) * short_spec.multiplier),
            "net_vega": float((-short.pricing.vega + long.pricing.vega) * short_spec.multiplier),
            "short_instrument": short.instrument_id.value,
            "long_instrument": long.instrument_id.value,
            "entry_credit": float(entry_credit),
            "multiplier": float(short_spec.multiplier),
            "surface_calibrated": bool(valuation.surface and valuation.surface.calibration_status.value == "calibrated"),
            "valuation_failures": len(valuation.failures),
        })
    panel = pd.DataFrame(rows)
    if panel.empty:
        return panel
    panel = _daily_decision_rows(panel, config.evaluation_time)
    panel["skew_rank"] = _expanding_percentile(panel["put25_atm_skew"], config.minimum_rank_history)
    horizon = config.forward_horizon_days
    panel["forward_spot_return"] = panel["spot"].shift(-horizon) / panel["spot"] - 1.0
    panel["forward_skew_change"] = panel["put25_atm_skew"].shift(-horizon) - panel["put25_atm_skew"]
    panel["spread_pnl"] = _forward_spread_pnl(panel, dataset, horizon)
    panel["forward_realized_vol"] = _forward_realized_vol(panel["spot"], horizon)
    panel["forward_max_drawdown"] = _forward_max_drawdown(panel["spot"], horizon)
    proxy = [_simulate_trade_proxy(row, dataset, config, entry_delay_slices=1) for _, row in panel.iterrows()]
    delayed_proxy = [_simulate_trade_proxy(row, dataset, config, entry_delay_slices=2) for _, row in panel.iterrows()]
    panel["trade_proxy_pnl"] = [item[0] for item in proxy]
    panel["trade_proxy_exit_reason"] = [item[1] for item in proxy]
    panel["trade_proxy_holding_slices"] = [item[2] for item in proxy]
    panel["trade_proxy_pnl_delay_2"] = [item[0] for item in delayed_proxy]
    panel["evidence_level"] = "TRADE_PROXY_ONLY"
    # Compatibility aliases for existing notebooks. These columns are not executable-backtest evidence.
    panel["strategy_pnl"] = panel["trade_proxy_pnl"]
    panel["exit_reason"] = panel["trade_proxy_exit_reason"]
    panel["holding_slices"] = panel["trade_proxy_holding_slices"]
    panel["strategy_pnl_delay_2"] = panel["trade_proxy_pnl_delay_2"]
    panel["high_skew"] = panel["skew_rank"] >= float(config.high_skew_percentile)
    panel["atm_iv_rank"] = _expanding_percentile(panel["atm_iv"], config.minimum_rank_history)
    panel["spot_trend"] = panel["spot"] / panel["spot"].rolling(20, min_periods=20).mean() - 1.0
    panel["sample"] = _time_split(len(panel))
    return panel


def analyze_hypothesis(panel: pd.DataFrame, config: ResearchConfig = ResearchConfig()) -> ResearchConclusion:
    required = {"skew_rank", "forward_skew_change", "strategy_pnl", "high_skew", "sample"}
    if panel.empty or not required <= set(panel.columns):
        return ResearchConclusion("INSUFFICIENT_DATA", 0, 0, "No eligible point-in-time observations were produced.", {})
    usable = panel.dropna(subset=["skew_rank", "forward_skew_change", "strategy_pnl"])
    high = usable[usable["high_skew"]]
    test = usable[usable["sample"] == "test"]
    test_high = test[test["high_skew"]]
    stats = {
        "usable_observations": len(usable),
        "high_skew_observations": len(high),
        "mean_forward_skew_change_all": _mean(usable["forward_skew_change"]),
        "mean_forward_skew_change_high": _mean(high["forward_skew_change"]),
        "mean_spread_pnl_all": _mean(usable["strategy_pnl"]),
        "mean_spread_pnl_high": _mean(high["strategy_pnl"]),
        "test_mean_spread_pnl_all": _mean(test["strategy_pnl"]),
        "test_mean_spread_pnl_high": _mean(test_high["strategy_pnl"]),
    }
    if len(usable) < config.minimum_observations or len(test_high) < 20:
        return ResearchConclusion(
            "INSUFFICIENT_DATA", len(usable), len(high),
            f"Need at least {config.minimum_observations} usable observations and 20 high-skew test observations; got {len(usable)} and {len(test_high)}.",
            stats,
        )
    difference = test_high["strategy_pnl"].mean() - test["strategy_pnl"].mean()
    low, high_ci = _block_bootstrap_difference(test, config)
    stats.update({"test_pnl_difference": float(difference), "bootstrap_ci_low": low, "bootstrap_ci_high": high_ci})
    supported = difference > 0 and low > 0 and test_high["forward_skew_change"].mean() < 0
    return ResearchConclusion(
        "TRADE_PROXY_SUPPORTED" if supported else "NOT_SUPPORTED", len(usable), len(high),
        "Signal and trade proxy passed the predeclared conditions; executable Strategy evidence is still required." if supported else "Hypothesis did not pass all predeclared out-of-sample conditions.",
        stats,
    )


def _expanding_percentile(series: pd.Series, minimum_history: int) -> pd.Series:
    result = []
    values = series.tolist()
    for index, value in enumerate(values):
        history = values[:index]
        result.append(None if len(history) < minimum_history else sum(item <= value for item in history) / len(history))
    return pd.Series(result, index=series.index, dtype="float64")


def _forward_spread_pnl(panel: pd.DataFrame, dataset: HistoricalDataset, horizon: int) -> pd.Series:
    market_by_time = {market.timestamp: market for market in dataset.slices}
    values = []
    for index, row in panel.iterrows():
        future_index = index + horizon
        if future_index >= len(panel):
            values.append(None)
            continue
        future = market_by_time.get(panel.iloc[future_index]["timestamp"])
        snapshots = {item.instrument_id.value: item for item in future.instruments} if future else {}
        short, long = snapshots.get(row["short_instrument"]), snapshots.get(row["long_instrument"])
        if not short or not long or not short.quote or not long.quote or short.quote.ask is None or long.quote.bid is None:
            values.append(None)
            continue
        exit_debit = float(short.quote.ask - long.quote.bid)
        values.append((row["entry_credit"] - exit_debit) * row["multiplier"])
    return pd.Series(values, index=panel.index, dtype="float64")


def _daily_decision_rows(panel: pd.DataFrame, evaluation_time: time) -> pd.DataFrame:
    ordered = panel.sort_values("timestamp").copy()
    ordered["decision_date"] = ordered["timestamp"].map(lambda value: value.date())
    target_seconds = evaluation_time.hour * 3600 + evaluation_time.minute * 60 + evaluation_time.second
    ordered["_distance"] = ordered["timestamp"].map(
        lambda value: abs(value.hour * 3600 + value.minute * 60 + value.second - target_seconds)
    )
    selected = ordered.loc[ordered.groupby("decision_date")["_distance"].idxmin()]
    return selected.drop(columns=["_distance"]).sort_values("timestamp").reset_index(drop=True)


def _forward_realized_vol(spot: pd.Series, horizon: int) -> pd.Series:
    from math import log, sqrt
    values = []
    prices = spot.tolist()
    for index in range(len(prices)):
        future = prices[index:index + horizon + 1]
        if len(future) < horizon + 1:
            values.append(None)
            continue
        returns = [log(future[i] / future[i - 1]) for i in range(1, len(future))]
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / max(1, len(returns) - 1)
        values.append(sqrt(variance * 252.0))
    return pd.Series(values, index=spot.index, dtype="float64")


def _forward_max_drawdown(spot: pd.Series, horizon: int) -> pd.Series:
    values = []
    prices = spot.tolist()
    for index, initial in enumerate(prices):
        future = prices[index + 1:index + horizon + 1]
        values.append(None if len(future) < horizon else min(value / initial - 1.0 for value in future))
    return pd.Series(values, index=spot.index, dtype="float64")


def _simulate_trade_proxy(
    row, dataset: HistoricalDataset, config: ResearchConfig, *, entry_delay_slices: int,
) -> tuple[float | None, str | None, int | None]:
    """Cheap research mapping proxy; never substitutes for BacktestEngine evidence."""
    slice_index = {market.timestamp: index for index, market in enumerate(dataset.slices)}
    decision = slice_index[row["timestamp"]]
    entry_index = decision + entry_delay_slices
    if entry_index >= len(dataset.slices):
        return None, None, None
    entry_market = dataset.slices[entry_index]
    entry_snapshots = {item.instrument_id.value: item for item in entry_market.instruments}
    entry_short, entry_long = entry_snapshots.get(row["short_instrument"]), entry_snapshots.get(row["long_instrument"])
    if not entry_short or not entry_long or not entry_short.quote or not entry_long.quote or entry_short.quote.bid is None or entry_long.quote.ask is None:
        return None, None, None
    entry_credit = float(entry_short.quote.bid - entry_long.quote.ask)
    for holding, market in enumerate(dataset.slices[entry_index + 1:], 1):
        snapshots = {item.instrument_id.value: item for item in market.instruments}
        short, long = snapshots.get(row["short_instrument"]), snapshots.get(row["long_instrument"])
        if not short or not long or not short.quote or not long.quote or short.quote.ask is None or long.quote.bid is None:
            continue
        exit_debit = float(short.quote.ask - long.quote.bid)
        dte = (row["expiry"].date() - market.timestamp.date()).days
        reason = None
        if exit_debit <= entry_credit * (1.0 - float(config.profit_target)):
            reason = "profit_target"
        elif exit_debit >= entry_credit * float(config.stop_loss_multiple):
            reason = "stop_loss"
        elif dte <= config.exit_dte:
            reason = "time_exit"
        if reason:
            commissions = float(config.commission_per_contract) * 4.0
            pnl = (entry_credit - exit_debit) * row["multiplier"] - commissions
            return pnl, reason, holding
    return None, None, None


def _time_split(length: int) -> list[str]:
    development_end, validation_end = int(length * 0.60), int(length * 0.80)
    return ["development" if i < development_end else "validation" if i < validation_end else "test" for i in range(length)]


def _block_bootstrap_difference(test: pd.DataFrame, config: ResearchConfig) -> tuple[float, float]:
    rng = Random(config.random_seed)
    block = min(config.bootstrap_block_size, len(test))
    differences = []
    for _ in range(config.bootstrap_samples):
        indexes = []
        while len(indexes) < len(test):
            start = rng.randrange(max(1, len(test) - block + 1))
            indexes.extend(range(start, min(start + block, len(test))))
        sample = test.iloc[indexes[:len(test)]]
        selected = sample[sample["high_skew"]]
        if len(selected):
            differences.append(float(selected["strategy_pnl"].mean() - sample["strategy_pnl"].mean()))
    if not differences:
        return float("nan"), float("nan")
    differences.sort()
    return differences[int(len(differences) * 0.025)], differences[min(len(differences) - 1, int(len(differences) * 0.975))]


def _mean(series: pd.Series) -> float | None:
    return None if len(series) == 0 else float(series.mean())
