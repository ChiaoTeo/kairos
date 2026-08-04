# Examples

这些示例对应当前的模块边界：

| 示例 | 说明 | 是否访问真实外部行情 |
| --- | --- | --- |
| `market/binance_spot_trade_stream.py` | 直接使用 Integration connection 监听 BTCUSDT trade | 是 |
| `market/binance_spot_runtime.py` | 通过 Market runtime service 订阅并消费 Binance 行情 | 是 |
| `market/massive_stocks_runtime.py` | 通过 Market application 订阅 Massive 实时 AAPL Quote | 是 |
| `strategies/massive_aapl_quote.py` | 策略通过 `Quote` 订阅 Massive provider 的 AAPL 行情 | 是 |
| `configs/massive_aapl_quote_paper.toml` | 用 paper runtime 启动策略，接收真实 Massive AAPL 行情，不连接 IBKR 下单 | 是 |
| `strategies/massive_spy_option_quote.py` + `configs/massive_spy_option_quote_paper.toml` | 通过 Massive Options WebSocket 订阅单个 SPY 期权 Quote | 是 |
| `strategies/massive_option_quote_runtime.py` | 不经过账户/执行模块，直接运行策略并打印 Massive 期权实时 Quote | 是 |
| `strategies/binance_equity_aapl_quote.py` + `configs/binance_equity_aapl_quote_live.toml` | 策略持续打印 Binance 股票 AAPL bid/ask | 是 |
| `strategies/btc_sma.py` + `configs/btc_sma_backtest.toml` | 通过 launch/composition 运行一个最小 SMA 回测 | 否 |
| `strategies/binance_btc_hold_paper.py` + `configs/binance_btc_hold_paper.toml` | 首个 trade 买入，持有 10 分钟后卖出 | 是 |

## 1. 直接监听 Binance connection

```bash
uv run --extra crypto-realtime python examples/market/binance_spot_trade_stream.py --events 5
```

这个示例只展示 Integration application 的连接能力，不创建 strategy、account
或 runtime。它适合检查 WebSocket、连接能力和 payload translator。

## 2. 监听 Market runtime

```bash
uv run --extra crypto-realtime python examples/market/binance_spot_runtime.py --events 5
```

这个示例展示：

```text
IntegrationConnectionAssembly
  -> PublicMarketAccess
  -> LiveMarketDataService
  -> MarketDataSubscriptionSpec
  -> Message
```

## 3. Massive 实时 AAPL 行情

先设置 Massive API key：

```bash
export MASSIVE_API_KEY="..."
uv run --extra massive python examples/market/massive_stocks_runtime.py --events 10
```

该示例只连接 Massive provider，不连接 broker，也不会下单。策略通过
`MarketDataSubscriptionSpec(..., Quote)` 收到标准化后的 AAPL `Quote`。

通过统一策略 runtime 启动：

```bash
uv run --extra massive kairos launch start \
  examples/configs/massive_aapl_quote_paper.toml
```

## 4. 运行最小回测

```bash
uv run kairospy launch diagnose validate examples/configs/btc_sma_backtest.toml
uv run kairospy launch start examples/configs/btc_sma_backtest.toml
```

该示例只覆盖 backtest composition，不需要 Binance API credential。真实交易前，
应先修改配置中的数据范围并完成 backtest/paper 验证。

## 5. Binance BTC 10 分钟 paper 策略

先创建一个本地 paper 账户：

```bash
uv run kairospy account create paper_btc \
  --broker binance --environment paper --book spot \
  --currency USDT --cash 10000
```

然后启动：

```bash
uv run --extra crypto-realtime kairospy launch start \
  examples/configs/binance_btc_hold_paper.toml
```

策略使用行情事件时间计时，而不是本地 sleep：首个 `TradePrint` 触发买入，
后续事件时间达到 10 分钟后发出目标仓位为 0 的卖出 intent。如果 10 分钟内
没有新行情，策略会在下一条行情到达时退出。

## 6. Binance 股票 AAPL 行情策略

Binance Stocks Trading 的行情接口需要 API key。先创建一个只读 credential 和
一个 live 账户（API secret 只用于账户/credential 配置，不会写入策略）：

```bash
uv run kairospy account credential create binance_read \
  --broker binance --api-key "$BINANCE_API_KEY" --api-secret "$BINANCE_API_SECRET"
uv run kairospy account create aapl_reader \
  --broker binance --environment live \
  --credential binance_read --credential-role readonly
```

如果账户已经绑定了其它 credential id，只需要把 `[account].ref` 改成对应账户；
行情 feed 默认复用该账户的 readonly credential。

然后运行：

```bash
uv run kairospy launch start \
  examples/configs/binance_equity_aapl_quote_live.toml
```

策略通过 `context.subscribe(..., Quote)` 订阅 AAPL。由于 Binance 股票行情目前
通过 REST 最新报价接口提供，integration 会在后台轮询并把每次有效报价送到
策略的 `on_data`，默认间隔约 5 秒。

## 7. 同时比较 Binance 与 Hyperliquid 的 BTC 现货价格

示例策略为 `examples/strategies/compare_binance_hyperliquid_btc.py`。它同时订阅
Binance 的 `BTC/USDT` 和 Hyperliquid 的 `BTC/USDC` 现货 `Quote`，打印两边的中间价
和价差，不会自动下单。

先将 `examples/configs/compare_binance_hyperliquid_btc_live.toml` 中的
`account.ref` 改成当前 workspace 中已有的账户，然后运行：

```bash
uv run --extra crypto-realtime kairospy launch start \
  examples/configs/compare_binance_hyperliquid_btc_live.toml
```

如果只想观察公共行情，不希望启动时刷新 Binance 私有账户，可以使用 paper
配置。它仍然订阅实时 Binance/Hyperliquid 公共行情：

```bash
uv run --extra crypto-realtime kairospy launch start \
  examples/configs/compare_binance_hyperliquid_btc_paper.toml
```

## 依赖说明

真实行情示例需要项目的 `crypto-realtime` optional extra。若运行环境通过 SOCKS
代理访问网络，还需要额外安装 `python-socks`。
