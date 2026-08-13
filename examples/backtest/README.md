# 首批回测策略

这两个策略对应设计文档规定的第一批验收对象：

- `quote_strategy.py`：Binance Spot BTCUSDT Quote 驱动策略。
- `hourly_bar_strategy.py`：Massive SPY `1h` Bar 驱动策略。

示例策略只依赖 `kairospy.strategy` 公共协议。它们需要在 workspace 中配置名为
`paper-account` 的模拟账户，并分别将 Market Replay 数据集配置为 Quote 或 `1h` Bar。

SPY 的订单在下一条可执行行情上撮合；策略不会使用当前小时 Bar 的 close 回溯成交。

仓库内的可运行入口是：

```bash
uv run kairos launch start --config config/launches/binance-btcusdt-quote-backtest.toml \
  --workspace "$PWD" --output json
uv run kairos launch wait binance-btcusdt-quote-backtest \
  --workspace "$PWD" --output json

uv run kairos launch start --config config/launches/massive-spy-hourly-bar-backtest.toml \
  --workspace "$PWD" --output json
uv run kairos launch wait massive-spy-hourly-bar-backtest \
  --workspace "$PWD" --output json
```

`examples/backtest/data/` 是确定性的规范化 fixture，用于先验证运行时和撮合链路；
正式历史回测应把 launch 配置的 `events` 替换为 Market CLI 下载并校验过的数据集。

真实历史 Bar 数据的下载入口是 `kairospy market data download`（不是一次性
`kairos-market-cli`）；Massive 需要 `MASSIVE_API_KEY`，Binance Spot 不需要 API key。
Binance 历史接口当前提供 Kline/Bar，Quote 策略的盘口回测仍应使用真实 bid/ask
数据集，或明确标记为 synthetic quote。
