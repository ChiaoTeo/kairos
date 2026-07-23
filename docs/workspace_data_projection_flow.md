# Workspace Data Projection Flow

状态：设计草案  
日期：2026-07-22  
适用对象：Workspace 数据投影、Dataset 历史/实时统一消费、策略运行输入

本文梳理一个目标流程：`Data` 负责拉取和维护数据，`Workspace code` 分成 Dataset 接入层和 Projection & Feature 管理层，`Run` 组合一个 workspace 代码入口和一个 strategy 代码入口，冻结投影和因子图并转换成策略收到的 typed views/events，`Strategy` 只消费自己收到的数据视图，不感知底层数据来自哪个 Dataset、历史文件还是实时流。

## 1. 核心结论

Workspace 不应该只是一个 Dataset alias 集合，也不应该只是 RunConfig 里一个静态名字。Run 时应该指定一个 workspace 代码入口和一个 strategy 代码入口，由 workspace code 负责数据接入、投影和因子图，由 strategy code 负责决策。Workspace code 内部仍然分为两层：

| 层 | 形态 | 职责 | 入口 |
|---|---|---|---|
| Dataset Attachment | 可持久化 manifest 或代码声明 | 接入 Dataset，给 Dataset 一个 workspace-local 名字和基础 view 约束 | CLI 或 workspace code |
| Projection & Feature Builder | Python 代码 | 组合、过滤、派生因子、fallback、按运行模式投影 Dataset | workspace code |

静态 `REQUIRES` 不应该复制策略需求。Projection & Feature Builder 才是表达数据装配和因子图逻辑的地方。

```text
Project
  -> Data Store
       -> Dataset A
          -> data/ historical rows
          -> live/ live state, stream config, capture
       -> Dataset B
          -> data/
          -> live/
  -> Workspace
       -> Workspace code: build_workspace(ws, params)
       -> Attachments
          -> "bars_raw"  -> Dataset A
          -> "book_live" -> Dataset B
       -> Projection & Feature Builder
          -> local graph node "bars"  -> attachment "bars_raw"
          -> local graph node "book"  -> attachment "book_live"
          -> feature nodes "momentum_30d", "rv_20d"
          -> market projection / feature projection
  -> Run
       -> workspace code entrypoint + strategy code entrypoint + mode + runtime bindings
       -> frozen workspace projection result
       -> typed strategy context
```

用户心智应该是：

1. `data` 准备 Dataset。
2. `workspace attach` 可以用 CLI 接入 Dataset，形成可检查的 Dataset 清单。
3. `workspace code` 读取或声明 attachments，选择、组合、派生因子并组织策略运行需要的数据图。
4. `run` 决定这些 inputs 按历史方式 replay，还是按实时方式 subscribe。
5. `strategy` 只读收到的 `Context`、market event、feature view、portfolio view 或 fill event。

策略不知道 Dataset，也不拥有因子生产管线。Dataset 和 factor graph 是 Data/Workspace/Run 的 wiring 细节，不是策略 API。策略项目可以提供 workspace code，但策略执行钩子不应该在运行中动态修改 Workspace。

## 2. Dataset 同时承载 historical 和 live

Dataset ID 是稳定的数据地址。一个 Dataset 可以有历史侧、实时侧，或者两者都有：

```text
.kairos/data/datasets/<dataset-id>/
  dataset.json
  data/
    event_day=2026-07-22/
      part-00000.parquet
  live/
    default/
      state.json
      capture/
```

语义上：

| 子目录 | 含义 | 消费方式 |
|---|---|---|
| `data/` | 已落库的历史数据 | query/replay/backtest |
| `live/` | 实时连接配置、状态、capture | subscribe/paper/live |
| `live/*/capture/` | 实时过程中捕获的原始或标准事件 | 后续 compact 到 `data/` |

因此不再保留 `bind-data` 和 `bind-live` 两条 Workspace binding 路径。历史和实时只是同一个 Dataset attachment 在不同 run mode 下的读取方式：

```text
Workspace attachment = local name + dataset id + allowed views + optional selectors
```

其中 `allowed views` 可以是：

| 值 | 含义 |
|---|---|
| `history` | 只允许历史查询/replay |
| `live` | 只允许实时订阅 |
| `both` | 运行模式决定用 historical 还是 live |

推荐默认是 `both`。如果底层 Dataset 只有历史或只有实时，`workspace inspect` 应该展示缺失侧，而不是让策略改代码。

## 3. Workspace 分两层

### 3.1 Dataset Attachment

Dataset Attachment 是 Workspace 的接入层。它回答：

1. 这个 Workspace 接入了哪些 Dataset。
2. 每个 Dataset 在 Workspace 里叫什么。
3. 允许用于 history、live 还是 both。
4. 是否有基础 selector，例如标的、字段、默认 view、freshness。

这层可以用 CLI 管理，因为它是可物化、可审计、可表格展示的：

```bash
kairos workspace create alpha
kairos workspace attach alpha --name bars_raw \
  --dataset market.ohlcv.crypto.hyperliquid.perpetual.1h \
  --instrument BTC \
  --view both

kairos workspace attach alpha --name book_live \
  --dataset market.orderbook.crypto.binance.spot.btc-usdt \
  --view live \
  --freshness-seconds 5
```

等价的代码入口也应该存在，用于测试或批量创建：

```python
ws.attach(
    name="bars_raw",
    dataset="market.ohlcv.crypto.hyperliquid.perpetual.1h",
    instruments=["BTC"],
    view="both",
)
ws.attach(
    name="book_live",
    dataset="market.orderbook.crypto.binance.spot.btc-usdt",
    view="live",
    freshness_seconds=5,
)
```

Attachment 不做复杂组合，不做特征派生，不决定策略上下文结构。它只把 Dataset 接进 Workspace。

### 3.2 Projection & Feature Builder

策略通常不只依赖一类数据。Workspace 应该允许用代码绑定、解析、创建和组合多个 Dataset，并在运行时吐出一个统一的数据图；但这个数据图属于 runtime wiring，不直接暴露给策略。

推荐 Run 组合两个代码入口：

```text
my_workspace.py
  build_workspace(ws, params) -> WorkspaceProjection

my_strategy.py
  BtcStrategy
```

示例：

```python
def build_workspace(ws, params):
    ws.attachments.use_profile(params.get("workspace_profile", "alpha"))

    bars = ws.use("bars_raw").ohlcv(
        name="bars",
        warmup="90d",
    )

    book = ws.use("book_live").orderbook(
        name="book",
        capture=True,
    )

    momentum = ws.features.momentum(
        name="momentum_30d",
        source=bars,
        lookback="30d",
    )

    rv = ws.features.realized_volatility(
        name="rv_20d",
        source=bars,
        lookback="20d",
    )

    return ws.project(
        market=[bars, book],
        features=[momentum, rv],
    )
```

这里的 `bars`、`book`、`momentum_30d`、`rv_20d` 是 Projection graph 里的节点名，不是 Dataset 名。Projection & Feature Builder 可以引用 attachment，也可以按类型调用 resolver：

```python
bars = ws.use("bars_raw").as_ohlcv(
    name="bars",
)

book = ws.resolve(
    name="book",
    kind="orderbook",
    venue="binance",
    market="spot",
    instruments=["BTCUSDT"],
    levels=20,
    view="live",
)
```

Workspace code 可以引用已接入的 attachments，也可以在代码里声明 attachments，还可以调用 typed resolver。typed resolver 如果解析到新的 Dataset，应把解析结果写入冻结后的 run manifest；是否同时 materialize 成 Workspace attachment，由用户决定。

因子属于这一层。因子是数据派生，不是策略决策本身。它应该和 Dataset 投影一起做 coverage、lookback、point-in-time、warmup 和 live freshness 检查，并在 Run 里冻结为可审计的 feature graph。

Run 启动时执行 workspace code，读取或声明 attachments，冻结 projection result，把 graph nodes 解析成策略上下文里的视图：

```text
Projection result
  bars   -> attachment bars_raw -> historical replay cursor or warmup window
  book   -> attachment book_live -> live event source or replayed capture
  momentum_30d -> feature from bars
  rv_20d -> feature from bars

Run projection
  -> MarketView
  -> FeatureView
  -> PortfolioView
  -> ReferenceView
  -> OrderView / IntentView / BudgetView
```

策略看到的是 `Context` 和事件，不是 provider、Dataset ID、文件路径、live view manifest 或 release ID。

策略读取因子的方式应该是 `context.features.factor("momentum_30d")`，而不是自己在 `on_market()` 里重新计算滚动窗口。策略可以决定如何使用因子，但不应该拥有因子生产逻辑。

运行模式负责把同一个 Workspace 输入集合解析成不同的数据访问形态：

| Run mode | `bars` | `book` | `momentum_30d` / `rv_20d` |
|---|---|---|---|
| backtest | historical replay | 不可用或用 capture replay | point-in-time feature replay |
| simulation/shadow | historical 或 live mirror | live subscription | historical snapshot 或 live feature update |
| paper/live | latest historical warmup + live subscription | live subscription | warmup 后随 market update 增量计算 |

这使策略可以做到“历史和实时无感”：策略处理同一种 market/feature/portfolio view，attachments 提供 Dataset 接入，Projection & Feature Builder 定义数据图和因子图，runtime 决定 view 背后是 replay cursor、batch table、live stream 还是 latest state。

## 4. CLI 目标体验

### 4.1 Inspect

`workspace inspect` 应该支持两种视角：先看接入层，再看投影层。

```bash
kairos workspace inspect alpha
kairos workspace inspect-code my_workspace:build_workspace \
  --mode paper \
  --param workspace_profile=alpha \
  --param instruments=BTC
```

接入层输出：

| Attachment | Dataset | Views | Selectors | Historical | Live | Primary Time | Notes |
|---|---|---|---|---|---|---|
| bars_raw | market.ohlcv.crypto.hyperliquid.perpetual.1h | both | BTC | ok, 48 files | missing | timestamp | live not configured |
| book_live | market.orderbook.crypto.binance.spot.btc-usdt | live | n/a | missing | ok, default | timestamp | fresh 2s |

投影层输出：

| Node | Kind | Source | Views | Historical | Live | Primary Time | Notes |
|---|---|---|---|---|---|---|
| bars | ohlcv | attachment:bars_raw | both | ok, 48 files | missing | timestamp | warmup 90d |
| book | orderbook | attachment:book_live | live | missing | ok, default | timestamp | fresh 2s |
| momentum_30d | feature | bars | history | ok | n/a | event_time | point-in-time, lookback 30d |
| rv_20d | feature | bars | history | ok | n/a | event_time | point-in-time, lookback 20d |

这里的关键不是“看 manifest JSON”，而是回答用户真正关心的问题：

1. Workspace 接入了哪些 Dataset。
2. Workspace code 会生成哪些 projection/feature graph nodes。
3. 每个 node 来自 attachment、typed resolver、feature transform 还是 live connector。
4. 底层数据是否覆盖标的、时间、字段和频率。
5. 底层 Dataset 是否有历史数据和实时视图。
6. 当前 run mode 下会不会可用。

`inspect-code --mode` 应返回 `preflight` 报告。`warning` 表示投影可冻结但有缺口，例如 optional attachment 未配置；`error` 表示该 run mode 不可执行，例如 backtest 引用了 live-only node、paper/live 引用了 history-only node、feature source 不存在。Run 启动必须冻结同一份报告到 `workspace_preflight.json`；存在 error 时应在策略执行前失败。

### 4.2 Attach

`workspace attach` 是 CLI 主路径，因为接入 Dataset 是可物化的。历史、实时和同时支持都通过 `--view` 表达：

```bash
kairos workspace attach alpha --name bars_raw --dataset market.ohlcv.crypto.hyperliquid.perpetual.1h --view both
kairos workspace attach alpha --name book_live --dataset market.orderbook.crypto.binance.spot.btc-usdt --view live
```

`bind-data` / `bind-live` 不再作为 CLI 或 Workspace API 暴露。用户调用旧命令时应该失败，并提示改用 `workspace attach --view history|live|both`。

如果同一个 local name 先后绑定 data 和 live 到同一个 Dataset，最终应该合并为一个 binding：

```text
bars_raw -> market.ohlcv.crypto.hyperliquid.perpetual.1h, views=both
```

如果同一个 local name 绑定到不同 Dataset，应明确报错，要求用户改名或显式覆盖。

Projection 和因子图管理不应该靠 CLI 表达。CLI 最多负责检查或物化投影结果：

```bash
kairos workspace inspect-code my_workspace:build_workspace --mode paper --param workspace_profile=alpha
kairos workspace materialize-code my_workspace:build_workspace --param workspace_profile=alpha
kairos workspace inspect alpha
```

这让用户用 CLI 接入 Dataset，用 workspace code 管理投影和因子图，同时仍然可以得到可审计的 Workspace snapshot。

## 5. Data 进程和落库

实时 data 进程的职责不是“给某个策略喂数据”，而是维护 Dataset 的 live side，并可选 capture：

```bash
kairos data connect binance.orderbook --instrument BTCUSDT --market spot --levels 20
kairos data run market.orderbook.crypto.binance.spot.btc-usdt --capture
```

推荐职责拆分：

| 组件 | 职责 |
|---|---|
| Data connector | 连接 provider，标准化事件 |
| Live view | 写 `live/default/state.json` 和 freshness evidence |
| Capture writer | 写 `live/default/capture/` |
| Compactor | 把 capture 转成 `data/` 下可 replay 的历史文件 |
| Workspace attachment | 只引用 Dataset，不拥有 data process |
| Projection & Feature builder | 只管理 Dataset 投影、因子和派生，不拥有 data process |
| Run | 对 Workspace inputs 做 replay 或 subscribe |

这样 live 数据自然会成为未来的历史数据：

```text
provider stream
  -> canonical events
  -> live/default/state.json
  -> live/default/capture/*.jsonl or parquet
  -> compactor
  -> data/event_day=.../part-*.parquet
```

## 6. 完整用例

### 6.1 准备历史 OHLCV

```bash
kairos data use hyperliquid.perpetual.ohlcv.1h \
  --instrument BTC \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-02-01T00:00:00+00:00
```

生成：

```text
Dataset: market.ohlcv.crypto.hyperliquid.perpetual.1h
Historical: configured
Live: optional
```

### 6.2 配置实时 order book

```bash
kairos data connect binance.orderbook \
  --instrument BTCUSDT \
  --market spot \
  --levels 20
```

生成：

```text
Dataset: market.orderbook.crypto.binance.spot.btc-usdt
Historical: missing until capture is compacted
Live: configured
```

### 6.3 接入 Dataset 到 Workspace

```bash
kairos workspace create alpha
kairos workspace attach alpha --name bars_raw \
  --dataset market.ohlcv.crypto.hyperliquid.perpetual.1h \
  --instrument BTC \
  --view both
kairos workspace attach alpha --name book_live \
  --dataset market.orderbook.crypto.binance.spot.btc-usdt \
  --view live \
  --freshness-seconds 5
kairos workspace inspect alpha
```

这一步只接入 Dataset，不表达策略上下文。

### 6.4 编写 Workspace Code 和 Strategy Code

Run 组合 workspace code 和 strategy code。workspace code 可以写任意 Python 逻辑来读取 attachments、定义投影和因子图；strategy code 只消费 `Context`。

```python
# my_workspace.py
def build_workspace(ws, params):
    ws.attachments.use_profile(params.get("workspace_profile", "alpha"))

    bars = ws.use("bars_raw").as_ohlcv(
        name="bars",
        start=params.get("start"),
        end=params.get("end"),
        warmup="90d",
    )

    book = ws.use("book_live").as_orderbook(
        name="book",
    )

    momentum = ws.features.momentum(
        name="momentum_30d",
        source=bars,
        lookback="30d",
    )

    realized_vol = ws.features.realized_volatility(
        name="rv_20d",
        source=bars,
        lookback="20d",
        annualization="crypto_365d",
    )

    return ws.project(
        market=[bars, book],
        features=[momentum, realized_vol],
    )
```

```python
# my_strategy.py
class BtcStrategy:
    @property
    def strategy_id(self):
        return "btc-strategy"

    def on_market(self, context):
        market = context.market
        momentum = context.features.factor("momentum_30d")
        realized_vol = context.features.factor("rv_20d")
        portfolio = context.portfolio
        return ()
```

### 6.5 Inspect Workspace Code

```bash
kairos workspace inspect-code my_workspace:build_workspace \
  --param workspace_profile=alpha \
  --param instruments=BTC \
  --param start=2026-01-01T00:00:00+00:00 \
  --param end=2026-02-01T00:00:00+00:00
```

预期输出：

| Node | Kind | Source | Views | Coverage | Runtime use |
|---|---|---|---|---|---|
| bars | ohlcv | attachment:bars_raw | both | BTC 2026-01-01..2026-02-01 ok | replay, warmup, optional live |
| book | orderbook | attachment:book_live | live | live fresh <= 5s | subscribe |
| momentum_30d | feature | bars | history/live | warmup 30d ok, point-in-time safe | FeatureView |
| rv_20d | feature | bars | history/live | warmup 20d ok, point-in-time safe | FeatureView |

如果缺标的、缺时间范围、缺字段、缺因子 warmup、因子不是 point-in-time safe 或缺 live freshness，inspect/preflight 应展示 typed diagnostic，而不是只说 Dataset 存在或不存在。

### 6.6 RunConfig

RunConfig 指向 workspace code 和 strategy code。Run 的核心组合就是这两个 entrypoint：

```toml
[run]
name = "btc-backtest"
mode = "backtest"
workspace = "my_workspace:build_workspace"
strategy = "my_strategy:BtcStrategy"

[params]
workspace_profile = "alpha"
instruments = ["BTC"]
start = "2026-01-01T00:00:00+00:00"
end = "2026-02-01T00:00:00+00:00"
```

backtest 时：

```bash
kairos run start --config configs/runs/btc-backtest.toml
```

runtime 执行 `my_workspace:build_workspace`，读取或声明 attachments，冻结 projection result 和 feature graph，然后把 `bars` 解析为 historical replay，并由 replay 结果构造 `MarketView`/`FeatureView`。如果 `book` 没有 compact 过的历史 capture，backtest preflight 应提示该 Projection graph node 在 backtest 不可用。策略代码不变，因为它从未直接依赖 `book` 这个 Dataset attachment，也没有自己重算因子。

paper/live 时：

```bash
kairos run start --config configs/runs/btc-paper.toml --supervise-live-services
```

runtime 对 `bars` 可以先读 historical warmup，再切到 live；对 `book` 直接订阅 live view；然后持续生成同一种 `Context`。策略代码不变。

## 7. 实现优先级建议

第一阶段只做体验闭环：

1. 定义 Workspace Attachment API 和 CLI：`workspace attach` / `ws.attach(...)`。
2. 定义 Workspace Code API：`build_workspace(ws, params) -> WorkspaceProjection`，内部包含 Projection & Feature Builder。
3. `workspace inspect <name>` 展示 attachments；`workspace inspect-code <entrypoint>` 展示 workspace code 生成的 projection graph 和 feature graph。
4. RunConfig 支持 `workspace = "module:build_workspace"` 和 `strategy = "module:Strategy"`，并冻结 projection result 与 feature graph。
5. Run preflight 按 mode 校验 Projection graph nodes、feature source、attachment 缺失、view 匹配、feature warmup、point-in-time 和 freshness 是否可用，并冻结 `workspace_preflight.json`。
6. Run projection 把 Projection graph 转换为策略 `Context` 和 `FeatureView`，策略层不暴露 Dataset attachment。
7. 删除 `workspace bind-data` / `bind-live` 旧入口，旧调用失败并指向 `workspace attach`。

第二阶段做 live 沉淀：

1. data process 管理 live connector 生命周期。
2. capture writer 标准化落盘。
3. compactor 把 capture 转成 Dataset `data/`。
4. backtest 支持 replay compacted live capture。

## 8. 验收用例：跨所资费套利

最终应以一个跨所资费套利策略跑通作为验收条件。目标不是只跑一个 toy backtest，而是验证 Workspace、Data、Feature、Risk、Treasury、Strategy 和 Run 的边界是否真的成立。

### 8.1 策略目标

验收策略：

```text
Hyperliquid 永续合约做空
Binance 现货或等价多头做多
通过 funding、basis、交易成本、风险暴露和资金可用性判断开仓、减仓和退出
```

核心组合：

| 方向 | Venue | Instrument | 目的 |
|---|---|---|---|
| Short | Hyperliquid perpetual | BTC perpetual | 收取或利用 funding / basis |
| Long | Binance spot 或等价多头 | BTC spot | 对冲方向性价格风险 |

### 8.2 Workspace Attachments

CLI 可以接入验收所需 Dataset：

```bash
kairos workspace create funding-arb

kairos workspace attach funding-arb --name hl_perp_mark \
  --dataset market.mark.crypto.hyperliquid.perpetual.btc \
  --view both

kairos workspace attach funding-arb --name hl_funding \
  --dataset market.funding.crypto.hyperliquid.perpetual.btc \
  --view both

kairos workspace attach funding-arb --name hl_orderbook \
  --dataset market.orderbook.crypto.hyperliquid.perpetual.btc \
  --view live

kairos workspace attach funding-arb --name binance_spot_book \
  --dataset market.orderbook.crypto.binance.spot.btc-usdt \
  --view live

kairos workspace attach funding-arb --name binance_spot_trades \
  --dataset market.trade.crypto.binance.spot.btc-usdt \
  --view both
```

具体 Dataset ID 可以随 connector 命名收敛调整，但验收必须覆盖：

1. Hyperliquid perpetual mark/index/funding。
2. Hyperliquid order book 或 quote。
3. Binance spot book/trade/price。
4. 两边账户、余额、仓位和可用保证金。
5. 资金迁移或 treasury 状态。

### 8.3 Workspace Code

Workspace code 负责数据投影、因子图和风险输入，不让策略直接碰 Dataset：

```python
# funding_arb_workspace.py
def build_workspace(ws, params):
    ws.attachments.use_profile(params.get("workspace_profile", "funding-arb"))

    hl_mark = ws.use("hl_perp_mark").as_mark_price(name="hl_mark")
    hl_funding = ws.use("hl_funding").as_funding(name="hl_funding", lookback="7d")
    hl_book = ws.use("hl_orderbook").as_orderbook(name="hl_book")
    bn_book = ws.use("binance_spot_book").as_orderbook(name="bn_book")

    basis = ws.features.basis(
        name="hl_bn_basis",
        short_leg=hl_mark,
        long_leg=bn_book,
        fees=params.get("fee_model", "default"),
    )

    expected_carry = ws.features.expected_funding_carry(
        name="expected_funding_carry",
        funding=hl_funding,
        basis=basis,
        horizon=params.get("carry_horizon", "8h"),
    )

    liquidity = ws.features.cross_venue_liquidity(
        name="cross_venue_liquidity",
        short_book=hl_book,
        long_book=bn_book,
        target_notional=params.get("target_notional"),
    )

    hedge_error = ws.features.hedge_error(
        name="hedge_error",
        short_leg=hl_mark,
        long_leg=bn_book,
    )

    return ws.project(
        market=[hl_mark, hl_book, bn_book],
        features=[basis, expected_carry, liquidity, hedge_error],
        portfolio=["hyperliquid_perp", "binance_spot"],
        treasury=["binance", "hyperliquid"],
    )
```

Workspace code 的验收重点：

1. Funding/basis/liquidity/hedge error 是 Workspace 管理的 features。
2. Feature warmup、point-in-time、freshness 和 coverage 可由 inspect/preflight 诊断。
3. Strategy 只读 `context.features`、`context.market`、`context.portfolio`、`context.budget`。

### 8.4 Strategy Code

Strategy code 负责建仓、减仓、退出和资金迁移意图，不直接读 Dataset：

```python
# funding_arb_strategy.py
class FundingArbStrategy:
    @property
    def strategy_id(self):
        return "cross-venue-funding-arb"

    def on_market(self, context):
        carry = context.features.factor("expected_funding_carry")
        liquidity = context.features.factor("cross_venue_liquidity")
        hedge_error = context.features.factor("hedge_error")
        portfolio = context.portfolio
        budget = context.budget

        if should_open(carry, liquidity, hedge_error, portfolio, budget):
            return open_pair_trade(
                short_venue="hyperliquid",
                long_venue="binance",
                instrument="BTC",
                notional=target_notional(carry, liquidity, budget),
            )

        if should_reduce(carry, liquidity, hedge_error, portfolio):
            return reduce_pair_trade(reason="carry_decayed_or_risk_increased")

        if should_transfer_capital(portfolio, budget):
            return request_treasury_transfer(
                source="binance",
                destination="hyperliquid",
                asset="USDC",
                amount=required_margin_topup(portfolio, budget),
            )

        return ()
```

### 8.5 Risk Checks

验收必须至少覆盖以下风险检查：

| 风险 | 检查 |
|---|---|
| Directional exposure | Hyperliquid short 和 Binance long 的 delta 偏差不超过阈值 |
| Funding decay | expected carry 低于阈值时不新开仓，已持仓触发减仓 |
| Basis reversal | basis 反向扩大或收敛失败时减仓 |
| Liquidity/slippage | 两边 order book 深度不足时不建仓或降低 notional |
| Margin/liquidation | Hyperliquid 保证金不足或 liquidation buffer 不足时减仓或补保证金 |
| Venue/account health | 任一 venue stale、断连、账户不可用时不加仓 |
| Transfer risk | 资金迁移未确认前不把目标资金计入可用预算 |
| Concentration | 单币种、单 venue、单策略 notional 不超过限额 |

风险检查应由 Run preflight 和 runtime risk view 一起覆盖。Strategy 可以读取 risk/budget view，但不应该绕过 risk engine 直接下单。

### 8.6 RunConfig

验收 RunConfig 应组合 workspace code 和 strategy code：

```toml
[run]
name = "funding-arb-paper"
mode = "paper"
workspace = "funding_arb_workspace:build_workspace"
strategy = "funding_arb_strategy:FundingArbStrategy"

[params]
workspace_profile = "funding-arb"
instrument = "BTC"
target_notional = "10000"
min_expected_carry_bps = "8"
max_hedge_error_bps = "15"
max_slippage_bps = "5"
min_liquidation_buffer_bps = "500"
carry_horizon = "8h"
```

验收命令：

```bash
kairos workspace inspect-code funding_arb_workspace:build_workspace \
  --mode paper \
  --param workspace_profile=funding-arb \
  --param instrument=BTC \
  --param target_notional=10000

kairos run config validate configs/runs/funding-arb-paper.toml
kairos run start --config configs/runs/funding-arb-paper.toml

kairos workspace inspect-code funding_arb_workspace:build_workspace \
  --mode live \
  --param workspace_profile=funding-arb \
  --param instrument=BTC \
  --param target_notional=10000

kairos run config validate configs/runs/funding-arb-live-preflight.toml
```

`funding-arb-live-preflight.toml` 只做 live 前置检查和人工上线准备，不默认绑定真实 provider ports，不发单。真实 live 验证必须在用户手动确认以下条件后再运行：

1. Hyperliquid official SDK `exchange` / `info` adapter 已注入 `LiveProviderPorts` 或等价 cross-venue execution binding，并通过真实 key/order 手动验证。
2. Binance spot 和 Hyperliquid perpetual 账户、余额、仓位、保证金和交易权限已经通过 readiness evidence。
3. API key、withdraw/transfer 权限和 treasury route 由用户在本机环境手动配置，不写入 RunConfig。
4. 首次 live run 必须 `start_reduce_only = true`，并由用户显式传入 `--confirm-live`。
5. Strategy 输出的 orders 仍带 `blocked_until = "live_execution_keys_and_risk_inputs_verified"`，直到 runtime risk/execution 层解除阻塞。

真实 key 和真实下单的手动步骤见 `docs/funding_arb_live_manual_verification.md`。在 Hyperliquid official SDK exchange/info/account address 未显式注入并通过手动 key/order 验证前，`build_live_provider_ports(provider="hyperliquid")` 必须 fail closed。

### 8.7 Acceptance Criteria

该用例验收通过需要满足：

1. `workspace inspect-code` 能展示 Hyperliquid 和 Binance 的 attachments、projection nodes、feature nodes、coverage、freshness 和风险输入状态。
2. 缺任一关键 Dataset、标的、字段、时间窗口、feature warmup 或 live freshness 时，preflight 给出 typed diagnostic。
3. 同一套 workspace code 和 strategy code 至少能跑 backtest 或 replay，以及 paper/live shadow 中的一种实时模式。
4. Strategy 不直接引用 Dataset ID、provider connector 或文件路径。
5. Funding/basis/liquidity/hedge error 因子由 Workspace 生成，并通过 `FeatureView` 给 Strategy。
6. 建仓会同时产生 Hyperliquid short 和 Binance long 的配对意图，不能单腿裸露。
7. 减仓逻辑能在 funding decay、basis reversal、hedge error 扩大、liquidity 下降或 margin buffer 不足时触发。
8. 资金迁移通过 treasury/transfer intent 表达，并在确认前不增加可用预算。
9. Run manifest 冻结 workspace code hash、strategy code hash、projection result、feature graph、risk limits、account/treasury bindings 和 preflight diagnostics。
10. 运行产物能解释一次建仓、一次减仓和一次资金迁移请求的输入因子、风险检查和 intent。
11. live preflight 配置在无真实 key 情况下可 validate；真实下单验收在需要 key 和 Hyperliquid execution binding 时停止，由用户手动运行。
12. Hyperliquid execution binding 未注入 official SDK exchange/info/account address 时必须给出 typed fail-closed error，不能 fallback 到 Binance、simulated 或 manual execution。

## 9. 全局边界

边界上应避免：

1. Workspace 自己拉 provider 数据。
2. Strategy 直接读 Dataset ID 或 provider connector。
3. Run 修改 Workspace attachment。
4. live 和 history 用两个 local input name 表示同一业务输入。
5. 把 `context.data["dataset_name"]` 作为正式策略接口。
6. 用静态 `REQUIRES` 复制 Projection & Feature Builder 已经表达过的数据需求。
7. 用 CLI 参数表达复杂投影、特征图、fallback 和 mode-specific 逻辑。
8. 在策略 `on_market()` 里临时生产正式因子。
