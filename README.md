# Kairos

一个以 `InstrumentDefinition + Catalog + Ledger` 为唯一事实模型的多资产量化研究、回测和交易编排工具。目前覆盖：

- 股票、ETF、上市期权与 SPXW；
- 加密现货、线性/反向永续和交割合约；
- 加密期权；
- 多账户、多资产、reporting currency、Funding、公司行为、行权/指派、到期结算；
- 确定性回测、paper/testnet 编排、对账、事件日志与 kill switch。

`kairos` 是唯一的产品、CLI 和 Python 包名；外部系统边界统一使用 `connectors`、`ports`、`Gateway`、`Client`、`Provider` 等职责命名。

> 第一次使用请从 [第一次研究：从一个假设走到可拒绝的 SMA 策略](docs/tutorial_first_research.md) 开始，不要直接连接 Paper 或 Live。

## 安装后新建自己的 Kairos 项目

普通用户不需要复制本仓库。安装后可以在任意空文件夹初始化自己的量化项目：

```bash
python3 -m pip install kairos
mkdir my-kairos-project
cd my-kairos-project
kairos init
python studies/starter.py
```

`kairos init` 会创建 `kairos.toml`、`pyproject.toml`、`data/`、`studies/`、`strategies/` 和一个可运行的 starter 脚本。默认不会覆盖已有文件；需要重写模板时显式使用 `kairos init --force`。

安装包只包含 Kairos 产品库和 CLI，不包含本仓库顶层 `studies/` 源码研究工作区。用户自己的研究代码应放在 `kairos init` 创建的项目目录中。

如果你是从源码参与开发，再使用 editable 安装：

```bash
python3 -m venv pyenv
./pyenv/bin/pip install -e '.[data,query,notebook]'
./pyenv/bin/kairos --help
```

## 新人先走这条路径

源码开发时，所有命令均在仓库根目录执行，要求 Python 3.11 或更高版本。

```bash
python3 -m venv pyenv
./pyenv/bin/pip install -e '.[data,query,notebook]'
./pyenv/bin/kairos --help
./pyenv/bin/kairos tutorial sma
```

最后一条命令使用仓库内置的确定性数据，不联网、不需要账户、不会下单。它会带你依次完成：

```text
Study -> Factor -> Strategy -> Backtest -> Run Artifact
```

CLI 默认输出本地化、面向人的字段和表格。脚本、CI 或其他程序调用时，应显式增加 `--format json`；需要固定语言时增加 `--lang zh-CN` 或 `--lang en-US`。

当前可用范围、外部 Paper/Testnet 门禁和下一批建设重点见
[当前产品状态](docs/current_product_status.md)。简短说：本地 deterministic 生命周期已可用；真实
Binance Testnet/IBKR Paper 仍必须通过 L4 soak、重启、对账和 kill-switch 证据后，才能被描述为外部就绪。

## 一句话理解系统

系统把“研究想法”逐层翻译为“可审计的账户事实”：

```text
Dataset Release
  -> Study / Factor
  -> Strategy
  -> Intent
  -> Execution Plan / Order
  -> Fill / TradeExecution
  -> Ledger Transaction
  -> Portfolio / Risk View
```

每一层只负责自己的问题。不要跨层传递供应商对象，也不要让策略直接改账户余额或调用交易所 SDK。

## 核心命名与边界

### 资产、产品与可交易标的

| 名称 | 回答的问题 | 边界与使用方式 |
| --- | --- | --- |
| `AssetId` | 记账单位是什么？ | 如 `USD`、`BTC`。用于现金、费用、结算和 Ledger；它不是股票或期权合约。 |
| `ProductType` / `InstrumentContractSpec` | 这是什么经济合约？ | 描述股票、现货、期货、永续、期权等产品属性；不保存 Venue symbol、tick 或账户状态。 |
| `ProductId` | 经济产品是谁？ | 表示跨 Venue 的经济身份；不等于某个交易场所的具体挂牌。 |
| `InstrumentId` | 系统内部稳定地指向哪个可定价/可交易标的？ | 业务代码、行情、策略、风险和账本统一使用它。不得从它反推交易所 symbol。 |
| `InstrumentDefinition` | 该标的的正式合约定义是什么？ | 绑定 `InstrumentId`、产品类型、合约条款和生命周期，是产品事实；不包含特定 Venue 的交易规则。 |
| `ListingDefinition` | 该标的在某个 Venue 如何挂牌和交易？ | 保存 Venue symbol、external id、tick、lot、最小名义金额等。Gateway 下单必须通过 Catalog 查它，不能猜 symbol。 |
| `VenueId` | 在哪个交易场所？ | 如 `binance`、`ibkr`；Venue 与数据供应商不是同一概念。 |
| `ProviderId` | 数据由谁提供？ | 如行情或参考数据供应商。Provider 可以覆盖多个 Venue，也可能不是 Venue。 |
| `AccountKey` | 哪个机构下的哪个账户？ | 由 `InstitutionId + AccountType + account_id` 组成；不要只用裸 `account_id` 假设全局唯一。 |

最重要的区别是：

```text
InstrumentId = 内部稳定身份
ListingDefinition.symbol = Venue 外部代码
AssetId = 记账单位
```

例如 BTC、BTC/USDT 现货和 BTC 永续不是同一个对象：`BTC` 是 `AssetId`，后两者是不同的 `InstrumentId`，且各自在 Binance 上拥有自己的 `ListingDefinition`。

### Catalog、Dataset 与 Release

系统有两类 Catalog，不要混用：

| 名称 | 管理什么 | 典型用途 |
| --- | --- | --- |
| Reference Catalog | 资产、产品、合约、挂牌、交易规则及其有效期 | 把内部 `InstrumentId` 解析到某个 Venue listing，检查合约和交易能力 |
| Data Catalog | 逻辑数据产品、不可变 Release、质量等级、状态和别名 | 发现、固定和复现研究/回测输入 |

数据系统中的核心名词：

| 名称 | 定义 | 不应被当成什么 |
| --- | --- | --- |
| `DataProductDefinition` / `DataProduct` / `DatasetKey` | 稳定的逻辑数据名，如 `market.ohlcv.crypto.binance.btc-usdt.1h` | 不是某次下载，也不保证内容永远不变；`DataProduct` 是 `DataProductDefinition` 的短别名 |
| `DatasetRelease` | 某个逻辑数据产品的一次不可变发布，带版本、内容 hash、时间范围和质量状态 | 不是“latest”这样的浮动引用 |
| Alias | 指向 Release 的便利名称 | 不能用于要求严格复现的最终证据；应记录解析后的 Release ID/hash |
| `DatasetLayer` | 数据加工层级：Source、Canonical、Curated、Features、Studies | 不是质量等级 |
| `QualityLevel` | 使用门槛：Q0 归档、Q1 完整性、Q2 研究、Q3 回测、Q4 生产 | 不代表数据所在 Layer |
| `DatasetStatus` | 治理状态，如 validated、approved、quarantined | 不等于质量等级；审批状态与质量证据是两个维度 |

原则：探索时可通过逻辑 Dataset 名发现数据；形成研究结论、回测结果或运行产物时，必须固定到不可变 `DatasetRelease` 和内容 hash。Notebook 和策略代码应通过 `ResearchDataClient` 读取，不直接依赖 `data/` 下的物理路径。

### Study、Factor、Strategy 与 Run

| 名称 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| Study | 记录可证伪假设、输入 Release、时间语义、样本划分和研究证据 | 不直接下单，不等同于策略 |
| Factor | 从当时可用信息计算可复用特征/信号 | 不决定账户、仓位、交易成本或订单类型 |
| Strategy | 把市场、Factor、组合和风险上下文映射为经济 `Intent` | 不调用 Venue API，不直接写 Ledger，不维护另一套账户真相 |
| Strategy Release | 冻结后的策略定义、参数和实现版本 | 不是可随运行临时修改的草稿 |
| Run | 某个 Strategy Release 在指定模式和输入上的一次执行 | 不是策略定义本身 |
| Run Artifact | Run 的可审计产物，记录输入、组合方式、结果和指纹 | 不是可变的运行配置文件 |

### Intent、Order、Fill、Position 与 Ledger

| 名称 | 含义 | 边界 |
| --- | --- | --- |
| `Intent` | 策略表达“想实现什么经济结果” | 如目标仓位、目标敞口、开/平结构、对冲、划转或撤单；它还不是 Venue 订单 |
| Execution Plan | Planner 根据账户、Catalog、能力和政策生成的执行方案 | 可以被风险、能力或安全门禁拒绝 |
| `Order` / Order Command | 发送给执行系统的具体指令及状态 | 必须满足 tick、lot、最小数量、限额、幂等和 kill switch 约束 |
| `Fill` / `TradeExecution` | 已发生的成交事实 | Order 不等于 Fill；提交成功也不等于成交 |
| Position | 由成交、结算和公司行为归约出的当前持仓视图 | 不是独立事实源，不应绕过 Ledger 手工修改 |
| Ledger Transaction | 至少两条、按资产平衡的不可变记账事务 | 是现金、仓位、费用、Funding、分红、结算等账户事实的最终来源 |
| Portfolio / Risk View | 从 Ledger、市场和定价信息派生的组合与风险投影 | 可重建，不应反向成为记账真相 |

执行主线是：

```text
Strategy 只产生 Intent
Planner 把 Intent 变成执行计划
Coordinator / Router 做就绪、能力、风险和安全检查
Execution Gateway 翻译为 Venue 请求
成交与生命周期事件进入 Ledger
Portfolio 和 Risk 从事实重新计算
```

### 五种运行模式

| 模式 | 数据/时钟 | 执行 | 适用场景 |
| --- | --- | --- | --- |
| `research` | 冻结数据 / 分析时钟 | 无 | 探索假设、构建和验证 Factor |
| `backtest` | 冻结数据 / replay | 成交模型 | 快速、确定性策略评估 |
| `historical-simulation` | 冻结数据 / replay | 模拟 Venue | 验证异步运行时、订单状态与恢复逻辑 |
| `shadow` | 实时或 Capture replay / 系统或回放时钟 | 无 | 计算完整决策和假设 Intent，但禁止发单 |
| `paper-trading` | 实时数据 / 系统时钟 | 模拟执行 | 在真实行情下验证运行与监控，不产生真实成交 |
| `live` | 实时数据 / 系统时钟 | 真实执行 Gateway | 真实交易；必须经过对账、安全门禁和显式确认 |

`paper/testnet` 是外部环境或账户语义，`paper-trading` 是系统运行模式，两者不要仅凭名称互换。Backtest 通过不代表 Live 安全；首次使用的推荐晋级顺序是：

```text
research -> backtest -> historical-simulation -> shadow -> paper-kairos/testnet -> live
```

## 常见误用

- 不要把 `BTCUSDT`、`AAPL` 等 Venue symbol 当作稳定 `InstrumentId`。
- 不要把 `AssetId`、`ProductId`、`InstrumentId` 混成一个字符串概念。
- 不要让研究代码、Notebook 或 Strategy 直接持有 IBKR/Binance/Massive SDK 对象。
- 不要通过文件路径固定研究输入；固定 `DatasetRelease`、内容 hash 和时间范围。
- 不要用当前时间或后修订数据回答历史时点的问题；区分 `event_time` 与 `available_time`。
- 不要把 Order accepted 当作已成交，也不要直接修改 Position；等待成交/结算事实进入 Ledger。
- 不要在未完成 Catalog、行情、账户、执行和 Ledger/Venue 对账前启动外部交易。
- 不要用 synthetic fixture 的盈利结果证明策略有效；它只证明机制可运行。

## 按目标选择入口

| 目标 | 推荐入口 |
| --- | --- |
| 第一次了解系统 | `./pyenv/bin/kairos tutorial sma` |
| 查找和检查历史数据 | `kairos data search`、`kairos data describe` |
| 做确定性策略回测 | `kairos run backtest --strategy ...` |
| 运行历史仿真 | `kairos run simulate --strategy ...` |
| 用 Capture 做零发单影子运行 | `kairos run shadow --strategy ...` |
| 在真实行情/fixture 上做模拟成交 | `kairos run paper --strategy ...` |
| 检查/晋级策略版本 | `kairos strategy status`、`kairos strategy check-promotion`、`kairos strategy promote` |
| 提交人工运维订单 | `kairos order submit` |
| 验证内部期权定价/IV | `kairos pricing option` |
| 校准波动率曲面 | `kairos vol calibrate` |
| 做情景风险重估 | `kairos risk scenario` |
| 同步产品与挂牌定义 | `kairos catalog sync` |
| 接入外部账户前检查 | `kairos account reconcile` |
| 外部 Paper/Testnet 验收 | `kairos runtime l4-preflight` 后按 runbook 执行 soak |
| 从运行成交生成执行校准 | `kairos runtime calibrate-execution` |

更完整的边界说明见 [系统架构](docs/architecture.md)，研究数据使用见 [研究数据指南](docs/research_data_guide.md)，首次研究的逐步操作见 [新人教程](docs/tutorial_first_research.md)。

## 功能与命令参考

期权研究框架正按 `Market Data -> Pricing/Vol Engine -> Strategy -> Backtest -> Risk Analytics`
建设。目标架构、接口约束和分阶段验收标准见
[`docs/options_research_architecture.md`](docs/options_research_architecture.md)。

无需连接 Venue 即可验证内部期权定价、IV 和 Greeks：

```bash
./pyenv/bin/kairos pricing option \
  --model black_scholes --right call --underlying 100 --strike 100 \
  --years 1 --rate 0.05 --market-price 10.45058357
```

对已保存的期权 dataset 逐 slice 校准并持久化内部曲面：

```bash
./pyenv/bin/kairos vol calibrate --dataset synthetic-profit_target-development
```

运行 spot/vol/time 完整重估并输出 Greeks PnL explain：

```bash
./pyenv/bin/kairos risk scenario \
  --right put --underlying 6000 --strike 5700 --years 0.1 \
  --rate 0.04 --volatility 0.25 --quantity -2 \
  --spot-shock -0.10 --vol-shock 0.05 --time-advance-days 1
```

第一组可复现研究位于
[`studies/spxw_put_skew/`](studies/spxw_put_skew/README.md)，研究 25Δ Put skew
均值回复及其对 25Δ/10Δ Bull Put Spread 的样本外解释力。Notebook 会在样本不足时明确返回
`INSUFFICIENT_DATA`，不会从 synthetic fixture 推导收益结论。

## 最快体验

不连接任何外部 Venue，直接运行两个多资产闭环：

```bash
./pyenv/bin/kairos backtest run --strategy covered-call
./pyenv/bin/kairos backtest run --strategy spot-perp-carry
```

每个命令都会输出 Conservative 和 Stress 结果，并将带 audit hash 的结果保存到 `data/backtests/reference/`。

SPXW 确定性回测：

```bash
./pyenv/bin/kairos backtest synthetic-scenario --scenario profit_target --split development
./pyenv/bin/kairos backtest run \
  --strategy bull-put-spread \
  --dataset synthetic-profit_target-development
```

SMA 主链使用新的 Strategy Release 入口：

```bash
./pyenv/bin/kairos --format json tutorial sma
./pyenv/bin/kairos --format json run backtest \
  --strategy sma-cross-v1@1.2.0 --fixture --fast 5 --slow 15
./pyenv/bin/kairos --format json run shadow \
  --strategy sma-cross-v1@1.2.0 --fixture --fast 5 --slow 15 \
  --run-root example-output/sma-shadow/runtime \
  --artifact-root example-output/sma-shadow/artifacts
```

新脚本统一使用带 `--strategy` 的通用入口；旧的策略专用运行命令不再作为产品入口展示。

真实行情模拟盘不需要账户凭据，也不会向 Binance 下单；它只拉公共现货 K 线，保存为可重放 capture，
再进入模拟成交链路：

```bash
./pyenv/bin/kairos --format json run paper \
  --strategy sma-cross-v1@1.2.0 \
  --live-binance-symbol BTCUSDT \
  --live-binance-interval 1m \
  --live-binance-limit 120 \
  --fast 5 --slow 15 \
  --run-root example-output/sma-live-market-paper/runtime \
  --artifact-root example-output/sma-live-market-paper/artifacts
```

Notebook 或脚本可以使用同一层产品 API：

```python
from kairos import Kairos

result = Kairos("data").backtest(
    strategy="sma-cross-v1@1.2.0",
    dataset="fixture:sma-bars-v1",
    parameters={"fast": 5, "slow": 15},
)
print(result.summary())
print(result.explain(at="2026-01-02T00:00:00Z"))
```

策略晋级必须附带可哈希证据：

```bash
./pyenv/bin/kairos --format json strategy check-promotion sma-cross-v1 \
  --version 1.2.0 \
  --to RESEARCH_VALIDATED \
  --evidence data/studies/<study>/<version>/results.json

./pyenv/bin/kairos --format json strategy promote sma-cross-v1 \
  --version 1.2.0 \
  --to RESEARCH_VALIDATED \
  --evidence data/studies/<study>/<version>/results.json \
  --actor reviewer@example \
  --capital-limit 10000 \
  --rollback-condition 'signal evidence invalidated'
```

晋级成功后，CLI 会返回 `evidence_bundle`，指向
`data/strategies/<strategy>/<version>/promotion-bundles/<stage>-<hash>/manifest.json`。这个文件是后续
Paper/Testnet readiness 和人工审查使用的稳定证据入口。
`check-promotion` 使用同一套 gate、evidence hash 和生命周期顺序检查，但不会改变 Strategy Release，
也不会写晋级记录。

## Catalog

Binance public reference/testnet：

```bash
./pyenv/bin/kairos catalog sync \
  --venue binance --environment testnet \
  --products spot,perpetual,future --symbols BTCUSDT,BTCUSDT_260925
```

IBKR 股票与期权（期权格式为 `SYMBOL:YYYYMMDD:STRIKE:C|P`）：

```bash
./pyenv/bin/kairos catalog sync \
  --venue ibkr --environment paper \
  --products equity,option \
  --symbols AAPL,AAPL:20260821:250:C
```

## 研究数据

Massive 研究数据源的目标架构、时间与身份契约、数据湖规划、分阶段实施和验收标准见
[`docs/market_data_provider_integration_plan.md`](docs/market_data_provider_integration_plan.md)。接入实现应通过统一 Catalog、canonical pipeline
和 Dataset ID，不允许研究代码直接依赖供应商 SDK 对象。

使用 Massive 建设美股横截面动量数据的产品定义、历史股票池、复权、质量门禁和实施路线见
[`docs/us_equity_momentum_data_plan.md`](docs/us_equity_momentum_data_plan.md)。

Massive REST/Flat File 请求只允许通过 `https://api.massiveprivateserver.site`，WebSocket 只允许
`wss://socket.massiveprivateserver.site`。API key 仅从环境变量读取：

```bash
export MASSIVE_API_KEY='...'

./pyenv/bin/pip install -e '.[data,massive]'
./pyenv/bin/kairos data provider-fetch --provider massive \
  --resource option-contracts --underlying SPX --start 2026-07-15

./pyenv/bin/kairos data plan \
  --connector-config examples/data/massive_connector.example.json \
  --dataset market.events.options.us.spxw --provider massive --venue opra \
  --start 2026-07-15T13:30:00+00:00 \
  --end 2026-07-15T20:00:00+00:00

./pyenv/bin/kairos data acquire \
  --connector-config examples/data/massive_connector.example.json \
  --dataset market.events.options.us.spxw --provider massive --venue opra \
  --start 2026-07-15T13:30:00+00:00 \
  --end 2026-07-15T20:00:00+00:00

./pyenv/bin/kairos data build-provider-slices --provider massive \
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
./pyenv/bin/kairos data provider-flat-file --provider massive --operation usage
./pyenv/bin/kairos data provider-flat-file --provider massive --operation status --key '<file-key>'
./pyenv/bin/kairos data provider-flat-file --provider massive --operation download --key '<file-key>'
```

OPRA Day Aggregates 支持按 `[start,end)` 交易日范围分批规划和下载。默认每次最多处理 5 个尚未
落地的文件；重复运行会跳过已有完整 receipt 的文件并继续后续日期：

```bash
# 盘中可安全执行：只查询前三个待处理文件的缓存状态并写批次报告
./pyenv/bin/kairos data provider-flat-file-batch --provider massive \
  --start 2026-01-01 --end 2026-07-16 --max-files 3 --dry-run

# 纽约 16:00 后执行；每次最多下载 5 个新文件
./pyenv/bin/kairos data provider-flat-file-batch --provider massive \
  --start 2026-01-01 --end 2026-07-16 --max-files 5
```

批次报告保存在 `data/source/provider=massive/resource=flat-files/batches/`，每个交易日会明确记录
`already_downloaded`、`downloaded`、`caching`、`deferred_by_batch_limit` 或 `error`。服务端仍会在
纽约工作日 09:30–16:00 硬拒绝实际下载。

下载范围完整后，离线构建冻结 inventory、月度 SPXW Daily OHLCV Parquet 和每日滚动代表序列；转换不需要 API key：

```bash
./pyenv/bin/kairos data prepare-spxw-daily-ohlcv \
  --dataset-id options.us.spxw.daily-ohlcv.2026-ytd.v2 \
  --start 2026-01-01 --end 2026-07-15
```

Source 仍按 request fingerprint 不可变保存；年度 inventory 将 trading date 映射到 file key、request
fingerprint、路径、字节数和 SHA-256。转换器按交易日历证明无缺日/重复，流式过滤 `O:SPXW`，校验
OCC ticker、OHLC 和 `window_start`，再写入按月分区的 ZSTD Parquet。Notebook 读取 Curated Dataset
和 `daily_representatives.parquet`，不扫描 request 目录。

数据落地后用两本 Notebook 做人工体检；它们只读取受管数据，不会发起 API 请求：

- [`examples/massive_data_quality.ipynb`](examples/massive_data_quality.ipynb)：HTTPS lineage、Source receipt、事件覆盖、延迟、点差和重放视图；
- [`examples/massive_research_diagnostics.ipynb`](examples/massive_research_diagnostics.ipynb)：MarketSnapshot、报价覆盖、标的价格、内部 IV/Greeks 和 feature。
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
SPXW_DAY_AGG_DATASET='options.us.spxw.daily-ohlcv.2026-ytd.v2' \
./pyenv/bin/jupyter lab examples/spxw_popular_options_2026.ipynb
```

当前已验证 Dataset 覆盖 2026-01-02 至 2026-07-14 的 132 个交易日和 1,009,000 条 SPXW
日聚合。具体 ticker 会到期，Notebook 因而同时保留 ticker 排名和每日滚动代表序列；后者用于年度
连续观察，但不能被解释为一张可持续持有的单一合约。

NVDA Notebook 使用以下受管 Dataset：

```bash
# 已下载的 OPRA 年度 Source 中离线过滤 NVDA
./pyenv/bin/kairos data prepare-option-daily-ohlcv \
  --dataset-id options.us.nvda.daily-ohlcv.2026-ytd.v1 \
  --option-root NVDA --start 2026-01-01 --end 2026-07-15

# 获取并归档调整后 NVDA 股票日线
./pyenv/bin/kairos data prepare-equity-daily-ohlcv --provider massive \
  --dataset-id equity.us.nvda.daily-ohlcv.2026-ytd.v1 \
  --ticker NVDA --start 2026-01-01 --end 2026-07-16

# 物化全部期权日收盘 implied volatility
./pyenv/bin/kairos data prepare-option-close-implied-volatility \
  --dataset-id features.us.massive.nvda.close-iv.2026-ytd.v1 \
  --option-dataset options.us.nvda.daily-ohlcv.2026-ytd.v1 \
  --equity-dataset equity.us.nvda.daily-ohlcv.2026-ytd.v1 \
  --risk-free-rate 0.04 --dividend-yield 0.0003

./pyenv/bin/jupyter lab examples/nvda_options_2026.ipynb
```

IV Feature Dataset 对所有输入行保留 `solver_status`；到期日收盘或违反套利边界的价格不会被静默
删除。当前 303,007 条 NVDA 期权日聚合中有 279,782 条收敛，覆盖率约 92.3%。该 IV 使用固定
利率/股息率和 Black-Scholes European approximation，适合探索，不等同于 vendor IV 或可执行报价 IV。

### 治理 OHLCV、Notebook 与 SMA 回测

历史 OHLCV 统一通过 Dataset Catalog 和 `ResearchDataClient` 使用，不再写入独立的
`data/history` CSV。先发现、诊断并准备达到 Q3 的不可变 Release：

```bash
./pyenv/bin/pip install -e '.[notebook]'
./pyenv/bin/kairos data search --dimension instrument=BTC-USDT --dimension frequency=1h
./pyenv/bin/kairos data describe --dataset market.ohlcv.crypto.binance.btc-usdt.1h
./pyenv/bin/kairos data prepare \
  --dataset market.ohlcv.crypto.binance.btc-usdt.1h \
  --start 2026-01-01T00:00:00+00:00 --end 2026-07-01T00:00:00+00:00 --quality Q3
```

Notebook 按逻辑产品或冻结 Release 读取，不接触物理路径：

```python
from kairos.data import OutputFormat, ResearchDataClient

data = ResearchDataClient("data")
df = data.get(
    "market.ohlcv.crypto.binance.btc-usdt.1h",
    start="2026-01-01T00:00:00Z",
    end="2026-07-01T00:00:00Z",
).collect(OutputFormat.PANDAS).set_index("period_start")
indicators = {
    "SMA 20": df.close.rolling(20).mean(),
    "EMA 50": df.close.ewm(span=50).mean(),
}
```

完整示例见 `examples/history_analysis.ipynb`。

在同一份历史数据上运行简单的 `SMA(20, 50)` 多头交叉策略：

```bash
./pyenv/bin/kairos backtest sma \
  --dataset market.ohlcv.crypto.binance.btc-usdt.1h --fast 20 --slow 50 --fee-bps 10
```

信号使用当前 K 线收盘价计算，并在下一根 K 线开盘成交；回测不会使用尚未发生的价格。

合约使用 `--market usdm` 或 `--market coinm`。存储和展示接口与数据源解耦，后续历史数据 provider 可继续接入 IBKR。

Binance 加密期权的 reference、行情、账户、限价执行与现金结算已经独立建模。Binance 没有等价的 options testnet，因此 CLI 会拒绝 `--product options --environment testnet`；options 只允许显式 `live --confirm-live`，并继续经过对账、限额和 kill switch。

SPX/SPXW 是一个专用研究切片；它不会限制 IBKR 的股票能力：

```bash
./pyenv/bin/kairos research capture --host 127.0.0.1 --port 4001
./pyenv/bin/kairos research capture-series \
  --venue ibkr --dataset-id spxw-20260714 --samples 60 --interval-seconds 60
```

股票、ETF、加密现货/永续使用 Catalog 中的内部 InstrumentId 做通用序列采集，不要求期权标的价字段：

```bash
./pyenv/bin/kairos research capture-series \
  --venue ibkr --environment paper \
  --instruments equity:us:AAPL \
  --dataset-id aapl-20260714 --samples 60 --interval-seconds 60

./pyenv/bin/kairos research capture-series \
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
./pyenv/bin/kairos account reconcile \
  --venue binance --environment testnet --product spot --account-id testnet

./pyenv/bin/kairos runtime l4-preflight \
  --venue binance --environment testnet \
  --strategy spot-perp-carry \
  --instrument crypto:binance:spot:BTCUSDT \
  --evidence-artifact data/runtime/binance-testnet/preflight-readiness.json
```

`--evidence-artifact` 会写出带 `kind=runtime_l4_preflight` 和 `audit_hash` 的 readiness evidence；
只有 `ready=true` 的外部 Paper/Testnet readiness artifact 才能用于 `PAPER_APPROVED` 晋级。

人工运维订单使用 `order submit`，并强制留下 actor/reason：

```bash
./pyenv/bin/kairos order submit \
  --venue simulated --environment testnet \
  --instrument crypto:sim:spot:BTCUSDT \
  --side sell --quantity 0.001 --limit-price 50000 \
  --actor operator@example --reason 'manual risk reduction'
```

策略运行验收使用 `runtime soak`；`live` 额外要求 `--confirm-live`。服务启动前强制进行 Catalog、行情、账户、执行与 Ledger/Venue reconciliation 检查；
kill switch 触发后只允许 reduce-only。

已产生 Durable Runtime Store 成交事实后，可以生成执行校准 release：

```bash
./pyenv/bin/kairos runtime calibrate-execution \
  --db data/runtime/binance-testnet/runtime.sqlite3 \
  --output-root data/calibration/execution \
  --venue binance --environment testnet \
  --strategy sma-cross-v1
```

本地 fixture 生成的校准 release 只能证明机制可运行；正式回测执行模型应绑定真实 Paper/Testnet/Live
样本生成的 `ExecutionCalibrationRelease`。

回测可显式绑定校准 release；产出的 Run Artifact 会记录 fill model、release id、release hash、样本数和
适用 venue/environment，并附带按校准平均 `fee_bps` 重估的基线/校准后权益对比：

```bash
./pyenv/bin/kairos run backtest \
  --strategy sma-cross-v1@1.2.0 \
  --fixture --fast 5 --slow 15 \
  --execution-calibration data/calibration/execution/<release-id>/manifest.json
```

提现能力未实现，API key 不应带提现权限。建议 Venue 分离只读/交易 key，并使用独立子账户。

## 测试

异步数据流、治理回测、公共实时 Quote/OrderBook、Live-vs-Replay 策略审计、运行模式组合和 Gateway/Client 接入示例见 [`examples/README.md`](examples/README.md)。

```bash
./pyenv/bin/python -m unittest discover -s tests -v
```

真实连接测试默认跳过：

- `RUN_IBKR_INTEGRATION=1`：IBKR 只读研究与 paper account 查询；
- `RUN_BINANCE_TESTNET=1` 加 testnet 环境变量：Binance public/time/account contract test。

Synthetic Scenario 数据的长期测试契约见 [docs/synthetic_scenario_data_spec.md](docs/synthetic_scenario_data_spec.md)。
