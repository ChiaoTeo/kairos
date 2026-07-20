from __future__ import annotations

import argparse
import json
from pathlib import Path

from studies.btc_options_readiness import btc_options_readiness
from kairos.storage.data_lake import write_json


def generate(root: Path):
    studies = root/"studies"
    term = _load(studies/"btc_term_vrp_v1"/"results.json")
    skew = _load(studies/"btc_skew_predictability_v1"/"results.json")
    cross = _load(studies/"btc_skew_cross_validation_v1"/"results.json")
    spread = _load(studies/"btc_skew_spread_backtest_v1"/"results.json")
    deribit_spread = _load(studies/"btc_deribit_skew_spread_trade_proxy_v1"/"results.json")
    attribution = _load(studies/"btc_deribit_skew_spread_trade_proxy_v1"/"risk_decomposition.json")
    hedged = _load(studies/"btc_deribit_skew_spread_daily_delta_hedged_v1"/"results.json")
    threshold_hedge = _load(studies/"btc_deribit_skew_spread_delta_threshold_sensitivity_v1"/"results.json")
    readiness = btc_options_readiness(root)
    output = studies/"btc_options_research_summary"; output.mkdir(parents=True, exist_ok=True)
    report = ["# BTC 期权期限溢价与 Skew 研究总结", "", "## 数据与门禁", "",
        f"- Deribit：21,930,528 笔 BTC 期权成交，覆盖 {readiness['gates'][0]['value']} 个自然日。",
        f"- 长期信号研究：`{'READY' if readiness['signal_research_ready'] else 'DATA_NOT_READY'}`。",
        f"- 可执行策略研究：`{'READY' if readiness['executable_strategy_ready'] else 'DATA_NOT_READY'}`；Binance 盘口仅 {readiness['gates'][-1]['value']} 天。",
        "", "## 多期限 IV 与未来 RV", "", "|期限|测试样本|平均ATM IV|平均未来RV|平均VRP|95% CI|IV>RV|支持|", "|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    for horizon, item in term["horizons"].items():
        report.append(f"|{horizon}|{item['observations']}|{item['mean_atm_iv_percent']:.2f}%|{item['mean_forward_rv_percent']:.2f}%|{item['mean_vrp_vol_points']:.2f}|[{item['block_bootstrap_95_ci'][0]:.2f}, {item['block_bootstrap_95_ci'][1]:.2f}]|{item['iv_above_rv_fraction']:.1%}|{'是' if item['h1_iv_exceeds_forward_rv'] else '否'}|")
    report += ["", "结论：所有期限平均 IV 都高于未来 RV，但在考虑标签重叠后，只有 7D 的 95% CI 完全高于零。", "",
               "## 25Δ Put Skew 均值回归", "", "|期限|高Skew样本|冻结阈值|未来Skew变化|95% CI|未来收益|最大回撤|支持|", "|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    for horizon, item in skew["horizons"].items():
        report.append(f"|{horizon}|{item['high_skew_test_observations']}|{item['development_80pct_threshold']:.3f}|{item['mean_future_skew_change']:.3f}|[{item['block_bootstrap_95_ci'][0]:.3f}, {item['block_bootstrap_95_ci'][1]:.3f}]|{item['mean_future_spot_return']:.1%}|{item['mean_future_max_drawdown']:.1%}|{'是' if item['h1_high_skew_mean_reverts'] else '否'}|")
    report += ["", "高 skew 后各期限 skew change 的置信区间均低于零，支持均值回归。它不等于卖 Put 必然盈利。", "",
        "## 跨源验证与策略", "",
        f"Deribit 成交型与 Binance 盘口型 30D skew 有 {cross['paired_days']} 个配对日，相关系数 {cross['pearson_correlation']:.2f}；Deribit 平均高 {cross['mean_difference']*100:.2f} 个波动率点。",
        "", f"Bull Put Spread 产生 {spread['trades']} 笔保守成交交易，平均 PnL {spread['mean_pnl_usdt']:.2f} USDT，95% CI [{spread['block_bootstrap_95_ci'][0]:.2f}, {spread['block_bootstrap_95_ci'][1]:.2f}]，正式状态 `{spread['status']}`。",
        "", f"Deribit 长周期成交代理产生 {deribit_spread['completed_trades']} 笔完整交易，平均 PnL {deribit_spread['mean_pnl_usd']:.2f} USD，95% CI [{deribit_spread['block_bootstrap_95_ci'][0]:.2f}, {deribit_spread['block_bootstrap_95_ci'][1]:.2f}]，状态 `{deribit_spread['status']}`。",
        "", f"亏损归因显示 PnL 与 BTC 收益相关系数 {attribution['pnl_correlations']['spot_return']:.2f}、与最大回撤相关系数 {attribution['pnl_correlations']['maximum_drawdown']:.2f}、与 ATM IV 变化相关系数 {attribution['pnl_correlations']['atm_iv_change_30d']:.2f}，而与 skew 变化仅 {attribution['pnl_correlations']['put_skew_change_30d']:.2f}。主要风险来自方向/Gamma 与整体 IV 扩张，而不是 skew 没有回落。",
        "", f"每日 Delta 对冲后平均 PnL 从 {hedged['mean_unhedged_pnl_usd']:.2f} 降至 {hedged['mean_hedged_pnl_usd']:.2f} USD；即使假设零对冲成本仍为 {hedged['zero_hedge_cost_mean_pnl_usd']:.2f} USD。对冲改善下跌子样本，却移除了上涨子样本原有的正 Delta 收益。",
        "", "阈值敏感性中，0.15 BTC 门槛把最差单笔从 -1,929.51 改善到 -1,397.91 USD、20% ES 从 -1,002.91 改善到 -810.15 USD，但平均 PnL 仍为 -66.56 USD。该网格属于同样本探索，不是样本外参数选择。",
        "", "## 限制", "", "- Deribit 长期曲面由成交采样而非完整盘口构造。", "- Deribit 价差版本使用按方向筛选的日内成交代理，不是同步 bid/ask。", "- index price 被用作 forward 代理。", "- Binance 盘口样本不足以形成策略有效性结论。", "- 本报告不是投资建议。", ""]
    (output/"REPORT.md").write_text("\n".join(report), encoding="utf-8")
    write_json(output/"readiness.json", readiness); _svg(output/"summary.svg", term, skew)
    return output


def _load(path): return json.loads(path.read_text(encoding="utf-8"))


def _svg(path, term, skew):
    horizons=[7,14,30,60,90]; width,height=1000,520; baseline=230; scale=18
    bars=[]
    for i,h in enumerate(horizons):
        x=90+i*175; vrp=term["horizons"][f"{h}d"]["mean_vrp_vol_points"]
        change=skew["horizons"][f"{h}d"]["mean_future_skew_change"]*100
        bars.append(f'<rect x="{x}" y="{baseline-vrp*scale}" width="55" height="{vrp*scale}" fill="#6750A4"/><text x="{x+27}" y="260" text-anchor="middle">{h}D</text><rect x="{x+65}" y="{360}" width="55" height="{-change*scale}" fill="#D97706"/><text x="{x+92}" y="{380-change*scale}" text-anchor="middle">{change:.1f}</text>')
    svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="#fbfaf7"/><text x="50" y="35" font-size="22" font-weight="bold">BTC term VRP and high-skew mean reversion</text><text x="50" y="70" fill="#6750A4">Mean IV - forward RV (vol points)</text><line x1="50" y1="{baseline}" x2="950" y2="{baseline}" stroke="#777"/>{''.join(bars)}<text x="50" y="330" fill="#D97706">Mean future 25Δ put-skew change (vol points)</text></svg>'''
    path.write_text(svg, encoding="utf-8")


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--data-root",type=Path,default=Path("data")); args=parser.parse_args(argv)
    print(generate(args.data_root))


if __name__ == "__main__": main()
