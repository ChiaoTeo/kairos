# Trader

一个以 `InstrumentDefinition + Catalog + Ledger` 为唯一事实模型的多资产量化研究、回测和交易编排工具。目前覆盖：

- 股票、ETF、上市期权与 SPXW；
- 加密现货、线性/反向永续和交割合约；
- 加密期权；
- 多账户、多资产、reporting currency、Funding、公司行为、行权/指派、到期结算；
- 确定性回测、paper/testnet 编排、对账、事件日志与 kill switch。

期权研究框架正按 `Market Data -> Pricing/Vol Engine -> Strategy -> Backtest -> Risk Analytics`
建设。目标架构、接口约束和分阶段验收标准见
[`docs/options_research_architecture.md`](docs/options_research_architecture.md)。

无需连接 Venue 即可验证内部期权定价、IV 和 Greeks：

```bash
./pyenv/bin/python -m trading pricing option \
  --model black_scholes --right call --underlying 100 --strike 100 \
  --years 1 --rate 0.05 --market-price 10.45058357
```

对已保存的期权 dataset 逐 slice 校准并持久化内部曲面：

```bash
./pyenv/bin/python -m trading vol calibrate --dataset mock-profit_target-development
```

运行 spot/vol/time 完整重估并输出 Greeks PnL explain：

```bash
./pyenv/bin/python -m trading risk scenario \
  --right put --underlying 6000 --strike 5700 --years 0.1 \
  --rate 0.04 --volatility 0.25 --quantity -2 \
  --spot-shock -0.10 --vol-shock 0.05 --time-advance-days 1
```

第一组可复现研究位于
[`research/spxw_put_skew/`](research/spxw_put_skew/README.md)，研究 25Δ Put skew
均值回复及其对 25Δ/10Δ Bull Put Spread 的样本外解释力。Notebook 会在样本不足时明确返回
`INSUFFICIENT_DATA`，不会从 synthetic fixture 推导收益结论。

## 最快体验

不连接任何外部 Venue，直接运行两个多资产闭环：

```bash
./pyenv/bin/python -m trading backtest run --strategy covered-call
./pyenv/bin/python -m trading backtest run --strategy spot-perp-carry
```

每个命令都会输出 Conservative 和 Stress 结果，并将带 audit hash 的结果保存到 `data/backtests/reference/`。

SPXW 确定性回测：

```bash
./pyenv/bin/python -m trading backtest mock --scenario profit_target --split development
./pyenv/bin/python -m trading backtest run \
  --strategy bull-put-spread \
  --dataset mock-profit_target-development
```

## Catalog

Binance public reference/testnet：

```bash
./pyenv/bin/python -m trading catalog sync \
  --venue binance --environment testnet \
  --products spot,perpetual,future --symbols BTCUSDT,BTCUSDT_260925
```

IBKR 股票与期权（期权格式为 `SYMBOL:YYYYMMDD:STRIKE:C|P`）：

```bash
./pyenv/bin/python -m trading catalog sync \
  --venue ibkr --environment paper \
  --products equity,option \
  --symbols AAPL,AAPL:20260821:250:C
```

## 研究数据

Massive 研究数据源的目标架构、时间与身份契约、数据湖规划、分阶段实施和验收标准见
[`docs/market_data_provider_integration_plan.md`](docs/market_data_provider_integration_plan.md)。接入实现应通过统一 Catalog、canonical pipeline
和 Dataset ID，不允许研究代码直接依赖供应商 SDK 对象。

Massive REST/Flat File 请求只允许通过 `https://api.massiveprivateserver.site`，WebSocket 只允许
`wss://socket.massiveprivateserver.site`。API key 仅从环境变量读取：

```bash
export MASSIVE_API_KEY='...'

./pyenv/bin/pip install -e '.[data,massive]'
./pyenv/bin/python -m trading data massive-fetch \
  --resource option-contracts --underlying SPX --start 2026-07-15

./pyenv/bin/python -m trading data prepare-massive-options \
  --dataset-id options.us.massive.spxw.20260715.v1 \
  --underlying SPX \
  --option-tickers O:SPXW260717C06000000,O:SPXW260717P06000000 \
  --start 2026-07-15T13:30:00+00:00 \
  --end 2026-07-15T20:00:00+00:00

./pyenv/bin/python -m trading data build-massive-slices \
  --source-dataset options.us.massive.spxw.20260715.v1 \
  --output-dataset spxw.massive.20260715.v1 \
  --start 2026-07-15T13:30:00+00:00 \
  --end 2026-07-15T20:00:00+00:00 \
  --risk-free-rate 0.043
```

若账户不能读取 `I:SPX` 历史聚合，SPXW ingestion 仍会保存历史 bid/ask；Curated builder 只在同到期、
同执行价的 Call/Put 双边报价均已在当时可见且未过期时，按 put-call parity 构造 synthetic forward。
manifest 和质量信息会明确标记该来源。要求官方 SPX 收盘价或结算价的研究仍会阻断。

Flat File 下载器会在纽约工作日 09:30–16:00 拒绝启动，并在下载前检查 150 GB/月用量上限：

```bash
./pyenv/bin/python -m trading data massive-flat-file --operation usage
./pyenv/bin/python -m trading data massive-flat-file --operation status --key '<file-key>'
./pyenv/bin/python -m trading data massive-flat-file --operation download --key '<file-key>'
```

OPRA Day Aggregates 支持按 `[start,end)` 交易日范围分批规划和下载。默认每次最多处理 5 个尚未
落地的文件；重复运行会跳过已有完整 receipt 的文件并继续后续日期：

```bash
# 盘中可安全执行：只查询前三个待处理文件的缓存状态并写批次报告
./pyenv/bin/python -m trading data massive-flat-file-batch \
  --start 2026-01-01 --end 2026-07-16 --max-files 3 --dry-run

# 纽约 16:00 后执行；每次最多下载 5 个新文件
./pyenv/bin/python -m trading data massive-flat-file-batch \
  --start 2026-01-01 --end 2026-07-16 --max-files 5
```

批次报告保存在 `data/source/provider=massive/resource=flat-files/batches/`，每个交易日会明确记录
`already_downloaded`、`downloaded`、`caching`、`deferred_by_batch_limit` 或 `error`。服务端仍会在
纽约工作日 09:30–16:00 硬拒绝实际下载。

下载范围完整后，离线构建冻结 inventory、月度 SPXW Parquet 和每日滚动代表序列；转换不需要 API key：

```bash
./pyenv/bin/python -m trading data prepare-spxw-day-aggs \
  --dataset-id options.us.massive.spxw.day-aggs.2026-ytd.v2 \
  --start 2026-01-01 --end 2026-07-15
```

Source 仍按 request fingerprint 不可变保存；年度 inventory 将 trading date 映射到 file key、request
fingerprint、路径、字节数和 SHA-256。转换器按交易日历证明无缺日/重复，流式过滤 `O:SPXW`，校验
OCC ticker、OHLC 和 `window_start`，再写入按月分区的 ZSTD Parquet。Notebook 读取 Curated Dataset
和 `daily_representatives.parquet`，不扫描 request 目录。

数据落地后用两本 Notebook 做人工体检；它们只读取受管数据，不会发起 API 请求：

- [`examples/massive_data_quality.ipynb`](examples/massive_data_quality.ipynb)：HTTPS lineage、Source receipt、事件覆盖、延迟、点差和重放视图；
- [`examples/massive_research_readiness.ipynb`](examples/massive_research_readiness.ipynb)：MarketSlice、报价覆盖、标的价格、内部 IV/Greeks 和 feature。
- [`examples/spxw_popular_options_2026.ipynb`](examples/spxw_popular_options_2026.ipynb)：读取完整 2026 YTD Day Aggregates，展示具体 ticker 热门榜，以及每日滚动的最活跃 Call/Put、0DTE ATM Call/Put、成交量和活跃合约数。
- [`examples/nvda_options_2026.ipynb`](examples/nvda_options_2026.ipynb)：展示 NVDA 2026 YTD 调整后日 K 线，并对全部 NVDA OPRA 期权日聚合展示内部 close-based IV 密度、近 ATM IV 时间序列和波动率微笑。

可通过环境变量切换数据集：

```bash
MASSIVE_EVENT_DATASET='<canonical-dataset-id>' \
MASSIVE_CURATED_DATASET='<historical-dataset-id>' \
./pyenv/bin/jupyter lab examples/
```

热门 SPXW Notebook 默认读取已转换的年度 Dataset；可用环境变量切换到兼容版本：

```bash
SPXW_DAY_AGG_DATASET='options.us.massive.spxw.day-aggs.2026-ytd.v2' \
./pyenv/bin/jupyter lab examples/spxw_popular_options_2026.ipynb
```

当前已验证 Dataset 覆盖 2026-01-02 至 2026-07-14 的 132 个交易日和 1,009,000 条 SPXW
日聚合。具体 ticker 会到期，Notebook 因而同时保留 ticker 排名和每日滚动代表序列；后者用于年度
连续观察，但不能被解释为一张可持续持有的单一合约。

NVDA Notebook 使用以下受管 Dataset：

```bash
# 已下载的 OPRA 年度 Source 中离线过滤 NVDA
./pyenv/bin/python -m trading data prepare-option-day-aggs \
  --dataset-id options.us.massive.nvda.day-aggs.2026-ytd.v1 \
  --option-root NVDA --start 2026-01-01 --end 2026-07-15

# 获取并归档调整后 NVDA 股票日线
./pyenv/bin/python -m trading data prepare-massive-equity-day-aggs \
  --dataset-id equity.us.massive.nvda.day-aggs.2026-ytd.v1 \
  --ticker NVDA --start 2026-01-01 --end 2026-07-16

# 物化全部期权日收盘内部 IV
./pyenv/bin/python -m trading data prepare-option-day-iv \
  --dataset-id features.us.massive.nvda.close-iv.2026-ytd.v1 \
  --option-dataset options.us.massive.nvda.day-aggs.2026-ytd.v1 \
  --equity-dataset equity.us.massive.nvda.day-aggs.2026-ytd.v1 \
  --risk-free-rate 0.04 --dividend-yield 0.0003

./pyenv/bin/jupyter lab examples/nvda_options_2026.ipynb
```

IV Feature Dataset 对所有输入行保留 `solver_status`；到期日收盘或违反套利边界的价格不会被静默
删除。当前 303,007 条 NVDA 期权日聚合中有 279,782 条收敛，覆盖率约 92.3%。该 IV 使用固定
利率/股息率和 Black-Scholes European approximation，适合探索，不等同于 vendor IV 或可执行报价 IV。

### 历史 K 线与 Notebook

下载并保存无需 API key 的 Binance 历史 OHLCV（`--end` 为开区间，时间必须包含时区）：

```bash
./pyenv/bin/pip install -e '.[notebook]'
./pyenv/bin/python -m trading history download \
  --dataset-id btcusdt-1h-2026h1 \
  --instrument crypto:binance:spot:BTCUSDT --symbol BTCUSDT \
  --interval 1h --start 2026-01-01T00:00:00+00:00 --end 2026-07-01T00:00:00+00:00

./pyenv/bin/python -m trading history show --dataset-id btcusdt-1h-2026h1
```

数据保存在 `data/history/<dataset-id>/metadata.json` 和 `bars.csv`。Notebook 中加载、计算指标和叠加展示：

```python
from trading.history import BarRepository

data = BarRepository().load("btcusdt-1h-2026h1")
df = data.frame()
indicators = {
    "SMA 20": df.close.rolling(20).mean(),
    "EMA 50": df.close.ewm(span=50).mean(),
}
figure, axes = data.plot(indicators=indicators)
```

完整示例见 `examples/history_analysis.ipynb`。

在同一份历史数据上运行简单的 `SMA(20, 50)` 多头交叉策略：

```bash
./pyenv/bin/python -m trading history backtest-sma \
  --dataset-id btcusdt-1h --fast 20 --slow 50 --fee-bps 10
```

信号使用当前 K 线收盘价计算，并在下一根 K 线开盘成交；回测不会使用尚未发生的价格。

合约使用 `--market usdm` 或 `--market coinm`。存储和展示接口与数据源解耦，后续历史数据 provider 可继续接入 IBKR。

Binance 加密期权的 reference、行情、账户、限价执行与现金结算已经独立建模。Binance 没有等价的 options testnet，因此 CLI 会拒绝 `--product options --environment testnet`；options 只允许显式 `live --confirm-live`，并继续经过对账、限额和 kill switch。

SPX/SPXW 是一个专用研究切片；它不会限制 IBKR 的股票能力：

```bash
./pyenv/bin/python -m trading research capture --host 127.0.0.1 --port 4001
./pyenv/bin/python -m trading research capture-series \
  --venue ibkr --dataset-id spxw-20260714 --samples 60 --interval-seconds 60
```

股票、ETF、加密现货/永续使用 Catalog 中的内部 InstrumentId 做通用序列采集，不要求期权标的价字段：

```bash
./pyenv/bin/python -m trading research capture-series \
  --venue ibkr --environment paper \
  --instruments equity:us:AAPL \
  --dataset-id aapl-20260714 --samples 60 --interval-seconds 60

./pyenv/bin/python -m trading research capture-series \
  --venue binance --environment testnet \
  --instruments crypto:binance:spot:BTCUSDT,crypto:binance:perpetual:BTCUSDT \
  --dataset-id btc-20260714 --samples 60 --interval-seconds 60
```

单个 snapshot 用于标的研究；验证策略有效性应使用连续数据、保守成交模型、冻结参数和样本外 split。

## 对账与安全交易入口

所有外部状态命令必须显式提供 environment。Binance 凭据只从环境变量读取：

- testnet：`BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`；
- live：`BINANCE_LIVE_API_KEY` / `BINANCE_LIVE_API_SECRET`。

```bash
./pyenv/bin/python -m trading account reconcile \
  --venue binance --environment testnet --product spot --account-id testnet

./pyenv/bin/python -m trading trade run \
  --strategy spot-perp-carry --venue binance --environment testnet \
  --product spot --account-id testnet \
  --instrument crypto:binance:spot:BTCUSDT \
  --side buy --quantity 0.001 --limit-price 50000 \
  --market-data-ready
```

`live` 额外要求 `--confirm-live`。服务启动前强制进行 Catalog、行情、账户、执行与 Ledger/Venue reconciliation 检查；kill switch 触发后只允许 reduce-only。

提现能力未实现，API key 不应带提现权限。建议 Venue 分离只读/交易 key，并使用独立子账户。

## 测试

```bash
./pyenv/bin/python -m unittest discover -s tests -v
```

真实连接测试默认跳过：

- `RUN_IBKR_INTEGRATION=1`：IBKR 只读研究与 paper account 查询；
- `RUN_BINANCE_TESTNET=1` 加 testnet 环境变量：Binance public/time/account contract test。

Mock 数据的长期测试契约见 [docs/mock_data_spec.md](docs/mock_data_spec.md)。
