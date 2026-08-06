# Examples

这些示例对应当前的模块边界：

| 示例 | 说明 | 是否访问真实外部行情 |
| --- | --- | --- |
| `market/binance_spot_trade_stream.py` | 直接使用 Integration connection 监听 BTCUSDT trade | 是 |
| `market/binance_spot_runtime.py` | 通过 Market runtime service 订阅并消费 Binance 行情 | 是 |
| `market/binance_options_runtime.py` | 自动发现 BTC 期权并只读轮询 Quote，不需要 API key | 是 |
| `market/massive_stocks_runtime.py` | 通过 Market application 订阅 Massive 实时 AAPL Quote | 是 |
| `strategies/massive_aapl_quote.py` | 策略通过 `Quote` 订阅 Massive provider 的 AAPL 行情 | 是 |
| `configs/massive_aapl_quote_paper.toml` | 用 paper runtime 启动策略，接收真实 Massive AAPL 行情，不连接 IBKR 下单 | 是 |
| `strategies/massive_spy_option_quote.py` + `configs/massive_spy_option_quote_paper.toml` | 通过 Massive Options WebSocket 订阅单个 SPY 期权 Quote | 是 |
| `strategies/massive_spy_crash_put.py` + `configs/massive_spy_crash_put_paper.toml` | 策略先从 Reference 选择 SPY 期权合约，再以动态选择订阅候选链；Reference 目录变化时 Market Actor 增量更新订阅；`on_start` 后由 Market Warmup 加载历史 Bar，`on_data` 选择有限损失的 Crash Put | 是 |
| `strategies/massive_option_quote_runtime.py` | 不经过账户/执行模块，直接运行策略并打印 Massive 期权实时 Quote | 是 |
| `strategies/binance_equity_aapl_quote.py` + `configs/binance_equity_aapl_quote_live.toml` | 策略持续打印 Binance 股票 AAPL bid/ask | 是 |
| `strategies/binance_spy_limit_buy_live.py` + `configs/binance_spy_limit_buy_live.toml` | 通过 Binance Stocks Trading API 提交一次 SPY 1 股、750 美元限价买入 | 是（需要 trade key） |
| `strategies/btc_sma.py` + `configs/btc_sma_backtest.toml` | 通过 launch/composition 运行一个最小 SMA 回测 | 否 |
| `strategies/binance_btc_hold_paper.py` + `configs/binance_btc_hold_paper.toml` | 首个 trade 买入，持有 10 分钟后卖出 | 是 |
| `strategies/account_context_live.py` + `configs/account_context_live.toml` | 通过 `context.accounts` 读取 live 账户余额和仓位，不下单 | 否（账户读取是 live） |

## 1. 直接监听 Binance connection

```bash
uv run --extra crypto-realtime python examples/market/binance_spot_trade_stream.py --events 5
```

这个示例只展示 Integration application 的连接能力，不创建 strategy、account
或 runtime。它适合检查 WebSocket、连接能力和 payload translator。

## 2. Binance BTC 期权报价

该示例默认自动发现 Binance BTC 期权并选择最近到期合约，只读取报价，不需要 API
key，也不会下单：

```bash
uv run python examples/market/binance_options_runtime.py --events 5
```

也可以指定具体的 Binance 期权 symbol：

```bash
uv run python examples/market/binance_options_runtime.py \
  --symbol BTC-260925-60000-C --events 5
```

Binance Options 当前通过原生 WebSocket 接入 Market stream 边界，支持动态订阅和退订。

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

## 5. IBKR broker connection

IBKR 在项目中作为 broker 接入，底层使用可选的 `ib_async` driver；订单、账户和
执行回报都先转换为项目的 canonical application models，单元测试通过 mock driver
运行，不需要启动 TWS 或 IB Gateway。

安装依赖：

```bash
uv sync --extra ibkr
```

启动 IB Gateway/TWS 后，可以通过 composition 使用 `connect_ibkr(...)` 创建
paper 或 live connection。paper 默认端口为 `4002`，live 默认端口为 `4001`；
host、port、client id 和 timeout 在 infrastructure factory 中注入，业务层不依赖
`ib_async` 对象。

## 6. Binance BTC 10 分钟 paper 策略

先创建一个本地 paper 账户：

```bash
uv run kairospy account simulate paper_btc \
  --broker binance --environment paper --product-family spot \
  --balance USDT=10000
```

然后启动：

```bash
uv run --extra crypto-realtime kairospy launch start \
  examples/configs/binance_btc_hold_paper.toml
```

策略使用行情事件时间计时，而不是本地 sleep：首个 `TradePrint` 触发买入，
后续事件时间达到 10 分钟后发出目标仓位为 0 的卖出 intent。如果 10 分钟内
没有新行情，策略会在下一条行情到达时退出。

## 7. Binance 股票 AAPL 行情策略

Binance Stocks Trading 的行情接口需要 API key。先创建一个只读 credential 和
一个 live 账户（API secret 只用于账户/credential 配置，不会写入策略）：

```bash
uv run kairospy account credential create binance_read \
  --broker binance --api-key "$BINANCE_API_KEY" --api-secret "$BINANCE_API_SECRET"
uv run kairospy account connect \
  --broker binance --environment live \
  --credential binance_read --alias aapl_reader
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

## 8. 通过 context 读取 live 账户

这个示例用于演示策略如何通过 `context.accounts` 读取账户视图、余额和仓位。
它使用 live 账户连接，但策略不会调用 `target_position`，也不会提交订单。

先在交易所创建一个只读 API key。建议关闭交易和提现权限，并且不要把 key 写入
配置文件或提交到 Git。下面以 Binance 为例：

```bash
export BINANCE_API_KEY="..."
export BINANCE_API_SECRET="..."

uv run kairospy account credential create account_context_read \
  --broker binance \
  --api-key "$BINANCE_API_KEY" \
  --api-secret "$BINANCE_API_SECRET"

uv run kairospy account connect \
  --broker binance \
  --environment live \
  --credential account_context_read \
  --alias account_context_demo
```

检查账户配置和策略入口：

```bash
uv run kairospy launch diagnose validate \
  examples/configs/account_context_live.toml
```

确认配置中的 `[accounts.main].ref` 是刚创建的账户 alias 后启动。这个示例显式只开启
`spot` 分区；如果要读取合约或 Binance Funding Wallet，需分别将 `segments` 改为
`['usd_m_futures']` 或 `['funding']`：

```bash
uv run kairospy launch start \
  examples/configs/account_context_live.toml
```

示例会打印账户身份、余额和当前仓位。`live` 在这里表示读取真实账户快照，
不表示策略具有交易权限；安全性仍取决于 API key 的权限配置。

策略读取接口有两种明确用法：只有一个已配置分区时使用
`context.accounts.only()`；需要选择具体账户和分区时使用：

```python
futures = (
    context.accounts
    .account("binance_zhaoqian888666")
    .segment("usd_m_futures")
    .view()
)
```

Binance Funding Wallet 不是 Spot 或 Simple Earn。将 launch 配置中的分区显式改为
`segments = ["funding"]` 后，策略可以用同样的 `.segment("funding").view()` 读取，
但该分区只提供余额读取，不提供交易能力。

## 9. Binance Stocks Trading 下单 SPY

Binance Stocks Trading 的下单接口使用普通 Binance API key/secret 的签名认证，
但 key 必须开启交易权限，并且账户需要先接受美股免责声明。本示例提交
`SPY`、`BUY`、`LIMIT`、`price=750`、`quantity=1`，默认使用 `USDC` 和 `MAIN`
钱包。

先创建一个只允许交易、不允许提现的 trade credential，并把它绑定到 live 账户的
`[credentials.trade]`。不要复用只读 credential，也不要把 secret 写进策略代码：

```bash
uv run kairospy account credential create binance_spy_trade \
  --broker binance \
  --api-key "$BINANCE_API_KEY" \
  --api-secret "$BINANCE_API_SECRET"
```

然后把它加入现有 Binance 账户的 trade credential（命令会校验 key 的交易权限）：

```bash
uv run kairospy account credential add \
  binance_zhaoqian888666 trade \
  --ref binance_spy_trade
```

确认 `/sapi/v1/equity/account/disclaimer` 已签署后，检查配置并启动：

```bash
uv run kairospy launch diagnose validate \
  examples/configs/binance_spy_limit_buy_live.toml
uv run kairospy launch start \
  examples/configs/binance_spy_limit_buy_live.toml
```

该策略启动时只发出一次 SPY 限价买入 intent。`live.safety.max_order_notional = 750`
会限制这笔示例订单的最大名义金额；订单是否成交取决于市场价格和交易时段。

## 9. 同时比较 Massive 与 Binance 的美股 AAPL 报价

先准备一个 paper 账户和两个只读行情 credential：

```bash
uv run kairospy account simulate paper --broker binance --environment paper --product-family spot --balance USDT=10000
uv run kairospy account credential create binance_read --broker binance --api-key "$BINANCE_API_KEY" --api-secret "$BINANCE_API_SECRET"
```

Massive credential 可以沿用 `MASSIVE_API_KEY`，也可以按项目 credential 命令创建
名为 `massive_stocks` 的 credential。然后运行：

```bash
uv run kairospy launch start examples/configs/compare_massive_binance_aapl_paper.toml
```

链路是：策略的两个 `context.subscribe` → Market application 的两个订阅 →
System-owned integration runtime 创建 Massive WebSocket 和 Binance Stocks REST
polling connection → canonical `Quote` → 策略 `on_data`。策略等两边都收到报价后，
打印两边中间价、`Massive - Binance` 绝对价差和百分比价差，不会下单。

## 8. 同时比较 Binance 与 Hyperliquid 的 BTC 现货价格

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
