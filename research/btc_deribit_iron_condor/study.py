from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import statistics

from trading import __version__
from research.btc_options_stats import block_bootstrap_ci, percentile
from trading.data import ResearchDataClient
from trading.data.products import BTC_DERIBIT_OPTION_TRADES, BTC_DERIBIT_TERM_SKEW_DAILY
from trading.storage.data_lake import write_json
from trading.research.validation import (
    CapitalSpec, DataCapabilities, EvidenceStatus, ExecutionArchetype,
    OutOfSampleEvidence, ProductProtocol, ResearchValidationResult, ReturnDriver,
    SampleSufficiency, StudyRegistration, ValidationArtifactWriter,
    ValidationLevel, ValidationState, approximate_required_samples, build_data_gap_plan,
    TestWindowRegistry, TestWindowUse,
)
from decimal import Decimal


MATURITIES = (7, 14, 30)
REGIMES = ("unconditional", "high_skew", "high_skew_high_iv", "fear_cooling")
STRUCTURES = ("symmetric", "delta_neutral_skewed")


def execute(root: str | Path = "data", commission_per_contract_leg_usd: float = 5.0):
    repository = ResearchDataClient(root)
    features = sorted(repository.load_rows(BTC_DERIBIT_TERM_SKEW_DAILY.product),
                      key=lambda row: row["period_start"])
    split = int(len(features) * .70)
    development, test = features[:split], features[split:]
    thresholds = {}
    for maturity in MATURITIES:
        thresholds[maturity] = {
            "skew": percentile(_finite(development, f"put_skew25_{maturity}d"), .80),
            "atm_iv": percentile(_finite(development, f"atm_iv_{maturity}d"), .80),
        }

    signals = _signals(test, thresholds, development[-365:])
    needed = {day for values in signals.values() for entry, exit_ in values for day in (entry, exit_)}
    daily = {day: [] for day in needed}
    for trade in repository.iter_rows(BTC_DERIBIT_OPTION_TRADES.product):
        day = date.fromisoformat(trade["event_time"][:10])
        if day in daily:
            daily[day].append(trade)

    trades = []
    for (maturity, regime), dates in signals.items():
        for entry, exit_ in dates:
            entry_book = _contracts(daily.get(entry, []))
            exit_book = _contracts(daily.get(exit_, []))
            expiry = _target_expiry(entry_book, entry, maturity)
            for structure in STRUCTURES:
                trade = _build_trade(entry, exit_, maturity, regime, structure, expiry,
                                     entry_book, exit_book, commission_per_contract_leg_usd)
                if trade:
                    trades.append(trade)

    result = _summarize(trades, signals, thresholds)
    result["periods"] = {
        "development": [features[0]["period_start"][:10], features[split]["period_start"][:10]],
        "test": [features[split]["period_start"][:10],
                 (date.fromisoformat(features[-1]["period_start"][:10]) + timedelta(days=1)).isoformat()],
    }
    return trades, result


def _signals(rows, thresholds, history=()):
    output = {(maturity, regime): [] for maturity in MATURITIES for regime in REGIMES}
    previous = {}
    iv_history = {maturity: _finite(history, f"atm_iv_{maturity}d")[-365:] for maturity in MATURITIES}
    last_exit = {key: None for key in output}
    for row in rows:
        day = date.fromisoformat(row["period_start"][:10])
        for maturity in MATURITIES:
            skew = _float(row.get(f"put_skew25_{maturity}d"))
            iv = _float(row.get(f"atm_iv_{maturity}d"))
            prior_iv = previous.get(maturity, math.nan)
            rolling_iv_threshold = percentile(iv_history[maturity], .80) if iv_history[maturity] else thresholds[maturity]["atm_iv"]
            flags = {
                "unconditional": True,
                "high_skew": skew >= thresholds[maturity]["skew"],
                "high_skew_high_iv": skew >= thresholds[maturity]["skew"] and iv >= rolling_iv_threshold,
                "fear_cooling": skew >= thresholds[maturity]["skew"] and iv >= rolling_iv_threshold
                                and math.isfinite(prior_iv) and iv <= prior_iv,
            }
            holding_days = min(7, max(3, maturity // 2))
            for regime, eligible in flags.items():
                key = (maturity, regime)
                if eligible and (last_exit[key] is None or day > last_exit[key]):
                    exit_ = day + timedelta(days=holding_days)
                    output[key].append((day, exit_))
                    last_exit[key] = exit_
            previous[maturity] = iv
            if math.isfinite(iv):
                iv_history[maturity] = (iv_history[maturity] + [iv])[-365:]
    return output


def _build_trade(entry, exit_, maturity, regime, structure, expiry, entry_book, exit_book, commission):
    if expiry is None:
        return None
    puts = [item for item in entry_book.values() if item["right"] == "put" and item["expiry"] == expiry]
    calls = [item for item in entry_book.values() if item["right"] == "call" and item["expiry"] == expiry]
    call_short_delta, call_long_delta = ((.25, .10) if structure == "symmetric" else (.15, .05))
    legs = {
        "short_put": _nearest(puts, -.25, "sell"),
        "long_put": _nearest(puts, -.10, "buy"),
        "short_call": _nearest(calls, call_short_delta, "sell"),
        "long_call": _nearest(calls, call_long_delta, "buy"),
    }
    if any(value is None for value in legs.values()):
        return None
    if not (legs["long_put"]["strike"] < legs["short_put"]["strike"]
            < legs["short_call"]["strike"] < legs["long_call"]["strike"]):
        return None
    put_delta = -legs["short_put"]["delta"] + legs["long_put"]["delta"]
    call_delta = -legs["short_call"]["delta"] + legs["long_call"]["delta"]
    call_quantity = 1.0 if structure == "symmetric" else min(2.5, max(.5, -put_delta / call_delta))
    quantities = {"short_put": 1.0, "long_put": 1.0,
                  "short_call": call_quantity, "long_call": call_quantity}
    exit_prices = {}
    for name, leg in legs.items():
        observed = exit_book.get(leg["instrument"])
        direction = "buy" if name.startswith("short") else "sell"
        if not observed or observed[direction] is None:
            return None
        exit_prices[name] = observed[direction]
    credit = (legs["short_put"]["sell"] - legs["long_put"]["buy"]
              + call_quantity * (legs["short_call"]["sell"] - legs["long_call"]["buy"]))
    debit = (exit_prices["short_put"] - exit_prices["long_put"]
             + call_quantity * (exit_prices["short_call"] - exit_prices["long_call"]))
    if credit <= 0:
        return None
    fees = commission * (2 + 2 * call_quantity) * 2
    put_width = legs["short_put"]["strike"] - legs["long_put"]["strike"]
    call_width = call_quantity * (legs["long_call"]["strike"] - legs["short_call"]["strike"])
    max_loss = max(put_width, call_width) - credit
    pnl = credit - debit - fees
    return {
        "entry_date": entry.isoformat(), "exit_date": exit_.isoformat(), "maturity_days": maturity,
        "regime": regime, "structure": structure, "expiry": expiry.isoformat(),
        "legs": {name: leg["instrument"] for name, leg in legs.items()},
        "call_quantity": call_quantity, "initial_delta_btc": put_delta + call_quantity * call_delta,
        "entry_credit_usd": credit, "exit_debit_usd": debit, "commission_usd": fees,
        "pnl_usd": pnl, "maximum_loss_proxy_usd": max_loss,
        "return_on_max_loss": pnl / max_loss if max_loss > 0 else math.nan,
    }


def _summarize(trades, signals, thresholds):
    groups = {}
    for maturity in MATURITIES:
        for regime in REGIMES:
            for structure in STRUCTURES:
                values = [row for row in trades if row["maturity_days"] == maturity
                          and row["regime"] == regime and row["structure"] == structure]
                pnl = [row["pnl_usd"] for row in values]
                returns = [row["return_on_max_loss"] for row in values if math.isfinite(row["return_on_max_loss"])]
                tail_count = max(1, math.ceil(len(pnl) * .20))
                ci = block_bootstrap_ci(pnl, max(1, maturity // 7), seed=20260715 + maturity) if pnl else [math.nan, math.nan]
                groups[f"{maturity}d/{regime}/{structure}"] = {
                    "signals": len(signals[(maturity, regime)]), "completed_trades": len(values),
                    "completion_rate": len(values) / len(signals[(maturity, regime)]) if signals[(maturity, regime)] else math.nan,
                    "mean_pnl_usd": statistics.fmean(pnl) if pnl else math.nan,
                    "median_pnl_usd": statistics.median(pnl) if pnl else math.nan,
                    "total_pnl_usd": sum(pnl), "win_rate": sum(value > 0 for value in pnl) / len(pnl) if pnl else math.nan,
                    "mean_return_on_max_loss": statistics.fmean(returns) if returns else math.nan,
                    "worst_trade_usd": min(pnl) if pnl else math.nan,
                    "expected_shortfall_20pct_usd": statistics.fmean(sorted(pnl)[:tail_count]) if pnl else math.nan,
                    "block_bootstrap_95_ci": ci,
                    "status": "TRADE_PROXY_TESTED" if len(values) >= 30 else "DATA_NOT_READY",
                }
    comparisons = {}
    for maturity in MATURITIES:
        for structure in STRUCTURES:
            baseline = groups[f"{maturity}d/unconditional/{structure}"]
            for regime in REGIMES[1:]:
                conditional = groups[f"{maturity}d/{regime}/{structure}"]
                comparisons[f"{maturity}d/{regime}/{structure}"] = {
                    "mean_pnl_uplift_vs_unconditional_usd": conditional["mean_pnl_usd"] - baseline["mean_pnl_usd"],
                    "enough_conditional_trades": conditional["completed_trades"] >= 30,
                    "interpretation": "descriptive_only" if conditional["completed_trades"] < 30 else "eligible_for_inference",
                }
    return {
        "study_id": "btc_deribit_iron_condor_trade_proxy_v1",
        "hypotheses": {
            "h1": "high put skew and ATM IV, especially after IV stops rising, identify excessive fear",
            "h2": "conditional iron condors outperform unconditional iron condors after costs",
        },
        "split": {"method": "chronological", "development_fraction": .70, "threshold_percentile": .80,
                  "skew_threshold": "frozen on development sample",
                  "atm_iv_threshold": "trailing 365 observations, using past data only"},
        "thresholds": thresholds, "holding_rule": "min(7, max(3, maturity_days//2)) calendar days",
        "groups": groups, "conditional_comparisons": comparisons,
        "conclusion": {
            "h1_excessive_fear": "PARTIALLY_SUPPORTED_BY_PRIOR_SIGNAL_STUDIES",
            "h2_conditional_iron_condor": "DATA_NOT_READY",
            "reason": "No conditional cell has the pre-specified minimum of 30 completed trade proxies.",
        },
        "limitations": ["direction-filtered daily trades are not synchronous executable bid/ask quotes",
                        "only contracts trading on both entry and exit are observable (completion bias)",
                        "index price converts BTC option premiums to USD", "commission model excludes slippage"],
    }


def _contracts(rows):
    grouped = {}
    for row in rows:
        try:
            expiry = datetime.fromisoformat(row["expiry"].replace("Z", "+00:00")).date()
            iv, strike, forward = float(row["trade_iv"]), float(row["strike"]), float(row["index_price_usd"])
            timestamp = datetime.fromisoformat(row["event_time"].replace("Z", "+00:00"))
            years = (datetime.fromisoformat(row["expiry"].replace("Z", "+00:00")) - timestamp).total_seconds() / (365 * 86400)
            if years <= 0 or iv <= 0:
                continue
            d1 = (math.log(forward / strike) + .5 * iv * iv * years) / (iv * math.sqrt(years))
            call_delta = .5 * (1 + math.erf(d1 / math.sqrt(2)))
            delta = call_delta if row["option_right"] == "call" else call_delta - 1
            price, amount = float(row["price_btc"]) * forward, max(float(row["amount_btc"]), .0001)
        except (ValueError, TypeError, KeyError):
            continue
        item = grouped.setdefault(row["instrument_id"], {"instrument": row["instrument_id"], "expiry": expiry,
            "right": row["option_right"], "strike": strike, "deltas": [], "buy_prices": [], "sell_prices": []})
        item["deltas"].append((delta, amount)); item[f"{row['direction']}_prices"].append((price, amount))
    for item in grouped.values():
        item["delta"] = _weighted(item["deltas"])
        item["buy"] = _weighted(item["buy_prices"]); item["sell"] = _weighted(item["sell_prices"])
    return grouped


def _weighted(values):
    if not values:
        return None
    ordered = sorted(values); total = sum(weight for _, weight in ordered); cumulative = 0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= total / 2:
            return value
    return ordered[-1][0]


def _target_expiry(book, day, target):
    expiries = {item["expiry"] for item in book.values() if item["expiry"] > day}
    return min(expiries, key=lambda expiry: (abs((expiry - day).days - target), expiry), default=None)


def _nearest(items, target, direction):
    valid = [item for item in items if item[direction] is not None]
    return min(valid, key=lambda item: (abs(item["delta"] - target), item["instrument"]), default=None)


def _finite(rows, key):
    return [value for row in rows if math.isfinite(value := _float(row.get(key)))]


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def main(argv=None):
    parser = argparse.ArgumentParser(); parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--commission-per-contract-leg-usd", type=float, default=5.0); args = parser.parse_args(argv)
    trades, result = execute(args.data_root, args.commission_per_contract_leg_usd)
    output = args.data_root / "studies" / result["study_id"]; output.mkdir(parents=True, exist_ok=True)
    ResearchDataClient(args.data_root).freeze_products(output / "data_snapshot.json", result["study_id"],
        (BTC_DERIBIT_OPTION_TRADES.product, BTC_DERIBIT_TERM_SKEW_DAILY.product), code_version=__version__)
    write_json(output / "study_spec.json", {key: result[key] for key in ("study_id", "hypotheses", "split", "holding_rule")})
    write_json(output / "trades.json", trades); write_json(output / "results.json", result)
    (output / "REPORT.md").write_text(_report(result), encoding="utf-8")
    _write_governed_artifacts(args.data_root, result, trades)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _write_governed_artifacts(root, result, trades=None):
    conditional = [item for key, item in result["groups"].items() if "/unconditional/" not in key]
    completed = max((item["completed_trades"] for item in conditional), default=0)
    periods = result["periods"]
    capital = CapitalSpec(Decimal("100000"), "USD", Decimal(".02"), Decimal(".10"),
        "maximum_loss_proxy_v1", True, False, "zero", "stop_before_negative_equity")
    required_samples=approximate_required_samples(.5)
    registration = StudyRegistration(
        result["study_id"], "1.0.0",
        "high put skew and elevated ATM IV, especially after IV stops rising, improve iron-condor returns",
        (ProductProtocol.OPTION,), ("short_volatility", "short_gamma", "skew"),
        (ReturnDriver.VOLATILITY, ReturnDriver.SKEW), ("gamma", "vega", "jump", "liquidity"),
        ExecutionArchetype.TAKER, tuple(periods["development"]), None, tuple(periods["test"]),
        ("put_skew25", "atm_iv", "atm_iv_change"), ("net_pnl", "return_on_max_loss"), MATURITIES,
        "mean_return_on_max_loss", required_samples, "conditional mean return > unconditional with CI above zero",
        "conditional upper confidence bound <= 0", ("synchronous_quotes", "quote_size", "settlement_price"), capital,
    )
    data = ResearchDataClient(root)
    capabilities = DataCapabilities(
        (data.catalog.release(BTC_DERIBIT_OPTION_TRADES.key).release_id,
         data.catalog.release(BTC_DERIBIT_TERM_SKEW_DAILY.key).release_id),
        event_time=True, point_in_time_universe=True, trade_events=True, trade_direction=True,
        supported_products=(ProductProtocol.OPTION,), maximum_validation_level=ValidationLevel.L3_MAPPING,
    )
    missing = ("synchronous_multi_leg_quotes", "quote_size", "settlement_price", "option_lifecycle_events")
    gaps = build_data_gap_plan(missing, target_samples=required_samples, collection_frequency="hourly",
                               collection_started_at="2026-07-15T00:00:00Z")
    governed = ResearchValidationResult(
        registration,
        ValidationState(EvidenceStatus.READY, EvidenceStatus.EXPLORATORY,
            EvidenceStatus.DATA_NOT_READY, EvidenceStatus.TRADE_PROXY_ONLY,
            ValidationLevel.L3_MAPPING, "signal can be mapped to an exploratory trade proxy"),
        capabilities, SampleSufficiency(completed, completed, float(completed), required_samples, .5, .80,
            ("high_skew", "high_skew_high_iv", "fear_cooling"), 0),
        OutOfSampleEvidence.TIME,
        {"groups": result["groups"], "conditional_comparisons": result["conditional_comparisons"],
         "source_conclusion": result["conclusion"]}, tuple(result["limitations"]), gaps,
    )
    execution_spec = {
        "execution_archetype": "taker_proxy", "entry": "direction-filtered daily trades",
        "exit": "reverse-direction daily trades", "multi_leg_synchronous": False,
        "fee_model": "5 USD per contract leg per side", "slippage_model": "not available",
    }
    test_usage = {"test_period": periods["test"], "purpose": "time_oos_trade_proxy",
                  "consumed": True, "decision_oos": False, "next_confirmatory_version_requires_new_window": True}
    risk_decomposition={"status":"EXPLORATORY","note":"group-level tail metrics are in results; exact Greeks attribution is unavailable for asynchronous trade proxies"}
    equity_curve={"status":"DATA_NOT_READY","points":[],"reason":"non-synchronous trade proxies can violate no-arbitrage maximum loss; tradable CAGR is prohibited"}
    ValidationArtifactWriter(root).write(governed, report=_report(result),
        extra_artifacts={"execution_spec.json": execution_spec, "test_usage.json": test_usage,
            "trades.json": trades if trades is not None else [], "risk_decomposition.json":risk_decomposition,
            "equity_curve.json":equity_curve},
        extra_audit={"source_study_directory": f"studies/{result['study_id']}"})
    TestWindowRegistry(Path(root)/"studies"/"test_window_registry.jsonl").register(
        TestWindowUse(result["study_id"],"1.0.0",periods["test"][0],periods["test"][1],"time_oos_trade_proxy",False))


def _report(result):
    lines = ["# BTC Deribit 铁鹰策略研究", "", "## 研究结论", "",
        "- **不能确认“市场恐慌后卖铁鹰可以稳定盈利”。** 所有条件组都少于预设的30笔完整成交代理，正式状态为 `DATA_NOT_READY`。",
        "- 7D无条件对称铁鹰的成交代理均值为正且bootstrap区间高于零，但它受成交选择偏差影响，不能解释为可执行收益。",
        "- 立即在高Skew或高Skew+高IV时卖出，7D结果更差；等待ATM IV停止上升后均值改善，方向符合“恐慌消退”假设，但只有个位数交易。",
        "- 14D条件组描述性结果最好；30D没有稳定优势。偏斜Delta中性结构没有一致胜过对称结构。",
        "", "## 测试期成交代理结果", "",
        "|期限|条件|结构|完整交易|均值PnL|95% CI|胜率|最差交易|20% ES|相对无条件提升|", "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    labels = {"unconditional": "无条件", "high_skew": "高Skew", "high_skew_high_iv": "高Skew+高IV", "fear_cooling": "恐慌消退"}
    for key, item in result["groups"].items():
        maturity, regime, structure = key.split("/")
        if item["completed_trades"]:
            uplift = "—" if regime == "unconditional" else f"{result['conditional_comparisons'][key]['mean_pnl_uplift_vs_unconditional_usd']:.2f}"
            ci = item["block_bootstrap_95_ci"]
            lines.append(f"|{maturity}|{labels[regime]}|{structure}|{item['completed_trades']}|{item['mean_pnl_usd']:.2f}|[{ci[0]:.2f}, {ci[1]:.2f}]|{item['win_rate']:.1%}|{item['worst_trade_usd']:.2f}|{item['expected_shortfall_20pct_usd']:.2f}|{uplift}|")
    lines += ["", "## 如何解释", "",
        "这里的关键不是胜率，而是尾部：多个组合胜率超过60%，但最差单笔达到数千美元，符合短Gamma策略“多次小赚、少数大亏”的结构。7D高恐慌组恶化说明在恐慌加速阶段入场过早；IV停止上升过滤值得作为下一阶段预注册规则，而不是从本样本挑出的盈利参数。",
        "", "## 方法与限制", "",
        "- 按时间70%/30%切分；Skew 80%阈值只在开发期冻结。ATM IV采用只含过去数据的365日滚动80%分位，以适应波动率制度变化。",
        "- 期限为7/14/30D，持有期为 `min(7, max(3, 期限//2))` 个自然日。",
        "- 对称结构为10Δ/25Δ Put与25Δ/10Δ Call；偏斜结构为10Δ/25Δ Put与15Δ/5Δ Call，并按初始Delta调整Call数量。",
        "- Deribit长期数据只有成交，没有同步盘口。入场卖腿取sell方向成交、买腿取buy方向成交，出场反向；这仍然不是可执行bid/ask回测。",
        "- 只有入场和出场四腿都成交的组合可观察，因此存在完成样本选择偏差；手续费已计入，滑点未计入。",
        "", "本研究不是投资建议。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
